import asyncio
from collections.abc import AsyncIterator

import pytest
from chat.db.session import session_factory
from chat.domain.models import FaqEntry, Session
from chat.repositories import chat_repository, faq_repository
from sqlalchemy import select
from ulid import ULID

_CONTENT = "Visiting hours are 8am to 5pm."
# Long enough that a create which does not wait for the lock has finished by the time
# it elapses, short enough not to slow the suite down when one correctly waits.
_LOCK_WAIT_SECONDS = 0.5


async def _new_session_id() -> str:
    async with session_factory() as session:
        return (await chat_repository.create_session(session)).id


def _revision() -> str:
    return str(ULID())


async def _reserved_id() -> int:
    async with session_factory() as session:
        return await faq_repository.reserve_id(session)


@pytest.fixture
async def session_id() -> str:
    return await _new_session_id()


@pytest.fixture
async def entry_id(session_id: str) -> AsyncIterator[int]:
    async with session_factory() as session:
        entry = await faq_repository.create(session, session_id, _CONTENT, _revision())
    yield entry.id
    async with session_factory() as session:
        await faq_repository.delete(session, session_id, entry.id)


async def test_create(session_id: str) -> None:
    revision = _revision()
    async with session_factory() as session:
        entry = await faq_repository.create(session, session_id, _CONTENT, revision)
    assert entry.id is not None
    assert entry.content == _CONTENT
    assert entry.session_id == session_id
    assert entry.live_revision == revision

    async with session_factory() as session:
        await faq_repository.delete(session, session_id, entry.id)


async def test_get(session_id: str, entry_id: int) -> None:
    async with session_factory() as session:
        fetched = await faq_repository.get(session, session_id, entry_id)
    assert fetched is not None
    assert fetched.content == _CONTENT


async def test_list_all_includes_created_entry(session_id: str, entry_id: int) -> None:
    async with session_factory() as session:
        all_entries = await faq_repository.list_all(session, session_id)
    assert any(e.id == entry_id for e in all_entries)


async def test_count_for_session_counts_only_this_sessions_entries(
    session_id: str, entry_id: int
) -> None:
    # The number the cap is compared against, so it has to be this session's own - and
    # the predicate is on the count itself, not applied to rows read back and tallied
    # here.
    other = await _new_session_id()
    async with session_factory() as session:
        await faq_repository.create(session, other, _CONTENT, _revision())
        await faq_repository.create(session, session_id, _CONTENT, _revision())

    async with session_factory() as session:
        assert await faq_repository.count_for_session(session, session_id) == 2
        assert await faq_repository.count_for_session(session, other) == 1


async def test_publish_replaces_the_content_and_the_live_revision(
    session_id: str, entry_id: int
) -> None:
    async with session_factory() as session:
        before = await faq_repository.get(session, session_id, entry_id)
        assert before is not None
        expected = before.live_revision
        published = await faq_repository.publish(
            session, session_id, entry_id, "New content.", _revision(), expected
        )
    assert published is not None
    assert published.content == "New content."
    assert published.live_revision != expected


async def test_publish_writes_nothing_when_the_expected_revision_is_stale(
    session_id: str, entry_id: int
) -> None:
    # The guard is the statement's own `WHERE`, so a save that read a revision since
    # superseded publishes over nothing rather than over an answer it never saw.
    async with session_factory() as session:
        lost = await faq_repository.publish(
            session,
            session_id,
            entry_id,
            "hijacked",
            _revision(),
            "01NOTTHELIVEONE00000000000",
        )
    assert lost is None
    async with session_factory() as session:
        survivor = await faq_repository.get(session, session_id, entry_id)
    assert survivor is not None
    assert survivor.content == _CONTENT


async def test_create_within_cap_inserts_while_there_is_room(session_id: str) -> None:
    async with session_factory() as session:
        entry, count = await faq_repository.create_within_cap(
            session,
            session_id,
            _CONTENT,
            _revision(),
            await _reserved_id(),
            max_entries=2,
        )
    assert count == 0
    assert entry is not None
    async with session_factory() as session:
        assert len(await faq_repository.list_all(session, session_id)) == 1


async def test_create_within_cap_counts_only_after_the_create_ahead_of_it(
    session_id: str, entry_id: int
) -> None:
    # What makes the cap a bound rather than a likelihood: a create counts the corpus
    # only once every create in front of it has committed. The lock is held here by a
    # transaction of the test's own, so the wait is a fact of the statement rather than
    # a matter of timing - without it this create counts one entry, finds room, and
    # takes a place the entry inserted below has already taken.
    async with session_factory() as holder:
        await holder.execute(
            select(Session.id)
            .where(Session.id == session_id)
            .with_for_update(key_share=True)
        )

        async def _second_create() -> tuple[FaqEntry | None, int]:
            async with session_factory() as session:
                return await faq_repository.create_within_cap(
                    session,
                    session_id,
                    _CONTENT,
                    _revision(),
                    await _reserved_id(),
                    max_entries=2,
                )

        waiting = asyncio.create_task(_second_create())
        done, _ = await asyncio.wait({waiting}, timeout=_LOCK_WAIT_SECONDS)
        assert not done, "the create counted the corpus without waiting for the lock"

        async with session_factory() as session:
            await faq_repository.create(session, session_id, _CONTENT, _revision())
        await holder.rollback()

        entry, count = await waiting

    assert entry is None
    assert count == 2
    async with session_factory() as session:
        assert len(await faq_repository.list_all(session, session_id)) == 2


async def test_delete(session_id: str) -> None:
    async with session_factory() as session:
        entry = await faq_repository.create(session, session_id, _CONTENT, _revision())
    async with session_factory() as session:
        assert await faq_repository.delete(session, session_id, entry.id) is True
    async with session_factory() as session:
        assert await faq_repository.get(session, session_id, entry.id) is None


# --- 007: nothing crosses a session boundary, on any of the five ------------------


async def test_another_sessions_entry_is_indistinguishable_from_one_that_never_existed(
    session_id: str, entry_id: int
) -> None:
    # Scoped by a predicate on the read, never by an ownership check afterwards - so a
    # well-formed id belonging to someone else simply does not resolve.
    other = await _new_session_id()

    async with session_factory() as session:
        assert await faq_repository.get(session, other, entry_id) is None
        assert await faq_repository.list_all(session, other) == []
        mine = await faq_repository.get(session, session_id, entry_id)
        assert mine is not None
        assert (
            await faq_repository.publish(
                session, other, entry_id, "hijacked", _revision(), mine.live_revision
            )
            is None
        )
        assert await faq_repository.delete(session, other, entry_id) is False

    async with session_factory() as session:
        survivor = await faq_repository.get(session, session_id, entry_id)
    assert survivor is not None
    assert survivor.content == _CONTENT


async def test_live_revisions_returns_only_this_sessions_revisions(
    session_id: str,
) -> None:
    mine = [_revision(), _revision()]
    async with session_factory() as session:
        for revision in mine:
            await faq_repository.create(session, session_id, _CONTENT, revision)

    other = await _new_session_id()
    async with session_factory() as session:
        await faq_repository.create(session, other, _CONTENT, _revision())

    async with session_factory() as session:
        assert set(await faq_repository.live_revisions(session, session_id)) == set(
            mine
        )


async def test_live_revisions_is_empty_for_a_session_with_no_corpus() -> None:
    # The ordinary starting state of every session, and not an error. It is also what
    # the turn short-circuits on, so it must be an empty list rather than a failure.
    async with session_factory() as session:
        assert (
            await faq_repository.live_revisions(session, await _new_session_id()) == []
        )
