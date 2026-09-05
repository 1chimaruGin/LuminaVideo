"""The identity kit.

Voice profile, logo, palette, fonts, caption style, intro and outro — one document, inherited
by every new project. `design-brief.md` calls series memory "automatic, not configured", and
this is the mechanism: a project reads defaults from here rather than asking the user again.

`identity` is a merged JSONB document rather than columns because its shape is still moving —
adding a brand font should not be a migration. The merge is shallow and deliberate: setting a
palette must not silently drop the caption style, which is exactly what a whole-document PUT
would do the first time two settings screens were open at once.
"""

from __future__ import annotations

from fastapi import APIRouter

from lumina.api.deps import CurrentChannel, Session
from lumina.api.schemas import ChannelEdit, ChannelOut
from lumina.db.models import Channel

router = APIRouter(prefix="/channel", tags=["channel"])


@router.get("", response_model=ChannelOut)
async def get_channel(channel: CurrentChannel) -> Channel:
    return channel


@router.patch("", response_model=ChannelOut)
async def edit_channel(body: ChannelEdit, session: Session, channel: CurrentChannel) -> Channel:
    """Update the kit. Absent fields are left alone; `identity` merges rather than replaces."""
    patch = body.model_dump(exclude_none=True)
    identity = patch.pop("identity", None)

    for field, value in patch.items():
        setattr(channel, field, value)

    if identity is not None:
        # Reassignment, not mutation. SQLAlchemy does not track in-place changes to a JSONB
        # dict, so `channel.identity.update(...)` updates the object in memory and writes
        # nothing to the database — a settings screen that appears to save and does not.
        channel.identity = {**channel.identity, **identity}

    await session.commit()
    return channel
