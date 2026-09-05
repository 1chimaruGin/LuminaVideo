"""Engine, session factory, declarative base."""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from lumina.config import get_settings


class Base(DeclarativeBase):
    pass


@lru_cache
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        # Workers hold a connection while polling providers; keep the pool small and let
        # asyncio concurrency do the work rather than growing connections per in-flight job.
        pool_size=10,
        max_overflow=10,
        echo=False,
    )


@lru_cache
def sessionmaker_for() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False, autoflush=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. One session per request, committed by the caller."""
    async with sessionmaker_for()() as session:
        yield session
