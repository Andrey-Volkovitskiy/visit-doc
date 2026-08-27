import shared_proto
from shared_proto.scheduling.v1 import scheduling_pb2 as pb
from shared_proto.scheduling.v1 import scheduling_pb2_grpc as pb_grpc

_EXPECTED_RPCS = {
    "EnsureSessionProvisioned",
    "RenamePatient",
    "ListPractitioners",
    "CheckAvailability",
    "BookAppointment",
    "ListUpcomingAppointments",
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
