"""Double-entry credit ledger.

Not a balance integer. The balance is derived, always:

    plan approved   -> reserve(estimate)
    asset returned  -> commit(actual)
    failure/reject  -> refund(reserved)

Reserve happens at plan approval, not at submit — otherwise a user queues a hundred scenes
against a balance that covers ten (invariant 4).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from lumina.domain.ids import JobId, LedgerEntryId, UserId
from lumina.domain.money import Cents


class EntryKind(StrEnum):
    #: Credits bought or granted. Increases available balance.
    GRANT = "grant"
    #: Held against approved work. Decreases available, increases reserved.
    RESERVE = "reserve"
    #: Work finished. Releases the hold and books the real cost.
    COMMIT = "commit"
    #: Work failed or was cancelled before submit. Releases the hold, books nothing.
    REFUND = "refund"


class LedgerEntry(BaseModel):
    id: LedgerEntryId
    user_id: UserId
    kind: EntryKind
    amount: Cents
    #: RESERVE/COMMIT/REFUND always reference the job they account for. GRANT never does.
    job_id: JobId | None = None
    #: COMMIT and REFUND reference the RESERVE they discharge.
    reserve_entry_id: LedgerEntryId | None = None


class Balance(BaseModel):
    """Derived from entries. Never stored as a mutable column."""

    granted: Cents
    reserved: Cents
    committed: Cents

    @property
    def available(self) -> Cents:
        return Cents(self.granted - self.reserved - self.committed)


def compute_balance(entries: list[LedgerEntry]) -> Balance:
    """Fold entries into a balance.

    A COMMIT releases the amount that was *reserved*, and books the amount that was
    *actually* spent. Those two numbers are different — the estimator drifts — so a commit
    must discharge its reserve by looking it up, not by subtracting its own amount. Getting
    this wrong leaks or double-counts held credit on every job whose estimate missed.
    """
    reserves: dict[LedgerEntryId, Cents] = {
        e.id: e.amount for e in entries if e.kind is EntryKind.RESERVE
    }
    discharged: set[LedgerEntryId] = set()

    granted = reserved = committed = 0
    for e in entries:
        match e.kind:
            case EntryKind.GRANT:
                granted += e.amount
            case EntryKind.RESERVE:
                reserved += e.amount
            case EntryKind.COMMIT | EntryKind.REFUND:
                if e.reserve_entry_id is None:
                    raise ValueError(f"{e.kind.value} entry {e.id} has no reserve_entry_id")
                if e.reserve_entry_id not in reserves:
                    raise ValueError(f"{e.kind.value} entry {e.id} references an unknown reserve")
                if e.reserve_entry_id in discharged:
                    raise ValueError(f"reserve {e.reserve_entry_id} discharged twice")
                discharged.add(e.reserve_entry_id)
                reserved -= reserves[e.reserve_entry_id]
                if e.kind is EntryKind.COMMIT:
                    committed += e.amount

    return Balance(granted=Cents(granted), reserved=Cents(reserved), committed=Cents(committed))


#: Refund on: provider error, timeout, policy rejection, user cancel before submit.
#: No refund on: successful generation the user simply dislikes.
REFUNDABLE_REASONS = frozenset(
    {"provider_error", "timeout", "policy_rejection", "cancelled_before_submit"}
)


class InsufficientCreditError(Exception):
    def __init__(self, needed: Cents, available: Cents) -> None:
        super().__init__(f"needs {needed} cents, {available} available")
        self.needed = needed
        self.available = available
