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
