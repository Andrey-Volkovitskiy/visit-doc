"""Postgres `Session`/`Chat`/`Message` repository (async session)."""

from datetime import datetime

from sqlalchemy import func, nullslast, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import PureRandomPolicy, ULIDGenerator

from chat.core.logging import get_logger
from chat.domain.models import Chat, Message, MessageSender, Session

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


async def lock_chat(session: AsyncSession, chat_id: str) -> None:
    """Take a connection-scoped Postgres advisory lock keyed by `chat_id`.

    Blocks until any other connection holding the same lock releases it. Keyed by chat
    rather than by session so two chats in one browser never serialize against each
    other - what has to be serialized is one chat's history read and message insert,
    since a concurrent sibling message could otherwise be missed by a history read
    whose insert has not committed yet.

    Connection-scoped, not transaction-scoped - stays held across this connection's own
    `commit()` calls until explicitly released. Must be released via `unlock_chat`,
    always in a `finally`, before the caller's `AsyncSession` closes and its connection
    returns to the pool.
    """
    await session.execute(
        text("SELECT pg_advisory_lock(hashtext(:chat_id)::bigint)"),
        {"chat_id": chat_id},
    )


async def unlock_chat(session: AsyncSession, chat_id: str) -> None:
    """Release the advisory lock `lock_chat` took for `chat_id`."""
    await session.execute(
        text("SELECT pg_advisory_unlock(hashtext(:chat_id)::bigint)"),
        {"chat_id": chat_id},
    )


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
