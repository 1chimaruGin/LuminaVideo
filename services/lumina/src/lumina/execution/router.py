"""Model router.

Routing policy, in the order `DEVELOPMENT.md` sets out:

    capability filter -> tier filter -> cost sort -> health check -> submit

with a fallback chain and a re-estimate when it falls back, because providers go down
regularly and automatic fallback is not optional.

Invariant 2 lives here: this file knows capabilities, cost and health. It never knows a
vendor's request shape, and nothing above it knows vendor names at all.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import structlog

from lumina.domain.money import Cents
from lumina.execution.providers import GenJob, Handle, Provider, ProviderError

log = structlog.get_logger(__name__)

#: Consecutive failures before a provider is dropped from routing.
TRIP_AFTER = 3
#: How long a tripped provider stays out.
COOLDOWN_S = 60.0


@dataclass
class Breaker:
    """One circuit breaker per provider, fed by submit outcomes."""

    failures: int = 0
    opened_at: float = 0.0

    @property
    def healthy(self) -> bool:
        if self.failures < TRIP_AFTER:
            return True
        return time.monotonic() - self.opened_at > COOLDOWN_S

    def ok(self) -> None:
        self.failures = 0
        self.opened_at = 0.0

    def fail(self) -> None:
        self.failures += 1
        if self.failures >= TRIP_AFTER:
            self.opened_at = time.monotonic()


class AllProvidersFailedError(Exception):
    def __init__(self, job: GenJob, tried: list[str]) -> None:
        super().__init__(f"every provider failed for {job.modality.value}: {', '.join(tried)}")
        self.tried = tried


@dataclass
class Router:
    providers: list[Provider]
    breakers: dict[str, Breaker] = field(default_factory=dict)

    def _breaker(self, name: str) -> Breaker:
        return self.breakers.setdefault(name, Breaker())

    def candidates(self, job: GenJob) -> list[tuple[Provider, Cents]]:
        """Capable, healthy providers, cheapest first."""
        out = [
            (p, p.estimate(job))
            for p in self.providers
            if p.capabilities.serves(job) and self._breaker(p.name).healthy
        ]
        return sorted(out, key=lambda pair: pair[1])

    async def submit(self, job: GenJob) -> tuple[Handle, Cents]:
        """Submit to the cheapest capable provider, falling through on failure.

        Returns the handle and the estimate *of the provider that accepted it* — not of the
        first choice. Reserving against one provider's price and paying another's is how the
        ledger drifts.
        """
        tried: list[str] = []
        for provider, estimate in self.candidates(job):
            tried.append(provider.name)
            try:
                handle = await provider.submit(job)
            except ProviderError as exc:
                self._breaker(provider.name).fail()
                log.warning("router.fallback", provider=provider.name, error=str(exc))
                if not exc.retryable:
                    raise
                continue
            self._breaker(provider.name).ok()
            return handle, estimate

        raise AllProvidersFailedError(job, tried)
