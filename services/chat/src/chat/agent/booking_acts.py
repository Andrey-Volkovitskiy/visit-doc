"""What the assistant did to the schedule, as a port the tool registry records through.

Every attempt to book, reschedule or cancel is a **booking act**, written before the
request leaves for the scheduler and settled once its answer arrives. The tool registry
does both - a write tool declares what it is about to do (`Tool.plan_act`), and the
registry records it around the handler - so no handler records anything and a new write
tool cannot forget to.

The registry reaches storage only through `BookingActRecorder`, so it depends on this
abstraction rather than on SQLAlchemy. The port is write-only: it has no method that
reads an act back, so nothing the agent holds can put its own record into a prompt, a
history or a tool result. The record is for staff.

Practitioner names come from the turn's roster read, which the booking node hands
over with `learn_practitioners`, and are overwritten on settle by any name the
scheduler's own answer carried. A name nobody reported stays None rather than
becoming a guess.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from shared_models.localtime import parse_local_datetime
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chat.domain.models import BookingActOperation, BookingActOutcome
from chat.repositories import booking_act_repository


class BookingActNotRecordedError(Exception):
    """Raised when an act could not be written because its message is not this turn's.

    The store answered, and the answer was that the patient message the act would hang
    off is not in this chat of this session - deleted with its chat mid-turn, say. A
    store that did not answer at all raises its own error instead.
    """


@dataclass(frozen=True)
class PlannedAct:
    """What one write is about to attempt, read from its arguments before it is sent.

    For a reschedule, `practitioner_id` and `starts_at` are where it moves *to*, and the
    three `previous_*` fields where it moves from. They are None for every other
    operation.
    """

    operation: BookingActOperation
    practitioner_id: str
    practitioner_full_name: str | None
    starts_at: datetime
    appointment_id: str | None = None
    previous_practitioner_id: str | None = None
    previous_practitioner_full_name: str | None = None
    previous_starts_at: datetime | None = None

    @classmethod
    def book(
        cls,
        *,
        practitioner_id: str,
        starts_at: datetime,
        name_of: Callable[[str], str | None],
    ) -> "PlannedAct":
        """Plan a new booking with `practitioner_id` at `starts_at`.

        The appointment's id is not known until the scheduler has made it.
        """
        return cls(
            operation=BookingActOperation.BOOK,
            practitioner_id=practitioner_id,
            practitioner_full_name=name_of(practitioner_id),
            starts_at=starts_at,
        )

    @classmethod
    def reschedule(
        cls,
        *,
        appointment_id: str,
        new_starts_at: datetime,
        new_practitioner_id: str | None,
        expected_starts_at: datetime,
        expected_practitioner_id: str,
        name_of: Callable[[str], str | None],
    ) -> "PlannedAct":
        """Plan moving `appointment_id` from where it was described to its new time.

        Args:
            new_practitioner_id: None when the appointment keeps its practitioner, who
                is then the one it was described with.
            expected_starts_at: The start the conversation described - the guard the
                request is sent with, and so where the act moves it from.
        """
        practitioner_id = new_practitioner_id or expected_practitioner_id
        return cls(
            operation=BookingActOperation.RESCHEDULE,
            practitioner_id=practitioner_id,
            practitioner_full_name=name_of(practitioner_id),
            starts_at=new_starts_at,
            appointment_id=appointment_id,
            previous_practitioner_id=expected_practitioner_id,
            previous_practitioner_full_name=name_of(expected_practitioner_id),
            previous_starts_at=expected_starts_at,
        )

    @classmethod
    def cancel(
        cls,
        *,
        appointment_id: str,
        expected_starts_at: datetime,
        expected_practitioner_id: str,
        name_of: Callable[[str], str | None],
    ) -> "PlannedAct":
        """Plan cancelling `appointment_id`, as the conversation described it."""
        return cls(
            operation=BookingActOperation.CANCEL,
            practitioner_id=expected_practitioner_id,
            practitioner_full_name=name_of(expected_practitioner_id),
            starts_at=expected_starts_at,
            appointment_id=appointment_id,
        )


@dataclass(frozen=True)
class Settlement:
    """What a write's answer established: its outcome, and what the answer reported.

    Every field after `refusal_reason` is None when the answer did not report it, and a
    settle then keeps the value the act was begun with. None here never means "clear
    it".
    """

    outcome: BookingActOutcome
    refusal_reason: str | None = None
    appointment_id: str | None = None
    practitioner_full_name: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    previous_practitioner_full_name: str | None = None
    previous_starts_at: datetime | None = None


@dataclass(frozen=True)
class ActHandle:
    """The act `begin` wrote, for the settle that follows it."""

    act_id: str


# What each write-tool status establishes. A status outside this table is one this build
# cannot name, and says nothing about whether the schedule changed.
_OUTCOME_BY_STATUS = {
    "booked": BookingActOutcome.DONE,
    "changed": BookingActOutcome.DONE,
    "unchanged": BookingActOutcome.UNCHANGED,
    "refused": BookingActOutcome.REFUSED,
    "unavailable": BookingActOutcome.NOT_SENT,
    "unknown": BookingActOutcome.UNKNOWN,
}


def _text(value: object) -> str | None:
    """Return `value` if it is a non-empty string, else None."""
    return value if isinstance(value, str) and value else None


def _local_datetime(value: object) -> datetime | None:
    """Return `value` as an offset-free local date-time, or None if it is not one."""
    if not isinstance(value, str):
        return None
    try:
        return parse_local_datetime(value)
    except ValueError:
        return None


def settlement_from(
    operation: BookingActOperation, result: dict[str, Any]
) -> Settlement:
    """Derive what a write tool's result settles its act as.

    Args:
        result: The handler's result, exactly as the model is about to read it.

    `booked` and `changed` are done, `unchanged` is unchanged, `refused` is refused with
    its reason, `unavailable` - which a write reports only when it is known nothing
    changed - is not sent, and `unknown` or any status this build cannot name is
    unknown. A refusal naming no reason is unknown too: the record never carries a
    refusal it cannot explain, and unknown claims nothing.

    Names and times are taken from the appointment a `done` or `unchanged` answer
    carries, because the scheduler's are authoritative at the moment of the act. Only a
    reschedule takes where it moved from: a cancellation's answer reports the start it
    had, but it moved nowhere. A value the answer carries unreadably is left unreported
    rather than guessed at.
    """
    outcome = _OUTCOME_BY_STATUS.get(str(result.get("status")))
    if outcome is None:
        return Settlement(outcome=BookingActOutcome.UNKNOWN)
    if outcome is BookingActOutcome.REFUSED:
        reason = _text(result.get("reason"))
        if reason is None:
            return Settlement(outcome=BookingActOutcome.UNKNOWN)
        return Settlement(outcome=outcome, refusal_reason=reason)
    if outcome not in (BookingActOutcome.DONE, BookingActOutcome.UNCHANGED):
        return Settlement(outcome=outcome)

    appointment = result.get("appointment")
    if not isinstance(appointment, dict):
        appointment = {}
    moved = (
        operation is BookingActOperation.RESCHEDULE
        and outcome is BookingActOutcome.DONE
    )
    return Settlement(
        outcome=outcome,
        appointment_id=_text(appointment.get("id")),
        practitioner_full_name=_text(appointment.get("practitioner_full_name")),
        starts_at=_local_datetime(appointment.get("starts_at")),
        ends_at=_local_datetime(appointment.get("ends_at")),
        previous_practitioner_full_name=(
            _text(result.get("previous_practitioner_full_name")) if moved else None
        ),
        previous_starts_at=(
            _local_datetime(result.get("previous_starts_at")) if moved else None
        ),
    )


class BookingActRecorder(Protocol):
    """Where the registry records booking acts. Write-only by design.

    `begin`, `record_not_sent` and `settle` raise when the write fails; what a failure
    means for the tool call is the registry's decision, not the recorder's.
    """

    def learn_practitioners(self, roster: Sequence[object]) -> None:
        """Remember each roster entry's `id -> full_name`, for `name_of`."""
        ...

    def name_of(self, practitioner_id: str) -> str | None:
        """Return the name the turn's roster gave `practitioner_id`, or None."""
        ...

    async def begin(self, planned: PlannedAct) -> ActHandle:
        """Record an act that is about to be sent, with no outcome yet."""
        ...

    async def record_not_sent(self, planned: PlannedAct) -> None:
        """Record an act that is known never to have been sent, already settled."""
        ...

    async def settle(self, handle: ActHandle, settlement: Settlement) -> None:
        """Record what came of an act `begin` wrote, unless it is settled already."""
        ...


class _RosterNames:
    """The `learn_practitioners`/`name_of` half every recorder shares."""

    def __init__(self) -> None:
        """Start knowing no names."""
        self._names: dict[str, str] = {}

    def learn_practitioners(self, roster: Sequence[object]) -> None:
        """Remember each roster entry's `id -> full_name`, for `name_of`.

        The roster is whatever the capability reported, so an entry that is not a
        mapping, or lacks either half, is skipped rather than guessed at.
        """
        for entry in roster:
            if not isinstance(entry, dict):
                continue
            practitioner_id = _text(entry.get("id"))
            full_name = _text(entry.get("full_name"))
            if practitioner_id is None or full_name is None:
                continue
            self._names[practitioner_id] = full_name

    def name_of(self, practitioner_id: str) -> str | None:
        """Return the name the turn's roster gave `practitioner_id`, or None."""
        return self._names.get(practitioner_id)


class DiscardingBookingActRecorder(_RosterNames):
    """A recorder that keeps nothing: the default for a context built without a turn.

    It still learns names, because naming is not recording and a plan made through it
    should read the same as one made through the real recorder.
    """

    async def begin(self, planned: PlannedAct) -> ActHandle:
        """Accept the act and keep nothing.

        The handle names no act, because none was kept; only this recorder's own
        `settle`, which keeps nothing either, is ever handed it.
        """
        return ActHandle(act_id="")

    async def record_not_sent(self, planned: PlannedAct) -> None:
        """Accept the act and keep nothing."""

    async def settle(self, handle: ActHandle, settlement: Settlement) -> None:
        """Accept the settlement and keep nothing."""


class DatabaseBookingActRecorder(_RosterNames):
    """The recorder a real turn writes through, bound to the message it is answering.

    Each call is its own short transaction on its own session, committed before it
    returns - so an act `begin` wrote is durable before the request it describes
    leaves. It never takes the chat's advisory lock: a staff post may hold that lock
    while it cancels this very turn, and a recorder waiting on it would wait forever.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        session_id: str,
        chat_id: str,
        message_id: str,
    ) -> None:
        """Bind the recorder to one turn's patient message.

        Args:
            message_id: The patient message the turn is answering, which every act of
                the turn hangs off.
        """
        super().__init__()
        self._session_factory = session_factory
        self._session_id = session_id
        self._chat_id = chat_id
        self._message_id = message_id

    async def begin(self, planned: PlannedAct) -> ActHandle:
        """Write the act with no outcome, and commit it.

        Raises: BookingActNotRecordedError if the message is not this chat's in this
            session, and whatever the store raised if it could not be reached.
        """
        return ActHandle(
            act_id=await self._insert(booking_act_repository.begin, planned)
        )

    async def record_not_sent(self, planned: PlannedAct) -> None:
        """Write the act already settled as not sent, and commit it.

        Raises: BookingActNotRecordedError if the message is not this chat's in this
            session, and whatever the store raised if it could not be reached.
        """
        await self._insert(booking_act_repository.insert_not_sent, planned)

    async def settle(self, handle: ActHandle, settlement: Settlement) -> None:
        """Write the act's outcome, and commit it.

        An act settled already is left as it is: the write's own guard makes a second
        settle a no-op, and that is not a failure.
        """
        async with self._session_factory() as session:
            await booking_act_repository.settle(
                session,
                act_id=handle.act_id,
                session_id=self._session_id,
                outcome=settlement.outcome,
                refusal_reason=settlement.refusal_reason,
                appointment_id=settlement.appointment_id,
                practitioner_full_name=settlement.practitioner_full_name,
                starts_at=settlement.starts_at,
                ends_at=settlement.ends_at,
                previous_practitioner_full_name=settlement.previous_practitioner_full_name,
                previous_starts_at=settlement.previous_starts_at,
            )

    async def _insert(
        self,
        write: Callable[..., Awaitable[str | None]],
        planned: PlannedAct,
    ) -> str:
        """Insert `planned` through `write` on this recorder's message.

        Args:
            write: `booking_act_repository.begin` or `.insert_not_sent`, which take the
                same target.

        Returns: the id of the act written.

        Raises: BookingActNotRecordedError if nothing was written.
        """
        async with self._session_factory() as session:
            act_id = await write(
                session,
                session_id=self._session_id,
                chat_id=self._chat_id,
                message_id=self._message_id,
                operation=planned.operation,
                practitioner_id=planned.practitioner_id,
                practitioner_full_name=planned.practitioner_full_name,
                starts_at=planned.starts_at,
                appointment_id=planned.appointment_id,
                previous_practitioner_id=planned.previous_practitioner_id,
                previous_practitioner_full_name=planned.previous_practitioner_full_name,
                previous_starts_at=planned.previous_starts_at,
            )
        if act_id is None:
            raise BookingActNotRecordedError(self._message_id)
        return act_id
