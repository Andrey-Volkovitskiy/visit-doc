"""`/patients` — list and rename only.

There is deliberately no create and no delete here: a patient is created with its chat
and deleted with it, so offering either would let this surface produce a patient with no
chat, or a chat pointing at nothing.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError

from scheduler.api.dependencies import require_session_id
from scheduler.db.session import session_factory
from scheduler.domain.models import Patient
from scheduler.domain.schemas import PatientOut, PatientUpdate
from scheduler.repositories import patient_repository

router = APIRouter()

_NAME_CONFLICT = "another patient in this session already has that name"


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


@router.patch("/patients/{patient_id}")
async def rename_patient(
    patient_id: str,
    body: PatientUpdate,
    session_id: Annotated[str, Depends(require_session_id)],
) -> PatientOut:
    """Rename a patient.

    Raises:
        HTTPException 404: unknown patient, or one belonging to another session.
        HTTPException 409: another patient in this session already has that name.
    """
    async with session_factory() as session:
        patient = await patient_repository.get(session, patient_id, session_id)
        if patient is None:
            raise HTTPException(status_code=404, detail="patient not found")
        try:
            renamed = await patient_repository.rename(session, patient, body.full_name)
        except IntegrityError as exc:
            await session.rollback()
            raise HTTPException(status_code=409, detail=_NAME_CONFLICT) from exc
        return _render(renamed)
