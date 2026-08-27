"""The cross-store cascade, against a real scheduling servicer and a real database.

Deleting a chat has to remove data that lives in two databases with no foreign key
between them. This is where that ordering is actually proven.
"""

from datetime import datetime

import grpc
import pytest
from chat.clients import scheduling
from chat.core.config import Settings as ChatSettings
from scheduler.db.session import session_factory
from scheduler.domain.models import Appointment, Patient, Practitioner, WorkingRange
from shared_models.scheduling import Specialty
from sqlalchemy import func, select
from ulid import ULID

from .conftest import DEFAULT_SCHEDULE, new_id

_TUESDAY_9AM = datetime(2026, 8, 18, 9, 0)
_LOCAL_NOW = datetime(2026, 8, 17, 8, 0)


def _chat_settings() -> ChatSettings:
    return ChatSettings(
        DATABASE_URL="postgresql+asyncpg://u:p@localhost:5432/unused",
        QDRANT_URL="http://localhost:6333",
        ANTHROPIC_API_KEY="unused",
        VOYAGE_API_KEY="unused",
    )


async def _seed_chat(session_id: str, chat_id: str, full_name: str) -> tuple[str, str]:
    """Create a practitioner and a patient for `chat_id`.

    Returns: the practitioner's id and the patient's id.
    """
    async with session_factory() as session:
        practitioner = Practitioner(
            id=str(ULID()),
            session_id=session_id,
            full_name=f"Dr {full_name}",
            specialty=Specialty.GENERAL_PRACTICE,
            appointment_duration_minutes=60,
        )
        patient = Patient(
            id=str(ULID()),
            session_id=session_id,
            chat_id=chat_id,
            full_name=full_name,
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


async def _count(model: type[Patient] | type[Appointment]) -> int:
    async with session_factory() as session:
        result = await session.execute(select(func.count()).select_from(model))
        return int(result.scalar_one())


async def test_deleting_a_chats_patient_takes_their_appointments_with_it(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    chat_id = new_id()
    practitioner_id, patient_id = await _seed_chat(session_id, chat_id, "Ada")
    settings = _chat_settings()
    await scheduling.book_appointment(
        scheduling_channel,
        settings,
        session_id=session_id,
        patient_id=patient_id,
        practitioner_id=practitioner_id,
        starts_at=_TUESDAY_9AM,
        local_now=_LOCAL_NOW,
        idempotency_key=new_id(),
    )
    assert await _count(Appointment) == 1

    result = await scheduling.delete_patient_for_chat(
        scheduling_channel, settings, session_id=session_id, chat_id=chat_id
    )

    assert result.patient_existed is True
    assert result.appointments_deleted == 1
    assert await _count(Patient) == 0
    assert await _count(Appointment) == 0


async def test_deleting_one_chat_leaves_the_sessions_other_patient_intact(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    doomed_chat = new_id()
    kept_chat = new_id()
    doomed_practitioner, doomed_patient = await _seed_chat(
        session_id, doomed_chat, "Ada"
    )
    _, kept_patient = await _seed_chat(session_id, kept_chat, "Bram")
    settings = _chat_settings()
    for patient_id, starts_at in (
        (doomed_patient, _TUESDAY_9AM),
        (kept_patient, datetime(2026, 8, 18, 11, 0)),
    ):
        await scheduling.book_appointment(
            scheduling_channel,
            settings,
            session_id=session_id,
            patient_id=patient_id,
            practitioner_id=doomed_practitioner,
            starts_at=starts_at,
            local_now=_LOCAL_NOW,
            idempotency_key=new_id(),
        )

    await scheduling.delete_patient_for_chat(
        scheduling_channel, settings, session_id=session_id, chat_id=doomed_chat
    )

    assert await _count(Patient) == 1
    assert await _count(Appointment) == 1
    async with session_factory() as session:
        surviving = await session.get(Patient, kept_patient)
    assert surviving is not None
    assert surviving.full_name == "Bram"


async def test_a_booking_arriving_after_the_deletion_is_refused(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    """No appointment outlives its patient - the foreign key says so, not a check."""
    session_id = new_id()
    chat_id = new_id()
    practitioner_id, patient_id = await _seed_chat(session_id, chat_id, "Ada")
    settings = _chat_settings()
    await scheduling.delete_patient_for_chat(
        scheduling_channel, settings, session_id=session_id, chat_id=chat_id
    )

    outcome = await scheduling.book_appointment(
        scheduling_channel,
        settings,
        session_id=session_id,
        patient_id=patient_id,
        practitioner_id=practitioner_id,
        starts_at=_TUESDAY_9AM,
        local_now=_LOCAL_NOW,
        idempotency_key=new_id(),
    )

    assert isinstance(outcome, scheduling.BookingRefusal)
    assert await _count(Appointment) == 0


async def test_an_unreachable_scheduler_deletes_nothing(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    chat_id = new_id()
    await _seed_chat(session_id, chat_id, "Ada")

    async with grpc.aio.insecure_channel("127.0.0.1:1") as dead_channel:
        with pytest.raises(scheduling.SchedulingUnavailableError):
            await scheduling.delete_patient_for_chat(
                dead_channel,
                _chat_settings(),
                session_id=session_id,
                chat_id=chat_id,
            )

    assert await _count(Patient) == 1


async def test_deletion_is_idempotent_across_the_wire(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    chat_id = new_id()
    await _seed_chat(session_id, chat_id, "Ada")
    settings = _chat_settings()

    first = await scheduling.delete_patient_for_chat(
        scheduling_channel, settings, session_id=session_id, chat_id=chat_id
    )
    second = await scheduling.delete_patient_for_chat(
        scheduling_channel, settings, session_id=session_id, chat_id=chat_id
    )

    assert first.patient_existed is True
    assert second.patient_existed is False
    assert second.appointments_deleted == 0
