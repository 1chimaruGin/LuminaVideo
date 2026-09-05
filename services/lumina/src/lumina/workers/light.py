"""`light` pool: planning, LLM calls, provider polling, ingest.

Small, always on. This pool is IO-bound — it waits on other people's APIs — so concurrency is
high and hardware is cheap. Never put ffmpeg or Chromium work here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from lumina.config import get_settings
from lumina.db.base import sessionmaker_for
from lumina.domain.jobs import Pool, Stage
from lumina.execution import stages
from lumina.execution.providers.fake import FakeProvider
from lumina.execution.router import Router
from lumina.state.assets import AssetStore
from lumina.state.ledger import Ledger
from lumina.workers.runner import Worker, serve


def providers() -> list[Any]:
    """Only what PROVIDERS_ENABLED names.

    The fake provider is the default so that running the app, or the tests, never spends
    money. Real adapters are opted into per environment.
    """
    enabled = get_settings().providers
    out: list[Any] = []
    if "fake" in enabled:
        out.append(FakeProvider())
    # fal / replicate land here in build-order step 4, once the ledger is proven.
    return out


StageFn = Callable[[stages.Ctx, dict[str, Any]], Awaitable[dict[str, Any]]]


def stage_handler(fn: StageFn) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Give a stage its context, and hand it a session per job.

    A session per job rather than per worker: one poisoned job must not take the others'
    transaction down with it.
    """

    async def run(payload: dict[str, Any]) -> dict[str, Any]:
        async with sessionmaker_for()() as session:
            ctx = stages.Ctx(
                session=session,
                router=Router(providers=providers()),
                assets=AssetStore(session),
                ledger=Ledger(session),
            )
            result = await fn(ctx, payload)
            await session.commit()
            return result

    return run


async def main() -> None:
    handlers = {
        # Everything IO-bound: planning, transcription, ranking, ingest and provider polling.
        Stage.INGEST: stage_handler(stages.ingest),
        Stage.TRANSCRIBE: stage_handler(stages.transcribe),
        Stage.ANALYZE: stage_handler(stages.analyze),
        Stage.PLAN: stage_handler(stages.plan),
        Stage.GENERATE: stage_handler(stages.generate),
        Stage.VOICE: stage_handler(stages.voice),
    }
    await serve(Worker(Pool.LIGHT, handlers, concurrency=32))


if __name__ == "__main__":
    asyncio.run(main())
