"""Tests for `chat_repository.py`'s `Session`/`Chat` persistence (data-model.md)."""

import asyncio
from itertools import pairwise

import pytest
from chat.db.session import pinned_session, session_factory
from chat.domain.models import EscalationReason, MessageSender
from chat.repositories import chat_repository
from chat.repositories.chat_repository import ConversationState
from sqlalchemy import text as sql_text
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
        async with pinned_session() as session:
            await chat_repository.lock_chat(session, chat_id)
            order.append("first-acquired")
            first_holds_lock.set()
            await release_first.wait()
            order.append("first-released")
            await chat_repository.unlock_chat(session, chat_id)

    async def acquire_after_first() -> None:
        await first_holds_lock.wait()
        async with pinned_session() as session:
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


async def _advisory_locks_in_this_database() -> int:
    """Return how many advisory locks the test database currently holds.

    `pg_locks` is cluster-wide, so the count is narrowed to `current_database()` - a
    locally-running service against the *dev* database is a different database and must
    not be mistaken for something this suite left behind.
    """
    async with session_factory() as session:
        return (
            await session.execute(
                sql_text(
                    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                    "AND database = "
                    "(SELECT oid FROM pg_database WHERE datname = current_database())"
                )
            )
        ).scalar_one()


async def test_chat_lock_is_taken_and_released_on_one_connection_across_a_commit() -> (
    None
):
    """The locked section commits, and the lock must not travel with the connection.

    An advisory lock belongs to the connection that took it, and a session bound to
    the engine returns its connection to the pool at the end of every transaction - so
    a `commit()` inside the section leaves the lock behind on a pooled connection and
    the release then runs on whatever the pool hands out next. That release does not
    fail loudly: `pg_advisory_unlock` reports false, the section finishes looking
    healthy, and the chat is unlockable for the lifetime of the process.

    The sibling checkout in the middle is what makes the difference observable. Without
    it the pool usually returns the very same connection and the bug hides.
    """
    _, chat_id = await _fresh_chat()

    async with pinned_session() as session:
        backend_pid_before = (
            await session.execute(sql_text("SELECT pg_backend_pid()"))
        ).scalar_one()
        await chat_repository.lock_chat(session, chat_id)

        await chat_repository.create_message(
            session,
            id=str(ULID()),
            chat_id=chat_id,
            sender=MessageSender.PATIENT,
            content="a message, committed inside the locked section",
        )

        # A sibling session takes a connection out of the pool while the section is
        # mid-flight, exactly as `run_pipeline`'s insert/escalation sessions do.
        async with session_factory() as sibling:
            await sibling.execute(sql_text("SELECT 1"))

        backend_pid_after = (
            await session.execute(sql_text("SELECT pg_backend_pid()"))
        ).scalar_one()
        assert backend_pid_after == backend_pid_before

        # Raises `ChatLockNotHeldError` if the lock was taken somewhere this
        # connection can no longer reach.
        await chat_repository.release_chat_lock(session, chat_id)

    assert await _advisory_locks_in_this_database() == 0


async def test_lock_chat_refuses_a_session_that_borrows_its_connection() -> None:
    """A lock this session cannot keep must be refused, not taken and then stranded."""
    async with session_factory() as session:
        with pytest.raises(chat_repository.UnpinnedChatLockError):
            await chat_repository.lock_chat(session, "borrowed-connection-chat")

    assert await _advisory_locks_in_this_database() == 0


async def test_release_chat_lock_surfaces_a_release_that_freed_nothing() -> None:
    """A release that freed nothing means the lock is stranded - never a success.

    Postgres answers `pg_advisory_unlock` with false rather than an error, so the only
    thing separating "released" from "still held, and now unreachable" is that this is
    raised rather than swallowed.
    """
    async with pinned_session() as session:
        with pytest.raises(chat_repository.ChatLockNotHeldError):
            await chat_repository.release_chat_lock(session, "never-locked-chat")


# --- 007: may the assistant speak here, and has a person acted --------------------


async def _fresh_chat() -> tuple[str, str]:
    """Return a fresh `(session_id, chat_id)`."""
    async with session_factory() as session:
        session_row = await chat_repository.create_session(session)
        chat = await chat_repository.create_chat(session, session_row.id)
    return session_row.id, chat.id


async def _state(session_id: str, chat_id: str) -> ConversationState:
    async with session_factory() as session:
        state = await chat_repository.get_conversation_state(
            session, chat_id, session_id
        )
    assert state is not None
    return state


async def test_an_ordinary_conversation_lets_the_assistant_speak() -> None:
    session_id, chat_id = await _fresh_chat()

    state = await _state(session_id, chat_id)

    assert state.may_assistant_reply is True
    assert state.pause_seconds_remaining is None
    assert state.emphasized is False


async def test_an_escalation_silences_and_emphasizes_with_no_deadline() -> None:
    session_id, chat_id = await _fresh_chat()

    async with session_factory() as session:
        transitioned = await chat_repository.set_escalated(
            session, chat_id, session_id, EscalationReason.PATIENT_ASKED_FOR_PERSON
        )
        await chat_repository.mark_attention(session, chat_id, session_id)

    state = await _state(session_id, chat_id)
    assert transitioned is True
    assert state.may_assistant_reply is False
    # Nothing about time passing means a patient who asked for a human got one.
    assert state.pause_seconds_remaining is None
    assert state.emphasized is True


async def test_a_second_escalation_transitions_nothing_and_keeps_the_first_reason() -> (
    None
):
    session_id, chat_id = await _fresh_chat()

    async with session_factory() as session:
        await chat_repository.set_escalated(
            session, chat_id, session_id, EscalationReason.PATIENT_ASKED_FOR_PERSON
        )
        again = await chat_repository.set_escalated(
            session, chat_id, session_id, EscalationReason.CORPUS_COULD_NOT_ANSWER
        )

    state = await _state(session_id, chat_id)
    assert again is False
    assert state.escalation_reason == EscalationReason.PATIENT_ASKED_FOR_PERSON


async def test_a_pause_silences_and_counts_down() -> None:
    session_id, chat_id = await _fresh_chat()

    async with session_factory() as session:
        await chat_repository.set_paused_until(session, chat_id, session_id, 120)

    state = await _state(session_id, chat_id)
    assert state.may_assistant_reply is False
    assert state.pause_seconds_remaining is not None
    assert 115 <= state.pause_seconds_remaining <= 120


async def test_an_elapsed_pause_lifts_by_itself() -> None:
    # Nothing runs when a deadline passes: the next read is what observes it.
    session_id, chat_id = await _fresh_chat()

    async with session_factory() as session:
        await chat_repository.set_paused_until(session, chat_id, session_id, -1)

    state = await _state(session_id, chat_id)
    assert state.may_assistant_reply is True
    assert state.pause_seconds_remaining is None


async def test_clearing_the_escalation_leaves_the_attention_alone() -> None:
    # The two axes, and the one place they most obviously disagree: returning the
    # assistant ends the silence, and nobody has answered the patient.
    session_id, chat_id = await _fresh_chat()

    async with session_factory() as session:
        await chat_repository.set_escalated(
            session, chat_id, session_id, EscalationReason.CORPUS_COULD_NOT_ANSWER
        )
        await chat_repository.mark_attention(session, chat_id, session_id)
        await chat_repository.clear_escalation(session, chat_id, session_id)

    state = await _state(session_id, chat_id)
    assert state.may_assistant_reply is True
    assert state.emphasized is True


async def test_attention_is_not_restamped_while_a_conversation_is_already_waiting() -> (
    None
):
    # It has been waiting since the first thing that needed a person; re-stamping would
    # send it to the back of a list ordered by how long each has waited.
    session_id, chat_id = await _fresh_chat()

    async with session_factory() as session:
        await chat_repository.mark_attention(session, chat_id, session_id)
    first = (await _state(session_id, chat_id)).attention_since

    async with session_factory() as session:
        await chat_repository.mark_attention(session, chat_id, session_id)

    assert (await _state(session_id, chat_id)).attention_since == first


async def test_another_sessions_conversation_has_no_state_here() -> None:
    session_id, chat_id = await _fresh_chat()
    other_session_id, _ = await _fresh_chat()
    del session_id

    async with session_factory() as session:
        assert (
            await chat_repository.get_conversation_state(
                session, chat_id, other_session_id
            )
            is None
        )
