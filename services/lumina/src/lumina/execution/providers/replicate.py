"""Replicate adapter. Implements `Provider`; nothing above this file knows the vendor exists.

Not implemented yet. Build order puts the ledger and estimator *before* any paid provider:
never ship provider calls without reservation in place.

Replicate supports webhooks — set `supports_webhook=True` in Capabilities once wired.
"""

from __future__ import annotations

from lumina.domain.money import Cents
from lumina.execution.providers.base import Capabilities, GenJob, Handle, ProviderResult


class ReplicateProvider:
    name = "replicate"
    capabilities: Capabilities

    def estimate(self, job: GenJob) -> Cents:
        raise NotImplementedError("replicate adapter not wired yet — build order step 4")

    async def submit(self, job: GenJob) -> Handle:
        raise NotImplementedError

    async def poll(self, handle: Handle) -> ProviderResult:
        raise NotImplementedError

    async def cancel(self, handle: Handle) -> None:
        raise NotImplementedError
