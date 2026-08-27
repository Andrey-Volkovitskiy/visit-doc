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


async def test_create_chat_persists_a_new_chat_with_no_patient() -> None:
    async with session_factory() as session:
        created_session = await chat_repository.create_session(session)
        chat = await chat_repository.create_chat(session, created_session.id)

    assert chat.id is not None
    assert chat.session_id == created_session.id
    assert chat.patient_id is None


async def test_create_chat_makes_a_distinct_chat_every_time() -> None:
    async with session_factory() as session:
        created_session = await chat_repository.create_session(session)
        first = await chat_repository.create_chat(session, created_session.id)
        second = await chat_repository.create_chat(session, created_session.id)

    assert first.id != second.id


async def test_get_chat_returns_the_chat_for_its_own_session() -> None:
    async with session_factory() as session:
        created_session = await chat_repository.create_session(session)
        chat = await chat_repository.create_chat(session, created_session.id)

        found = await chat_repository.get_chat(session, chat.id, created_session.id)

    assert found is not None
    assert found.id == chat.id


async def test_get_chat_returns_none_for_another_sessions_chat() -> None:
    async with session_factory() as session:
        owner = await chat_repository.create_session(session)
        stranger = await chat_repository.create_session(session)
        chat = await chat_repository.create_chat(session, owner.id)

        found = await chat_repository.get_chat(session, chat.id, stranger.id)

    assert found is None


async def test_get_chat_returns_none_for_an_unknown_chat_id() -> None:
    async with session_factory() as session:
        created_session = await chat_repository.create_session(session)

        found = await chat_repository.get_chat(
            session, "nonexistent-id", created_session.id
        )

    assert found is None


async def test_set_patient_records_the_scheduler_side_patient_and_its_name() -> None:
    async with session_factory() as session:
        created_session = await chat_repository.create_session(session)
        chat = await chat_repository.create_chat(session, created_session.id)

        await chat_repository.set_patient(session, chat.id, "PATIENT01", "Ada Lovelace")
        found = await chat_repository.get_chat(session, chat.id, created_session.id)

    assert found is not None
    assert found.patient_id == "PATIENT01"
    assert found.patient_name == "Ada Lovelace"


async def test_list_chats_for_session_is_empty_for_a_session_with_none() -> None:
    async with session_factory() as session:
        created_session = await chat_repository.create_session(session)

        listed = await chat_repository.list_chats_for_session(
            session, created_session.id
        )

    assert listed == []


async def test_list_chats_for_session_excludes_another_sessions_chats() -> None:
    async with session_factory() as session:
        owner = await chat_repository.create_session(session)
        stranger = await chat_repository.create_session(session)
        await chat_repository.create_chat(session, owner.id)

        listed = await chat_repository.list_chats_for_session(session, stranger.id)

    assert listed == []


async def test_list_chats_puts_chats_with_messages_ahead_of_chats_without() -> None:
    async with session_factory() as session:
        created_session = await chat_repository.create_session(session)
        with_message = await chat_repository.create_chat(session, created_session.id)
        await chat_repository.create_message(
            session,
            id=str(ULID()),
            chat_id=with_message.id,
            sender=MessageSender.PATIENT,
            content="hi",
        )
        # Created last, so it is the newest chat - but it holds no message, so it must
        # still rank behind the one the visitor actually talked in.
        empty = await chat_repository.create_chat(session, created_session.id)

        listed = await chat_repository.list_chats_for_session(
            session, created_session.id
        )

    assert [chat.id for chat, _ in listed] == [with_message.id, empty.id]
    assert listed[0][1] is not None
    assert listed[1][1] is None


async def test_list_chats_orders_chats_with_messages_by_newest_message() -> None:
    async with session_factory() as session:
        created_session = await chat_repository.create_session(session)
        older = await chat_repository.create_chat(session, created_session.id)
        newer = await chat_repository.create_chat(session, created_session.id)
        await chat_repository.create_message(
            session,
            id=str(ULID()),
            chat_id=newer.id,
            sender=MessageSender.PATIENT,
            content="first",
        )
        await asyncio.sleep(0.01)
        await chat_repository.create_message(
            session,
            id=str(ULID()),
            chat_id=older.id,
            sender=MessageSender.PATIENT,
            content="second",
        )

        listed = await chat_repository.list_chats_for_session(
            session, created_session.id
        )

    assert [chat.id for chat, _ in listed] == [older.id, newer.id]


async def test_list_chats_orders_empty_chats_by_newest_created() -> None:
    async with session_factory() as session:
        created_session = await chat_repository.create_session(session)
        first = await chat_repository.create_chat(session, created_session.id)
        await asyncio.sleep(0.01)
        second = await chat_repository.create_chat(session, created_session.id)

        listed = await chat_repository.list_chats_for_session(
            session, created_session.id
        )

    assert [chat.id for chat, _ in listed] == [second.id, first.id]


async def test_delete_chat_removes_all_its_messages() -> None:
    async with session_factory() as session:
        created_session = await chat_repository.create_session(session)
        chat = await chat_repository.create_chat(session, created_session.id)
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
        chat = await chat_repository.create_chat(session, created_session.id)

        await chat_repository.delete_chat(session, chat.id)

        still_there = await chat_repository.get_session(session, created_session.id)

    assert still_there is not None


async def test_delete_chat_is_noop_for_unknown_chat_id() -> None:
    async with session_factory() as session:
        await chat_repository.delete_chat(session, "nonexistent-id")  # must not raise


async def test_lock_chat_blocks_a_second_holder_until_released() -> None:
    """`lock_chat` must genuinely serialize two separate DB connections, not just
    two coroutines sharing one - the whole point is to close a race between two
    concurrent HTTP requests, each with its own `AsyncSession`/connection.
    """
    chat_id = "lock-test-chat"
    order: list[str] = []
    first_holds_lock = asyncio.Event()
    release_first = asyncio.Event()

    async def hold_then_release() -> None:
        async with session_factory() as session:
            await chat_repository.lock_chat(session, chat_id)
            order.append("first-acquired")
            first_holds_lock.set()
            await release_first.wait()
            order.append("first-released")
            await chat_repository.unlock_chat(session, chat_id)

    async def acquire_after_first() -> None:
        await first_holds_lock.wait()
        async with session_factory() as session:
            await chat_repository.lock_chat(session, chat_id)
            order.append("second-acquired")
            await chat_repository.unlock_chat(session, chat_id)

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
