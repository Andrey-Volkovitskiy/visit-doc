"""Postgres `Session`/`Chat`/`Message` repository (async session)."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import (
    Connection,
    Integer,
    and_,
    case,
    func,
    nullslast,
    or_,
    select,
    text,
    update,
)
from sqlalchemy import delete as sql_delete
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
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
    session: AsyncSession, chat_id: str, patient_id: str, patient_name: str
) -> None:
    """Record the scheduler-side patient this chat books on behalf of.

    `patient_name` is cached, never authored here - it is whatever the scheduler
    reported, kept so the chat list can render without a call per row.
    """
    await session.execute(
        update(Chat)
        .where(Chat.id == chat_id)
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
        that lock - the lock is then still held by whichever connection took it, and
        nothing on this path can reach it any more.

    `pg_advisory_unlock` answers false rather than raising when it releases nothing,
    which reads exactly like a release that worked. Turning that into an error is what
    keeps a stranded lock from being indistinguishable from a successful unlock: the
    chat it keys is now permanently unlockable, and every later turn or staff action in
    it would wait on the lock forever.
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

    A lock that could not be released is logged before it is re-raised, because this
    runs in a `finally`: the exception it raises may be the one the caller sees, or it
    may be replacing one already in flight, and a stranded lock is too damaging to
    depend on which of those happens.
    """
    try:
        try:
            await unlock_chat(session, chat_id)
        except SQLAlchemyError:
            await session.rollback()
            await unlock_chat(session, chat_id)
    except ChatLockNotHeldError:
        get_logger().critical("chat.lock_stranded", chat_id=chat_id)
        raise


async def create_message(
    session: AsyncSession,
    *,
    id: str,
    chat_id: str,
    sender: MessageSender,
    content: str,
    grounded: bool | None = None,
    citations: list[dict[str, object]] | None = None,
    reply_to_message_ids: list[str] | None = None,
) -> Message:
    """Insert a new `Message` and return it.

    Args:
        id: Caller-supplied, never generated here - a patient message reuses the
            request's turn id; an assistant message gets a fresh ULID.
        reply_to_message_ids: For an assistant message, every patient message id it
            answers, in order (more than one for a merged burst). Not meaningful for a
            patient message.

    Append-only: a `Message` is written once, in full, with no "complete"/pending step.
    """
    message = Message(
        id=id,
        chat_id=chat_id,
        sender=sender,
        content=content,
        grounded=grounded,
        citations=citations,
        reply_to_message_ids=reply_to_message_ids,
    )
    session.add(message)
    await session.commit()
    await session.refresh(message)
    get_logger().info(
        "message.persisted",
        message_id=message.id,
        chat_id=chat_id,
        sender=sender,
        content=content,
    )
    return message


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


async def delete_chat(session: AsyncSession, chat_id: str) -> None:
    """Hard-delete `chat_id`.

    Its `Message` rows cascade via FK, not an application-level loop.
    """
    chat = await session.get(Chat, chat_id)
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


async def get_conversation_state(
    session: AsyncSession, chat_id: str, session_id: str
) -> ConversationState | None:
    """Return `chat_id`'s conversation state, or None if it is not this session's."""
    result = await session.execute(
        select(
            Chat.escalated_at,
            Chat.escalation_reason,
            Chat.assistant_paused_until,
            Chat.attention_since,
            _MAY_ASSISTANT_REPLY,
            _PAUSE_SECONDS_REMAINING,
        ).where(Chat.id == chat_id, Chat.session_id == session_id)
    )
    row = result.first()
    if row is None:
        return None
    return ConversationState(*row)


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


async def set_paused_until(
    session: AsyncSession, chat_id: str, session_id: str, seconds: int
) -> None:
    """Silence the assistant in `chat_id` for `seconds` from now.

    Restarts a pause that was already running, so a staff member sending a sequence of
    messages never has the assistant cut in between them.
    """
    await session.execute(
        update(Chat)
        .where(Chat.id == chat_id, Chat.session_id == session_id)
        .values(
            assistant_paused_until=func.now()
            + func.make_interval(0, 0, 0, 0, 0, 0, seconds)
        )
    )
    await session.commit()


async def clear_pause(session: AsyncSession, chat_id: str, session_id: str) -> None:
    """Let the assistant speak in `chat_id` again."""
    await session.execute(
        update(Chat)
        .where(Chat.id == chat_id, Chat.session_id == session_id)
        .values(assistant_paused_until=None)
    )
    await session.commit()


async def mark_attention(session: AsyncSession, chat_id: str, session_id: str) -> None:
    """Record that `chat_id` needs a person, if it is not waiting already.

    Left alone when already set: the conversation has been waiting since the first
    thing that needed a person, and re-stamping it would send it to the back of a queue
    ordered by how long each has waited.
    """
    await session.execute(
        update(Chat)
        .where(
            Chat.id == chat_id,
            Chat.session_id == session_id,
            Chat.attention_since.is_(None),
        )
        .values(attention_since=func.now())
    )
    await session.commit()


async def clear_attention(session: AsyncSession, chat_id: str, session_id: str) -> None:
    """Record that a person has spoken in `chat_id`."""
    await session.execute(
        update(Chat)
        .where(Chat.id == chat_id, Chat.session_id == session_id)
        .values(attention_since=None)
    )
    await session.commit()


async def set_attention_mark(
    session: AsyncSession, message_id: str, mark: AttentionMark
) -> None:
    """Mark `message_id` as needing a person, for `mark`'s reason."""
    await session.execute(
        update(Message)
        .where(Message.id == message_id)
        .values(attention_mark=mark.value)
    )
    await session.commit()


async def clear_clearable_marks(session: AsyncSession, chat_id: str) -> int:
    """Clear every mark in `chat_id` that a person speaking answers.

    Returns: how many marks were cleared.

    One statement, however many marks accumulated. The permanent kinds are absent from
    its predicate rather than skipped afterwards: a staff member answering the patient
    does not mean the corpus gained the entry it was missing, or that the failure did
    not happen.
    """
    result = await session.execute(
        update(Message)
        .where(
            Message.chat_id == chat_id,
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

    The one query in this module with no session predicate, because the session *is*
    what it enumerates - and it is reachable only from the admin surface.
    """
    result = await session.execute(select(Session.id).order_by(Session.created_at))
    return list(result.scalars().all())


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
