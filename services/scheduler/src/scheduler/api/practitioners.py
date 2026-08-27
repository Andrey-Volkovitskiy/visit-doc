"""`/practitioners` — the admin surface for creating, editing, and deleting them."""

from datetime import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from shared_models.scheduling import Specialty, Weekday
from sqlalchemy.exc import IntegrityError

from scheduler.api.dependencies import require_session_id
from scheduler.db.session import session_factory
from scheduler.domain.models import Practitioner, WorkingRange
from scheduler.domain.schemas import (
    PractitionerCreate,
    PractitionerOut,
    PractitionerUpdate,
    WorkingRangeIn,
    to_working_range_out,
)
from scheduler.repositories import practitioner_repository

router = APIRouter()

_NAME_CONFLICT = "another practitioner in this session already has that name"
_OVERLAPPING_RANGES = "working ranges on one weekday must not overlap"


def _as_triples(
    schedule: list[WorkingRangeIn],
) -> list[tuple[Weekday, time, time]]:
    """Convert supplied ranges into the repository's own tuple shape.

    Returns: one `(weekday, start_time, end_time)` triple per supplied range, in the
        order they were given.
    """
    return [(r.weekday, r.start_time, r.end_time) for r in schedule]


def _render(
    practitioner: Practitioner, schedule: list[WorkingRange]
) -> PractitionerOut:
    """Render a practitioner and their already-loaded schedule for the wire.

    Takes the schedule rather than loading it, so a list endpoint can load every
    practitioner's ranges in one query instead of one per rendered practitioner.
    """
    return PractitionerOut(
        id=practitioner.id,
        full_name=practitioner.full_name,
        specialty=Specialty(practitioner.specialty),
        appointment_duration_minutes=practitioner.appointment_duration_minutes,
        schedule=[
            to_working_range_out(Weekday(r.weekday), r.start_time, r.end_time)
            for r in schedule
        ],
    )


@router.get("/practitioners")
async def list_practitioners(
    session_id: Annotated[str, Depends(require_session_id)],
) -> list[PractitionerOut]:
    """Return the session's practitioners, oldest first."""
    async with session_factory() as session:
        practitioners = await practitioner_repository.list_for_session(
            session, session_id
        )
        schedules = await practitioner_repository.get_schedules(
            session, [p.id for p in practitioners]
        )
    return [_render(p, schedules[p.id]) for p in practitioners]


@router.post("/practitioners", status_code=201)
async def create_practitioner(
    body: PractitionerCreate,
    session_id: Annotated[str, Depends(require_session_id)],
) -> PractitionerOut:
    """Create a practitioner, applying every default the caller left out.

    Raises:
        HTTPException 409: the caller supplied a name already used in this session.
        HTTPException 422: the supplied ranges overlap on a weekday.
        IntegrityError: propagated from `_conflict_or_unprocessable()` when the write
            was rejected by some other constraint.

    A caller who supplies no name never sees 409: the repository picks a pool name and
    retries it if a concurrent creation took it first, so the conflict never describes
    a decision this caller made.
    """
    async with session_factory() as session:
        try:
            practitioner = await practitioner_repository.create(
                session,
                session_id,
                full_name=body.full_name,
                specialty=body.specialty or practitioner_repository.DEFAULT_SPECIALTY,
                appointment_duration_minutes=(
                    body.appointment_duration_minutes
                    or practitioner_repository.DEFAULT_DURATION_MINUTES
                ),
                schedule=(
                    None if body.schedule is None else _as_triples(body.schedule)
                ),
            )
        except IntegrityError as exc:
            await session.rollback()
            raise _conflict_or_unprocessable(exc) from exc
        schedule = await practitioner_repository.get_schedule(session, practitioner.id)
    return _render(practitioner, schedule)


@router.patch("/practitioners/{practitioner_id}")
async def update_practitioner(
    practitioner_id: str,
    body: PractitionerUpdate,
    session_id: Annotated[str, Depends(require_session_id)],
) -> PractitionerOut:
    """Edit a practitioner; omitted fields are left untouched.

    Raises:
        HTTPException 404: unknown practitioner, or one belonging to another session.
        HTTPException 409: the new name is already used in this session.
        HTTPException 422: the supplied ranges overlap on a weekday.
        IntegrityError: propagated from `_conflict_or_unprocessable()` when the write
            was rejected by some other constraint.

    The field edits and the schedule are applied as one transaction, so a rejected
    request leaves the practitioner exactly as it found them.

    Narrowing a schedule past an existing appointment succeeds: that appointment keeps
    the time it was agreed at, and goes on blocking anything that overlaps it.
    """
    async with session_factory() as session:
        practitioner = await practitioner_repository.get(
            session, practitioner_id, session_id
        )
        if practitioner is None:
            raise HTTPException(status_code=404, detail="practitioner not found")

        if body.full_name is not None:
            practitioner.full_name = body.full_name
        if body.specialty is not None:
            practitioner.specialty = body.specialty
        if body.appointment_duration_minutes is not None:
            practitioner.appointment_duration_minutes = (
                body.appointment_duration_minutes
            )
        session.add(practitioner)
        try:
            # One transaction for both halves: committing the field edits first would
            # leave them applied behind the 422 a rejected schedule returns, so the
            # caller would be told nothing changed while the practitioner had a new
            # appointment grid.
            if body.schedule is not None:
                await practitioner_repository.replace_schedule(
                    session, practitioner.id, _as_triples(body.schedule)
                )
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise _conflict_or_unprocessable(exc) from exc
        schedule = await practitioner_repository.get_schedule(session, practitioner.id)
    return _render(practitioner, schedule)


@router.delete("/practitioners/{practitioner_id}", status_code=204)
async def delete_practitioner(
    practitioner_id: str,
    session_id: Annotated[str, Depends(require_session_id)],
) -> None:
    """Delete a practitioner and, by database cascade, their appointments.

    Raises: HTTPException 404 if the practitioner belongs to another session.

    Other patients' appointments with other practitioners are untouched - the cascade
    is keyed on this practitioner alone.
    """
    async with session_factory() as session:
        practitioner = await practitioner_repository.get(
            session, practitioner_id, session_id
        )
        if practitioner is None:
            raise HTTPException(status_code=404, detail="practitioner not found")
        await practitioner_repository.delete(session, practitioner)


def _conflict_or_unprocessable(exc: IntegrityError) -> HTTPException:
    """Turn a rejected write into the status its violated constraint stands for.

    Raises: IntegrityError re-raised when the violated constraint is none of the ones
        these routes can produce, since reporting an unrecognized one as a client error
        would send the operator to debug a schedule that is not what failed.
    """
    detail = str(exc.orig)
    if practitioner_repository.NAME_UNIQUE_CONSTRAINT in detail:
        return HTTPException(status_code=409, detail=_NAME_CONFLICT)
    if any(
        constraint in detail for constraint in practitioner_repository.RANGE_CONSTRAINTS
    ):
        return HTTPException(status_code=422, detail=_OVERLAPPING_RANGES)
    raise exc
