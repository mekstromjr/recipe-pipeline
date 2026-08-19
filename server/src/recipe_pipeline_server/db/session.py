"""Async engine/session wiring for the `recipes` DB.

One engine per process, built in the app lifespan; request handlers get an ``AsyncSession``
via the ``get_session`` dependency. ``pool_pre_ping`` because the shared Postgres restarts
independently of this service — a stale pooled connection must not 500 the first request
after a DB bounce.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def make_engine(
    db_url: str, *, connect_timeout: int = 5, pool_timeout: int = 5
) -> AsyncEngine:
    """Engine with every wait bounded.

    ``connect_timeout`` (psycopg) caps a single TCP+auth attempt; ``pool_timeout`` caps how
    long a request waits for a free pooled connection. Both default to hard ceilings so a
    slow or blackholed database surfaces as a prompt error rather than a hung request.
    """
    return create_async_engine(
        db_url,
        pool_pre_ping=True,
        pool_timeout=pool_timeout,
        connect_args={"connect_timeout": connect_timeout},
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: committed ORM objects stay readable while serializing the
    # response (avoids the MissingGreenlet trap on server-default columns).
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session
