"""SQLAlchemy 2.0 declarative models."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
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
    STAFF = "staff"


class EscalationReason(StrEnum):
    """Why staff were called - a Python-level closed set of exactly three values.

    Whether a reason silences the assistant is decided by the code that applies the
    escalation, not by membership here: only `PATIENT_ASKED_FOR_PERSON` silences the
    conversation; `CORPUS_COULD_NOT_ANSWER` and `ASSISTANT_FAILED` do not.
    """

    # The three values are the three triggers, so no separate taxonomy exists and no
    # fourth value is legal. A failure is owed a retry - the thing that broke may
    # already be working - and a corpus gap is owed a person for *that question* while
    # the assistant goes on answering the rest, where a patient asking for a human is
    # owed a person and nothing else, which is why only the first one silences (spec
    # 007 FR-003d).

    PATIENT_ASKED_FOR_PERSON = "patient_asked_for_person"
    CORPUS_COULD_NOT_ANSWER = "corpus_could_not_answer"
    ASSISTANT_FAILED = "assistant_failed"


class AttentionMark(StrEnum):
    """Why one patient message needs a person - a Python-level closed set.

    The kind is the whole of the mark: whether it ever clears is read from
    `CLEARABLE_MARKS` below rather than stored beside it, so the two cannot disagree.
    """

    # The first three carry the same values as `EscalationReason` and are set by the
    # same act that calls staff, so there is no call without a mark on the message that
    # caused it, and no mark of those kinds without a call behind it. `UNANSWERED` is
    # not a call: it records a consequence of silence - a message that arrived while
    # the assistant could not reply, which nothing has answered (spec 007 FR-027a/b).

    PATIENT_ASKED_FOR_PERSON = "patient_asked_for_person"
    CORPUS_COULD_NOT_ANSWER = "corpus_could_not_answer"
    ASSISTANT_FAILED = "assistant_failed"
    UNANSWERED = "unanswered"


# The marks a staff message clears, and therefore the `IN` list of the one statement
# that clears them. The other two are permanent: a staff member answering the patient
# does not mean the corpus gained the entry it was missing, or that the failure did not
# happen (spec 007 FR-027c).
#
# This is deliberately a constant rather than a method on the enum or a column on the
# row. Lifetime is a property of the *kind*, and a stored "cleared by" field would be a
# second source for a fact the kind already determines - the duplication the spec's
# withdrawn readiness flag was rejected for.
CLEARABLE_MARKS = frozenset(
    {AttentionMark.PATIENT_ASKED_FOR_PERSON, AttentionMark.UNANSWERED}
)


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

    Both `session_id` and `live_revision` are non-nullable, which is what makes an entry
    that no session owns, or that names no searchable revision, unrepresentable rather
    than merely filtered out on the read.
    """

    __tablename__ = "faq_entries"
    __table_args__ = (Index("ix_faq_entries_session", "session_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # Allocated from the sequence before the chunks are written, so a create's chunks
    # can carry the entry they belong to before the row that publishes them exists.
    session_id: Mapped[str] = mapped_column(
        String(_ULID_LENGTH),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # The one revision of this entry's indexed chunks retrieval may search. Every other
    # revision - superseded by a later save, or written by one that never published -
    # is unreachable and awaits a sweep.
    live_revision: Mapped[str] = mapped_column(String(_ULID_LENGTH), nullable=False)
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
    __table_args__ = (
        # An escalation always carries exactly one reason, and a reason never outlives
        # the escalation that set it. Enforced here so neither half can be set alone.
        CheckConstraint(
            "(escalated_at IS NULL) = (escalation_reason IS NULL)",
            name="ck_chats_escalation_reason_with_escalated_at",
        ),
        Index("ix_chats_session_attention", "session_id", "attention_since"),
    )

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
    # Non-NULL means the assistant is silenced here, with no deadline: it was asked to
    # fetch a person and none has dealt with it. Cleared by the first staff message, or
    # by the console's switch.
    escalated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    escalation_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # A stored deadline, not a running timer - it has to survive a reload, a second tab
    # and a restart, and two tabs have to count down together. Compared against the
    # database's own clock rather than any client's.
    assistant_paused_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Non-NULL means a person is needed here and none has spoken since. Deliberately a
    # separate fact from `escalated_at`: the two are cleared by different things and
    # disagree in both directions - a failure sets this without silencing, and the
    # switch clears the silence without clearing this.
    attention_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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
    __table_args__ = (
        # Partial: the statement clearing a chat's marks and the read asking whether it
        # holds one both address only marked rows, a small minority of a chat's
        # messages.
        Index(
            "ix_messages_chat_attention_mark",
            "chat_id",
            "attention_mark",
            postgresql_where=text("attention_mark IS NOT NULL"),
        ),
    )

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
    # Only ever set on a patient message: which of `AttentionMark`'s four kinds this one
    # is, or NULL for no mark. Plain string, like `sender`, so a future kind needs no
    # migration - and callers pass an `AttentionMark` member, never a bare literal.
    # Whether a mark ever clears is read from `CLEARABLE_MARKS`, so there is no second
    # field here that could disagree with the kind.
    attention_mark: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
