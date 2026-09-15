"""A case's appointments, planted, read and released through the scheduler's contract.

The harness never plants through the agent: a precondition the booking loop created
would be the thing under measurement setting up its own exam. Every call here goes to
the scheduling service's own gRPC surface, scoped by the run's session, and dated by
the run clock rather than this host's.

A call that does not come back is never read as one that did nothing. A booking whose
answer was lost is reported as unanswered, not as nothing booked, since it may still
have booked; `release` cancels whatever a patient holds standing, whoever booked it;
and a cancellation, or the listing it starts from, that fails raises
`CleanupFailedError` rather than returning as if the patient held nothing. When a
patient is released, and what a failed release stops, are the driver's to decide
(`driver/run.py`).
"""

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Final

import grpc
from chat.agent.tools.scheduling_tools import derive_idempotency_key
from shared_models.localtime import format_local_datetime, parse_local_datetime
from shared_models.scheduling import AppointmentStatus
from shared_proto.scheduling.v1 import scheduling_pb2 as pb
from shared_proto.scheduling.v1 import scheduling_pb2_grpc

from golden_harness.cases import AppointmentRef
from golden_harness.record import AppointmentState, Unplantable, UnplantableSituation

# Generous beside the chat service's 2s: nothing here is on a patient's clock, and a
# slow answer that arrives is worth more than a fast failure that proves nothing.
_DEADLINE_SECONDS: Final = 10.0

_STATUS_BY_PROTO: Final = {
    pb.APPOINTMENT_STATUS_STANDING: AppointmentStatus.STANDING,
    pb.APPOINTMENT_STATUS_CANCELLED: AppointmentStatus.CANCELLED,
}


class SchedulingCallError(RuntimeError):
    """A scheduler call failed, or answered in a shape that cannot be read."""


class CleanupFailedError(RuntimeError):
    """A patient's standing appointments could not all be cancelled.

    `cancelled` holds what was cancelled before the failure.
    """

    def __init__(self, message: str, *, cancelled: list[AppointmentState]) -> None:
        """Carry the failure and what the release cancelled before it."""
        super().__init__(message)
        self.cancelled = cancelled


@asynccontextmanager
async def scheduling_stub(target: str) -> AsyncGenerator[Any]:
    """Open a channel to the scheduler at `target` and yield its stub, closing it after.

    The stub is yielded untyped: protoc emits it without annotations.
    """
    async with grpc.aio.insecure_channel(target) as channel:
        yield scheduling_pb2_grpc.SchedulingStub(channel)  # type: ignore[no-untyped-call]


async def resolve_roster(stub: Any, session_id: str) -> dict[str, str]:
    """Map each practitioner's full name in the session to its id.

    Raises: SchedulingCallError when the listing fails.
    """
    try:
        response = await stub.ListPractitioners(
            pb.ListPractitionersRequest(session_id=session_id),
            timeout=_DEADLINE_SECONDS,
        )
    except grpc.aio.AioRpcError as exc:
        raise SchedulingCallError(f"ListPractitioners: {exc.code().name}") from exc
    return {p.full_name: p.id for p in response.practitioners}


async def plant(
    stub: Any,
    *,
    session_id: str,
    patient_id: str,
    given: Sequence[AppointmentRef],
    clock: datetime,
) -> list[AppointmentState] | Unplantable:
    """Book every precondition of a fixture for the patient, in order.

    Returns: the appointments as the scheduler booked them, or why planting stopped

    A practitioner named by no roster entry books nothing at all, since the name is
    checked for every entry before the first booking. Any later failure - a refusal,
    or a call that did not come back - stops at that entry, and what was booked before
    it stays booked until the patient is released.
    """
    if not given:
        return []
    try:
        roster = await resolve_roster(stub, session_id)
    except SchedulingCallError as exc:
        return Unplantable(
            situation=UnplantableSituation.ROSTER_UNREADABLE,
            detail=f"the roster could not be read: {exc}",
        )
    unknown = sorted({e.practitioner for e in given if e.practitioner not in roster})
    if unknown:
        return Unplantable(
            situation=UnplantableSituation.NOT_ON_ROSTER,
            detail=f"not on the session's roster: {', '.join(unknown)}",
        )

    planted: list[AppointmentState] = []
    for entry in given:
        practitioner_id = roster[entry.practitioner]
        booked = await _book(
            stub,
            session_id=session_id,
            patient_id=patient_id,
            practitioner_id=practitioner_id,
            starts_at=_start(entry, clock),
            clock=clock,
        )
        if isinstance(booked, Unplantable):
            precondition = f"{entry.practitioner} {entry.day} {entry.time}"
            return booked.model_copy(
                update={"detail": f"{precondition}: {booked.detail}"}
            )
        planted.append(booked)
    return planted


async def read_post_state(
    stub: Any, *, session_id: str, patient_id: str, clock: datetime
) -> list[AppointmentState]:
    """Return every appointment the patient holds, past and future, of either status.

    Returns: the future appointments ascending, then the past ones most recent first

    Raises: SchedulingCallError when the listing fails, when the scheduler cut the past
        appointments short, or when an appointment cannot be read.
    """
    future, past = await _list(
        stub,
        session_id=session_id,
        patient_id=patient_id,
        clock=clock,
        status_filter=pb.STATUS_FILTER_BOTH,
    )
    return [_state(appointment) for appointment in (*future, *past)]


async def release(
    stub: Any, *, session_id: str, patient_id: str, clock: datetime
) -> list[AppointmentState]:
    """Cancel every appointment the patient still holds standing.

    Returns: the appointments cancelled, as the scheduler reported them after the change

    Raises: CleanupFailedError naming the appointment when a cancellation is refused,
        is not answered, or is answered with no result, or when the standing
        appointments cannot be listed.

    Each cancellation's guard - the start and the practitioner - is taken from the
    listing it was just read in. An appointment the scheduler reports as already
    cancelled was not cancelled by this call and is not returned.
    """
    try:
        future, past = await _list(
            stub,
            session_id=session_id,
            patient_id=patient_id,
            clock=clock,
            status_filter=pb.STATUS_FILTER_STANDING,
        )
    except SchedulingCallError as exc:
        raise CleanupFailedError(
            f"the standing appointments of {patient_id} could not be listed: {exc}",
            cancelled=[],
        ) from exc

    cancelled: list[AppointmentState] = []
    for appointment in (*future, *past):
        try:
            response = await stub.CancelAppointment(
                pb.CancelAppointmentRequest(
                    session_id=session_id,
                    patient_id=patient_id,
                    appointment_id=appointment.id,
                    expected_starts_at=appointment.starts_at,
                    expected_practitioner_id=appointment.practitioner_id,
                    local_now=format_local_datetime(clock),
                ),
                timeout=_DEADLINE_SECONDS,
            )
        except grpc.aio.AioRpcError as exc:
            raise CleanupFailedError(
                f"cancelling {appointment.id} was not answered: {exc.code().name}",
                cancelled=cancelled,
            ) from exc

        result = response.WhichOneof("result")
        if result == "no_change":
            continue
        if result == "failure":
            reason = pb.ChangeFailureReason.Name(response.failure.reason)
            raise CleanupFailedError(
                f"cancelling {appointment.id} was refused: {reason}",
                cancelled=cancelled,
            )
        if result != "appointment":
            raise CleanupFailedError(
                f"cancelling {appointment.id} was answered with no result",
                cancelled=cancelled,
            )
        try:
            cancelled.append(_state(response.appointment))
        except SchedulingCallError as exc:
            raise CleanupFailedError(
                f"cancelling {appointment.id} was answered unreadably: {exc}",
                cancelled=cancelled,
            ) from exc
    return cancelled


async def _book(
    stub: Any,
    *,
    session_id: str,
    patient_id: str,
    practitioner_id: str,
    starts_at: datetime,
    clock: datetime,
) -> AppointmentState | Unplantable:
    """Book one appointment.

    Returns: the booked appointment, or why it was not booked - its detail not yet
        naming the precondition
    """
    try:
        response = await stub.BookAppointment(
            pb.BookAppointmentRequest(
                session_id=session_id,
                patient_id=patient_id,
                practitioner_id=practitioner_id,
                starts_at=format_local_datetime(starts_at),
                local_now=format_local_datetime(clock),
                idempotency_key=derive_idempotency_key(
                    patient_id, practitioner_id, starts_at
                ),
            ),
            timeout=_DEADLINE_SECONDS,
        )
    except grpc.aio.AioRpcError as exc:
        return Unplantable(
            situation=UnplantableSituation.BOOKING_UNANSWERED,
            detail=f"BookAppointment was not answered: {exc.code().name}",
        )
    result = response.WhichOneof("result")
    if result is None:
        # Neither an appointment nor a failure: reading the unset `failure` would file a
        # scheduler that answered unreadably as a refusal the label caused.
        return Unplantable(
            situation=UnplantableSituation.BOOKING_UNREADABLE,
            detail="BookAppointment was answered with no result",
        )
    if result != "appointment":
        reason = pb.BookingFailureReason.Name(response.failure.reason)
        message = response.failure.detail
        return Unplantable(
            situation=UnplantableSituation.BOOKING_REFUSED,
            detail=f"refused: {reason} {message}".rstrip(),
            failure_reason=reason,
            scheduler_message=message or None,
        )
    try:
        return _state(response.appointment)
    except SchedulingCallError as exc:
        return Unplantable(
            situation=UnplantableSituation.BOOKING_UNREADABLE,
            detail=f"booked, but the answer could not be read: {exc}",
        )


async def _list(
    stub: Any,
    *,
    session_id: str,
    patient_id: str,
    clock: datetime,
    status_filter: int,
) -> tuple[list[Any], list[Any]]:
    """List the patient's appointments over all time with one status filter.

    Returns: the future appointments and the past appointments, as wire messages

    Raises: SchedulingCallError when the call fails or the past leg was cut short.
    """
    try:
        response = await stub.ListAppointments(
            pb.ListAppointmentsRequest(
                session_id=session_id,
                patient_id=patient_id,
                local_now=format_local_datetime(clock),
                time_filter=pb.TIME_FILTER_BOTH,
                status_filter=status_filter,  # type: ignore[arg-type]
            ),
            timeout=_DEADLINE_SECONDS,
        )
    except grpc.aio.AioRpcError as exc:
        raise SchedulingCallError(f"ListAppointments: {exc.code().name}") from exc
    if response.past_truncated:
        raise SchedulingCallError(
            f"the past appointments of {patient_id} were truncated"
        )
    return list(response.future), list(response.past)


def _start(entry: AppointmentRef, clock: datetime) -> datetime:
    """Return the local start a precondition names on `clock`."""
    start_time = entry.start_time()
    assert start_time is not None, "a precondition always names its time"
    return datetime.combine(entry.date_on(clock), start_time)


def _state(appointment: Any) -> AppointmentState:
    """Read a wire appointment into the record's shape.

    Raises: SchedulingCallError when its status is unset or unknown, or its start is
        not a local date-time.
    """
    status = _STATUS_BY_PROTO.get(appointment.status)
    if status is None:
        raise SchedulingCallError(
            f"appointment {appointment.id} has an unreadable status "
            f"{int(appointment.status)}"
        )
    try:
        starts_at = parse_local_datetime(appointment.starts_at)
    except ValueError as exc:
        raise SchedulingCallError(
            f"appointment {appointment.id} has an unreadable start"
        ) from exc
    return AppointmentState(
        practitioner_full_name=appointment.practitioner_full_name,
        starts_at=starts_at,
        status=status,
    )
