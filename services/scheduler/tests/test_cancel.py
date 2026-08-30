"""Tests for `cancel()`: the one conditional UPDATE, and what it refuses.

The guard is a `WHERE` clause, not a preceding check, and that is the property these
tests are shaped around: every refusal here must leave the row byte-identical, and the
only way to observe the guard is to change the row underneath a confirmation and watch
the write decline to match it.
"""

from datetime import datetime, timedelta

import pytest
from scheduler.repositories import appointment_repository
from scheduler.repositories.appointment_repository import (
    ChangeApplied,
    ChangeNoOp,
    ChangeRefused,
)
from shared_models.scheduling import AppointmentStatus, ChangeFailureReason
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from .conftest import (
    make_appointment,
    new_id,
    seed_patient,
    seed_practitioner,
)

_TUESDAY_9AM = datetime(2026, 8, 18, 9, 0)
_TUESDAY_10AM = datetime(2026, 8, 18, 10, 0)
_LOCAL_NOW = datetime(2026, 8, 17, 8, 0)


class _Booked:
    """One standing appointment and the ids a change request needs to name it."""

    def __init__(
        self,
        session_id: str,
        patient_id: str,
        practitioner_id: str,
        appointment_id: str,
        idempotency_key: str,
    ) -> None:
        self.session_id = session_id
        self.patient_id = patient_id
        self.practitioner_id = practitioner_id
        self.appointment_id = appointment_id
        self.idempotency_key = idempotency_key


async def _seed(
    session: AsyncSession,
    *,
    starts_at: datetime = _TUESDAY_9AM,
    status: AppointmentStatus = AppointmentStatus.STANDING,
) -> _Booked:
    """Write one appointment straight to the table, bypassing the booking rules."""
    session_id = new_id()
    practitioner = await seed_practitioner(session, session_id)
    patient = await seed_patient(session, session_id)
    appointment = make_appointment(
        session_id,
        patient.id,
        practitioner.id,
        starts_at,
        starts_at + timedelta(hours=1),
        status=status,
    )
    session.add(appointment)
    await session.commit()
    return _Booked(
        session_id,
        patient.id,
        practitioner.id,
        appointment.id,
        appointment.idempotency_key,
    )


async def _cancel(
    session: AsyncSession,
    booked: _Booked,
    *,
    session_id: str | None = None,
    expected_starts_at: datetime = _TUESDAY_9AM,
    expected_practitioner_id: str | None = None,
    local_now: datetime = _LOCAL_NOW,
) -> ChangeApplied | ChangeNoOp | ChangeRefused:
    return await appointment_repository.cancel(
        session,
        session_id=session_id if session_id is not None else booked.session_id,
        patient_id=booked.patient_id,
        appointment_id=booked.appointment_id,
        expected_starts_at=expected_starts_at,
        expected_practitioner_id=(
            expected_practitioner_id
            if expected_practitioner_id is not None
            else booked.practitioner_id
        ),
        local_now=local_now,
    )


async def _row(session: AsyncSession, appointment_id: str) -> object:
    from scheduler.domain.models import Appointment

    session.expire_all()
    return await session.get(Appointment, appointment_id)


async def test_a_cancellation_sets_the_status_and_returns_both_sides(
    db_session: AsyncSession,
) -> None:
    booked = await _seed(db_session)

    outcome = await _cancel(db_session, booked)

    assert isinstance(outcome, ChangeApplied)
    assert outcome.appointment.status == AppointmentStatus.CANCELLED
    assert outcome.previous_starts_at == _TUESDAY_9AM
    assert outcome.previous_practitioner_id == booked.practitioner_id


async def test_a_cancellation_keeps_the_identifier_times_and_practitioner(
    db_session: AsyncSession,
) -> None:
    # FR-009: the record survives with everything but its status intact, which is what
    # makes "what have I cancelled?" answerable at all.
    booked = await _seed(db_session)

    await _cancel(db_session, booked)

    row = await _row(db_session, booked.appointment_id)
    assert row is not None
    assert row.id == booked.appointment_id  # type: ignore[attr-defined]
    assert row.starts_at == _TUESDAY_9AM  # type: ignore[attr-defined]
    assert row.ends_at == _TUESDAY_10AM  # type: ignore[attr-defined]
    assert row.practitioner_id == booked.practitioner_id  # type: ignore[attr-defined]
    assert row.status == AppointmentStatus.CANCELLED  # type: ignore[attr-defined]


async def test_an_appointment_id_from_another_session_is_not_found(
    db_session: AsyncSession,
) -> None:
    # FR-018: the scope is a predicate on the write, so another session's id simply
    # does not resolve - it is never cancelled and never distinguished from one that
    # never existed.
    booked = await _seed(db_session)

    outcome = await _cancel(db_session, booked, session_id=new_id())

    assert isinstance(outcome, ChangeRefused)
    assert outcome.reason is ChangeFailureReason.APPOINTMENT_NOT_FOUND
    row = await _row(db_session, booked.appointment_id)
    assert row.status == AppointmentStatus.STANDING  # type: ignore[attr-defined]


async def test_an_appointment_that_never_existed_is_not_found(
    db_session: AsyncSession,
) -> None:
    booked = await _seed(db_session)
    booked.appointment_id = new_id()

    outcome = await _cancel(db_session, booked)

    assert isinstance(outcome, ChangeRefused)
    assert outcome.reason is ChangeFailureReason.APPOINTMENT_NOT_FOUND


async def test_cancelling_an_already_cancelled_appointment_is_a_no_op(
    db_session: AsyncSession,
) -> None:
    # FR-017: the appointment is in the state that was asked for, so this is success,
    # not a failed cancellation - and it stays distinguishable from "never existed".
    booked = await _seed(db_session, status=AppointmentStatus.CANCELLED)

    outcome = await _cancel(db_session, booked)

    assert isinstance(outcome, ChangeNoOp)
    assert outcome.appointment.id == booked.appointment_id
    assert outcome.appointment.status == AppointmentStatus.CANCELLED


async def test_a_re_sent_cancellation_answers_no_op_rather_than_stale(
    db_session: AsyncSession,
) -> None:
    # FR-021's second arm, discharged for a cancellation by the classification path:
    # the re-send quotes the pre-cancellation state and must not read as a conflict.
    booked = await _seed(db_session)
    first = await _cancel(db_session, booked)
    assert isinstance(first, ChangeApplied)

    second = await _cancel(db_session, booked)

    assert isinstance(second, ChangeNoOp)


async def test_an_appointment_already_under_way_is_refused(
    db_session: AsyncSession,
) -> None:
    booked = await _seed(db_session)

    outcome = await _cancel(
        db_session, booked, local_now=_TUESDAY_9AM + timedelta(minutes=30)
    )

    assert isinstance(outcome, ChangeRefused)
    assert outcome.reason is ChangeFailureReason.ALREADY_STARTED


async def test_an_appointment_starting_exactly_at_local_now_is_refused(
    db_session: AsyncSession,
) -> None:
    # The boundary counts as started, matching how booking treats a start at exactly
    # `local_now` as in the past.
    booked = await _seed(db_session)

    outcome = await _cancel(db_session, booked, local_now=_TUESDAY_9AM)

    assert isinstance(outcome, ChangeRefused)
    assert outcome.reason is ChangeFailureReason.ALREADY_STARTED


async def test_a_guard_naming_a_start_the_row_no_longer_holds_is_refused(
    db_session: AsyncSession,
) -> None:
    booked = await _seed(db_session)

    outcome = await _cancel(
        db_session, booked, expected_starts_at=_TUESDAY_9AM + timedelta(hours=2)
    )

    assert isinstance(outcome, ChangeRefused)
    assert outcome.reason is ChangeFailureReason.STALE_CONFIRMATION


async def test_a_guard_naming_a_practitioner_the_row_no_longer_holds_is_refused(
    db_session: AsyncSession,
) -> None:
    booked = await _seed(db_session)
    elsewhere = await seed_practitioner(db_session, booked.session_id, full_name="Dr Z")

    outcome = await _cancel(db_session, booked, expected_practitioner_id=elsewhere.id)

    assert isinstance(outcome, ChangeRefused)
    assert outcome.reason is ChangeFailureReason.STALE_CONFIRMATION


@pytest.mark.parametrize(
    "kwargs",
    [
        {"expected_starts_at": _TUESDAY_9AM + timedelta(hours=3)},
        {"local_now": _TUESDAY_9AM + timedelta(minutes=1)},
    ],
)
async def test_every_refusal_leaves_the_row_completely_untouched(
    db_session: AsyncSession, kwargs: dict[str, object]
) -> None:
    # SC-005, FR-008: a refused change is not a partial change. Start, end,
    # practitioner, id and status all have to be exactly what they were.
    booked = await _seed(db_session)

    outcome = await _cancel(db_session, booked, **kwargs)  # type: ignore[arg-type]

    assert isinstance(outcome, ChangeRefused)
    row = await _row(db_session, booked.appointment_id)
    assert row.starts_at == _TUESDAY_9AM  # type: ignore[attr-defined]
    assert row.ends_at == _TUESDAY_10AM  # type: ignore[attr-defined]
    assert row.practitioner_id == booked.practitioner_id  # type: ignore[attr-defined]
    assert row.status == AppointmentStatus.STANDING  # type: ignore[attr-defined]


async def test_a_cancellation_frees_the_booking_key(db_session: AsyncSession) -> None:
    booked = await _seed(db_session)

    await _cancel(db_session, booked)

    assert (
        await appointment_repository.get_by_idempotency_key(
            db_session, booked.idempotency_key
        )
        is None
    )


async def test_the_cancelled_event_carries_the_old_start_and_no_new_one(
    db_session: AsyncSession,
) -> None:
    # FR-037: a cancellation carries NO new-start field at all - not an empty one -
    # which is what makes it distinguishable from a move at a glance.
    booked = await _seed(db_session)

    with capture_logs() as logs:
        await _cancel(db_session, booked)

    event = next(log for log in logs if log["event"] == "appointment.cancelled")
    assert event["appointment_id"] == booked.appointment_id
    assert event["old_starts_at"] == _TUESDAY_9AM.isoformat()
    assert event["practitioner_id"] == booked.practitioner_id
    assert "new_starts_at" not in event


async def test_the_key_released_event_names_the_key_that_was_freed(
    db_session: AsyncSession,
) -> None:
    booked = await _seed(db_session)

    with capture_logs() as logs:
        await _cancel(db_session, booked)

    event = next(log for log in logs if log["event"] == "change.key_released")
    assert event["appointment_id"] == booked.appointment_id
    assert event["idempotency_key"] == booked.idempotency_key


async def test_a_no_op_emits_unchanged_and_not_cancelled(
    db_session: AsyncSession,
) -> None:
    # FR-040: a request that transitioned nothing gets its own record kind, so one
    # `appointment.cancelled` still means one cancellation.
    booked = await _seed(db_session, status=AppointmentStatus.CANCELLED)

    with capture_logs() as logs:
        await _cancel(db_session, booked)

    events = [log["event"] for log in logs]
    assert "appointment.unchanged" in events
    assert "appointment.cancelled" not in events
    unchanged = next(log for log in logs if log["event"] == "appointment.unchanged")
    assert unchanged["operation"] == "cancel"
    assert unchanged["starts_at"] == _TUESDAY_9AM.isoformat()


async def test_a_refusal_emits_change_refused_with_its_single_reason(
    db_session: AsyncSession,
) -> None:
    booked = await _seed(db_session)

    with capture_logs() as logs:
        await _cancel(db_session, booked, expected_starts_at=_TUESDAY_10AM)

    event = next(log for log in logs if log["event"] == "change.refused")
    assert event["appointment_id"] == booked.appointment_id
    assert event["operation"] == "cancel"
    assert event["reason"] == ChangeFailureReason.STALE_CONFIRMATION
    assert "appointment.cancelled" not in [log["event"] for log in logs]


# --- the read-back carries its own scope --------------------------------------


async def test_the_change_read_back_is_scoped_to_the_session_and_patient(
    db_session: AsyncSession,
) -> None:
    """FR-018: every lookup carries the session, not a check applied afterwards.

    Reached directly because every caller happens to prove ownership immediately
    beforehand - which is exactly why the guarantee must live in the query rather than
    in that habit. A fourth caller added later inherits the predicate; it cannot
    inherit the discipline.
    """
    from scheduler.repositories.appointment_repository import _load_change_context

    booked = await _seed(db_session)

    assert (
        await _load_change_context(
            db_session,
            booked.appointment_id,
            session_id=booked.session_id,
            patient_id=booked.patient_id,
        )
        is not None
    )
    assert (
        await _load_change_context(
            db_session,
            booked.appointment_id,
            session_id=new_id(),
            patient_id=booked.patient_id,
        )
        is None
    )
    assert (
        await _load_change_context(
            db_session,
            booked.appointment_id,
            session_id=booked.session_id,
            patient_id=new_id(),
        )
        is None
    )
