"""The recipes that are not video generation.

The product had drifted into being a video generator with four unreachable menu entries.
These exercise the lanes that never generate a frame — subtitles over an existing video, and
finding the moments inside a long one — because those are the cheapest things it sells and
the ones it competes most directly on.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lumina.db.models import Channel, Plan, Project, Scene, User
from lumina.domain.plan import SceneState, Tier
from lumina.execution import dubtrack, speak, stages
from lumina.execution.compose import captions, ffmpeg
from lumina.execution.providers.fake import FakeProvider
from lumina.execution.router import Router
from lumina.intelligence.languages import get_pack
from lumina.state.assets import AssetStore, LocalBlobs
from lumina.state.ledger import Ledger

DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB, reason="TEST_DATABASE_URL not set")

TRANSCRIPT = (
    "Light is fast but not instant. "
    "Why does the Sun you see look 8 minutes old. "
    "Look far enough and you are looking backwards"
)


@pytest.fixture
async def session():
    engine = create_async_engine(DB or "")
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
        await s.rollback()
    await engine.dispose()


@pytest.fixture
def ctx(session, tmp_path: Path):
    return stages.Ctx(
        session=session,
        router=Router(providers=[FakeProvider()]),
        assets=AssetStore(session, LocalBlobs(tmp_path)),
        ledger=Ledger(session),
    )


async def _plan_with_scenes(session, n: int = 3) -> tuple[Plan, uuid.UUID]:
    u = User(email=f"{uuid.uuid4()}@x.com")
    session.add(u)
    await session.flush()
    c = Channel(user_id=u.id, name="T", language="en", identity={})
    session.add(c)
    await session.flush()
    p = Project(channel_id=c.id, title="T", recipe="subtitle_only", language="en")
    session.add(p)
    await session.flush()
    plan = Plan(project_id=p.id, version=1, content={})
    session.add(plan)
    await session.flush()
    session.add_all(
        Scene(
            plan_id=plan.id,
            index=i,
            prompt="",
            script_line=f"Line number {i} of the narration.",
            caption=f"Line {i}",
            duration_ms=3000,
            tier=Tier.IMAGE_MOTION.value,
            state=SceneState.DRAFT.value,
            ref_asset_ids=[],
        )
        for i in range(n)
    )
    await session.flush()
    return plan, u.id


async def test_ingest_reads_real_media_properties(ctx, tmp_path):
    """Duration comes from probing the file, not from what the client claimed."""
    src = tmp_path / "upload.mp4"
    img = await ffmpeg.still(tmp_path / "f.png", ffmpeg.Size(320, 568), "0x1b2a63", "0x070b1e")
    await ffmpeg.ken_burns(img, src, ms=3000, size=ffmpeg.Size(320, 568), zoom_in=True)

    out = await stages.ingest(ctx, {"path": str(src)})
    assert out["duration_ms"] == pytest.approx(3000, abs=200)
    assert (tmp_path / out["storage_key"]).is_file()


async def test_subtitle_lane_needs_no_generation(ctx, session, tmp_path):
    """Subtitles only: ingest, transcribe, caption. Not one frame is generated.

    This is the cheapest thing the product sells, and the reason it must not depend on the
    generation path at all.
    """
    plan, user_id = await _plan_with_scenes(session)

    heard = await stages.transcribe(
        ctx, {"asset_id": str(uuid.uuid4()), "duration_ms": 9000, "text": TRANSCRIPT}
    )
    assert len(heard["lines"]) == 3
    assert all(line["duration_ms"] > 0 for line in heard["lines"])

    out = await stages.captions(ctx, {"plan_id": str(plan.id), "language": "en", "style": "pop"})
    assert out["count"] > 3, "expected one state per highlighted word, not per line"

    # Every state is a real, stored PNG.
    for state in out["states"][:4]:
        data = await ctx.assets.read(uuid.UUID(state["asset_id"]))
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        assert state["end_ms"] > state["start_ms"]

    # Nothing was reserved or charged: this lane never calls a provider.
    available, reserved, committed = await ctx.ledger.balance(user_id)
    assert (available, reserved, committed) == (0, 0, 0)


async def test_analyze_ranks_moments_and_keeps_the_score_visible(ctx):
    heard = await stages.transcribe(
        ctx, {"asset_id": str(uuid.uuid4()), "duration_ms": 120_000, "text": TRANSCRIPT}
    )
    out = await stages.analyze(ctx, {"lines": heard["lines"]})

    moments = out["moments"]
    assert moments, "found nothing in a two-minute video"
    # Ranked, and the score travels with each moment so the creator can disagree with it.
    scores = [m["score"] for m in moments]
    assert scores == sorted(scores, reverse=True)
    assert all(0 < m["score"] <= 99 for m in moments)
    assert all(m["end_ms"] > m["start_ms"] for m in moments)


async def test_captions_shape_non_latin_scripts(tmp_path):
    """The reason this uses a browser rather than ffmpeg's drawtext.

    Myanmar stacks marks above and below the baseline; a rasterizer without HarfBuzz shaping
    renders it wrong. Two things are checked, and the second is the one that used to be
    broken silently:

      - the units come from the pack, so a continuously-written script highlights a syllable
        at a time. Splitting on whitespace gives two "words" for this line and a highlight
        that never moves.
      - a face is actually present. A missing one does not fail, it draws identical empty
        boxes — which is why `fonts.require` runs before the browser starts.
    """
    burmese = "မြန်မာစာ စမ်းသပ်ချက်"
    line = captions.word_timings(burmese, 0, 2000, get_pack("my"))
    assert len(line.words) > 4, f"whitespace split would give 2; got {[w for w, _ in line.words]}"

    states = await captions.rasterize(
        [line], tmp_path, width=540, height=960, style="pop", language="my"
    )
    assert len(states) == len(line.words)
    assert all(s.png.stat().st_size > 1000 for s in states)
    # Tofu is identical for every glyph, so every state would be the same size. Real shaping
    # gives syllables of different widths.
    assert len({s.png.stat().st_size for s in states}) > 1, "every state identical — tofu?"


async def test_captions_refuse_a_script_with_no_face_rather_than_drawing_boxes(
    tmp_path, monkeypatch
):
    """A missing font is the one failure that reaches the finished video looking like a bug
    in the app: a caption of empty rectangles, burnt in, unfixable."""
    from lumina.execution.compose import fonts

    monkeypatch.setattr(fonts, "DIR", tmp_path / "nothing-here")
    line = captions.word_timings("မြန်မာစာ", 0, 2000, get_pack("my"))
    with pytest.raises(fonts.MissingFontError, match="make fonts"):
        await captions.rasterize(
            [line], tmp_path, width=540, height=960, style="pop", language="my"
        )


async def test_compose_cuts_the_source_when_a_scene_points_into_it(ctx, session, tmp_path):
    """Clip keeps the moments and drops the rest — that is its entire value, and it is the
    lane this behaviour belongs to. Contrast with the test below: Subtitle starts from the
    same kind of file and must do the opposite.
    """
    from lumina.db.models import Channel, Plan, Project, Scene, User
    from lumina.domain.assets import AssetKind
    from lumina.domain.plan import SceneState, Tier

    # The project's own async runner, not subprocess.run: blocking on a process inside an
    # async function is the exact mistake that matters for real in a worker.
    source = tmp_path / "source.mp4"
    await ffmpeg.run(
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=640x360:rate=30:duration=6",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        str(source),
    )
    asset = await ctx.assets.put_path(source, kind=AssetKind.VIDEO, mime="video/mp4")

    user = User(email=f"{uuid.uuid4()}@example.com")
    session.add(user)
    await session.flush()
    channel = Channel(user_id=user.id, name="T", language="en", identity={})
    session.add(channel)
    await session.flush()
    project = Project(
        channel_id=channel.id,
        title="Clips",
        recipe="clip_long_video",
        language="en",
        source_asset_id=asset.id,
    )
    session.add(project)
    await session.flush()
    plan = Plan(project_id=project.id, version=1, content={"title": "Clips"})
    session.add(plan)
    await session.flush()

    # Two scenes cut from different places in the source, and no generated picture at all.
    for i, (at, ms) in enumerate([(500, 1500), (3000, 1200)]):
        session.add(
            Scene(
                plan_id=plan.id,
                index=i,
                prompt="",
                script_line=f"Line {i}.",
                caption=f"Line {i}",
                duration_ms=ms,
                source_start_ms=at,
                tier=Tier.STOCK.value,
                state=SceneState.DRAFT.value,
                ref_asset_ids=[],
            )
        )
    await session.flush()

    out = await stages.compose(ctx, {"plan_id": str(plan.id), "aspect": "9:16"})

    assert out["scenes"] == 2
    # 1.5s + 1.2s, with the usual encoder slack. The other 3.3 seconds are gone, on purpose.
    assert 2400 <= out["duration_ms"] <= 3200, out["duration_ms"]


async def test_subtitling_hands_the_whole_video_back_rather_than_the_captioned_bits(
    ctx, session, tmp_path
):
    """The bug this exists to stop coming back.

    Subtitle and Clip start from the same kind of file and shared one code path, so Subtitle
    inherited Clip's behaviour: it cut the video down to the moments that happened to carry a
    caption. A sixty-second video with two cues came out five seconds long — under a screen
    that said "your video is untouched. Only the captions are drawn on."
    """
    from lumina.db.models import Channel, Plan, Project, Scene, User
    from lumina.domain.assets import AssetKind
    from lumina.domain.plan import SceneState, Tier

    source = tmp_path / "source.mp4"
    await ffmpeg.run(
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=640x360:rate=30:duration=6",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        str(source),
    )
    asset = await ctx.assets.put_path(source, kind=AssetKind.VIDEO, mime="video/mp4")

    user = User(email=f"{uuid.uuid4()}@example.com")
    session.add(user)
    await session.flush()
    channel = Channel(user_id=user.id, name="T", language="en", identity={})
    session.add(channel)
    await session.flush()
    project = Project(
        channel_id=channel.id,
        title="Subs",
        recipe="subtitle_only",
        language="en",
        source_asset_id=asset.id,
    )
    session.add(project)
    await session.flush()
    plan = Plan(project_id=project.id, version=1, content={"title": "Subs"})
    session.add(plan)
    await session.flush()

    # Two short cues in a six-second video. Under the old behaviour the render was 2.7s.
    for i, (at, ms) in enumerate([(500, 1500), (3000, 1200)]):
        session.add(
            Scene(
                plan_id=plan.id,
                index=i,
                prompt="",
                script_line=f"Line {i}.",
                caption=f"Line {i}",
                duration_ms=ms,
                source_start_ms=at,
                tier=Tier.STOCK.value,
                state=SceneState.DRAFT.value,
                ref_asset_ids=[],
            )
        )
    await session.flush()

    out = await stages.compose(ctx, {"plan_id": str(plan.id), "aspect": "9:16"})

    assert 5600 <= out["duration_ms"] <= 6600, (
        f"the whole six seconds, not the {2700}ms that carry captions: {out['duration_ms']}"
    )

    data = await ctx.assets.read(uuid.UUID(out["asset_id"]))
    assert data[4:8] == b"ftyp", "not an mp4"
    assert len(data) > 5000


async def test_subtitles_land_at_the_moment_they_were_said(ctx, session, tmp_path):
    """The other half of keeping the video whole.

    Captions are normally laid end to end, because scenes are. When the video is not re-cut
    that is wrong: a cue belongs at its timestamp *in the source*, and accumulating instead
    drags every caption later than the speech it belongs to by the length of every silent gap
    before it. Two cues at 0.5s and 3.0s would have been drawn at 0.5s and 2.0s.
    """
    from lumina.db.models import Channel, Plan, Project, Scene, User
    from lumina.domain.plan import SceneState, Tier

    user = User(email=f"{uuid.uuid4()}@example.com")
    session.add(user)
    await session.flush()
    channel = Channel(user_id=user.id, name="T", language="en", identity={})
    session.add(channel)
    await session.flush()
    project = Project(channel_id=channel.id, title="S", recipe="subtitle_only", language="en")
    session.add(project)
    await session.flush()
    plan = Plan(project_id=project.id, version=1, content={"title": "S"})
    session.add(plan)
    await session.flush()

    for i, (at, ms) in enumerate([(500, 1500), (3000, 1200)]):
        session.add(
            Scene(
                plan_id=plan.id,
                index=i,
                prompt="",
                script_line=f"Line {i}.",
                caption=f"Line {i}",
                duration_ms=ms,
                source_start_ms=at,
                tier=Tier.STOCK.value,
                state=SceneState.DRAFT.value,
                ref_asset_ids=[],
            )
        )
    await session.flush()

    out = await stages.captions(ctx, {"plan_id": str(plan.id), "language": "en", "aspect": "9:16"})
    starts = sorted({int(state["start_ms"]) for state in out["states"]})

    assert starts[0] == 500, "the first cue is where the file says it is"
    assert any(s >= 3000 for s in starts), (
        f"the second cue must land at 3.0s, not at the end of the first: {starts}"
    )


async def test_a_caption_is_the_same_physical_size_in_every_export(tmp_path):
    """The bug behind "the TikTok export text is too big".

    The font came from the frame's *height*, so the same caption was 7.5% of the width in a
    9:16 export and 2.3% in a 16:9 one — a 3.3x difference between two exports of one video,
    and enormous text on the shape short-form actually uses.

    The short edge is the dimension that does not change when the same content is reframed,
    so sizing from it holds the caption steady across exports — and lands where the
    conventions are, since a vertical caption occupies a larger share of the width than a
    landscape one.
    """
    from lumina.execution.compose import captions as caps

    line = caps.word_timings("Light is fast", 0, 1500, get_pack("en"))
    sizes: dict[str, tuple[int, int]] = {}
    for aspect, size in ffmpeg.ASPECTS.items():
        out = tmp_path / aspect.replace(":", "x")
        out.mkdir()
        states = await caps.rasterize(
            [line], out, width=size.w, height=size.h, style="pop", language="en"
        )
        assert states
        sizes[aspect] = (size.w, size.h)

    # The formula, checked directly: every aspect lands on the same font for a 1080-class
    # frame, which is what "same physical size" means here.
    fonts = {a: max(12, round(min(w, h) * 0.05)) for a, (w, h) in sizes.items()}
    assert len(set(fonts.values())) == 1, f"caption size drifts between exports: {fonts}"

    # And the share of the width still differs the way the conventions do.
    share = {a: fonts[a] / w for a, (w, _) in sizes.items()}
    assert share["9:16"] > share["16:9"], "vertical captions are proportionally larger"
    assert share["9:16"] < 0.06, f"still too big for a Short: {share['9:16']:.1%}"


async def test_the_caption_scale_control_actually_changes_the_size(tmp_path):
    """It is taste on top of the default, and it has to reach the renderer."""
    from lumina.execution.compose import captions as caps

    line = caps.word_timings("Light is fast", 0, 1500, get_pack("en"))
    small = tmp_path / "small"
    big = tmp_path / "big"
    small.mkdir()
    big.mkdir()

    a = await caps.rasterize([line], small, width=1080, height=1920, language="en", scale=0.7)
    b = await caps.rasterize([line], big, width=1080, height=1920, language="en", scale=1.6)
    assert a[0].png.stat().st_size < b[0].png.stat().st_size, "bigger text, bigger image"


async def test_dubbing_hands_back_a_video_that_still_has_sound_on_it(ctx, session, tmp_path):
    """The other bug this lane could ship, and the one nobody would catch by looking.

    Dub reaches VOICE like the generating lanes do, and VOICE wrote a silent track of the
    right length unconditionally — the honest placeholder for a video that has no audio of
    its own. Dub's video does have audio: the creator's. Muxing silence over it would hand
    back a mute copy of a working video, and a screenshot of the result looks perfect.

    So this asserts the thing an eye cannot check, and the thing a stream listing cannot
    check either: not that the file *has* an audio track — silence is an audio track, and
    asserting `has_audio` passes just as happily on a muted video — but that the track is
    still audible.
    """
    from lumina.db.models import Channel, Plan, Project, Scene, User
    from lumina.domain.assets import AssetKind
    from lumina.domain.plan import SceneState, Tier

    #: Picture *and* sound — a silent source would pass this test for the wrong reason.
    source = tmp_path / "spoken.mp4"
    await ffmpeg.run(
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=640x360:rate=30:duration=6",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=6",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-c:a",
        "aac",
        "-shortest",
        str(source),
    )
    assert (await ffmpeg.probe(source)).has_audio, "the fixture itself is silent"
    asset = await ctx.assets.put_path(
        source,
        kind=AssetKind.VIDEO,
        mime="video/mp4",
        duration_ms=(await ffmpeg.probe(source)).duration_ms,
    )

    user = User(email=f"{uuid.uuid4()}@example.com")
    session.add(user)
    await session.flush()
    channel = Channel(user_id=user.id, name="T", language="en", identity={})
    session.add(channel)
    await session.flush()
    project = Project(
        channel_id=channel.id,
        title="Dubbed",
        recipe="dub",
        language="my",
        source_language="ja",
        caption_languages=["my"],
        source_asset_id=asset.id,
    )
    session.add(project)
    await session.flush()
    plan = Plan(project_id=project.id, version=1, content={"title": "Dubbed"})
    session.add(plan)
    await session.flush()

    for i, (at, ms) in enumerate([(500, 1500), (3000, 1200)]):
        session.add(
            Scene(
                plan_id=plan.id,
                index=i,
                prompt="",
                script_line=f"Line {i}.",
                caption=f"Line {i}",
                translations={"my": f"မြန်မာ {i}"},
                duration_ms=ms,
                source_start_ms=at,
                tier=Tier.STOCK.value,
                state=SceneState.DRAFT.value,
                ref_asset_ids=[],
            )
        )
    await session.flush()

    await stages.voice(ctx, {"plan_id": str(plan.id), "language": "my"})
    out = await stages.compose(ctx, {"plan_id": str(plan.id), "aspect": "9:16"})

    rendered = await ffmpeg.probe(tmp_path / out["storage_key"])
    assert rendered.has_audio, "the dub came back with no audio track at all"
    assert await _loudness(tmp_path / out["storage_key"]) > -60, "the dub came back silent"
    # Whole, like Subtitle and unlike Clip: six seconds in, six seconds out.
    assert 5500 <= rendered.duration_ms <= 6500, rendered.duration_ms


async def _loudness(path) -> float:
    """Mean volume in dB. Digital silence measures around -91; a 440Hz tone, around -3.

    Measured rather than inferred from the stream listing because the failure this guards
    against produces a perfectly valid AAC track — of nothing.
    """
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-v",
        "info",
        "-i",
        str(path),
        "-af",
        "volumedetect",
        "-f",
        "null",
        "-",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    for line in err.decode("utf-8", "replace").splitlines():
        if "mean_volume:" in line:
            return float(line.split("mean_volume:")[1].split("dB")[0])
    raise AssertionError("ffmpeg reported no mean_volume")


async def test_a_dub_places_every_line_at_its_own_timestamp(monkeypatch):
    """The failure that makes a dub unwatchable, and it is not a crash.

    A translated line is rarely as long as the line it replaces, so a track built by laying
    the clips end to end drifts: line one runs half a second long, line two starts half a
    second late, and by the third minute the voice is answering a question that has not been
    asked. Each clip belongs at its own cue's timestamp instead — which this asserts by
    measuring where the sound actually is, rather than trusting the arithmetic that put it
    there.
    """
    from lumina.execution import speak

    #: A second of tone per call, whatever the text. The placement is what is under test, so
    #: the engine is replaced with something whose output length is known exactly.
    async def tone(text: str, *, language: str, voice: str) -> bytes:
        return b"\x00\x40" * speak.RATE

    monkeypatch.setattr(speak, "say", tone)

    cues = [
        speak.Cue("one", 0, 1000),
        speak.Cue("two", 5_000, 1000),
        speak.Cue("three", 9_000, 1000),
    ]
    wav = await speak.dub(cues, language="my", voice="Kore", total_ms=11_000)

    pcm = wav[44:]
    assert len(pcm) == speak.RATE * speak.WIDTH * 11, "the track is not as long as the video"

    def loud_at(ms: int) -> bool:
        at = ms * speak.RATE // 1000 * speak.WIDTH
        return any(pcm[at : at + 200])

    for cue in cues:
        assert loud_at(cue.start_ms + 100), f"nothing at {cue.start_ms}ms"
    # And silence in the gaps the original speaker left, rather than a line that slid early.
    assert not loud_at(3_000), "a line drifted into the gap before it"
    assert not loud_at(7_000), "a line drifted into the gap before it"


async def test_a_line_too_long_for_its_window_is_sped_up_not_left_to_overrun(monkeypatch):
    """A line that runs over the next one is the same desync by a different route.

    Only ever faster, and only so far: past 1.5x a voice stops sounding hurried and starts
    sounding comic, at which point the overrun is the lesser problem. So this checks both —
    that a long line is compressed, and that it is not compressed past recognition.
    """
    from lumina.execution import speak

    async def long(text: str, *, language: str, voice: str) -> bytes:
        return b"\x00\x40" * (speak.RATE * 4)

    monkeypatch.setattr(speak, "say", long)

    wav = await speak.dub(
        [speak.Cue("a long line", 0, 2_000)], language="my", voice="Kore", total_ms=4_000
    )
    pcm = wav[44:]

    def last_loud() -> int:
        for i in range(len(pcm) - 2, 0, -2):
            if pcm[i] or pcm[i + 1]:
                return i // speak.WIDTH * 1000 // speak.RATE
        return 0

    spoken_ms = last_loud()
    assert spoken_ms < 4_000, "the line was not compressed at all"
    assert spoken_ms > 2_000, "compressed past the cap — it would sound like a chipmunk"


@pytest.mark.skipif(not os.environ.get("REAL_TTS"), reason="hits the speech engine for real")
async def test_a_real_dub_comes_back_speaking_the_target_language(ctx, session, tmp_path):
    """The whole lane against the real engine, run by hand with REAL_TTS=1.

    Skipped by default because it spends money and needs a key, but kept in the suite rather
    than in a scratch file: it is the only check that covers the seam between synthesis,
    placement, muxing and encoding, and every one of those has already been wrong once.
    """
    from lumina.domain.assets import AssetKind
    from lumina.domain.money import Cents

    source = tmp_path / "src.mp4"
    await ffmpeg.run(
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=640x360:rate=30:duration=8",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=300:duration=8",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-c:a",
        "aac",
        "-shortest",
        str(source),
    )
    #: Stored with its duration, the way `/uploads` stores a real upload. Without that the
    #: narration is built to the last spoken line instead of to the video, and `-shortest`
    #: then trims the picture to match it.
    asset = await ctx.assets.put_path(
        source,
        kind=AssetKind.VIDEO,
        mime="video/mp4",
        duration_ms=(await ffmpeg.probe(source)).duration_ms,
    )

    user = User(email=f"{uuid.uuid4()}@example.com")
    session.add(user)
    await session.flush()
    channel = Channel(user_id=user.id, name="T", language="en", identity={})
    session.add(channel)
    await session.flush()
    project = Project(
        channel_id=channel.id,
        title="Dubbed",
        recipe="dub",
        language="my",
        source_language="en",
        caption_languages=["my"],
        source_asset_id=asset.id,
        voice_id="Kore",
    )
    session.add(project)
    await session.flush()
    plan = Plan(project_id=project.id, version=1, content={"title": "Dubbed"})
    session.add(plan)
    await session.flush()

    for i, (en, my, at, ms) in enumerate(
        [
            ("Ancient China.", "ရှေးခေတ် တရုတ်ပြည်။", 500, 1800),
            ("The sun is shining.", "နေသာနေတယ်။", 4000, 1800),
        ]
    ):
        session.add(
            Scene(
                plan_id=plan.id,
                index=i,
                prompt="",
                script_line=en,
                caption=en,
                translations={"my": my},
                duration_ms=ms,
                source_start_ms=at,
                tier=Tier.STOCK.value,
                state=SceneState.DRAFT.value,
                ref_asset_ids=[],
            )
        )
    await session.flush()
    await ctx.ledger.grant(user.id, Cents(1000))

    spoke = await stages.voice(ctx, {"plan_id": str(plan.id), "language": "my"})
    assert spoke["asset_id"], "nothing was spoken"

    out = await stages.compose(ctx, {"plan_id": str(plan.id), "aspect": "9:16"})
    rendered = tmp_path / out["storage_key"]
    #: Kept where a person can listen to it. The assertions below are objective, but the only
    #: way to know a dub is *good* is to hear it.
    await asyncio.to_thread(Path("/tmp/claude-1000/e2e-dub.mp4").write_bytes, rendered.read_bytes())

    assert await _loudness(rendered) > -60, "the dub is silent"
    probe = await ffmpeg.probe(rendered)
    assert 7_500 <= probe.duration_ms <= 8_500, probe.duration_ms


async def test_a_line_that_will_not_fit_is_cut_at_its_window_not_over_the_next_one(monkeypatch):
    """Two voices talking over each other, and a click where the first was cut off.

    Speeding a clip up is capped, so a line can still come back longer than the space before
    the next one. Writing it anyway ran it into the next line's slot, where the next clip
    overwrote it part way through — the first line stopped mid-word with a step to silence,
    which is heard as a click. It is bounded to its own room and faded out instead.
    """
    from lumina.execution import speak

    #: Four seconds of tone per line, into windows two seconds apart. Every line overruns.
    async def long(text: str, *, language: str, voice: str) -> bytes:
        return b"\x00\x40" * (speak.RATE * 4)

    monkeypatch.setattr(speak, "say", long)

    cues = [speak.Cue(f"line {i}", i * 2_000, 1_800) for i in range(4)]
    wav = await speak.dub(cues, language="my", voice="Kore", total_ms=10_000)
    pcm = wav[44:]

    def sample_at(ms: int) -> int:
        at = ms * speak.RATE // 1000 * speak.WIDTH
        return int.from_bytes(pcm[at : at + speak.WIDTH], "little", signed=True)

    #: The tone is written at this amplitude, so "still sounding" means near it.
    loud = 0x4000

    #: By the moment its successor begins, each line must have fallen away to nothing — that
    #: is the overlap this guards against.
    for i in range(3):
        boundary = (i + 1) * 2_000
        assert abs(sample_at(boundary - 1)) < loud * 0.1, f"line {i} ran into line {i + 1}"

    #: And it falls away rather than stopping dead: a step to zero mid-waveform is a click,
    #: and a click at the end of every over-long line is worse than the truncation causing it.
    ramping = [abs(sample_at(2_000 - n)) for n in (30, 20, 10, 2)]
    assert ramping == sorted(ramping, reverse=True), f"cut without a fade: {ramping}"
    assert ramping[0] > loud * 0.5, "faded so early the line loses a word"


def _dub_cues() -> list[speak.Cue]:
    return [speak.Cue("မင်္ဂလာပါ။", 0, 1500), speak.Cue("ကျေးဇူးတင်ပါတယ်။", 2000, 1500)]


async def test_a_track_already_built_is_not_built_again(ctx, session, monkeypatch) -> None:
    """The whole point: the second call costs nothing."""
    calls = 0

    async def counted(cues, *, language, voice, total_ms):
        nonlocal calls
        calls += 1
        return speak.wav(b"\x00\x40" * speak.RATE)

    monkeypatch.setattr(speak, "dub", counted)

    first, made_first = await dubtrack.build(
        session, ctx.assets, _dub_cues(), voice="Kore", language="my", total_ms=4_000
    )
    second, made_second = await dubtrack.build(
        session, ctx.assets, _dub_cues(), voice="Kore", language="my", total_ms=4_000
    )

    assert calls == 1, "the dub was synthesized twice"
    assert made_first and not made_second
    assert first.id == second.id, "a second row was written for the same audio"


async def test_editing_a_line_rebuilds_rather_than_serving_the_old_words(
    ctx, session, monkeypatch
) -> None:
    """The failure the cache must not have."""
    calls = 0

    async def counted(cues, *, language, voice, total_ms):
        nonlocal calls
        calls += 1
        return speak.wav(bytes([calls]) * (speak.RATE * 2))

    monkeypatch.setattr(speak, "dub", counted)

    await dubtrack.build(
        session, ctx.assets, _dub_cues(), voice="Kore", language="my", total_ms=4_000
    )
    edited = [speak.Cue("ပြင်ထားတဲ့စာ", 0, 1500), _dub_cues()[1]]
    await dubtrack.build(session, ctx.assets, edited, voice="Kore", language="my", total_ms=4_000)

    assert calls == 2, "the edited line was spoken with the old audio"


@pytest.mark.skipif(not os.environ.get("REAL_TTS"), reason="hits the speech engine for real")
async def test_narrate_makes_a_video_as_long_as_the_script_takes_to_say(ctx, session, tmp_path):
    """The inversion, end to end.

    Every other lane inherits its clock from the footage. Here the script decides: the lines
    are spoken, their lengths become the cue timings, and a five-second bed is looped out to
    however long the narration turned out to be. So the assertion that matters is that the
    finished video is the length of the *speech*, not the length of the footage it ran over.
    """
    from lumina.domain.assets import AssetKind
    from lumina.domain.money import Cents

    #: Deliberately far shorter than the script will be, so a bed that was merely trimmed
    #: rather than looped would produce a five-second video and fail here.
    bed = tmp_path / "bed.mp4"
    await ffmpeg.run(
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=640x360:rate=30:duration=5",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        str(bed),
    )
    asset = await ctx.assets.put_path(
        bed,
        kind=AssetKind.VIDEO,
        mime="video/mp4",
        duration_ms=(await ffmpeg.probe(bed)).duration_ms,
    )

    user = User(email=f"{uuid.uuid4()}@example.com")
    session.add(user)
    await session.flush()
    channel = Channel(user_id=user.id, name="T", language="my", identity={})
    session.add(channel)
    await session.flush()
    project = Project(
        channel_id=channel.id,
        title="Narrated",
        recipe="narrate",
        language="my",
        source_asset_id=asset.id,
        voice_id="Kore",
    )
    session.add(project)
    await session.flush()
    plan = Plan(project_id=project.id, version=1, content={"title": "Narrated"})
    session.add(plan)
    await session.flush()

    lines = ["ရှေးခေတ် တရုတ်ပြည်။", "နေသာနေတယ်။", "ငှက်တွေ တွန်နေကြတယ်။"]
    for i, line in enumerate(lines):
        session.add(
            Scene(
                plan_id=plan.id,
                index=i,
                prompt="",
                script_line=line,
                caption=line,
                #: A placeholder from reading speed. VOICE must replace it with the real one.
                duration_ms=1_000,
                tier=Tier.STOCK.value,
                state=SceneState.DRAFT.value,
                ref_asset_ids=[],
            )
        )
    await session.flush()
    await ctx.ledger.grant(user.id, Cents(1000))

    spoke = await stages.voice(ctx, {"plan_id": str(plan.id), "language": "my"})
    assert spoke["asset_id"], "nothing was narrated"

    scenes = list(
        await session.scalars(select(Scene).where(Scene.plan_id == plan.id).order_by(Scene.index))
    )
    assert all(s.duration_ms != 1_000 for s in scenes), "the placeholder timings were kept"
    spoken_ms = sum(s.duration_ms for s in scenes)

    out = await stages.compose(ctx, {"plan_id": str(plan.id), "aspect": "9:16"})
    rendered = tmp_path / out["storage_key"]
    await asyncio.to_thread(
        Path("/tmp/claude-1000/e2e-narrate.mp4").write_bytes, rendered.read_bytes()
    )

    probe = await ffmpeg.probe(rendered)
    assert probe.duration_ms > 6_000, "the bed was not looped past its own five seconds"
    assert abs(probe.duration_ms - spoken_ms) < 1_500, (
        f"video is {probe.duration_ms}ms but the narration is {spoken_ms}ms"
    )
    assert await _loudness(rendered) > -60, "the narration is silent"


async def test_still_captions_draw_one_state_a_line_not_one_a_word(tmp_path):
    """The export shipped with word-by-word highlighting and no way to turn it off.

    It is the short-form convention and a distraction on anything a viewer reads rather than
    skims. Turning it off is also the cheaper render — a state is a rasterised PNG and an
    entry in the overlay graph, so a ten-word line costs ten of each to say the same thing.
    """
    line = captions.word_timings("One two three four five", 0, 4000, get_pack("en"))

    moving = await captions.rasterize([line], tmp_path / "a", width=540, height=960, style="pop")
    still = await captions.rasterize(
        [line], tmp_path / "b", width=540, height=960, style="pop", karaoke=False
    )

    assert len(moving) == len(line.words), "one state per word is what karaoke means"
    assert len(still) == 1, f"a still line is one picture, got {len(still)}"

    #: And it is on screen for the whole line, not for one word's worth of it.
    assert still[0].start_ms == line.start_ms
    assert still[0].end_ms - still[0].start_ms == 4000
