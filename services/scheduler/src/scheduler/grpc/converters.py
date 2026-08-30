"""Translation between the gRPC wire types and this service's domain objects.

The wire carries local wall-clock times as offset-free ISO-8601 strings, validated here
on ingress. Keeping every parse in one module is what makes "no timezone reaches the
domain" checkable in one place rather than at each handler.

Only conversions that add something - a required-field check, a wire error type - live
here. A value that needs nothing but `shared_models.localtime`'s own formatter is
formatted at its call site, so there is one name for the operation rather than two.
"""

from datetime import date, datetime
from typing import cast

from shared_models.localtime import (
    format_local_datetime,
    format_local_time,
    parse_local_date,
    parse_local_datetime,
)
from shared_models.scheduling import (
    AppointmentStatus,
    ChangeFailureReason,
    StatusFilter,
    TimeFilter,
    Weekday,
)
from shared_proto.scheduling.v1 import scheduling_pb2 as pb

from scheduler.domain.models import (
    NAME_LENGTH,
    Appointment,
    Patient,
    Practitioner,
    WorkingRange,
)

# The status mapping, declared in both directions rather than derived, so a member
# added to either side without the other fails here rather than travelling as the
# proto3 zero value. Read with `.get()` on ingress: proto3 enums are open, and a
# scheduler deployed ahead of this build sends a number this one has no member for.
_PROTO_BY_APPOINTMENT_STATUS = {
    AppointmentStatus.STANDING: pb.APPOINTMENT_STATUS_STANDING,
    AppointmentStatus.CANCELLED: pb.APPOINTMENT_STATUS_CANCELLED,
}
_APPOINTMENT_STATUS_BY_PROTO: dict[int, AppointmentStatus] = {
    int(proto): status for status, proto in _PROTO_BY_APPOINTMENT_STATUS.items()
}


# One entry per member of each closed set, declared rather than derived: a value added
# on either side without the other fails here, where the mapping is, instead of
# travelling as proto3's zero value.
_PROTO_BY_CHANGE_REASON = {
    ChangeFailureReason.APPOINTMENT_NOT_FOUND: (
        pb.CHANGE_FAILURE_REASON_APPOINTMENT_NOT_FOUND
    ),
    ChangeFailureReason.ALREADY_CANCELLED: pb.CHANGE_FAILURE_REASON_ALREADY_CANCELLED,
    ChangeFailureReason.ALREADY_STARTED: pb.CHANGE_FAILURE_REASON_ALREADY_STARTED,
    ChangeFailureReason.STALE_CONFIRMATION: (
        pb.CHANGE_FAILURE_REASON_STALE_CONFIRMATION
    ),
    ChangeFailureReason.PRACTITIONER_NOT_FOUND: (
        pb.CHANGE_FAILURE_REASON_PRACTITIONER_NOT_FOUND
    ),
    ChangeFailureReason.PATIENT_NOT_FOUND: pb.CHANGE_FAILURE_REASON_PATIENT_NOT_FOUND,
    ChangeFailureReason.IN_PAST: pb.CHANGE_FAILURE_REASON_IN_PAST,
    ChangeFailureReason.BEYOND_HORIZON: pb.CHANGE_FAILURE_REASON_BEYOND_HORIZON,
    ChangeFailureReason.OUTSIDE_SCHEDULE: pb.CHANGE_FAILURE_REASON_OUTSIDE_SCHEDULE,
    ChangeFailureReason.OFF_GRID: pb.CHANGE_FAILURE_REASON_OFF_GRID,
    ChangeFailureReason.PRACTITIONER_BUSY: pb.CHANGE_FAILURE_REASON_PRACTITIONER_BUSY,
    ChangeFailureReason.PATIENT_BUSY: pb.CHANGE_FAILURE_REASON_PATIENT_BUSY,
}

# The zero values are the narrowest corner, so an unset filter can only ever narrow.
_TIME_FILTER_BY_PROTO: dict[int, TimeFilter] = {
    int(pb.TIME_FILTER_FUTURE): TimeFilter.FUTURE,
    int(pb.TIME_FILTER_PAST): TimeFilter.PAST,
    int(pb.TIME_FILTER_BOTH): TimeFilter.BOTH,
}
_STATUS_FILTER_BY_PROTO: dict[int, StatusFilter] = {
    int(pb.STATUS_FILTER_STANDING): StatusFilter.STANDING,
    int(pb.STATUS_FILTER_CANCELLED): StatusFilter.CANCELLED,
    int(pb.STATUS_FILTER_BOTH): StatusFilter.BOTH,
}


class ConversionError(ValueError):
    """Raised when a request field cannot be read as the type the contract promises.

    Always a caller defect - a malformed date-time, an offset where none is allowed, or
    a specialty outside the closed set - so the servicer answers it with
    `INVALID_ARGUMENT` rather than a domain refusal.
    """


def read_local_datetime(value: str, field_name: str) -> datetime:
    """Parse an offset-free ISO-8601 date-time from `field_name`'s wire value.

    Raises: ConversionError if the value is missing, unparseable, or carries any
        timezone offset.
    """
    if not value:
        raise ConversionError(f"{field_name} is required")
    try:
        return parse_local_datetime(value)
    except ValueError as exc:
        raise ConversionError(f"{field_name}: {exc}") from exc


def read_local_date(value: str, field_name: str) -> date:
    """Parse a `YYYY-MM-DD` local date from `field_name`'s wire value.

    Raises: ConversionError if the value is missing or is not a bare local date.
    """
    if not value:
        raise ConversionError(f"{field_name} is required")
    try:
        return parse_local_date(value)
    except ValueError as exc:
        raise ConversionError(f"{field_name}: {exc}") from exc


def read_required_id(value: str, field_name: str) -> str:
    """Return `value`, rejecting an empty id.

    Raises: ConversionError if `value` is empty.
    """
    if not value:
        raise ConversionError(f"{field_name} is required")
    return value


def read_patient_name(value: str) -> str:
    """Return `value`, rejecting a name the patient table could not hold.

    The bounds match `PatientUpdate`'s, so the same name is accepted whichever surface
    it arrives through.

    Raises: ConversionError if the name is empty or longer than the column allows.
    """
    if not value:
        raise ConversionError("full_name is required")
    if len(value) > NAME_LENGTH:
        raise ConversionError(f"full_name must be at most {NAME_LENGTH} characters")
    return value


def to_proto_appointment_status(status: AppointmentStatus) -> "pb.AppointmentStatus":
    """Render an appointment's status onto the wire."""
    return _PROTO_BY_APPOINTMENT_STATUS[status]


def read_appointment_status(value: int) -> AppointmentStatus:
    """Read a wire status into its domain member.

    Raises: ConversionError if the value is unspecified or is one this build has no
        member for.

    The unspecified zero value is rejected rather than defaulted: proto3 sends it for a
    field nobody set, and treating that as standing would silently present a cancelled
    appointment as a live one - and resurrect it into every rule that reads status.
    """
    status = _APPOINTMENT_STATUS_BY_PROTO.get(value)
    if status is None:
        raise ConversionError(f"unrecognized appointment status: {int(value)}")
    return status


def to_proto_working_range(working_range: WorkingRange) -> pb.WorkingRange:
    """Render one working range onto the wire."""
    # The proto enum and `shared_models.Weekday` share their numbering by construction,
    # so the domain value goes onto the wire as the plain int protobuf stores. The cast
    # is for the generated stub, which types the field as its enum wrapper - a wrapper
    # that is not actually callable at runtime.
    return pb.WorkingRange(
        weekday=cast("pb.Weekday", Weekday(working_range.weekday).value),
        start_time=format_local_time(working_range.start_time),
        end_time=format_local_time(working_range.end_time),
    )


def to_proto_practitioner(
    practitioner: Practitioner, schedule: list[WorkingRange]
) -> pb.Practitioner:
    """Render a practitioner and its schedule onto the wire."""
    return pb.Practitioner(
        id=practitioner.id,
        full_name=practitioner.full_name,
        specialty=practitioner.specialty,
        appointment_duration_minutes=practitioner.appointment_duration_minutes,
        schedule=[to_proto_working_range(r) for r in schedule],
    )


def to_proto_patient(patient: Patient) -> pb.Patient:
    """Render a patient onto the wire."""
    return pb.Patient(
        id=patient.id, chat_id=patient.chat_id, full_name=patient.full_name
    )


def to_proto_appointment(
    appointment: Appointment, patient: Patient, practitioner: Practitioner
) -> pb.Appointment:
    """Render an appointment, denormalizing both parties' display fields onto it.

    The caller never joins to resolve a name, so the chat service can build its
    confirmation from the response alone.

    Raises: ConversionError if the appointment's stored status is outside the closed
        set - the `CHECK` constraint makes that a corrupted row rather than a caller
        defect, so it fails loudly instead of travelling as unspecified.
    """
    return pb.Appointment(
        id=appointment.id,
        patient_id=patient.id,
        patient_full_name=patient.full_name,
        practitioner_id=practitioner.id,
        practitioner_full_name=practitioner.full_name,
        practitioner_specialty=practitioner.specialty,
        starts_at=format_local_datetime(appointment.starts_at),
        ends_at=format_local_datetime(appointment.ends_at),
        status=to_proto_appointment_status(_read_stored_status(appointment.status)),
    )


def _read_stored_status(value: str) -> AppointmentStatus:
    """Read a stored status column into its domain member.

    Raises: ConversionError if the column holds a value outside the closed set.
    """
    try:
        return AppointmentStatus(value)
    except ValueError as exc:
        raise ConversionError(f"unrecognized stored status: {value!r}") from exc


def to_proto_change_failure(reason: ChangeFailureReason) -> pb.ChangeFailure:
    """Render one refused change onto the wire.

    `detail` is for logs only - the assistant's explanation to the patient is built
    from `reason`, never from this string.
    """
    return pb.ChangeFailure(reason=_PROTO_BY_CHANGE_REASON[reason], detail=reason.value)


def read_time_filter(value: int) -> TimeFilter:
    """Read a wire time filter into its domain member.

    Raises: ConversionError if the value is one this build has no member for.
    """
    time_filter = _TIME_FILTER_BY_PROTO.get(value)
    if time_filter is None:
        raise ConversionError(f"unrecognized time_filter: {int(value)}")
    return time_filter


def read_status_filter(value: int) -> StatusFilter:
    """Read a wire status filter into its domain member.

    Raises: ConversionError if the value is one this build has no member for.
    """
    status_filter = _STATUS_FILTER_BY_PROTO.get(value)
    if status_filter is None:
        raise ConversionError(f"unrecognized status_filter: {int(value)}")
    return status_filter
