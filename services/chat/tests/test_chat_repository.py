"""Tests for `chat_repository.py`'s `Session`/`Chat` persistence (data-model.md)."""

import asyncio
from itertools import pairwise

from chat.db.session import session_factory
from chat.domain.models import MessageSender
from chat.repositories import chat_repository
from ulid import ULID


def _randomness(ulid_str: str) -> int:
    """Return the 80-bit randomness portion of a ULID string as an int."""
    return int.from_bytes(ULID.from_str(ulid_str).bytes[6:], "big")


async def test_create_session_returns_persisted_row() -> None:
    async with session_factory() as session:
        created = await chat_repository.create_session(session)

    assert created.id is not None
    assert created.created_at is not None


async def test_get_session_returns_none_for_unknown_id() -> None:
    async with session_factory() as session:
        fetched = await chat_repository.get_session(session, "unknown-id")
    assert fetched is None


async def test_get_session_returns_created_session() -> None:
    async with session_factory() as session:
        created = await chat_repository.create_session(session)

    async with session_factory() as session:
        fetched = await chat_repository.get_session(session, created.id)

    assert fetched is not None
    assert fetched.id == created.id


async def test_session_ids_are_not_sequential_or_monotonic() -> None:
    """FR-017: consecutive `Session.id`s must not be guessable from one another.

    `python-ulid`'s bare `ULID()` constructor is monotonic *by default* in the
    installed version - same-millisecond calls increment the previous randomness by
    1 rather than resourcing fresh entropy (research.md #1, empirically verified).
    `create_session` MUST route around that via an explicit `PureRandomPolicy`
    generator; this test fails against the monotonic default.
    """
    async with session_factory() as session:
        ids = [(await chat_repository.create_session(session)).id for _ in range(10)]

    randomness_values = [_randomness(i) for i in ids]
    diffs = [b - a for a, b in pairwise(randomness_values)]

    assert not all(diff == 1 for diff in diffs)


async def test_get_or_create_chat_for_session_creates_one_when_none_exists() -> None:
    async with session_factory() as session:
        created_session = await chat_repository.create_session(session)
        chat = await chat_repository.get_or_create_chat_for_session(
            session, created_session.id
        )

    assert chat.id is not None
    assert chat.session_id == created_session.id


async def test_get_or_create_chat_for_session_reuses_existing_chat() -> None:
    async with session_factory() as session:
        created_session = await chat_repository.create_session(session)
        first = await chat_repository.get_or_create_chat_for_session(
            session, created_session.id
        )
        second = await chat_repository.get_or_create_chat_for_session(
            session, created_session.id
        )

    assert first.id == second.id


async def test_delete_chat_removes_all_its_messages() -> None:
    async with session_factory() as session:
        created_session = await chat_repository.create_session(session)
        chat = await chat_repository.get_or_create_chat_for_session(
            session, created_session.id
        )
        await chat_repository.create_message(
            session,
            id=str(ULID()),
            chat_id=chat.id,
            sender=MessageSender.PATIENT,
            content="hi",
        )

        await chat_repository.delete_chat(session, chat.id)

        remaining = await chat_repository.list_messages(session, chat.id)

    assert remaining == []


async def test_delete_chat_leaves_session_untouched() -> None:
    async with session_factory() as session:
        created_session = await chat_repository.create_session(session)
        chat = await chat_repository.get_or_create_chat_for_session(
            session, created_session.id
        )

        await chat_repository.delete_chat(session, chat.id)

        still_there = await chat_repository.get_session(session, created_session.id)

    assert still_there is not None


async def test_delete_chat_is_noop_for_unknown_chat_id() -> None:
    async with session_factory() as session:
        await chat_repository.delete_chat(session, "nonexistent-id")  # must not raise


async def test_lock_session_blocks_a_second_holder_until_released() -> None:
    """`lock_session` must genuinely serialize two separate DB connections, not just
    two coroutines sharing one - the whole point is to close a race between two
    concurrent HTTP requests, each with its own `AsyncSession`/connection.
    """
    session_id = "lock-test-session"
    order: list[str] = []
    first_holds_lock = asyncio.Event()
    release_first = asyncio.Event()

    async def hold_then_release() -> None:
        async with session_factory() as session:
            await chat_repository.lock_session(session, session_id)
            order.append("first-acquired")
            first_holds_lock.set()
            await release_first.wait()
            order.append("first-released")
            await chat_repository.unlock_session(session, session_id)

    async def acquire_after_first() -> None:
        await first_holds_lock.wait()
        async with session_factory() as session:
            await chat_repository.lock_session(session, session_id)
            order.append("second-acquired")
            await chat_repository.unlock_session(session, session_id)

    first_task = asyncio.create_task(hold_then_release())
    await first_holds_lock.wait()
    second_task = asyncio.create_task(acquire_after_first())

    # Give the second task a real chance to attempt (and block on) the lock before
    # releasing the first - proves it's actually waiting, not just winning a race.
    await asyncio.sleep(0.1)
    assert not second_task.done()

    release_first.set()
    await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=5)

    assert order == ["first-acquired", "first-released", "second-acquired"]
