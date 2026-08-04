from collections.abc import AsyncIterator

import pytest
from chat.db.session import session_factory
from chat.repositories import faq_repository

_CONTENT = "Visiting hours are 8am to 5pm."


@pytest.fixture
async def entry_id() -> AsyncIterator[int]:
    async with session_factory() as session:
        entry = await faq_repository.create(session, _CONTENT)
    yield entry.id
    async with session_factory() as session:
        await faq_repository.delete(session, entry.id)


async def test_create() -> None:
    async with session_factory() as session:
        entry = await faq_repository.create(session, _CONTENT)
    assert entry.id is not None
    assert entry.content == _CONTENT

    async with session_factory() as session:
        await faq_repository.delete(session, entry.id)


async def test_get(entry_id: int) -> None:
    async with session_factory() as session:
        fetched = await faq_repository.get(session, entry_id)
    assert fetched is not None
    assert fetched.content == _CONTENT


async def test_list_all_includes_created_entry(entry_id: int) -> None:
    async with session_factory() as session:
        all_entries = await faq_repository.list_all(session)
    assert any(e.id == entry_id for e in all_entries)


async def test_update_persists_new_content(entry_id: int) -> None:
    async with session_factory() as session:
        updated = await faq_repository.update(session, entry_id, "Updated hours.")
    assert updated is not None
    assert updated.content == "Updated hours."

    # New session: `session_factory` sets `expire_on_commit=False`, so re-`get`-ing on
    # the same session would return the cached in-memory object without hitting the DB.
    async with session_factory() as session:
        refetched = await faq_repository.get(session, entry_id)
    assert refetched is not None
    assert refetched.content == "Updated hours."


async def test_delete(entry_id: int) -> None:
    async with session_factory() as session:
        assert await faq_repository.delete(session, entry_id) is True
    async with session_factory() as session:
        assert await faq_repository.get(session, entry_id) is None


async def test_delete_unknown_id_returns_false() -> None:
    async with session_factory() as session:
        assert await faq_repository.delete(session, -1) is False
