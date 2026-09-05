"""Money is integer cents. Never float."""

from __future__ import annotations

from typing import NewType

Cents = NewType("Cents", int)

ZERO = Cents(0)


def cents(value: int) -> Cents:
    if value < 0:
        raise ValueError(f"cents must be non-negative, got {value}")
    return Cents(value)


def add(*values: Cents) -> Cents:
    return Cents(sum(values))


def format_cents(value: Cents) -> str:
    return f"${value / 100:,.2f}"
