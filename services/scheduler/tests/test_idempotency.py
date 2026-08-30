"""Tests for the derived idempotency key: replay, mismatch, and the refused case."""

from datetime import datetime, timedelta

import pytest
from scheduler.db.session import session_factory
from scheduler.domain.models import Appointment
from scheduler.repositories import appointment_repository
from scheduler.repositories.appointment_repository import (
    BookingCreated,
    BookingRefused,
    IdempotencyKeyMismatchError,
)
from shared_models.scheduling import BookingFailureReason
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from .conftest import new_id, seed_patient, seed_practitioner

_TUESDAY_9AM = datetime(2026, 8, 18, 9, 0)
_LOCAL_NOW = datetime(2026, 8, 17, 8, 0)
_HORIZON_DAYS = 90
_KEY = "derived-key-for-this-booking"


async def _book(
    session: AsyncSession,
    *,
    session_id: str,
    patient_id: str,
    practitioner_id: str,
    starts_at: datetime = _TUESDAY_9AM,
    local_now: datetime = _LOCAL_NOW,
    idempotency_key: str = _KEY,
) -> BookingCreated | BookingRefused:
    return await appointment_repository.book(
        session,
        session_id=session_id,
        patient_id=patient_id,
        practitioner_id=practitioner_id,
        starts_at=starts_at,
        local_now=local_now,
        idempotency_key=idempotency_key,
        horizon_days=_HORIZON_DAYS,
    )


async def _appointment_count(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(Appointment))
    return int(result.scalar_one())


async def test_an_unused_key_inserts(db_session: AsyncSession) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)

    outcome = await _book(
        db_session,
        session_id=session_id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
    )

    assert isinstance(outcome, BookingCreated)
    assert outcome.idempotent_replay is False
    assert outcome.appointment.idempotency_key == _KEY


async def test_a_used_key_with_a_matching_request_replays_and_creates_nothing(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)

    first = await _book(
        db_session,
        session_id=session_id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
    )
    second = await _book(
        db_session,
        session_id=session_id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
    )

    assert isinstance(first, BookingCreated)
    assert isinstance(second, BookingCreated)
    assert second.appointment.id == first.appointment.id
    assert second.idempotent_replay is True
    assert await _appointment_count(db_session) == 1


async def test_a_replay_is_never_reported_as_a_conflict_with_itself(
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

    replayed = await _book(
        db_session,
        session_id=session_id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
    )

    assert not isinstance(replayed, BookingRefused)


@pytest.mark.parametrize("differing", ["patient", "practitioner", "starts_at"])
async def test_a_used_key_with_any_differing_field_is_refused_not_replayed(
    db_session: AsyncSession, differing: str
) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id, full_name="A")
    other_practitioner = await seed_practitioner(db_session, session_id, full_name="B")
    patient = await seed_patient(db_session, session_id, full_name="Ada")
    other_patient = await seed_patient(db_session, session_id, full_name="Bram")

    await _book(
        db_session,
        session_id=session_id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
    )

    request = {
        "patient_id": patient.id,
        "practitioner_id": practitioner.id,
        "starts_at": _TUESDAY_9AM,
    }
    request |= {
        "patient": {"patient_id": other_patient.id},
        "practitioner": {"practitioner_id": other_practitioner.id},
        "starts_at": {"starts_at": _TUESDAY_9AM + timedelta(hours=1)},
    }[differing]

    with pytest.raises(IdempotencyKeyMismatchError) as raised:
        await _book(db_session, session_id=session_id, **request)  # type: ignore[arg-type]

    assert raised.value.mismatched_fields == [differing]
    assert await _appointment_count(db_session) == 1


async def test_a_key_mismatch_is_logged_as_a_defect(db_session: AsyncSession) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)
    created = await _book(
        db_session,
        session_id=session_id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
    )
    assert isinstance(created, BookingCreated)

    with capture_logs() as logs, pytest.raises(IdempotencyKeyMismatchError):
        await _book(
            db_session,
            session_id=session_id,
            patient_id=patient.id,
            practitioner_id=practitioner.id,
            starts_at=_TUESDAY_9AM + timedelta(hours=1),
        )

    mismatch = next(e for e in logs if e["event"] == "booking.key_mismatch")
    assert mismatch["log_level"] == "error"
    assert mismatch["idempotency_key"] == _KEY
    assert mismatch["stored_appointment_id"] == created.appointment.id
    assert mismatch["mismatched_fields"] == ["starts_at"]


async def test_a_key_presented_after_a_refusal_is_evaluated_afresh(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)

    # Refused: a Sunday falls outside the default Monday-Friday schedule.
    refused = await _book(
        db_session,
        session_id=session_id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
        starts_at=datetime(2026, 8, 23, 10, 0),
    )
    assert refused == BookingRefused(BookingFailureReason.OUTSIDE_SCHEDULE)

    # The same key, now for a legal time: the refusal did not consume it, and this is
    # not treated as a mismatch either.
    retried = await _book(
        db_session,
        session_id=session_id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
    )

    assert isinstance(retried, BookingCreated)
    assert retried.idempotent_replay is False


async def test_deleting_the_appointment_frees_its_key(
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

    await db_session.delete(patient)
    await db_session.commit()
    replacement = await seed_patient(db_session, session_id, full_name="Ada II")

    reused = await _book(
        db_session,
        session_id=session_id,
        patient_id=replacement.id,
        practitioner_id=practitioner.id,
    )

    assert isinstance(reused, BookingCreated)
    assert reused.idempotent_replay is False


async def test_two_concurrent_identical_attempts_yield_one_row_and_a_replay(
    db_session: AsyncSession,
) -> None:
    """The UNIQUE constraint is the race guard; the loser re-reads and replays.

    Both attempts must report the same appointment, and neither may report the
    patient's own booking back to them as a conflict.
    """
    import asyncio

    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)

    async def attempt() -> BookingCreated | BookingRefused:
        async with session_factory() as session:
            return await _book(
                session,
                session_id=session_id,
                patient_id=patient.id,
                practitioner_id=practitioner.id,
            )

    outcomes = await asyncio.gather(attempt(), attempt())

    assert all(isinstance(o, BookingCreated) for o in outcomes)
    ids = {o.appointment.id for o in outcomes if isinstance(o, BookingCreated)}
    assert len(ids) == 1
    async with session_factory() as session:
        assert await _appointment_count(session) == 1


async def test_get_by_idempotency_key_ignores_a_cancelled_row_holding_the_key(
    db_session: AsyncSession,
) -> None:
    from shared_models.scheduling import AppointmentStatus

    from .conftest import make_appointment

    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)
    db_session.add(
        make_appointment(
            session_id,
            patient.id,
            practitioner.id,
            _TUESDAY_9AM,
            _TUESDAY_9AM + timedelta(hours=1),
            idempotency_key=_KEY,
            status=AppointmentStatus.CANCELLED,
        )
    )
    await db_session.commit()

    assert await appointment_repository.get_by_idempotency_key(db_session, _KEY) is None


async def test_a_key_freed_by_cancellation_books_a_new_appointment_not_a_replay(
    db_session: AsyncSession,
) -> None:
    # The worst outcome available in this feature is a cancelled appointment being
    # replayed to a caller rebooking that slot - returning it as a fresh booking.
    from shared_models.scheduling import AppointmentStatus

    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)
    first = await _book(
        db_session,
        session_id=session_id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
    )
    assert isinstance(first, BookingCreated)
    original_id = first.appointment.id

    first.appointment.status = AppointmentStatus.CANCELLED
    await db_session.commit()

    second = await _book(
        db_session,
        session_id=session_id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
    )

    assert isinstance(second, BookingCreated)
    assert second.idempotent_replay is False
    assert second.appointment.id != original_id
    assert second.appointment.status == AppointmentStatus.STANDING
