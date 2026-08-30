"""A change re-sent after it landed reports its original outcome, not a conflict.

This is the case the guard's second arm exists for. A caller whose deadline expired has
no way to know whether the change landed, so it re-sends the request it already sent -
quoting the state it read out *before* the move, because that is what it told the
patient. With only the described-state arm, the appointment no longer matches it and
the re-send is refused as stale: a conflict reported for a change that succeeded.
"""

from datetime import datetime

import grpc
from chat.agent.tools.scheduling_tools import derive_idempotency_key
from chat.clients import scheduling

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


async def test_an_identical_move_sent_twice_answers_applied_then_no_change(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    booked = await _book(scheduling_channel, session_id, patient_id, practitioner_id)

    async def send() -> object:
        # Identical both times, quoting the PRE-move state - which is what a retry of
        # a request whose answer was lost actually looks like.
        return await scheduling.reschedule_appointment(
            scheduling_channel,
            _chat_settings(),
            session_id=session_id,
            patient_id=patient_id,
            appointment_id=booked.appointment.id,
            new_starts_at=_TUESDAY_10AM,
            new_practitioner_id=None,
            expected_starts_at=_TUESDAY_9AM,
            expected_practitioner_id=practitioner_id,
            local_now=_LOCAL_NOW,
        )

    first = await send()
    second = await send()

    assert isinstance(first, scheduling.ChangeApplied)
    assert isinstance(second, scheduling.ChangeNoOp)
    # Emphatically not a stale confirmation: the change the caller asked for is the
    # state the appointment is in.
    assert not isinstance(second, scheduling.ChangeRefusal)
    assert second.appointment.starts_at == _TUESDAY_10AM


async def test_a_third_identical_send_still_answers_no_change(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    booked = await _book(scheduling_channel, session_id, patient_id, practitioner_id)

    outcomes = []
    for _ in range(3):
        outcomes.append(
            await scheduling.reschedule_appointment(
                scheduling_channel,
                _chat_settings(),
                session_id=session_id,
                patient_id=patient_id,
                appointment_id=booked.appointment.id,
                new_starts_at=_TUESDAY_10AM,
                new_practitioner_id=None,
                expected_starts_at=_TUESDAY_9AM,
                expected_practitioner_id=practitioner_id,
                local_now=_LOCAL_NOW,
            )
        )

    assert isinstance(outcomes[0], scheduling.ChangeApplied)
    assert all(isinstance(o, scheduling.ChangeNoOp) for o in outcomes[1:])


async def test_a_re_sent_cancellation_answers_applied_then_no_change(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    # The same guarantee, reached by a different mechanism: a cancellation's guard has
    # only the described arm, so the re-send matches nothing and the classification
    # read reports it as already in the state asked for.
    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    booked = await _book(scheduling_channel, session_id, patient_id, practitioner_id)

    async def send() -> object:
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

    first = await send()
    second = await send()

    assert isinstance(first, scheduling.ChangeApplied)
    assert isinstance(second, scheduling.ChangeNoOp)
