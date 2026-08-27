"""SQLAlchemy 2.0 declarative models."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

_ULID_LENGTH = 26
PATIENT_NAME_LENGTH = 200


class MessageSender(StrEnum):
    """Legal values for `Message.sender` - a Python-level closed set.

    Deliberately *not* a database-level enum: `Message.sender` stays a plain `String`
    column, so a future value can be added without a migration. This enum exists
    purely to stop application code from passing an arbitrary/misspelled string where
    the repository or agent layer expects a sender - callers should always pass a
    `MessageSender` member, never a bare string literal.
    """

    PATIENT = "patient"
    ASSISTANT = "assistant"


class Base(DeclarativeBase):
    """Declarative base for all SQLAlchemy models."""


def all_table_names() -> tuple[str, ...]:
    """Return every table this service owns, in declaration order.

    Read from the metadata rather than listed by hand, so a suite truncating "every
    table" between tests keeps doing that after a table is added - a hand-written list
    silently leaves the new one's rows to leak into the next test.
    """
    return tuple(table.name for table in Base.metadata.sorted_tables)


class FaqEntry(Base):
    """A unit of clinic knowledge that can be retrieved to ground an answer.

    No `title` field — citations reference the retrieved passage itself.
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
    """An anonymous visitor's identity, scoped to one browser.

    `id` is never server-generated - it's minted explicitly by the repository layer
    before insert, not the database.
    """

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(_ULID_LENGTH), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Chat(Base):
    """One continuous chat thread between a `Session` and the assistant.

    A session may hold any number of chats, including none.

    `patient_id` references the scheduler service's own database and is deliberately
    not a foreign key: the two services own separate databases, so this side holds an
    opaque id and never a `Patient` row. It is NULL until that patient exists, which is
    the normal state of a chat created while scheduling was unreachable.

    `patient_name` is a cached display value this service never authors - it is written
    from whatever the scheduler reported, so the chat list has something to render
    without a per-render call. Renaming through this service updates both stores in the
    one request, which is what keeps the copy true; renaming through the scheduler's own
    admin API instead writes only its side and leaves this copy stale, since nothing
    here re-reads a name it already has.
    """

    __tablename__ = "chats"

    id: Mapped[str] = mapped_column(String(_ULID_LENGTH), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(_ULID_LENGTH),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    patient_id: Mapped[str | None] = mapped_column(
        String(_ULID_LENGTH), nullable=True, index=True
    )
    patient_name: Mapped[str | None] = mapped_column(
        String(PATIENT_NAME_LENGTH), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Message(Base):
    """A single message belonging to a `Chat`, authored by one sender.

    `sender` is stored as a plain string column, not a database-level enum, so a
    future value can be added with no schema migration - callers should still only
    ever write it via a `MessageSender` member (above), never a bare string literal.
    `id` is always caller-supplied, never server-generated - a patient message reuses
    the request's turn id, an assistant message gets a fresh ULID.
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
