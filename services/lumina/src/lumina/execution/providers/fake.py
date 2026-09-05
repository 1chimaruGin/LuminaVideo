"""Deterministic, instant, free. The default provider in development and tests.

Build order step 1 is "prove a job runs end to end and produces a file" with one fake
provider. Nobody should burn credits running the test suite, so this stays wired as the
default until PROVIDERS_ENABLED says otherwise.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field

from lumina.domain.assets import AssetKind
from lumina.domain.money import Cents
from lumina.domain.plan import Aspect, Tier
from lumina.execution.providers.base import (
    Capabilities,
    GenJob,
    Handle,
    Modality,
    ProviderResult,
    ProviderStatus,
)

_KIND_FOR: dict[Modality, AssetKind] = {
    Modality.TEXT_TO_IMAGE: AssetKind.IMAGE,
    Modality.IMAGE_TO_VIDEO: AssetKind.CLIP,
    Modality.TEXT_TO_VIDEO: AssetKind.CLIP,
    Modality.TEXT_TO_SPEECH: AssetKind.AUDIO,
    Modality.VOICE_CLONE: AssetKind.AUDIO,
    Modality.TRANSCRIBE: AssetKind.TEXT,
}


@dataclass
class FakeProvider:
    """Implements `Provider`. Configurable failure modes so tests can exercise the router's
    fallback chain, the circuit breaker, and every refundable ledger path."""

    name: str = "fake"
    latency_s: float = 0.0
    #: Fail every Nth submission (0 disables). Exercises fallback + refund.
    fail_every: int = 0
    #: Reject on prompts containing this marker. Exercises the policy-rejection path.
    reject_marker: str = "__reject__"
    cost_per_second_cents: int = 1

    _submissions: int = field(default=0, init=False)
    _handles: dict[str, GenJob] = field(default_factory=dict, init=False)

    capabilities: Capabilities = field(
        default_factory=lambda: Capabilities(
            modalities=frozenset(Modality),
            tiers=frozenset(Tier),
            aspects=frozenset(Aspect),
            languages=frozenset(),  # language-agnostic
            max_duration_ms=60_000,
            max_refs=8,
            supports_webhook=False,
        )
    )

    def estimate(self, job: GenJob) -> Cents:
        return Cents(max(1, self.cost_per_second_cents * job.duration_ms // 1000))

    async def submit(self, job: GenJob) -> Handle:
        if self.latency_s:
            await asyncio.sleep(self.latency_s)
        self._submissions += 1
        ref = hashlib.sha256(f"{job.scene_id}:{job.prompt}:{job.seed}".encode()).hexdigest()[:24]
        self._handles[ref] = job
        return Handle(provider=self.name, provider_ref=ref)

    async def poll(self, handle: Handle) -> ProviderResult:
        job = self._handles.get(handle.provider_ref)
        if job is None:
            return ProviderResult(status=ProviderStatus.FAILED, error="unknown handle")

        if self.reject_marker in job.prompt:
            return ProviderResult(status=ProviderStatus.REJECTED, error="content policy")

        if self.fail_every and self._submissions % self.fail_every == 0:
            return ProviderResult(status=ProviderStatus.FAILED, error="synthetic failure")

        return ProviderResult(
            status=ProviderStatus.SUCCEEDED,
            # Content-addressed by the same inputs, so re-running is a cache hit.
            url=f"fake://{handle.provider_ref}",
            kind=_KIND_FOR[job.modality],
            actual_cost=self.estimate(job),
        )

    async def cancel(self, handle: Handle) -> None:
        self._handles.pop(handle.provider_ref, None)
