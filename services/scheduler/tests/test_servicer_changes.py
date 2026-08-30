"""Tests for the change RPCs: three outcomes on one response, and what each carries."""

from collections.abc import AsyncIterator
from datetime import datetime, timedelta

import grpc
import pytest
import pytest_asyncio
from scheduler.grpc.interceptors import LoggingInterceptor
from scheduler.grpc.servicer import SchedulingServicer
from shared_models.scheduling import AppointmentStatus
from shared_proto.scheduling.v1 import scheduling_pb2 as pb
from shared_proto.scheduling.v1 import scheduling_pb2_grpc
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import make_appointment, new_id, seed_patient, seed_practitioner

_TUESDAY_9AM = datetime(2026, 8, 18, 9, 0)
_TUESDAY_9AM_WIRE = "2026-08-18T09:00:00"
_LOCAL_NOW = "2026-08-17T08:00:00"


@pytest_asyncio.fixture
async def stub() -> AsyncIterator[scheduling_pb2_grpc.SchedulingStub]:
    server = grpc.aio.server(interceptors=[LoggingInterceptor()])
    scheduling_pb2_grpc.add_SchedulingServicer_to_server(  # type: ignore[no-untyped-call]
        SchedulingServicer(), server
    )
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
        yield scheduling_pb2_grpc.SchedulingStub(channel)  # type: ignore[no-untyped-call]
    await server.stop(0)


class _Booked:
    def __init__(
        self,
        session_id: str,
        patient_id: str,
        practitioner_id: str,
        appointment_id: str,
    ) -> None:
        self.session_id = session_id
        self.patient_id = patient_id
        self.practitioner_id = practitioner_id
        self.appointment_id = appointment_id


async def _seed(
    session: AsyncSession,
    *,
    status: AppointmentStatus = AppointmentStatus.STANDING,
) -> _Booked:
    session_id = new_id()
    practitioner = await seed_practitioner(session, session_id)
    patient = await seed_patient(session, session_id)
    appointment = make_appointment(
        session_id,
        patient.id,
        practitioner.id,
        _TUESDAY_9AM,
        _TUESDAY_9AM + timedelta(hours=1),
        status=status,
    )
    session.add(appointment)
    await session.commit()
    return _Booked(session_id, patient.id, practitioner.id, appointment.id)


def _cancel_request(booked: _Booked, **overrides: str) -> pb.CancelAppointmentRequest:
    fields: dict[str, str] = {
        "session_id": booked.session_id,
        "patient_id": booked.patient_id,
        "appointment_id": booked.appointment_id,
        "expected_starts_at": _TUESDAY_9AM_WIRE,
        "expected_practitioner_id": booked.practitioner_id,
        "local_now": _LOCAL_NOW,
    }
    fields.update(overrides)
    return pb.CancelAppointmentRequest(**fields)


async def test_a_cancellation_answers_with_the_appointment_as_it_now_stands(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    booked = await _seed(db_session)

    response = await stub.CancelAppointment(_cancel_request(booked))

    assert response.WhichOneof("result") == "appointment"
    assert response.appointment.id == booked.appointment_id
    assert response.appointment.status == pb.APPOINTMENT_STATUS_CANCELLED
    assert response.appointment.starts_at == _TUESDAY_9AM_WIRE


async def test_a_cancellation_carries_no_previous_start(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    # `previous_starts_at` exists for a move that actually moved something. A
    # cancellation has no destination, so filling it in would describe the appointment
    # as having been moved to the time it already had.
    booked = await _seed(db_session)

    response = await stub.CancelAppointment(_cancel_request(booked))

    assert response.previous_starts_at == ""
    assert response.previous_practitioner_id == ""


async def test_cancelling_an_already_cancelled_appointment_answers_no_change(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    booked = await _seed(db_session, status=AppointmentStatus.CANCELLED)

    response = await stub.CancelAppointment(_cancel_request(booked))

    assert response.WhichOneof("result") == "no_change"
    assert response.no_change.appointment.id == booked.appointment_id
    assert response.no_change.appointment.status == pb.APPOINTMENT_STATUS_CANCELLED


async def test_a_re_sent_cancellation_answers_no_change_not_a_failure(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    booked = await _seed(db_session)
    first = await stub.CancelAppointment(_cancel_request(booked))
    assert first.WhichOneof("result") == "appointment"

    second = await stub.CancelAppointment(_cancel_request(booked))

    assert second.WhichOneof("result") == "no_change"


async def test_an_appointment_that_never_existed_is_a_typed_failure(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    # An evaluated refusal travels as a successful rpc carrying a failure, exactly as
    # a booking refusal does - gRPC status codes stay reserved for transport.
    booked = await _seed(db_session)

    response = await stub.CancelAppointment(
        _cancel_request(booked, appointment_id=new_id())
    )

    assert response.WhichOneof("result") == "failure"
    assert response.failure.reason == pb.CHANGE_FAILURE_REASON_APPOINTMENT_NOT_FOUND


async def test_another_sessions_appointment_is_reported_as_not_found(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    booked = await _seed(db_session)

    response = await stub.CancelAppointment(
        _cancel_request(booked, session_id=new_id())
    )

    assert response.WhichOneof("result") == "failure"
    assert response.failure.reason == pb.CHANGE_FAILURE_REASON_APPOINTMENT_NOT_FOUND


async def test_a_stale_guard_is_reported_as_a_stale_confirmation(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    booked = await _seed(db_session)

    response = await stub.CancelAppointment(
        _cancel_request(booked, expected_starts_at="2026-08-18T14:00:00")
    )

    assert response.WhichOneof("result") == "failure"
    assert response.failure.reason == pb.CHANGE_FAILURE_REASON_STALE_CONFIRMATION


async def test_an_appointment_already_under_way_is_refused(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    booked = await _seed(db_session)

    response = await stub.CancelAppointment(
        _cancel_request(booked, local_now="2026-08-18T09:30:00")
    )

    assert response.WhichOneof("result") == "failure"
    assert response.failure.reason == pb.CHANGE_FAILURE_REASON_ALREADY_STARTED


async def test_a_missing_required_field_is_an_invalid_argument(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    booked = await _seed(db_session)

    with pytest.raises(grpc.aio.AioRpcError) as caught:
        await stub.CancelAppointment(_cancel_request(booked, expected_starts_at=""))

    assert caught.value.code() is grpc.StatusCode.INVALID_ARGUMENT


async def test_the_failure_detail_carries_the_reason_for_logs(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    booked = await _seed(db_session)

    response = await stub.CancelAppointment(
        _cancel_request(booked, appointment_id=new_id())
    )

    assert response.failure.detail == "appointment_not_found"


# --- RescheduleAppointment ---------------------------------------------------

_TUESDAY_10AM_WIRE = "2026-08-18T10:00:00"


def _reschedule_request(
    booked: _Booked, **overrides: str
) -> pb.RescheduleAppointmentRequest:
    fields: dict[str, str] = {
        "session_id": booked.session_id,
        "patient_id": booked.patient_id,
        "appointment_id": booked.appointment_id,
        "new_starts_at": _TUESDAY_10AM_WIRE,
        "expected_starts_at": _TUESDAY_9AM_WIRE,
        "expected_practitioner_id": booked.practitioner_id,
        "local_now": _LOCAL_NOW,
    }
    fields.update(overrides)
    return pb.RescheduleAppointmentRequest(**fields)


async def test_a_move_answers_with_the_appointment_at_its_new_time(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    booked = await _seed(db_session)

    response = await stub.RescheduleAppointment(_reschedule_request(booked))

    assert response.WhichOneof("result") == "appointment"
    assert response.appointment.id == booked.appointment_id
    assert response.appointment.starts_at == _TUESDAY_10AM_WIRE
    assert response.appointment.status == pb.APPOINTMENT_STATUS_STANDING


async def test_previous_starts_at_accompanies_a_real_move(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    booked = await _seed(db_session)

    response = await stub.RescheduleAppointment(_reschedule_request(booked))

    assert response.previous_starts_at == _TUESDAY_9AM_WIRE
    assert response.previous_practitioner_id == booked.practitioner_id


async def test_a_move_that_transitioned_nothing_answers_no_change(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    booked = await _seed(db_session)

    response = await stub.RescheduleAppointment(
        _reschedule_request(booked, new_starts_at=_TUESDAY_9AM_WIRE)
    )

    assert response.WhichOneof("result") == "no_change"
    assert response.no_change.appointment.starts_at == _TUESDAY_9AM_WIRE
    # No previous fields: nothing moved, so there is no state it came from.
    assert response.previous_starts_at == ""


async def test_a_re_sent_move_answers_no_change_not_a_stale_confirmation(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    # SC-008: quoting the pre-move state on the second send is what a retry looks like,
    # and it must not read as a conflict for a change that succeeded.
    booked = await _seed(db_session)
    first = await stub.RescheduleAppointment(_reschedule_request(booked))
    assert first.WhichOneof("result") == "appointment"

    second = await stub.RescheduleAppointment(_reschedule_request(booked))

    assert second.WhichOneof("result") == "no_change"


async def test_a_refused_move_is_a_typed_failure(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    booked = await _seed(db_session)

    response = await stub.RescheduleAppointment(
        _reschedule_request(booked, new_starts_at="2026-08-18T09:20:00")
    )

    assert response.WhichOneof("result") == "failure"
    assert response.failure.reason == pb.CHANGE_FAILURE_REASON_OFF_GRID


async def test_a_move_of_a_cancelled_appointment_is_refused_not_a_no_change(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    # The asymmetry: for a cancellation that state is the target, for a move it is an
    # ineligibility - there is no un-cancelling by moving.
    booked = await _seed(db_session, status=AppointmentStatus.CANCELLED)

    response = await stub.RescheduleAppointment(_reschedule_request(booked))

    assert response.WhichOneof("result") == "failure"
    assert response.failure.reason == pb.CHANGE_FAILURE_REASON_ALREADY_CANCELLED


async def test_an_empty_new_practitioner_means_keep_the_one_it_has(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    booked = await _seed(db_session)

    response = await stub.RescheduleAppointment(_reschedule_request(booked))

    assert response.appointment.practitioner_id == booked.practitioner_id


async def test_a_move_missing_a_required_field_is_an_invalid_argument(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    booked = await _seed(db_session)

    with pytest.raises(grpc.aio.AioRpcError) as caught:
        await stub.RescheduleAppointment(
            _reschedule_request(booked, new_starts_at="")
        )

    assert caught.value.code() is grpc.StatusCode.INVALID_ARGUMENT


async def test_a_swap_carries_both_the_previous_id_and_the_previous_name(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    booked = await _seed(db_session)
    other = await seed_practitioner(
        db_session, booked.session_id, full_name="Dr B", duration_minutes=30
    )

    response = await stub.RescheduleAppointment(
        _reschedule_request(booked, new_practitioner_id=other.id)
    )

    assert response.WhichOneof("result") == "appointment"
    assert response.appointment.practitioner_id == other.id
    assert response.appointment.practitioner_full_name == "Dr B"
    assert response.previous_practitioner_id == booked.practitioner_id
    assert response.previous_practitioner_full_name == "Dr A"


async def test_a_swap_recomputes_the_end_from_the_new_practitioner(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    booked = await _seed(db_session)
    other = await seed_practitioner(
        db_session, booked.session_id, full_name="Dr B", duration_minutes=30
    )

    response = await stub.RescheduleAppointment(
        _reschedule_request(booked, new_practitioner_id=other.id)
    )

    assert response.appointment.starts_at == _TUESDAY_10AM_WIRE
    assert response.appointment.ends_at == "2026-08-18T10:30:00"


async def test_an_unknown_new_practitioner_is_a_typed_failure(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    booked = await _seed(db_session)

    response = await stub.RescheduleAppointment(
        _reschedule_request(booked, new_practitioner_id=new_id())
    )

    assert response.WhichOneof("result") == "failure"
    assert response.failure.reason == pb.CHANGE_FAILURE_REASON_PRACTITIONER_NOT_FOUND


async def test_a_move_that_kept_its_practitioner_names_that_same_one(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    booked = await _seed(db_session)

    response = await stub.RescheduleAppointment(_reschedule_request(booked))

    assert response.previous_practitioner_id == booked.practitioner_id
    assert response.previous_practitioner_full_name == "Dr A"


async def test_an_appointment_that_vanished_under_a_change_is_not_a_typed_failure(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    """It completes as UNKNOWN, which is the honest answer and the safe one.

    A typed failure would say the change was evaluated and declined; a `no_change`
    would say the appointment was already in that state. Both are false - the change
    committed. UNKNOWN carries no claim either way, and the chat client turns a
    non-retryable status into an unknown outcome, which is what the patient is told.
    """
    from unittest.mock import AsyncMock, patch

    from scheduler.repositories import appointment_repository

    booked = await _seed(db_session)

    with (
        patch.object(
            appointment_repository,
            "_load_change_context",
            new=AsyncMock(return_value=None),
        ),
        pytest.raises(grpc.aio.AioRpcError) as caught,
    ):
        await stub.CancelAppointment(_cancel_request(booked))

    assert caught.value.code() is grpc.StatusCode.UNKNOWN


async def test_another_patients_appointment_is_not_found_over_the_wire(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    # SC-014 end to end: same session, different patient, and the answer is the typed
    # failure an unknown appointment gets - not a leak that one exists.
    booked = await _seed(db_session)
    intruder = await seed_patient(db_session, booked.session_id, full_name="Bram")

    response = await stub.CancelAppointment(
        _cancel_request(booked, patient_id=intruder.id)
    )

    assert response.WhichOneof("result") == "failure"
    assert response.failure.reason == pb.CHANGE_FAILURE_REASON_APPOINTMENT_NOT_FOUND
