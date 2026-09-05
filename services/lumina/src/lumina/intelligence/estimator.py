"""Cost estimation.

Deliberately separate from the router, as `DEVELOPMENT.md` requires: this answers "what will
this cost" without committing to a provider, so the Recipe screen can price a plan before
anything runs and before capacity or health have been consulted.

It asks every capable provider what it would charge and takes the cheapest, which is the same
order the router will end up in — but it never submits, never reserves and never mutates.
"""

from __future__ import annotations

from collections.abc import Iterable

from lumina.domain.money import Cents
from lumina.domain.plan import Aspect, Plan, Tier
from lumina.execution.providers import GenJob, Modality, Provider

#: What each stage needs from a provider, per scene.
SCENE_MODALITY: dict[Tier, Modality] = {
    Tier.STOCK: Modality.TEXT_TO_IMAGE,
    Tier.IMAGE_MOTION: Modality.TEXT_TO_IMAGE,
    Tier.BUDGET_VIDEO: Modality.TEXT_TO_VIDEO,
    Tier.MID_VIDEO: Modality.TEXT_TO_VIDEO,
    Tier.PREMIUM_VIDEO: Modality.TEXT_TO_VIDEO,
}


class NoCapableProviderError(Exception):
    def __init__(self, job: GenJob) -> None:
        super().__init__(f"no provider serves {job.modality.value} at tier {job.tier.value}")
        self.job = job


def cheapest(providers: Iterable[Provider], job: GenJob) -> tuple[Provider, Cents]:
    """The capability filter and cost sort from the routing policy, without submitting."""
    candidates = [(p, p.estimate(job)) for p in providers if p.capabilities.serves(job)]
    if not candidates:
        raise NoCapableProviderError(job)
    return min(candidates, key=lambda pair: pair[1])


def estimate_scene(providers: Iterable[Provider], plan: Plan, scene_index: int) -> Cents:
    scene = plan.scenes[scene_index]
    job = GenJob(
        scene_id=scene.id,
        modality=SCENE_MODALITY[scene.tier],
        tier=scene.tier,
        prompt=scene.prompt,
        language=plan.language,
        aspect=plan.aspects[0] if plan.aspects else Aspect.VERTICAL,
        duration_ms=scene.duration_ms,
    )
    return cheapest(providers, job)[1]


def estimate_plan(providers: Iterable[Provider], plan: Plan) -> Cents:
    """What the whole plan costs at its current tiers.

    Voice and captions are per-scene too, but they are charged at the preview tier for every
    scene regardless of whether that scene is later upgraded — an upgraded scene reuses its
    narration rather than re-reading it.
    """
    providers = list(providers)
    total: int = sum(estimate_scene(providers, plan, i) for i in range(len(plan.scenes)))

    voice = GenJob(
        scene_id=plan.scenes[0].id,
        modality=Modality.TEXT_TO_SPEECH,
        tier=Tier.IMAGE_MOTION,
        prompt=" ".join(s.script_line for s in plan.scenes),
        language=plan.language,
        duration_ms=min(60_000, plan.duration_ms),
    )
    total += cheapest(providers, voice)[1]
    return Cents(total)
