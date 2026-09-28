"""Postgres `BookingAct` repository: the write-ahead record of schedule changes.

An act is written in two steps. `begin` inserts it with no outcome before the request
leaves for the scheduler, and `settle` fills the outcome in once the answer arrives -
at most once, because its `WHERE` carries `outcome IS NULL`. `insert_not_sent` is the
one-step form for an act that is known never to have been sent.

Every statement carries the session predicate. The two inserts take it from the chat
the message belongs to, so a message id from another session selects nothing and
inserts nothing; the settle and the read take it from the act's own `session_id`.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import (
    ColumnElement,
    DateTime,
    String,
    and_,
    distinct,
    func,
    insert,
    literal,
    null,
    or_,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from chat.domain.models import (
    BookingAct,
    BookingActOperation,
    BookingActOutcome,
    Chat,
    Message,
)


async def _insert_on_owned_message(
    session: AsyncSession,
    *,
    session_id: str,
    chat_id: str,
    message_id: str,
    operation: BookingActOperation,
    practitioner_id: str,
    practitioner_full_name: str | None,
    starts_at: datetime,
    appointment_id: str | None,
    previous_practitioner_id: str | None,
    previous_practitioner_full_name: str | None,
    previous_starts_at: datetime | None,
    outcome: BookingActOutcome | None,
) -> str | None:
    """Insert one act on `message_id`, if it is in `chat_id` and `session_id` owns it.

    Returns: the new act's id, or None if the message is not that session's and
        nothing was written.

    An `INSERT ... FROM SELECT` over the message and its chat, for the reason
    `chat_repository._insert_into_owned_chat` gives: the scope belongs to the write
    only if the write reads it. A settled `outcome` is stamped `settled_at` in the same
    statement, which is what the table's check requires.
    """
    act_id = str(ULID())
    source = (
        select(
            literal(act_id, String),
            literal(session_id, String),
            Message.chat_id,
            Message.id,
            literal(operation.value, String),
            literal(outcome.value if outcome is not None else None, String),
            literal(appointment_id, String),
            literal(practitioner_id, String),
            literal(practitioner_full_name, String),
            literal(starts_at, DateTime()),
            literal(previous_practitioner_id, String),
            literal(previous_practitioner_full_name, String),
            literal(previous_starts_at, DateTime()),
            func.now() if outcome is not None else null(),
        )
        .join(Chat, Chat.id == Message.chat_id)
        .where(
            Message.id == message_id,
            Message.chat_id == chat_id,
            Chat.session_id == session_id,
        )
    )
    result = await session.execute(
        insert(BookingAct)
        .from_select(
            [
                "id",
                "session_id",
                "chat_id",
                "message_id",
                "operation",
                "outcome",
                "appointment_id",
                "practitioner_id",
                "practitioner_full_name",
                "starts_at",
                "previous_practitioner_id",
                "previous_practitioner_full_name",
                "previous_starts_at",
                "settled_at",
            ],
            source,
        )
        .returning(BookingAct.id)
    )
    written = result.scalars().first()
    await session.commit()
    return written


async def begin(
    session: AsyncSession,
    *,
    session_id: str,
    chat_id: str,
    message_id: str,
    operation: BookingActOperation,
    practitioner_id: str,
    practitioner_full_name: str | None,
    starts_at: datetime,
    appointment_id: str | None = None,
    previous_practitioner_id: str | None = None,
    previous_practitioner_full_name: str | None = None,
    previous_starts_at: datetime | None = None,
) -> str | None:
    """Record an attempt that is about to be sent, with no outcome yet.

    Args:
        message_id: The patient message the turn is answering.
        practitioner_id: For a reschedule, the practitioner it moves *to*.
        starts_at: For a reschedule, the start it moves *to*.
        previous_practitioner_id: Reschedule only, with the two below: where it moves
            from.

    Returns: the new act's id, or None if `message_id` is not a message of `chat_id`
        in `session_id` and nothing was written.

    Committed before returning, so the row is durable before the caller sends anything.
    """
    return await _insert_on_owned_message(
        session,
        session_id=session_id,
        chat_id=chat_id,
        message_id=message_id,
        operation=operation,
        practitioner_id=practitioner_id,
        practitioner_full_name=practitioner_full_name,
        starts_at=starts_at,
        appointment_id=appointment_id,
        previous_practitioner_id=previous_practitioner_id,
        previous_practitioner_full_name=previous_practitioner_full_name,
        previous_starts_at=previous_starts_at,
        outcome=None,
    )


async def insert_not_sent(
    session: AsyncSession,
    *,
    session_id: str,
    chat_id: str,
    message_id: str,
    operation: BookingActOperation,
    practitioner_id: str,
    practitioner_full_name: str | None,
    starts_at: datetime,
    appointment_id: str | None = None,
    previous_practitioner_id: str | None = None,
    previous_practitioner_full_name: str | None = None,
    previous_starts_at: datetime | None = None,
) -> str | None:
    """Record an attempt that is known never to have been sent, already settled.

    Returns: the new act's id, or None if `message_id` is not a message of `chat_id`
        in `session_id` and nothing was written.

    Takes the same target as `begin`, and writes it with the `not_sent` outcome in one
    statement: there is no answer to wait for.
    """
    return await _insert_on_owned_message(
        session,
        session_id=session_id,
        chat_id=chat_id,
        message_id=message_id,
        operation=operation,
        practitioner_id=practitioner_id,
        practitioner_full_name=practitioner_full_name,
        starts_at=starts_at,
        appointment_id=appointment_id,
        previous_practitioner_id=previous_practitioner_id,
        previous_practitioner_full_name=previous_practitioner_full_name,
        previous_starts_at=previous_starts_at,
        outcome=BookingActOutcome.NOT_SENT,
    )


async def settle(
    session: AsyncSession,
    *,
    act_id: str,
    session_id: str,
    outcome: BookingActOutcome,
    refusal_reason: str | None = None,
    appointment_id: str | None = None,
    practitioner_full_name: str | None = None,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    previous_practitioner_full_name: str | None = None,
    previous_starts_at: datetime | None = None,
) -> bool:
    """Write the outcome of an act that has none yet.

    Args:
        refusal_reason: Set exactly when `outcome` is a refusal.
        appointment_id: Each of this and the five below is what the scheduler's answer
            reported. None means the answer did not report it, and the value the act
            was begun with is kept - never that the stored value is to be cleared.

    Returns: True if this call settled the act, False if nothing changed - it was
        settled already, or it is not an act of `session_id`.

    The guard is part of the write: `outcome IS NULL` makes a second settle a no-op, so
    an act is settled at most once and never rewritten afterwards.
    """
    result = await session.execute(
        update(BookingAct)
        .where(
            BookingAct.id == act_id,
            BookingAct.session_id == session_id,
            BookingAct.outcome.is_(None),
        )
        .values(
            outcome=outcome.value,
            refusal_reason=refusal_reason,
            settled_at=func.now(),
            appointment_id=func.coalesce(appointment_id, BookingAct.appointment_id),
            practitioner_full_name=func.coalesce(
                practitioner_full_name, BookingAct.practitioner_full_name
            ),
            starts_at=func.coalesce(starts_at, BookingAct.starts_at),
            ends_at=func.coalesce(ends_at, BookingAct.ends_at),
            previous_practitioner_full_name=func.coalesce(
                previous_practitioner_full_name,
                BookingAct.previous_practitioner_full_name,
            ),
            previous_starts_at=func.coalesce(
                previous_starts_at, BookingAct.previous_starts_at
            ),
        )
        .returning(BookingAct.id)
    )
    settled = result.scalars().first() is not None
    await session.commit()
    return settled


async def list_for_chat(
    session: AsyncSession, chat_id: str, session_id: str
) -> list[BookingAct]:
    """Return `chat_id`'s acts in the order they were attempted.

    Ordered by `seq`, which the database assigns at insert, never by a clock. Empty
    both for a chat with no acts and for a chat that is not `session_id`'s - a caller
    resolves the chat first, as every other thread read does.
    """
    result = await session.execute(
        select(BookingAct)
        .where(BookingAct.chat_id == chat_id, BookingAct.session_id == session_id)
        .order_by(BookingAct.seq.asc())
    )
    return list(result.scalars().all())


@dataclass(frozen=True)
class RecentBookingChanges:
    """What the assistant changed on the schedule in the last `window_minutes`.

    `booked`, `cancelled` and `rescheduled` count distinct appointments with an act of
    that operation that settled done inside the window: one appointment rescheduled
    twice counts once, and one booked then cancelled counts once under each.
    `outcome_unknown` counts acts, not appointments - an unknown booking may name none -
    whose outcome is not known: settled unknown inside the window, or never settled and
    written inside it. Attempts that changed nothing are counted nowhere.
    """

    window_minutes: int
    booked: int
    cancelled: int
    rescheduled: int
    outcome_unknown: int


async def count_recent_changes(
    session: AsyncSession, session_id: str, minutes: int
) -> RecentBookingChanges:
    """Count `session_id`'s schedule changes in the last `minutes` (database clock).

    One statement. An act that never settled has no settle time, so its creation time
    is the only moment it has and is what places it in the window.
    """
    since = func.now() - timedelta(minutes=minutes)
    settled_in_window = BookingAct.settled_at > since
    done = and_(BookingAct.outcome == BookingActOutcome.DONE.value, settled_in_window)

    def done_appointments(operation: BookingActOperation) -> ColumnElement[int]:
        return func.count(distinct(BookingAct.appointment_id)).filter(
            done, BookingAct.operation == operation.value
        )

    unknown = func.count().filter(
        or_(
            and_(
                BookingAct.outcome == BookingActOutcome.UNKNOWN.value,
                settled_in_window,
            ),
            and_(BookingAct.outcome.is_(None), BookingAct.created_at > since),
        )
    )
    result = await session.execute(
        select(
            done_appointments(BookingActOperation.BOOK),
            done_appointments(BookingActOperation.CANCEL),
            done_appointments(BookingActOperation.RESCHEDULE),
            unknown,
        ).where(
            BookingAct.session_id == session_id,
            or_(settled_in_window, BookingAct.created_at > since),
        )
    )
    booked, cancelled, rescheduled, outcome_unknown = result.one()
    return RecentBookingChanges(
        window_minutes=minutes,
        booked=booked,
        cancelled=cancelled,
        rescheduled=rescheduled,
        outcome_unknown=outcome_unknown,
    )
