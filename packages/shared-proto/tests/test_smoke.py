import shared_proto
from shared_proto.scheduling.v1 import scheduling_pb2 as pb
from shared_proto.scheduling.v1 import scheduling_pb2_grpc as pb_grpc

_EXPECTED_RPCS = {
    "EnsureSessionProvisioned",
    "RenamePatient",
    "ListPractitioners",
    "CheckAvailability",
    "BookAppointment",
    "ListAppointments",
    "RescheduleAppointment",
    "CancelAppointment",
    "DeletePatientForChat",
}


def test_package_imports() -> None:
    assert shared_proto is not None


def test_servicer_exposes_every_declared_rpc() -> None:
    declared = {name for name in dir(pb_grpc.SchedulingServicer) if name[0].isupper()}
    assert declared == _EXPECTED_RPCS


def test_service_descriptor_declares_every_rpc() -> None:
    service = pb.DESCRIPTOR.services_by_name["Scheduling"]
    assert set(service.methods_by_name) == _EXPECTED_RPCS


def test_check_availability_request_carries_patient_id() -> None:
    fields = pb.CheckAvailabilityRequest.DESCRIPTOR.fields_by_name
    assert "patient_id" in fields
    assert "local_now" in fields


def test_book_appointment_response_is_a_oneof_over_appointment_and_failure() -> None:
    oneofs = pb.BookAppointmentResponse.DESCRIPTOR.oneofs_by_name
    assert set(oneofs) == {"result"}
    assert {f.name for f in oneofs["result"].fields} == {"appointment", "failure"}
    assert "idempotent_replay" in pb.BookAppointmentResponse.DESCRIPTOR.fields_by_name


def test_booking_failure_reason_declares_the_closed_set_of_eight() -> None:
    values = {v.name for v in pb.BookingFailureReason.DESCRIPTOR.values}
    assert values == {
        "BOOKING_FAILURE_REASON_UNSPECIFIED",
        "BOOKING_FAILURE_REASON_PRACTITIONER_BUSY",
        "BOOKING_FAILURE_REASON_PATIENT_BUSY",
        "BOOKING_FAILURE_REASON_OUTSIDE_SCHEDULE",
        "BOOKING_FAILURE_REASON_OFF_GRID",
        "BOOKING_FAILURE_REASON_IN_PAST",
        "BOOKING_FAILURE_REASON_BEYOND_HORIZON",
        "BOOKING_FAILURE_REASON_PRACTITIONER_NOT_FOUND",
        "BOOKING_FAILURE_REASON_PATIENT_NOT_FOUND",
    }


def test_specialty_travels_as_a_validated_string_not_a_proto_enum() -> None:
    field = pb.Practitioner.DESCRIPTOR.fields_by_name["specialty"]
    assert field.type == field.TYPE_STRING


def test_rename_patient_response_is_a_oneof_over_patient_and_failure() -> None:
    oneofs = pb.RenamePatientResponse.DESCRIPTOR.oneofs_by_name
    assert set(oneofs) == {"result"}
    assert {f.name for f in oneofs["result"].fields} == {"patient", "failure"}


def test_rename_failure_reason_declares_its_closed_set() -> None:
    values = {v.name for v in pb.RenameFailureReason.DESCRIPTOR.values}
    assert values == {
        "RENAME_FAILURE_REASON_UNSPECIFIED",
        "RENAME_FAILURE_REASON_NAME_TAKEN",
        "RENAME_FAILURE_REASON_PATIENT_NOT_FOUND",
    }


def test_change_and_listing_messages_all_import() -> None:
    # One assertion per name the 006 delta adds, so a stub regenerated from a
    # half-applied proto names the message it is missing.
    for message in (
        pb.ChangeFailure,
        pb.NoChange,
        pb.ChangeAppointmentResponse,
        pb.RescheduleAppointmentRequest,
        pb.CancelAppointmentRequest,
        pb.ListAppointmentsRequest,
        pb.ListAppointmentsResponse,
    ):
        assert message.DESCRIPTOR is not None
    for enum in (
        pb.AppointmentStatus,
        pb.ChangeFailureReason,
        pb.TimeFilter,
        pb.StatusFilter,
    ):
        assert enum.DESCRIPTOR is not None


def test_appointment_carries_status() -> None:
    assert "status" in pb.Appointment.DESCRIPTOR.fields_by_name


def test_appointment_status_declares_its_closed_set() -> None:
    values = {v.name for v in pb.AppointmentStatus.DESCRIPTOR.values}
    assert values == {
        "APPOINTMENT_STATUS_UNSPECIFIED",
        "APPOINTMENT_STATUS_STANDING",
        "APPOINTMENT_STATUS_CANCELLED",
    }


def test_change_failure_reason_declares_the_closed_set_of_twelve() -> None:
    values = {v.name for v in pb.ChangeFailureReason.DESCRIPTOR.values}
    assert values == {
        "CHANGE_FAILURE_REASON_UNSPECIFIED",
        "CHANGE_FAILURE_REASON_APPOINTMENT_NOT_FOUND",
        "CHANGE_FAILURE_REASON_ALREADY_CANCELLED",
        "CHANGE_FAILURE_REASON_ALREADY_STARTED",
        "CHANGE_FAILURE_REASON_STALE_CONFIRMATION",
        "CHANGE_FAILURE_REASON_PRACTITIONER_NOT_FOUND",
        "CHANGE_FAILURE_REASON_PATIENT_NOT_FOUND",
        "CHANGE_FAILURE_REASON_IN_PAST",
        "CHANGE_FAILURE_REASON_BEYOND_HORIZON",
        "CHANGE_FAILURE_REASON_OUTSIDE_SCHEDULE",
        "CHANGE_FAILURE_REASON_OFF_GRID",
        "CHANGE_FAILURE_REASON_PRACTITIONER_BUSY",
        "CHANGE_FAILURE_REASON_PATIENT_BUSY",
    }


def test_change_response_is_a_oneof_over_the_three_outcomes() -> None:
    oneofs = pb.ChangeAppointmentResponse.DESCRIPTOR.oneofs_by_name
    assert set(oneofs) == {"result"}
    assert {f.name for f in oneofs["result"].fields} == {
        "appointment",
        "no_change",
        "failure",
    }
    fields = pb.ChangeAppointmentResponse.DESCRIPTOR.fields_by_name
    # Outside the oneof deliberately: both accompany `appointment`, they do not
    # replace it.
    assert "previous_starts_at" in fields
    assert "previous_practitioner_id" in fields


def test_both_change_requests_carry_the_two_guard_fields() -> None:
    for request in (pb.RescheduleAppointmentRequest, pb.CancelAppointmentRequest):
        fields = request.DESCRIPTOR.fields_by_name
        assert "expected_starts_at" in fields
        assert "expected_practitioner_id" in fields
        assert "local_now" in fields
        assert "session_id" in fields


def test_reschedule_request_carries_a_destination_and_cancel_does_not() -> None:
    reschedule = pb.RescheduleAppointmentRequest.DESCRIPTOR.fields_by_name
    assert "new_starts_at" in reschedule
    assert "new_practitioner_id" in reschedule
    cancel = pb.CancelAppointmentRequest.DESCRIPTOR.fields_by_name
    assert "new_starts_at" not in cancel


def test_neither_change_request_carries_an_idempotency_key() -> None:
    # FR-020: a key exists to stop a second row coming into being, and neither
    # operation can create one. A key derived from the target state would replay the
    # first move on the third of 09:00 -> 10:00 -> 09:00 -> 10:00.
    for request in (pb.RescheduleAppointmentRequest, pb.CancelAppointmentRequest):
        assert "idempotency_key" not in request.DESCRIPTOR.fields_by_name


def test_filter_zero_values_are_the_narrowest_corner() -> None:
    # proto3 gives every enum an unavoidable default, so an unset filter can only
    # ever narrow, never widen (FR-014, research #11).
    assert pb.TimeFilter.DESCRIPTOR.values_by_number[0].name == "TIME_FILTER_FUTURE"
    assert (
        pb.StatusFilter.DESCRIPTOR.values_by_number[0].name == "STATUS_FILTER_STANDING"
    )


def test_listing_response_carries_two_separate_legs() -> None:
    fields = pb.ListAppointmentsResponse.DESCRIPTOR.fields_by_name
    assert {"future", "past", "past_truncated"} <= set(fields)
    assert fields["future"].is_repeated
    assert fields["past"].is_repeated


def test_check_availability_request_carries_excluded_appointment_id() -> None:
    fields = pb.CheckAvailabilityRequest.DESCRIPTOR.fields_by_name
    assert "excluded_appointment_id" in fields


def test_list_upcoming_appointments_is_gone_from_the_contract() -> None:
    # Removed rather than kept alongside `ListAppointments`: two RPCs answering one
    # question by different axes is how a caller ends up asking the one that quietly
    # omits cancelled appointments from a listing that asked for them.
    service = pb.DESCRIPTOR.services_by_name["Scheduling"]
    assert "ListUpcomingAppointments" not in service.methods_by_name
    assert not hasattr(pb, "ListUpcomingAppointmentsRequest")
    assert not hasattr(pb, "ListUpcomingAppointmentsResponse")
