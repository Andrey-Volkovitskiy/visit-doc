"""The released key: rebooking a cancelled slot creates a new appointment, not a replay.

The booking key is derived from patient, practitioner and start, so rebooking the exact
slot that was just cancelled presents the *same* key. If cancelling did not release it,
the second booking would replay the cancelled appointment and report it as a fresh one -
which is the worst outcome this feature can produce, and the reason the key's uniqueness
is a partial index rather than a constraint.
"""

from datetime import datetime

import grpc
from chat.agent.tools.scheduling_tools import derive_idempotency_key
from chat.clients import scheduling
from scheduler.db.session import session_factory
from scheduler.domain.models import Appointment
from shared_models.scheduling import AppointmentStatus
from sqlalchemy import select

from .conftest import new_id
from .test_booking_roundtrip import _chat_settings, _seed

_TUESDAY_9AM = datetime(2026, 8, 18, 9, 0)
_LOCAL_NOW = datetime(2026, 8, 17, 8, 0)


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


async def test_rebooking_a_cancelled_slot_produces_a_new_appointment(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    first = await _book(scheduling_channel, session_id, patient_id, practitioner_id)

    await scheduling.cancel_appointment(
        scheduling_channel,
        _chat_settings(),
        session_id=session_id,
        patient_id=patient_id,
        appointment_id=first.appointment.id,
        expected_starts_at=_TUESDAY_9AM,
        expected_practitioner_id=practitioner_id,
        local_now=_LOCAL_NOW,
    )

    second = await _book(scheduling_channel, session_id, patient_id, practitioner_id)

    assert second.idempotent_replay is False
    assert second.appointment.id != first.appointment.id
    assert second.appointment.status is AppointmentStatus.STANDING


async def test_both_rows_survive_and_hold_the_same_key(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    # The cancelled row keeps the key it was created with - that is the record of which
    # key created it. The partial index simply stops constraining it.
    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    first = await _book(scheduling_channel, session_id, patient_id, practitioner_id)
    await scheduling.cancel_appointment(
        scheduling_channel,
        _chat_settings(),
        session_id=session_id,
        patient_id=patient_id,
        appointment_id=first.appointment.id,
        expected_starts_at=_TUESDAY_9AM,
        expected_practitioner_id=practitioner_id,
        local_now=_LOCAL_NOW,
    )
    second = await _book(scheduling_channel, session_id, patient_id, practitioner_id)

    async with session_factory() as session:
        result = await session.execute(
            select(Appointment).order_by(Appointment.status.asc())
        )
        rows = list(result.scalars().all())

    assert len(rows) == 2
    assert {r.status for r in rows} == {
        AppointmentStatus.CANCELLED,
        AppointmentStatus.STANDING,
    }
    assert len({r.idempotency_key for r in rows}) == 1
    assert {r.id for r in rows} == {first.appointment.id, second.appointment.id}


async def test_the_rebooked_appointment_is_the_one_that_is_listed(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    first = await _book(scheduling_channel, session_id, patient_id, practitioner_id)
    await scheduling.cancel_appointment(
        scheduling_channel,
        _chat_settings(),
        session_id=session_id,
        patient_id=patient_id,
        appointment_id=first.appointment.id,
        expected_starts_at=_TUESDAY_9AM,
        expected_practitioner_id=practitioner_id,
        local_now=_LOCAL_NOW,
    )
    second = await _book(scheduling_channel, session_id, patient_id, practitioner_id)

    listing = await scheduling.list_appointments(
        scheduling_channel,
        _chat_settings(),
        session_id=session_id,
        patient_id=patient_id,
        local_now=_LOCAL_NOW,
    )

    assert [a.id for a in listing.future] == [second.appointment.id]
