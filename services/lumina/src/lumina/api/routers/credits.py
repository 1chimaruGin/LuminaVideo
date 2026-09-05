"""Balance and quotes.

Reads the ledger; never mutates a balance. Every credit movement happens inside the stage
that caused it, so that the reservation and the work commit together.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from lumina.api.deps import CurrentUser, LedgerDep, Session
from lumina.api.schemas import BalanceOut, LedgerEntryOut, QuoteOut
from lumina.db.models import LedgerEntry, Project
from lumina.execution.providers.fake import FakeProvider
from lumina.intelligence.pricing import price_plan

router = APIRouter(prefix="/credits", tags=["credits"])


@router.get("", response_model=BalanceOut)
async def balance(user: CurrentUser, ledger: LedgerDep) -> BalanceOut:
    available, reserved, committed = await ledger.balance(user.id)
    return BalanceOut(available=int(available), reserved=int(reserved), committed=int(committed))


@router.get("/history", response_model=list[LedgerEntryOut])
async def history(session: Session, user: CurrentUser, limit: int = 50) -> list[LedgerEntry]:
    """Every credit movement, newest first.

    Worth showing in full rather than as a net figure per day: a reserve that was refunded
    because a provider failed is a different story from one that was committed, and a user
    looking at their balance after a bad run needs to be able to see which happened.
    """
    rows = await session.scalars(
        select(LedgerEntry)
        .where(LedgerEntry.user_id == user.id)
        .order_by(LedgerEntry.created_at.desc())
        .limit(min(limit, 200))
    )
    return list(rows)


@router.get("/quote/{project_id}", response_model=QuoteOut)
async def quote(project_id: uuid.UUID, session: Session) -> QuoteOut:
    """What this plan costs at its current tiers, before anything runs.

    The same function the draft reserves against. If these were computed separately they would
    eventually disagree, and the way a user finds that out is being quoted 36 and charged 41 —
    the single worst thing a product that sells credits can do.

    Uses the estimator rather than the router: it answers "what would this cost" without
    consulting provider health or capacity and without committing to anyone. Pricing a plan
    must never have a side effect.
    """
    project = await session.get(Project, project_id)
    if project is None or project.current_plan_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no plan yet")

    priced = await price_plan(
        session,
        [FakeProvider()],
        project.current_plan_id,
        project.language,
        project.recipe,
    )
    return QuoteOut(credits=int(priced.total), per_scene=priced.per_scene)
