"""Patient reads and writes, scoped to one session.

A patient is created with its chat and deleted with it - there is deliberately no
free-standing create or delete, only the create-if-absent that provisioning calls and
the delete-for-chat that chat deletion calls.
"""

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from scheduler.core.logging import get_logger
from scheduler.domain.models import Appointment, Patient
from scheduler.domain.name_pools import WRITER_POOL
from scheduler.domain.naming import MAX_NAME_ATTEMPTS, NamedEntity, allocate_name


class ChatSessionMismatchError(Exception):
    """Raised when `chat_id` exists but is paired with a patient of another session.

    Kept distinct from "no patient yet" so that provisioning cannot answer a caller
    with a row it does not own: the chat is unique across the whole table, so a
    mismatched one is not a chat this session may create a patient for.
    """


async def get_by_chat_id(
    session: AsyncSession, chat_id: str, session_id: str
) -> Patient | None:
    """Return the patient paired with `chat_id` in `session_id`, or None if none yet.

    Scoped on the read like every other lookup here: `chat_id` being unique means no
    two rows collide on it, which says nothing about who may read the row it names.
    """
    result = await session.execute(
        select(Patient).where(
            Patient.chat_id == chat_id, Patient.session_id == session_id
        )
    )
    return result.scalars().first()


async def _chat_id_is_taken(session: AsyncSession, chat_id: str) -> bool:
    """Whether any session's patient already holds `chat_id`.

    Asked only after an unscoped write conflict, to tell a name collision apart from a
    chat that belongs to someone else - never to return that patient.
    """
    result = await session.execute(
        select(func.count()).select_from(Patient).where(Patient.chat_id == chat_id)
    )
    return int(result.scalar_one()) > 0


async def get(
    session: AsyncSession, patient_id: str, session_id: str
) -> Patient | None:
    """Return `patient_id` if it belongs to `session_id`, else None."""
    result = await session.execute(
        select(Patient).where(
            Patient.id == patient_id, Patient.session_id == session_id
        )
    )
    return result.scalars().first()


async def list_for_session(session: AsyncSession, session_id: str) -> list[Patient]:
    """Return the session's patients, oldest first."""
    result = await session.execute(
        select(Patient)
        .where(Patient.session_id == session_id)
        .order_by(Patient.created_at.asc(), Patient.id.asc())
    )
    return list(result.scalars().all())


async def taken_names(session: AsyncSession, session_id: str) -> set[str]:
    """Return every patient name already used in `session_id`."""
    result = await session.execute(
        select(Patient.full_name).where(Patient.session_id == session_id)
    )
    return set(result.scalars().all())


async def create_if_absent(
    session: AsyncSession, session_id: str, chat_id: str
) -> tuple[Patient, bool]:
    """Return this chat's patient, creating one with the next pool name if absent.

    Returns: the patient, and whether this call created it.

    Idempotent on `chat_id`, which is what makes a later retry safe after a failed
    provisioning attempt: a second call returns the same patient rather than a second
    one. A name chosen and then taken by a concurrent creation is retried with a freshly
    computed name rather than failing the call.

    Raises:
        ChatSessionMismatchError: `chat_id` already belongs to another session.
        IntegrityError: a name collision persists past the retry budget.
    """
    existing = await get_by_chat_id(session, chat_id, session_id)
    if existing is not None:
        return existing, False

    for attempt in range(1, MAX_NAME_ATTEMPTS + 1):
        full_name = allocate_name(
            WRITER_POOL,
            await taken_names(session, session_id),
            entity=NamedEntity.PATIENT,
        )
        patient = Patient(
            id=str(ULID()),
            session_id=session_id,
            chat_id=chat_id,
            full_name=full_name,
        )
        session.add(patient)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            # Either a concurrent creation took the name, or something took the chat
            # itself. The chat is unique, so a concurrent creation *in this session*
            # resolves to returning their patient - while a chat held by a different
            # session is not ours to answer with, and stops here.
            concurrent = await get_by_chat_id(session, chat_id, session_id)
            if concurrent is not None:
                return concurrent, False
            if await _chat_id_is_taken(session, chat_id):
                raise ChatSessionMismatchError(chat_id) from exc
            get_logger().warning(
                "name.collision_retried", entity=NamedEntity.PATIENT, attempt=attempt
            )
            continue
        await session.refresh(patient)
        return patient, True

    raise IntegrityError("patient name allocation exhausted its retries", None, None)  # type: ignore[arg-type]


async def rename(session: AsyncSession, patient: Patient, full_name: str) -> Patient:
    """Rename `patient`.

    Raises: IntegrityError if another patient in the same session already has that name.
    """
    patient.full_name = full_name
    session.add(patient)
    await session.commit()
    await session.refresh(patient)
    return patient


async def delete_for_chat(
    session: AsyncSession, session_id: str, chat_id: str
) -> tuple[bool, int]:
    """Delete this chat's patient and, by cascade, that patient's appointments.

    Returns: whether a patient existed, and how many appointments went with it.

    Idempotent: deleting an already-absent patient succeeds and reports nothing removed.
    A chat belonging to another session reads as absent, which is the same answer the
    contract gives everywhere else - and, unlike a check applied after an unscoped read,
    it never loads the other session's row in the first place.
    """
    patient = await get_by_chat_id(session, chat_id, session_id)
    if patient is None:
        return False, 0

    count = await session.execute(
        select(func.count())
        .select_from(Appointment)
        .where(Appointment.patient_id == patient.id)
    )
    appointments_deleted = int(count.scalar_one())

    await session.delete(patient)
    await session.commit()
    get_logger().info(
        "patient.deleted",
        chat_id=chat_id,
        patient_id=patient.id,
        appointments_deleted=appointments_deleted,
    )
    return True, appointments_deleted
