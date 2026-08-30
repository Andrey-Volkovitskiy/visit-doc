"""Tests for `reschedule()`: one write, both sides returned, every placement rule kept.

A move is the same conditional `UPDATE` a cancellation is, with a destination. So the
same properties are asserted - the guard is a predicate, a refusal leaves the row
byte-identical - plus the ones only a move has: `ends_at` recomputed from the
practitioner who will hold it, and every rule that governs a booking's placement
governing the move.
"""

from datetime import datetime, time, timedelta

import pytest
from scheduler.repositories import appointment_repository
from scheduler.repositories.appointment_repository import (
    ChangeApplied,
    ChangeNoOp,
    ChangeRefused,
)
from shared_models.scheduling import (
    AppointmentStatus,
    ChangeFailureReason,
    Weekday,
)
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
_TUESDAY_11AM = datetime(2026, 8, 18, 11, 0)
_LOCAL_NOW = datetime(2026, 8, 17, 8, 0)
_HORIZON_DAYS = 90


class _Booked:
    def __init__(
        self,
        session_id: str,
        patient_id: str,
        practitioner_id: str,
        appointment_id: str,
    ) -> None:
        self.session_id = session_id
        self.patient_id = patient_id
        self.practitioner_id = practitioner_id
        self.appointment_id = appointment_id


async def _seed(
    session: AsyncSession,
    *,
    starts_at: datetime = _TUESDAY_9AM,
    duration_minutes: int = 60,
    status: AppointmentStatus = AppointmentStatus.STANDING,
) -> _Booked:
    session_id = new_id()
    practitioner = await seed_practitioner(
        session, session_id, duration_minutes=duration_minutes
    )
    patient = await seed_patient(session, session_id)
    appointment = make_appointment(
        session_id,
        patient.id,
        practitioner.id,
        starts_at,
        starts_at + timedelta(minutes=duration_minutes),
        status=status,
    )
    session.add(appointment)
    await session.commit()
    return _Booked(session_id, patient.id, practitioner.id, appointment.id)


async def _reschedule(
    session: AsyncSession,
    booked: _Booked,
    *,
    new_starts_at: datetime = _TUESDAY_10AM,
    new_practitioner_id: str | None = None,
    session_id: str | None = None,
    expected_starts_at: datetime = _TUESDAY_9AM,
    expected_practitioner_id: str | None = None,
    local_now: datetime = _LOCAL_NOW,
) -> ChangeApplied | ChangeNoOp | ChangeRefused:
    return await appointment_repository.reschedule(
        session,
        session_id=session_id if session_id is not None else booked.session_id,
        patient_id=booked.patient_id,
        appointment_id=booked.appointment_id,
        new_starts_at=new_starts_at,
        new_practitioner_id=new_practitioner_id,
        expected_starts_at=expected_starts_at,
        expected_practitioner_id=(
            expected_practitioner_id
            if expected_practitioner_id is not None
            else booked.practitioner_id
        ),
        local_now=local_now,
        horizon_days=_HORIZON_DAYS,
    )


async def _row(session: AsyncSession, appointment_id: str) -> object:
    from scheduler.domain.models import Appointment

    session.expire_all()
    return await session.get(Appointment, appointment_id)


# --- the move itself ---------------------------------------------------------


async def test_a_move_rewrites_both_bounds_and_returns_both_sides(
    db_session: AsyncSession,
) -> None:
    booked = await _seed(db_session)

    outcome = await _reschedule(db_session, booked)

    assert isinstance(outcome, ChangeApplied)
    assert outcome.appointment.starts_at == _TUESDAY_10AM
    assert outcome.appointment.ends_at == _TUESDAY_11AM
    # The pre-image comes from the same statement, so it describes the state the row
    # actually left rather than one a concurrent change may have replaced.
    assert outcome.previous_starts_at == _TUESDAY_9AM
    assert outcome.previous_practitioner_id == booked.practitioner_id


async def test_the_appointment_keeps_its_identifier_patient_and_practitioner(
    db_session: AsyncSession,
) -> None:
    # It is the same appointment, not a cancellation plus a new booking.
    booked = await _seed(db_session)

    outcome = await _reschedule(db_session, booked)

    assert isinstance(outcome, ChangeApplied)
    assert outcome.appointment.id == booked.appointment_id
    assert outcome.appointment.patient_id == booked.patient_id
    assert outcome.appointment.practitioner_id == booked.practitioner_id
    assert outcome.appointment.status == AppointmentStatus.STANDING


async def test_the_patient_ends_up_holding_exactly_one_appointment(
    db_session: AsyncSession,
) -> None:
    from scheduler.domain.models import Appointment
    from sqlalchemy import func, select

    booked = await _seed(db_session)

    await _reschedule(db_session, booked)

    result = await db_session.execute(select(func.count()).select_from(Appointment))
    assert result.scalar_one() == 1


async def test_ends_at_is_derived_from_the_practitioners_current_length(
    db_session: AsyncSession,
) -> None:
    # Not carried over from the old row: the end is recomputed from the practitioner
    # who will hold the appointment, read at the moment of the change.
    booked = await _seed(db_session, duration_minutes=30)

    outcome = await _reschedule(db_session, booked)

    assert isinstance(outcome, ChangeApplied)
    assert outcome.appointment.ends_at == _TUESDAY_10AM + timedelta(minutes=30)


async def test_a_move_to_the_time_it_already_holds_is_a_no_op(
    db_session: AsyncSession,
) -> None:
    # The appointment does not block its own slot - an exclusion constraint compares
    # distinct rows - so this succeeds, and transitions nothing.
    booked = await _seed(db_session)

    outcome = await _reschedule(db_session, booked, new_starts_at=_TUESDAY_9AM)

    assert isinstance(outcome, ChangeNoOp)
    assert outcome.appointment.starts_at == _TUESDAY_9AM


async def test_a_re_sent_move_quoting_the_pre_move_state_is_a_no_op(
    db_session: AsyncSession,
) -> None:
    # FR-021's second arm: the guard passes when the appointment matches EITHER the
    # described state or the target state. Without it, a retry of a move that landed
    # would be refused as stale - reporting a conflict for a change that succeeded.
    booked = await _seed(db_session)
    first = await _reschedule(db_session, booked)
    assert isinstance(first, ChangeApplied)

    second = await _reschedule(db_session, booked)

    assert isinstance(second, ChangeNoOp)
    assert second.appointment.starts_at == _TUESDAY_10AM


async def test_a_move_can_overlap_the_slot_it_currently_occupies(
    db_session: AsyncSession,
) -> None:
    # 09:00-10:00 moving to 09:30-10:30 overlaps itself, which the write path allows
    # because an exclusion constraint never compares a row to its own previous value.
    booked = await _seed(db_session, duration_minutes=30)

    outcome = await _reschedule(
        db_session, booked, new_starts_at=_TUESDAY_9AM + timedelta(minutes=30)
    )

    assert isinstance(outcome, ChangeApplied)
    assert outcome.appointment.starts_at == _TUESDAY_9AM + timedelta(minutes=30)


# --- the guard ---------------------------------------------------------------


async def test_a_guard_naming_a_start_the_row_no_longer_holds_is_refused(
    db_session: AsyncSession,
) -> None:
    booked = await _seed(db_session)

    outcome = await _reschedule(db_session, booked, expected_starts_at=_TUESDAY_11AM)

    assert isinstance(outcome, ChangeRefused)
    assert outcome.reason is ChangeFailureReason.STALE_CONFIRMATION


async def test_a_guard_naming_a_practitioner_the_row_no_longer_holds_is_refused(
    db_session: AsyncSession,
) -> None:
    booked = await _seed(db_session)
    elsewhere = await seed_practitioner(db_session, booked.session_id, full_name="Dr Z")

    outcome = await _reschedule(
        db_session, booked, expected_practitioner_id=elsewhere.id
    )

    assert isinstance(outcome, ChangeRefused)
    assert outcome.reason is ChangeFailureReason.STALE_CONFIRMATION


async def test_a_cancelled_appointment_cannot_be_moved(
    db_session: AsyncSession,
) -> None:
    # For a reschedule this is a refusal, not a no-op: cancelled is not a state a move
    # may target, and there is no un-cancelling.
    booked = await _seed(db_session, status=AppointmentStatus.CANCELLED)

    outcome = await _reschedule(db_session, booked)

    assert isinstance(outcome, ChangeRefused)
    assert outcome.reason is ChangeFailureReason.ALREADY_CANCELLED


async def test_an_appointment_already_under_way_cannot_be_moved(
    db_session: AsyncSession,
) -> None:
    booked = await _seed(db_session)

    outcome = await _reschedule(
        db_session, booked, local_now=_TUESDAY_9AM + timedelta(minutes=30)
    )

    assert isinstance(outcome, ChangeRefused)
    assert outcome.reason is ChangeFailureReason.ALREADY_STARTED


async def test_another_sessions_appointment_is_not_found(
    db_session: AsyncSession,
) -> None:
    booked = await _seed(db_session)

    outcome = await _reschedule(db_session, booked, session_id=new_id())

    assert isinstance(outcome, ChangeRefused)
    assert outcome.reason is ChangeFailureReason.APPOINTMENT_NOT_FOUND


@pytest.mark.parametrize(
    "kwargs",
    [
        {"expected_starts_at": _TUESDAY_11AM},
        {"local_now": _TUESDAY_9AM + timedelta(minutes=1)},
        {"new_starts_at": datetime(2026, 8, 18, 9, 20)},
        {"new_starts_at": datetime(2026, 8, 18, 23, 0)},
    ],
)
async def test_every_refusal_leaves_the_row_exactly_as_it_was(
    db_session: AsyncSession, kwargs: dict[str, object]
) -> None:
    booked = await _seed(db_session)

    outcome = await _reschedule(db_session, booked, **kwargs)  # type: ignore[arg-type]

    assert isinstance(outcome, ChangeRefused)
    row = await _row(db_session, booked.appointment_id)
    assert row.starts_at == _TUESDAY_9AM  # type: ignore[attr-defined]
    assert row.ends_at == _TUESDAY_10AM  # type: ignore[attr-defined]
    assert row.practitioner_id == booked.practitioner_id  # type: ignore[attr-defined]
    assert row.status == AppointmentStatus.STANDING  # type: ignore[attr-defined]


# --- placement: the same rules a booking obeys -------------------------------


async def test_a_new_start_in_the_past_is_refused_as_in_past_not_already_started(
    db_session: AsyncSession,
) -> None:
    # Two different situations, two different values: the appointment has not started,
    # the time it is asked to move to has passed.
    booked = await _seed(db_session)

    outcome = await _reschedule(
        db_session, booked, new_starts_at=datetime(2026, 8, 10, 9, 0)
    )

    assert isinstance(outcome, ChangeRefused)
    assert outcome.reason is ChangeFailureReason.IN_PAST


async def test_a_new_start_beyond_the_horizon_is_refused(
    db_session: AsyncSession,
) -> None:
    booked = await _seed(db_session)

    outcome = await _reschedule(
        db_session, booked, new_starts_at=_LOCAL_NOW + timedelta(days=_HORIZON_DAYS + 5)
    )

    assert isinstance(outcome, ChangeRefused)
    assert outcome.reason is ChangeFailureReason.BEYOND_HORIZON


async def test_a_new_start_outside_every_working_range_is_refused(
    db_session: AsyncSession,
) -> None:
    booked = await _seed(db_session)

    outcome = await _reschedule(
        db_session, booked, new_starts_at=datetime(2026, 8, 18, 23, 0)
    )

    assert isinstance(outcome, ChangeRefused)
    assert outcome.reason is ChangeFailureReason.OUTSIDE_SCHEDULE


async def test_a_new_start_off_the_grid_inside_a_range_is_refused(
    db_session: AsyncSession,
) -> None:
    booked = await _seed(db_session)

    outcome = await _reschedule(
        db_session, booked, new_starts_at=datetime(2026, 8, 18, 9, 20)
    )

    assert isinstance(outcome, ChangeRefused)
    assert outcome.reason is ChangeFailureReason.OFF_GRID


async def test_a_move_onto_another_patients_slot_is_refused_practitioner_busy(
    db_session: AsyncSession,
) -> None:
    # Decided by the exclusion constraint at the write, exactly as a booking's is.
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    mine = await seed_patient(db_session, session_id, full_name="Ada")
    theirs = await seed_patient(db_session, session_id, full_name="Bram")
    ours = make_appointment(
        session_id, mine.id, practitioner.id, _TUESDAY_9AM, _TUESDAY_10AM
    )
    db_session.add_all(
        [
            ours,
            make_appointment(
                session_id, theirs.id, practitioner.id, _TUESDAY_10AM, _TUESDAY_11AM
            ),
        ]
    )
    await db_session.commit()

    outcome = await appointment_repository.reschedule(
        db_session,
        session_id=session_id,
        patient_id=mine.id,
        appointment_id=ours.id,
        new_starts_at=_TUESDAY_10AM,
        new_practitioner_id=None,
        expected_starts_at=_TUESDAY_9AM,
        expected_practitioner_id=practitioner.id,
        local_now=_LOCAL_NOW,
        horizon_days=_HORIZON_DAYS,
    )

    assert isinstance(outcome, ChangeRefused)
    assert outcome.reason is ChangeFailureReason.PRACTITIONER_BUSY


async def test_a_move_onto_the_patients_own_other_appointment_is_refused(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()
    here = await seed_practitioner(db_session, session_id, full_name="Dr A")
    there = await seed_practitioner(db_session, session_id, full_name="Dr B")
    patient = await seed_patient(db_session, session_id)
    ours = make_appointment(
        session_id, patient.id, here.id, _TUESDAY_9AM, _TUESDAY_10AM
    )
    db_session.add_all(
        [
            ours,
            make_appointment(
                session_id, patient.id, there.id, _TUESDAY_10AM, _TUESDAY_11AM
            ),
        ]
    )
    await db_session.commit()

    outcome = await appointment_repository.reschedule(
        db_session,
        session_id=session_id,
        patient_id=patient.id,
        appointment_id=ours.id,
        new_starts_at=_TUESDAY_10AM,
        new_practitioner_id=None,
        expected_starts_at=_TUESDAY_9AM,
        expected_practitioner_id=here.id,
        local_now=_LOCAL_NOW,
        horizon_days=_HORIZON_DAYS,
    )

    assert isinstance(outcome, ChangeRefused)
    assert outcome.reason is ChangeFailureReason.PATIENT_BUSY


async def test_placement_is_validated_by_the_same_implementation_booking_uses(
    db_session: AsyncSession,
) -> None:
    """Every placement refusal a move produces is one `validate_start` produces.

    The rule is true by construction rather than by two implementations agreeing, so
    this walks the reasons and checks each maps across with its value intact.
    """
    from scheduler.domain.availability import DailyRange, validate_start

    schedule = [DailyRange(Weekday.TUESDAY, time(9, 0), time(17, 0))]
    for start, expected in (
        (datetime(2026, 8, 10, 9, 0), ChangeFailureReason.IN_PAST),
        (datetime(2026, 8, 18, 23, 0), ChangeFailureReason.OUTSIDE_SCHEDULE),
        (datetime(2026, 8, 18, 9, 20), ChangeFailureReason.OFF_GRID),
    ):
        booking_reason = validate_start(
            start,
            schedule=schedule,
            duration_minutes=60,
            local_now=_LOCAL_NOW,
            horizon_days=_HORIZON_DAYS,
        )
        assert booking_reason is not None
        assert ChangeFailureReason(booking_reason.value) is expected


# --- the change record -------------------------------------------------------


async def test_the_rescheduled_event_carries_both_starts_and_both_practitioners(
    db_session: AsyncSession,
) -> None:
    booked = await _seed(db_session)

    with capture_logs() as logs:
        await _reschedule(db_session, booked)

    event = next(log for log in logs if log["event"] == "appointment.rescheduled")
    assert event["appointment_id"] == booked.appointment_id
    assert event["old_starts_at"] == _TUESDAY_9AM.isoformat()
    assert event["new_starts_at"] == _TUESDAY_10AM.isoformat()
    assert event["old_practitioner_id"] == booked.practitioner_id
    assert event["new_practitioner_id"] == booked.practitioner_id


async def test_a_move_that_transitioned_nothing_emits_unchanged_instead(
    db_session: AsyncSession,
) -> None:
    # FR-040: mutually exclusive, and that is the point of having both - a re-sent move
    # must not make the log show two moves where one happened.
    booked = await _seed(db_session)

    with capture_logs() as logs:
        await _reschedule(db_session, booked, new_starts_at=_TUESDAY_9AM)

    events = [log["event"] for log in logs]
    assert "appointment.unchanged" in events
    assert "appointment.rescheduled" not in events
    unchanged = next(log for log in logs if log["event"] == "appointment.unchanged")
    assert unchanged["operation"] == "reschedule"


async def test_a_refused_move_emits_change_refused_and_no_completed_change(
    db_session: AsyncSession,
) -> None:
    booked = await _seed(db_session)

    with capture_logs() as logs:
        await _reschedule(db_session, booked, expected_starts_at=_TUESDAY_11AM)

    events = [log["event"] for log in logs]
    assert "change.refused" in events
    assert "appointment.rescheduled" not in events
    assert "appointment.unchanged" not in events
    refused = next(log for log in logs if log["event"] == "change.refused")
    assert refused["operation"] == "reschedule"
    assert refused["reason"] == ChangeFailureReason.STALE_CONFIRMATION


async def test_a_move_never_emits_the_cancelled_event(
    db_session: AsyncSession,
) -> None:
    booked = await _seed(db_session)

    with capture_logs() as logs:
        await _reschedule(db_session, booked)

    assert "appointment.cancelled" not in [log["event"] for log in logs]


# --- swapping the practitioner ----------------------------------------------


async def _seed_two(session: AsyncSession) -> tuple[_Booked, str, int]:
    """Seed one appointment with Dr A, plus a Dr B of a different length.

    Returns: the booking, the second practitioner's id, and that practitioner's
        appointment length in minutes.
    """
    session_id = new_id()
    here = await seed_practitioner(
        session, session_id, full_name="Dr A", duration_minutes=60
    )
    there = await seed_practitioner(
        session, session_id, full_name="Dr B", duration_minutes=30
    )
    patient = await seed_patient(session, session_id)
    appointment = make_appointment(
        session_id, patient.id, here.id, _TUESDAY_9AM, _TUESDAY_10AM
    )
    session.add(appointment)
    await session.commit()
    return (
        _Booked(session_id, patient.id, here.id, appointment.id),
        there.id,
        30,
    )


async def test_practitioner_start_and_end_change_together(
    db_session: AsyncSession,
) -> None:
    # FR-003: one write on one row. Not a cancellation plus a new booking, which is
    # what "the two halves come apart" would look like from the outside.
    booked, other_id, _ = await _seed_two(db_session)

    outcome = await _reschedule(db_session, booked, new_practitioner_id=other_id)

    assert isinstance(outcome, ChangeApplied)
    assert outcome.appointment.id == booked.appointment_id
    assert outcome.appointment.practitioner_id == other_id
    assert outcome.appointment.starts_at == _TUESDAY_10AM
    assert outcome.previous_practitioner_id == booked.practitioner_id
    assert outcome.previous_starts_at == _TUESDAY_9AM


async def test_ends_at_comes_from_the_new_practitioners_length(
    db_session: AsyncSession,
) -> None:
    # FR-004: an appointment can come out shorter than it went in. A patient must be
    # told when that happens, which is only possible if it is true here first.
    booked, other_id, other_minutes = await _seed_two(db_session)

    outcome = await _reschedule(db_session, booked, new_practitioner_id=other_id)

    assert isinstance(outcome, ChangeApplied)
    assert outcome.appointment.ends_at == _TUESDAY_10AM + timedelta(
        minutes=other_minutes
    )


async def test_a_swap_can_make_an_appointment_longer_than_it_went_in(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()
    here = await seed_practitioner(
        db_session, session_id, full_name="Dr A", duration_minutes=30
    )
    there = await seed_practitioner(
        db_session, session_id, full_name="Dr B", duration_minutes=60
    )
    patient = await seed_patient(db_session, session_id)
    appointment = make_appointment(
        session_id,
        patient.id,
        here.id,
        _TUESDAY_9AM,
        _TUESDAY_9AM + timedelta(minutes=30),
    )
    db_session.add(appointment)
    await db_session.commit()
    booked = _Booked(session_id, patient.id, here.id, appointment.id)

    outcome = await _reschedule(db_session, booked, new_practitioner_id=there.id)

    assert isinstance(outcome, ChangeApplied)
    assert outcome.appointment.ends_at - outcome.appointment.starts_at == timedelta(
        minutes=60
    )


async def test_a_swap_keeping_the_same_start_succeeds_when_the_new_one_is_free(
    db_session: AsyncSession,
) -> None:
    # FR-007: the appointment does not block its own change, so keeping the time and
    # changing only the practitioner is a legal move.
    booked, other_id, other_minutes = await _seed_two(db_session)

    outcome = await _reschedule(
        db_session, booked, new_starts_at=_TUESDAY_9AM, new_practitioner_id=other_id
    )

    assert isinstance(outcome, ChangeApplied)
    assert outcome.appointment.starts_at == _TUESDAY_9AM
    assert outcome.appointment.practitioner_id == other_id
    assert outcome.appointment.ends_at == _TUESDAY_9AM + timedelta(
        minutes=other_minutes
    )


async def test_an_unknown_new_practitioner_is_refused_with_the_row_untouched(
    db_session: AsyncSession,
) -> None:
    booked, _, _ = await _seed_two(db_session)

    outcome = await _reschedule(db_session, booked, new_practitioner_id=new_id())

    assert isinstance(outcome, ChangeRefused)
    assert outcome.reason is ChangeFailureReason.PRACTITIONER_NOT_FOUND
    row = await _row(db_session, booked.appointment_id)
    assert row.practitioner_id == booked.practitioner_id  # type: ignore[attr-defined]
    assert row.starts_at == _TUESDAY_9AM  # type: ignore[attr-defined]


async def test_a_practitioner_from_another_session_is_reported_identically(
    db_session: AsyncSession,
) -> None:
    booked, _, _ = await _seed_two(db_session)
    stranger = await seed_practitioner(db_session, new_id(), full_name="Dr Elsewhere")

    outcome = await _reschedule(db_session, booked, new_practitioner_id=stranger.id)

    assert isinstance(outcome, ChangeRefused)
    assert outcome.reason is ChangeFailureReason.PRACTITIONER_NOT_FOUND


async def test_a_swap_onto_a_practitioner_who_is_busy_then_is_refused(
    db_session: AsyncSession,
) -> None:
    booked, other_id, _ = await _seed_two(db_session)
    theirs = await seed_patient(db_session, booked.session_id, full_name="Bram")
    db_session.add(
        make_appointment(
            booked.session_id,
            theirs.id,
            other_id,
            _TUESDAY_10AM,
            _TUESDAY_10AM + timedelta(minutes=30),
        )
    )
    await db_session.commit()

    outcome = await _reschedule(db_session, booked, new_practitioner_id=other_id)

    assert isinstance(outcome, ChangeRefused)
    assert outcome.reason is ChangeFailureReason.PRACTITIONER_BUSY


async def test_the_swap_event_names_both_practitioners_and_they_differ(
    db_session: AsyncSession,
) -> None:
    # FR-038: without both fields a same-time swap logs as a change from a time to the
    # identical time, which reads as a change that did nothing.
    booked, other_id, _ = await _seed_two(db_session)

    with capture_logs() as logs:
        await _reschedule(
            db_session,
            booked,
            new_starts_at=_TUESDAY_9AM,
            new_practitioner_id=other_id,
        )

    event = next(log for log in logs if log["event"] == "appointment.rescheduled")
    assert event["old_practitioner_id"] == booked.practitioner_id
    assert event["new_practitioner_id"] == other_id
    assert event["old_practitioner_id"] != event["new_practitioner_id"]
    # The times are identical, which is exactly why the practitioner fields carry it.
    assert event["old_starts_at"] == event["new_starts_at"]


async def test_both_practitioner_fields_are_present_even_when_unchanged(
    db_session: AsyncSession,
) -> None:
    booked = await _seed(db_session)

    with capture_logs() as logs:
        await _reschedule(db_session, booked)

    event = next(log for log in logs if log["event"] == "appointment.rescheduled")
    assert event["old_practitioner_id"] == booked.practitioner_id
    assert event["new_practitioner_id"] == booked.practitioner_id


async def test_a_same_time_same_practitioner_request_is_still_a_no_op(
    db_session: AsyncSession,
) -> None:
    booked, other_id, _ = await _seed_two(db_session)
    await _reschedule(
        db_session, booked, new_starts_at=_TUESDAY_9AM, new_practitioner_id=other_id
    )

    # Re-sent, quoting the pre-swap state as a retry would.
    second = await _reschedule(
        db_session, booked, new_starts_at=_TUESDAY_9AM, new_practitioner_id=other_id
    )

    assert isinstance(second, ChangeNoOp)


async def test_a_completed_swap_carries_the_previous_practitioners_name(
    db_session: AsyncSession,
) -> None:
    booked, other_id, _ = await _seed_two(db_session)

    outcome = await _reschedule(db_session, booked, new_practitioner_id=other_id)

    assert isinstance(outcome, ChangeApplied)
    assert outcome.previous_practitioner_full_name == "Dr A"
    assert outcome.practitioner.full_name == "Dr B"


async def test_a_move_that_kept_its_practitioner_names_that_same_one(
    db_session: AsyncSession,
) -> None:
    booked = await _seed(db_session)

    outcome = await _reschedule(db_session, booked)

    assert isinstance(outcome, ChangeApplied)
    assert outcome.previous_practitioner_full_name == outcome.practitioner.full_name


# --- the booking key follows the booking it describes ------------------------


async def test_a_move_releases_the_key_the_original_booking_derived(
    db_session: AsyncSession,
) -> None:
    """A moved appointment no longer sits where its key says, so it stops holding it.

    The key is derived from exactly (patient, practitioner, starts_at). Once the
    appointment is somewhere else, that key describes a booking request nobody has
    made - and holding it makes the *next* genuine request for that slot collide with
    an appointment that is no longer in it.
    """
    booked = await _seed(db_session)
    original_key = (
        await _row(db_session, booked.appointment_id)
    ).idempotency_key  # type: ignore[attr-defined]

    await _reschedule(db_session, booked)

    assert (
        await appointment_repository.get_by_idempotency_key(db_session, original_key)
        is None
    )
    moved = await _row(db_session, booked.appointment_id)
    assert moved.idempotency_key != original_key  # type: ignore[attr-defined]


async def test_the_freed_key_lets_the_original_slot_be_booked_again(
    db_session: AsyncSession,
) -> None:
    # The defect this prevents: availability offers the vacated slot, and the booking
    # that follows is refused as a key-derivation defect - permanently, for as long as
    # the moved appointment stands.
    booked = await _seed(db_session)
    original_key = (
        await _row(db_session, booked.appointment_id)
    ).idempotency_key  # type: ignore[attr-defined]
    await _reschedule(db_session, booked)

    outcome = await appointment_repository.book(
        db_session,
        session_id=booked.session_id,
        patient_id=booked.patient_id,
        practitioner_id=booked.practitioner_id,
        starts_at=_TUESDAY_9AM,
        local_now=_LOCAL_NOW,
        idempotency_key=original_key,
        horizon_days=_HORIZON_DAYS,
    )

    from scheduler.repositories.appointment_repository import BookingCreated

    assert isinstance(outcome, BookingCreated)
    assert outcome.idempotent_replay is False
    assert outcome.appointment.id != booked.appointment_id


async def test_a_move_that_transitioned_nothing_keeps_the_key_it_had(
    db_session: AsyncSession,
) -> None:
    # A no-op transitioned nothing, so it must write nothing - including the key. A
    # rotation here would make `appointment.unchanged` a lie about the row.
    booked = await _seed(db_session)
    original_key = (
        await _row(db_session, booked.appointment_id)
    ).idempotency_key  # type: ignore[attr-defined]

    outcome = await _reschedule(db_session, booked, new_starts_at=_TUESDAY_9AM)

    assert isinstance(outcome, ChangeNoOp)
    unchanged = await _row(db_session, booked.appointment_id)
    assert unchanged.idempotency_key == original_key  # type: ignore[attr-defined]


async def test_a_cancellation_leaves_the_key_on_the_row_it_created(
    db_session: AsyncSession,
) -> None:
    # A cancellation frees the key by leaving the partial index, not by overwriting the
    # column - the row is still the record of which key created it, and it never moved.
    booked = await _seed(db_session)
    original_key = (
        await _row(db_session, booked.appointment_id)
    ).idempotency_key  # type: ignore[attr-defined]

    await appointment_repository.cancel(
        db_session,
        session_id=booked.session_id,
        patient_id=booked.patient_id,
        appointment_id=booked.appointment_id,
        expected_starts_at=_TUESDAY_9AM,
        expected_practitioner_id=booked.practitioner_id,
        local_now=_LOCAL_NOW,
    )

    cancelled = await _row(db_session, booked.appointment_id)
    assert cancelled.idempotency_key == original_key  # type: ignore[attr-defined]


# --- a grandfathered appointment ---------------------------------------------


async def _narrow_to_afternoon(session: AsyncSession, practitioner_id: str) -> None:
    """Leave the Tuesday 09:00 appointment outside its practitioner's current hours."""
    from scheduler.repositories import practitioner_repository

    await practitioner_repository.replace_schedule(
        session, practitioner_id, [(Weekday.TUESDAY, time(14, 0), time(16, 0))]
    )


async def test_a_grandfathered_appointment_can_be_moved_onto_a_currently_legal_time(
    db_session: AsyncSession,
) -> None:
    """Its own out-of-schedule start is never re-validated; the new one always is.

    The guard compares the stored start for equality and asks nothing about whether it
    would still be bookable - which is what "grandfathered" means. Only the destination
    is put through the placement rules.
    """
    booked = await _seed(db_session)
    await _narrow_to_afternoon(db_session, booked.practitioner_id)

    outcome = await _reschedule(
        db_session, booked, new_starts_at=datetime(2026, 8, 18, 14, 0)
    )

    assert isinstance(outcome, ChangeApplied)
    assert outcome.appointment.starts_at == datetime(2026, 8, 18, 14, 0)
    assert outcome.previous_starts_at == _TUESDAY_9AM


async def test_a_grandfathered_appointment_may_not_move_outside_current_hours(
    db_session: AsyncSession,
) -> None:
    # Being grandfathered exempts it from being disturbed, not from the rules that
    # govern where it may go next - so it cannot move to another hour the practitioner
    # no longer works, including the one it currently occupies.
    booked = await _seed(db_session)
    await _narrow_to_afternoon(db_session, booked.practitioner_id)

    outcome = await _reschedule(
        db_session, booked, new_starts_at=datetime(2026, 8, 18, 11, 0)
    )

    assert isinstance(outcome, ChangeRefused)
    assert outcome.reason is ChangeFailureReason.OUTSIDE_SCHEDULE
    row = await _row(db_session, booked.appointment_id)
    assert row.starts_at == _TUESDAY_9AM  # type: ignore[attr-defined]


async def test_a_grandfathered_appointment_may_not_stay_where_it_is(
    db_session: AsyncSession,
) -> None:
    # The sharpest form of the rule: asking to "move" it to the time it already holds
    # is refused, because that time is no longer one the practitioner works. The
    # appointment keeps it only for as long as nobody asks to place it there again.
    booked = await _seed(db_session)
    await _narrow_to_afternoon(db_session, booked.practitioner_id)

    outcome = await _reschedule(db_session, booked, new_starts_at=_TUESDAY_9AM)

    assert isinstance(outcome, ChangeRefused)
    assert outcome.reason is ChangeFailureReason.OUTSIDE_SCHEDULE
    row = await _row(db_session, booked.appointment_id)
    assert row.starts_at == _TUESDAY_9AM  # type: ignore[attr-defined]
    assert row.status == AppointmentStatus.STANDING  # type: ignore[attr-defined]
