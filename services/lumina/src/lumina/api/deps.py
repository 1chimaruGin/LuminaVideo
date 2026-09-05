"""Request-scoped dependencies."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lumina.config import get_settings
from lumina.db.base import get_session
from lumina.db.models import Channel, User
from lumina.platform import auth
from lumina.state.assets import AssetStore
from lumina.state.ledger import Ledger

Session = Annotated[AsyncSession, Depends(get_session)]


async def current_user(
    session: Session,
    authorization: Annotated[str | None, Header()] = None,
    x_lumina_user: Annotated[str | None, Header()] = None,
) -> User:
    """The signed-in user.

    Two ways in, and only one of them ships.

    `Authorization: Bearer <token>` is the real path: the token is signed, scoped and expiring,
    and it is what `POST /auth/session` hands back.

    `X-Lumina-User: <uuid>` is a development convenience for curl and the test suite. It is
    refused outright in production, because possession of a user id would otherwise be
    possession of the account — and the id turns up in logs, tickets and error reports.
    """
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "expected a bearer token")
        user_id = auth.read(token)
        if user_id is None:
            # Expired, tampered with, or signed by a different secret — the client is told
            # only that it must sign in again. Which of those it was is not its business.
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session expired; sign in again")
        return await _load(session, user_id)

    if x_lumina_user is not None:
        if get_settings().is_production:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not signed in")
        try:
            return await _load(session, uuid.UUID(x_lumina_user))
        except ValueError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad user id") from None

    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not signed in")


async def _load(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if user is None:
        # A valid token for a deleted account. Same answer as no token at all.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not signed in")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def current_channel(session: Session, user: CurrentUser) -> Channel:
    channel = await session.scalar(select(Channel).where(Channel.user_id == user.id).limit(1))
    if channel is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no channel")
    return channel


CurrentChannel = Annotated[Channel, Depends(current_channel)]


async def ledger(session: Session) -> AsyncIterator[Ledger]:
    yield Ledger(session)


async def assets(session: Session) -> AsyncIterator[AssetStore]:
    yield AssetStore(session)


LedgerDep = Annotated[Ledger, Depends(ledger)]
AssetsDep = Annotated[AssetStore, Depends(assets)]
