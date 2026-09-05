"""The provider adapter boundary.

INVARIANT 2: no provider branching outside this package. The router knows capabilities and
cost; it never knows a vendor name or a vendor's request shape. If you find yourself writing
`if provider == "fal"` anywhere above this line, the abstraction has leaked.

Everything that differs between vendors is absorbed here: auth, async semantics (poll vs
webhook), input schema, supported aspect ratios and durations, content policy, regional
availability, failure modes, and latency (20s to 10min).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from lumina.domain.assets import AssetKind
from lumina.domain.ids import SceneId
from lumina.domain.money import Cents
from lumina.domain.plan import Aspect, Tier


class Modality(StrEnum):
    TEXT_TO_IMAGE = "text_to_image"
    IMAGE_TO_VIDEO = "image_to_video"
    TEXT_TO_VIDEO = "text_to_video"
    TEXT_TO_SPEECH = "text_to_speech"
    VOICE_CLONE = "voice_clone"
    TRANSCRIBE = "transcribe"


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What the router filters on. Purely declarative — no vendor semantics."""

    modalities: frozenset[Modality]
    tiers: frozenset[Tier]
    aspects: frozenset[Aspect]
    languages: frozenset[str]  # BCP-47; empty means language-agnostic
    max_duration_ms: int
    max_refs: int
    supports_webhook: bool = False

    def serves(self, job: GenJob) -> bool:
        return (
            job.modality in self.modalities
            and job.tier in self.tiers
            and job.aspect in self.aspects
            and job.duration_ms <= self.max_duration_ms
            and len(job.ref_urls) <= self.max_refs
            and (not self.languages or job.language in self.languages)
        )


@dataclass(frozen=True, slots=True)
class GenJob:
    """One unit of generation, in vendor-neutral terms."""

    scene_id: SceneId
    modality: Modality
    tier: Tier
    prompt: str
    language: str
    aspect: Aspect = Aspect.VERTICAL
    duration_ms: int = 5_000
    ref_urls: tuple[str, ...] = ()
    #: Same prompt + same refs + same seed = same output. Drives the dedup cache.
    seed: int | None = None
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Handle:
    """An in-flight submission. `provider_ref` is whatever the vendor calls its job id."""

    provider: str
    provider_ref: str
    #: Set when the vendor will call us back instead of us polling.
    webhook_token: str | None = None


class ProviderStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"  # content policy — do not retry, do refund


@dataclass(frozen=True, slots=True)
class ProviderResult:
    status: ProviderStatus
    #: Populated only on SUCCEEDED.
    url: str | None = None
    kind: AssetKind | None = None
    #: What the vendor actually charged, when it tells us. Feeds ledger COMMIT.
    actual_cost: Cents | None = None
    error: str | None = None


class ProviderError(Exception):
    """Transport or auth failure. Retryable; distinct from a REJECTED result."""

    def __init__(self, provider: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.retryable = retryable


@runtime_checkable
class Provider(Protocol):
    name: str
    capabilities: Capabilities

    def estimate(self, job: GenJob) -> Cents:
        """What this job will cost here. Must not perform I/O — the estimator calls it
        for every candidate to price a plan before committing to any provider."""
        ...

    async def submit(self, job: GenJob) -> Handle: ...

    async def poll(self, handle: Handle) -> ProviderResult: ...

    async def cancel(self, handle: Handle) -> None: ...
