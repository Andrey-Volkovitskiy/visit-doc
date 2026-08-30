"""Tests for the wire<->domain translations that carry an appointment's status."""

from datetime import datetime

import pytest
from scheduler.grpc import converters
from scheduler.grpc.converters import ConversionError
from shared_models.scheduling import AppointmentStatus
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
