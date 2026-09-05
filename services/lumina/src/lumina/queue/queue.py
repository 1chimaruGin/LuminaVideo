"""Postgres-backed job queue.

Deviation 1 in TECHSTACK.md. The queue lives in the same database as the ledger and the
scene state machine so that reserving credits, transitioning a scene, and enqueuing the work
commit or fail as ONE transaction. A Redis queue here would be a dual write with no shared
transaction, and its two failure modes — charged-but-never-ran, and ran-twice-charged-twice —
are both invariant 4 violations that cost real money.

Mechanics:
  - `SELECT ... FOR UPDATE SKIP LOCKED` for contention-free claiming across N workers.
  - `LISTEN/NOTIFY` for wakeups, so idle workers do not poll a hot table.
  - `idempotency_key` under a UNIQUE constraint: enqueuing identical work twice is a no-op
    that returns the original job.
  - A lease (`claimed_until`). A worker that dies mid-flight has its job returned to the
    queue by `reap_expired`, not lost.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from lumina.domain.jobs import Pool, Stage, idempotency_key

#: How long a worker may hold a job before it is considered dead and redelivered.
DEFAULT_LEASE = timedelta(minutes=15)


#: Channel workers LISTEN on. One per pool so a render worker is not woken by planning work.
def channel(pool: Pool) -> str:
    return f"lumina_jobs_{pool.value.replace('-', '_')}"


@dataclass(frozen=True, slots=True)
class QueuedJob:
    id: uuid.UUID
    stage: Stage
    pool: Pool
    payload: dict[str, Any]
    attempts: int
    idempotency_key: str


class Queue:
    """Thin wrapper over the `jobs` table. Takes a session so the caller controls the
    transaction — enqueue is meant to be called *inside* the caller's unit of work."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(
        self,
        stage: Stage,
        payload: dict[str, Any],
        *,
        pool: Pool | None = None,
        run_after: datetime | None = None,
        project: uuid.UUID | None = None,
    ) -> uuid.UUID:
        """Enqueue work, idempotently.

        Does NOT commit. The caller commits, together with whatever ledger and scene-state
        changes belong to the same decision. That co-commit is the entire point of putting
        the queue in Postgres.

        `project` fills in `jobs.project_id`. The column existed from the start and nothing
        ever wrote to it, so every job in the database had a null there and no query could ask
        "what work belongs to this project" — which is what deleting one needs in order to
        find the credit reservations it is still holding.
        """
        from lumina.domain.jobs import STAGE_POOL

        key = idempotency_key(stage, payload)
        target_pool = pool or STAGE_POOL[stage]

        row = await self._session.execute(
            text(
                """
                INSERT INTO jobs (id, stage, pool, payload, idempotency_key, status,
                                  run_after, attempts, created_at, project_id)
                VALUES (gen_random_uuid(), :stage, :pool, CAST(:payload AS jsonb), :key,
                        'queued', COALESCE(:run_after, now()), 0, now(), :project)
                ON CONFLICT (idempotency_key) DO UPDATE
                    SET idempotency_key = EXCLUDED.idempotency_key  -- no-op, forces RETURNING
                RETURNING id
                """
            ),
            {
                "stage": stage.value,
                "pool": target_pool.value,
                "payload": _json(payload),
                "key": key,
                "run_after": run_after,
                "project": project,
            },
        )
        job_id: uuid.UUID = row.scalar_one()

        # Wake a worker after this transaction commits. NOTIFY is transactional in
        # Postgres — it fires on COMMIT and is discarded on ROLLBACK, which is exactly the
        # behaviour we want and the reason this is safe to call here.
        await self._session.execute(
            text("SELECT pg_notify(:chan, :payload)"),
            {"chan": channel(target_pool), "payload": str(job_id)},
        )
        return job_id

    async def notify(self, pool: Pool | str) -> None:
        """Wake a worker without enqueuing anything.

        `enqueue` announces its own job, but a requeued one was already in the table — its
        status changed and nothing fired. Without this the retry runs only when the idle poll
        next comes round, which reads as a button that did nothing.
        """
        target = pool if isinstance(pool, Pool) else Pool(pool)
        await self._session.execute(text("SELECT pg_notify(:chan, '')"), {"chan": channel(target)})

    async def claim(
        self, pool: Pool, *, limit: int = 1, lease: timedelta = DEFAULT_LEASE
    ) -> list[QueuedJob]:
        """Atomically claim up to `limit` runnable jobs for this pool."""
        rows = await self._session.execute(
            text(
                """
                WITH claimed AS (
                    SELECT id FROM jobs
                     WHERE pool = :pool
                       AND status = 'queued'
                       AND run_after <= now()
                     ORDER BY run_after
                     FOR UPDATE SKIP LOCKED
                     LIMIT :limit
                )
                UPDATE jobs j
                   SET status = 'running',
                       attempts = j.attempts + 1,
                       claimed_at = now(),
                       claimed_until = now() + :lease,
                       updated_at = now()
                  FROM claimed c
                 WHERE j.id = c.id
                RETURNING j.id, j.stage, j.pool, j.payload, j.attempts, j.idempotency_key
                """
            ),
            {"pool": pool.value, "limit": limit, "lease": lease},
        )
        return [
            QueuedJob(
                id=r.id,
                stage=Stage(r.stage),
                pool=Pool(r.pool),
                payload=r.payload,
                attempts=r.attempts,
                idempotency_key=r.idempotency_key,
            )
            for r in rows
        ]

    async def succeed(self, job_id: uuid.UUID, result: dict[str, Any] | None = None) -> None:
        await self._session.execute(
            text(
                """
                UPDATE jobs
                   SET status = 'succeeded', result = CAST(:result AS jsonb),
                       claimed_until = NULL, finished_at = now(), updated_at = now()
                 WHERE id = :id AND status = 'running'
                """
            ),
            {"id": job_id, "result": _json(result or {})},
        )

    async def fail(
        self, job_id: uuid.UUID, error: str, *, retry_in: timedelta | None = None
    ) -> None:
        """Fail a job, optionally scheduling a retry.

        Retry puts it back to 'queued' with a later `run_after` — exponential backoff is the
        caller's decision because it depends on whether the provider said 'try again' or
        'never'."""
        if retry_in is not None:
            await self._session.execute(
                text(
                    """
                    UPDATE jobs
                       SET status = 'queued', error = :error, run_after = now() + :delay,
                           claimed_until = NULL, updated_at = now()
                     WHERE id = :id
                    """
                ),
                {"id": job_id, "error": error, "delay": retry_in},
            )
        else:
            await self._session.execute(
                text(
                    """
                    UPDATE jobs
                       SET status = 'failed', error = :error, claimed_until = NULL,
                           finished_at = now(), updated_at = now()
                     WHERE id = :id
                    """
                ),
                {"id": job_id, "error": error},
            )

    async def reap_expired(self) -> int:
        """Return jobs whose lease expired to the queue. A worker died; the work did not."""
        result = await self._session.execute(
            text(
                """
                UPDATE jobs
                   SET status = 'queued', claimed_until = NULL, updated_at = now()
                 WHERE status = 'running' AND claimed_until < now()
                """
            )
        )
        return cast(CursorResult[Any], result).rowcount or 0


async def wait_for_notify(conn: AsyncConnection, pool: Pool, timeout_s: float) -> bool:
    """Block until a job is announced for `pool`, or `timeout_s` elapses.

    Returns True if woken by a notification. Callers must still poll on the timeout path:
    a notification can be missed across a reconnect, and `reap_expired` produces runnable
    jobs that nobody notifies about.
    """
    raw = await conn.get_raw_connection()
    driver = raw.driver_connection  # asyncpg.Connection
    if driver is None:  # pragma: no cover — only on a closed pool
        raise RuntimeError("no raw asyncpg connection available for LISTEN")
    import asyncio

    queue: asyncio.Queue[None] = asyncio.Queue()

    def _on_notify(*_: object) -> None:
        queue.put_nowait(None)

    chan = channel(pool)
    await driver.add_listener(chan, _on_notify)
    try:
        await asyncio.wait_for(queue.get(), timeout=timeout_s)
        return True
    except TimeoutError:
        return False
    finally:
        await driver.remove_listener(chan, _on_notify)


def _json(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def utcnow() -> datetime:
    return datetime.now(UTC)
