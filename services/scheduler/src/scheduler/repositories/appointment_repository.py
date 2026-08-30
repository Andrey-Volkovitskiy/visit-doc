"""Booking and changing: the predicates, the insert, and the conditional update.

Overlap is the one rule this module does not decide. Two exclusion constraints do, at
insert, because that is the only thing that survives two transactions racing for the
same slot - so a `PRACTITIONER_BUSY` or `PATIENT_BUSY` refusal is read back off a failed
insert rather than predicted by a read that could already be stale.

A *change* follows the same principle one step further: it is a single conditional
`UPDATE` whose `WHERE` clause carries the identity, the session scope, the eligibility
rules and the staleness guard together. Nothing is read first. A preceding check would
leave a window in which two changes both pass and the second silently overwrites the
first - and the pairing that matters most, a cancellation racing a move, collides with
no other appointment, so the datastore cannot catch it either. When the statement
matches nothing, a classification read names which reason to report; it decides nothing,
so it cannot reintroduce the race it is reporting on.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from shared_models.scheduling import (
    AppointmentStatus,
    BookingFailureReason,
    ChangeFailureReason,
    StatusFilter,
    TimeFilter,
)
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from ulid import ULID

from scheduler.core.logging import get_logger
from scheduler.domain.availability import Interval, validate_start
from scheduler.domain.models import Appointment, Patient, Practitioner
from scheduler.repositories import practitioner_repository

# The partial unique INDEX, not a constraint: PostgreSQL has no partial UNIQUE
# constraint, so a violation names the index instead. Matching on the old constraint
# name would silently stop recognising a lost key race as a replay.
_IDEMPOTENCY_KEY_CONSTRAINT = "ix_appointments_idempotency_key_standing"

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


@dataclass(frozen=True)
class ChangeApplied:
    """A change whose write moved the row, carrying both sides of that one statement.

    The old values come from the same `UPDATE`, not from a read before it: a separate
    read would describe a "before" state a concurrent change may already have replaced,
    which is a false record rather than a missing one.
    """

    appointment: Appointment
    patient: Patient
    practitioner: Practitioner
    previous_starts_at: datetime
    previous_practitioner_id: str


@dataclass(frozen=True)
class ChangeNoOp:
    """The appointment was already in the state the request asked for.

    Distinct from `ChangeApplied` because the caller must be able to tell one change
    from a change re-sent - the log carries one record per real transition, and this
    one instead.
    """

    appointment: Appointment
    patient: Patient
    practitioner: Practitioner


@dataclass(frozen=True)
class ChangeRefused:
    """A change the service evaluated and declined, with the one reason why."""

    reason: ChangeFailureReason


# A change outcome is exactly one of three things, and every caller must handle all
# three: applied, already-in-that-state, or refused with one reason.
ChangeOutcome = ChangeApplied | ChangeNoOp | ChangeRefused

# The most recent past appointments a listing carries. Past appointments accumulate
# without limit while future ones are bounded by the booking horizon, so only this leg
# needs a cap - and it is sized for what a conversation can read back, not as a clinic
# policy.
PAST_LEG_LIMIT = 20


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
    """Return the *standing* appointment that recorded `idempotency_key`, if any.

    A cancelled appointment holding the key is not one: the key lives as long as the
    appointment stands, so cancelling releases it and the slot rebooks as an ordinary
    new booking. Without the predicate, rebooking a cancelled slot would replay the
    cancelled appointment and report it as a fresh one.
    """
    result = await session.execute(
        select(Appointment).where(
            Appointment.idempotency_key == idempotency_key,
            Appointment.status == AppointmentStatus.STANDING,
        )
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

    Cancelled appointments are absent: they occupy no slot, so a slot one of them holds
    is offered again immediately. The exclusion constraints carry the same predicate, so
    the offer path and the write path agree about what "taken" means.

    Widened by a day at each end so an appointment starting just outside the window but
    running into it is still seen.
    """
    window_start = datetime.combine(from_date, datetime.min.time()) - timedelta(days=1)
    window_end = datetime.combine(to_date, datetime.min.time()) + timedelta(days=2)
    result = await session.execute(
        select(Appointment).where(
            Appointment.session_id == session_id,
            Appointment.status == AppointmentStatus.STANDING,
            or_(
                Appointment.practitioner_id == practitioner_id,
                Appointment.patient_id == patient_id,
            ),
            Appointment.starts_at < window_end,
            Appointment.ends_at > window_start,
        )
    )
    return [Interval(a.starts_at, a.ends_at) for a in result.scalars().all()]


@dataclass(frozen=True)
class AppointmentListing:
    """One patient's appointments, in two separately bounded and ordered legs.

    Two fields rather than one list so neither leg can crowd the other out: twenty
    future appointments must not consume a cap that exists only because past ones
    accumulate without limit.

    `past_truncated` is scoped to the past leg alone, so it can never be read as "the
    whole answer is partial".
    """

    future: list[Appointment]
    past: list[Appointment]
    past_truncated: bool


_STATUSES_BY_FILTER = {
    StatusFilter.STANDING: (AppointmentStatus.STANDING,),
    StatusFilter.CANCELLED: (AppointmentStatus.CANCELLED,),
    StatusFilter.BOTH: (AppointmentStatus.STANDING, AppointmentStatus.CANCELLED),
}


async def list_for_patient(
    session: AsyncSession,
    *,
    session_id: str,
    patient_id: str,
    local_now: datetime,
    time_filter: TimeFilter,
    status_filter: StatusFilter,
) -> AppointmentListing:
    """Return this patient's appointments in the corner of the grid that was asked for.

    Returns: the future leg ascending and unbounded, the past leg descending and capped,
        and whether the past leg elided anything.

    The two axes are independent: one is a comparison against the client's clock, the
    other is stored, so every combination is answerable. The boundary belongs to the
    past: an appointment starting at exactly `local_now` is under way, matching how
    booking treats that same instant as already gone.
    """
    statuses = _STATUSES_BY_FILTER[status_filter]
    future: list[Appointment] = []
    past: list[Appointment] = []
    past_truncated = False

    if time_filter in (TimeFilter.FUTURE, TimeFilter.BOTH):
        result = await session.execute(
            select(Appointment)
            .where(
                Appointment.session_id == session_id,
                Appointment.patient_id == patient_id,
                Appointment.status.in_(statuses),
                Appointment.starts_at > local_now,
            )
            .order_by(Appointment.starts_at.asc())
        )
        future = list(result.scalars().all())

    if time_filter in (TimeFilter.PAST, TimeFilter.BOTH):
        # One row past the cap, so "exactly the cap" and "the cap of more" are
        # distinguishable - a plain `LIMIT 20` would report a complete list of twenty
        # as truncated and an elided one identically.
        result = await session.execute(
            select(Appointment)
            .where(
                Appointment.session_id == session_id,
                Appointment.patient_id == patient_id,
                Appointment.status.in_(statuses),
                Appointment.starts_at <= local_now,
            )
            .order_by(Appointment.starts_at.desc())
            .limit(PAST_LEG_LIMIT + 1)
        )
        probed = list(result.scalars().all())
        past_truncated = len(probed) > PAST_LEG_LIMIT
        past = probed[:PAST_LEG_LIMIT]

    return AppointmentListing(
        future=future, past=past, past_truncated=past_truncated
    )


async def classify_change_failure(
    session: AsyncSession,
    *,
    session_id: str,
    patient_id: str,
    appointment_id: str,
    expected_starts_at: datetime,
    expected_practitioner_id: str,
    local_now: datetime,
) -> ChangeFailureReason:
    """Name which eligibility rule a change that wrote nothing actually broke.

    Returns: the first reason that holds, in the fixed precedence - not found, then
        already cancelled, then already started, then a stale confirmation.

    Runs only after a conditional update matched no row, and decides nothing: its output
    is a reason, never an outcome, so it cannot reintroduce the race it is reporting on.
    Scoped to the session and the patient exactly as that update was, so an appointment
    belonging to anyone else is reported as absent rather than described.

    A row that satisfies every rule still answers `STALE_CONFIRMATION`: the update
    matched nothing, so something changed between it and this read, and that is the
    honest name for it.
    """
    result = await session.execute(
        select(Appointment).where(
            Appointment.id == appointment_id,
            Appointment.session_id == session_id,
            Appointment.patient_id == patient_id,
        )
    )
    appointment = result.scalars().first()
    if appointment is None:
        return ChangeFailureReason.APPOINTMENT_NOT_FOUND
    if appointment.status == AppointmentStatus.CANCELLED:
        return ChangeFailureReason.ALREADY_CANCELLED
    if appointment.starts_at <= local_now:
        return ChangeFailureReason.ALREADY_STARTED
    return ChangeFailureReason.STALE_CONFIRMATION


async def cancel(
    session: AsyncSession,
    *,
    session_id: str,
    patient_id: str,
    appointment_id: str,
    expected_starts_at: datetime,
    expected_practitioner_id: str,
    local_now: datetime,
) -> ChangeOutcome:
    """Cancel one appointment, or report the single reason it was refused.

    Returns: a `ChangeApplied` when the status was set, a `ChangeNoOp` when the
        appointment was already cancelled, or a `ChangeRefused` naming the one rule the
        request broke.

    The record is retained: it keeps its identifier, its practitioner and its times, and
    only stops counting. Both partial exclusion constraints and the partial unique index
    take it from that moment, so the slot is bookable again and the booking key free -
    neither needs a second statement.

    The guard carries only the described state, not a target one: a cancellation asks
    for a status, and the `status = 'standing'` predicate already excludes the row that
    is in the state being asked for. A re-sent cancellation therefore matches nothing
    and is answered by the classification read, which finds it cancelled and reports a
    no-op rather than a conflict.
    """
    old = aliased(Appointment, name="old")
    statement = (
        update(Appointment)
        .where(
            Appointment.id == old.id,
            Appointment.id == appointment_id,
            # FR-018: the scope is a predicate on the write itself. A check applied to
            # the result afterwards is not a check - the row would already have moved.
            Appointment.session_id == session_id,
            Appointment.patient_id == patient_id,
            Appointment.status == AppointmentStatus.STANDING,
            Appointment.starts_at > local_now,
            Appointment.starts_at == expected_starts_at,
            Appointment.practitioner_id == expected_practitioner_id,
        )
        .values(status=AppointmentStatus.CANCELLED)
        .returning(
            old.starts_at,
            old.practitioner_id,
            old.idempotency_key,
        )
    )
    result = await session.execute(statement)
    row = result.first()
    if row is None:
        await session.rollback()
        return await _refuse_change(
            session,
            operation="cancel",
            session_id=session_id,
            patient_id=patient_id,
            appointment_id=appointment_id,
            expected_starts_at=expected_starts_at,
            expected_practitioner_id=expected_practitioner_id,
            local_now=local_now,
            no_op_reason=ChangeFailureReason.ALREADY_CANCELLED,
        )

    await session.commit()
    loaded = await _load_change_context(session, appointment_id)
    if loaded is None:
        # Both parties are gone, so the cascade already removed the appointment. The
        # cancellation stands; there is simply nothing left to describe.
        raise AppointmentVanishedError(appointment_id)
    appointment, patient, practitioner = loaded

    # After the commit, and never awaited for correctness: recording follows a change,
    # it does not gate one.
    logger = get_logger()
    logger.info(
        "appointment.cancelled",
        appointment_id=appointment_id,
        old_starts_at=row.starts_at.isoformat(),
        practitioner_id=row.practitioner_id,
    )
    logger.info(
        "change.key_released",
        appointment_id=appointment_id,
        idempotency_key=row.idempotency_key,
    )
    return ChangeApplied(
        appointment=appointment,
        patient=patient,
        practitioner=practitioner,
        previous_starts_at=row.starts_at,
        previous_practitioner_id=row.practitioner_id,
    )


class AppointmentVanishedError(Exception):
    """Raised when a change committed but its appointment can no longer be read.

    Only reachable when the patient or the practitioner was deleted between the write
    and the read that describes it, taking the appointment by cascade. The change did
    happen, so the caller must not report that nothing did.
    """

    def __init__(self, appointment_id: str) -> None:
        super().__init__(f"appointment {appointment_id} vanished after its change")
        self.appointment_id = appointment_id


async def _refuse_change(
    session: AsyncSession,
    *,
    operation: str,
    session_id: str,
    patient_id: str,
    appointment_id: str,
    expected_starts_at: datetime,
    expected_practitioner_id: str,
    local_now: datetime,
    no_op_reason: ChangeFailureReason | None,
) -> ChangeNoOp | ChangeRefused:
    """Turn a change that wrote nothing into the refusal - or no-op - it actually means.

    Args:
        no_op_reason: The reason that, for this operation, names the state the request
            asked for rather than a rule it broke. `ALREADY_CANCELLED` for a
            cancellation, since reaching that state is what a cancellation is for; None
            for an operation that has no such reason.

    Returns: a `ChangeNoOp` when the appointment is already in the state asked for, or a
        `ChangeRefused` carrying the one reason the precedence resolved to.
    """
    reason = await classify_change_failure(
        session,
        session_id=session_id,
        patient_id=patient_id,
        appointment_id=appointment_id,
        expected_starts_at=expected_starts_at,
        expected_practitioner_id=expected_practitioner_id,
        local_now=local_now,
    )
    logger = get_logger()
    if reason is no_op_reason:
        loaded = await _load_change_context(session, appointment_id)
        if loaded is not None:
            appointment, patient, practitioner = loaded
            logger.info(
                "appointment.unchanged",
                appointment_id=appointment_id,
                operation=operation,
                starts_at=appointment.starts_at.isoformat(),
            )
            return ChangeNoOp(
                appointment=appointment, patient=patient, practitioner=practitioner
            )
        reason = ChangeFailureReason.APPOINTMENT_NOT_FOUND

    logger.info(
        "change.refused",
        appointment_id=appointment_id,
        operation=operation,
        reason=reason,
    )
    return ChangeRefused(reason=reason)


async def _load_change_context(
    session: AsyncSession, appointment_id: str
) -> tuple[Appointment, Patient, Practitioner] | None:
    """Read back an appointment and both its parties, for rendering a change's answer.

    Returns: the appointment with its patient and practitioner, or None if any of the
        three is gone.

    Read after the write rather than before it: the identities are fixed by then, and a
    read beforehand would sit inside the window the conditional update exists to close.
    """
    appointment = await session.get(Appointment, appointment_id)
    if appointment is None:
        return None
    await session.refresh(appointment)
    patient = await session.get(Patient, appointment.patient_id)
    practitioner = await session.get(Practitioner, appointment.practitioner_id)
    if patient is None or practitioner is None:
        return None
    return appointment, patient, practitioner


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
