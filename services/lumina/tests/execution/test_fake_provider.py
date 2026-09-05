from __future__ import annotations

import uuid

import pytest

from lumina.domain.ids import SceneId
from lumina.domain.plan import Aspect, Tier
from lumina.execution.providers import GenJob, Modality, Provider
from lumina.execution.providers.fake import FakeProvider


def job(**kw: object) -> GenJob:
    base = dict(
        scene_id=SceneId(uuid.uuid4()),
        modality=Modality.TEXT_TO_IMAGE,
        tier=Tier.IMAGE_MOTION,
        prompt="a lighthouse at dusk",
        language="en",
    )
    return GenJob(**{**base, **kw})  # type: ignore[arg-type]


def test_fake_provider_satisfies_the_protocol() -> None:
    assert isinstance(FakeProvider(), Provider)


def test_estimate_does_no_io_and_is_deterministic() -> None:
    p = FakeProvider()
    j = job(duration_ms=5_000)
    assert p.estimate(j) == p.estimate(j)


async def test_submit_is_content_addressed() -> None:
    """Same prompt, same refs, same seed -> same handle. This is what makes regeneration a
    cache hit rather than a second charge."""
    p = FakeProvider()
    scene_id = SceneId(uuid.uuid4())
    a = await p.submit(job(scene_id=scene_id, seed=7))
    b = await p.submit(job(scene_id=scene_id, seed=7))
    assert a.provider_ref == b.provider_ref


async def test_different_seeds_produce_different_handles() -> None:
    p = FakeProvider()
    scene_id = SceneId(uuid.uuid4())
    a = await p.submit(job(scene_id=scene_id, seed=1))
    b = await p.submit(job(scene_id=scene_id, seed=2))
    assert a.provider_ref != b.provider_ref


async def test_policy_rejection_is_distinct_from_failure() -> None:
    """REJECTED must not be retried but must be refunded; FAILED may be retried. Collapsing
    them either burns money on a doomed retry or refuses to refund a rejection."""
    p = FakeProvider()
    handle = await p.submit(job(prompt="__reject__ something"))
    result = await p.poll(handle)
    assert result.status == "rejected"


async def test_capabilities_filter_rejects_out_of_range_work() -> None:
    p = FakeProvider()
    assert p.capabilities.serves(job(duration_ms=10_000))
    assert not p.capabilities.serves(job(duration_ms=90_000))


@pytest.mark.parametrize("aspect", list(Aspect))
async def test_all_aspects_round_trip(aspect: Aspect) -> None:
    p = FakeProvider()
    result = await p.poll(await p.submit(job(aspect=aspect)))
    assert result.status == "succeeded"
    assert result.actual_cost is not None
