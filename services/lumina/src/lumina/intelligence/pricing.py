"""What a plan's scenes cost, in one place.

`/credits/quote` shows a number and `/projects/{id}/draft` reserves one. If those two are
computed by separate code they will eventually disagree, and the way a user finds out is
being quoted 36 and charged 41 — which is the single worst thing a product that sells credits
can do.

So both call this. It is the estimator's capability filter and cost sort, applied to the
scenes as they are stored, and it never has a side effect.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lumina.db.models import Scene
from lumina.domain.jobs import Stage, stages_for
from lumina.domain.money import Cents
from lumina.domain.plan import Tier
from lumina.execution.providers import GenJob, Provider
from lumina.intelligence.estimator import SCENE_MODALITY, cheapest


@dataclass(frozen=True, slots=True)
class Quote:
    """What a plan costs at its current tiers."""

    #: (scene id, cents), in scene order. Per scene rather than a total alone because the
    #: reservation is made per scene — a failure refunds one scene's hold, not the whole plan.
    lines: list[tuple[uuid.UUID, Cents]]

    @property
    def total(self) -> Cents:
        return Cents(sum(int(c) for _, c in self.lines))

    @property
    def per_scene(self) -> list[int]:
        return [int(c) for _, c in self.lines]


def price_scene(providers: list[Provider], scene: Scene, language: str) -> Cents:
    """What generating this one scene would cost, at the tier it is currently set to."""
    tier = Tier(scene.tier)
    job = GenJob(
        scene_id=scene.id,  # type: ignore[arg-type]
        modality=SCENE_MODALITY[tier],
        tier=tier,
        prompt=scene.prompt,
        language=language,
        duration_ms=scene.duration_ms,
    )
    return cheapest(providers, job)[1]


async def price_plan(
    session: AsyncSession,
    providers: list[Provider],
    plan_id: uuid.UUID,
    language: str,
    recipe: str = "explainer",
) -> Quote:
    """Price every scene in a plan, in order.

    A lane that does not generate costs nothing per scene. Subtitles and Clip keep the
    creator's own footage — no frame is drawn, no provider is called — so pricing their scenes
    as though one would be quotes ten credits for work that costs zero and never runs.

    That is not a rounding error in a product whose whole promise is "cost before every
    action": the Recipe screen would tell someone their subtitle job costs ten, and the ledger
    would then hold nothing, and the two would never agree.
    """
    scenes = list(
        await session.scalars(select(Scene).where(Scene.plan_id == plan_id).order_by(Scene.index))
    )
    if Stage.GENERATE not in stages_for(recipe):
        return Quote(lines=[(s.id, Cents(0)) for s in scenes])
    return Quote(lines=[(s.id, price_scene(providers, s, language)) for s in scenes])
