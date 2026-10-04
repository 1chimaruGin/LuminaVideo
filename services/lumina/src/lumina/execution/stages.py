"""Stage handlers.

Stages are pure functions producing immutable artifacts. Each one is idempotent: re-running
with the same inputs produces the same artifact key and must not double-charge (invariant 3).

The dependencies a stage needs are passed in rather than imported, so a stage can be run in a
test against a fake provider and a temporary directory with no infrastructure at all. That is
what makes build-order step 1 — "prove a job runs end to end and produces a file" — testable.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from lumina.db.models import Asset, Channel, Job, LedgerEntry, Project, Render, Scene
from lumina.db.models import Plan as PlanRow
from lumina.domain.assets import AssetKind
from lumina.domain.jobs import Stage, idempotency_key
from lumina.domain.money import Cents
from lumina.domain.plan import (
    KEEPS_WHOLE_SOURCE,
    TIMED_BY_NARRATION,
    Recipe,
    SceneState,
    Tier,
    assert_transition,
)
from lumina.execution import dubtrack, speak, subtitles
from lumina.execution import transcribe as transcribe_mod
from lumina.execution.compose import captions as captions_mod
from lumina.execution.compose import ffmpeg
from lumina.execution.providers import GenJob, Modality, ProviderStatus
from lumina.execution.router import Router
from lumina.intelligence import planner
from lumina.intelligence.estimator import SCENE_MODALITY
from lumina.intelligence.languages import get_pack
from lumina.queue import Queue
from lumina.state.assets import AssetStore
from lumina.state.ledger import Ledger

log = structlog.get_logger(__name__)


@dataclass
class Ctx:
    """Everything a stage is allowed to touch."""

    session: AsyncSession
    router: Router
    assets: AssetStore
    ledger: Ledger


class RejectedError(Exception):
    """Content policy said no. Never retry; always refund."""


async def _completed(session: AsyncSession, key: str) -> dict[str, Any] | None:
    """The result of identical work, if it has already been done.

    Deduplicating at the asset layer is not enough on its own: by the time the bytes come
    back, the provider has been called and the charge has been made. Invariant 3 says a
    re-run must not double-charge, so the check has to happen *before* anything is submitted.
    """
    job = await session.scalar(
        select(Job).where(Job.idempotency_key == key, Job.status == "succeeded")
    )
    return job.result if job is not None and job.result else None


async def _remember(session: AsyncSession, key: str, stage: Stage, result: dict[str, Any]) -> None:
    """Record finished work against its idempotency key, so a re-run is free."""
    await session.execute(
        text(
            """
            INSERT INTO jobs (id, stage, pool, payload, idempotency_key, status, result,
                              run_after, attempts, created_at, updated_at, finished_at)
            VALUES (gen_random_uuid(), :stage, 'light', '{}'::jsonb, :key, 'succeeded',
                    CAST(:result AS jsonb), now(), 1, now(), now(), now())
            ON CONFLICT (idempotency_key) DO UPDATE
               SET status = 'succeeded', result = EXCLUDED.result, finished_at = now()
            """
        ),
        {"stage": stage.value, "key": key, "result": json.dumps(result, sort_keys=True)},
    )


async def generate(ctx: Ctx, payload: dict[str, Any]) -> dict[str, Any]:
    """Produce one scene's picture or clip.

    Reserve → submit → poll → commit, in that order. The reservation happens before the
    provider is ever called, which is invariant 4 and the reason this function is long.
    """
    scene_id = uuid.UUID(payload["scene_id"])
    scene = await ctx.session.get(Scene, scene_id)
    if scene is None:
        raise ValueError(f"scene {scene_id} is gone")

    tier = Tier(scene.tier)
    job = GenJob(
        scene_id=scene.id,  # type: ignore[arg-type]
        modality=SCENE_MODALITY[tier],
        tier=tier,
        prompt=scene.prompt,
        language=payload.get("language", "en"),
        duration_ms=scene.duration_ms,
        seed=payload.get("seed"),
    )

    key = idempotency_key(
        Stage.GENERATE,
        {
            "scene": str(scene.id),
            "prompt": scene.prompt,
            "tier": scene.tier,
            "seed": payload.get("seed"),
        },
    )

    preview_tier = tier in (Tier.STOCK, Tier.IMAGE_MOTION)
    target = SceneState.PREVIEWING if preview_tier else SceneState.UPGRADING

    cached = await _completed(ctx.session, key)
    if cached is not None:
        # Identical work already produced an artifact. Reattach it and charge nothing —
        # no reservation, no provider call, no commit.
        _attach(scene, uuid.UUID(cached["asset_id"]), preview_tier)
        log.info("stage.generate.cached", scene=str(scene.id), key=key)
        return cached
    assert_transition(SceneState(scene.state), target)
    scene.state = target.value

    candidates = ctx.router.candidates(job)
    if not candidates:
        raise ValueError("no capable provider")

    # The hold this job was queued with, if it was queued through the draft. Approving the
    # plan is what reserves (invariant 4), so the normal path finds one already waiting and
    # must NOT take a second — that would hold twice the money for one picture.
    #
    # Falling back to reserving here covers the paths that enqueue a single job without going
    # through the draft. Those check the balance at the API before enqueuing, so this is a
    # backstop rather than the rule.
    reserve = await _held(ctx, payload)
    if reserve is None:
        reserve = await ctx.ledger.reserve(uuid.UUID(payload["user_id"]), candidates[0][1])

    # Failure handling is the worker's, not this function's.
    #
    # It used to be here: refund, mark the scene FAILED, raise. None of it survived — the
    # exception rolled back this session, so a provider error held the user's credits for ever
    # and left the scene looking like one that had simply never started. The worker fails the
    # job in a session that actually commits, and releases the hold and marks the scene there.
    #
    # So this raises and nothing else. The distinctions that matter to the caller are carried
    # by the exception type: `RejectedError` must never be retried, because a content-policy
    # refusal will refuse again and each attempt is paid for.
    handle, _ = await ctx.router.submit(job)
    result = await _await(ctx, handle)

    if result.status is ProviderStatus.REJECTED:
        raise RejectedError(result.error or "content policy")

    if result.status is not ProviderStatus.SUCCEEDED or result.url is None:
        raise RuntimeError(result.error or "generation failed")

    asset = await _materialise(ctx, result, scene)

    _attach(scene, asset.id, preview_tier)

    await ctx.ledger.commit(reserve, result.actual_cost or Cents(candidates[0][1]))
    out = {"asset_id": str(asset.id), "storage_key": asset.storage_key}
    await _remember(ctx.session, key, Stage.GENERATE, out)
    log.info("stage.generate", scene=str(scene.id), asset=asset.storage_key)
    return out


async def _held(ctx: Ctx, payload: dict[str, Any]) -> LedgerEntry | None:
    """The reservation already made for this job, if there is one.

    Matched on `job_id` and filtered to reserves that have not been discharged. Without the
    discharge filter a retry of an already-committed job would find the spent hold and try to
    commit it twice, which the single-discharge index rejects — correctly, but as a database
    error rather than as the "this was already paid for" that it actually is.
    """
    job_id = payload.get("job_id")
    if not job_id:
        return None

    discharged = select(LedgerEntry.reserve_entry_id).where(
        LedgerEntry.kind.in_(("commit", "refund")),
        LedgerEntry.reserve_entry_id.is_not(None),
    )
    found: LedgerEntry | None = await ctx.session.scalar(
        select(LedgerEntry).where(
            LedgerEntry.job_id == uuid.UUID(str(job_id)),
            LedgerEntry.kind == "reserve",
            LedgerEntry.id.not_in(discharged),
        )
    )
    return found


def _attach(scene: Scene, asset_id: uuid.UUID, preview: bool) -> None:
    """Point the scene at its new artifact.

    A final never overwrites a preview — both are kept, which is what makes revert possible.
    """
    if preview:
        scene.preview_asset_id = asset_id
        scene.state = SceneState.PREVIEW_READY.value
    else:
        scene.final_asset_id = asset_id
        scene.state = SceneState.FINAL.value
    scene.error = None


#: Deterministic palette for placeholder frames, taken from the design's scene gradients.
_PALETTE = [
    ("0x1b2a63", "0x070b1e"),
    ("0x5c3410", "0x120a04"),
    ("0x2a1f5e", "0x0a0718"),
    ("0x7a2f3c", "0x170609"),
    ("0x6b4a0d", "0x150e02"),
    ("0x0f2547", "0x04060f"),
]


async def ingest(ctx: Ctx, payload: dict[str, Any]) -> dict[str, Any]:
    """Take in a file the user already has.

    The entry point for every recipe that starts from existing media rather than from an
    idea — Subtitles only, and Clip my long video. It probes the real duration and dimensions
    rather than trusting what the client claimed, because everything downstream sizes itself
    from those numbers.
    """
    source = Path(payload["path"])
    # Even a stat is blocking, and this runs on the light pool's loop alongside thousands of
    # in-flight provider calls.
    if not await asyncio.to_thread(source.is_file):
        raise ValueError(f"nothing at {source}")

    duration = await ffmpeg.probe_duration_ms(source)
    asset = await ctx.assets.put_path(
        source,
        kind=AssetKind.VIDEO,
        mime=payload.get("mime", "video/mp4"),
        duration_ms=duration,
    )
    log.info("stage.ingest", asset=asset.storage_key, ms=duration)
    return {"asset_id": str(asset.id), "storage_key": asset.storage_key, "duration_ms": duration}


async def transcribe(ctx: Ctx, payload: dict[str, Any]) -> dict[str, Any]:
    """Speech to timed text.

    Only the recipes that start from existing media need this. Where the narration is our own
    TTS the script is already known, and forced alignment against known text is both cheaper
    and more accurate than transcribing it back — a distinction that matters most in the
    languages where ASR is weakest, which are exactly the ones the language packs target.

    The engine sits behind `Transcriber`; nothing here knows which one is running. Audio is
    extracted to mono 16 kHz first because that is what every ASR model wants, it is the same
    conversion for all of them, and doing it once means a re-transcribe never re-decodes the
    video.
    """
    asset_id = uuid.UUID(payload["asset_id"])
    duration = int(payload.get("duration_ms", 0))
    language = payload.get("language", "en")

    # A file the creator already has beats listening every time: it is exact, it is free, and
    # it works on a server with no speech engine installed — which is the only kind this
    # product has today. Listening is the fallback.
    supplied = payload.get("transcript_asset_id")
    if supplied:
        raw = (await ctx.assets.read(uuid.UUID(str(supplied)))).decode("utf-8", "replace")
        read = subtitles.parse(raw, duration_ms=duration, language=language)
        lines = [
            {"text": x.text, "start_ms": x.start_ms, "duration_ms": x.duration_ms}
            for x in read.segments
        ]
        log.info("stage.transcribe", asset=str(asset_id), lines=len(lines), engine=read.format)
        return {"lines": lines, "engine": read.format, "timed": read.timed}

    # Language-aware: the pack ranks the engines that can read its script, and the
    # environment says which of those are configured. See `transcribe.transcriber`.
    engine = transcribe_mod.transcriber(payload.get("text") or "", language=language)

    if isinstance(engine, transcribe_mod.EvenSplit):
        # The split reads no audio, so the bytes are never fetched. Not an optimisation worth
        # skipping: the asset here is the creator's whole upload, and pulling a gigabyte out
        # of storage to ignore it would dominate the runtime of every development run.
        segments = await engine.transcribe(Path(), language=language, duration_ms=duration)
    else:
        with tempfile.TemporaryDirectory(prefix="lumina-asr-") as tmp:
            work = Path(tmp)
            source = work / "source"
            source.write_bytes(await ctx.assets.read(asset_id))
            audio = await ffmpeg.extract_audio(source, work / "speech.wav")
            segments = await engine.transcribe(audio, language=language, duration_ms=duration)

    lines = [
        {"text": s.text, "start_ms": s.start_ms, "duration_ms": s.duration_ms} for s in segments
    ]
    log.info("stage.transcribe", asset=str(asset_id), lines=len(lines), engine=engine.name)
    return {"lines": lines, "engine": engine.name, "timed": True}


async def analyze(ctx: Ctx, payload: dict[str, Any]) -> dict[str, Any]:
    """Find the moments worth posting inside a long video.

    The Clip lane's whole value. Ranking is a judgement, so the score is returned alongside
    each moment rather than used to silently discard anything — a creator who disagrees with
    the ranking needs to see that it was a guess.
    """
    lines = payload.get("lines") or []
    window = int(payload.get("window_ms", 40_000))

    moments: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        start = int(line["start_ms"])
        text = str(line["text"])
        # A placeholder heuristic in the shape the real ranker will use: reward lines that
        # open a loop or carry a number, which is what tends to survive a scroll.
        score = 50
        if any(w in text.lower() for w in ("why", "how", "what if", "never", "always")):
            score += 25
        if any(ch.isdigit() for ch in text):
            score += 15
        score = min(99, score + (len(text) % 10))
        moments.append(
            {
                "index": i,
                "start_ms": start,
                "end_ms": start + window,
                "score": score,
                "line": text,
            }
        )

    # Highest score first. Typed explicitly because the moment dicts are heterogeneous.
    moments.sort(key=lambda m: -int(str(m["score"])))
    log.info("stage.analyze", found=len(moments))
    return {"moments": moments[:8]}


async def captions(ctx: Ctx, payload: dict[str, Any]) -> dict[str, Any]:
    """Rasterize the caption track for a plan.

    Every recipe ends here, which is why it is the stage all five have in common. States are
    stored as assets so a re-render reuses them: changing the caption style re-runs this and
    `compose`, and nothing else.
    """
    scenes = (
        await ctx.session.scalars(
            select(Scene)
            .where(Scene.plan_id == uuid.UUID(payload["plan_id"]))
            .order_by(Scene.index)
        )
    ).all()
    if not scenes:
        raise ValueError("nothing to caption")

    size = ffmpeg.ASPECTS.get(payload.get("aspect", "9:16"), ffmpeg.ASPECTS["9:16"])
    style = payload.get("style", "pop")
    # Where the creator dragged the caption to, as a share of the frame from the bottom.
    bottom = float(payload.get("caption_bottom", 0.16))
    scale = float(payload.get("caption_scale", 1.0))
    #: Word-by-word highlighting, or the whole line held still. The first is the short-form
    #: convention; the second is what a documentary or anything read rather than skimmed
    #: wants — and it is the cheaper of the two by a factor of the words in a line.
    karaoke = bool(payload.get("caption_karaoke", True))

    # Where each caption sits in the finished video, and the two lanes disagree about it.
    #
    # Normally scenes are laid end to end, so a caption starts where everything before it
    # ended. Subtitling does not re-cut anything — the video comes out whole — so a cue belongs
    # at the timestamp it has *in the source*. Accumulating there would drift every caption
    # later than the speech it belongs to, by the length of every gap before it.
    project = await ctx.session.get(Project, await _plan_project(ctx, scenes[0].plan_id))
    whole = project is not None and Recipe(project.recipe) in KEEPS_WHOLE_SOURCE

    # Which languages are burnt in, primary first. Empty means just the project's own, which
    # is every ordinary video; more than one is a bilingual subtitle.
    tracks = list(project.caption_languages) if project is not None else []
    primary = tracks[0] if tracks else payload.get("language", "en")
    secondary = tracks[1:]
    pack = get_pack(primary)

    #: What was *spoken*, which is what tells a translation apart from the scene's own words.
    #:
    #: This used to be `payload["language"]` — the language the captions come out as. Passing
    #: the target where `line_for` expects the source made the two equal on every project, so
    #: `line_for` took its "this is not a translation" path and burnt in `script_line`: the
    #: creator asked for Burmese captions, paid for them, and got the English transcript
    #: rendered onto the video.
    spoken = (project.source_language if project is not None else None) or primary

    at = 0
    lines: list[captions_mod.Line] = []
    for scene in scenes:
        start = scene.source_start_ms if whole and scene.source_start_ms is not None else at
        line = captions_mod.word_timings(
            line_for(scene, primary, spoken),
            start,
            scene.duration_ms,
            pack,
        )
        lines.append(
            replace(
                line,
                also=[(code, line_for(scene, code, spoken)) for code in secondary],
            )
        )
        at += scene.duration_ms

    with tempfile.TemporaryDirectory(prefix="lumina-caps-") as tmp:
        states = await captions_mod.rasterize(
            lines,
            Path(tmp),
            width=size.w,
            height=size.h,
            style=style,
            language=primary,
            bottom=bottom,
            scale=scale,
            karaoke=karaoke,
        )
        stored = [
            {
                "asset_id": str(
                    (await ctx.assets.put_path(s.png, kind=AssetKind.IMAGE, mime="image/png")).id
                ),
                "start_ms": s.start_ms,
                "end_ms": s.end_ms,
            }
            for s in states
        ]

    # Hand off. Compose needs these states, and enqueuing both from the API would race —
    # compose would claim its job and find no captions to burn in. Chaining here means the
    # dependency is expressed once, by the stage that satisfies it.
    if payload.get("render_id"):
        await Queue(ctx.session).enqueue(
            Stage.COMPOSE,
            {
                "plan_id": payload["plan_id"],
                "aspect": payload.get("aspect", "9:16"),
                "render_id": payload["render_id"],
                "caption_asset_ids": [s["asset_id"] for s in stored],
                "caption_windows": [[s["start_ms"], s["end_ms"]] for s in stored],
            },
        )

    log.info("stage.captions", scenes=len(scenes), states=len(stored), style=style)
    return {"states": stored, "count": len(stored)}


async def plan(ctx: Ctx, payload: dict[str, Any]) -> dict[str, Any]:
    """Brief -> a stored, versioned plan with its scenes.

    The plan is a user-editable artifact, not hidden reasoning (invariant 5), so it is
    persisted as rows the moment it exists: a `plans` row for the authored content and one
    `scenes` row each, so that regenerating scene four later takes a lock on scene four and
    nothing else.
    """
    project_id = uuid.UUID(payload["project_id"])
    project = await ctx.session.get(Project, project_id)
    if project is None:
        raise ValueError(f"project {project_id} is gone")

    drafted = await planner.plan(
        payload["brief"],
        recipe=Recipe(payload.get("recipe", project.recipe)),
        language=payload.get("language", project.language),
        target_ms=int(payload.get("target_ms", 60_000)),
        # Set only by the lanes whose lines are given rather than written. See planner.plan.
        lines=payload.get("lines"),
    )

    # A new version rather than an edit in place, so earlier versions stay readable and the
    # user can revert to the plan they already approved.
    highest = await ctx.session.scalar(
        select(func.max(PlanRow.version)).where(PlanRow.project_id == project_id)
    )
    row = PlanRow(
        project_id=project_id,
        version=(highest or 0) + 1,
        content={
            "title": drafted.title,
            "summary": drafted.summary,
            "recipe": drafted.recipe.value,
            "language": drafted.language,
            "aspects": [a.value for a in drafted.aspects],
            #: Indices of lines the planner added rather than the creator writing them.
            #: Carried on the plan so it survives a reload — see `written_by_us` in the API.
            "written_by_us": payload.get("written_by_us") or [],
        },
    )
    ctx.session.add(row)
    await ctx.session.flush()

    for s in drafted.scenes:
        ctx.session.add(
            Scene(
                plan_id=row.id,
                index=s.index,
                prompt=s.prompt,
                script_line=s.script_line,
                caption=s.caption,
                duration_ms=s.duration_ms,
                tier=s.tier.value,
                state=SceneState.DRAFT.value,
                ref_asset_ids=[],
            )
        )
    project.current_plan_id = row.id
    # The project takes the plan's name — unless the creator gave it one.
    #
    # Until it was planned all we had was the brief, so the title was its first 80 characters,
    # which for a lane whose whole input is a file meant projects listed as
    # "ch11_20260825_1600-1605.mp4". The planner writes a real short title, so it wins over a
    # filename. It does not win over a person: a typed name used to be overwritten by the
    # plan's, which on a transcribed lane is that video's first caption — so naming a video
    # did nothing, and the header showed a line of the transcript instead.
    if drafted.title.strip() and not payload.get("keep_title"):
        project.title = drafted.title.strip()[:80]
    await ctx.session.flush()

    log.info("stage.plan", plan=str(row.id), version=row.version, scenes=len(drafted.scenes))
    return {
        "plan_id": str(row.id),
        "version": row.version,
        "scenes": len(drafted.scenes),
        "title": drafted.title,
        "summary": drafted.summary,
    }


def line_for(scene: Scene, language: str, source_language: str) -> str:
    """This scene's line in one language.

    The scene's own text is already in the source language, so that one is not a translation
    and is never looked up — which also means a project whose primary track *is* the spoken
    language keeps working with no translations stored at all.
    """
    if language == source_language:
        return scene.caption or scene.script_line
    return scene.translations.get(language) or scene.caption or scene.script_line


async def _materialise(ctx: Ctx, result: Any, scene: Scene) -> Any:
    """Turn a provider result into stored bytes.

    A real adapter downloads the vendor's output. The fake provider has nothing to download,
    so a frame is synthesized instead — deliberately a real PNG rather than a placeholder
    string, because composition downstream has to have something it can actually render.
    """
    if not str(result.url or "").startswith("fake://"):
        raise NotImplementedError("downloading real provider output lands with the adapters")

    c0, c1 = _PALETTE[scene.index % len(_PALETTE)]
    with tempfile.TemporaryDirectory(prefix="lumina-still-") as tmp:
        frame = await ffmpeg.still(Path(tmp) / "frame.png", ffmpeg.ASPECTS["9:16"], c0, c1)
        return await ctx.assets.put_path(
            frame, kind=AssetKind.IMAGE, mime="image/png", width=1080, height=1920
        )


async def _await(ctx: Ctx, handle: Any) -> Any:
    """Poll the provider that owns this handle until it settles."""
    provider = next(p for p in ctx.router.providers if p.name == handle.provider)
    return await provider.poll(handle)


async def voice(ctx: Ctx, payload: dict[str, Any]) -> dict[str, Any]:
    """Narrate the whole plan in one pass, then split per scene.

    Two lanes arrive here from opposite directions, and the difference decides what happens
    when there is no real speech to write. Explainer and Recap generate every frame, so the
    video has no audio of its own and a silent track of the right length is the honest
    placeholder — compose muxes against its duration. Dub does have audio: the creator's. Its
    words are also a translation, not a script anyone authored, so what gets spoken is the
    caption track rather than `script_line`.
    """
    scenes = (
        await ctx.session.scalars(
            select(Scene)
            .where(Scene.plan_id == uuid.UUID(payload["plan_id"]))
            .order_by(Scene.index)
        )
    ).all()
    if not scenes:
        raise ValueError("nothing to narrate")

    project = await ctx.session.get(Project, await _plan_project(ctx, scenes[0].plan_id))
    tracks = list(project.caption_languages) if project is not None else []
    speaking = tracks[0] if tracks else payload.get("language", "en")
    heard = (project.source_language if project is not None else None) or speaking
    script = " ".join(line_for(s, speaking, heard) for s in scenes)
    total_ms = sum(s.duration_ms for s in scenes)

    #: Spoken here rather than through the router because a dub is not one utterance: every
    #: line has to land at its own cue, and the router's job shape — one prompt, one clip —
    #: cannot express that. The generating lanes still go through the router, which is why
    #: both paths exist below rather than one replacing the other.
    asset: Any | None = None
    narrating = project is not None and Recipe(project.recipe) in TIMED_BY_NARRATION

    if narrating and speak.can_speak(speaking) and (voice := _voice_of(project)):
        #: The clock is made here. Every other lane arrives with timings already decided by
        #: the source; this one has none until the words have been said, so the durations come
        #: back with the audio and are written onto the scenes before anything downstream —
        #: captions, compose — asks how long the video is.
        spoken, spans = await speak.narrate(
            [s.script_line for s in scenes], language=speaking, voice=voice
        )
        at = 0
        for scene, span in zip(scenes, spans, strict=True):
            scene.duration_ms = span
            scene.source_start_ms = None
            at += span
        with tempfile.TemporaryDirectory(prefix="lumina-voice-") as tmp:
            path = Path(tmp) / "vo.wav"
            path.write_bytes(spoken)
            asset = await ctx.assets.put_path(
                path, kind=AssetKind.AUDIO, mime="audio/wav", duration_ms=at
            )
        for s in scenes:
            s.vo_asset_id = asset.id
        await ctx.session.flush()
        log.info("stage.voice.narrated", scenes=len(scenes), voice=voice, ms=at)
        return {"asset_id": str(asset.id), "duration_ms": at}

    if speak.can_speak(speaking) and (voice := _voice_of(project)):
        cues = [
            speak.Cue(
                text=line_for(s, speaking, heard),
                start_ms=s.source_start_ms if s.source_start_ms is not None else at,
                duration_ms=s.duration_ms,
            )
            for s, at in zip(scenes, _laid_end_to_end(scenes), strict=True)
        ]
        #: The same track the creator auditioned, if they auditioned this voice on these
        #: lines. Speech is quota'd per request and a dub is one request per line, so
        #: re-synthesizing here spent a second full allowance to produce audio we already had
        #: — the difference between dubbing two videos a day and dubbing one.
        asset, made = await dubtrack.build(
            ctx.session,
            ctx.assets,
            cues,
            voice=voice,
            language=speaking,
            total_ms=await _track_length(ctx, project, cues),
        )
        log.info(
            "stage.voice.spoken",
            scenes=len(scenes),
            voice=voice,
            language=speaking,
            synthesized=made,
        )
    else:
        job = GenJob(
            scene_id=scenes[0].id,  # type: ignore[arg-type]
            modality=Modality.TEXT_TO_SPEECH,
            tier=Tier.IMAGE_MOTION,
            prompt=script,
            language=speaking,
            duration_ms=total_ms,
        )
        handle, _estimate = await ctx.router.submit(job)
        result = await _await(ctx, handle)
        if result.status is not ProviderStatus.SUCCEEDED:
            raise RuntimeError(result.error or "voice failed")
        asset = await _speech(ctx, result, total_ms)

    if asset is None:
        # No adapter can say these words yet. A lane that kept the creator's footage still has
        # the creator's audio on it, and laying silence over that would take away a working
        # video to stand in for one we cannot make: leave `vo_asset_id` unset and compose
        # copies the original track through.
        if project is not None and Recipe(project.recipe) in KEEPS_WHOLE_SOURCE:
            log.info("stage.voice.kept_source", scenes=len(scenes), language=speaking)
            return {"asset_id": None, "kept_source_audio": True}
        with tempfile.TemporaryDirectory(prefix="lumina-voice-") as tmp:
            track = await ffmpeg.silence(Path(tmp) / "vo.m4a", total_ms)
            asset = await ctx.assets.put_path(
                track, kind=AssetKind.AUDIO, mime="audio/mp4", duration_ms=total_ms
            )

    for s in scenes:
        s.vo_asset_id = asset.id
    log.info("stage.voice", scenes=len(scenes), asset=asset.storage_key)
    return {"asset_id": str(asset.id)}


def _voice_of(project: Project | None) -> str | None:
    """Which voice this project is dubbed in, if its creator has chosen one.

    None is a real answer and not a default to paper over: nobody has said how the video
    should sound, so nothing should be spoken over it yet.
    """
    return project.voice_id if project is not None and project.voice_id else None


def _laid_end_to_end(scenes: Sequence[Scene]) -> list[int]:
    """Where each scene starts when they are simply concatenated.

    The fallback for a scene with no timestamp of its own. A lane that keeps the whole source
    has `source_start_ms` on every scene and never reaches this; one that does not has no
    original clock to sync to, so the only meaningful position is "after the one before".
    """
    at, out = 0, []
    for scene in scenes:
        out.append(at)
        at += scene.duration_ms
    return out


async def _track_length(ctx: Ctx, project: Project | None, cues: Sequence[speak.Cue]) -> int:
    """How long the narration track has to be.

    As long as the video, on a lane that keeps the video. Not the sum of the line durations,
    and not the last line's end either — `stitch` passes `-shortest`, so a narration track
    that stops when the talking stops *truncates the picture there*. An eight-second video
    whose last line ended at 5.8s came back 5.8 seconds long, missing its final shot, with
    nothing in the logs to say why.

    Falling back to the last cue's end covers a lane with no source to measure, where the
    lines are the only clock there is.
    """
    longest = max((c.start_ms + c.duration_ms for c in cues), default=0)
    if project is not None and project.source_asset_id is not None:
        source = await ctx.session.get(Asset, project.source_asset_id)
        if source is not None and source.duration_ms:
            return max(longest, source.duration_ms)
    return longest


async def _speech(ctx: Ctx, result: Any, total_ms: int) -> Any | None:
    """The narration a provider actually produced, or None when none of it was real.

    Returning None rather than a silent stand-in is what lets `voice` tell its two cases
    apart. Deciding that inside this helper — where the provider result is — keeps the caller
    from having to ask a vendor question it has no business asking (invariant 2).
    """
    if not result.url or str(result.url).startswith("fake://"):
        return None
    raise NotImplementedError("downloading real speech lands with the TTS adapter")


async def compose(ctx: Ctx, payload: dict[str, Any]) -> dict[str, Any]:
    """Assemble the scenes into one deliverable.

    This is where most videos are actually made: stills plus programmatic motion, at roughly
    one percent of the cost of premium video generation. Every scene becomes a Ken Burns
    clip, the clips are concatenated, and the narration is muxed over the result.

    Zoom direction alternates between scenes. A sequence of identical pushes reads as a
    slideshow with an effect stuck on, which is the tell of an auto-generated video.
    """
    aspect = payload.get("aspect", "9:16")
    size = ffmpeg.ASPECTS.get(aspect, ffmpeg.ASPECTS["9:16"])

    scenes = (
        await ctx.session.scalars(
            select(Scene)
            .where(Scene.plan_id == uuid.UUID(payload["plan_id"]))
            .order_by(Scene.index)
        )
    ).all()
    if not scenes:
        raise ValueError("nothing to compose")

    # The lanes that keep the creator's footage cut from it; the lanes that generate frames
    # animate stills. Which one applies is a property of the project, not a branch on the
    # recipe name — a scene either points into a source video or it does not.
    project = await ctx.session.get(Project, (await _plan_project(ctx, scenes[0].plan_id)))
    source_id = project.source_asset_id if project is not None else None

    with tempfile.TemporaryDirectory(prefix="lumina-compose-") as tmp:
        work = Path(tmp)
        clips: list[Path] = []

        #: Fetched when some scene points into it — or when the whole lane is *about* the
        #: footage. Narrate's scenes carry no source timestamps at all (its clock comes from
        #: the narration), so the "does anything point into it" test alone left the bed
        #: unfetched and the lane with nothing to draw.
        narrated = project is not None and Recipe(project.recipe) in TIMED_BY_NARRATION
        source: Path | None = None
        if source_id is not None and (
            narrated or any(s.source_start_ms is not None for s in scenes)
        ):
            source = work / "source.mp4"
            source.write_bytes(await ctx.assets.read(source_id))

        # Subtitling hands the video back whole. Cutting it into the captioned moments is
        # Clip's job and the opposite of this one: a sixty-second video with two cues came out
        # five seconds long, holding only the two moments that happened to have captions.
        whole = (
            project is not None
            and Recipe(project.recipe) in KEEPS_WHOLE_SOURCE
            and source is not None
        )
        #: Reframing folded into the caption pass for the lane that keeps the whole video.
        #:
        #: This used to call `cut` first — a full re-encode of the entire video to scale and
        #: crop it — and then re-encode all of it again to burn the captions on. Two passes
        #: over a three-minute file, the second undoing none of the first, and a generation of
        #: lossy quality given away in between. The scale and crop are a filter; they belong
        #: in the graph that is already running.
        reframe = ""
        #: Narration is read off the scenes, and the whole-source path empties them below, so
        #: the answer is taken before they go. Keeping every *frame* and keeping the original
        #: *sound* are two different things and only Subtitle wants both: a dub keeps the
        #: picture precisely so it can replace what is heard over it. Reading this after the
        #: blanking silently discarded the dubbed track — the video came back in its original
        #: voice, which is the one outcome that lane exists to prevent.
        narration = next((s.vo_asset_id for s in scenes if s.vo_asset_id), None)

        #: The lane where the script decides the length. The footage is a bed rather than the
        #: subject: looped if the script outruns it, cut short if it does not. The scenes are
        #: emptied for the same reason the whole-source lane empties them — there is one clip,
        #: not one per line — but the reason differs, so the two paths stay separate.
        #: A bed is the *fallback*, not the design.
        #:
        #: Real faceless video changes visual every three to six seconds, so a narrated video
        #: is normally a sequence: one picture per beat, each held for as long as its line
        #: takes to say. Looping a single clip under the whole thing is right for exactly one
        #: genre — narration over gameplay — and reads as low effort everywhere else. So the
        #: bed runs only when the creator gave nothing to cut to.
        if narrated and not any(s.preview_asset_id or s.final_asset_id for s in scenes):
            if source is None:
                raise ValueError("nothing to narrate over")
            spoken_ms = sum(s.duration_ms for s in scenes)
            clips.append(await ffmpeg.bed(source, work / "bed.mp4", ms=spoken_ms, size=size))
            scenes = []
        elif whole and source is not None:
            clips.append(source)
            reframe = f"scale={size}:force_original_aspect_ratio=increase,crop={size.wh},setsar=1"
            scenes = []

        for i, scene in enumerate(scenes):
            if source is not None and scene.source_start_ms is not None:
                # Cut and reframe. Re-encoded rather than stream-copied because a copy can
                # only cut on a keyframe, which moves the real start by seconds — fatal when
                # the whole point is that the first line lands immediately.
                clips.append(
                    await ffmpeg.cut(
                        source,
                        work / f"clip-{i:03d}.mp4",
                        start_ms=scene.source_start_ms,
                        end_ms=scene.source_start_ms + scene.duration_ms,
                        size=size,
                    )
                )
                continue

            asset_id = scene.final_asset_id or scene.preview_asset_id
            if asset_id is None:
                raise ValueError(f"scene {scene.index} has no picture to compose")

            #: A beat's picture may be a clip the creator uploaded rather than a still.
            #:
            #: Ken Burns takes a frame, so a video handed to it would be decoded as a single
            #: image — the first frame, held silently for the whole beat. Cut to length and
            #: looped if the beat outlasts it, which is what `bed` already does.
            picture = await ctx.session.get(Asset, asset_id)
            if picture is not None and picture.kind == AssetKind.VIDEO.value:
                shot = work / f"shot-{i:03d}.mp4"
                shot.write_bytes(await ctx.assets.read(asset_id))
                clips.append(
                    await ffmpeg.bed(
                        shot, work / f"clip-{i:03d}.mp4", ms=scene.duration_ms, size=size
                    )
                )
                continue

            frame = work / f"frame-{i:03d}.png"
            frame.write_bytes(await ctx.assets.read(asset_id))
            clips.append(
                await ffmpeg.ken_burns(
                    frame,
                    work / f"clip-{i:03d}.mp4",
                    ms=scene.duration_ms,
                    size=size,
                    zoom_in=i % 2 == 0,
                )
            )

        audio: Path | None = None
        if narration is not None:
            audio = work / "narration.m4a"
            audio.write_bytes(await ctx.assets.read(narration))

        mark = await _watermark(ctx, project, work)
        overlay = await _caption_overlay(ctx, payload, work, size, reframe, mark)
        out = await ffmpeg.stitch(clips, audio, work / "out.mp4", work, captions=overlay)
        duration = await ffmpeg.probe_duration_ms(out)
        asset = await ctx.assets.put_path(
            out,
            kind=AssetKind.VIDEO,
            mime="video/mp4",
            duration_ms=duration,
            width=size.w,
            height=size.h,
        )

    # Close out the delivery this render was started for. Done here rather than by the worker
    # so the row and the asset it points at commit together — a render marked ready whose
    # asset id is still null is a download button that 404s.
    render_id = payload.get("render_id")
    if render_id:
        render = await ctx.session.get(Render, uuid.UUID(str(render_id)))
        if render is not None:
            render.asset_id = asset.id
            render.status = "ready"

    log.info("stage.compose", scenes=len(scenes), ms=duration, asset=asset.storage_key)
    return {
        "asset_id": str(asset.id),
        "storage_key": asset.storage_key,
        "scenes": len(scenes),
        "duration_ms": duration,
        "aspect": aspect,
    }


async def _watermark(ctx: Ctx, project: Project | None, work: Path) -> Path | None:
    """The mark to burn into this render, if any.

    Three answers, and the default is ours: a channel that has said nothing gets the Lumina
    mark. `own` uses the logo on the identity kit — falling back to ours rather than to
    nothing if that logo has gone missing, because a render is not the place to discover that
    an asset was deleted. `none` is none.

    Who is *allowed* to choose is deliberately not decided here. That is a billing question
    and there is no plan on an account yet — only a credit ledger — so this honours whatever
    the channel says and the paywall goes in front of the control that sets it.
    """
    if project is None:
        return ffmpeg.own_logo()
    channel = await ctx.session.get(Channel, project.channel_id)
    if channel is None:
        return ffmpeg.own_logo()

    choice = str(channel.identity.get("watermark", "lumina"))
    if choice == "none":
        return None
    if choice == "own" and channel.logo_asset_id is not None:
        logo = work / "logo.png"
        try:
            logo.write_bytes(await ctx.assets.read(channel.logo_asset_id))
        except Exception:
            log.warning("compose.logo_missing", channel=str(channel.id))
            return ffmpeg.own_logo()
        return logo
    return ffmpeg.own_logo()


async def _caption_overlay(
    ctx: Ctx,
    payload: dict[str, Any],
    work: Path,
    size: ffmpeg.Size,
    base: str = "",
    watermark: Path | None = None,
) -> tuple[list[str], str] | None:
    """Materialise the caption states this render was handed, ready for the filter graph.

    Returns None when there are none, so an uncaptioned render still stream-copies its video
    instead of paying for a re-encode that changes nothing.
    """
    ids = payload.get("caption_asset_ids") or []
    windows = payload.get("caption_windows") or []
    if not ids or len(ids) != len(windows):
        #: Still a graph when the frames need reframing — see `overlay_filter`.
        inputs, graph = captions_mod.overlay_filter(
            [], work, (size.w, size.h), fps=ffmpeg.FPS, base=base, watermark=watermark
        )
        return (inputs, graph) if graph else None

    states: list[captions_mod.CaptionState] = []
    for i, (asset_id, (start_ms, end_ms)) in enumerate(zip(ids, windows, strict=True)):
        png = work / f"cap-{i:04d}.png"
        png.write_bytes(await ctx.assets.read(uuid.UUID(str(asset_id))))
        states.append(
            captions_mod.CaptionState(png=png, start_ms=int(start_ms), end_ms=int(end_ms))
        )

    inputs, graph = captions_mod.overlay_filter(
        states, work, (size.w, size.h), fps=ffmpeg.FPS, base=base, watermark=watermark
    )
    return (inputs, graph) if graph else None


async def _plan_project(ctx: Ctx, plan_id: uuid.UUID) -> uuid.UUID | None:
    """Which project a plan belongs to. One column, so a scalar rather than a whole row."""
    found: uuid.UUID | None = await ctx.session.scalar(
        select(PlanRow.project_id).where(PlanRow.id == plan_id)
    )
    return found


async def record_cost(
    session: AsyncSession, job_id: uuid.UUID, provider: str, est: int, act: int
) -> None:
    """COGS telemetry. The monthly reconciliation is a query over these columns."""
    job = await session.get(Job, job_id)
    if job is None:
        return
    job.provider = provider
    job.estimated_cents = est
    job.actual_cents = act
