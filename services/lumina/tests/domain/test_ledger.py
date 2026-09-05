"""Ledger invariants. Get these wrong and it costs real money."""

from __future__ import annotations

import uuid

import pytest
from hypothesis import given
from hypothesis import strategies as st

from lumina.domain.ids import LedgerEntryId, UserId
from lumina.domain.ledger import EntryKind, LedgerEntry, compute_balance
from lumina.domain.money import Cents

USER = UserId(uuid.uuid4())


def entry(kind: EntryKind, amount: int, reserve: LedgerEntryId | None = None) -> LedgerEntry:
    return LedgerEntry(
        id=LedgerEntryId(uuid.uuid4()),
        user_id=USER,
        kind=kind,
        amount=Cents(amount),
        reserve_entry_id=reserve,
    )


def test_reserve_holds_against_available() -> None:
    grant = entry(EntryKind.GRANT, 1000)
    reserve = entry(EntryKind.RESERVE, 300)
    bal = compute_balance([grant, reserve])
    assert bal.reserved == 300
    assert bal.available == 700


def test_commit_discharges_the_reserved_amount_not_the_actual() -> None:
    """The whole point of double entry. A commit releases what was HELD (the estimate) and
    books what was SPENT (the actual). Those differ whenever the estimator drifts."""
    grant = entry(EntryKind.GRANT, 1000)
    reserve = entry(EntryKind.RESERVE, 300)  # estimated 300
    commit = entry(EntryKind.COMMIT, 250, reserve=reserve.id)  # actually cost 250

    bal = compute_balance([grant, reserve, commit])

    assert bal.reserved == 0, "the full 300 hold must be released, not just the 250 spent"
    assert bal.committed == 250
    assert bal.available == 750, "the 50 of unspent estimate returns to the user"


def test_commit_over_estimate_still_zeroes_the_hold() -> None:
    entries = [entry(EntryKind.GRANT, 1000), (r := entry(EntryKind.RESERVE, 300))]
    entries.append(entry(EntryKind.COMMIT, 380, reserve=r.id))  # provider charged more
    bal = compute_balance(entries)
    assert bal.reserved == 0
    assert bal.committed == 380
    assert bal.available == 620


def test_refund_releases_the_hold_and_books_nothing() -> None:
    entries = [entry(EntryKind.GRANT, 1000), (r := entry(EntryKind.RESERVE, 300))]
    entries.append(entry(EntryKind.REFUND, 300, reserve=r.id))
    bal = compute_balance(entries)
    assert bal.reserved == 0
    assert bal.committed == 0
    assert bal.available == 1000, "a failed job must cost the user nothing"


def test_a_reserve_cannot_be_discharged_twice() -> None:
    entries = [entry(EntryKind.GRANT, 1000), (r := entry(EntryKind.RESERVE, 300))]
    entries.append(entry(EntryKind.COMMIT, 300, reserve=r.id))
    entries.append(entry(EntryKind.REFUND, 300, reserve=r.id))
    with pytest.raises(ValueError, match="discharged twice"):
        compute_balance(entries)


def test_discharge_must_reference_a_reserve() -> None:
    with pytest.raises(ValueError, match="no reserve_entry_id"):
        compute_balance([entry(EntryKind.GRANT, 100), entry(EntryKind.COMMIT, 50)])


@given(
    grant=st.integers(min_value=0, max_value=1_000_000),
    holds=st.lists(
        st.tuples(
            st.integers(min_value=0, max_value=10_000),  # estimate
            st.integers(min_value=0, max_value=10_000),  # actual
            st.booleans(),  # committed vs refunded
        ),
        max_size=20,
    ),
)
def test_every_settled_reserve_leaves_zero_held(grant: int, holds: list) -> None:
    """Property: once every reserve is discharged, `reserved` is exactly zero — regardless
    of how far the estimates drifted. A non-zero remainder is leaked credit."""
    entries = [entry(EntryKind.GRANT, grant)]
    spent = 0
    for estimate, actual, did_commit in holds:
        r = entry(EntryKind.RESERVE, estimate)
        entries.append(r)
        if did_commit:
            entries.append(entry(EntryKind.COMMIT, actual, reserve=r.id))
            spent += actual
        else:
            entries.append(entry(EntryKind.REFUND, estimate, reserve=r.id))

    bal = compute_balance(entries)
    assert bal.reserved == 0
    assert bal.committed == spent
    assert bal.available == grant - spent
