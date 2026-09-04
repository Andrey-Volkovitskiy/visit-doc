"""`/patients` — list only.

There is deliberately no create, no delete and no edit here: a patient is created with
its chat, named once at creation, and deleted with its chat, so offering any of them
would let this surface produce a patient with no chat, a chat pointing at nothing, or a
name that disagrees with the one the chat caches.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from scheduler.api.dependencies import require_session_id
from scheduler.db.session import session_factory
from scheduler.domain.models import Patient
from scheduler.domain.schemas import PatientOut
from scheduler.repositories import patient_repository

router = APIRouter()


def _render(patient: Patient) -> PatientOut:
    """Render a patient for the wire."""
    return PatientOut(
        id=patient.id, chat_id=patient.chat_id, full_name=patient.full_name
    )


@router.get("/patients")
async def list_patients(
    session_id: Annotated[str, Depends(require_session_id)],
) -> list[PatientOut]:
    """Return the session's patients, oldest first."""
    async with session_factory() as session:
        patients = await patient_repository.list_for_session(session, session_id)
    return [_render(p) for p in patients]
