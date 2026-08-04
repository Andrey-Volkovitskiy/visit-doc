"""Async SQLAlchemy engine/session factory for the app's database."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from chat.core.config import Settings, get_settings


def create_engine(settings: Settings) -> AsyncEngine:
    """Build the async engine for the given settings."""
    return create_async_engine(settings.DATABASE_URL)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build a session factory bound to the given engine."""
    return async_sessionmaker(engine, expire_on_commit=False)


engine = create_engine(get_settings())
session_factory = create_session_factory(engine)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an `AsyncSession`."""
    async with session_factory() as session:
        yield session
