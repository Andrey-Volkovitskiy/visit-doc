"""Tests for `book()`: what it creates, and the single reason it refuses."""

import asyncio
from datetime import datetime, time, timedelta

from scheduler.db.session import session_factory
from scheduler.repositories import appointment_repository
from scheduler.repositories.appointment_repository import (
    BookingCreated,
    BookingRefused,
)
from shared_models.scheduling import BookingFailureReason
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import new_id, seed_patient, seed_practitioner

_TUESDAY_9AM = datetime(2026, 8, 18, 9, 0)
_LOCAL_NOW = datetime(2026, 8, 17, 8, 0)
_HORIZON_DAYS = 90


async def _book(
    session: AsyncSession,
    *,
    session_id: str,
    patient_id: str,
    practitioner_id: str,
    starts_at: datetime = _TUESDAY_9AM,
    local_now: datetime = _LOCAL_NOW,
    idempotency_key: str | None = None,
) -> BookingCreated | BookingRefused:
    return await appointment_repository.book(
        session,
        session_id=session_id,
        patient_id=patient_id,
        practitioner_id=practitioner_id,
        starts_at=starts_at,
        local_now=local_now,
        idempotency_key=idempotency_key or new_id(),
        horizon_days=_HORIZON_DAYS,
    )


async def _appointment_count(session: AsyncSession) -> int:
    from scheduler.domain.models import Appointment

    result = await session.execute(select(func.count()).select_from(Appointment))
    return int(result.scalar_one())


async def test_a_successful_booking_derives_ends_at_from_the_duration(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id, duration_minutes=45)
    patient = await seed_patient(db_session, session_id)

    outcome = await _book(
        db_session,
        session_id=session_id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
    )

    assert isinstance(outcome, BookingCreated)
    assert outcome.appointment.starts_at == _TUESDAY_9AM
    assert outcome.appointment.ends_at == _TUESDAY_9AM + timedelta(minutes=45)
    assert outcome.idempotent_replay is False


async def test_a_practitioner_from_another_session_is_reported_as_not_found(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()
    other_session = new_id()
    practitioner = await seed_practitioner(db_session, other_session)
    patient = await seed_patient(db_session, session_id)

    outcome = await _book(
        db_session,
        session_id=session_id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
    )

    assert outcome == BookingRefused(BookingFailureReason.PRACTITIONER_NOT_FOUND)


async def test_a_nonexistent_practitioner_is_reported_identically(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()
    patient = await seed_patient(db_session, session_id)

    outcome = await _book(
        db_session,
        session_id=session_id,
        patient_id=patient.id,
        practitioner_id=new_id(),
    )

    assert outcome == BookingRefused(BookingFailureReason.PRACTITIONER_NOT_FOUND)


async def test_a_patient_from_another_session_is_reported_as_not_found(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, new_id())

    outcome = await _book(
        db_session,
        session_id=session_id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
    )

    assert outcome == BookingRefused(BookingFailureReason.PATIENT_NOT_FOUND)


async def test_a_start_in_the_past_is_refused(db_session: AsyncSession) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)

    outcome = await _book(
        db_session,
        session_id=session_id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
        local_now=_TUESDAY_9AM,
    )

    assert outcome == BookingRefused(BookingFailureReason.IN_PAST)


async def test_a_start_beyond_the_horizon_is_refused(db_session: AsyncSession) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)

    outcome = await _book(
        db_session,
        session_id=session_id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
        local_now=_TUESDAY_9AM - timedelta(days=_HORIZON_DAYS, minutes=1),
    )

    assert outcome == BookingRefused(BookingFailureReason.BEYOND_HORIZON)


async def test_a_start_outside_every_working_range_is_refused_as_outside_schedule(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)

    # A Sunday: the default schedule covers Monday to Friday only.
    outcome = await _book(
        db_session,
        session_id=session_id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
        starts_at=datetime(2026, 8, 23, 10, 0),
    )

    assert outcome == BookingRefused(BookingFailureReason.OUTSIDE_SCHEDULE)


async def test_a_start_off_the_grid_inside_a_range_is_refused_as_off_grid(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)

    outcome = await _book(
        db_session,
        session_id=session_id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
        starts_at=datetime(2026, 8, 18, 9, 30),
    )

    assert outcome == BookingRefused(BookingFailureReason.OFF_GRID)


async def test_a_second_patient_cannot_take_a_time_the_practitioner_already_holds(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    first = await seed_patient(db_session, session_id, full_name="Ada")
    second = await seed_patient(db_session, session_id, full_name="Bram")

    await _book(
        db_session,
        session_id=session_id,
        patient_id=first.id,
        practitioner_id=practitioner.id,
    )
    outcome = await _book(
        db_session,
        session_id=session_id,
        patient_id=second.id,
        practitioner_id=practitioner.id,
    )

    assert outcome == BookingRefused(BookingFailureReason.PRACTITIONER_BUSY)


async def test_two_patients_in_one_session_may_both_book_at_different_times(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    first = await seed_patient(db_session, session_id, full_name="Ada")
    second = await seed_patient(db_session, session_id, full_name="Bram")

    first_outcome = await _book(
        db_session,
        session_id=session_id,
        patient_id=first.id,
        practitioner_id=practitioner.id,
    )
    second_outcome = await _book(
        db_session,
        session_id=session_id,
        patient_id=second.id,
        practitioner_id=practitioner.id,
        starts_at=_TUESDAY_9AM + timedelta(hours=1),
    )

    assert isinstance(first_outcome, BookingCreated)
    assert isinstance(second_outcome, BookingCreated)
    assert await _appointment_count(db_session) == 2


async def test_a_patient_cannot_double_book_themselves_across_practitioners(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()
    first_practitioner = await seed_practitioner(db_session, session_id, full_name="A")
    second_practitioner = await seed_practitioner(db_session, session_id, full_name="B")
    patient = await seed_patient(db_session, session_id)

    await _book(
        db_session,
        session_id=session_id,
        patient_id=patient.id,
        practitioner_id=first_practitioner.id,
    )
    outcome = await _book(
        db_session,
        session_id=session_id,
        patient_id=patient.id,
        practitioner_id=second_practitioner.id,
    )

    assert outcome == BookingRefused(BookingFailureReason.PATIENT_BUSY)


async def test_a_back_to_back_booking_with_the_same_practitioner_is_accepted(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)

    await _book(
        db_session,
        session_id=session_id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
    )
    outcome = await _book(
        db_session,
        session_id=session_id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
        starts_at=_TUESDAY_9AM + timedelta(hours=1),
    )

    assert isinstance(outcome, BookingCreated)


async def test_a_concurrent_duplicate_leaves_exactly_one_row(
    db_session: AsyncSession,
) -> None:
    """Two attempts on one slot, with *different* keys, race for real.

    The exclusion constraint - not any application check - is what resolves this, so
    exactly one wins and the other is refused as busy.
    """
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    first = await seed_patient(db_session, session_id, full_name="Ada")
    second = await seed_patient(db_session, session_id, full_name="Bram")

    async def attempt(patient_id: str) -> BookingCreated | BookingRefused:
        async with session_factory() as session:
            return await _book(
                session,
                session_id=session_id,
                patient_id=patient_id,
                practitioner_id=practitioner.id,
            )

    outcomes = await asyncio.gather(attempt(first.id), attempt(second.id))

    created = [o for o in outcomes if isinstance(o, BookingCreated)]
    refused = [o for o in outcomes if isinstance(o, BookingRefused)]
    assert len(created) == 1
    assert len(refused) == 1
    assert refused[0].reason is BookingFailureReason.PRACTITIONER_BUSY
    async with session_factory() as session:
        assert await _appointment_count(session) == 1


async def test_a_practitioner_created_with_no_schedule_can_never_be_booked(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id, schedule=[])
    patient = await seed_patient(db_session, session_id)

    outcome = await _book(
        db_session,
        session_id=session_id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
    )

    assert outcome == BookingRefused(BookingFailureReason.OUTSIDE_SCHEDULE)


async def test_a_narrowed_schedule_does_not_disturb_an_existing_appointment(
    db_session: AsyncSession,
) -> None:
    from scheduler.repositories import practitioner_repository

    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)
    booked = await _book(
        db_session,
        session_id=session_id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
    )
    assert isinstance(booked, BookingCreated)

    # Narrow Tuesday to a window that no longer contains the booked hour.
    await practitioner_repository.replace_schedule(
        db_session, practitioner.id, [(1, time(14, 0), time(16, 0))]
    )

    async with session_factory() as session:
        assert await _appointment_count(session) == 1
        stored = await session.get(type(booked.appointment), booked.appointment.id)
        assert stored is not None
        assert stored.starts_at == _TUESDAY_9AM


async def test_a_grandfathered_appointment_still_blocks_an_overlapping_booking(
    db_session: AsyncSession,
) -> None:
    from scheduler.repositories import practitioner_repository

    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    first = await seed_patient(db_session, session_id, full_name="Ada")
    second = await seed_patient(db_session, session_id, full_name="Bram")
    await _book(
        db_session,
        session_id=session_id,
        patient_id=first.id,
        practitioner_id=practitioner.id,
    )

    # Widen Tuesday so 09:00 is on the grid again, then try to take the held hour.
    await practitioner_repository.replace_schedule(
        db_session, practitioner.id, [(1, time(8, 0), time(18, 0))]
    )
    outcome = await _book(
        db_session,
        session_id=session_id,
        patient_id=second.id,
        practitioner_id=practitioner.id,
    )

    assert outcome == BookingRefused(BookingFailureReason.PRACTITIONER_BUSY)


async def test_busy_intervals_ignores_the_practitioners_cancelled_appointments(
    db_session: AsyncSession,
) -> None:
    from shared_models.scheduling import AppointmentStatus

    from .conftest import make_appointment

    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id, full_name="Ada")
    other = await seed_patient(db_session, session_id, full_name="Bram")
    db_session.add(
        make_appointment(
            session_id,
            other.id,
            practitioner.id,
            _TUESDAY_9AM,
            _TUESDAY_9AM + timedelta(hours=1),
            status=AppointmentStatus.CANCELLED,
        )
    )
    await db_session.commit()

    busy = await appointment_repository.busy_intervals(
        db_session,
        session_id=session_id,
        practitioner_id=practitioner.id,
        patient_id=patient.id,
        from_date=_TUESDAY_9AM.date(),
        to_date=_TUESDAY_9AM.date(),
    )

    assert busy == []


async def test_busy_intervals_ignores_the_patients_own_cancelled_appointments(
    db_session: AsyncSession,
) -> None:
    from shared_models.scheduling import AppointmentStatus

    from .conftest import make_appointment

    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id, full_name="Dr A")
    elsewhere = await seed_practitioner(db_session, session_id, full_name="Dr B")
    patient = await seed_patient(db_session, session_id)
    db_session.add(
        make_appointment(
            session_id,
            patient.id,
            elsewhere.id,
            _TUESDAY_9AM,
            _TUESDAY_9AM + timedelta(hours=1),
            status=AppointmentStatus.CANCELLED,
        )
    )
    await db_session.commit()

    busy = await appointment_repository.busy_intervals(
        db_session,
        session_id=session_id,
        practitioner_id=practitioner.id,
        patient_id=patient.id,
        from_date=_TUESDAY_9AM.date(),
        to_date=_TUESDAY_9AM.date(),
    )

    assert busy == []


async def test_busy_intervals_still_reports_a_standing_appointment(
    db_session: AsyncSession,
) -> None:
    from .conftest import make_appointment

    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id, full_name="Ada")
    other = await seed_patient(db_session, session_id, full_name="Bram")
    db_session.add(
        make_appointment(
            session_id,
            other.id,
            practitioner.id,
            _TUESDAY_9AM,
            _TUESDAY_9AM + timedelta(hours=1),
        )
    )
    await db_session.commit()

    busy = await appointment_repository.busy_intervals(
        db_session,
        session_id=session_id,
        practitioner_id=practitioner.id,
        patient_id=patient.id,
        from_date=_TUESDAY_9AM.date(),
        to_date=_TUESDAY_9AM.date(),
    )

    assert [(i.start, i.end) for i in busy] == [
        (_TUESDAY_9AM, _TUESDAY_9AM + timedelta(hours=1))
    ]


# --- excluded_appointment_id: an appointment never blocks its own change ------


async def test_busy_intervals_omits_the_excluded_appointment_from_the_practitioner(
    db_session: AsyncSession,
) -> None:
    from .conftest import make_appointment

    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id, full_name="Ada")
    other = await seed_patient(db_session, session_id, full_name="Bram")
    theirs = make_appointment(
        session_id,
        other.id,
        practitioner.id,
        _TUESDAY_9AM,
        _TUESDAY_9AM + timedelta(hours=1),
    )
    db_session.add(theirs)
    await db_session.commit()

    busy = await appointment_repository.busy_intervals(
        db_session,
        session_id=session_id,
        practitioner_id=practitioner.id,
        patient_id=patient.id,
        from_date=_TUESDAY_9AM.date(),
        to_date=_TUESDAY_9AM.date(),
        excluded_appointment_id=theirs.id,
    )

    assert busy == []


async def test_busy_intervals_omits_the_excluded_appointment_from_the_patient(
    db_session: AsyncSession,
) -> None:
    # Both sides: the appointment being moved must not block its own new time through
    # the patient's own commitments either.
    from .conftest import make_appointment

    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id, full_name="Dr A")
    elsewhere = await seed_practitioner(db_session, session_id, full_name="Dr B")
    patient = await seed_patient(db_session, session_id)
    mine = make_appointment(
        session_id,
        patient.id,
        elsewhere.id,
        _TUESDAY_9AM,
        _TUESDAY_9AM + timedelta(hours=1),
    )
    db_session.add(mine)
    await db_session.commit()

    busy = await appointment_repository.busy_intervals(
        db_session,
        session_id=session_id,
        practitioner_id=practitioner.id,
        patient_id=patient.id,
        from_date=_TUESDAY_9AM.date(),
        to_date=_TUESDAY_9AM.date(),
        excluded_appointment_id=mine.id,
    )

    assert busy == []


async def test_an_excluded_id_from_another_session_excludes_nothing(
    db_session: AsyncSession,
) -> None:
    # Scoped like every other id: passing another session's appointment id excludes
    # nothing, rather than revealing that it exists.
    from .conftest import make_appointment

    theirs_session = new_id()
    theirs_practitioner = await seed_practitioner(db_session, theirs_session)
    theirs_patient = await seed_patient(db_session, theirs_session)
    theirs = make_appointment(
        theirs_session,
        theirs_patient.id,
        theirs_practitioner.id,
        _TUESDAY_9AM,
        _TUESDAY_9AM + timedelta(hours=1),
    )
    db_session.add(theirs)
    await db_session.commit()

    busy = await appointment_repository.busy_intervals(
        db_session,
        session_id=theirs_session,
        practitioner_id=theirs_practitioner.id,
        patient_id=theirs_patient.id,
        from_date=_TUESDAY_9AM.date(),
        to_date=_TUESDAY_9AM.date(),
        # A well-formed id that belongs to nothing in any session this call can see.
        excluded_appointment_id=new_id(),
    )

    assert [(i.start, i.end) for i in busy] == [
        (_TUESDAY_9AM, _TUESDAY_9AM + timedelta(hours=1))
    ]


async def test_omitting_the_exclusion_leaves_every_appointment_in_place(
    db_session: AsyncSession,
) -> None:
    from .conftest import make_appointment

    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)
    mine = make_appointment(
        session_id,
        patient.id,
        practitioner.id,
        _TUESDAY_9AM,
        _TUESDAY_9AM + timedelta(hours=1),
    )
    db_session.add(mine)
    await db_session.commit()

    busy = await appointment_repository.busy_intervals(
        db_session,
        session_id=session_id,
        practitioner_id=practitioner.id,
        patient_id=patient.id,
        from_date=_TUESDAY_9AM.date(),
        to_date=_TUESDAY_9AM.date(),
    )

    assert len(busy) == 1
