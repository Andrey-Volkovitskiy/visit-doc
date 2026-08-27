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
from shared_models.scheduling import Weekday
from shared_proto.scheduling.v1 import scheduling_pb2 as pb

from scheduler.domain.models import (
    NAME_LENGTH,
    Appointment,
    Patient,
    Practitioner,
    WorkingRange,
)


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
    )
