"""The refusal precedence: exactly one reason per refusal, always the same one.

An attempt can break several rules at once. FR-006 fixes which one is reported, so the
cases here deliberately break two at a time - the interesting assertion is not that a
broken rule is caught, but that the *other* broken rule is not the one named.
"""

from datetime import datetime, timedelta

from scheduler.repositories import appointment_repository
from scheduler.repositories.appointment_repository import ChangeNoOp, ChangeRefused
from shared_models.scheduling import AppointmentStatus, ChangeFailureReason
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import make_appointment, new_id, seed_patient, seed_practitioner

_TUESDAY_9AM = datetime(2026, 8, 18, 9, 0)
_LOCAL_NOW = datetime(2026, 8, 17, 8, 0)
_AFTER_IT_STARTED = _TUESDAY_9AM + timedelta(minutes=30)
_WRONG_START = _TUESDAY_9AM + timedelta(hours=4)


async def _seed(
    session: AsyncSession, *, status: AppointmentStatus = AppointmentStatus.STANDING
) -> tuple[str, str, str, str]:
    """Returns: the session id, the patient id, the practitioner id, and the id of one
    appointment at Tuesday 09:00 in the given status.
    """
    session_id = new_id()
    practitioner = await seed_practitioner(session, session_id)
    patient = await seed_patient(session, session_id)
    appointment = make_appointment(
        session_id,
        patient.id,
        practitioner.id,
        _TUESDAY_9AM,
        _TUESDAY_9AM + timedelta(hours=1),
        status=status,
    )
    session.add(appointment)
    await session.commit()
    return session_id, patient.id, practitioner.id, appointment.id


async def _classify(
    session: AsyncSession,
    session_id: str,
    patient_id: str,
    appointment_id: str,
    practitioner_id: str,
    *,
    expected_starts_at: datetime = _TUESDAY_9AM,
    local_now: datetime = _LOCAL_NOW,
) -> ChangeFailureReason:
    return await appointment_repository.classify_change_failure(
        session,
        session_id=session_id,
        patient_id=patient_id,
        appointment_id=appointment_id,
        expected_starts_at=expected_starts_at,
        expected_practitioner_id=practitioner_id,
        local_now=local_now,
    )


async def test_a_missing_appointment_outranks_every_other_reason(
    db_session: AsyncSession,
) -> None:
    # An id that does not resolve also has a start that cannot be compared and a guard
    # that cannot match; only one of those is worth reporting.
    session_id, patient_id, practitioner_id, _ = await _seed(db_session)

    reason = await _classify(
        db_session,
        session_id,
        patient_id,
        new_id(),
        practitioner_id,
        expected_starts_at=_WRONG_START,
        local_now=_AFTER_IT_STARTED,
    )

    assert reason is ChangeFailureReason.APPOINTMENT_NOT_FOUND


async def test_another_sessions_appointment_is_not_found_not_stale(
    db_session: AsyncSession,
) -> None:
    _, patient_id, practitioner_id, appointment_id = await _seed(db_session)

    reason = await _classify(
        db_session, new_id(), patient_id, appointment_id, practitioner_id
    )

    assert reason is ChangeFailureReason.APPOINTMENT_NOT_FOUND


async def test_already_cancelled_outranks_already_started(
    db_session: AsyncSession,
) -> None:
    session_id, patient_id, practitioner_id, appointment_id = await _seed(
        db_session, status=AppointmentStatus.CANCELLED
    )

    reason = await _classify(
        db_session,
        session_id,
        patient_id,
        appointment_id,
        practitioner_id,
        local_now=_AFTER_IT_STARTED,
    )

    assert reason is ChangeFailureReason.ALREADY_CANCELLED


async def test_already_cancelled_outranks_a_stale_guard(
    db_session: AsyncSession,
) -> None:
    session_id, patient_id, practitioner_id, appointment_id = await _seed(
        db_session, status=AppointmentStatus.CANCELLED
    )

    reason = await _classify(
        db_session,
        session_id,
        patient_id,
        appointment_id,
        practitioner_id,
        expected_starts_at=_WRONG_START,
    )

    assert reason is ChangeFailureReason.ALREADY_CANCELLED


async def test_already_started_outranks_a_stale_guard(
    db_session: AsyncSession,
) -> None:
    session_id, patient_id, practitioner_id, appointment_id = await _seed(db_session)

    reason = await _classify(
        db_session,
        session_id,
        patient_id,
        appointment_id,
        practitioner_id,
        expected_starts_at=_WRONG_START,
        local_now=_AFTER_IT_STARTED,
    )

    assert reason is ChangeFailureReason.ALREADY_STARTED


async def test_a_stale_guard_is_reported_when_nothing_else_holds(
    db_session: AsyncSession,
) -> None:
    session_id, patient_id, practitioner_id, appointment_id = await _seed(db_session)

    reason = await _classify(
        db_session,
        session_id,
        patient_id,
        appointment_id,
        practitioner_id,
        expected_starts_at=_WRONG_START,
    )

    assert reason is ChangeFailureReason.STALE_CONFIRMATION


async def test_the_four_eligibility_reasons_come_first_in_the_declared_order() -> None:
    # The precedence lives in the enum's declaration order, and the resolver walks it.
    # Pinned here so a reordering that changes which reason a two-rule break reports
    # fails on the ordering itself rather than on a distant behavioural test.
    assert list(ChangeFailureReason)[:4] == [
        ChangeFailureReason.APPOINTMENT_NOT_FOUND,
        ChangeFailureReason.ALREADY_CANCELLED,
        ChangeFailureReason.ALREADY_STARTED,
        ChangeFailureReason.STALE_CONFIRMATION,
    ]


async def test_a_cancellation_is_reachable_by_only_three_of_the_four(
    db_session: AsyncSession,
) -> None:
    # `already_cancelled` is the target state of a cancellation, not a refusal of one,
    # so it answers `no_change` (research #9). The other three refuse it.
    session_id, patient_id, practitioner_id, appointment_id = await _seed(
        db_session, status=AppointmentStatus.CANCELLED
    )

    outcome = await appointment_repository.cancel(
        db_session,
        session_id=session_id,
        patient_id=patient_id,
        appointment_id=appointment_id,
        expected_starts_at=_TUESDAY_9AM,
        expected_practitioner_id=practitioner_id,
        local_now=_LOCAL_NOW,
    )

    assert isinstance(outcome, ChangeNoOp)


async def test_the_other_three_reasons_do_refuse_a_cancellation(
    db_session: AsyncSession,
) -> None:
    session_id, patient_id, practitioner_id, appointment_id = await _seed(db_session)

    async def cancel(**overrides: object) -> object:
        kwargs: dict[str, object] = {
            "session_id": session_id,
            "patient_id": patient_id,
            "appointment_id": appointment_id,
            "expected_starts_at": _TUESDAY_9AM,
            "expected_practitioner_id": practitioner_id,
            "local_now": _LOCAL_NOW,
        }
        kwargs.update(overrides)
        return await appointment_repository.cancel(db_session, **kwargs)  # type: ignore[arg-type]

    not_found = await cancel(appointment_id=new_id())
    already_started = await cancel(local_now=_AFTER_IT_STARTED)
    stale = await cancel(expected_starts_at=_WRONG_START)

    assert isinstance(not_found, ChangeRefused)
    assert not_found.reason is ChangeFailureReason.APPOINTMENT_NOT_FOUND
    assert isinstance(already_started, ChangeRefused)
    assert already_started.reason is ChangeFailureReason.ALREADY_STARTED
    assert isinstance(stale, ChangeRefused)
    assert stale.reason is ChangeFailureReason.STALE_CONFIRMATION
