"""Tests for `ListAppointments`: the four corners, and whose appointments they are."""

from collections.abc import AsyncIterator
from datetime import datetime, timedelta

import grpc
import pytest
import pytest_asyncio
from scheduler.grpc.interceptors import LoggingInterceptor
from scheduler.grpc.servicer import SchedulingServicer
from shared_models.scheduling import AppointmentStatus, NotFoundEntity
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
    status: AppointmentStatus = AppointmentStatus.STANDING,
) -> str:
    """Write an appointment straight to the table, bypassing the booking rules.

    Lets a test place one in the past, or already cancelled, which the booking path
    would refuse.

    Returns: the new appointment's id.
    """
    appointment = make_appointment(
        session_id,
        patient_id,
        practitioner_id,
        starts_at,
        starts_at + timedelta(hours=1),
        status=status,
    )
    session.add(appointment)
    await session.commit()
    return appointment.id


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

    response = await stub.ListAppointments(
        pb.ListAppointmentsRequest(
            session_id=session_id, patient_id=patient.id, local_now=_LOCAL_NOW
        )
    )

    assert [a.starts_at for a in response.future] == ["2026-08-18T09:00:00"]


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

    response = await stub.ListAppointments(
        pb.ListAppointmentsRequest(
            session_id=session_id, patient_id=patient.id, local_now=_LOCAL_NOW
        )
    )

    starts = [a.starts_at for a in response.future]
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

    listed = await stub.ListAppointments(
        pb.ListAppointmentsRequest(
            session_id=session_id, patient_id=patient.id, local_now=mid_appointment
        )
    )
    assert list(listed.future) == []

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

    response = await stub.ListAppointments(
        pb.ListAppointmentsRequest(
            session_id=session_id, patient_id=mine.id, local_now=_LOCAL_NOW
        )
    )

    assert list(response.future) == []


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
        await stub.ListAppointments(
            pb.ListAppointmentsRequest(
                session_id=new_id(), patient_id=patient.id, local_now=_LOCAL_NOW
            )
        )

    assert caught.value.code() is grpc.StatusCode.NOT_FOUND
    assert caught.value.details() == NotFoundEntity.PATIENT.value


async def test_an_unknown_patient_is_not_found_rather_than_empty(
    stub: scheduling_pb2_grpc.SchedulingStub,
) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as caught:
        await stub.ListAppointments(
            pb.ListAppointmentsRequest(
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

    response = await stub.ListAppointments(
        pb.ListAppointmentsRequest(
            session_id=session_id, patient_id=patient.id, local_now=_LOCAL_NOW
        )
    )

    assert list(response.future) == []


async def test_a_listed_appointment_carries_both_parties_names(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id, full_name="Osler")
    patient = await seed_patient(db_session, session_id, full_name="Ada")
    await _book_directly(
        db_session, session_id, patient.id, practitioner.id, _TUESDAY_9AM
    )

    response = await stub.ListAppointments(
        pb.ListAppointmentsRequest(
            session_id=session_id, patient_id=patient.id, local_now=_LOCAL_NOW
        )
    )

    listed = response.future[0]
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

    response = await stub.ListAppointments(
        pb.ListAppointmentsRequest(
            session_id=session_id,
            patient_id=patient.id,
            local_now="2030-01-01T00:00:00",
        )
    )

    assert list(response.future) == []


async def test_an_unset_filter_pair_yields_the_future_standing_corner(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    # The proto3 zero values are the narrowest corner deliberately, so a caller that
    # forgets to set the filters gets the safe answer, never a patient's cancelled
    # history.
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)
    standing = await _book_directly(
        db_session, session_id, patient.id, practitioner.id, _TUESDAY_9AM
    )
    await _book_directly(
        db_session,
        session_id,
        patient.id,
        practitioner.id,
        _TUESDAY_9AM + timedelta(days=1),
        AppointmentStatus.CANCELLED,
    )
    await _book_directly(
        db_session,
        session_id,
        patient.id,
        practitioner.id,
        datetime(2026, 8, 10, 9, 0),
    )

    response = await stub.ListAppointments(
        pb.ListAppointmentsRequest(
            session_id=session_id, patient_id=patient.id, local_now=_LOCAL_NOW
        )
    )

    assert [a.id for a in response.future] == [standing]
    assert list(response.past) == []
    assert response.past_truncated is False


async def test_each_filter_combination_maps_to_the_right_legs(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)
    future_standing = await _book_directly(
        db_session, session_id, patient.id, practitioner.id, _TUESDAY_9AM
    )
    future_cancelled = await _book_directly(
        db_session,
        session_id,
        patient.id,
        practitioner.id,
        _TUESDAY_9AM + timedelta(days=1),
        AppointmentStatus.CANCELLED,
    )
    past_standing = await _book_directly(
        db_session, session_id, patient.id, practitioner.id, datetime(2026, 8, 10, 9, 0)
    )
    past_cancelled = await _book_directly(
        db_session,
        session_id,
        patient.id,
        practitioner.id,
        datetime(2026, 8, 11, 9, 0),
        AppointmentStatus.CANCELLED,
    )

    async def corner(
        time_filter: int, status_filter: int
    ) -> pb.ListAppointmentsResponse:
        return await stub.ListAppointments(
            pb.ListAppointmentsRequest(
                session_id=session_id,
                patient_id=patient.id,
                local_now=_LOCAL_NOW,
                time_filter=time_filter,
                status_filter=status_filter,
            )
        )

    future_st = await corner(pb.TIME_FILTER_FUTURE, pb.STATUS_FILTER_STANDING)
    assert [a.id for a in future_st.future] == [future_standing]
    assert list(future_st.past) == []

    future_ca = await corner(pb.TIME_FILTER_FUTURE, pb.STATUS_FILTER_CANCELLED)
    assert [a.id for a in future_ca.future] == [future_cancelled]

    past_st = await corner(pb.TIME_FILTER_PAST, pb.STATUS_FILTER_STANDING)
    assert [a.id for a in past_st.past] == [past_standing]
    assert list(past_st.future) == []

    past_ca = await corner(pb.TIME_FILTER_PAST, pb.STATUS_FILTER_CANCELLED)
    assert [a.id for a in past_ca.past] == [past_cancelled]

    everything = await corner(pb.TIME_FILTER_BOTH, pb.STATUS_FILTER_BOTH)
    assert {a.id for a in everything.future} == {future_standing, future_cancelled}
    assert {a.id for a in everything.past} == {past_standing, past_cancelled}


async def test_every_listed_appointment_carries_its_status(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    # FR-015: a cancelled appointment is identified as cancelled wherever it appears.
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
        _TUESDAY_9AM + timedelta(days=1),
        AppointmentStatus.CANCELLED,
    )

    response = await stub.ListAppointments(
        pb.ListAppointmentsRequest(
            session_id=session_id,
            patient_id=patient.id,
            local_now=_LOCAL_NOW,
            status_filter=pb.STATUS_FILTER_BOTH,
        )
    )

    assert [a.status for a in response.future] == [
        pb.APPOINTMENT_STATUS_STANDING,
        pb.APPOINTMENT_STATUS_CANCELLED,
    ]


async def test_an_unresolvable_patient_aborts_rather_than_returning_two_empty_legs(
    stub: scheduling_pb2_grpc.SchedulingStub,
) -> None:
    # Two empty legs mean the patient exists and has nothing matching. One value must
    # not stand for that and for "no such patient" at once.
    with pytest.raises(grpc.aio.AioRpcError) as caught:
        await stub.ListAppointments(
            pb.ListAppointmentsRequest(
                session_id=new_id(),
                patient_id=new_id(),
                local_now=_LOCAL_NOW,
                time_filter=pb.TIME_FILTER_BOTH,
                status_filter=pb.STATUS_FILTER_BOTH,
            )
        )

    assert caught.value.code() is grpc.StatusCode.NOT_FOUND
    assert caught.value.details() == NotFoundEntity.PATIENT.value


async def test_the_past_leg_reports_when_it_elided_some(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    from scheduler.repositories.appointment_repository import PAST_LEG_LIMIT

    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)
    for day in range(1, PAST_LEG_LIMIT + 3):
        await _book_directly(
            db_session,
            session_id,
            patient.id,
            practitioner.id,
            datetime(2026, 8, 17, 8, 0) - timedelta(days=day),
        )

    response = await stub.ListAppointments(
        pb.ListAppointmentsRequest(
            session_id=session_id,
            patient_id=patient.id,
            local_now=_LOCAL_NOW,
            time_filter=pb.TIME_FILTER_PAST,
        )
    )

    assert len(response.past) == PAST_LEG_LIMIT
    assert response.past_truncated is True
