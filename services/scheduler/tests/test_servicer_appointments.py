"""Tests for `ListUpcomingAppointments`: what counts as upcoming, and whose it is."""

from collections.abc import AsyncIterator
from datetime import datetime, timedelta

import grpc
import pytest
import pytest_asyncio
from scheduler.grpc.interceptors import LoggingInterceptor
from scheduler.grpc.servicer import SchedulingServicer
from shared_models.scheduling import NotFoundEntity
from shared_proto.scheduling.v1 import scheduling_pb2 as pb
from shared_proto.scheduling.v1 import scheduling_pb2_grpc
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import make_appointment, new_id, seed_patient, seed_practitioner

_LOCAL_NOW = "2026-08-17T08:00:00"
_TUESDAY_9AM = datetime(2026, 8, 18, 9, 0)


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


async def _book_directly(
    session: AsyncSession,
    session_id: str,
    patient_id: str,
    practitioner_id: str,
    starts_at: datetime,
) -> None:
    """Write an appointment straight to the table, bypassing the booking rules.

    Lets a test place one in the past, which the booking path would refuse.
    """
    session.add(
        make_appointment(
            session_id,
            patient_id,
            practitioner_id,
            starts_at,
            starts_at + timedelta(hours=1),
        )
    )
    await session.commit()


async def test_only_appointments_starting_after_local_now_are_listed(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)
    await _book_directly(
        db_session, session_id, patient.id, practitioner.id, _TUESDAY_9AM
    )
    await _book_directly(
        db_session,
        session_id,
        patient.id,
        practitioner.id,
        datetime(2026, 8, 10, 9, 0),
    )

    response = await stub.ListUpcomingAppointments(
        pb.ListUpcomingAppointmentsRequest(
            session_id=session_id, patient_id=patient.id, local_now=_LOCAL_NOW
        )
    )

    assert [a.starts_at for a in response.appointments] == ["2026-08-18T09:00:00"]


async def test_they_are_listed_earliest_first(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)
    for offset in (2, 0, 1):
        await _book_directly(
            db_session,
            session_id,
            patient.id,
            practitioner.id,
            _TUESDAY_9AM + timedelta(days=offset),
        )

    response = await stub.ListUpcomingAppointments(
        pb.ListUpcomingAppointmentsRequest(
            session_id=session_id, patient_id=patient.id, local_now=_LOCAL_NOW
        )
    )

    starts = [a.starts_at for a in response.appointments]
    assert starts == sorted(starts)


async def test_an_appointment_already_under_way_is_absent_but_still_blocks_a_booking(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    """Two different questions: what is still to come, and what the slot holds."""
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)
    other = await seed_patient(db_session, session_id, full_name="Bram")
    started_at = datetime(2026, 8, 18, 9, 0)
    await _book_directly(
        db_session, session_id, patient.id, practitioner.id, started_at
    )
    # A clock reading half-way through that appointment.
    mid_appointment = "2026-08-18T09:30:00"

    listed = await stub.ListUpcomingAppointments(
        pb.ListUpcomingAppointmentsRequest(
            session_id=session_id, patient_id=patient.id, local_now=mid_appointment
        )
    )
    assert list(listed.appointments) == []

    # A later booking that overlaps it is still refused by the constraint.
    booked = await stub.BookAppointment(
        pb.BookAppointmentRequest(
            session_id=session_id,
            patient_id=other.id,
            practitioner_id=practitioner.id,
            starts_at="2026-08-18T09:00:00",
            local_now="2026-08-17T08:00:00",
            idempotency_key=new_id(),
        )
    )
    assert booked.WhichOneof("result") == "failure"
    assert booked.failure.reason == pb.BOOKING_FAILURE_REASON_PRACTITIONER_BUSY


async def test_another_patients_appointments_are_never_listed(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    mine = await seed_patient(db_session, session_id, full_name="Ada")
    theirs = await seed_patient(db_session, session_id, full_name="Bram")
    await _book_directly(
        db_session, session_id, theirs.id, practitioner.id, _TUESDAY_9AM
    )

    response = await stub.ListUpcomingAppointments(
        pb.ListUpcomingAppointmentsRequest(
            session_id=session_id, patient_id=mine.id, local_now=_LOCAL_NOW
        )
    )

    assert list(response.appointments) == []


async def test_another_sessions_patient_is_not_found_rather_than_empty(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    """An empty list already means "this patient has nothing upcoming".

    Answering a patient that does not resolve with the same value would leave the
    caller unable to tell the two apart - and it reads to a patient who does have
    appointments as being told they have none.
    """
    other_session = new_id()
    practitioner = await seed_practitioner(db_session, other_session)
    patient = await seed_patient(db_session, other_session)
    await _book_directly(
        db_session, other_session, patient.id, practitioner.id, _TUESDAY_9AM
    )

    with pytest.raises(grpc.aio.AioRpcError) as caught:
        await stub.ListUpcomingAppointments(
            pb.ListUpcomingAppointmentsRequest(
                session_id=new_id(), patient_id=patient.id, local_now=_LOCAL_NOW
            )
        )

    assert caught.value.code() is grpc.StatusCode.NOT_FOUND
    assert caught.value.details() == NotFoundEntity.PATIENT.value


async def test_an_unknown_patient_is_not_found_rather_than_empty(
    stub: scheduling_pb2_grpc.SchedulingStub,
) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as caught:
        await stub.ListUpcomingAppointments(
            pb.ListUpcomingAppointmentsRequest(
                session_id=new_id(), patient_id=new_id(), local_now=_LOCAL_NOW
            )
        )

    assert caught.value.code() is grpc.StatusCode.NOT_FOUND
    assert caught.value.details() == NotFoundEntity.PATIENT.value


async def test_a_patient_with_nothing_booked_gets_an_empty_list_not_an_error(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    session_id = new_id()
    patient = await seed_patient(db_session, session_id)

    response = await stub.ListUpcomingAppointments(
        pb.ListUpcomingAppointmentsRequest(
            session_id=session_id, patient_id=patient.id, local_now=_LOCAL_NOW
        )
    )

    assert list(response.appointments) == []


async def test_a_listed_appointment_carries_both_parties_names(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id, full_name="Osler")
    patient = await seed_patient(db_session, session_id, full_name="Ada")
    await _book_directly(
        db_session, session_id, patient.id, practitioner.id, _TUESDAY_9AM
    )

    response = await stub.ListUpcomingAppointments(
        pb.ListUpcomingAppointmentsRequest(
            session_id=session_id, patient_id=patient.id, local_now=_LOCAL_NOW
        )
    )

    listed = response.appointments[0]
    assert listed.practitioner_full_name == "Osler"
    assert listed.patient_full_name == "Ada"
    assert listed.practitioner_specialty == "General Practice"
    assert listed.ends_at == "2026-08-18T10:00:00"


async def test_the_callers_clock_decides_and_not_the_servers(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    """A clock set far past the appointment must empty the list, whatever day it is."""
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)
    await _book_directly(
        db_session, session_id, patient.id, practitioner.id, _TUESDAY_9AM
    )

    response = await stub.ListUpcomingAppointments(
        pb.ListUpcomingAppointmentsRequest(
            session_id=session_id,
            patient_id=patient.id,
            local_now="2030-01-01T00:00:00",
        )
    )

    assert list(response.appointments) == []
