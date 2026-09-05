"""Build-order step 1: prove a job runs end to end and produces a file.

Runs against a real Postgres and the local-disk asset store, with the fake provider. No
mocking of the database, the queue or the ledger — those are exactly the parts where the
interesting bugs live, and a test that mocks them proves nothing.

Skips cleanly when no database is configured, so the unit suite stays runnable anywhere.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lumina.db.models import Channel, Job, Plan, Project, Scene, User
from lumina.domain.jobs import Pool, Stage, idempotency_key
from lumina.domain.money import Cents
from lumina.domain.plan import SceneState, Tier
from lumina.execution import stages
from lumina.execution.providers.fake import FakeProvider
from lumina.execution.router import Router
from lumina.queue import Queue
from lumina.queue.queue import QueuedJob
from lumina.state.assets import AssetStore, LocalBlobs
from lumina.state.ledger import Ledger

DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB, reason="TEST_DATABASE_URL not set")


@pytest.fixture
async def session():
    engine = create_async_engine(DB or "", poolclass=None)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
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


async def _seed(session, recipe: str = "explainer", **project) -> tuple[User, Plan, list[Scene]]:
    user = User(email=f"{uuid.uuid4()}@example.com")
    session.add(user)
    await session.flush()

    channel = Channel(user_id=user.id, name="Test", language="en", identity={})
    session.add(channel)
    await session.flush()

    project = Project(channel_id=channel.id, title="Webb", recipe=recipe, language="en", **project)
    session.add(project)
    await session.flush()

    plan = Plan(project_id=project.id, version=1, content={"title": "Webb"})
    session.add(plan)
    await session.flush()

    scenes = [
        Scene(
            plan_id=plan.id,
            index=i,
            prompt=f"scene {i}",
            script_line=f"line {i}",
            duration_ms=5000,
            tier=Tier.IMAGE_MOTION.value,
            state=SceneState.DRAFT.value,
            ref_asset_ids=[],
        )
        for i in range(3)
    ]
    session.add_all(scenes)
    await session.flush()
    return user, plan, scenes


async def test_generate_produces_a_file_and_charges_once(ctx, session, tmp_path):
    user, _plan, scenes = await _seed(session)
    await ctx.ledger.grant(user.id, Cents(1000))

    out = await stages.generate(
        ctx, {"scene_id": str(scenes[0].id), "user_id": str(user.id), "language": "en"}
    )

    # A real file exists on disk, at its content-addressed key.
    written = tmp_path / out["storage_key"]
    assert written.is_file(), "the stage did not write an artifact"
    assert written.read_bytes(), "the artifact is empty"

    # The scene advanced and kept its preview.
    assert scenes[0].state == SceneState.PREVIEW_READY.value
    assert scenes[0].preview_asset_id is not None

    # Exactly one reserve and one commit; nothing left held.
    available, reserved, committed = await ctx.ledger.balance(user.id)
    assert reserved == 0, "credits are still held after a successful generation"
    assert committed > 0
    assert available == 1000 - committed


async def test_rerunning_a_stage_reuses_the_asset(ctx, session):
    """Invariant 3: same inputs, same artifact key, no second object."""
    user, _plan, scenes = await _seed(session)
    await ctx.ledger.grant(user.id, Cents(1000))

    a = await stages.generate(ctx, {"scene_id": str(scenes[0].id), "user_id": str(user.id)})
    scenes[0].state = SceneState.PREVIEW_READY.value
    b = await stages.generate(ctx, {"scene_id": str(scenes[0].id), "user_id": str(user.id)})

    assert a["storage_key"] == b["storage_key"]
    assert a["asset_id"] == b["asset_id"], "the same bytes were stored twice"


async def test_a_rejected_job_releases_its_hold_and_fails_its_scene(session, tmp_path):
    """A content-policy refusal must give the money back — through the path that really runs.

    Deliberately driven by the worker rather than by calling the stage directly. The refund
    used to live inside `generate`, where it looked correct and never reached the database:
    the exception that failed the stage rolled back the session the refund was written to. A
    test that called the stage directly saw the refund in its own uncommitted session and
    passed, while production held the user's credits for ever.

    So this asserts what a separate connection can see afterwards, which is the only version
    of the claim that means anything.
    """
    from lumina.workers.light import stage_handler
    from lumina.workers.runner import Worker

    user, _plan, scenes = await _seed(session)
    await Ledger(session).grant(user.id, Cents(1000))
    scenes[0].prompt = "__reject__ a banned thing"

    job_id = await Queue(session).enqueue(
        Stage.GENERATE, {"scene_id": str(scenes[0].id), "user_id": str(user.id)}
    )
    reserve = await Ledger(session).reserve(user.id, Cents(20), job_id=job_id)
    assert reserve is not None
    await session.commit()

    # Handed straight to the worker rather than claimed out of the queue: claiming is covered
    # by its own tests, and this one is about what happens *after* a job fails.
    queued = QueuedJob(
        id=job_id,
        stage=Stage.GENERATE,
        pool=Pool.LIGHT,
        payload={"scene_id": str(scenes[0].id), "user_id": str(user.id)},
        attempts=1,
        idempotency_key="probe",
    )

    # The worker builds its own session from DATABASE_URL, so the suite points that at the
    # test database too (see the `test` target). Without it a test would drive a worker that
    # reads and writes the development database.
    assert os.environ.get("DATABASE_URL") == DB, (
        "run via `make test`: the worker's own session must reach the test database"
    )

    engine = create_async_engine(DB or "")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        worker = Worker(Pool.LIGHT, {Stage.GENERATE: stage_handler(stages.generate)})
        await worker._run_one(queued)

        # A fresh session: anything the failed stage's own transaction "did" is gone.
        async with maker() as s:
            available, held, committed = await Ledger(s).balance(user.id)
            assert held == 0, "a rejected job left credits held"
            assert committed == 0, "a rejected job was charged"
            assert available == 1000, "the hold was not returned in full"

            scene = await s.get(Scene, scenes[0].id)
            assert scene is not None
            assert scene.state == SceneState.FAILED.value, (
                "a scene whose job died must not look like one that never started"
            )
            assert scene.error, "the failure must say something the user can act on"

            job = await s.get(Job, job_id)
            assert job is not None and job.status == "failed"
    finally:
        await engine.dispose()


async def test_voice_then_compose_produces_a_playable_video(ctx, session, tmp_path):
    """The whole preview tier, end to end: stills, narration, motion, one file.

    Asserts the artifact is a real H.264/AAC video of roughly the right length, not merely
    that the stage returned without raising. "The stage ran" is not the same claim as "the
    creator has something they can post", and only the second one matters.
    """
    user, plan, scenes = await _seed(session)
    await ctx.ledger.grant(user.id, Cents(1000))

    for s in scenes:
        await stages.generate(ctx, {"scene_id": str(s.id), "user_id": str(user.id)})

    await stages.voice(ctx, {"plan_id": str(plan.id), "language": "en"})
    assert all(s.vo_asset_id is not None for s in scenes)

    out = await stages.compose(ctx, {"plan_id": str(plan.id), "aspect": "9:16"})
    assert out["scenes"] == 3

    video = tmp_path / out["storage_key"]
    assert video.is_file()
    assert video.stat().st_size > 20_000, "suspiciously small for three seconds of 1080p"

    # Three 5s scenes; allow slack for frame rounding at the clip boundaries.
    assert 14_000 <= out["duration_ms"] <= 16_000, out["duration_ms"]

    streams = await _probe(video)
    assert "h264" in streams, streams
    assert "aac" in streams, streams
    assert "1080" in streams and "1920" in streams, streams


async def test_dub_leaves_the_creators_own_audio_alone_when_nothing_can_speak(ctx, session):
    """A silent track over a video that already had a voice is worse than no dub at all.

    Nothing can say a Burmese line yet. The lane that reached this stage kept every frame of
    the creator's video, so it still has their audio on it — and the stage that used to write
    silence unconditionally would have replaced a video that works with one that is mute.
    """
    user, plan, scenes = await _seed(
        session, recipe="dub", source_language="ja", caption_languages=["my"]
    )
    await ctx.ledger.grant(user.id, Cents(1000))

    out = await stages.voice(ctx, {"plan_id": str(plan.id), "language": "my"})

    assert out["kept_source_audio"] is True
    assert out["asset_id"] is None
    assert all(s.vo_asset_id is None for s in scenes), "nothing was muxed over their audio"


async def test_a_generated_lane_still_gets_a_track_of_the_right_length(ctx, session):
    """The other side of the same branch, which must not regress.

    Explainer draws every frame, so there is no original audio to protect: silence is not a
    downgrade there, it is the only thing compose has to mux against.
    """
    user, plan, scenes = await _seed(session)
    await ctx.ledger.grant(user.id, Cents(1000))

    out = await stages.voice(ctx, {"plan_id": str(plan.id), "language": "en"})

    assert out["asset_id"], "a lane with no audio of its own needs a track"
    assert all(s.vo_asset_id is not None for s in scenes)


async def test_dub_speaks_the_translation_and_not_the_line_it_was_translated_from(ctx, session):
    """What gets spoken is the target language, and the two are trivial to swap.

    Captions shipped this exact bug once: the target was passed where the source belonged, the
    "is this a translation?" test came out false, and creators got the original transcript
    burnt onto the video they had paid to translate. The dubbing path reads the same two
    fields, so it can fail the same way — silently, and only audibly at the end.
    """
    user, plan, scenes = await _seed(
        session, recipe="dub", source_language="ja", caption_languages=["my"]
    )
    for i, scene in enumerate(scenes):
        scene.translations = {"my": f"မြန်မာ {i}"}
    await session.flush()
    await ctx.ledger.grant(user.id, Cents(1000))

    said: list[str] = []
    submit = ctx.router.submit

    async def spy(job):
        said.append(job.prompt)
        return await submit(job)

    ctx.router.submit = spy  # type: ignore[method-assign]
    try:
        await stages.voice(ctx, {"plan_id": str(plan.id), "language": "my"})
    finally:
        ctx.router.submit = submit  # type: ignore[method-assign]

    assert said, "the stage never asked anyone to speak"
    assert "မြန်မာ 0" in said[0], said[0]
    assert "line 0" not in said[0], "it read out the Japanese it was supposed to replace"


async def _probe(path) -> str:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=codec_name,width,height",
        "-of",
        "csv=p=0",
        str(path),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    return out.decode()


async def test_queue_enqueue_is_idempotent(session):
    """Identical work enqueued twice is one job — the unique key does the work.

    The payload is unique per run, and the job is claimed until it is found rather than
    within some limit. Two earlier versions were coupled to global state: the first used a
    fixed payload and `limit=10`, the second raised it to 500 — both passed only while the
    jobs table happened to be shallow enough, and a shared queue is never reliably shallow.
    How deep the backlog is has nothing to do with whether the unique key works.
    """
    q = Queue(session)
    payload = {"scene_id": str(uuid.uuid4()), "seed": 7}

    a = await q.enqueue(Stage.GENERATE, payload)
    b = await q.enqueue(Stage.GENERATE, payload)
    assert a == b, "the same work produced two jobs"

    key = idempotency_key(Stage.GENERATE, payload)
    rows = await session.scalar(
        select(func.count()).select_from(Job).where(Job.idempotency_key == key)
    )
    assert rows == 1

    # Drain until it surfaces. Claiming is destructive, so it can only ever appear once —
    # which is the property under test, independent of how much else is queued.
    seen = 0
    for _ in range(50):
        batch = await q.claim(Pool.LIGHT, limit=200)
        if not batch:
            break
        seen += len([c for c in batch if c.id == a])
        if seen:
            break
    assert seen == 1, "the job was not claimable exactly once"


async def test_rerunning_a_stage_does_not_charge_twice(ctx, session):
    """Invariant 3, second half: same inputs must not double-charge.

    The asset layer deduplicates *after* the provider has been called and paid. That is not
    enough — the charge has to be avoided too, which means the check has to happen before
    anything is submitted.
    """
    user, _plan, scenes = await _seed(session)
    await ctx.ledger.grant(user.id, Cents(1000))

    await stages.generate(ctx, {"scene_id": str(scenes[0].id), "user_id": str(user.id)})
    _, _, after_first = await ctx.ledger.balance(user.id)

    scenes[0].state = SceneState.PREVIEW_READY.value
    await stages.generate(ctx, {"scene_id": str(scenes[0].id), "user_id": str(user.id)})
    _, reserved, after_second = await ctx.ledger.balance(user.id)

    assert reserved == 0
    assert after_second == after_first, "re-running the same work charged a second time"
