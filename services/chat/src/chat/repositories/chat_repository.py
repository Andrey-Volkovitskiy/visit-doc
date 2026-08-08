"""Postgres `Session`/`Chat`/`Message` repository (async session, data-model.md)."""

from sqlalchemy import select, text
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
    """Create and persist a new `Session` with a non-guessable id (FR-017)."""
    new_session = Session(id=str(_id_generator.generate()))
    session.add(new_session)
    await session.commit()
    await session.refresh(new_session)
    return new_session


async def get_session(session: AsyncSession, session_id: str) -> Session | None:
    """Return the `Session` with `session_id`, or None if it doesn't exist."""
    return await session.get(Session, session_id)


async def get_chat_for_session(session: AsyncSession, session_id: str) -> Chat | None:
    """Return `session_id`'s current `Chat`, or None if it has none yet.

    Read-only - never creates one (used by `GET /chat`, FR-010).
    """
    result = await session.execute(
        select(Chat).where(Chat.session_id == session_id).order_by(Chat.id.desc())
    )
    return result.scalars().first()


async def lock_session(session: AsyncSession, session_id: str) -> None:
    """Take a session-scoped Postgres advisory lock keyed by `session_id`.

    Blocks until any other connection holding the same lock releases it. Serializes
    concurrent requests for one session through `_event_stream`'s chat-creation +
    history-read + message-insert critical section (FR-009/FR-012), closing the races
    that let two concurrent first messages create two `Chat` rows, or let a second
    concurrent message's history read miss a sibling message that hadn't committed
    yet. Deliberately session-scoped, not transaction-scoped (`pg_advisory_xact_lock`
    would do) - that critical section spans several of this module's own internal
    `session.commit()` calls (`get_or_create_chat_for_session`, `create_message`),
    each of which would release a transaction-scoped lock early. Must be released
    explicitly via `unlock_session`, always in a `finally`, before the caller's
    `AsyncSession` closes and its connection returns to the pool.
    """
    await session.execute(
        text("SELECT pg_advisory_lock(hashtext(:session_id)::bigint)"),
        {"session_id": session_id},
    )


async def unlock_session(session: AsyncSession, session_id: str) -> None:
    """Release the advisory lock `lock_session` took for `session_id`."""
    await session.execute(
        text("SELECT pg_advisory_unlock(hashtext(:session_id)::bigint)"),
        {"session_id": session_id},
    )


async def get_or_create_chat_for_session(
    session: AsyncSession, session_id: str
) -> Chat:
    """Return `session_id`'s current `Chat`, creating one if none exists (FR-009).

    Exactly one active `Chat` per `Session` is enforced here, in application logic -
    not by a uniqueness constraint (data-model.md, research.md #1).
    """
    existing = await get_chat_for_session(session, session_id)
    if existing is not None:
        return existing

    chat = Chat(id=str(_id_generator.generate()), session_id=session_id)
    session.add(chat)
    await session.commit()
    await session.refresh(chat)
    return chat


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

    `id` is always caller-supplied, never generated here - a patient message reuses
    the request's `turn_id`, an assistant message gets a fresh ULID (research.md #4).
    Append-only: a `Message` is written once, in full, with no "complete"/pending
    step (research.md #3). `reply_to_message_ids` is only meaningful for an assistant
    message - every patient message id it answers, in order (possibly more than one,
    for a merged burst) - so history-building can pair a turn's messages explicitly
    instead of inferring pairing from row order.
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
    """Return `chat_id`'s messages in chronological order (FR-002).

    Ordered by `created_at`, not `id` - ULIDs are only monotonic within the
    generating process's clock/randomness and aren't guaranteed to sort in true
    creation order across concurrent writers (data-model.md).
    """
    result = await session.execute(
        select(Message)
        .where(Message.chat_id == chat_id)
        .order_by(Message.created_at.asc())
    )
    return list(result.scalars().all())


async def delete_chat(session: AsyncSession, chat_id: str) -> None:
    """Hard-delete `chat_id` (FR-005).

    Its `Message` rows cascade via FK, not an application-level loop (research.md #7).
    """
    chat = await session.get(Chat, chat_id)
    if chat is None:
        return
    await session.delete(chat)
    await session.commit()
