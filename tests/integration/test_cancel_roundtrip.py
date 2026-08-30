"""Cancelling through the real client and servicer, and what it frees.

The freed slot is the point: a cancellation that leaves the row in place is only
correct if both partial exclusion constraints and the partial unique index really take
it out of consideration - which no single-service test can show, because the offer path
and the write path are different code reading the same schema.
"""

from datetime import date, datetime

import grpc
from chat.clients import scheduling
from scheduler.db.session import session_factory
from scheduler.domain.models import Appointment
from shared_models.scheduling import AppointmentStatus, ChangeFailureReason
from sqlalchemy import select

from .conftest import new_id
from .test_booking_roundtrip import _chat_settings, _seed

_TUESDAY = date(2026, 8, 18)
_TUESDAY_9AM = datetime(2026, 8, 18, 9, 0)
_LOCAL_NOW = datetime(2026, 8, 17, 8, 0)


async def _book(
    channel: grpc.aio.Channel, session_id: str, patient_id: str, practitioner_id: str
) -> scheduling.BookingSuccess:
    from chat.agent.tools.scheduling_tools import derive_idempotency_key

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


async def _available(
    channel: grpc.aio.Channel, session_id: str, patient_id: str, practitioner_id: str
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
    )
    return result.available_starts


async def test_a_cancelled_slot_is_offered_again_and_can_be_rebooked(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    booked = await _book(scheduling_channel, session_id, patient_id, practitioner_id)

    assert _TUESDAY_9AM not in await _available(
        scheduling_channel, session_id, patient_id, practitioner_id
    )

    cancelled = await scheduling.cancel_appointment(
        scheduling_channel,
        _chat_settings(),
        session_id=session_id,
        patient_id=patient_id,
        appointment_id=booked.appointment.id,
        expected_starts_at=_TUESDAY_9AM,
        expected_practitioner_id=practitioner_id,
        local_now=_LOCAL_NOW,
    )

    assert isinstance(cancelled, scheduling.ChangeApplied)
    assert cancelled.appointment.status is AppointmentStatus.CANCELLED
    # Offered again immediately: the exclusion constraints are partial, so the row it
    # left behind occupies nothing.
    assert _TUESDAY_9AM in await _available(
        scheduling_channel, session_id, patient_id, practitioner_id
    )

    rebooked = await _book(scheduling_channel, session_id, patient_id, practitioner_id)
    assert rebooked.appointment.starts_at == _TUESDAY_9AM


async def test_the_cancelled_record_is_retained_with_everything_but_its_status(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    booked = await _book(scheduling_channel, session_id, patient_id, practitioner_id)

    await scheduling.cancel_appointment(
        scheduling_channel,
        _chat_settings(),
        session_id=session_id,
        patient_id=patient_id,
        appointment_id=booked.appointment.id,
        expected_starts_at=_TUESDAY_9AM,
        expected_practitioner_id=practitioner_id,
        local_now=_LOCAL_NOW,
    )

    async with session_factory() as session:
        result = await session.execute(
            select(Appointment).where(Appointment.id == booked.appointment.id)
        )
        row = result.scalars().one()

    assert row.status == AppointmentStatus.CANCELLED
    assert row.starts_at == _TUESDAY_9AM
    assert row.practitioner_id == practitioner_id
    assert row.patient_id == patient_id


async def test_a_cancelled_appointment_leaves_the_standing_listing(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    from shared_models.scheduling import StatusFilter

    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    booked = await _book(scheduling_channel, session_id, patient_id, practitioner_id)

    await scheduling.cancel_appointment(
        scheduling_channel,
        _chat_settings(),
        session_id=session_id,
        patient_id=patient_id,
        appointment_id=booked.appointment.id,
        expected_starts_at=_TUESDAY_9AM,
        expected_practitioner_id=practitioner_id,
        local_now=_LOCAL_NOW,
    )

    standing = await scheduling.list_appointments(
        scheduling_channel,
        _chat_settings(),
        session_id=session_id,
        patient_id=patient_id,
        local_now=_LOCAL_NOW,
    )
    cancelled = await scheduling.list_appointments(
        scheduling_channel,
        _chat_settings(),
        session_id=session_id,
        patient_id=patient_id,
        local_now=_LOCAL_NOW,
        status_filter=StatusFilter.CANCELLED,
    )

    assert [a.id for a in standing.future] == []
    assert [a.id for a in cancelled.future] == [booked.appointment.id]
    assert cancelled.future[0].status is AppointmentStatus.CANCELLED


async def test_an_appointment_id_from_another_session_is_not_found(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    """And nothing distinguishes it from an id that never existed.

    Both come back as the same reason for the same reason: the scope is a predicate on
    the write, so another session's id simply does not resolve. A caller that could tell
    the two apart could enumerate other sessions' appointments.
    """
    theirs_session = new_id()
    theirs_practitioner, theirs_patient = await _seed(theirs_session)
    theirs = await _book(
        scheduling_channel, theirs_session, theirs_patient, theirs_practitioner
    )

    mine_session = new_id()
    _, mine_patient = await _seed(mine_session)

    foreign = await scheduling.cancel_appointment(
        scheduling_channel,
        _chat_settings(),
        session_id=mine_session,
        patient_id=mine_patient,
        appointment_id=theirs.appointment.id,
        expected_starts_at=_TUESDAY_9AM,
        expected_practitioner_id=theirs_practitioner,
        local_now=_LOCAL_NOW,
    )
    invented = await scheduling.cancel_appointment(
        scheduling_channel,
        _chat_settings(),
        session_id=mine_session,
        patient_id=mine_patient,
        appointment_id=new_id(),
        expected_starts_at=_TUESDAY_9AM,
        expected_practitioner_id=theirs_practitioner,
        local_now=_LOCAL_NOW,
    )

    assert isinstance(foreign, scheduling.ChangeRefusal)
    assert isinstance(invented, scheduling.ChangeRefusal)
    assert foreign.reason is ChangeFailureReason.APPOINTMENT_NOT_FOUND
    assert foreign.reason is invented.reason
    assert foreign.detail == invented.detail

    # And their appointment is untouched.
    async with session_factory() as session:
        result = await session.execute(
            select(Appointment).where(Appointment.id == theirs.appointment.id)
        )
        assert result.scalars().one().status == AppointmentStatus.STANDING


async def test_a_re_sent_cancellation_reports_the_same_outcome_not_a_conflict(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    booked = await _book(scheduling_channel, session_id, patient_id, practitioner_id)

    async def cancel() -> object:
        return await scheduling.cancel_appointment(
            scheduling_channel,
            _chat_settings(),
            session_id=session_id,
            patient_id=patient_id,
            appointment_id=booked.appointment.id,
            expected_starts_at=_TUESDAY_9AM,
            expected_practitioner_id=practitioner_id,
            local_now=_LOCAL_NOW,
        )

    first = await cancel()
    second = await cancel()

    assert isinstance(first, scheduling.ChangeApplied)
    assert isinstance(second, scheduling.ChangeNoOp)
