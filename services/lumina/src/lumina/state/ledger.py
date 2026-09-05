"""Credit ledger.

Invariant 4: credits are reserved before work starts, never charged after. The balance is
folded from entries and never stored, so there is no counter to drift.

Every write here happens inside the caller's transaction. Reserving credits, transitioning a
scene and enqueuing the job must commit together or not at all — that co-commit is the entire
reason the queue lives in Postgres (see TECHSTACK.md, deviation 1).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lumina.db.models import LedgerEntry
from lumina.domain.ledger import EntryKind, InsufficientCreditError
from lumina.domain.money import Cents


class Ledger:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def balance(self, user_id: uuid.UUID) -> tuple[Cents, Cents, Cents]:
        """(available, reserved, committed), folded from the entries."""
        rows = (
            await self._session.scalars(select(LedgerEntry).where(LedgerEntry.user_id == user_id))
        ).all()

        reserves = {r.id: r.amount for r in rows if r.kind == EntryKind.RESERVE.value}
        granted = reserved = committed = 0
        for e in rows:
            if e.kind == EntryKind.GRANT.value:
                granted += e.amount
            elif e.kind == EntryKind.RESERVE.value:
                reserved += e.amount
            elif e.kind in (EntryKind.COMMIT.value, EntryKind.REFUND.value):
                # A discharge releases what was HELD, and books what was SPENT. Those differ
                # whenever the estimate missed, which is most of the time.
                assert e.reserve_entry_id is not None
                reserved -= reserves.get(e.reserve_entry_id, 0)
                if e.kind == EntryKind.COMMIT.value:
                    committed += e.amount

        return Cents(granted - reserved - committed), Cents(reserved), Cents(committed)

    async def grant(self, user_id: uuid.UUID, amount: Cents, reason: str = "grant") -> LedgerEntry:
        return await self._add(user_id, EntryKind.GRANT, amount, reason=reason)

    async def reserve(
        self, user_id: uuid.UUID, amount: Cents, *, job_id: uuid.UUID | None = None
    ) -> LedgerEntry:
        """Hold credits against approved work.

        At plan approval, not at submit — otherwise a user queues a hundred scenes against a
        balance that covers ten.
        """
        available, _, _ = await self.balance(user_id)
        if amount > available:
            raise InsufficientCreditError(amount, available)
        return await self._add(user_id, EntryKind.RESERVE, amount, job_id=job_id)

    async def commit(self, reserve: LedgerEntry, actual: Cents) -> LedgerEntry:
        """Work finished. Releases the hold and books what it really cost."""
        return await self._add(
            reserve.user_id,
            EntryKind.COMMIT,
            actual,
            job_id=reserve.job_id,
            reserve_entry_id=reserve.id,
        )

    async def refund(self, reserve: LedgerEntry, reason: str) -> LedgerEntry:
        """Work failed, was rejected, or was cancelled before submit. Books nothing.

        No refund for a successful generation the user simply dislikes — that distinction is
        the caller's to make, not this method's.
        """
        return await self._add(
            reserve.user_id,
            EntryKind.REFUND,
            Cents(reserve.amount),
            job_id=reserve.job_id,
            reserve_entry_id=reserve.id,
            reason=reason,
        )

    async def _add(
        self,
        user_id: uuid.UUID,
        kind: EntryKind,
        amount: Cents,
        *,
        job_id: uuid.UUID | None = None,
        reserve_entry_id: uuid.UUID | None = None,
        reason: str | None = None,
    ) -> LedgerEntry:
        entry = LedgerEntry(
            user_id=user_id,
            kind=kind.value,
            amount=int(amount),
            job_id=job_id,
            reserve_entry_id=reserve_entry_id,
            reason=reason,
        )
        self._session.add(entry)
        await self._session.flush()
        return entry
