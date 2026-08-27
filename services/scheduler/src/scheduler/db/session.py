"""This service's engine and session factory, over the shared constructors."""

from collections.abc import AsyncIterator

from shared_db import create_engine, create_session_factory
from sqlalchemy.ext.asyncio import AsyncSession

from scheduler.core.config import get_settings

engine = create_engine(get_settings().SCHEDULER_DATABASE_URL)
session_factory = create_session_factory(engine)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an `AsyncSession`."""
    async with session_factory() as session:
        yield session
