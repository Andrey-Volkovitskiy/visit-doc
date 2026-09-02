from collections.abc import AsyncIterator

import pytest
from chat.db.session import session_factory
from chat.repositories import chat_repository, faq_repository
from ulid import ULID

_CONTENT = "Visiting hours are 8am to 5pm."


async def _new_session_id() -> str:
    async with session_factory() as session:
        return (await chat_repository.create_session(session)).id


def _revision() -> str:
    return str(ULID())


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
