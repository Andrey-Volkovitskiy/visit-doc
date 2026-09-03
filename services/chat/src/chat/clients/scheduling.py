"""The only module in this service that knows the scheduling service exists on a wire.

Everything above it - tool handlers, graph nodes, API routes - depends on the plain
dataclasses declared here and never on a protobuf type, a channel, or a status code.

Two kinds of failure are deliberately kept apart:

* A booking the scheduler *evaluated and refused* comes back as a `BookingRefusal`,
  carrying one of the eight reasons. It is a normal result, and the assistant explains
  it to the patient.
* The scheduler failing to answer raises `SchedulingUnavailableError`, after the whole
  attempt budget is spent. Its `outcome_unknown` says whether a write may nonetheless
  have landed - a deadline we stopped waiting on does not stop the server working - so
  the assistant can say "nothing was created" only when that is actually known.

`SchedulingRequestError` sits outside both: the scheduler and this service disagree
about the contract - either this service sent something the contract forbids, or the
answer carried a value this build cannot read. A defect either way, rather than anything
the patient can act on. `SchedulingNotFoundError` covers an id that did not resolve, and
names which one.

All three share `SchedulingError`, so a caller that must not fail on *any* scheduling
problem can say so in one `except` that a fourth kind cannot slip past.
"""

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from weakref import WeakKeyDictionary

import grpc
import structlog
from shared_models.localtime import (
    format_local_date,
    format_local_datetime,
    parse_local_datetime,
    parse_local_time,
)
from shared_models.scheduling import (
    AppointmentStatus,
    BookingFailureReason,
    ChangeFailureReason,
    NotFoundEntity,
    RenameFailureReason,
    StatusFilter,
    TimeFilter,
    Weekday,
)
from shared_proto.metadata import TURN_ID_METADATA_KEY
from shared_proto.scheduling.v1 import scheduling_pb2 as pb
from shared_proto.scheduling.v1 import scheduling_pb2_grpc

from chat.core.config import Settings
from chat.core.logging import get_logger

# The only two statuses a retry can help with. Everything else means the server
# processed the request and answered, so sending it again would either duplicate work
# or repeat the same refusal.
_RETRYABLE_STATUSES = frozenset(
    {grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED}
)

_FAILURE_REASON_BY_PROTO = {
    pb.BOOKING_FAILURE_REASON_PRACTITIONER_BUSY: (
        BookingFailureReason.PRACTITIONER_BUSY
    ),
    pb.BOOKING_FAILURE_REASON_PATIENT_BUSY: BookingFailureReason.PATIENT_BUSY,
    pb.BOOKING_FAILURE_REASON_OUTSIDE_SCHEDULE: BookingFailureReason.OUTSIDE_SCHEDULE,
    pb.BOOKING_FAILURE_REASON_OFF_GRID: BookingFailureReason.OFF_GRID,
    pb.BOOKING_FAILURE_REASON_IN_PAST: BookingFailureReason.IN_PAST,
    pb.BOOKING_FAILURE_REASON_BEYOND_HORIZON: BookingFailureReason.BEYOND_HORIZON,
    pb.BOOKING_FAILURE_REASON_PRACTITIONER_NOT_FOUND: (
        BookingFailureReason.PRACTITIONER_NOT_FOUND
    ),
    pb.BOOKING_FAILURE_REASON_PATIENT_NOT_FOUND: (
        BookingFailureReason.PATIENT_NOT_FOUND
    ),
}

_APPOINTMENT_STATUS_BY_PROTO = {
    pb.APPOINTMENT_STATUS_STANDING: AppointmentStatus.STANDING,
    pb.APPOINTMENT_STATUS_CANCELLED: AppointmentStatus.CANCELLED,
}

# Zero is deliberately absent: on the wire it is `WEEKDAY_UNSPECIFIED`, the value
# proto3 sends for a field nobody populated, and it names no day. The wire's days run
# 1..7 while `shared_models.Weekday` runs 0..6, so this is a real translation rather
# than a pass-through.
_WEEKDAY_BY_PROTO = {
    pb.WEEKDAY_MONDAY: Weekday.MONDAY,
    pb.WEEKDAY_TUESDAY: Weekday.TUESDAY,
    pb.WEEKDAY_WEDNESDAY: Weekday.WEDNESDAY,
    pb.WEEKDAY_THURSDAY: Weekday.THURSDAY,
    pb.WEEKDAY_FRIDAY: Weekday.FRIDAY,
    pb.WEEKDAY_SATURDAY: Weekday.SATURDAY,
    pb.WEEKDAY_SUNDAY: Weekday.SUNDAY,
}

_CHANGE_REASON_BY_PROTO = {
    pb.CHANGE_FAILURE_REASON_APPOINTMENT_NOT_FOUND: (
        ChangeFailureReason.APPOINTMENT_NOT_FOUND
    ),
    pb.CHANGE_FAILURE_REASON_ALREADY_CANCELLED: ChangeFailureReason.ALREADY_CANCELLED,
    pb.CHANGE_FAILURE_REASON_ALREADY_STARTED: ChangeFailureReason.ALREADY_STARTED,
    pb.CHANGE_FAILURE_REASON_STALE_CONFIRMATION: (
        ChangeFailureReason.STALE_CONFIRMATION
    ),
    pb.CHANGE_FAILURE_REASON_PRACTITIONER_NOT_FOUND: (
        ChangeFailureReason.PRACTITIONER_NOT_FOUND
    ),
    pb.CHANGE_FAILURE_REASON_PATIENT_NOT_FOUND: ChangeFailureReason.PATIENT_NOT_FOUND,
    pb.CHANGE_FAILURE_REASON_IN_PAST: ChangeFailureReason.IN_PAST,
    pb.CHANGE_FAILURE_REASON_BEYOND_HORIZON: ChangeFailureReason.BEYOND_HORIZON,
    pb.CHANGE_FAILURE_REASON_OUTSIDE_SCHEDULE: ChangeFailureReason.OUTSIDE_SCHEDULE,
    pb.CHANGE_FAILURE_REASON_OFF_GRID: ChangeFailureReason.OFF_GRID,
    pb.CHANGE_FAILURE_REASON_PRACTITIONER_BUSY: ChangeFailureReason.PRACTITIONER_BUSY,
    pb.CHANGE_FAILURE_REASON_PATIENT_BUSY: ChangeFailureReason.PATIENT_BUSY,
}

_PROTO_BY_TIME_FILTER = {
    TimeFilter.FUTURE: pb.TIME_FILTER_FUTURE,
    TimeFilter.PAST: pb.TIME_FILTER_PAST,
    TimeFilter.BOTH: pb.TIME_FILTER_BOTH,
}

_PROTO_BY_STATUS_FILTER = {
    StatusFilter.STANDING: pb.STATUS_FILTER_STANDING,
    StatusFilter.CANCELLED: pb.STATUS_FILTER_CANCELLED,
    StatusFilter.BOTH: pb.STATUS_FILTER_BOTH,
}

_RENAME_REASON_BY_PROTO = {
    pb.RENAME_FAILURE_REASON_NAME_TAKEN: RenameFailureReason.NAME_TAKEN,
    pb.RENAME_FAILURE_REASON_PATIENT_NOT_FOUND: RenameFailureReason.PATIENT_NOT_FOUND,
}


class SchedulingError(Exception):
    """Base class for every way a scheduling call can fail.

    Exists so a caller whose contract is "this must never fail my request" - chat
    creation, say - can say that once, instead of listing the subclasses and silently
    letting the next one through.
    """


class SchedulingUnavailableError(SchedulingError):
    """Raised when the scheduling service could not answer within its budget.

    `outcome_unknown` says whether a write this call attempted may nonetheless exist.
    It is False only when no attempt can have been processed - every one failed to reach
    the server. It is True when an attempt may have been received and acted on: our own
    deadline expiring while the server was still working, or a server-side status, which
    means the request *was* processed. A caller must not tell the patient that nothing
    happened while it is True.
    """

    def __init__(self, detail: str, *, outcome_unknown: bool = True) -> None:
        """Build the error, defaulting to the cautious answer that a write may exist."""
        super().__init__(detail)
        self.outcome_unknown = outcome_unknown


class SchedulingNotFoundError(SchedulingError):
    """Raised when the scheduler could not resolve an id the request named.

    Includes an id belonging to another session, which the contract reports
    identically to one that never existed. Nothing was read and nothing was written.

    `entity` says *which* id failed to resolve, read from the status detail the
    contract defines. It is None only when the scheduler named something this build has
    no member for - a deployment skew - and a caller must then say that the lookup
    failed rather than pick one of the possibilities to blame.
    """

    def __init__(self, detail: str, *, entity: NotFoundEntity | None = None) -> None:
        """Build the error, recording which id failed to resolve when one was named."""
        super().__init__(detail)
        self.entity = entity


class SchedulingRequestError(SchedulingError):
    """Raised when the scheduler and this service disagree about the contract.

    Either direction: a request the scheduler rejected as malformed or contradictory -
    an idempotency key presented with a request it was not derived from - or an answer
    carrying a value this build cannot read, whether a member a newer scheduler has and
    this one does not or a timestamp that is not the offset-free local form. A defect
    either way, never something the patient chose, and not retryable: sending the same
    thing again produces the same answer.

    Raised only where nothing was written, or where a write is known not to have
    happened. A value that could not be read *after* the scheduler said a write landed
    is a `SchedulingUnavailableError` with `outcome_unknown` true instead - reporting
    that one as a request error would have the caller deny a real appointment.
    """


def _read_timestamp(value: str, field: str) -> datetime:
    """Read a wire timestamp into its naive `datetime`.

    Raises: SchedulingRequestError if `value` is not an offset-free local date-time -
        including the empty string proto3 sends for a timestamp nobody set.

    Wraps exactly the one call that reads the wire string, so an unreadable answer is
    the only thing that becomes a scheduling error: a `ValueError` from anywhere else,
    including a caller handing this module a timezone-aware `datetime` of its own,
    stays the defect it is instead of being reported as the scheduler's.
    """
    try:
        return parse_local_datetime(value)
    except ValueError as exc:
        get_logger().error(
            "scheduling.unreadable_timestamp", field=field, wire_value=value
        )
        raise SchedulingRequestError(f"unreadable {field}: {value!r}") from exc


def _read_time_of_day(value: str, field: str) -> time:
    """Read a wire time-of-day into its naive `time`.

    Raises: SchedulingRequestError if `value` is not an offset-free local time.

    Wraps exactly the one call that reads the wire string, for the reason
    `_read_timestamp()` gives.
    """
    try:
        return parse_local_time(value)
    except ValueError as exc:
        get_logger().error("scheduling.unreadable_time", field=field, wire_value=value)
        raise SchedulingRequestError(f"unreadable {field}: {value!r}") from exc


@dataclass(frozen=True)
class WorkingRangeInfo:
    """One span of a practitioner's weekly schedule."""

    weekday: Weekday
    start_time: str
    end_time: str


@dataclass(frozen=True)
class PractitionerInfo:
    """A practitioner as the assistant may describe them to a patient."""

    id: str
    full_name: str
    specialty: str
    appointment_duration_minutes: int
    schedule: tuple[WorkingRangeInfo, ...]

    @property
    def bookable(self) -> bool:
        """Whether any whole appointment fits inside any of this schedule's ranges.

        Raises: SchedulingRequestError propagated from `_range_minutes()` if a range
            carries hours this build cannot read.

        False for an empty schedule, and for a duration longer than every range - both
        of which leave the practitioner listed but with no time to offer.
        """
        return any(
            self._range_minutes(r) >= self.appointment_duration_minutes
            for r in self.schedule
        )

    @staticmethod
    def _range_minutes(working_range: WorkingRangeInfo) -> int:
        """Return `working_range`'s length in whole minutes.

        Raises: SchedulingRequestError propagated from `_read_time_of_day()` if either
            end is not an offset-free local time.
        """
        start = _read_time_of_day(working_range.start_time, "working range start")
        end = _read_time_of_day(working_range.end_time, "working range end")
        return (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)


@dataclass(frozen=True)
class PatientInfo:
    """The patient one chat books on behalf of."""

    id: str
    chat_id: str
    full_name: str


@dataclass(frozen=True)
class AppointmentInfo:
    """An appointment, with both parties' names already resolved.

    `status` says whether it still counts. A cancelled appointment is a real record
    that can be listed and named, so nothing above this module may treat presence
    alone as meaning the appointment stands.
    """

    id: str
    patient_id: str
    patient_full_name: str
    practitioner_id: str
    practitioner_full_name: str
    practitioner_specialty: str
    starts_at: datetime
    ends_at: datetime
    status: AppointmentStatus


@dataclass(frozen=True)
class AvailabilityResult:
    """Start times bookable by one patient with one practitioner.

    `truncated` means the requested window was clamped or the result capped, so an
    empty list with `truncated` false is the only thing that means "genuinely nothing
    bookable here".
    """

    available_starts: tuple[datetime, ...]
    appointment_duration_minutes: int
    truncated: bool


@dataclass(frozen=True)
class BookingRefusal:
    """A booking the scheduler evaluated and declined, with the single reason why."""

    reason: BookingFailureReason
    detail: str


@dataclass(frozen=True)
class BookingSuccess:
    """A booking that exists, whether created now or replayed from an earlier attempt.

    `idempotent_replay` is diagnostic: the patient is told the same thing either way.
    """

    appointment: AppointmentInfo
    idempotent_replay: bool


@dataclass(frozen=True)
class ChangeApplied:
    """A change whose write took effect, with the state it came from.

    The previous values come from the same statement that wrote the new ones, so they
    describe the state the appointment actually left - not one a concurrent change may
    already have replaced. `previous_practitioner_full_name` is the practitioner it had,
    equal to the current one when only the time moved.
    """

    appointment: AppointmentInfo
    previous_starts_at: datetime
    previous_practitioner_full_name: str


@dataclass(frozen=True)
class ChangeNoOp:
    """The appointment was already in the state the request asked for.

    A success, not a refusal: a re-sent change, or a patient asking for the time they
    already have. Kept distinct from `ChangeApplied` so a caller can report one change
    per real transition rather than one per request.
    """

    appointment: AppointmentInfo


@dataclass(frozen=True)
class ChangeRefusal:
    """A change the scheduler evaluated and declined, with the single reason why."""

    reason: ChangeFailureReason
    detail: str


@dataclass(frozen=True)
class AppointmentListing:
    """One patient's appointments, in two separately bounded legs.

    Never merged into one list: the past leg is capped because past appointments
    accumulate without limit, and sharing a cap with the future leg would let twenty
    future appointments crowd out every past one.

    `past_truncated` is about the past leg alone, so a caller can say *that part* of the
    list is incomplete rather than that the whole answer is.
    """

    future: tuple[AppointmentInfo, ...]
    past: tuple[AppointmentInfo, ...]
    past_truncated: bool


@dataclass(frozen=True)
class ProvisioningResult:
    """What one `ensure_session_provisioned` call found or created.

    The session's practitioners are deliberately not carried here even though the
    response includes them: nothing provisioning does needs them, and a caller that
    wants them asks `list_practitioners`.
    """

    patient: PatientInfo
    patient_created: bool
    practitioner_created: bool


@dataclass(frozen=True)
class RenameRefusal:
    """A rename the scheduler evaluated and declined, with the single reason why."""

    reason: RenameFailureReason
    detail: str


@dataclass(frozen=True)
class DeletionResult:
    """What one `delete_patient_for_chat` call removed."""

    patient_existed: bool
    appointments_deleted: int


def create_channel(settings: Settings) -> grpc.aio.Channel:
    """Open the shared channel to the scheduling service.

    Built once at app startup and reused: a channel is a connection pool, and building
    one per call would pay TCP and HTTP/2 setup on every tool invocation.
    """
    return grpc.aio.insecure_channel(settings.SCHEDULING_GRPC_TARGET)


# One stub per channel, for the life of that channel. Building a stub binds a
# multicallable per method, so constructing one per RPC allocated six objects to use
# one - and the channel is explicitly built once and reused, so its stub can be too.
# Keyed weakly, so a closed channel is not held alive by this cache.
_STUBS: "WeakKeyDictionary[grpc.aio.Channel, Any]" = WeakKeyDictionary()


def _stub(channel: grpc.aio.Channel) -> Any:
    """Return the `SchedulingStub` for `channel`, building it on first use.

    protoc emits the stub without annotations, so this one function absorbs that rather
    than every call site repeating the same suppression.
    """
    stub = _STUBS.get(channel)
    if stub is None:
        stub = scheduling_pb2_grpc.SchedulingStub(channel)  # type: ignore[no-untyped-call]
        _STUBS[channel] = stub
    return stub


def _turn_id_metadata() -> Sequence[tuple[str, str]]:
    """Return the current turn's correlation id as gRPC metadata, if one is bound.

    Read from the log context rather than threaded through every signature, matching
    how the id is scoped everywhere else in this service.
    """
    turn_id = structlog.contextvars.get_contextvars().get("turn_id")
    if not isinstance(turn_id, str):
        return ()
    return ((TURN_ID_METADATA_KEY, turn_id),)


def _read_not_found_entity(detail: str, method: str) -> NotFoundEntity | None:
    """Read which id failed to resolve out of a NOT_FOUND status detail.

    Returns None for a detail this build has no member for, which is a scheduler
    deployed ahead of this service - logged, because the alternative to knowing is a
    caller guessing.
    """
    try:
        return NotFoundEntity(detail)
    except ValueError:
        get_logger().error(
            "scheduling.unknown_not_found_entity", method=method, error_detail=detail
        )
        return None


async def _call(
    settings: Settings,
    method: str,
    invoke: Callable[..., Any],
    request: Any,
) -> Any:
    """Invoke one RPC under the deadline and attempt budget, returning its response.

    Raises:
        SchedulingUnavailableError: every attempt failed with a retryable transport
            status, or a server-side status ended it early. Its `outcome_unknown` says
            whether a write may nonetheless have landed.
        SchedulingRequestError: the server rejected the request as invalid.
        SchedulingNotFoundError: the server could not resolve an id the request named.

    Retries only `UNAVAILABLE` and `DEADLINE_EXCEEDED`, pausing between attempts so the
    budget spans real time; any other status means the server answered, so the answer
    stands.
    """
    logger = get_logger()
    metadata = _turn_id_metadata()
    last_detail = ""
    # Stays False only while every failure so far proves the request never reached the
    # server. A deadline is ours, not the server's: it may well have finished the work
    # after we stopped waiting for it.
    outcome_unknown = False
    attempts_made = 0
    for attempt in range(1, settings.SCHEDULING_MAX_ATTEMPTS + 1):
        if attempt > 1:
            await asyncio.sleep(settings.SCHEDULING_RETRY_BACKOFF_SECONDS)
        attempts_made = attempt
        try:
            response = await invoke(
                request,
                timeout=settings.SCHEDULING_TIMEOUT_SECONDS,
                metadata=metadata,
            )
        except grpc.aio.AioRpcError as exc:
            status = exc.code()
            last_detail = exc.details() or status.name
            logger.info(
                "scheduling.call", method=method, attempt=attempt, status=status.name
            )
            if status == grpc.StatusCode.INVALID_ARGUMENT:
                raise SchedulingRequestError(last_detail) from exc
            if status == grpc.StatusCode.NOT_FOUND:
                raise SchedulingNotFoundError(
                    last_detail, entity=_read_not_found_entity(last_detail, method)
                ) from exc
            if status == grpc.StatusCode.DEADLINE_EXCEEDED:
                outcome_unknown = True
            if status not in _RETRYABLE_STATUSES:
                # The server answered, so it processed the request - whatever it did
                # before failing stands, and this is not a dependency outage.
                outcome_unknown = True
                break
        else:
            logger.info("scheduling.call", method=method, attempt=attempt, status="OK")
            return response

    logger.error(
        "scheduling.unavailable",
        method=method,
        attempts=attempts_made,
        outcome_unknown=outcome_unknown,
        error_detail=last_detail,
    )
    if not outcome_unknown:
        # Only a request that never reached the server is evidence about the server
        # itself; a status it answered with is a defect, not an outage, and paging on
        # it would make a handler bug indistinguishable from the scheduler being down.
        logger.critical(
            "critical.dependency_unreachable",
            dependency="scheduler",
            error_detail=last_detail,
        )
    raise SchedulingUnavailableError(last_detail, outcome_unknown=outcome_unknown)


def _read_weekday(proto_weekday: Any, practitioner_id: str) -> Weekday:
    """Read a wire weekday into its domain member.

    Raises: SchedulingRequestError if the range names no weekday at all, or names one
        this build has no member for - a scheduler deployed ahead of this service.

    The unspecified zero value is rejected rather than defaulted: proto3 sends it for a
    field nobody set, and reading it as Monday would put a practitioner in front of the
    patient working hours they may not work. The two are logged apart because they are
    different defects - one is a range that lost its weekday, the other a day this
    build does not know.
    """
    if proto_weekday == pb.WEEKDAY_UNSPECIFIED:
        get_logger().error("scheduling.unset_weekday", practitioner_id=practitioner_id)
        raise SchedulingRequestError("working range names no weekday")
    weekday = _WEEKDAY_BY_PROTO.get(proto_weekday)
    if weekday is None:
        get_logger().error(
            "scheduling.unknown_weekday",
            proto_weekday=int(proto_weekday),
            practitioner_id=practitioner_id,
        )
        raise SchedulingRequestError(f"unrecognized weekday: {int(proto_weekday)}")
    return weekday


def _to_practitioner(message: pb.Practitioner) -> PractitionerInfo:
    """Read a wire practitioner into its domain form.

    Raises: SchedulingRequestError if a working range names no weekday, or one this
        build has no member for. Not dropped from the schedule instead: a day missing
        from it reads as a practitioner who does not work then, which is a wrong answer
        rather than an incomplete one.
    """
    return PractitionerInfo(
        id=message.id,
        full_name=message.full_name,
        specialty=message.specialty,
        appointment_duration_minutes=message.appointment_duration_minutes,
        schedule=tuple(
            WorkingRangeInfo(
                weekday=_read_weekday(r.weekday, message.id),
                start_time=r.start_time,
                end_time=r.end_time,
            )
            for r in message.schedule
        ),
    )


def _to_appointment(message: pb.Appointment) -> AppointmentInfo:
    """Read a wire appointment into its domain form.

    Raises:
        SchedulingRequestError: `status` is unset or is a value this build has no
            member for, or either timestamp is not an offset-free local date-time. The
            status is not defaulted to standing: proto3 sends the zero value for a
            field nobody set, and reading that as standing would present a cancelled
            appointment to the patient as a live one.

    A caller that has already been told a write landed must convert that error rather
    than propagate it - it says the answer could not be read, not that nothing happened.
    """
    status = _APPOINTMENT_STATUS_BY_PROTO.get(message.status)
    if status is None:
        get_logger().error(
            "scheduling.unknown_appointment_status",
            proto_status=int(message.status),
            appointment_id=message.id,
        )
        raise SchedulingRequestError(
            f"unrecognized appointment status: {int(message.status)}"
        )
    return AppointmentInfo(
        id=message.id,
        patient_id=message.patient_id,
        patient_full_name=message.patient_full_name,
        practitioner_id=message.practitioner_id,
        practitioner_full_name=message.practitioner_full_name,
        practitioner_specialty=message.practitioner_specialty,
        starts_at=_read_timestamp(message.starts_at, "appointment start"),
        ends_at=_read_timestamp(message.ends_at, "appointment end"),
        status=status,
    )


async def ensure_session_provisioned(
    channel: grpc.aio.Channel, settings: Settings, *, session_id: str, chat_id: str
) -> ProvisioningResult:
    """Create this chat's patient, and one practitioner if the session has none.

    Raises:
        SchedulingUnavailableError: the scheduler could not be reached. Its
            `outcome_unknown` says whether the patient may nonetheless have been
            created - which costs a caller nothing to ignore, since this rpc is
            idempotent and a later attempt returns whatever the first one created.
        SchedulingNotFoundError: `chat_id` already belongs to another session's
            patient, which is never answered with that patient. Nothing was created.
        SchedulingRequestError: the scheduler rejected the request as malformed.
            Nothing was created, and sending the same request again is rejected again.

    Idempotent: calling it again for a chat that already has a patient returns that
    patient with `patient_created` false, creating nothing.
    """
    response = await _call(
        settings,
        "EnsureSessionProvisioned",
        _stub(channel).EnsureSessionProvisioned,
        pb.EnsureSessionProvisionedRequest(session_id=session_id, chat_id=chat_id),
    )
    return ProvisioningResult(
        patient=PatientInfo(
            id=response.patient.id,
            chat_id=response.patient.chat_id,
            full_name=response.patient.full_name,
        ),
        patient_created=response.patient_created,
        practitioner_created=response.practitioner_created,
    )


async def rename_patient(
    channel: grpc.aio.Channel,
    settings: Settings,
    *,
    session_id: str,
    patient_id: str,
    full_name: str,
) -> PatientInfo | RenameRefusal:
    """Rename one patient, or report the single reason the new name was refused.

    Returns: the patient as the scheduler stored it, or a `RenameRefusal` naming why
        the name was declined.

    Raises:
        SchedulingUnavailableError: the scheduler could not answer. Its
            `outcome_unknown` says whether the rename may nonetheless have landed -
            but this write is idempotent, so the same request may simply be sent again.
        SchedulingRequestError: the name was empty or longer than the contract allows,
            or the refusal carried a reason this build cannot name.
        SchedulingNotFoundError: propagated from `_call()` - not reachable here, since
            an unknown patient is a typed `RenameRefusal` instead.
    """
    response = await _call(
        settings,
        "RenamePatient",
        _stub(channel).RenamePatient,
        pb.RenamePatientRequest(
            session_id=session_id, patient_id=patient_id, full_name=full_name
        ),
    )
    if response.WhichOneof("result") == "failure":
        reason = _RENAME_REASON_BY_PROTO.get(response.failure.reason)
        if reason is None:
            # A reason this build has no name for - an unset field, or a scheduler
            # deployed ahead of this service. The refusal is still trustworthy (nothing
            # was renamed), but it cannot be explained, so it is reported as a defect.
            get_logger().error(
                "rename.unknown_failure_reason",
                proto_reason=int(response.failure.reason),
                error_detail=response.failure.detail,
            )
            raise SchedulingRequestError(
                f"unrecognized rename failure reason: {int(response.failure.reason)}"
            )
        return RenameRefusal(reason=reason, detail=response.failure.detail)
    return PatientInfo(
        id=response.patient.id,
        chat_id=response.patient.chat_id,
        full_name=response.patient.full_name,
    )


async def list_practitioners(
    channel: grpc.aio.Channel, settings: Settings, *, session_id: str
) -> tuple[PractitionerInfo, ...]:
    """Return every practitioner in this session.

    Raises:
        SchedulingUnavailableError: the scheduler could not be reached. A read, so
            nothing was written and the request is always safe to send again.
        SchedulingRequestError: the scheduler rejected the request as malformed, or a
            working range in its answer named no weekday at all or one this build has
            no member for - a defect either way, not something a caller can resolve by
            asking differently.
        SchedulingNotFoundError: propagated from `_call()` - not reachable here, since
            a session the scheduler holds no practitioners for is an empty tuple rather
            than an id that did not resolve.
    """
    response = await _call(
        settings,
        "ListPractitioners",
        _stub(channel).ListPractitioners,
        pb.ListPractitionersRequest(session_id=session_id),
    )
    return tuple(_to_practitioner(p) for p in response.practitioners)


async def check_availability(
    channel: grpc.aio.Channel,
    settings: Settings,
    *,
    session_id: str,
    practitioner_id: str,
    patient_id: str,
    from_date: date,
    to_date: date,
    local_now: datetime,
    excluded_appointment_id: str | None = None,
) -> AvailabilityResult:
    """Return the start times this patient can book with this practitioner.

    Args:
        excluded_appointment_id: The appointment being moved, omitted from both parties'
            commitments so it does not block its own new time. Without it, the slot an
            appointment currently holds is missing from its own options.

    Raises:
        SchedulingUnavailableError: the scheduler could not answer. A read, so nothing
            was written and the request is always safe to send again.
        SchedulingNotFoundError: no such practitioner or patient in this session; its
            `entity` says which, so the caller never has to assume.
        SchedulingRequestError: the scheduler rejected the request - `to_date` before
            `from_date`, or a field it could not read - or its answer carried a start
            time that is not an offset-free local date-time. Nothing was read either
            way, and sending the same request again is answered the same way.
        ValueError: propagated from `shared_models.localtime` if `local_now` carries a
            timezone offset, which is a defect in the caller rather than in the answer.

    Availability is patient-relative: a slot colliding with this patient's own
    appointment - with any practitioner - is already gone from the result.

    An empty result therefore always describes a practitioner who exists: "not one of
    this clinic's" arrives as `SchedulingNotFoundError`, never as zero available times.
    """
    response = await _call(
        settings,
        "CheckAvailability",
        _stub(channel).CheckAvailability,
        pb.CheckAvailabilityRequest(
            session_id=session_id,
            practitioner_id=practitioner_id,
            patient_id=patient_id,
            from_date=format_local_date(from_date),
            to_date=format_local_date(to_date),
            local_now=format_local_datetime(local_now),
            excluded_appointment_id=excluded_appointment_id or "",
        ),
    )
    return AvailabilityResult(
        available_starts=tuple(
            _read_timestamp(start, "available start")
            for start in response.available_starts
        ),
        appointment_duration_minutes=response.appointment_duration_minutes,
        truncated=response.truncated,
    )


async def book_appointment(
    channel: grpc.aio.Channel,
    settings: Settings,
    *,
    session_id: str,
    patient_id: str,
    practitioner_id: str,
    starts_at: datetime,
    local_now: datetime,
    idempotency_key: str,
) -> BookingSuccess | BookingRefusal:
    """Create one appointment, or report the single reason it was refused.

    Returns: a `BookingSuccess` when the appointment exists - created now or replayed
        from an identical earlier attempt - or a `BookingRefusal` naming why not.

    Raises:
        SchedulingUnavailableError: the scheduler could not answer, or it answered with
            an appointment this build cannot read. Its `outcome_unknown` distinguishes
            "nothing was created" from "this may have been created and we cannot tell",
            and the unreadable answer is always the second: the appointment exists, and
            only its rendering failed.
        SchedulingRequestError: `idempotency_key` was already used for a *different*
            booking, or the refusal carried a reason this build cannot name; nothing was
            created either way.
        SchedulingNotFoundError: propagated from `_call()` - not reachable here, since
            an unknown patient or practitioner is a typed `BookingRefusal` instead.
        ValueError: propagated from `shared_models.localtime` if `starts_at` or
            `local_now` carries a timezone offset, which is a defect in the caller
            rather than in the answer.
    """
    response = await _call(
        settings,
        "BookAppointment",
        _stub(channel).BookAppointment,
        pb.BookAppointmentRequest(
            session_id=session_id,
            patient_id=patient_id,
            practitioner_id=practitioner_id,
            starts_at=format_local_datetime(starts_at),
            local_now=format_local_datetime(local_now),
            idempotency_key=idempotency_key,
        ),
    )
    if response.WhichOneof("result") == "failure":
        reason = _FAILURE_REASON_BY_PROTO.get(response.failure.reason)
        if reason is None:
            # A reason this build has no name for - an unset field, or a scheduler
            # deployed ahead of this service. The refusal itself is still trustworthy
            # (a failure response means nothing was created), but it cannot be
            # explained, so it is reported as a defect rather than guessed at.
            get_logger().error(
                "booking.unknown_failure_reason",
                proto_reason=int(response.failure.reason),
                error_detail=response.failure.detail,
            )
            raise SchedulingRequestError(
                f"unrecognized booking failure reason: {int(response.failure.reason)}"
            )
        return BookingRefusal(reason=reason, detail=response.failure.detail)

    # The scheduler has answered with an appointment, so it exists. A failure to render
    # it can no longer be reported as a request error, which every caller reads as
    # "nothing was created" - the one claim that turns an uncancellable appointment
    # into a second one.
    try:
        appointment = _to_appointment(response.appointment)
    except SchedulingRequestError as exc:
        raise SchedulingUnavailableError(
            f"booking response could not be read: {exc}", outcome_unknown=True
        ) from exc
    return BookingSuccess(
        appointment=appointment, idempotent_replay=response.idempotent_replay
    )


def _read_change_reason(failure: pb.ChangeFailure) -> ChangeFailureReason:
    """Read a refusal's reason into its domain member.

    Raises: SchedulingRequestError if the reason is unset or is one this build has no
        member for - a scheduler deployed ahead of this service. The refusal itself is
        still trustworthy, but it cannot be explained, so it is reported as a defect
        rather than guessed at.
    """
    reason = _CHANGE_REASON_BY_PROTO.get(failure.reason)
    if reason is None:
        get_logger().error(
            "change.unknown_failure_reason",
            proto_reason=int(failure.reason),
            error_detail=failure.detail,
        )
        raise SchedulingRequestError(
            f"unrecognized change failure reason: {int(failure.reason)}"
        )
    return reason


def _to_change_outcome(
    response: pb.ChangeAppointmentResponse,
) -> ChangeApplied | ChangeNoOp | ChangeRefusal:
    """Read one change response into exactly one of its three domain outcomes.

    Raises:
        SchedulingRequestError: the refusal named a reason this build cannot explain.
            The refusal itself is trustworthy - nothing was changed.
        SchedulingUnavailableError: with `outcome_unknown` true, for the two cases where
            the scheduler's answer cannot be read but is not a refusal - a response
            carrying no result at all, and an appointment this build cannot render
            (a status a newer scheduler has and this one does not). The second matters
            most: the change *completed*, so letting it surface as a request error
            would have the caller tell the patient nothing was changed. An unreadable
            `previous_starts_at` is the same case, and takes the same route.

    `previous_practitioner_full_name` falls back to the appointment's current
    practitioner when the response names none, which is what a cancellation sends: it
    has no destination, so the practitioner it had is the one it still has.
    """
    result = response.WhichOneof("result")
    if result == "failure":
        return ChangeRefusal(
            reason=_read_change_reason(response.failure),
            detail=response.failure.detail,
        )
    if result != "appointment" and result != "no_change":
        get_logger().error("change.response_without_result")
        raise SchedulingUnavailableError(
            "change response carried no result", outcome_unknown=True
        )

    # From here the scheduler has told us the change did not fail, so a failure to read
    # its answer can no longer be reported as one.
    try:
        if result == "no_change":
            return ChangeNoOp(
                appointment=_to_appointment(response.no_change.appointment)
            )
        appointment = _to_appointment(response.appointment)
        previous_starts_at = (
            _read_timestamp(response.previous_starts_at, "previous start")
            if response.previous_starts_at
            else appointment.starts_at
        )
    except SchedulingRequestError as exc:
        raise SchedulingUnavailableError(
            f"change response could not be read: {exc}", outcome_unknown=True
        ) from exc

    return ChangeApplied(
        appointment=appointment,
        previous_starts_at=previous_starts_at,
        previous_practitioner_full_name=(
            response.previous_practitioner_full_name
            or appointment.practitioner_full_name
        ),
    )


async def reschedule_appointment(
    channel: grpc.aio.Channel,
    settings: Settings,
    *,
    session_id: str,
    patient_id: str,
    appointment_id: str,
    new_starts_at: datetime,
    new_practitioner_id: str | None,
    expected_starts_at: datetime,
    expected_practitioner_id: str,
    local_now: datetime,
) -> ChangeApplied | ChangeNoOp | ChangeRefusal:
    """Move one appointment, or report the one reason it was not moved.

    Args:
        new_practitioner_id: The practitioner to move it to, or None to keep the one it
            has. Practitioner, start and end change together in one write.
        expected_starts_at: The start the assistant stated to the patient when it asked
            them to confirm - not a value re-read just now, which would match the
            appointment's current state by definition and disable the guard.
        expected_practitioner_id: The practitioner it stated, for the same reason.

    Returns: a `ChangeApplied` when the appointment moved, carrying the state it came
        from, a `ChangeNoOp` when it was already there, or a `ChangeRefusal` naming why
        not.

    Raises:
        SchedulingUnavailableError: the scheduler could not answer. Its
            `outcome_unknown` says whether the move may nonetheless have landed; when it
            is true the caller must not report that nothing happened.
        SchedulingRequestError: the scheduler rejected the request as malformed, or the
            response carried a reason this build cannot name. Nothing was moved either
            way - the first is refused before the write, and the second accompanies a
            refusal.
        SchedulingNotFoundError: propagated from `_call()` - not reachable here, since
            an appointment, patient or practitioner that does not resolve is a typed
            `ChangeRefusal` instead.
        ValueError: propagated from `shared_models.localtime` if any of the three
            `datetime` arguments carries a timezone offset, which is a defect in the
            caller rather than in the answer.
    """
    response = await _call(
        settings,
        "RescheduleAppointment",
        _stub(channel).RescheduleAppointment,
        pb.RescheduleAppointmentRequest(
            session_id=session_id,
            patient_id=patient_id,
            appointment_id=appointment_id,
            new_starts_at=format_local_datetime(new_starts_at),
            # Empty is the contract's "keep the practitioner it has".
            new_practitioner_id=new_practitioner_id or "",
            expected_starts_at=format_local_datetime(expected_starts_at),
            expected_practitioner_id=expected_practitioner_id,
            local_now=format_local_datetime(local_now),
        ),
    )
    return _to_change_outcome(response)


async def cancel_appointment(
    channel: grpc.aio.Channel,
    settings: Settings,
    *,
    session_id: str,
    patient_id: str,
    appointment_id: str,
    expected_starts_at: datetime,
    expected_practitioner_id: str,
    local_now: datetime,
) -> ChangeApplied | ChangeNoOp | ChangeRefusal:
    """Cancel one appointment, or report the one reason it was not.

    Args:
        expected_starts_at: The start the assistant stated to the patient when it asked
            them to confirm - not a value re-read just now, which would match the
            appointment's current state by definition and disable the guard.
        expected_practitioner_id: The practitioner it stated, for the same reason.

    Returns: a `ChangeApplied` when the appointment is now cancelled, a `ChangeNoOp`
        when it already was, or a `ChangeRefusal` naming why not.

    Raises:
        SchedulingUnavailableError: the scheduler could not answer. Its
            `outcome_unknown` says whether the cancellation may nonetheless have
            landed; when it is true the caller must not report that nothing happened.
        SchedulingRequestError: the scheduler rejected the request as malformed, or the
            response carried a reason this build cannot name. Nothing was cancelled
            either way - the first is refused before the write, and the second
            accompanies a refusal.
        SchedulingNotFoundError: propagated from `_call()` - not reachable here, since
            an appointment, patient or practitioner that does not resolve is a typed
            `ChangeRefusal` instead.
        ValueError: propagated from `shared_models.localtime` if `expected_starts_at`
            or `local_now` carries a timezone offset, which is a defect in the caller
            rather than in the answer.
    """
    response = await _call(
        settings,
        "CancelAppointment",
        _stub(channel).CancelAppointment,
        pb.CancelAppointmentRequest(
            session_id=session_id,
            patient_id=patient_id,
            appointment_id=appointment_id,
            expected_starts_at=format_local_datetime(expected_starts_at),
            expected_practitioner_id=expected_practitioner_id,
            local_now=format_local_datetime(local_now),
        ),
    )
    return _to_change_outcome(response)


async def list_appointments(
    channel: grpc.aio.Channel,
    settings: Settings,
    *,
    session_id: str,
    patient_id: str,
    local_now: datetime,
    time_filter: TimeFilter = TimeFilter.FUTURE,
    status_filter: StatusFilter = StatusFilter.STANDING,
) -> AppointmentListing:
    """Return this patient's appointments in the corner of the grid asked for.

    Raises:
        SchedulingUnavailableError: the scheduler could not be reached. A read, so
            nothing was written and the request is always safe to send again.
        SchedulingNotFoundError: no such patient in this session, which the contract
            keeps distinct from an empty result - that one means the patient exists and
            has nothing matching.
        SchedulingRequestError: the scheduler rejected the request as malformed, or its
            answer carried an appointment status this build has no member for or a
            timestamp that is not an offset-free local date-time. No listing is
            returned in any of those cases, and none of them is evidence about the
            appointments themselves.
        ValueError: propagated from `shared_models.localtime` if `local_now` carries a
            timezone offset, which is a defect in the caller rather than in the answer.

    Both filters default to the narrowest value, so the unqualified question answers
    "still to come, and not cancelled" without a caller having to say so.
    """
    response = await _call(
        settings,
        "ListAppointments",
        _stub(channel).ListAppointments,
        pb.ListAppointmentsRequest(
            session_id=session_id,
            patient_id=patient_id,
            local_now=format_local_datetime(local_now),
            time_filter=_PROTO_BY_TIME_FILTER[time_filter],
            status_filter=_PROTO_BY_STATUS_FILTER[status_filter],
        ),
    )
    return AppointmentListing(
        future=tuple(_to_appointment(a) for a in response.future),
        past=tuple(_to_appointment(a) for a in response.past),
        past_truncated=response.past_truncated,
    )


async def delete_patient_for_chat(
    channel: grpc.aio.Channel, settings: Settings, *, session_id: str, chat_id: str
) -> DeletionResult:
    """Delete this chat's patient and, by cascade, that patient's appointments.

    Raises:
        SchedulingUnavailableError: the scheduler could not answer. Its
            `outcome_unknown` says whether the deletion may nonetheless have happened -
            false means nothing was deleted, true means it is genuinely not known. The
            request is safe to send again either way: deleting an already-absent
            patient succeeds, reporting `patient_existed` false.
        SchedulingRequestError: the scheduler rejected the request as malformed, which
            it does before touching anything - nothing was deleted, and sending the
            same request again is rejected again.
        SchedulingNotFoundError: propagated from `_call()` - not reachable here, since
            a chat whose patient is already gone is a success carrying
            `patient_existed` false, not an id that did not resolve.
    """
    response = await _call(
        settings,
        "DeletePatientForChat",
        _stub(channel).DeletePatientForChat,
        pb.DeletePatientForChatRequest(session_id=session_id, chat_id=chat_id),
    )
    return DeletionResult(
        patient_existed=response.patient_existed,
        appointments_deleted=response.appointments_deleted,
    )


@dataclass(frozen=True)
class SessionPurge:
    """What a session's deletion removed from the scheduling store."""

    patients_deleted: int
    practitioners_deleted: int
    appointments_deleted: int


async def delete_session(
    channel: grpc.aio.Channel, settings: Settings, *, session_id: str
) -> SessionPurge:
    """Delete everything `session_id` owns in the scheduling store.

    Raises:
        SchedulingUnavailableError: the scheduler could not answer. Its
            `outcome_unknown` says whether the deletion may nonetheless have happened.
            Either way the request is safe to send again: the rpc is idempotent, and a
            session that owns nothing is deleted successfully with every count at zero.
        SchedulingRequestError: the scheduler rejected the request as invalid. Nothing
            was deleted, and sending the same request again is rejected again.
        SchedulingNotFoundError: propagated from `_call()` - not reachable here, since
            a session the scheduler holds nothing for is deleted successfully with
            every count at zero, not an id that did not resolve.

    Zero counts therefore mean "there was nothing left", never "this did not work" -
    which is what lets a caller re-run a deletion it had to report incomplete.
    """
    response = await _call(
        settings,
        "DeleteSession",
        _stub(channel).DeleteSession,
        pb.DeleteSessionRequest(session_id=session_id),
    )
    return SessionPurge(
        patients_deleted=response.patients_deleted,
        practitioners_deleted=response.practitioners_deleted,
        appointments_deleted=response.appointments_deleted,
    )


__all__ = [
    "AppointmentInfo",
    "AppointmentListing",
    "AvailabilityResult",
    "BookingRefusal",
    "BookingSuccess",
    "ChangeApplied",
    "ChangeNoOp",
    "ChangeRefusal",
    "DeletionResult",
    "PatientInfo",
    "PractitionerInfo",
    "ProvisioningResult",
    "RenameRefusal",
    "SchedulingError",
    "SchedulingNotFoundError",
    "SchedulingRequestError",
    "SchedulingUnavailableError",
    "SessionPurge",
    "WorkingRangeInfo",
    "book_appointment",
    "cancel_appointment",
    "check_availability",
    "create_channel",
    "delete_patient_for_chat",
    "delete_session",
    "ensure_session_provisioned",
    "list_appointments",
    "list_practitioners",
    "rename_patient",
    "reschedule_appointment",
]
