from datetime import datetime, time

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import (
    make_appointment,
    make_patient,
    make_practitioner,
    make_working_range,
    new_id,
)


async def _scalar(session: AsyncSession, sql: str) -> object:
    result = await session.execute(text(sql))
    return result.scalar()


async def test_btree_gist_extension_is_installed(db_session: AsyncSession) -> None:
    assert await _scalar(
        db_session, "SELECT count(*) FROM pg_extension WHERE extname = 'btree_gist'"
    )


async def test_timerange_type_exists(db_session: AsyncSession) -> None:
    assert await _scalar(
        db_session, "SELECT count(*) FROM pg_type WHERE typname = 'timerange'"
    )


async def test_all_four_tables_are_present(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename IN "
            "('practitioners', 'working_ranges', 'patients', 'appointments')"
        )
    )
    assert set(result.scalars().all()) == {
        "practitioners",
        "working_ranges",
        "patients",
        "appointments",
    }


async def test_practitioner_overlap_is_rejected(db_session: AsyncSession) -> None:
    session_id = new_id()
    practitioner = make_practitioner(session_id)
    first_patient = make_patient(session_id, full_name="Ada")
    second_patient = make_patient(session_id, full_name="Bram")
    db_session.add_all([practitioner, first_patient, second_patient])
    await db_session.commit()

    db_session.add(
        make_appointment(
            session_id,
            first_patient.id,
            practitioner.id,
            datetime(2026, 8, 18, 9, 0),
            datetime(2026, 8, 18, 10, 0),
        )
    )
    await db_session.commit()

    db_session.add(
        make_appointment(
            session_id,
            second_patient.id,
            practitioner.id,
            datetime(2026, 8, 18, 9, 30),
            datetime(2026, 8, 18, 10, 30),
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_patient_overlap_is_rejected(db_session: AsyncSession) -> None:
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
            datetime(2026, 8, 18, 9, 0),
            datetime(2026, 8, 18, 10, 0),
        )
    )
    await db_session.commit()

    db_session.add(
        make_appointment(
            session_id,
            patient.id,
            second_practitioner.id,
            datetime(2026, 8, 18, 9, 30),
            datetime(2026, 8, 18, 10, 30),
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_back_to_back_appointments_are_accepted(db_session: AsyncSession) -> None:
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
                datetime(2026, 8, 18, 9, 0),
                datetime(2026, 8, 18, 10, 0),
            ),
            make_appointment(
                session_id,
                patient.id,
                practitioner.id,
                datetime(2026, 8, 18, 10, 0),
                datetime(2026, 8, 18, 11, 0),
            ),
        ]
    )
    await db_session.commit()

    assert await _scalar(db_session, "SELECT count(*) FROM appointments") == 2


async def test_working_range_overlap_on_one_weekday_is_rejected(
    db_session: AsyncSession,
) -> None:
    practitioner = make_practitioner(new_id())
    db_session.add(practitioner)
    await db_session.commit()

    db_session.add(make_working_range(practitioner.id, 1, time(9, 0), time(12, 0)))
    await db_session.commit()

    db_session.add(make_working_range(practitioner.id, 1, time(11, 0), time(14, 0)))
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_contiguous_working_ranges_on_one_weekday_are_accepted(
    db_session: AsyncSession,
) -> None:
    practitioner = make_practitioner(new_id())
    db_session.add(practitioner)
    await db_session.commit()

    db_session.add_all(
        [
            make_working_range(practitioner.id, 1, time(8, 0), time(12, 0)),
            make_working_range(practitioner.id, 1, time(12, 0), time(16, 0)),
        ]
    )
    await db_session.commit()

    assert await _scalar(db_session, "SELECT count(*) FROM working_ranges") == 2


async def test_the_same_range_on_two_weekdays_is_accepted(
    db_session: AsyncSession,
) -> None:
    practitioner = make_practitioner(new_id())
    db_session.add(practitioner)
    await db_session.commit()

    db_session.add_all(
        [
            make_working_range(practitioner.id, 1, time(9, 0), time(17, 0)),
            make_working_range(practitioner.id, 2, time(9, 0), time(17, 0)),
        ]
    )
    await db_session.commit()

    assert await _scalar(db_session, "SELECT count(*) FROM working_ranges") == 2


async def test_deleting_a_practitioner_cascades_to_ranges_and_appointments(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()
    practitioner = make_practitioner(session_id)
    patient = make_patient(session_id)
    db_session.add_all([practitioner, patient])
    await db_session.commit()
    db_session.add_all(
        [
            make_working_range(practitioner.id, 1, time(9, 0), time(17, 0)),
            make_appointment(
                session_id,
                patient.id,
                practitioner.id,
                datetime(2026, 8, 18, 9, 0),
                datetime(2026, 8, 18, 10, 0),
            ),
        ]
    )
    await db_session.commit()

    await db_session.delete(practitioner)
    await db_session.commit()

    assert await _scalar(db_session, "SELECT count(*) FROM working_ranges") == 0
    assert await _scalar(db_session, "SELECT count(*) FROM appointments") == 0
    assert await _scalar(db_session, "SELECT count(*) FROM patients") == 1


async def test_deleting_a_patient_cascades_to_their_appointments(
    db_session: AsyncSession,
) -> None:
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
            datetime(2026, 8, 18, 9, 0),
            datetime(2026, 8, 18, 10, 0),
        )
    )
    await db_session.commit()

    await db_session.delete(patient)
    await db_session.commit()

    assert await _scalar(db_session, "SELECT count(*) FROM appointments") == 0
    assert await _scalar(db_session, "SELECT count(*) FROM practitioners") == 1


async def test_practitioner_name_is_unique_within_a_session(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()
    db_session.add(make_practitioner(session_id, full_name="Dr A"))
    await db_session.commit()
    db_session.add(make_practitioner(session_id, full_name="Dr A"))
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_the_same_practitioner_name_may_exist_in_two_sessions(
    db_session: AsyncSession,
) -> None:
    db_session.add_all(
        [
            make_practitioner(new_id(), full_name="Dr A"),
            make_practitioner(new_id(), full_name="Dr A"),
        ]
    )
    await db_session.commit()
    assert await _scalar(db_session, "SELECT count(*) FROM practitioners") == 2


async def test_patient_name_is_unique_within_a_session(
    db_session: AsyncSession,
) -> None:
    session_id = new_id()
    db_session.add(make_patient(session_id, full_name="Ada"))
    await db_session.commit()
    db_session.add(make_patient(session_id, full_name="Ada"))
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_chat_id_is_globally_unique(db_session: AsyncSession) -> None:
    chat_id = new_id()
    db_session.add(make_patient(new_id(), full_name="Ada", chat_id=chat_id))
    await db_session.commit()
    db_session.add(make_patient(new_id(), full_name="Bram", chat_id=chat_id))
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_idempotency_key_is_globally_unique(db_session: AsyncSession) -> None:
    session_id = new_id()
    practitioner = make_practitioner(session_id)
    first_patient = make_patient(session_id, full_name="Ada")
    second_patient = make_patient(session_id, full_name="Bram")
    db_session.add_all([practitioner, first_patient, second_patient])
    await db_session.commit()

    key = "shared-key"
    db_session.add(
        make_appointment(
            session_id,
            first_patient.id,
            practitioner.id,
            datetime(2026, 8, 18, 9, 0),
            datetime(2026, 8, 18, 10, 0),
            idempotency_key=key,
        )
    )
    await db_session.commit()

    db_session.add(
        make_appointment(
            session_id,
            second_patient.id,
            practitioner.id,
            datetime(2026, 8, 19, 9, 0),
            datetime(2026, 8, 19, 10, 0),
            idempotency_key=key,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
