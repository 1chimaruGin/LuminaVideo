"""Per-scene progress over SSE.

Never one global spinner: each scene reports its own state, so regenerating scene four
visibly leaves the others alone.

The stream is a hint, not the source of truth. Jobs outlive the tab, connections drop, and a
notification can be missed across a reconnect — so the client refetches the project on
connect and treats every event as a nudge to update, never as the only copy of a state
change.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from lumina.api.deps import Session
from lumina.db.models import Scene

router = APIRouter(prefix="/events", tags=["events"])

#: How often to re-read scene state. Redis pub/sub replaces this once the workers publish;
#: polling one indexed table is cheap enough to be the honest first version.
POLL_S = 1.0


@router.get("/plan/{plan_id}")
async def plan_events(plan_id: uuid.UUID, session: Session) -> EventSourceResponse:
    async def stream() -> AsyncIterator[dict[str, str]]:
        seen: dict[str, str] = {}
        while True:
            rows = await session.scalars(
                select(Scene).where(Scene.plan_id == plan_id).order_by(Scene.index)
            )
            for scene in rows:
                key = str(scene.id)
                state = f"{scene.state}:{scene.preview_asset_id}:{scene.final_asset_id}"
                if seen.get(key) != state:
                    seen[key] = state
                    yield {
                        "event": "scene",
                        "data": json.dumps(
                            {
                                "id": key,
                                "index": scene.index,
                                "state": scene.state,
                                "error": scene.error,
                                "preview_asset_id": str(scene.preview_asset_id or "") or None,
                                "final_asset_id": str(scene.final_asset_id or "") or None,
                            }
                        ),
                    }
            await session.commit()  # release the snapshot so the next read sees worker writes
            await asyncio.sleep(POLL_S)

    return EventSourceResponse(stream())
