"""Tests for the wire<->domain translations of an appointment's status and a weekday."""

from datetime import datetime, time
from typing import cast

import pytest
from scheduler.domain.models import WorkingRange
from scheduler.grpc import converters
from scheduler.grpc.converters import ConversionError, StoredStateError
from shared_models.scheduling import AppointmentStatus, Weekday
from shared_proto.scheduling.v1 import scheduling_pb2 as pb

from .conftest import make_appointment, make_patient, make_practitioner, new_id


def _rendered(status: AppointmentStatus) -> pb.Appointment:
    session_id = new_id()
    patient = make_patient(session_id)
    practitioner = make_practitioner(session_id)
    appointment = make_appointment(
        session_id,
        patient.id,
        practitioner.id,
        datetime(2026, 9, 2, 9, 0),
        datetime(2026, 9, 2, 10, 0),
        status=status,
    )
    return converters.to_proto_appointment(appointment, patient, practitioner)


def test_a_standing_appointment_is_rendered_as_standing() -> None:
    assert _rendered(AppointmentStatus.STANDING).status == (
        pb.APPOINTMENT_STATUS_STANDING
    )


def test_a_cancelled_appointment_is_rendered_as_cancelled() -> None:
    # FR-015: a cancelled appointment is identified as cancelled wherever it appears,
    # so a caller never has to infer status from absence.
    assert _rendered(AppointmentStatus.CANCELLED).status == (
        pb.APPOINTMENT_STATUS_CANCELLED
    )


def test_no_appointment_is_ever_rendered_unspecified() -> None:
    for status in AppointmentStatus:
        assert _rendered(status).status != pb.APPOINTMENT_STATUS_UNSPECIFIED


@pytest.mark.parametrize(
    ("wire", "expected"),
    [
        (pb.APPOINTMENT_STATUS_STANDING, AppointmentStatus.STANDING),
        (pb.APPOINTMENT_STATUS_CANCELLED, AppointmentStatus.CANCELLED),
    ],
)
def test_a_named_status_reads_back_as_its_domain_member(
    wire: int, expected: AppointmentStatus
) -> None:
    assert converters.read_appointment_status(wire) is expected


def test_an_unspecified_status_is_rejected_rather_than_read_as_standing() -> None:
    # proto3's unavoidable zero value must not become the safe-looking answer:
    # defaulting it to standing would silently resurrect a cancelled appointment into
    # every rule that reads status.
    with pytest.raises(ConversionError):
        converters.read_appointment_status(pb.APPOINTMENT_STATUS_UNSPECIFIED)


def test_an_unknown_status_number_is_rejected() -> None:
    # proto3 enums are open: a scheduler deployed ahead of this build sends a number
    # this one has no member for, and guessing which is worse than failing.
    with pytest.raises(ConversionError):
        converters.read_appointment_status(99)


def test_the_status_mapping_is_total_over_the_domain_enum() -> None:
    for status in AppointmentStatus:
        assert (
            converters.read_appointment_status(
                converters.to_proto_appointment_status(status)
            )
            is status
        )


def test_a_corrupt_stored_status_is_not_a_caller_defect() -> None:
    """Rendering failures on a committed write must not become INVALID_ARGUMENT.

    `to_proto_appointment` runs *after* `cancel()`/`reschedule()` has committed. The
    interceptor turns every `ConversionError` into INVALID_ARGUMENT, which the chat
    client reads as "this service sent something the contract forbids" and both change
    tools answer with "nothing was changed. The appointment still stands exactly as it
    was" - a flat denial of a change that happened.

    So a stored value this build cannot read must surface as a server fault, which
    completes as UNKNOWN and reaches the patient as an unknown outcome.
    """
    session_id = new_id()
    patient = make_patient(session_id)
    practitioner = make_practitioner(session_id)
    appointment = make_appointment(
        session_id,
        patient.id,
        practitioner.id,
        datetime(2026, 9, 2, 9, 0),
        datetime(2026, 9, 2, 10, 0),
    )
    appointment.status = "rescheduled-away"  # outside the closed set

    with pytest.raises(Exception) as caught:
        converters.to_proto_appointment(appointment, patient, practitioner)

    assert not isinstance(caught.value, ConversionError)


def test_a_malformed_request_field_is_still_a_caller_defect() -> None:
    # Unchanged, and the distinction the test above rests on: reading a *request* field
    # this build cannot parse is the caller's fault and stays INVALID_ARGUMENT.
    with pytest.raises(ConversionError):
        converters.read_local_datetime("not a date", "starts_at")
    with pytest.raises(ConversionError):
        converters.read_appointment_status(pb.APPOINTMENT_STATUS_UNSPECIFIED)


def _working_range(weekday: Weekday) -> WorkingRange:
    return WorkingRange(
        id=new_id(),
        practitioner_id=new_id(),
        weekday=weekday,
        start_time=time(9, 0),
        end_time=time(17, 0),
    )


@pytest.mark.parametrize(
    ("domain", "wire"),
    [
        (Weekday.MONDAY, pb.WEEKDAY_MONDAY),
        (Weekday.TUESDAY, pb.WEEKDAY_TUESDAY),
        (Weekday.WEDNESDAY, pb.WEEKDAY_WEDNESDAY),
        (Weekday.THURSDAY, pb.WEEKDAY_THURSDAY),
        (Weekday.FRIDAY, pb.WEEKDAY_FRIDAY),
        (Weekday.SATURDAY, pb.WEEKDAY_SATURDAY),
        (Weekday.SUNDAY, pb.WEEKDAY_SUNDAY),
    ],
)
def test_each_stored_weekday_is_rendered_as_its_named_wire_value(
    domain: Weekday, wire: int
) -> None:
    # The stored numbering is Python's `date.weekday()`; the wire's is one ahead,
    # because zero there is the unset sentinel. The mapping is what keeps them apart,
    # so every day is pinned rather than assumed to pass through.
    assert converters.to_proto_weekday(domain) == wire
    assert converters.to_proto_working_range(_working_range(domain)).weekday == wire


def test_no_working_range_is_ever_rendered_with_an_unspecified_weekday() -> None:
    # Zero on the wire means "nobody set this". A stored Monday rendered as zero would
    # be indistinguishable from a range that lost its weekday in transit, and the
    # reader could only guess - which is how a practitioner ends up presented as
    # working hours they do not work.
    for weekday in Weekday:
        rendered = converters.to_proto_working_range(_working_range(weekday))
        assert rendered.weekday != pb.WEEKDAY_UNSPECIFIED


def test_a_stored_weekday_outside_the_closed_set_is_not_a_caller_defect() -> None:
    # The column is a plain SmallInteger, so a row written by something other than this
    # service can carry a number no day maps to. It must not render as zero ("unset")
    # nor travel raw, and - like a corrupt stored status - it is not the caller's
    # doing, so it must not become INVALID_ARGUMENT.
    with pytest.raises(StoredStateError):
        converters.to_proto_working_range(_working_range(cast("Weekday", 9)))
