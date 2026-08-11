"""SQLAlchemy 2.0 declarative models."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

_ULID_LENGTH = 26


class MessageSender(StrEnum):
    """Legal values for `Message.sender` - a Python-level closed set.

    Deliberately *not* a database-level enum (`Message.sender` stays a plain
    `String` column, research.md, FR-013): the DB schema stays open so a future
    `staff` value (ROADMAP Phase 1d) never needs a migration. This enum exists purely
    to stop application code from passing an arbitrary/misspelled string where the
    repository or agent layer expects a sender - callers should always pass a
    `MessageSender` member, never a bare string literal (docs/python-style-guide.md).
    """

    PATIENT = "patient"
    ASSISTANT = "assistant"


class Base(DeclarativeBase):
    """Declarative base for all SQLAlchemy models."""


class FaqEntry(Base):
    """A unit of clinic knowledge that can be retrieved to ground an answer.

    No `title` field — citations reference the retrieved passage itself (data-model.md).
    """

    __tablename__ = "faq_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Session(Base):
    """An anonymous visitor's identity, scoped to one browser (data-model.md).

    `id` is never server-generated here — `chat_repository.create_session` mints it
    explicitly via a `PureRandomPolicy` ULID generator (FR-017, research.md #1).
    """

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(_ULID_LENGTH), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Chat(Base):
    """One continuous chat thread between a `Session` and the assistant (data-model.md).

    No uniqueness constraint on `session_id`: exactly-one-active-chat-per-session
    (FR-009) is an application-level rule, not a schema one, so a later Patient layer
    can allow multiple chats per session without dropping a constraint (research.md #1).
    """

    __tablename__ = "chats"

    id: Mapped[str] = mapped_column(String(_ULID_LENGTH), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(_ULID_LENGTH),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Message(Base):
    """A single message belonging to a `Chat`, authored by one sender (data-model.md).

    `sender` is stored as a plain string column, not a database-level enum, so a third
    value (`staff`, ROADMAP Phase 1d) can be added later with no schema migration
    (FR-013) - callers should still only ever write it via a `MessageSender` member
    (above), never a bare string literal. `id` is always caller-supplied (never
    server-generated): a patient message reuses the request's `turn_id`, an assistant
    message gets a fresh ULID (research.md #4).
    """

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(_ULID_LENGTH), primary_key=True)
    chat_id: Mapped[str] = mapped_column(
        String(_ULID_LENGTH), ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    sender: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    grounded: Mapped[bool | None] = mapped_column(nullable=True)
    citations: Mapped[list[dict[str, object]] | None] = mapped_column(
        JSONB, nullable=True
    )
    # Only ever set on an assistant message: every patient message id it answers, in
    # order - a burst of several unanswered patient messages merged into one Claude
    # turn (FR-014, history.py's derive_reply_to_message_ids) is answered by exactly
    # one assistant message, so a single scalar FK can't represent it; this ties a reply
    # to its turn(s) explicitly, so history-building never has to infer pairing from
    # row order (which a stray/delayed write can violate). Plain JSONB, like
    # `citations` above - not a FK, so no per-element referential integrity, but this
    # is diagnostic-only data (never joined on in SQL) and a chat's messages are
    # always deleted together anyway (chat_id's own CASCADE).
    reply_to_message_ids: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
