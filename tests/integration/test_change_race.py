"""Two changes racing for one appointment: one applies, the other is refused.

This is the pairing the datastore cannot catch. Two bookings colliding are stopped by
an exclusion constraint, but a *cancellation* collides with nothing - it writes a status
on a row nobody else is competing for - so a cancellation racing a move is caught only
by the guard being a predicate on the write itself.

If the guard were a check performed before the write, both would pass it, and the second
would silently overwrite the first after its patient had been told it succeeded. That
failure is invisible in every single-threaded test, which is why this one exists.
"""

import asyncio
from datetime import datetime

import grpc
from chat.agent.tools.scheduling_tools import derive_idempotency_key
from chat.clients import scheduling
from scheduler.db.session import session_factory
from scheduler.domain.models import Appointment
from shared_models.scheduling import AppointmentStatus, ChangeFailureReason
from sqlalchemy import select

from .conftest import new_id
from .test_booking_roundtrip import _chat_settings, _seed

_TUESDAY_9AM = datetime(2026, 8, 18, 9, 0)
_TUESDAY_10AM = datetime(2026, 8, 18, 10, 0)
_TUESDAY_11AM = datetime(2026, 8, 18, 11, 0)
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


async def _stored(appointment_id: str) -> Appointment:
    async with session_factory() as session:
        result = await session.execute(
            select(Appointment).where(Appointment.id == appointment_id)
        )
        return result.scalars().one()


async def test_a_cancellation_racing_a_move_leaves_exactly_one_applied(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    booked = await _book(scheduling_channel, session_id, patient_id, practitioner_id)
    common = {
        "session_id": session_id,
        "patient_id": patient_id,
        "appointment_id": booked.appointment.id,
        # Both quote the same state, because both were described from it.
        "expected_starts_at": _TUESDAY_9AM,
        "expected_practitioner_id": practitioner_id,
        "local_now": _LOCAL_NOW,
    }

    cancel, move = await asyncio.gather(
        scheduling.cancel_appointment(
            scheduling_channel, _chat_settings(), **common
        ),
        scheduling.reschedule_appointment(
            scheduling_channel,
            _chat_settings(),
            new_starts_at=_TUESDAY_10AM,
            new_practitioner_id=None,
            **common,
        ),
    )

    outcomes = [cancel, move]
    applied = [o for o in outcomes if isinstance(o, scheduling.ChangeApplied)]
    refused = [o for o in outcomes if isinstance(o, scheduling.ChangeRefusal)]

    assert len(applied) == 1, outcomes
    assert len(refused) == 1, outcomes
    # The loser is told the appointment changed under it - not that its change worked.
    assert refused[0].reason in {
        ChangeFailureReason.STALE_CONFIRMATION,
        ChangeFailureReason.ALREADY_CANCELLED,
    }

    row = await _stored(booked.appointment.id)
    if isinstance(cancel, scheduling.ChangeApplied):
        # The cancellation won: the move must not have overwritten it.
        assert row.status == AppointmentStatus.CANCELLED
        assert row.starts_at == _TUESDAY_9AM
    else:
        # The move won: the cancellation must not have taken it away.
        assert row.status == AppointmentStatus.STANDING
        assert row.starts_at == _TUESDAY_10AM


async def test_two_moves_to_different_times_leave_exactly_one_applied(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    booked = await _book(scheduling_channel, session_id, patient_id, practitioner_id)
    common = {
        "session_id": session_id,
        "patient_id": patient_id,
        "appointment_id": booked.appointment.id,
        "new_practitioner_id": None,
        "expected_starts_at": _TUESDAY_9AM,
        "expected_practitioner_id": practitioner_id,
        "local_now": _LOCAL_NOW,
    }

    first, second = await asyncio.gather(
        scheduling.reschedule_appointment(
            scheduling_channel,
            _chat_settings(),
            new_starts_at=_TUESDAY_10AM,
            **common,
        ),
        scheduling.reschedule_appointment(
            scheduling_channel,
            _chat_settings(),
            new_starts_at=_TUESDAY_11AM,
            **common,
        ),
    )

    outcomes = [first, second]
    applied = [o for o in outcomes if isinstance(o, scheduling.ChangeApplied)]
    assert len(applied) == 1, outcomes

    row = await _stored(booked.appointment.id)
    assert row.starts_at == applied[0].appointment.starts_at


async def test_no_completed_change_is_ever_overwritten_by_the_loser(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    """The stored row always matches whichever change reported success.

    Run several times, because a race that is usually won by one side would otherwise
    look correct on a single pass.
    """
    for _ in range(5):
        session_id = new_id()
        practitioner_id, patient_id = await _seed(session_id)
        booked = await _book(
            scheduling_channel, session_id, patient_id, practitioner_id
        )
        common = {
            "session_id": session_id,
            "patient_id": patient_id,
            "appointment_id": booked.appointment.id,
            "expected_starts_at": _TUESDAY_9AM,
            "expected_practitioner_id": practitioner_id,
            "local_now": _LOCAL_NOW,
        }

        cancel, move = await asyncio.gather(
            scheduling.cancel_appointment(
                scheduling_channel, _chat_settings(), **common
            ),
            scheduling.reschedule_appointment(
                scheduling_channel,
                _chat_settings(),
                new_starts_at=_TUESDAY_10AM,
                new_practitioner_id=None,
                **common,
            ),
        )

        row = await _stored(booked.appointment.id)
        if isinstance(cancel, scheduling.ChangeApplied):
            assert row.status == AppointmentStatus.CANCELLED
        if isinstance(move, scheduling.ChangeApplied):
            assert row.starts_at == _TUESDAY_10AM
            assert row.status == AppointmentStatus.STANDING
