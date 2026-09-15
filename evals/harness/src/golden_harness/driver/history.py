"""Planting a case's prior conversation into its chat, before the case's turn is posted.

No published surface posts as the assistant - the console writes as staff, which the
classifier reads differently - so history is written straight into the chat database,
as rows the service itself would have written for those turns.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta

from chat.domain.models import Chat, Message, MessageSender
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from golden_harness.cases import HistoryEntry, HistoryRole
from golden_harness.driver.session import ChatNotFoundError

# The gap between two planted rows, and between the last of them and the turn.
_SPACING = timedelta(seconds=1)

_SENDER_BY_ROLE = {
    HistoryRole.USER: MessageSender.PATIENT,
    HistoryRole.ASSISTANT: MessageSender.ASSISTANT,
}


async def plant_history(
    db: AsyncSession,
    session_id: str,
    chat_id: str,
    history: Sequence[HistoryEntry],
    before: datetime,
) -> list[str]:
    """Write one message per history entry into `session_id`'s chat, oldest first.

    Args:
        before: a timezone-aware instant every planted row precedes - the moment just
            before the case's turn is posted, so the turn's own message sorts after
            them in the thread.

    Returns: the planted message ids, oldest first

    Raises: ValueError when `before` is naive; ChatNotFoundError when no chat
        `chat_id` belongs to `session_id`, in which case nothing is written.

    Each row gets a fresh ULID, its role's sender, the text verbatim and strictly
    ascending timestamps one second apart ending one second before `before`; every
    other column is left NULL, as on a row no FAQ half, reply or mark ever touched.
    All rows commit together.
    """
    if before.tzinfo is None or before.utcoffset() is None:
        raise ValueError("before must carry a timezone")

    owned = await db.execute(
        select(Chat.id).where(Chat.id == chat_id, Chat.session_id == session_id)
    )
    if owned.scalar_one_or_none() is None:
        raise ChatNotFoundError(f"no chat {chat_id} in session {session_id}")

    planted: list[str] = []
    for offset, entry in enumerate(history):
        message_id = str(ULID())
        db.add(
            Message(
                id=message_id,
                chat_id=chat_id,
                sender=_SENDER_BY_ROLE[entry.role],
                content=entry.text,
                created_at=before - _SPACING * (len(history) - offset),
            )
        )
        planted.append(message_id)
    await db.commit()
    return planted
