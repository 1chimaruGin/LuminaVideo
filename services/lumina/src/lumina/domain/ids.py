"""Typed identifiers.

Distinct NewTypes so mypy rejects passing a ProjectId where a SceneId belongs — a class of
bug that is otherwise invisible until it corrupts someone's storyboard.
"""

from __future__ import annotations

import uuid
from typing import NewType

UserId = NewType("UserId", uuid.UUID)
ChannelId = NewType("ChannelId", uuid.UUID)
ProjectId = NewType("ProjectId", uuid.UUID)
PlanId = NewType("PlanId", uuid.UUID)
SceneId = NewType("SceneId", uuid.UUID)
AssetId = NewType("AssetId", uuid.UUID)
JobId = NewType("JobId", uuid.UUID)
RenderId = NewType("RenderId", uuid.UUID)
LedgerEntryId = NewType("LedgerEntryId", uuid.UUID)


def new_id() -> uuid.UUID:
    return uuid.uuid4()
