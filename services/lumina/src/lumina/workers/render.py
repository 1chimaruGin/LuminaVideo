"""`cpu-render` pool: ffmpeg composition and Chromium caption rasterization.

Cheap CPU, burst. Low concurrency — each job saturates cores. This is the pool whose hourly
cost decides margin, which is why it runs on dedicated hardware rather than a hyperscaler.
"""

from __future__ import annotations

import asyncio

from lumina.domain.jobs import Pool, Stage
from lumina.execution import stages
from lumina.workers.light import stage_handler
from lumina.workers.runner import Worker, serve


async def main() -> None:
    handlers = {
        # Both are CPU-bound and both are why this pool exists: captions run Chromium,
        # compose runs ffmpeg. Neither belongs anywhere near the light pool's event loop.
        Stage.CAPTIONS: stage_handler(stages.captions),
        Stage.COMPOSE: stage_handler(stages.compose),
    }
    await serve(Worker(Pool.CPU_RENDER, handlers, concurrency=2))


if __name__ == "__main__":
    asyncio.run(main())
