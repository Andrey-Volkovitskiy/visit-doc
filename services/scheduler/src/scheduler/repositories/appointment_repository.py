"""Booking: the idempotency check, the creation-time predicates, and the insert.

Overlap is the one rule this module does not decide. Two exclusion constraints do, at
insert, because that is the only thing that survives two transactions racing for the
same slot - so a `PRACTITIONER_BUSY` or `PATIENT_BUSY` refusal is read back off a failed
insert rather than predicted by a read that could already be stale.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from shared_models.scheduling import BookingFailureReason
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from scheduler.core.logging import get_logger
from scheduler.domain.availability import Interval, validate_start
from scheduler.domain.models import Appointment, Patient, Practitioner
from scheduler.repositories import practitioner_repository

_IDEMPOTENCY_KEY_CONSTRAINT = "appointments_idempotency_key_unique"

# Every constraint an insert can violate, and the refusal it stands for. A table rather
# than a branch per constraint, so adding one is a line here and the race-lost warning
# is emitted from a single place. The idempotency key is deliberately absent: it means a
# replay, not a refusal, and is handled separately below.
_REFUSAL_BY_CONSTRAINT = {
    "appointments_practitioner_no_overlap": BookingFailureReason.PRACTITIONER_BUSY,
    "appointments_patient_no_overlap": BookingFailureReason.PATIENT_BUSY,
    # The row was deleted between the lookup above and this insert - the foreign key,
    # not application logic, is what stops an appointment outliving either party.
    "appointments_patient_id_fkey": BookingFailureReason.PATIENT_NOT_FOUND,
    "appointments_practitioner_id_fkey": BookingFailureReason.PRACTITIONER_NOT_FOUND,
}


@dataclass(frozen=True)
class BookingCreated:
    """A booking that exists, created now or replayed from an identical earlier one."""

    appointment: Appointment
    patient: Patient
    practitioner: Practitioner
    idempotent_replay: bool


@dataclass(frozen=True)
class BookingRefused:
    """A booking the service evaluated and declined, with the one reason why."""

    reason: BookingFailureReason


class IdempotencyKeyMismatchError(Exception):
    """Raised when a used key arrives with a different patient, practitioner, or start.

    Always a caller defect: the key is derived from exactly those three fields, so a
    mismatch means the derivation broke. Answered with an error status rather than a
    refusal, because there is nothing for the patient to choose differently - and
    replaying the stored appointment instead would confirm a time they never asked for.
    """

    def __init__(
        self, stored_appointment_id: str, mismatched_fields: list[str]
    ) -> None:
        super().__init__(
            f"idempotency key reused with different {', '.join(mismatched_fields)}"
        )
        self.stored_appointment_id = stored_appointment_id
        self.mismatched_fields = mismatched_fields


async def get_by_idempotency_key(
    session: AsyncSession, idempotency_key: str
) -> Appointment | None:
    """Return the appointment that recorded `idempotency_key`, if any."""
    result = await session.execute(
        select(Appointment).where(Appointment.idempotency_key == idempotency_key)
    )
    return result.scalars().first()


async def busy_intervals(
    session: AsyncSession,
    *,
    session_id: str,
    practitioner_id: str,
    patient_id: str,
    from_date: date,
    to_date: date,
) -> list[Interval]:
    """Return every interval already taken by this practitioner or this patient.

    Both sides matter: availability is patient-relative, so a slot colliding with the
    patient's own appointment with a *different* practitioner must not be offered, or
    booking it would then be refused by the patient-overlap rule.

    Scoped to `session_id` like every other read: without it, an id from another session
    would still subtract that session's appointments from the answer, so the times it
    holds could be inferred from which slots went missing.

    Widened by a day at each end so an appointment starting just outside the window but
    running into it is still seen.
    """
    window_start = datetime.combine(from_date, datetime.min.time()) - timedelta(days=1)
    window_end = datetime.combine(to_date, datetime.min.time()) + timedelta(days=2)
    result = await session.execute(
        select(Appointment).where(
            Appointment.session_id == session_id,
            or_(
                Appointment.practitioner_id == practitioner_id,
                Appointment.patient_id == patient_id,
            ),
            Appointment.starts_at < window_end,
            Appointment.ends_at > window_start,
        )
    )
    return [Interval(a.starts_at, a.ends_at) for a in result.scalars().all()]


async def list_upcoming(
    session: AsyncSession, *, session_id: str, patient_id: str, local_now: datetime
) -> list[Appointment]:
    """Return this patient's appointments starting strictly after `local_now`.

    An appointment already under way is absent from this list while still blocking any
    booking that overlaps it - the two are different questions.
    """
    result = await session.execute(
        select(Appointment)
        .where(
            Appointment.session_id == session_id,
            Appointment.patient_id == patient_id,
            Appointment.starts_at > local_now,
        )
        .order_by(Appointment.starts_at.asc())
    )
    return list(result.scalars().all())


async def book(
    session: AsyncSession,
    *,
    session_id: str,
    patient_id: str,
    practitioner_id: str,
    starts_at: datetime,
    local_now: datetime,
    idempotency_key: str,
    horizon_days: int,
) -> BookingCreated | BookingRefused:
    """Create one appointment, or return the single reason it was refused.

    Returns: a `BookingCreated` when the appointment exists, or a `BookingRefused`
        naming the first rule the attempt broke, in the fixed refusal precedence.

    Raises:
        IdempotencyKeyMismatchError: `idempotency_key` was already recorded against a
            different patient, practitioner, or start time.
        IntegrityError: propagated from `_resolve_insert_conflict()` when the insert was
            rejected by a constraint this operation cannot produce.

    The key is written only on a successful insert, so a refused attempt leaves it free
    to be tried again once the patient picks a different time.
    """
    logger = get_logger()
    logger.info(
        "booking.attempted",
        patient_id=patient_id,
        practitioner_id=practitioner_id,
        starts_at=starts_at.isoformat(),
        idempotency_key=idempotency_key,
    )

    replayed = await _replay_if_recorded(
        session,
        session_id=session_id,
        idempotency_key=idempotency_key,
        patient_id=patient_id,
        practitioner_id=practitioner_id,
        starts_at=starts_at,
    )
    if replayed is not None:
        return replayed

    practitioner = await practitioner_repository.get(
        session, practitioner_id, session_id
    )
    if practitioner is None:
        return _refuse(BookingFailureReason.PRACTITIONER_NOT_FOUND, starts_at)
    patient = await _get_patient(session, patient_id, session_id)
    if patient is None:
        return _refuse(BookingFailureReason.PATIENT_NOT_FOUND, starts_at)

    schedule = practitioner_repository.to_daily_ranges(
        await practitioner_repository.get_schedule(session, practitioner.id)
    )
    reason = validate_start(
        starts_at,
        schedule=schedule,
        duration_minutes=practitioner.appointment_duration_minutes,
        local_now=local_now,
        horizon_days=horizon_days,
    )
    if reason is not None:
        return _refuse(reason, starts_at)

    ends_at = starts_at + timedelta(minutes=practitioner.appointment_duration_minutes)
    appointment = Appointment(
        id=str(ULID()),
        session_id=session_id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
        starts_at=starts_at,
        ends_at=ends_at,
        idempotency_key=idempotency_key,
    )
    session.add(appointment)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        return await _resolve_insert_conflict(
            session,
            exc,
            session_id=session_id,
            idempotency_key=idempotency_key,
            patient_id=patient_id,
            practitioner_id=practitioner_id,
            starts_at=starts_at,
        )

    logger.info(
        "booking.created",
        appointment_id=appointment.id,
        starts_at=starts_at.isoformat(),
        ends_at=ends_at.isoformat(),
    )
    return BookingCreated(
        appointment=appointment,
        patient=patient,
        practitioner=practitioner,
        idempotent_replay=False,
    )


async def _replay_if_recorded(
    session: AsyncSession,
    *,
    session_id: str,
    idempotency_key: str,
    patient_id: str,
    practitioner_id: str,
    starts_at: datetime,
) -> BookingCreated | None:
    """Return the appointment this key already recorded, if the request matches it.

    Returns: the original booking when the key was used for exactly this request, or
        None when the key is unused and the attempt should proceed.

    Raises: IdempotencyKeyMismatchError if the key was used for a different booking.

    A key recorded by another session is treated as unused rather than replayed: the key
    is global, but the appointment behind it is not, and replaying it here would return
    that session's appointment - and both parties' names - to a caller the ordinary
    session-scoped lookups below would have refused.
    """
    stored = await get_by_idempotency_key(session, idempotency_key)
    if stored is None:
        return None
    if stored.session_id != session_id:
        get_logger().warning(
            "booking.key_foreign_session",
            idempotency_key=idempotency_key,
            stored_appointment_id=stored.id,
        )
        return None

    mismatched = [
        name
        for name, stored_value, requested in (
            ("patient", stored.patient_id, patient_id),
            ("practitioner", stored.practitioner_id, practitioner_id),
            ("starts_at", stored.starts_at, starts_at),
        )
        if stored_value != requested
    ]
    if mismatched:
        get_logger().error(
            "booking.key_mismatch",
            idempotency_key=idempotency_key,
            stored_appointment_id=stored.id,
            mismatched_fields=mismatched,
        )
        raise IdempotencyKeyMismatchError(stored.id, mismatched)

    patient = await session.get(Patient, stored.patient_id)
    practitioner = await session.get(Practitioner, stored.practitioner_id)
    if patient is None or practitioner is None:
        # The appointment's own cascades make this unreachable; treated as an unused
        # key rather than crashing if it ever happens.
        return None

    get_logger().info(
        "booking.replayed",
        appointment_id=stored.id,
        idempotency_key=idempotency_key,
    )
    return BookingCreated(
        appointment=stored,
        patient=patient,
        practitioner=practitioner,
        idempotent_replay=True,
    )


async def _resolve_insert_conflict(
    session: AsyncSession,
    exc: IntegrityError,
    *,
    session_id: str,
    idempotency_key: str,
    patient_id: str,
    practitioner_id: str,
    starts_at: datetime,
) -> BookingCreated | BookingRefused:
    """Turn a rejected insert into the refusal - or the replay - it actually means.

    Returns: the original booking when a concurrent attempt won the key race with an
        identical request, or the refusal the violated constraint stands for.

    Raises:
        IdempotencyKeyMismatchError: the key race was lost to a *different* booking.
        IntegrityError: re-raised when the violated constraint is none of the ones this
            operation can produce, since guessing would hide a real schema problem.
    """
    detail = str(exc.orig)
    logger = get_logger()

    if _IDEMPOTENCY_KEY_CONSTRAINT in detail:
        # Two identical attempts raced. The loser re-reads and re-runs the same match
        # check the happy path does, so it reports the winner's appointment.
        replayed = await _replay_if_recorded(
            session,
            session_id=session_id,
            idempotency_key=idempotency_key,
            patient_id=patient_id,
            practitioner_id=practitioner_id,
            starts_at=starts_at,
        )
        if replayed is not None:
            return replayed
        raise exc

    for constraint, reason in _REFUSAL_BY_CONSTRAINT.items():
        if constraint in detail:
            logger.warning(
                "booking.race_lost", reason=reason, starts_at=starts_at.isoformat()
            )
            return _refuse(reason, starts_at)
    raise exc


def _refuse(reason: BookingFailureReason, starts_at: datetime) -> BookingRefused:
    """Log and return one evaluated refusal."""
    get_logger().info("booking.refused", reason=reason, starts_at=starts_at.isoformat())
    return BookingRefused(reason=reason)


async def _get_patient(
    session: AsyncSession, patient_id: str, session_id: str
) -> Patient | None:
    """Return `patient_id` if it belongs to `session_id`, else None."""
    result = await session.execute(
        select(Patient).where(
            Patient.id == patient_id, Patient.session_id == session_id
        )
    )
    return result.scalars().first()
