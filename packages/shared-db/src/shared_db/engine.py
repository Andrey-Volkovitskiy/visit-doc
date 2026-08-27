"""Async engine and session-factory construction, shared by every service."""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(database_url: str) -> AsyncEngine:
    """Build the async engine for `database_url`."""
    return create_async_engine(database_url)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build a session factory bound to `engine`.

    `expire_on_commit` is off, so objects loaded before a commit stay usable after it
    without a refresh - which callers rely on to render a row they just wrote.
    """
    return async_sessionmaker(engine, expire_on_commit=False)
