"""A sequence of moves that returns to a time it already used.

09:00 -> 10:00 -> 09:00 -> 10:00 is the sequence that breaks any scheme deriving an
idempotency key from the target state: the third move would derive the first move's key
and replay it, leaving the appointment at 09:00 while reporting success. The change RPCs
carry no key for exactly this reason, and this pins that all three moves take effect in
order.
"""

from datetime import datetime

import grpc
from chat.agent.tools.scheduling_tools import derive_idempotency_key
from chat.clients import scheduling
from scheduler.db.session import session_factory
from scheduler.domain.models import Appointment
from shared_models.scheduling import AppointmentStatus
from sqlalchemy import func, select
from structlog.testing import capture_logs

from .conftest import new_id
from .test_booking_roundtrip import _chat_settings, _seed

_TUESDAY_9AM = datetime(2026, 8, 18, 9, 0)
_TUESDAY_10AM = datetime(2026, 8, 18, 10, 0)
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


async def _move(
    channel: grpc.aio.Channel,
    session_id: str,
    patient_id: str,
    appointment_id: str,
    practitioner_id: str,
    *,
    frm: datetime,
    to: datetime,
) -> object:
    return await scheduling.reschedule_appointment(
        channel,
        _chat_settings(),
        session_id=session_id,
        patient_id=patient_id,
        appointment_id=appointment_id,
        new_starts_at=to,
        new_practitioner_id=None,
        # Each move quotes the state the assistant would have just read out - the one
        # the previous move left behind.
        expected_starts_at=frm,
        expected_practitioner_id=practitioner_id,
        local_now=_LOCAL_NOW,
    )


async def test_three_confirmed_moves_all_take_effect_in_order(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    booked = await _book(scheduling_channel, session_id, patient_id, practitioner_id)

    outcomes = []
    for frm, to in (
        (_TUESDAY_9AM, _TUESDAY_10AM),
        (_TUESDAY_10AM, _TUESDAY_9AM),
        (_TUESDAY_9AM, _TUESDAY_10AM),
    ):
        outcomes.append(
            await _move(
                scheduling_channel,
                session_id,
                patient_id,
                booked.appointment.id,
                practitioner_id,
                frm=frm,
                to=to,
            )
        )

    # All three moved something - none was replayed as an earlier one.
    assert all(isinstance(o, scheduling.ChangeApplied) for o in outcomes), outcomes
    assert [o.appointment.starts_at for o in outcomes] == [  # type: ignore[union-attr]
        _TUESDAY_10AM,
        _TUESDAY_9AM,
        _TUESDAY_10AM,
    ]
    assert [o.previous_starts_at for o in outcomes] == [  # type: ignore[union-attr]
        _TUESDAY_9AM,
        _TUESDAY_10AM,
        _TUESDAY_9AM,
    ]


async def test_the_appointment_ends_at_ten_holding_the_id_it_started_with(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    booked = await _book(scheduling_channel, session_id, patient_id, practitioner_id)

    for frm, to in (
        (_TUESDAY_9AM, _TUESDAY_10AM),
        (_TUESDAY_10AM, _TUESDAY_9AM),
        (_TUESDAY_9AM, _TUESDAY_10AM),
    ):
        await _move(
            scheduling_channel,
            session_id,
            patient_id,
            booked.appointment.id,
            practitioner_id,
            frm=frm,
            to=to,
        )

    async with session_factory() as session:
        count = await session.execute(select(func.count()).select_from(Appointment))
        assert count.scalar_one() == 1
        row = (
            await session.execute(
                select(Appointment).where(Appointment.id == booked.appointment.id)
            )
        ).scalars().one()

    assert row.id == booked.appointment.id
    assert row.starts_at == _TUESDAY_10AM
    assert row.status == AppointmentStatus.STANDING


async def test_the_sequence_leaves_exactly_three_change_records(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    # SC-009: the count of change records equals the count of appointments actually
    # altered. Three moves happened, so there are three records and not four.
    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    booked = await _book(scheduling_channel, session_id, patient_id, practitioner_id)

    with capture_logs() as logs:
        for frm, to in (
            (_TUESDAY_9AM, _TUESDAY_10AM),
            (_TUESDAY_10AM, _TUESDAY_9AM),
            (_TUESDAY_9AM, _TUESDAY_10AM),
        ):
            await _move(
                scheduling_channel,
                session_id,
                patient_id,
                booked.appointment.id,
                practitioner_id,
                frm=frm,
                to=to,
            )

    rescheduled = [log for log in logs if log["event"] == "appointment.rescheduled"]
    assert len(rescheduled) == 3
    assert [r["new_starts_at"] for r in rescheduled] == [
        _TUESDAY_10AM.isoformat(),
        _TUESDAY_9AM.isoformat(),
        _TUESDAY_10AM.isoformat(),
    ]


async def test_a_re_send_of_a_landed_move_adds_no_record_and_no_stale_refusal(
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
        frm=_TUESDAY_9AM,
        to=_TUESDAY_10AM,
    )

    with capture_logs() as logs:
        resent = await _move(
            scheduling_channel,
            session_id,
            patient_id,
            booked.appointment.id,
            practitioner_id,
            frm=_TUESDAY_9AM,
            to=_TUESDAY_10AM,
        )

    assert isinstance(resent, scheduling.ChangeNoOp)
    events = [log["event"] for log in logs]
    assert "appointment.rescheduled" not in events
    assert "change.refused" not in events
    assert "appointment.unchanged" in events
