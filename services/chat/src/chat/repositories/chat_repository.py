"""Postgres `Session`/`Chat`/`Message` repository (async session)."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    ColumnElement,
    Connection,
    Insert,
    Integer,
    Select,
    String,
    Text,
    and_,
    case,
    exists,
    func,
    insert,
    literal,
    nullslast,
    or_,
    select,
    text,
    update,
)
from sqlalchemy import delete as sql_delete
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from ulid import PureRandomPolicy, ULIDGenerator

from chat.core.logging import get_logger
from chat.domain.models import (
    CLEARABLE_MARKS,
    AttentionMark,
    Chat,
    EscalationReason,
    FaqEntry,
    Message,
    MessageSender,
    Session,
)

# Shared ULID generator for both `Session.id` and `Chat.id`. `Session.id` is a bearer
# credential (the session cookie value) and MUST be non-guessable (FR-017); bare
# `ULID()` is monotonic *by default* in the installed python-ulid version -
# same-millisecond calls increment the previous randomness by 1 rather than
# resourcing it (research.md #1, empirically verified). This `PureRandomPolicy`
# generator always sources fresh `os.urandom` entropy instead, and is stateless, so
# one module-level instance is safe to share across calls - reused for `Chat.id` too,
# which has no non-guessability requirement of its own but loses nothing from it.
_id_generator = ULIDGenerator(policy=PureRandomPolicy())


async def create_session(session: AsyncSession) -> Session:
    """Create and persist a new `Session` with a non-guessable id."""
    new_session = Session(id=str(_id_generator.generate()))
    session.add(new_session)
    await session.commit()
    await session.refresh(new_session)
    return new_session


async def get_session(session: AsyncSession, session_id: str) -> Session | None:
    """Return the `Session` with `session_id`, or None if it doesn't exist."""
    return await session.get(Session, session_id)


async def list_chats_for_session(
    session: AsyncSession, session_id: str
) -> list[tuple[Chat, datetime | None]]:
    """Return `session_id`'s chats in display order, each with its newest message time.

    Returns: a list of `(chat, last_message_at)` pairs, where `last_message_at` is the
        creation time of that chat's newest message, or None if it has none.

    Ordered so that a chat holding messages always outranks one holding none - even if
    the empty chat was created more recently - and within each group by recency. That
    is subtler than sorting on a coalesced timestamp, which would let a brand-new empty
    chat displace the one the visitor was just talking in.
    """
    last_message_at = func.max(Message.created_at).label("last_message_at")
    result = await session.execute(
        select(Chat, last_message_at)
        .outerjoin(Message, Message.chat_id == Chat.id)
        .where(Chat.session_id == session_id)
        .group_by(Chat.id)
        .order_by(
            nullslast(last_message_at.desc()),
            Chat.created_at.desc(),
        )
    )
    return [(chat, newest) for chat, newest in result.all()]


async def create_chat(session: AsyncSession, session_id: str) -> Chat:
    """Create and persist a new `Chat` for `session_id`, with no patient yet."""
    chat = Chat(id=str(_id_generator.generate()), session_id=session_id)
    session.add(chat)
    await session.commit()
    await session.refresh(chat)
    return chat


async def get_chat(session: AsyncSession, chat_id: str, session_id: str) -> Chat | None:
    """Return `chat_id` if it belongs to `session_id`, else None.

    Scoped by filter rather than by a post-hoc ownership check, so a chat belonging to
    another session is indistinguishable from one that never existed.
    """
    result = await session.execute(
        select(Chat).where(Chat.id == chat_id, Chat.session_id == session_id)
    )
    return result.scalars().first()


async def set_patient(
    session: AsyncSession,
    chat_id: str,
    session_id: str,
    patient_id: str,
    patient_name: str,
) -> None:
    """Record the scheduler-side patient this chat books on behalf of.

    Scoped to `session_id` on the write itself, so a chat id from another session
    writes nothing rather than being caught by a check after the fact.

    `patient_name` is cached, never authored here - it is whatever the scheduler
    reported, kept so the chat list can render without a call per row.
    """
    await session.execute(
        update(Chat)
        .where(Chat.id == chat_id, Chat.session_id == session_id)
        .values(patient_id=patient_id, patient_name=patient_name)
    )
    await session.commit()


async def set_patient_name(
    session: AsyncSession, chat_id: str, session_id: str, patient_name: str
) -> None:
    """Update this chat's cached patient name.

    Scoped to `session_id` on the write itself, so a chat id from another session
    updates nothing rather than being caught by a check after the fact.
    """
    await session.execute(
        update(Chat)
        .where(Chat.id == chat_id, Chat.session_id == session_id)
        .values(patient_name=patient_name)
    )
    await session.commit()


class UnpinnedChatLockError(RuntimeError):
    """Raised when `lock_chat` is given a session that borrows connections per
    transaction, rather than one holding a single connection for the whole section.
    """


class ChatLockNotHeldError(RuntimeError):
    """Raised when Postgres reports that the connection asked to release a chat's
    advisory lock was not holding it.

    Says nothing about where the lock went. Postgres answers false both when another,
    live connection is still holding it and when nobody is - the connection that took
    it having been replaced, which releases whatever it held.
    """


async def lock_chat(session: AsyncSession, chat_id: str) -> None:
    """Take a connection-scoped Postgres advisory lock keyed by `chat_id`.

    Raises: UnpinnedChatLockError if `session` is not bound to a single connection
        held for the whole locked section - use `db.session.pinned_session()`.

    Blocks until any other connection holding the same lock releases it. Keyed by chat
    rather than by session so two chats in one browser never serialize against each
    other - what has to be serialized is one chat's history read and message insert,
    since a concurrent sibling message could otherwise be missed by a history read
    whose insert has not committed yet.

    Connection-scoped, not transaction-scoped: the lock lives on the connection that
    took it, and follows that connection wherever it goes - including back into the
    pool, which is what the guard above exists to prevent. A session bound to the
    engine hands its connection back at the end of every transaction, so a `commit()`
    inside the locked section would leave the lock on a connection this section no
    longer has, and the release would run against a different one. That release
    reports failure rather than raising (`pg_advisory_unlock` returns false), so
    nothing about the section looks wrong afterwards while the lock is stranded for
    the lifetime of the process.

    Must be released via `release_chat_lock`, always in a `finally`, before the
    pinned session's connection returns to the pool.
    """
    bind = session.get_bind()
    if not isinstance(bind, Connection):
        raise UnpinnedChatLockError(
            "lock_chat needs a session bound to one held connection "
            "(db.session.pinned_session), not one bound to the engine"
        )
    await session.execute(
        text("SELECT pg_advisory_lock(hashtext(:chat_id)::bigint)"),
        {"chat_id": chat_id},
    )


async def unlock_chat(session: AsyncSession, chat_id: str) -> None:
    """Release the advisory lock `lock_chat` took for `chat_id`.

    Raises: ChatLockNotHeldError if Postgres reports this connection was not holding
        that lock - whatever became of the lock, this path can no longer reach it.

    `pg_advisory_unlock` answers false rather than raising when it releases nothing,
    which reads exactly like a release that worked. Turning that into an error is what
    keeps the two apart. It does not tell them apart from each other, though: false is
    equally the answer when another live connection is still holding the lock - and the
    chat it keys is then unlockable for that connection's lifetime, every later turn or
    staff action in it waiting forever - and when the connection that took it has since
    been replaced, which released it along with everything else that backend held.
    """
    released = (
        await session.execute(
            text("SELECT pg_advisory_unlock(hashtext(:chat_id)::bigint)"),
            {"chat_id": chat_id},
        )
    ).scalar_one()
    if not released:
        raise ChatLockNotHeldError(
            f"this connection was not holding chat {chat_id}'s advisory lock"
        )


async def release_chat_lock(session: AsyncSession, chat_id: str) -> None:
    """Release `chat_id`'s advisory lock, recovering from an aborted transaction.

    Raises: ChatLockNotHeldError if the lock could not be released because this
        connection never held it.

    Belongs in a `finally`, always. A statement inside the locked section may have
    aborted `session`'s transaction, in which case Postgres refuses the unlock too
    (`InFailedSQLTransactionError`) - masking the real error and leaking the advisory
    lock on this pooled connection.

    The rollback is deliberately on that failure path only. An unconditional one would
    also expire every object loaded inside the section despite `expire_on_commit=False`,
    which governs `commit()` and not `rollback()` - forcing a doomed refresh once
    `session` closes and they are used detached.

    A release that freed nothing is logged before it is re-raised, because this runs
    in a `finally`: the exception it raises may be the one the caller sees, or it may
    be replacing one already in flight, and what happened here is too important to
    depend on which of those does.
    """
    try:
        try:
            await unlock_chat(session, chat_id)
        except SQLAlchemyError:
            await session.rollback()
            await unlock_chat(session, chat_id)
    except ChatLockNotHeldError:
        # The event says only what Postgres reported - this connection was not holding
        # the lock - because that is the whole of what is known here. Whether a lock is
        # *stranded* is a further fact this side cannot see, and the likeliest way to
        # get here is the one where none is: the connection was replaced under us (a
        # terminated backend, a restart, a reset, a proxy drop), and a dead backend
        # releases its own advisory locks. `error` rather than `critical` for the same
        # reason - a `critical` that fires on the survivable case teaches people to
        # scroll past `critical`.
        get_logger().error("chat.lock_not_held", chat_id=chat_id)
        raise


async def release_chat_lock_after_commit(session: AsyncSession, chat_id: str) -> None:
    """Release `chat_id`'s advisory lock without letting the release decide the answer.

    Belongs in the `finally` of a locked section whose writes have already committed.
    Those writes are durable whatever happens here, so a release that fails is recorded
    and goes no further: raising would replace the answer the commit earned with a 500
    or a truncated stream, and a caller told their write did not happen sends it again -
    which the patient reads as the clinic answering the same thing twice.

    `release_chat_lock` records the release that freed nothing; this entry is the other
    half of it - that the caller was nevertheless told their write succeeded, so nobody
    reading the log has to infer which of the two happened. Both sit at `error`, and
    deliberately not at `critical`: the lock may be held on a connection nothing here
    can reach, in which case the chat it keys is stuck, or it may already be gone with
    the connection that took it, in which case nothing is wrong but this entry.
    """
    try:
        await release_chat_lock(session, chat_id)
    except Exception as exc:  # noqa: BLE001 - see the docstring: nothing raised here
        # can undo a committed write, so nothing raised here may change the answer.
        get_logger().error(
            "chat.lock_release_failed", chat_id=chat_id, error_detail=str(exc)
        )


def _insert_into_owned_chat(
    *,
    id: str,
    chat_id: str,
    session_id: str,
    sender: MessageSender,
    content: str,
    grounded: bool | None,
    citations: list[dict[str, object]] | None,
    reply_to_message_ids: list[str] | None,
    unless: ColumnElement[bool] | None = None,
) -> Insert:
    """Build the insert of one message into `chat_id`, if `session_id` owns it.

    Args:
        unless: A further condition on the chat that must be false for the row to be
            written. None for a write nothing but ownership guards.

    An `INSERT ... FROM SELECT` over `chats` rather than an insert of a row built here:
    `Message` carries no session of its own, so the only way the scope can belong to
    the write is for the write to read the chat. A chat id from another session then
    selects no row and inserts nothing, instead of being checked either side of a write
    that already happened.
    """
    source = select(
        literal(id, String),
        Chat.id,
        literal(sender.value, String),
        literal(content, Text),
        literal(grounded, Boolean),
        literal(citations, JSONB),
        literal(reply_to_message_ids, JSONB),
    ).where(Chat.id == chat_id, Chat.session_id == session_id)
    if unless is not None:
        source = source.where(~unless)
    return insert(Message).from_select(
        [
            "id",
            "chat_id",
            "sender",
            "content",
            "grounded",
            "citations",
            "reply_to_message_ids",
        ],
        source,
    )


async def create_message(
    session: AsyncSession,
    *,
    id: str,
    chat_id: str,
    session_id: str,
    sender: MessageSender,
    content: str,
    grounded: bool | None = None,
    citations: list[dict[str, object]] | None = None,
    reply_to_message_ids: list[str] | None = None,
) -> Message | None:
    """Insert a new `Message` into `session_id`'s chat `chat_id` and return it.

    Args:
        id: Caller-supplied, never generated here - a patient message reuses the
            request's turn id; an assistant message gets a fresh ULID.
        reply_to_message_ids: For an assistant message, every patient message id it
            answers, in order (more than one for a merged burst). Not meaningful for a
            patient message.

    Returns: the message written, or None if `session_id` does not own `chat_id` and
        nothing was written. The two are told apart so that no caller can report, or
        answer, a message the thread does not hold.

    Scoped on the write itself rather than by the caller having resolved the chat
    first: a chat id from another session - or one deleted since it was resolved -
    writes nothing rather than being caught by a check that the insert has already
    outrun.

    Append-only: a `Message` is written once, in full, with no "complete"/pending step.
    """
    result = await session.execute(
        _insert_into_owned_chat(
            id=id,
            chat_id=chat_id,
            session_id=session_id,
            sender=sender,
            content=content,
            grounded=grounded,
            citations=citations,
            reply_to_message_ids=reply_to_message_ids,
        ).returning(Message)
    )
    message = result.scalars().first()
    await session.commit()
    if message is None:
        return None
    get_logger().info(
        "message.persisted",
        message_id=message.id,
        chat_id=chat_id,
        sender=sender,
        content=content,
    )
    return message


class ReplyWrite(StrEnum):
    """What became of one assistant reply's insert.

    Three members because the insert declines to write for two unrelated reasons, and a
    caller owes the conversation something different for each. A write that could not be
    attempted is none of them - it raises, so "nobody is expecting this reply" is never
    confused with "the store did not answer".
    """

    # The reply is in the thread.
    STORED = "stored"
    # A person is leading the conversation now, so the assistant's reply is not wanted.
    TAKEN_OVER = "taken_over"
    # The chat is no longer this session's - deleted while the reply was generating.
    # Nobody took anything over; there is simply no conversation left to answer.
    CHAT_GONE = "chat_gone"


async def create_assistant_reply_unless_taken_over(
    session: AsyncSession,
    *,
    id: str,
    chat_id: str,
    session_id: str,
    answering_message_id: str,
    content: str,
    grounded: bool | None,
    citations: list[dict[str, object]] | None,
    reply_to_message_ids: list[str] | None,
) -> ReplyWrite:
    """Insert one assistant reply, unless a person has taken the conversation over.

    Args:
        answering_message_id: The patient message this reply answers - the anchor a
            takeover is measured from, so a staff message that predates it does not
            count against a reply the patient is still owed.

    Returns: what became of the reply - see `ReplyWrite`. A write that could not be
        attempted raises rather than answering one of them.

    The guard is the `WHERE` of the insert itself, not a check preceding it: between a
    read and a separate write a staff member can post, and the reply that lands behind
    theirs is exactly the one FR-013a says must never stand beside it. It carries the
    session predicate for the same reason - a chat id alone says nothing about who may
    be answered in it.

    Both predicates live in one `WHERE`, so an insert that writes nothing cannot say
    which of them stopped it. The ownership half is therefore asked again, alongside the
    insert rather than after it: the insert is a data-modifying CTE, which Postgres runs
    exactly once and to completion whatever the enclosing query reads, so one statement
    answers both halves and a takeover is never reported for a chat that is simply gone.
    """
    inserted = (
        _insert_into_owned_chat(
            id=id,
            chat_id=chat_id,
            session_id=session_id,
            sender=MessageSender.ASSISTANT,
            content=content,
            grounded=grounded,
            citations=citations,
            reply_to_message_ids=reply_to_message_ids,
            unless=_taken_over_since(answering_message_id),
        )
        .returning(Message.id)
        .cte("inserted")
    )
    result = await session.execute(
        select(
            exists(select(inserted.c.id)),
            exists(_owned_chat_id(chat_id, session_id)),
        )
    )
    stored, chat_owned = result.one()
    await session.commit()
    if not stored:
        return ReplyWrite.TAKEN_OVER if chat_owned else ReplyWrite.CHAT_GONE
    get_logger().info(
        "message.persisted",
        message_id=id,
        chat_id=chat_id,
        sender=MessageSender.ASSISTANT,
        content=content,
    )
    return ReplyWrite.STORED


async def list_messages(session: AsyncSession, chat_id: str) -> list[Message]:
    """Return `chat_id`'s messages in chronological order.

    Ordered by `created_at`, not `id` - ULIDs are only monotonic within the
    generating process's clock/randomness and aren't guaranteed to sort in true
    creation order across concurrent writers.
    """
    result = await session.execute(
        select(Message)
        .where(Message.chat_id == chat_id)
        .order_by(Message.created_at.asc())
    )
    return list(result.scalars().all())


async def delete_chat(session: AsyncSession, chat_id: str, session_id: str) -> None:
    """Hard-delete `session_id`'s chat `chat_id`.

    Reached through `get_chat`, so the session predicate is on the read that finds the
    row rather than on a check applied to it afterwards: a chat belonging to another
    session is deleted no more than one that never existed, and neither is an error.

    Its `Message` rows cascade via FK, not an application-level loop.
    """
    chat = await get_chat(session, chat_id, session_id)
    if chat is None:
        return
    await session.delete(chat)
    await session.commit()


# --- Whether the assistant may speak here, and whether a person is still needed -----

# Read in SQL rather than compared in Python. This deadline is written by one request
# and read by another, so a Python-side clock on a different worker would be a second
# clock that can disagree with the one the deadline was written against.
_PAUSE_IS_RUNNING = and_(
    Chat.assistant_paused_until.is_not(None),
    Chat.assistant_paused_until > func.now(),
)
_MAY_ASSISTANT_REPLY = and_(Chat.escalated_at.is_(None), ~_PAUSE_IS_RUNNING)
_PAUSE_SECONDS_REMAINING = case(
    (
        _PAUSE_IS_RUNNING,
        func.ceil(func.extract("epoch", Chat.assistant_paused_until - func.now())).cast(
            Integer
        ),
    ),
    else_=None,
)


# A person is needed here and none has spoken since. Deliberately not the same question
# as whether the assistant may speak: a failure raises this without silencing, and the
# console's switch ends a silence without answering anybody.
_EMPHASIZED = or_(Chat.escalated_at.is_not(None), Chat.attention_since.is_not(None))


@dataclass(frozen=True)
class ConversationState:
    """One conversation's escalation, pause and attention, plus what they imply.

    `may_assistant_reply` and `pause_seconds_remaining` are computed from the stored
    columns in the same statement that reads them, never stored beside them - so the
    answer a console renders and the answer a turn acts on cannot disagree.
    """

    escalated_at: datetime | None
    escalation_reason: str | None
    assistant_paused_until: datetime | None
    attention_since: datetime | None
    may_assistant_reply: bool
    pause_seconds_remaining: int | None

    @property
    def emphasized(self) -> bool:
        """Whether this conversation still needs a person."""
        return self.escalated_at is not None or self.attention_since is not None


# Everything a `ConversationState` is built from, in its field order. Named once so a
# read of the state and a write that reports the state it left select the same things:
# two lists would be one edit away from describing the same conversation differently.
_CONVERSATION_STATE = (
    Chat.escalated_at,
    Chat.escalation_reason,
    Chat.assistant_paused_until,
    Chat.attention_since,
    _MAY_ASSISTANT_REPLY,
    _PAUSE_SECONDS_REMAINING,
)


async def get_conversation_state(
    session: AsyncSession, chat_id: str, session_id: str
) -> ConversationState | None:
    """Return `chat_id`'s conversation state, or None if it is not this session's."""
    result = await session.execute(
        select(*_CONVERSATION_STATE).where(
            Chat.id == chat_id, Chat.session_id == session_id
        )
    )
    row = result.first()
    if row is None:
        return None
    return ConversationState(*row)


def _taken_over_since(message_id: str) -> ColumnElement[bool]:
    """A predicate on `Chat`: has a person taken it over since `message_id` arrived?

    True when a staff member has posted in the conversation since that message, or when
    the assistant is paused - the console's two gestures for taking a conversation, both
    of which mean a person is leading it now rather than the assistant.

    Built as an expression rather than answered here so the guard can travel into the
    `WHERE` of the write it protects, instead of being read once and trusted afterwards.

    The anchor is resolved *inside* the `Chat` row the enclosing statement is acting on,
    never by its id alone. This predicate decides a write, and a message id unique
    across the table still says nothing about which conversation it belongs to - by id
    alone, an anchor from some other chat made that chat's staff messages decide whether
    a reply may land here. Correlated rather than given a chat and session of its own,
    so the conversation it answers about is by construction the one the statement is
    already scoped to - the same row the pause half beside it reads its deadline from.
    An anchor naming no message of that chat therefore contributes nothing, exactly as
    one naming no message at all does.

    Read against the database's own clock for the reason `_PAUSE_IS_RUNNING` is read in
    SQL: the deadline is written by one request and read by another, and a Python-side
    comparison would be a second clock that can disagree with it.
    """
    anchor = aliased(Message)
    later = aliased(Message)
    staff_spoke = exists(
        select(later.id)
        .join(anchor, anchor.chat_id == later.chat_id)
        .where(
            anchor.chat_id == Chat.id,
            anchor.id == message_id,
            later.sender == MessageSender.STAFF.value,
            later.created_at > anchor.created_at,
        )
    )
    return or_(staff_spoke, _PAUSE_IS_RUNNING)


class TakeoverRead(StrEnum):
    """What one read of a conversation's takeover answered.

    Three members because "nobody took this conversation over" and "there is no such
    conversation here" are facts about two different worlds, and a bool answered both
    with False - leaving the caller to apply, and record, a decision about a chat that
    is gone as though it were an ordinary no-op.
    """

    # Nobody has taken it over since the message asked about.
    NOT_TAKEN_OVER = "not_taken_over"
    # A person is leading it now: a staff member posted since, or the assistant is
    # paused.
    TAKEN_OVER = "taken_over"
    # The chat is not this session's - deleted while the turn ran, or never theirs.
    # Nobody took anything over; there is no conversation left to take.
    CHAT_GONE = "chat_gone"


async def get_takeover_since(
    session: AsyncSession, chat_id: str, session_id: str, message_id: str
) -> TakeoverRead:
    """Say whether a person has taken `chat_id` over since `message_id` arrived.

    Returns: what the read found - see `TakeoverRead`.

    One statement for all three answers rather than a second read to tell the last two
    apart: the row comes back only when `session_id` owns `chat_id`, so its presence
    already carries the ownership half and the takeover predicate rides on it.
    """
    result = await session.execute(
        select(_taken_over_since(message_id)).where(
            Chat.id == chat_id, Chat.session_id == session_id
        )
    )
    row = result.one_or_none()
    if row is None:
        return TakeoverRead.CHAT_GONE
    return TakeoverRead.TAKEN_OVER if bool(row[0]) else TakeoverRead.NOT_TAKEN_OVER


async def set_escalated(
    session: AsyncSession, chat_id: str, session_id: str, reason: EscalationReason
) -> bool:
    """Escalate `chat_id` for `reason`, unless it is escalated already.

    Returns: True if this call transitioned the conversation, False if it was already
        escalated and nothing changed.

    The guard is part of the write, so a second escalation cannot overwrite the reason
    that first silenced the conversation.
    """
    result = await session.execute(
        update(Chat)
        .where(
            Chat.id == chat_id,
            Chat.session_id == session_id,
            Chat.escalated_at.is_(None),
        )
        .values(escalated_at=func.now(), escalation_reason=reason.value)
        .returning(Chat.id)
    )
    transitioned = result.scalars().first() is not None
    await session.commit()
    return transitioned


async def clear_escalation(
    session: AsyncSession, chat_id: str, session_id: str
) -> None:
    """End `chat_id`'s escalation, leaving its attention and marks untouched."""
    await session.execute(
        update(Chat)
        .where(Chat.id == chat_id, Chat.session_id == session_id)
        .values(escalated_at=None, escalation_reason=None)
    )
    await session.commit()


@dataclass(frozen=True)
class PauseWrite:
    """What one pause write did: the state it left, and the pause it replaced.

    `restarted` is about the row as it was an instant before, which only the statement
    that overwrote it can report - a read taken beforehand would be answering about a
    deadline the write had not yet reached.
    """

    state: ConversationState
    restarted: bool


async def set_paused_until(
    session: AsyncSession, chat_id: str, session_id: str, seconds: int
) -> PauseWrite | None:
    """Silence the assistant in `chat_id` for `seconds` from now.

    Returns: the conversation's state as this write left it, and whether a pause was
        already running when it landed - or None if `chat_id` is not this session's, in
        which case nothing was written at all. A chat this session owns always answers
        with a state, so None never stands for "no pause was running".

    Restarts a pause that was already running, so a staff member sending a sequence of
    messages never has the assistant cut in between them.

    Both halves of the answer come from the write itself, which is why no read runs on
    either side of it: `RETURNING` reads the row the statement produced, and the joined
    `previous` is the same row as it was before the statement - Postgres evaluates a
    `FROM` entry against the pre-update snapshot. The session predicate stays on the
    `UPDATE`'s own `WHERE`; the join adds the earlier image, not the scope.
    """
    previous = aliased(Chat, name="previous")
    result = await session.execute(
        update(Chat)
        .where(
            Chat.id == chat_id,
            Chat.session_id == session_id,
            previous.id == Chat.id,
        )
        .values(
            assistant_paused_until=func.now()
            + func.make_interval(0, 0, 0, 0, 0, 0, seconds)
        )
        .returning(
            *_CONVERSATION_STATE,
            and_(
                previous.assistant_paused_until.is_not(None),
                previous.assistant_paused_until > func.now(),
            ),
        )
    )
    row = result.first()
    await session.commit()
    if row is None:
        return None
    *state, restarted = row
    return PauseWrite(state=ConversationState(*state), restarted=bool(restarted))


async def clear_pause(
    session: AsyncSession, chat_id: str, session_id: str
) -> ConversationState | None:
    """Let the assistant speak in `chat_id` again.

    Returns: the conversation's state as this write left it, or None if `chat_id` is
        not this session's - the same answer a read of that chat gives, and for the
        same reason: there is no such conversation here.

    Reported by the write rather than by a read after it, so a caller that must render
    the state it just changed does not go back for it.
    """
    result = await session.execute(
        update(Chat)
        .where(Chat.id == chat_id, Chat.session_id == session_id)
        .values(assistant_paused_until=None)
        .returning(*_CONVERSATION_STATE)
    )
    row = result.first()
    await session.commit()
    if row is None:
        return None
    return ConversationState(*row)


async def mark_attention(session: AsyncSession, chat_id: str, session_id: str) -> bool:
    """Record that `chat_id` needs a person, if it is not waiting already.

    Returns: True if this call put the conversation in the queue, False if it was
        waiting already and nothing changed.

    Left alone when already set: the conversation has been waiting since the first
    thing that needed a person, and re-stamping it would send it to the back of a queue
    ordered by how long each has waited.
    """
    result = await session.execute(
        update(Chat)
        .where(
            Chat.id == chat_id,
            Chat.session_id == session_id,
            Chat.attention_since.is_(None),
        )
        .values(attention_since=func.now())
        .returning(Chat.id)
    )
    emphasized = result.scalars().first() is not None
    await session.commit()
    return emphasized


async def clear_attention(session: AsyncSession, chat_id: str, session_id: str) -> None:
    """Record that a person has spoken in `chat_id`."""
    await session.execute(
        update(Chat)
        .where(Chat.id == chat_id, Chat.session_id == session_id)
        .values(attention_since=None)
    )
    await session.commit()


def _owned_chat_id(chat_id: str, session_id: str) -> Select[tuple[str]]:
    """Select `chat_id`, and nothing at all unless `session_id` owns it.

    `Message` carries no session of its own, so a write addressing one reaches its
    owner through the chat. Written as a subquery for the enclosing statement's `WHERE`
    rather than as a lookup performed first: the scope then belongs to the write, and a
    message id from another session addresses no row instead of addressing one that is
    checked afterwards.
    """
    return select(Chat.id).where(Chat.id == chat_id, Chat.session_id == session_id)


async def set_attention_mark(
    session: AsyncSession,
    chat_id: str,
    session_id: str,
    message_id: str,
    mark: AttentionMark,
) -> None:
    """Mark `message_id` as needing a person, for `mark`'s reason.

    Scoped to `chat_id` and its owning `session_id` on the write itself, so a message
    id naming another conversation - or another session's - marks nothing. The message
    id alone would not do it: unique means no collision, not permission to write.
    """
    await session.execute(
        update(Message)
        .where(
            Message.id == message_id,
            Message.chat_id.in_(_owned_chat_id(chat_id, session_id)),
        )
        .values(attention_mark=mark.value)
    )
    await session.commit()


async def clear_clearable_marks(
    session: AsyncSession, chat_id: str, session_id: str
) -> int:
    """Clear every mark in `session_id`'s chat `chat_id` that a person speaking answers.

    Returns: how many marks this statement cleared. Zero for a conversation that held
        none, and zero for a chat this session does not own - which is not two meanings
        but one, since neither cleared anything.

    One statement, however many marks accumulated. The permanent kinds are absent from
    its predicate rather than skipped afterwards: a staff member answering the patient
    does not mean the corpus gained the entry it was missing, or that the failure did
    not happen.
    """
    result = await session.execute(
        update(Message)
        .where(
            Message.chat_id.in_(_owned_chat_id(chat_id, session_id)),
            Message.attention_mark.in_([mark.value for mark in CLEARABLE_MARKS]),
        )
        .values(attention_mark=None)
        .returning(Message.id)
    )
    cleared = len(list(result.scalars().all()))
    await session.commit()
    return cleared


@dataclass(frozen=True)
class ConsoleConversation:
    """One row of the staff side's listing, with everything it renders.

    `emphasized`, `may_assistant_reply` and `pause_seconds_remaining` are computed in
    the same statement that reads the columns deciding them, so what a staff member is
    shown and what a turn acts on cannot disagree.
    """

    chat_id: str
    patient_name: str | None
    last_message_at: datetime | None
    escalated_at: datetime | None
    escalation_reason: str | None
    attention_since: datetime | None
    emphasized: bool
    may_assistant_reply: bool
    pause_seconds_remaining: int | None


async def list_conversations_for_console(
    session: AsyncSession, session_id: str
) -> list[ConsoleConversation]:
    """Return every conversation in `session_id`, in the staff side's display order.

    Ordered so the ones needing a person come first and, among those, the one that has
    been waiting longest leads - ascending `attention_since`, which is stamped once and
    never re-stamped, so a conversation that keeps being ignored keeps its place rather
    than being sent to the back. The rest keep the patient side's own order: the chat
    with the newest message first, then chats holding none.

    Every conversation appears, emphasized or not: this is a listing, not a queue.
    """
    last_message_at = func.max(Message.created_at).label("last_message_at")
    emphasized = _EMPHASIZED.label("emphasized")
    result = await session.execute(
        select(
            Chat.id,
            Chat.patient_name,
            last_message_at,
            Chat.escalated_at,
            Chat.escalation_reason,
            Chat.attention_since,
            emphasized,
            _MAY_ASSISTANT_REPLY,
            _PAUSE_SECONDS_REMAINING,
        )
        .outerjoin(Message, Message.chat_id == Chat.id)
        .where(Chat.session_id == session_id)
        .group_by(Chat.id)
        .order_by(
            emphasized.desc(),
            nullslast(Chat.attention_since.asc()),
            nullslast(last_message_at.desc()),
            Chat.created_at.desc(),
        )
    )
    return [ConsoleConversation(*row) for row in result.all()]


@dataclass(frozen=True)
class SessionDeletion:
    """What removing one session's row took with it, by cascade."""

    chats_deleted: int
    faq_entries_deleted: int


async def list_session_ids(session: AsyncSession) -> list[str]:
    """Return every session this service holds, oldest first.

    One of the two queries in this module with no session predicate, because the
    session *is* what it enumerates - and both are reachable only from the admin
    surface. Ordered by id within a timestamp, since `created_at` defaults to the
    transaction's clock and two sessions can share it exactly.
    """
    result = await session.execute(
        select(Session.id).order_by(Session.created_at, Session.id)
    )
    return list(result.scalars().all())


@dataclass(frozen=True)
class SessionSummary:
    """One session as the admin surface lists it, with what deleting it would take.

    `chats` and `faq_entries` are the two counts a deletion reports, read here before
    the fact, so a listing and a deletion report describe a session in the same terms.
    `last_message_at` is the newest message in any of its chats, and None for a session
    that holds none - a session created by a visit that never said anything.
    """

    session_id: str
    created_at: datetime
    chats: int
    faq_entries: int
    last_message_at: datetime | None


async def list_sessions(session: AsyncSession) -> list[SessionSummary]:
    """Return every session this service holds, oldest first, with its counts.

    The order is `list_session_ids`', so a listing read beforehand lines up row for row
    with the report a sweep of every session returns.

    The counts are correlated subqueries rather than joins: a session joined to both
    its chats and its FAQ entries multiplies one against the other, and a count taken
    over that product is wrong for every session holding more than one of either.
    """
    chats = (
        select(func.count())
        .select_from(Chat)
        .where(Chat.session_id == Session.id)
        .scalar_subquery()
    )
    faq_entries = (
        select(func.count())
        .select_from(FaqEntry)
        .where(FaqEntry.session_id == Session.id)
        .scalar_subquery()
    )
    last_message_at = (
        select(func.max(Message.created_at))
        .select_from(Message)
        .join(Chat, Chat.id == Message.chat_id)
        .where(Chat.session_id == Session.id)
        .scalar_subquery()
    )
    result = await session.execute(
        select(
            Session.id,
            Session.created_at,
            chats,
            faq_entries,
            last_message_at,
        ).order_by(Session.created_at, Session.id)
    )
    return [SessionSummary(*row) for row in result.all()]


async def delete_session(session: AsyncSession, session_id: str) -> SessionDeletion:
    """Delete one session, and report what went with it.

    Returns: how many chats and FAQ entries the cascade removed. Both are counted
        before the delete, because they go by FK cascade rather than by statements of
        their own - so this is the only moment their number is knowable.

    Deleting an absent session succeeds with zero counts: "already gone" and "was never
    here" are the same end state, and a caller re-running an incomplete deletion does
    not act differently on them.
    """
    chats = await session.execute(
        select(func.count()).select_from(Chat).where(Chat.session_id == session_id)
    )
    entries = await session.execute(
        select(func.count())
        .select_from(FaqEntry)
        .where(FaqEntry.session_id == session_id)
    )
    counts = SessionDeletion(
        chats_deleted=int(chats.scalar_one()),
        faq_entries_deleted=int(entries.scalar_one()),
    )
    await session.execute(sql_delete(Session).where(Session.id == session_id))
    await session.commit()
    return counts
