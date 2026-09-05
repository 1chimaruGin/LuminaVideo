"""Shared worker loop.

Both pools run this; they differ only in which `Pool` they claim for and which stage
handlers they register. Splitting them into separate deployables happens in the Dockerfile,
not here — mixing resource profiles in one pool is the classic mistake that triples cost.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lumina.db.base import sessionmaker_for
from lumina.db.models import LedgerEntry, Scene
from lumina.domain.jobs import Pool, Stage
from lumina.domain.plan import SceneState
from lumina.queue import Queue
from lumina.state.ledger import Ledger

#: States a scene can legally move to FAILED from. A scene that already reached FINAL keeps
#: its finished asset — a failed *upgrade* must not throw away the video the user already has.
_CAN_FAIL = frozenset({SceneState.DRAFT, SceneState.PREVIEWING, SceneState.UPGRADING})

log = structlog.get_logger(__name__)

Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

#: How long to sleep when the queue is empty and no NOTIFY arrives. Also bounds how long a
#: reaped job waits, since nothing notifies about those.
IDLE_TIMEOUT_S = 5.0


class Worker:
    def __init__(self, pool: Pool, handlers: Mapping[Stage, Handler], *, concurrency: int = 8):
        self.pool = pool
        self.handlers = handlers
        self.concurrency = concurrency
        self._stop = asyncio.Event()

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        log.info(
            "worker.start", pool=self.pool.value, stages=sorted(s.value for s in self.handlers)
        )
        sm = sessionmaker_for()

        while not self._stop.is_set():
            claimed = 0
            async with sm() as session:
                queue = Queue(session)
                jobs = await queue.claim(self.pool, limit=self.concurrency)
                await session.commit()
                claimed = len(jobs)

            if not jobs:
                # Nothing runnable. Wait for a NOTIFY, but wake up anyway on timeout —
                # notifications can be missed across a reconnect, and reaped jobs are
                # runnable without anyone announcing them.
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=IDLE_TIMEOUT_S)
                continue

            await asyncio.gather(*(self._run_one(job) for job in jobs))
            log.debug("worker.batch", pool=self.pool.value, claimed=claimed)

    async def _run_one(self, job: Any) -> None:
        handler = self.handlers.get(job.stage)
        sm = sessionmaker_for()
        async with sm() as session:
            queue = Queue(session)
            if handler is None:
                await self._failed(session, job, f"no handler for stage {job.stage.value}")
                await session.commit()
                return
            try:
                # The job's own id goes in with the payload. A stage needs it to find the
                # credit hold the job was queued with — without it, `generate` cannot tell
                # "already paid for at plan approval" from "not paid for yet" and reserves a
                # second time, holding twice the money for one picture.
                result = await handler({**job.payload, "job_id": str(job.id)})
            except Exception as exc:
                log.exception("job.failed", job_id=str(job.id), stage=job.stage.value)
                await self._failed(session, job, str(exc))
            else:
                await queue.succeed(job.id, result)
            await session.commit()

    async def _failed(self, session: AsyncSession, job: Any, error: str) -> None:
        """Everything that has to be true once a job has definitively failed.

        Done here rather than inside the stage because a stage's own session is rolled back by
        the exception that failed it. `generate` calls `refund()` and then raises — and that
        refund never reached the database, so a provider error held the user's credits for
        ever. Same for the scene's FAILED state, which is why a failed scene showed as one
        that had simply never started.

        This session is the one that commits, so this is where those have to happen.

        Retry policy stays the caller's decision: a transport blip should retry and a policy
        rejection never should. Until a stage says which it was, a failure is final — and a
        final failure must release its money.
        """
        await Queue(session).fail(job.id, error)
        await self._release(session, job)
        await self._mark_scene(session, job, error)

    async def _release(self, session: AsyncSession, job: Any) -> None:
        """Refund whatever this job was holding.

        Scoped to holds that have not already been discharged, so a stage that *did* manage to
        refund or commit before failing is not double-discharged — the ledger's single-
        discharge index would reject that, correctly, but as a database error rather than as
        the "already settled" it actually is.
        """
        discharged = select(LedgerEntry.reserve_entry_id).where(
            LedgerEntry.kind.in_(("commit", "refund")),
            LedgerEntry.reserve_entry_id.is_not(None),
        )
        held = (
            await session.scalars(
                select(LedgerEntry).where(
                    LedgerEntry.job_id == job.id,
                    LedgerEntry.kind == "reserve",
                    LedgerEntry.id.not_in(discharged),
                )
            )
        ).all()
        for entry in held:
            await Ledger(session).refund(entry, "job_failed")
        if held:
            log.info("job.refunded", job_id=str(job.id), entries=len(held))

    async def _mark_scene(self, session: AsyncSession, job: Any, error: str) -> None:
        """Show the failure on the card it belongs to.

        Per-scene progress is the storyboard's whole promise; a scene whose job died silently
        reverting to "not started" is the one state that breaks it.
        """
        scene_id = job.payload.get("scene_id")
        if not scene_id:
            return
        scene = await session.get(Scene, uuid.UUID(str(scene_id)))
        if scene is None:
            return
        if SceneState(scene.state) in _CAN_FAIL:
            scene.state = SceneState.FAILED.value
        scene.error = error[:500]


async def serve(worker: Worker) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, worker.request_stop)
    await worker.run()
