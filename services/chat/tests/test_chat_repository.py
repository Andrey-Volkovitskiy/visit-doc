"""Tests for `chat_repository.py`'s `Session`/`Chat` persistence (data-model.md)."""

import asyncio
from itertools import pairwise

import pytest
from chat.db.session import pinned_session, session_factory
from chat.domain.models import EscalationReason, MessageSender
from chat.repositories import chat_repository, faq_repository
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

        await chat_repository.set_patient(
            session, chat.id, created_session.id, "PATIENT01", "Ada Lovelace"
        )
        found = await chat_repository.get_chat(session, chat.id, created_session.id)

    assert found is not None
    assert found.patient_id == "PATIENT01"
    assert found.patient_name == "Ada Lovelace"


async def test_set_patient_ignores_a_chat_id_from_another_session() -> None:
    async with session_factory() as session:
        owner = await chat_repository.create_session(session)
        stranger = await chat_repository.create_session(session)
        chat = await chat_repository.create_chat(session, owner.id)

        await chat_repository.set_patient(
            session, chat.id, stranger.id, "PATIENT01", "Ada Lovelace"
        )
        found = await chat_repository.get_chat(session, chat.id, owner.id)

    assert found is not None
    assert found.patient_id is None
    assert found.patient_name is None


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
            session_id=created_session.id,
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
            session_id=created_session.id,
            sender=MessageSender.PATIENT,
            content="first",
        )
        await asyncio.sleep(0.01)
        await chat_repository.create_message(
            session,
            id=str(ULID()),
            chat_id=older.id,
            session_id=created_session.id,
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
            session_id=created_session.id,
            sender=MessageSender.PATIENT,
            content="hi",
        )

        await chat_repository.delete_chat(session, chat.id, created_session.id)

        remaining = await chat_repository.list_messages(session, chat.id)

    assert remaining == []


async def test_delete_chat_leaves_session_untouched() -> None:
    async with session_factory() as session:
        created_session = await chat_repository.create_session(session)
        chat = await chat_repository.create_chat(session, created_session.id)

        await chat_repository.delete_chat(session, chat.id, created_session.id)

        still_there = await chat_repository.get_session(session, created_session.id)

    assert still_there is not None


async def test_delete_chat_is_noop_for_unknown_chat_id() -> None:
    async with session_factory() as session:
        created_session = await chat_repository.create_session(session)
        # Must not raise.
        await chat_repository.delete_chat(session, "nonexistent-id", created_session.id)


async def test_delete_chat_ignores_a_chat_id_from_another_session() -> None:
    async with session_factory() as session:
        owner = await chat_repository.create_session(session)
        stranger = await chat_repository.create_session(session)
        chat = await chat_repository.create_chat(session, owner.id)

        await chat_repository.delete_chat(session, chat.id, stranger.id)
        survivor = await chat_repository.get_chat(session, chat.id, owner.id)

    assert survivor is not None


# --- messages: the insert reaches a chat this session owns, or no row at all --------


async def test_create_message_returns_the_message_it_wrote() -> None:
    async with session_factory() as session:
        created_session = await chat_repository.create_session(session)
        chat = await chat_repository.create_chat(session, created_session.id)

        written = await chat_repository.create_message(
            session,
            id=str(ULID()),
            chat_id=chat.id,
            session_id=created_session.id,
            sender=MessageSender.PATIENT,
            content="hello",
        )

    assert written is not None
    assert written.chat_id == chat.id
    assert written.sender == MessageSender.PATIENT
    assert written.content == "hello"
    assert written.created_at is not None


async def test_create_message_writes_nothing_into_another_sessions_chat() -> None:
    """A chat id on its own must not admit a message: unique is not permission.

    The refusal has to be visible in the return value too - a caller that cannot tell
    it apart from a write would go on to answer, log, or render a message the thread
    does not hold.
    """
    async with session_factory() as session:
        owner = await chat_repository.create_session(session)
        stranger = await chat_repository.create_session(session)
        chat = await chat_repository.create_chat(session, owner.id)

        refused = await chat_repository.create_message(
            session,
            id=str(ULID()),
            chat_id=chat.id,
            session_id=stranger.id,
            sender=MessageSender.PATIENT,
            content="not mine to send",
        )
        held = await chat_repository.list_messages(session, chat.id)

    assert refused is None
    assert held == []


async def test_create_message_writes_nothing_into_an_unknown_chat() -> None:
    # The same answer a chat from another session gets, for the same reason `get_chat`
    # gives one answer to both: neither names a chat this session may write into.
    async with session_factory() as session:
        created_session = await chat_repository.create_session(session)

        refused = await chat_repository.create_message(
            session,
            id=str(ULID()),
            chat_id="nonexistent-id",
            session_id=created_session.id,
            sender=MessageSender.PATIENT,
            content="nowhere to land",
        )

    assert refused is None


# --- what an assistant reply's insert declined to write, and why --------------------
#
# The insert carries two predicates in one `WHERE` - the chat is this session's, and
# nobody has taken it over - so "wrote nothing" covers two situations that have nothing
# to do with each other. Folded into one answer, a chat deleted mid-turn is recorded and
# reasoned about as a staff member taking the conversation, when no person did anything.


async def _answered_chat() -> tuple[str, str, str]:
    """Return a fresh `(session_id, chat_id, message_id)` with one patient message."""
    async with session_factory() as session:
        session_row = await chat_repository.create_session(session)
        chat = await chat_repository.create_chat(session, session_row.id)
        message = await chat_repository.create_message(
            session,
            id=str(ULID()),
            chat_id=chat.id,
            session_id=session_row.id,
            sender=MessageSender.PATIENT,
            content="when can I visit?",
        )
    assert message is not None
    return session_row.id, chat.id, message.id


async def _reply_answering(
    session_id: str, chat_id: str, message_id: str
) -> chat_repository.ReplyWrite:
    """Write one assistant reply to `message_id`, returning what the write answered."""
    async with session_factory() as session:
        return await chat_repository.create_assistant_reply_unless_taken_over(
            session,
            id=str(ULID()),
            chat_id=chat_id,
            session_id=session_id,
            answering_message_id=message_id,
            content="Visiting hours are 8am to 5pm.",
            grounded=True,
            citations=None,
            reply_to_message_ids=[message_id],
        )


async def test_a_reply_nobody_took_over_is_stored() -> None:
    session_id, chat_id, message_id = await _answered_chat()

    write = await _reply_answering(session_id, chat_id, message_id)

    assert write is chat_repository.ReplyWrite.STORED
    async with session_factory() as session:
        messages = await chat_repository.list_messages(session, chat_id)
    assert [m.sender for m in messages] == [
        MessageSender.PATIENT,
        MessageSender.ASSISTANT,
    ]


async def test_a_reply_a_staff_post_beat_is_refused_as_a_takeover() -> None:
    session_id, chat_id, message_id = await _answered_chat()
    async with session_factory() as session:
        await chat_repository.create_message(
            session,
            id=str(ULID()),
            chat_id=chat_id,
            session_id=session_id,
            sender=MessageSender.STAFF,
            content="I've got this one.",
        )

    write = await _reply_answering(session_id, chat_id, message_id)

    assert write is chat_repository.ReplyWrite.TAKEN_OVER
    async with session_factory() as session:
        messages = await chat_repository.list_messages(session, chat_id)
    assert [m.sender for m in messages] == [MessageSender.PATIENT, MessageSender.STAFF]


async def test_a_reply_into_a_deleted_chat_is_not_reported_as_a_takeover() -> None:
    # `DELETE /chats/{id}` landing while the turn was generating. Nothing was written
    # here either, but nobody took the conversation over - there is no conversation.
    # Told apart because the caller acts on the difference: one is a person leading the
    # chat, and reporting it for a chat that no longer exists puts a staff member in a
    # conversation nobody ever touched.
    session_id, chat_id, message_id = await _answered_chat()
    async with session_factory() as session:
        await chat_repository.delete_chat(session, chat_id, session_id)

    write = await _reply_answering(session_id, chat_id, message_id)

    assert write is chat_repository.ReplyWrite.CHAT_GONE


async def test_a_reply_into_another_sessions_chat_is_not_reported_as_a_takeover() -> (
    None
):
    # The other half of the ownership predicate, and the same answer: a chat id from
    # another session names no conversation this caller may be answering in.
    _, chat_id, message_id = await _answered_chat()
    async with session_factory() as session:
        stranger = await chat_repository.create_session(session)

    write = await _reply_answering(stranger.id, chat_id, message_id)

    assert write is chat_repository.ReplyWrite.CHAT_GONE
    async with session_factory() as session:
        messages = await chat_repository.list_messages(session, chat_id)
    assert [m.sender for m in messages] == [MessageSender.PATIENT]


async def _staff_posted_in(session_id: str, chat_id: str) -> None:
    """Take `chat_id` over the way the console does, with one staff message."""
    async with session_factory() as session:
        await chat_repository.create_message(
            session,
            id=str(ULID()),
            chat_id=chat_id,
            session_id=session_id,
            sender=MessageSender.STAFF,
            content="I've got this one.",
        )


async def test_a_staff_post_in_another_sessions_chat_does_not_refuse_a_reply() -> None:
    # The guard resolved its anchor by message id alone, correlated to no chat at all,
    # so `answering_message_id` naming a message of some other conversation made *that*
    # conversation's staff messages decide whether a reply may land in this one - across
    # sessions included. Unique means no collision, not permission to be read from.
    session_id, chat_id, _ = await _answered_chat()
    stranger_session, stranger_chat, stranger_message = await _answered_chat()
    await _staff_posted_in(stranger_session, stranger_chat)

    write = await _reply_answering(session_id, chat_id, stranger_message)

    assert write is chat_repository.ReplyWrite.STORED
    async with session_factory() as session:
        messages = await chat_repository.list_messages(session, chat_id)
    assert [m.sender for m in messages] == [
        MessageSender.PATIENT,
        MessageSender.ASSISTANT,
    ]


# --- what a read of the same guard answered, and about which conversation -----------
#
# The predicate has two consumers - the reply insert's `WHERE` above and the read below
# - and a correlated subquery compiles differently in each, so both are checked. The
# read has a third answer the guard does not need: it is asked by a turn that stored no
# reply, and that turn has to tell "nobody took this over" from "there is no longer a
# conversation to take".


async def test_a_takeover_read_of_an_untouched_conversation_finds_no_takeover() -> None:
    session_id, chat_id, message_id = await _answered_chat()

    async with session_factory() as session:
        read = await chat_repository.get_takeover_since(
            session, chat_id, session_id, message_id
        )

    assert read is chat_repository.TakeoverRead.NOT_TAKEN_OVER


async def test_a_takeover_read_sees_the_staff_post_that_took_the_conversation() -> None:
    session_id, chat_id, message_id = await _answered_chat()
    await _staff_posted_in(session_id, chat_id)

    async with session_factory() as session:
        read = await chat_repository.get_takeover_since(
            session, chat_id, session_id, message_id
        )

    assert read is chat_repository.TakeoverRead.TAKEN_OVER


async def test_a_staff_post_in_another_sessions_chat_is_no_takeover_here() -> None:
    # Finding 1's other consumer: the anchor is scoped to the chat being asked about,
    # so another conversation's staff messages answer nothing about this one.
    session_id, chat_id, _ = await _answered_chat()
    stranger_session, stranger_chat, stranger_message = await _answered_chat()
    await _staff_posted_in(stranger_session, stranger_chat)

    async with session_factory() as session:
        read = await chat_repository.get_takeover_since(
            session, chat_id, session_id, stranger_message
        )

    assert read is chat_repository.TakeoverRead.NOT_TAKEN_OVER


async def test_a_takeover_read_of_a_deleted_chat_says_the_chat_is_gone() -> None:
    # `DELETE /chats/{id}` landing mid-turn again, this time reaching the read rather
    # than the insert. Answered False, it told a caller "nobody took this conversation
    # over" about a conversation that no longer exists - which is what let a turn go on
    # to escalate, and record escalating, a chat nobody can be handed.
    session_id, chat_id, message_id = await _answered_chat()
    async with session_factory() as session:
        await chat_repository.delete_chat(session, chat_id, session_id)

    async with session_factory() as session:
        read = await chat_repository.get_takeover_since(
            session, chat_id, session_id, message_id
        )

    assert read is chat_repository.TakeoverRead.CHAT_GONE


async def test_a_takeover_read_of_another_sessions_chat_says_the_chat_is_gone() -> None:
    # The other half of the ownership predicate, and the same answer: a chat id from
    # another session names no conversation this caller has anything to ask about.
    _, chat_id, message_id = await _answered_chat()
    async with session_factory() as session:
        stranger = await chat_repository.create_session(session)

    async with session_factory() as session:
        read = await chat_repository.get_takeover_since(
            session, chat_id, stranger.id, message_id
        )

    assert read is chat_repository.TakeoverRead.CHAT_GONE


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
    session_id, chat_id = await _fresh_chat()

    async with pinned_session() as session:
        backend_pid_before = (
            await session.execute(sql_text("SELECT pg_backend_pid()"))
        ).scalar_one()
        await chat_repository.lock_chat(session, chat_id)

        await chat_repository.create_message(
            session,
            id=str(ULID()),
            chat_id=chat_id,
            session_id=session_id,
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
    """A release that freed nothing is never reported as a success.

    Postgres answers `pg_advisory_unlock` with false rather than an error, so the only
    thing separating "released" from "this connection was not holding it" is that this
    is raised rather than swallowed. Which of the two the second one is - a lock still
    held elsewhere, or one already gone with the connection that took it - is not
    knowable from here, and neither the error nor its log entry claims to know.
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
    # The guard lives in the write's own `WHERE`, and this layer knows nothing about
    # which reasons silence - so the two reasons here are simply two distinct values,
    # and this is the one place that proves a second one cannot overwrite the first.
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
            session, chat_id, session_id, EscalationReason.PATIENT_ASKED_FOR_PERSON
        )
        await chat_repository.mark_attention(session, chat_id, session_id)
        await chat_repository.clear_escalation(session, chat_id, session_id)

    state = await _state(session_id, chat_id)
    assert state.may_assistant_reply is True
    assert state.emphasized is True


async def test_marking_attention_says_whether_it_queued_the_conversation() -> None:
    session_id, chat_id = await _fresh_chat()

    async with session_factory() as session:
        first = await chat_repository.mark_attention(session, chat_id, session_id)
        again = await chat_repository.mark_attention(session, chat_id, session_id)

    assert first is True
    assert again is False


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


# --- the admin surface's listing ------------------------------------------------------


async def test_list_sessions_returns_every_session_in_list_session_ids_order() -> None:
    # The two are read by different queries, and a sweep's report is matched against a
    # listing row by row - so they must not be able to disagree about the order.
    first, _ = await _fresh_chat()
    second, _ = await _fresh_chat()

    async with session_factory() as session:
        summaries = await chat_repository.list_sessions(session)
        ids = await chat_repository.list_session_ids(session)

    assert [s.session_id for s in summaries] == ids == [first, second]


async def test_list_sessions_counts_chats_and_entries_independently() -> None:
    """Two chats and two entries is 2 and 2, never 4 and 4.

    A session joined to both multiplies one against the other, and every count taken
    over that product is wrong - which is invisible at one of each, the shape any
    smaller fixture would have.
    """
    session_id, _ = await _fresh_chat()
    async with session_factory() as session:
        await chat_repository.create_chat(session, session_id)
        await faq_repository.create(session, session_id, "a", str(ULID()))
        await faq_repository.create(session, session_id, "b", str(ULID()))

    async with session_factory() as session:
        summary = next(
            s
            for s in await chat_repository.list_sessions(session)
            if s.session_id == session_id
        )

    assert (summary.chats, summary.faq_entries) == (2, 2)


async def test_list_sessions_reports_no_last_message_for_a_silent_session() -> None:
    # None means "nobody ever said anything here", and nothing else - a session that
    # holds a chat but no message is still silent.
    session_id, _ = await _fresh_chat()

    async with session_factory() as session:
        summary = next(
            s
            for s in await chat_repository.list_sessions(session)
            if s.session_id == session_id
        )

    assert summary.last_message_at is None
    assert summary.chats == 1


async def test_list_sessions_reports_the_newest_message_across_every_chat() -> None:
    session_id, first_chat = await _fresh_chat()
    async with session_factory() as session:
        second_chat = await chat_repository.create_chat(session, session_id)
        await chat_repository.create_message(
            session,
            id=str(ULID()),
            chat_id=second_chat.id,
            session_id=session_id,
            sender=MessageSender.PATIENT,
            content="first",
        )
        await asyncio.sleep(0.01)
        newest = await chat_repository.create_message(
            session,
            id=str(ULID()),
            chat_id=first_chat,
            session_id=session_id,
            sender=MessageSender.PATIENT,
            content="second",
        )
    assert newest is not None

    async with session_factory() as session:
        summary = next(
            s
            for s in await chat_repository.list_sessions(session)
            if s.session_id == session_id
        )

    assert summary.last_message_at == newest.created_at


async def test_list_sessions_counts_only_the_session_it_is_describing() -> None:
    # Every count here is its own subquery, and a missing predicate on any one of them
    # would report the whole table's total against every row alike.
    quiet, _ = await _fresh_chat()
    busy, busy_chat = await _fresh_chat()
    async with session_factory() as session:
        await faq_repository.create(session, busy, "a", str(ULID()))
        await chat_repository.create_message(
            session,
            id=str(ULID()),
            chat_id=busy_chat,
            session_id=busy,
            sender=MessageSender.PATIENT,
            content="hi",
        )

    async with session_factory() as session:
        summaries = {
            s.session_id: s for s in await chat_repository.list_sessions(session)
        }

    assert (summaries[quiet].chats, summaries[quiet].faq_entries) == (1, 0)
    assert summaries[quiet].last_message_at is None
    assert (summaries[busy].chats, summaries[busy].faq_entries) == (1, 1)
    assert summaries[busy].last_message_at is not None
