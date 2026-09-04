"""Postgres `FaqEntry` repository (async session).

Every function takes the owning session id and puts it in the statement's `WHERE`
clause. Scoping is a predicate on the read or the write, never a check applied to the
result afterwards, so an id belonging to another session resolves to nothing rather
than being caught after the row has already been handed back.
"""

from collections.abc import Sequence

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select, text
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from chat.domain.models import FaqEntry, Session


async def reserve_id(session: AsyncSession) -> int:
    """Return the next `faq_entries` id without inserting a row.

    Separates allocating an entry's identity from publishing it, which is what lets a
    create write its indexed chunks - each carrying the entry they belong to - before
    the row that makes them live exists. A reserved id that is never published is
    simply skipped; the sequence is not a count of rows.

    The value is never returned twice, to this caller or any other, and nothing resets
    the sequence - so an id from here is fresh, and no chunk written by an earlier
    attempt can be carrying it. A create therefore has no older revision of its own to
    sweep, and adding a sweep to that path would only ever scroll and find nothing.
    """
    (entry_id,) = await reserve_ids(session, 1)
    return entry_id


async def reserve_ids(session: AsyncSession, count: int) -> list[int]:
    """Return `count` fresh `faq_entries` ids without inserting any rows.

    Taken in one statement rather than one round trip per id: the caller planting a
    whole starter corpus needs every id before it writes a single chunk, and each id
    carries the same guarantee the single-id case does - fresh, never handed out twice,
    and costing nothing if the entry it was reserved for is never published.
    """
    result = await session.execute(
        text("SELECT nextval('faq_entries_id_seq') FROM generate_series(1, :count)"),
        {"count": count},
    )
    return [int(entry_id) for entry_id in result.scalars().all()]


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


async def create_many(
    session: AsyncSession,
    session_id: str,
    entries: Sequence[tuple[int, str, str]],
) -> list[FaqEntry]:
    """Insert several entries owned by `session_id`, in one transaction.

    Args:
        entries: One `(entry_id, content, live_revision)` triple per entry, each id
            already taken from the sequence by `reserve_ids` and each revision already
            written to the retrieval store.

    Returns: the inserted entries, in the order they were given.

    One commit publishes all of them, so the corpus this creates is all there or not
    there at all - a caller cannot end up with half a starter corpus and no way to name
    which half. There is no cap check and no row lock here, unlike `create_within_cap`:
    the only caller plants a session's corpus before that session's id has left the
    process, so no other create can be racing it, and how many entries it may plant is
    decided by the caller from the same cap.
    """
    rows = [
        FaqEntry(
            id=entry_id,
            session_id=session_id,
            content=content,
            live_revision=live_revision,
        )
        for entry_id, content, live_revision in entries
    ]
    session.add_all(rows)
    await session.commit()
    for row in rows:
        await session.refresh(row)
    return rows


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
    """Return how many entries `session_id` owns.

    Counted by the database, in one row, rather than by pulling the ids across and
    measuring the list here - the number is all any caller wants, and this one runs
    inside the locked section that serializes a session's creates.
    """
    result = await session.execute(
        select(func.count())
        .select_from(FaqEntry)
        .where(FaqEntry.session_id == session_id)
    )
    return int(result.scalar_one())


async def create_within_cap(
    session: AsyncSession,
    session_id: str,
    content: str,
    live_revision: str,
    entry_id: int,
    max_entries: int,
) -> tuple[FaqEntry | None, int]:
    """Insert an entry unless `session_id` already owns `max_entries` of them.

    Returns: the inserted entry, or None if the corpus was already at its cap, and how
        many entries the session owned when that was decided.

    Counting and inserting are one transaction, and every create for one session queues
    behind a row lock on the session that owns the corpus - so each one counts the rows
    of every create that committed before it, and the cap bounds the corpus rather than
    merely usually bounding it. The lock is taken as a statement of its own on purpose:
    a count folded into the same statement would answer from the snapshot that
    statement began with, which is the one from before the wait. It is taken in its
    no-key form, which conflicts with another create and with nothing that merely
    references the session - a new chat, or any other table's foreign key check - so
    only what has to serialize does.

    A refused create writes nothing at all, so the id its caller reserved is simply
    never used.
    """
    await session.execute(
        select(Session.id)
        .where(Session.id == session_id)
        .with_for_update(key_share=True)
    )
    count = await count_for_session(session, session_id)
    if count >= max_entries:
        await session.rollback()
        return None, count
    entry = await create(session, session_id, content, live_revision, entry_id)
    return entry, count


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
