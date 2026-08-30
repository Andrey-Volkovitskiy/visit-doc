"""The four datastore rules a cancelled appointment depends on, against a real database.

These are the load-bearing rules of this feature and the only ones whose violation is
invisible in application code: an exclusion constraint written without its `WHERE`
passes every single-threaded test and fails "a cancelled slot is bookable again
immediately" in production. So they are asserted here, by writing rows and watching
PostgreSQL accept or reject them - never by reading a `WHERE` clause back out of
`pg_constraint` and calling that a test.
"""

from datetime import datetime

import pytest
from shared_models.scheduling import AppointmentStatus
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import (
    make_appointment,
    make_patient,
    make_practitioner,
    new_id,
)

_NINE = datetime(2026, 9, 2, 9, 0)
_TEN = datetime(2026, 9, 2, 10, 0)


async def test_a_cancelled_appointment_does_not_block_the_practitioner_slot(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()
    practitioner = make_practitioner(session_id)
    first = make_patient(session_id, full_name="Ada")
    second = make_patient(session_id, full_name="Bram")
    db_session.add_all([practitioner, first, second])
    await db_session.commit()

    db_session.add(
        make_appointment(
            session_id,
            first.id,
            practitioner.id,
            _NINE,
            _TEN,
            status=AppointmentStatus.CANCELLED,
        )
    )
    await db_session.commit()

    db_session.add(
        make_appointment(session_id, second.id, practitioner.id, _NINE, _TEN)
    )
    await db_session.commit()

    result = await db_session.execute(text("SELECT count(*) FROM appointments"))
    assert result.scalar() == 2


async def test_a_cancelled_appointment_does_not_block_the_patient_slot(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()
    first_practitioner = make_practitioner(session_id, full_name="Dr A")
    second_practitioner = make_practitioner(session_id, full_name="Dr B")
    patient = make_patient(session_id)
    db_session.add_all([first_practitioner, second_practitioner, patient])
    await db_session.commit()

    db_session.add(
        make_appointment(
            session_id,
            patient.id,
            first_practitioner.id,
            _NINE,
            _TEN,
            status=AppointmentStatus.CANCELLED,
        )
    )
    await db_session.commit()

    db_session.add(
        make_appointment(session_id, patient.id, second_practitioner.id, _NINE, _TEN)
    )
    await db_session.commit()

    result = await db_session.execute(text("SELECT count(*) FROM appointments"))
    assert result.scalar() == 2


async def test_two_standing_appointments_still_collide_on_the_practitioner(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()
    practitioner = make_practitioner(session_id)
    first = make_patient(session_id, full_name="Ada")
    second = make_patient(session_id, full_name="Bram")
    db_session.add_all([practitioner, first, second])
    await db_session.commit()

    db_session.add(make_appointment(session_id, first.id, practitioner.id, _NINE, _TEN))
    await db_session.commit()

    db_session.add(
        make_appointment(session_id, second.id, practitioner.id, _NINE, _TEN)
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_two_standing_appointments_still_collide_on_the_patient(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()
    first_practitioner = make_practitioner(session_id, full_name="Dr A")
    second_practitioner = make_practitioner(session_id, full_name="Dr B")
    patient = make_patient(session_id)
    db_session.add_all([first_practitioner, second_practitioner, patient])
    await db_session.commit()

    db_session.add(
        make_appointment(session_id, patient.id, first_practitioner.id, _NINE, _TEN)
    )
    await db_session.commit()

    db_session.add(
        make_appointment(session_id, patient.id, second_practitioner.id, _NINE, _TEN)
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_cancelling_frees_the_slot_for_a_new_standing_appointment(
    db_session: AsyncSession,
) -> None:
    # The sequence SC-011 actually describes: the row is standing, blocks the slot, is
    # cancelled in place, and the same slot then takes a new booking.
    session_id = new_id()
    practitioner = make_practitioner(session_id)
    first = make_patient(session_id, full_name="Ada")
    second = make_patient(session_id, full_name="Bram")
    db_session.add_all([practitioner, first, second])
    await db_session.commit()

    standing = make_appointment(session_id, first.id, practitioner.id, _NINE, _TEN)
    db_session.add(standing)
    await db_session.commit()

    standing.status = AppointmentStatus.CANCELLED
    await db_session.commit()

    db_session.add(
        make_appointment(session_id, second.id, practitioner.id, _NINE, _TEN)
    )
    await db_session.commit()

    result = await db_session.execute(
        text("SELECT count(*) FROM appointments WHERE status = 'standing'")
    )
    assert result.scalar() == 1


async def test_a_cancelled_rows_idempotency_key_may_be_reused(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()
    practitioner = make_practitioner(session_id)
    patient = make_patient(session_id)
    db_session.add_all([practitioner, patient])
    await db_session.commit()

    key = "shared-key"
    db_session.add(
        make_appointment(
            session_id,
            patient.id,
            practitioner.id,
            _NINE,
            _TEN,
            idempotency_key=key,
            status=AppointmentStatus.CANCELLED,
        )
    )
    await db_session.commit()

    db_session.add(
        make_appointment(
            session_id,
            patient.id,
            practitioner.id,
            _NINE,
            _TEN,
            idempotency_key=key,
        )
    )
    await db_session.commit()

    result = await db_session.execute(
        text("SELECT count(*) FROM appointments WHERE idempotency_key = :k"),
        {"k": key},
    )
    assert result.scalar() == 2


async def test_two_standing_rows_may_not_share_an_idempotency_key(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()
    practitioner = make_practitioner(session_id)
    first = make_patient(session_id, full_name="Ada")
    second = make_patient(session_id, full_name="Bram")
    db_session.add_all([practitioner, first, second])
    await db_session.commit()

    key = "shared-key"
    db_session.add(
        make_appointment(
            session_id,
            first.id,
            practitioner.id,
            _NINE,
            _TEN,
            idempotency_key=key,
        )
    )
    await db_session.commit()

    db_session.add(
        make_appointment(
            session_id,
            second.id,
            practitioner.id,
            datetime(2026, 9, 3, 9, 0),
            datetime(2026, 9, 3, 10, 0),
            idempotency_key=key,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_two_cancelled_rows_may_share_an_idempotency_key(
    db_session: AsyncSession,
) -> None:
    # The index is partial, so it constrains nothing among cancelled rows at all - a
    # slot booked, cancelled, rebooked and cancelled again leaves two of them.
    session_id = new_id()
    practitioner = make_practitioner(session_id)
    first = make_patient(session_id, full_name="Ada")
    second = make_patient(session_id, full_name="Bram")
    db_session.add_all([practitioner, first, second])
    await db_session.commit()

    key = "shared-key"
    db_session.add_all(
        [
            make_appointment(
                session_id,
                first.id,
                practitioner.id,
                _NINE,
                _TEN,
                idempotency_key=key,
                status=AppointmentStatus.CANCELLED,
            ),
            make_appointment(
                session_id,
                second.id,
                practitioner.id,
                _NINE,
                _TEN,
                idempotency_key=key,
                status=AppointmentStatus.CANCELLED,
            ),
        ]
    )
    await db_session.commit()

    result = await db_session.execute(
        text("SELECT count(*) FROM appointments WHERE idempotency_key = :k"),
        {"k": key},
    )
    assert result.scalar() == 2


async def test_the_check_constraint_rejects_a_status_outside_the_two(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()
    practitioner = make_practitioner(session_id)
    patient = make_patient(session_id)
    db_session.add_all([practitioner, patient])
    await db_session.commit()
    appointment = make_appointment(session_id, patient.id, practitioner.id, _NINE, _TEN)
    db_session.add(appointment)
    await db_session.commit()

    # Written as raw SQL: the point is that the database refuses it, so going through
    # a Python enum that cannot hold the value would test the wrong layer.
    with pytest.raises(DBAPIError):
        await db_session.execute(
            text("UPDATE appointments SET status = 'pending' WHERE id = :id"),
            {"id": appointment.id},
        )
        await db_session.commit()
    await db_session.rollback()


async def test_status_defaults_to_standing_for_a_row_that_does_not_name_one(
    db_session: AsyncSession,
) -> None:
    # Existing rows are standing, which is exactly what they are - the migration's
    # default is what makes that true without a backfill.
    session_id = new_id()
    practitioner = make_practitioner(session_id)
    patient = make_patient(session_id)
    db_session.add_all([practitioner, patient])
    await db_session.commit()

    await db_session.execute(
        text(
            "INSERT INTO appointments "
            "(id, session_id, patient_id, practitioner_id, starts_at, ends_at, "
            " idempotency_key) "
            "VALUES (:id, :sid, :pat, :prac, :start, :end, :key)"
        ),
        {
            "id": new_id(),
            "sid": session_id,
            "pat": patient.id,
            "prac": practitioner.id,
            "start": _NINE,
            "end": _TEN,
            "key": new_id(),
        },
    )
    await db_session.commit()

    result = await db_session.execute(text("SELECT status FROM appointments"))
    assert result.scalar() == AppointmentStatus.STANDING.value
