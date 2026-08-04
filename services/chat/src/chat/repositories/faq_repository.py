"""Postgres `FaqEntry` repository (async session)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chat.domain.models import FaqEntry


async def create(session: AsyncSession, content: str) -> FaqEntry:
    """Insert a new `FaqEntry` and return it."""
    entry = FaqEntry(content=content)
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return entry


async def get(session: AsyncSession, entry_id: int) -> FaqEntry | None:
    """Return the `FaqEntry` with `entry_id`, or None if it doesn't exist."""
    return await session.get(FaqEntry, entry_id)


async def list_all(session: AsyncSession) -> list[FaqEntry]:
    """Return every `FaqEntry`."""
    result = await session.execute(select(FaqEntry))
    return list(result.scalars().all())


async def update(session: AsyncSession, entry_id: int, content: str) -> FaqEntry | None:
    """Update `entry_id`'s content and return it, or None if it doesn't exist."""
    entry = await session.get(FaqEntry, entry_id)
    if entry is None:
        return None
    entry.content = content
    await session.commit()
    await session.refresh(entry)
    return entry


async def delete(session: AsyncSession, entry_id: int) -> bool:
    """Delete `entry_id`. Return True if it existed, False otherwise."""
    entry = await session.get(FaqEntry, entry_id)
    if entry is None:
        return False
    await session.delete(entry)
    await session.commit()
    return True
