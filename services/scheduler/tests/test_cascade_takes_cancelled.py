"""The delete cascades are deliberately status-blind, and this pins that.

Every *read* in this service gained a status predicate with this feature. The cascades
did not, and must not: deleting a chat's patient, or a practitioner, has to take that
party's cancelled appointments along with their standing ones, or a cancellation would
strand rows behind a party that no longer exists.

This is a regression pin, not a red-green step - it passes as written the moment the
status column exists. It is here to fail the day someone reads "every read states its
statuses" as covering the foreign keys too and scopes a cascade to standing rows.
"""

from datetime import datetime

from shared_models.scheduling import AppointmentStatus
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import (
    make_appointment,
    make_patient,
    make_practitioner,
    new_id,
)


async def _appointment_count(session: AsyncSession) -> int:
    result = await session.execute(text("SELECT count(*) FROM appointments"))
    return int(result.scalar_one())


async def test_deleting_a_patient_takes_their_cancelled_appointments_too(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()
    practitioner = make_practitioner(session_id)
    patient = make_patient(session_id)
    db_session.add_all([practitioner, patient])
    await db_session.commit()
    db_session.add_all(
        [
            make_appointment(
                session_id,
                patient.id,
                practitioner.id,
                datetime(2026, 9, 2, 9, 0),
                datetime(2026, 9, 2, 10, 0),
            ),
            make_appointment(
                session_id,
                patient.id,
                practitioner.id,
                datetime(2026, 9, 3, 9, 0),
                datetime(2026, 9, 3, 10, 0),
                status=AppointmentStatus.CANCELLED,
            ),
        ]
    )
    await db_session.commit()
    assert await _appointment_count(db_session) == 2

    await db_session.delete(patient)
    await db_session.commit()

    assert await _appointment_count(db_session) == 0


async def test_deleting_a_practitioner_takes_their_cancelled_appointments_too(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()
    practitioner = make_practitioner(session_id)
    patient = make_patient(session_id)
    db_session.add_all([practitioner, patient])
    await db_session.commit()
    db_session.add_all(
        [
            make_appointment(
                session_id,
                patient.id,
                practitioner.id,
                datetime(2026, 9, 2, 9, 0),
                datetime(2026, 9, 2, 10, 0),
            ),
            make_appointment(
                session_id,
                patient.id,
                practitioner.id,
                datetime(2026, 9, 3, 9, 0),
                datetime(2026, 9, 3, 10, 0),
                status=AppointmentStatus.CANCELLED,
            ),
        ]
    )
    await db_session.commit()

    await db_session.delete(practitioner)
    await db_session.commit()

    assert await _appointment_count(db_session) == 0
    assert (
        await db_session.execute(text("SELECT count(*) FROM patients"))
    ).scalar_one() == 1


async def test_a_wholly_cancelled_patient_is_still_deleted_with_their_rows(
    db_session: AsyncSession,
) -> None:
    # The case a status-scoped cascade would pass every other test on: a patient whose
    # every appointment is cancelled has nothing standing to cascade, so a filtered
    # cascade leaves the rows behind and the patient delete fails on the foreign key.
    session_id = new_id()
    practitioner = make_practitioner(session_id)
    patient = make_patient(session_id)
    db_session.add_all([practitioner, patient])
    await db_session.commit()
    db_session.add(
        make_appointment(
            session_id,
            patient.id,
            practitioner.id,
            datetime(2026, 9, 2, 9, 0),
            datetime(2026, 9, 2, 10, 0),
            status=AppointmentStatus.CANCELLED,
        )
    )
    await db_session.commit()

    await db_session.delete(patient)
    await db_session.commit()

    assert await _appointment_count(db_session) == 0
