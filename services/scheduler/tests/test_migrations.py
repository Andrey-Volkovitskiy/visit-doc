from datetime import datetime, time
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
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

_SCHEDULER_ROOT = Path(__file__).resolve().parents[1]

# How PostgreSQL renders `status = 'standing'` back out of a partial constraint or
# index, with its own casts and parenthesisation.
_STANDING_PREDICATE = "(status)::text = 'standing'::text"


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


async def _constraint_def(session: AsyncSession, name: str) -> str | None:
    result = await session.execute(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :name"
        ),
        {"name": name},
    )
    value = result.scalar()
    return None if value is None else str(value)


async def _index_def(session: AsyncSession, name: str) -> str | None:
    result = await session.execute(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
        {"name": name},
    )
    value = result.scalar()
    return None if value is None else str(value)


async def test_appointments_has_the_status_column(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT data_type, character_maximum_length, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_name = 'appointments' AND column_name = 'status'"
        )
    )
    row = result.one()
    assert row.data_type == "character varying"
    assert row.character_maximum_length == 16
    assert row.is_nullable == "NO"
    assert "standing" in row.column_default


async def test_the_status_check_constraint_names_both_values(
    db_session: AsyncSession,
) -> None:
    definition = await _constraint_def(db_session, "appointments_status_valid")
    assert definition is not None
    assert "standing" in definition
    assert "cancelled" in definition


async def test_both_exclusion_constraints_are_partial_on_standing(
    db_session: AsyncSession,
) -> None:
    # The single assertion the whole feature rests on: without this `WHERE`, a
    # cancelled appointment goes on occupying its slot and SC-011 fails at the
    # datastore, where no application filter could rescue it.
    for name in (
        "appointments_patient_no_overlap",
        "appointments_practitioner_no_overlap",
    ):
        definition = await _constraint_def(db_session, name)
        assert definition is not None, name
        assert _STANDING_PREDICATE in definition, name


async def test_the_idempotency_key_is_a_partial_unique_index(
    db_session: AsyncSession,
) -> None:
    definition = await _index_def(
        db_session, "ix_appointments_idempotency_key_standing"
    )
    assert definition is not None
    assert "UNIQUE" in definition
    assert _STANDING_PREDICATE in definition


async def test_the_old_unconditional_key_constraint_is_gone(
    db_session: AsyncSession,
) -> None:
    # Dropped, not left alongside: keeping it would go on holding the key of a
    # cancelled appointment and FR-011's release would never happen.
    assert (
        await _constraint_def(db_session, "appointments_idempotency_key_unique")
        is None
    )


async def test_the_listing_index_exists(db_session: AsyncSession) -> None:
    definition = await _index_def(db_session, "ix_appointments_patient_status_starts")
    assert definition is not None
    assert "patient_id" in definition
    assert "status" in definition
    assert "starts_at" in definition


def test_the_status_revision_downgrades_and_upgrades_cleanly() -> None:
    """Round-trip the 006 revision, and leave the schema back at head.

    Synchronous, and outside the `db_session` fixture, because alembic drives its own
    sync engine: an async session holding a transaction open would deadlock the DDL
    this takes.
    """
    from shared_db import sync_database_url
    from sqlalchemy import create_engine as create_sync_engine

    alembic_cfg = Config(str(_SCHEDULER_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(_SCHEDULER_ROOT / "alembic"))
    # The same env var alembic's own env.py reads, so the round trip runs against the
    # isolated test database this suite's conftest pointed that var at.
    engine = create_sync_engine(sync_database_url("SCHEDULER_DATABASE_URL"))

    def status_columns() -> int:
        with engine.connect() as connection:
            return int(
                connection.execute(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name = 'appointments' AND column_name = 'status'"
                    )
                ).scalar_one()
            )

    def key_constraints() -> int:
        with engine.connect() as connection:
            return int(
                connection.execute(
                    text(
                        "SELECT count(*) FROM pg_constraint "
                        "WHERE conname = 'appointments_idempotency_key_unique'"
                    )
                ).scalar_one()
            )

    try:
        command.downgrade(alembic_cfg, "-1")
        assert status_columns() == 0
        # The 005 shape is genuinely restored, not merely stripped of the column: the
        # unconditional key constraint comes back, so a downgrade leaves a database
        # the previous release can run against.
        assert key_constraints() == 1

        command.upgrade(alembic_cfg, "head")
        assert status_columns() == 1
        assert key_constraints() == 0
    finally:
        # Head either way, so a failure mid-round-trip does not leave every later
        # test in this session running against a half-migrated schema.
        command.upgrade(alembic_cfg, "head")
        engine.dispose()
