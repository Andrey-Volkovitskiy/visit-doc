"""Postgres `FaqEntry` repository (async session).

Every function takes the owning session id and puts it in the statement's `WHERE`
clause. Scoping is a predicate on the read or the write, never a check applied to the
result afterwards, so an id belonging to another session resolves to nothing rather
than being caught after the row has already been handed back.
"""

from sqlalchemy import delete as sql_delete
from sqlalchemy import select, text
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from chat.domain.models import FaqEntry


async def reserve_id(session: AsyncSession) -> int:
    """Return the next `faq_entries` id without inserting a row.

    Separates allocating an entry's identity from publishing it, which is what lets a
    create write its indexed chunks - each carrying the entry they belong to - before
    the row that makes them live exists. A reserved id that is never published is
    simply skipped; the sequence is not a count of rows.
    """
    result = await session.execute(text("SELECT nextval('faq_entries_id_seq')"))
    return int(result.scalar_one())


async def create(
    session: AsyncSession,
    session_id: str,
    content: str,
    live_revision: str,
    entry_id: int | None = None,
) -> FaqEntry:
    """Insert a new `FaqEntry` owned by `session_id` and return it.

    Args:
        live_revision: The revision whose indexed chunks retrieval may search for this
            entry.
        entry_id: An id already taken from the sequence by `reserve_id`, when the
            entry's chunks were written before this row existed. Omitted, the sequence
            supplies one at insert time.

    This insert is the moment the entry becomes visible - to a listing and to retrieval
    alike - because it is the only thing that names a revision live.
    """
    entry = FaqEntry(
        id=entry_id, session_id=session_id, content=content, live_revision=live_revision
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return entry


async def get(session: AsyncSession, session_id: str, entry_id: int) -> FaqEntry | None:
    """Return `session_id`'s entry `entry_id`, or None if it has no such entry."""
    result = await session.execute(
        select(FaqEntry).where(
            FaqEntry.id == entry_id, FaqEntry.session_id == session_id
        )
    )
    return result.scalars().first()


async def list_all(session: AsyncSession, session_id: str) -> list[FaqEntry]:
    """Return every entry `session_id` owns, oldest first."""
    result = await session.execute(
        select(FaqEntry)
        .where(FaqEntry.session_id == session_id)
        .order_by(FaqEntry.id.asc())
    )
    return list(result.scalars().all())


async def count_for_session(session: AsyncSession, session_id: str) -> int:
    """Return how many entries `session_id` owns."""
    result = await session.execute(
        select(FaqEntry.id).where(FaqEntry.session_id == session_id)
    )
    return len(result.scalars().all())


async def live_revisions(session: AsyncSession, session_id: str) -> list[str]:
    """Return the revision of every entry `session_id` owns.

    These are exactly the revisions retrieval may search: one per entry, and no entry
    without one. An empty list is the ordinary starting state of a session that has
    added nothing yet, and is not an error - it is distinguishable from a failed read
    only because a failure raises rather than returning an empty list.
    """
    result = await session.execute(
        select(FaqEntry.live_revision).where(FaqEntry.session_id == session_id)
    )
    return list(result.scalars().all())


async def publish(
    session: AsyncSession,
    session_id: str,
    entry_id: int,
    content: str,
    live_revision: str,
    expected_revision: str,
) -> FaqEntry | None:
    """Publish `live_revision` for `entry_id`, unless another save got there first.

    Args:
        expected_revision: The revision that was live when this save read the entry.
            Carried in the statement's own `WHERE`, so a save that read a revision
            since superseded writes nothing rather than publishing over one it never
            saw.

    Returns: the updated entry, or None if the guard matched no row.

    None is a *failed, retryable save* - not a missing entry. The two are told apart by
    the caller, which already knows the entry existed a moment ago because it read it.
    """
    result = await session.execute(
        sql_update(FaqEntry)
        .where(
            FaqEntry.id == entry_id,
            FaqEntry.session_id == session_id,
            FaqEntry.live_revision == expected_revision,
        )
        .values(content=content, live_revision=live_revision)
        .returning(FaqEntry)
    )
    entry = result.scalars().first()
    if entry is None:
        await session.rollback()
        return None
    await session.commit()
    return entry


async def delete(session: AsyncSession, session_id: str, entry_id: int) -> bool:
    """Delete `session_id`'s entry `entry_id`. Return True if it existed.

    Removing the row un-publishes every revision it named, so the entry stops being
    answerable at this instant - before anything touches the retrieval store.
    """
    result = await session.execute(
        sql_delete(FaqEntry)
        .where(FaqEntry.id == entry_id, FaqEntry.session_id == session_id)
        .returning(FaqEntry.id)
    )
    deleted = result.scalars().first()
    await session.commit()
    return deleted is not None
