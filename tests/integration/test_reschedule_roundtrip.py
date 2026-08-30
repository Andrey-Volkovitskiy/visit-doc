"""Moving an appointment through the real client and servicer.

The property that matters is that it is the *same* appointment: one row, the id it
started with, at the new time - never a cancellation plus a new booking, which is what
"the two halves come apart" would look like from the outside.
"""

from datetime import date, datetime

import grpc
from chat.agent.tools.scheduling_tools import derive_idempotency_key
from chat.clients import scheduling
from scheduler.db.session import session_factory
from scheduler.domain.models import Appointment
from shared_models.scheduling import AppointmentStatus
from sqlalchemy import func, select

from .conftest import new_id
from .test_booking_roundtrip import _chat_settings, _seed

_TUESDAY = date(2026, 8, 18)
_TUESDAY_9AM = datetime(2026, 8, 18, 9, 0)
_TUESDAY_10AM = datetime(2026, 8, 18, 10, 0)
_LOCAL_NOW = datetime(2026, 8, 17, 8, 0)


async def _book(
    channel: grpc.aio.Channel,
    session_id: str,
    patient_id: str,
    practitioner_id: str,
    starts_at: datetime = _TUESDAY_9AM,
) -> scheduling.BookingSuccess:
    outcome = await scheduling.book_appointment(
        channel,
        _chat_settings(),
        session_id=session_id,
        patient_id=patient_id,
        practitioner_id=practitioner_id,
        starts_at=starts_at,
        local_now=_LOCAL_NOW,
        idempotency_key=derive_idempotency_key(
            patient_id, practitioner_id, starts_at
        ),
    )
    assert isinstance(outcome, scheduling.BookingSuccess)
    return outcome


async def _move(
    channel: grpc.aio.Channel,
    session_id: str,
    patient_id: str,
    appointment_id: str,
    practitioner_id: str,
    *,
    new_starts_at: datetime = _TUESDAY_10AM,
    expected_starts_at: datetime = _TUESDAY_9AM,
) -> object:
    return await scheduling.reschedule_appointment(
        channel,
        _chat_settings(),
        session_id=session_id,
        patient_id=patient_id,
        appointment_id=appointment_id,
        new_starts_at=new_starts_at,
        new_practitioner_id=None,
        expected_starts_at=expected_starts_at,
        expected_practitioner_id=practitioner_id,
        local_now=_LOCAL_NOW,
    )


async def _available(
    channel: grpc.aio.Channel,
    session_id: str,
    patient_id: str,
    practitioner_id: str,
    *,
    excluded_appointment_id: str | None = None,
) -> tuple[datetime, ...]:
    result = await scheduling.check_availability(
        channel,
        _chat_settings(),
        session_id=session_id,
        practitioner_id=practitioner_id,
        patient_id=patient_id,
        from_date=_TUESDAY,
        to_date=_TUESDAY,
        local_now=_LOCAL_NOW,
        excluded_appointment_id=excluded_appointment_id,
    )
    return result.available_starts


async def test_a_move_leaves_exactly_one_appointment_with_its_original_id(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    booked = await _book(scheduling_channel, session_id, patient_id, practitioner_id)

    moved = await _move(
        scheduling_channel,
        session_id,
        patient_id,
        booked.appointment.id,
        practitioner_id,
    )

    assert isinstance(moved, scheduling.ChangeApplied)
    assert moved.appointment.id == booked.appointment.id
    assert moved.appointment.starts_at == _TUESDAY_10AM
    assert moved.previous_starts_at == _TUESDAY_9AM

    async with session_factory() as session:
        count = await session.execute(select(func.count()).select_from(Appointment))
        assert count.scalar_one() == 1
        row = (
            await session.execute(
                select(Appointment).where(Appointment.id == booked.appointment.id)
            )
        ).scalars().one()
    assert row.starts_at == _TUESDAY_10AM
    assert row.status == AppointmentStatus.STANDING


async def test_the_old_slot_is_offered_again_and_the_new_one_is_not(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    booked = await _book(scheduling_channel, session_id, patient_id, practitioner_id)

    await _move(
        scheduling_channel,
        session_id,
        patient_id,
        booked.appointment.id,
        practitioner_id,
    )

    starts = await _available(
        scheduling_channel, session_id, patient_id, practitioner_id
    )
    assert _TUESDAY_9AM in starts
    assert _TUESDAY_10AM not in starts


async def test_the_appointment_does_not_block_its_own_next_move(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    # FR-007: asking to move it again offers 10:00 among the options, because an
    # appointment never blocks its own change.
    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    booked = await _book(scheduling_channel, session_id, patient_id, practitioner_id)
    await _move(
        scheduling_channel,
        session_id,
        patient_id,
        booked.appointment.id,
        practitioner_id,
    )

    starts = await _available(
        scheduling_channel,
        session_id,
        patient_id,
        practitioner_id,
        excluded_appointment_id=booked.appointment.id,
    )

    assert _TUESDAY_10AM in starts


async def test_the_ends_at_is_recomputed_rather_than_carried_over(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    booked = await _book(scheduling_channel, session_id, patient_id, practitioner_id)

    moved = await _move(
        scheduling_channel,
        session_id,
        patient_id,
        booked.appointment.id,
        practitioner_id,
    )

    assert isinstance(moved, scheduling.ChangeApplied)
    original_length = booked.appointment.ends_at - booked.appointment.starts_at
    new_length = moved.appointment.ends_at - moved.appointment.starts_at
    assert new_length == original_length
    assert moved.appointment.ends_at == _TUESDAY_10AM + original_length
