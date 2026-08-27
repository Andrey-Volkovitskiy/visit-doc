"""Chat's rename call against a real scheduling servicer and a real database.

The chat unit tier fakes this boundary; this is where the contract those fakes stand
in for is proven - that a rename really lands in the scheduler's own table, that the
value chat caches is the one the scheduler stored, and that a name already held in the
session comes back as a typed refusal rather than an error.
"""

import grpc
from chat.clients import scheduling
from chat.core.config import Settings as ChatSettings
from scheduler.db.session import session_factory
from scheduler.domain.models import Patient
from shared_models.scheduling import RenameFailureReason
from sqlalchemy import select
from ulid import ULID

from .conftest import new_id


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


async def _seed_patient(session_id: str, full_name: str) -> str:
    """Create one patient directly in the scheduler's database, returning its id."""
    async with session_factory() as session:
        patient = Patient(
            id=str(ULID()),
            session_id=session_id,
            chat_id=new_id(),
            full_name=full_name,
        )
        session.add(patient)
        await session.commit()
        return patient.id


async def _stored_name(patient_id: str) -> str:
    async with session_factory() as session:
        result = await session.execute(
            select(Patient.full_name).where(Patient.id == patient_id)
        )
        return str(result.scalar_one())


async def test_the_rename_round_trip_updates_the_schedulers_row(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    patient_id = await _seed_patient(session_id, "Ada Lovelace")

    result = await scheduling.rename_patient(
        scheduling_channel,
        _chat_settings(),
        session_id=session_id,
        patient_id=patient_id,
        full_name="Grace Hopper",
    )

    assert isinstance(result, scheduling.PatientInfo)
    assert result.full_name == "Grace Hopper"
    # The value chat would cache is the one that is actually stored.
    assert await _stored_name(patient_id) == "Grace Hopper"


async def test_renaming_twice_to_the_same_name_is_idempotent(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    # What lets a caller whose deadline expired simply send the request again.
    session_id = new_id()
    patient_id = await _seed_patient(session_id, "Ada Lovelace")
    settings = _chat_settings()

    for _ in range(2):
        result = await scheduling.rename_patient(
            scheduling_channel,
            settings,
            session_id=session_id,
            patient_id=patient_id,
            full_name="Grace Hopper",
        )
        assert isinstance(result, scheduling.PatientInfo)

    assert await _stored_name(patient_id) == "Grace Hopper"


async def test_a_name_taken_in_the_session_comes_back_as_a_refusal(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    await _seed_patient(session_id, "Ada Lovelace")
    second_id = await _seed_patient(session_id, "Bram Stoker")

    result = await scheduling.rename_patient(
        scheduling_channel,
        _chat_settings(),
        session_id=session_id,
        patient_id=second_id,
        full_name="Ada Lovelace",
    )

    assert isinstance(result, scheduling.RenameRefusal)
    assert result.reason is RenameFailureReason.NAME_TAKEN
    assert await _stored_name(second_id) == "Bram Stoker"


async def test_another_sessions_patient_does_not_resolve(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    owner_session_id = new_id()
    patient_id = await _seed_patient(owner_session_id, "Ada Lovelace")

    result = await scheduling.rename_patient(
        scheduling_channel,
        _chat_settings(),
        session_id=new_id(),
        patient_id=patient_id,
        full_name="Grace Hopper",
    )

    assert isinstance(result, scheduling.RenameRefusal)
    assert result.reason is RenameFailureReason.PATIENT_NOT_FOUND
    assert await _stored_name(patient_id) == "Ada Lovelace"
