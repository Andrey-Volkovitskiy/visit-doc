"""Chat's gRPC client against a real scheduling servicer and a real database.

The chat unit tier fakes this boundary; this is where the contract those fakes stand in
for is actually proven - including that a booking really lands in the scheduler's own
tables, and that a repeated attempt replays rather than colliding.
"""

from datetime import date, datetime

import grpc
import pytest
from chat.agent.tools.registry import ToolContext, ToolRegistry
from chat.agent.tools.scheduling_tools import (
    SCHEDULING_TOOLS,
    derive_idempotency_key,
)
from chat.clients import scheduling
from chat.core.config import Settings as ChatSettings
from scheduler.db.session import session_factory
from scheduler.domain.models import Appointment, Patient, Practitioner, WorkingRange
from shared_models.scheduling import BookingFailureReason, Specialty
from sqlalchemy import func, select
from ulid import ULID

from .conftest import DEFAULT_SCHEDULE, new_id

_TUESDAY = date(2026, 8, 18)
_TUESDAY_9AM = datetime(2026, 8, 18, 9, 0)
_LOCAL_NOW = datetime(2026, 8, 17, 8, 0)


def _chat_settings() -> ChatSettings:
    """Chat's own settings, with a short budget so a failing call fails quickly."""
    return ChatSettings(
        DATABASE_URL="postgresql+asyncpg://u:p@localhost:5432/unused",
        QDRANT_URL="http://localhost:6333",
        ANTHROPIC_API_KEY="unused",
        VOYAGE_API_KEY="unused",
        SCHEDULING_TIMEOUT_SECONDS=2.0,
        SCHEDULING_MAX_ATTEMPTS=2,
    )


async def _seed(session_id: str) -> tuple[str, str]:
    """Create one practitioner and one patient directly in the scheduler's database.

    Returns: the practitioner's id and the patient's id.
    """
    async with session_factory() as session:
        practitioner = Practitioner(
            id=str(ULID()),
            session_id=session_id,
            full_name="William Osler",
            specialty=Specialty.GENERAL_PRACTICE,
            appointment_duration_minutes=60,
        )
        patient = Patient(
            id=str(ULID()),
            session_id=session_id,
            chat_id=new_id(),
            full_name="Ada Lovelace",
        )
        session.add_all([practitioner, patient])
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
        return practitioner.id, patient.id


async def _appointment_count() -> int:
    async with session_factory() as session:
        result = await session.execute(select(func.count()).select_from(Appointment))
        return int(result.scalar_one())


async def test_the_booking_round_trip_lands_a_row_in_the_scheduler(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    settings = _chat_settings()

    listed = await scheduling.list_practitioners(
        scheduling_channel, settings, session_id=session_id
    )
    assert [p.full_name for p in listed] == ["William Osler"]
    assert listed[0].bookable is True
    # The days survive the crossing unchanged. The wire's `Weekday` numbering is not
    # the stored one - zero there is the unset sentinel - so this is the one place the
    # two ends' mappings are proven to agree rather than each tested against a fake.
    assert [r.weekday for r in listed[0].schedule] == [
        weekday for weekday, _, _ in DEFAULT_SCHEDULE
    ]

    availability = await scheduling.check_availability(
        scheduling_channel,
        settings,
        session_id=session_id,
        practitioner_id=practitioner_id,
        patient_id=patient_id,
        from_date=_TUESDAY,
        to_date=_TUESDAY,
        local_now=_LOCAL_NOW,
    )
    assert _TUESDAY_9AM in availability.available_starts
    assert availability.appointment_duration_minutes == 60

    outcome = await scheduling.book_appointment(
        scheduling_channel,
        settings,
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
    assert outcome.appointment.practitioner_full_name == "William Osler"
    assert outcome.appointment.patient_full_name == "Ada Lovelace"
    assert outcome.appointment.starts_at == _TUESDAY_9AM

    # Verified from outside the conversation: the row genuinely exists.
    async with session_factory() as session:
        stored = await session.get(Appointment, outcome.appointment.id)
    assert stored is not None
    assert stored.starts_at == _TUESDAY_9AM
    assert stored.ends_at == datetime(2026, 8, 18, 10, 0)
    assert stored.session_id == session_id


async def test_a_repeated_booking_replays_rather_than_conflicting_with_itself(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    settings = _chat_settings()
    key = derive_idempotency_key(patient_id, practitioner_id, _TUESDAY_9AM)

    async def attempt() -> scheduling.BookingSuccess | scheduling.BookingRefusal:
        return await scheduling.book_appointment(
            scheduling_channel,
            settings,
            session_id=session_id,
            patient_id=patient_id,
            practitioner_id=practitioner_id,
            starts_at=_TUESDAY_9AM,
            local_now=_LOCAL_NOW,
            idempotency_key=key,
        )

    first = await attempt()
    second = await attempt()

    assert isinstance(first, scheduling.BookingSuccess)
    assert isinstance(second, scheduling.BookingSuccess)
    assert second.appointment.id == first.appointment.id
    assert first.idempotent_replay is False
    assert second.idempotent_replay is True
    assert await _appointment_count() == 1


async def test_an_offered_slot_disappears_from_the_next_availability_call(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    settings = _chat_settings()

    await scheduling.book_appointment(
        scheduling_channel,
        settings,
        session_id=session_id,
        patient_id=patient_id,
        practitioner_id=practitioner_id,
        starts_at=_TUESDAY_9AM,
        local_now=_LOCAL_NOW,
        idempotency_key=derive_idempotency_key(
            patient_id, practitioner_id, _TUESDAY_9AM
        ),
    )

    availability = await scheduling.check_availability(
        scheduling_channel,
        settings,
        session_id=session_id,
        practitioner_id=practitioner_id,
        patient_id=patient_id,
        from_date=_TUESDAY,
        to_date=_TUESDAY,
        local_now=_LOCAL_NOW,
    )

    assert _TUESDAY_9AM not in availability.available_starts
    # Half-open intervals: the hour starting exactly when it ends is still offered.
    assert datetime(2026, 8, 18, 10, 0) in availability.available_starts


async def test_a_domain_refusal_crosses_the_wire_as_a_typed_result(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    settings = _chat_settings()

    outcome = await scheduling.book_appointment(
        scheduling_channel,
        settings,
        session_id=session_id,
        patient_id=patient_id,
        practitioner_id=practitioner_id,
        # A Sunday: outside the default Monday-Friday schedule.
        starts_at=datetime(2026, 8, 23, 10, 0),
        local_now=_LOCAL_NOW,
        idempotency_key=new_id(),
    )

    assert isinstance(outcome, scheduling.BookingRefusal)
    assert outcome.reason is BookingFailureReason.OUTSIDE_SCHEDULE
    assert await _appointment_count() == 0


async def test_a_key_reused_for_a_different_booking_is_a_request_error(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    settings = _chat_settings()
    key = "one-key-two-bookings"

    await scheduling.book_appointment(
        scheduling_channel,
        settings,
        session_id=session_id,
        patient_id=patient_id,
        practitioner_id=practitioner_id,
        starts_at=_TUESDAY_9AM,
        local_now=_LOCAL_NOW,
        idempotency_key=key,
    )

    with pytest.raises(scheduling.SchedulingRequestError):
        await scheduling.book_appointment(
            scheduling_channel,
            settings,
            session_id=session_id,
            patient_id=patient_id,
            practitioner_id=practitioner_id,
            starts_at=datetime(2026, 8, 18, 11, 0),
            local_now=_LOCAL_NOW,
            idempotency_key=key,
        )

    assert await _appointment_count() == 1


async def test_an_unreachable_scheduler_raises_rather_than_fabricating_a_result() -> (
    None
):
    """Nothing listening on the port, so the whole attempt budget is spent and fails."""
    settings = _chat_settings()
    async with grpc.aio.insecure_channel("127.0.0.1:1") as dead_channel:
        with pytest.raises(scheduling.SchedulingUnavailableError):
            await scheduling.book_appointment(
                dead_channel,
                settings,
                session_id=new_id(),
                patient_id=new_id(),
                practitioner_id=new_id(),
                starts_at=_TUESDAY_9AM,
                local_now=_LOCAL_NOW,
                idempotency_key=new_id(),
            )

    assert await _appointment_count() == 0


async def test_the_booking_tools_drive_the_whole_round_trip(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    """The same path the agent takes: tool handlers over the real service."""
    session_id = new_id()
    practitioner_id, patient_id = await _seed(session_id)
    registry = ToolRegistry(
        SCHEDULING_TOOLS,
        ToolContext(
            channel=scheduling_channel,
            settings=_chat_settings(),
            session_id=session_id,
            patient_id=patient_id,
            local_now=_LOCAL_NOW,
        ),
    )

    listed = await registry.dispatch("list_practitioners", {})
    assert listed["practitioners"][0]["id"] == practitioner_id

    offered = await registry.dispatch(
        "check_availability",
        {
            "practitioner_id": practitioner_id,
            "from_date": "2026-08-18",
            "to_date": "2026-08-18",
        },
    )
    assert offered["available_starts"][0] == "2026-08-18T09:00:00"

    booked = await registry.dispatch(
        "book_appointment",
        {
            "practitioner_id": practitioner_id,
            "starts_at": offered["available_starts"][0],
        },
    )

    assert booked["status"] == "booked"
    assert booked["appointment"]["practitioner_full_name"] == "William Osler"
    assert await _appointment_count() == 1


async def test_the_read_only_questions_never_cross_a_patient_or_a_session(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    """SC-004, through the real tool handlers against rows this test wrote itself.

    Two patients in one session and a third in another, each with their own booking.
    A registry bound to one patient must answer for that patient alone - the handlers
    read the patient and session from their bound context, so the question cannot be
    asked about anyone else.
    """
    session_id = new_id()
    other_session_id = new_id()
    practitioner_id, mine = await _seed(session_id)
    settings = _chat_settings()

    async with session_factory() as session:
        sibling = Patient(
            id=str(ULID()),
            session_id=session_id,
            chat_id=new_id(),
            full_name="Bram Stoker",
        )
        session.add(sibling)
        await session.commit()
        sibling_id = sibling.id
    stranger_practitioner, stranger = await _seed(other_session_id)

    for patient_id, practitioner, starts_at in (
        (mine, practitioner_id, _TUESDAY_9AM),
        (sibling_id, practitioner_id, datetime(2026, 8, 18, 11, 0)),
        (stranger, stranger_practitioner, _TUESDAY_9AM),
    ):
        booked = await scheduling.book_appointment(
            scheduling_channel,
            settings,
            session_id=(
                session_id if patient_id in {mine, sibling_id} else other_session_id
            ),
            patient_id=patient_id,
            practitioner_id=practitioner,
            starts_at=starts_at,
            local_now=_LOCAL_NOW,
            idempotency_key=new_id(),
        )
        assert isinstance(booked, scheduling.BookingSuccess)

    registry = ToolRegistry(
        SCHEDULING_TOOLS,
        ToolContext(
            channel=scheduling_channel,
            settings=settings,
            session_id=session_id,
            patient_id=mine,
            local_now=_LOCAL_NOW,
        ),
    )

    appointments = (await registry.dispatch("list_my_appointments", {}))["future"]
    practitioners = (await registry.dispatch("list_practitioners", {}))["practitioners"]

    # Only this patient's own booking, not their session sibling's and not the other
    # session's - even though all three exist in the table right now.
    assert [a["starts_at"] for a in appointments] == ["2026-08-18T09:00:00"]
    assert [p["id"] for p in practitioners] == [practitioner_id]
    assert stranger_practitioner not in [p["id"] for p in practitioners]


async def test_a_patient_with_nothing_booked_is_told_so_rather_than_erroring(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    _, patient_id = await _seed(session_id)
    registry = ToolRegistry(
        SCHEDULING_TOOLS,
        ToolContext(
            channel=scheduling_channel,
            settings=_chat_settings(),
            session_id=session_id,
            patient_id=patient_id,
            local_now=_LOCAL_NOW,
        ),
    )

    result = await registry.dispatch("list_my_appointments", {})

    # Two empty legs, not an error and not one merged empty list: the patient exists
    # and has nothing matching the corner that was asked for.
    assert result == {"future": [], "past": [], "past_truncated": False}
    assert "status" not in result
