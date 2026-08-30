"""Handing an appointment to a different practitioner, end to end.

The appointment changes hands: practitioner, start and end move together in one write,
the identifier survives, and the new end comes from the practitioner who will actually
hold it - so an appointment can come out of a swap shorter or longer than it went in.
"""

from datetime import date, datetime, timedelta

import grpc
from chat.agent.tools.scheduling_tools import derive_idempotency_key
from chat.clients import scheduling
from scheduler.db.session import session_factory
from scheduler.domain.models import Appointment, Practitioner, WorkingRange
from shared_models.scheduling import AppointmentStatus, Specialty
from sqlalchemy import func, select
from ulid import ULID

from .conftest import DEFAULT_SCHEDULE, new_id
from .test_booking_roundtrip import _chat_settings, _seed

_TUESDAY = date(2026, 8, 18)
_TUESDAY_9AM = datetime(2026, 8, 18, 9, 0)
_TUESDAY_10AM = datetime(2026, 8, 18, 10, 0)
_LOCAL_NOW = datetime(2026, 8, 17, 8, 0)
_OTHER_DURATION_MINUTES = 30


async def _seed_second_practitioner(session_id: str) -> str:
    """Create a second practitioner whose appointments are half as long.

    Returns: the new practitioner's id.
    """
    async with session_factory() as session:
        practitioner = Practitioner(
            id=str(ULID()),
            session_id=session_id,
            full_name="Elizabeth Blackwell",
            specialty=Specialty.DENTISTRY,
            appointment_duration_minutes=_OTHER_DURATION_MINUTES,
        )
        session.add(practitioner)
        await session.commit()
        for weekday, start, end in DEFAULT_SCHEDULE:
            session.add(
                WorkingRange(
                    id=str(ULID()),
                    practitioner_id=practitioner.id,
                    weekday=weekday,
                    start_time=start,
                    end_time=end,
                )
            )
        await session.commit()
        return practitioner.id


async def _book(
    channel: grpc.aio.Channel, session_id: str, patient_id: str, practitioner_id: str
) -> scheduling.BookingSuccess:
    outcome = await scheduling.book_appointment(
        channel,
        _chat_settings(),
        session_id=session_id,
        patient_id=patient_id,
        practitioner_id=practitioner_id,
        starts_at=_TUESDAY_9AM,
        local_now=_LOCAL_NOW,
        idempotency_key=derive_idempotency_key(
            patient_id, practitioner_id, _TUESDAY_9AM
        ),
    )
    assert isinstance(outcome, scheduling.BookingSuccess)
    return outcome


async def _swap(
    channel: grpc.aio.Channel,
    session_id: str,
    patient_id: str,
    appointment_id: str,
    old_practitioner_id: str,
    new_practitioner_id: str,
    *,
    new_starts_at: datetime = _TUESDAY_10AM,
) -> object:
    return await scheduling.reschedule_appointment(
        channel,
        _chat_settings(),
        session_id=session_id,
        patient_id=patient_id,
        appointment_id=appointment_id,
        new_starts_at=new_starts_at,
        new_practitioner_id=new_practitioner_id,
        expected_starts_at=_TUESDAY_9AM,
        expected_practitioner_id=old_practitioner_id,
        local_now=_LOCAL_NOW,
    )


async def test_a_swap_leaves_one_appointment_with_its_id_and_the_new_practitioner(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    other_id = await _seed_second_practitioner(session_id)
    booked = await _book(scheduling_channel, session_id, patient_id, practitioner_id)

    swapped = await _swap(
        scheduling_channel,
        session_id,
        patient_id,
        booked.appointment.id,
        practitioner_id,
        other_id,
    )

    assert isinstance(swapped, scheduling.ChangeApplied)
    assert swapped.appointment.id == booked.appointment.id
    assert swapped.appointment.practitioner_id == other_id

    async with session_factory() as session:
        count = await session.execute(select(func.count()).select_from(Appointment))
        assert count.scalar_one() == 1
        row = (
            (
                await session.execute(
                    select(Appointment).where(Appointment.id == booked.appointment.id)
                )
            )
            .scalars()
            .one()
        )
    assert row.practitioner_id == other_id
    assert row.status == AppointmentStatus.STANDING


async def test_the_end_comes_from_the_new_practitioners_own_duration(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    other_id = await _seed_second_practitioner(session_id)
    booked = await _book(scheduling_channel, session_id, patient_id, practitioner_id)
    assert booked.appointment.ends_at - booked.appointment.starts_at == timedelta(
        minutes=60
    )

    swapped = await _swap(
        scheduling_channel,
        session_id,
        patient_id,
        booked.appointment.id,
        practitioner_id,
        other_id,
    )

    assert isinstance(swapped, scheduling.ChangeApplied)
    assert swapped.appointment.ends_at - swapped.appointment.starts_at == timedelta(
        minutes=_OTHER_DURATION_MINUTES
    )


async def test_the_old_practitioners_slot_is_offered_again(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    other_id = await _seed_second_practitioner(session_id)
    booked = await _book(scheduling_channel, session_id, patient_id, practitioner_id)

    await _swap(
        scheduling_channel,
        session_id,
        patient_id,
        booked.appointment.id,
        practitioner_id,
        other_id,
    )

    result = await scheduling.check_availability(
        scheduling_channel,
        _chat_settings(),
        session_id=session_id,
        practitioner_id=practitioner_id,
        patient_id=patient_id,
        from_date=_TUESDAY,
        to_date=_TUESDAY,
        local_now=_LOCAL_NOW,
    )

    assert _TUESDAY_9AM in result.available_starts


async def test_a_swap_that_keeps_the_time_carries_both_practitioners_names(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    # The case that would otherwise log as a change from a time to the identical time:
    # only the practitioner moved, and both names have to come back for the assistant
    # to describe it at all.
    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    other_id = await _seed_second_practitioner(session_id)
    booked = await _book(scheduling_channel, session_id, patient_id, practitioner_id)

    swapped = await _swap(
        scheduling_channel,
        session_id,
        patient_id,
        booked.appointment.id,
        practitioner_id,
        other_id,
        new_starts_at=_TUESDAY_9AM,
    )

    assert isinstance(swapped, scheduling.ChangeApplied)
    assert swapped.appointment.starts_at == _TUESDAY_9AM
    assert swapped.previous_starts_at == _TUESDAY_9AM
    assert swapped.previous_practitioner_full_name == "William Osler"
    assert swapped.appointment.practitioner_full_name == "Elizabeth Blackwell"
    assert swapped.appointment.practitioner_specialty == Specialty.DENTISTRY


async def test_a_forced_refusal_leaves_the_appointment_with_its_first_practitioner(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    booked = await _book(scheduling_channel, session_id, patient_id, practitioner_id)

    refused = await _swap(
        scheduling_channel,
        session_id,
        patient_id,
        booked.appointment.id,
        practitioner_id,
        new_id(),
    )

    assert isinstance(refused, scheduling.ChangeRefusal)
    async with session_factory() as session:
        row = (
            (
                await session.execute(
                    select(Appointment).where(Appointment.id == booked.appointment.id)
                )
            )
            .scalars()
            .one()
        )
    assert row.practitioner_id == practitioner_id
    assert row.starts_at == _TUESDAY_9AM
    assert row.status == AppointmentStatus.STANDING
