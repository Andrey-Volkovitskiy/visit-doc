"""Shared pytest fixtures for scheduler's unit tests."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, time
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import ASGITransport
from httpx import AsyncClient as HttpxAsyncClient
from scheduler.core.config import Settings
from scheduler.domain.models import (
    Appointment,
    Patient,
    Practitioner,
    WorkingRange,
)
from scheduler.repositories import practitioner_repository
from shared_db import ensure_database_exists, isolated_database_url
from shared_models.scheduling import AppointmentStatus, Specialty, Weekday
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from ulid import ULID

_SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
# Must run before any other `scheduler.*` module reads SCHEDULER_DATABASE_URL (env vars
# beat `.env`, so this override reaches every later Settings()/get_settings() call).
# Uses Settings() directly, not get_settings(): it needs the pre-override value, and
# caching it here would freeze the singleton on the dev URL for the whole session.
_base_settings = Settings()
os.environ["SCHEDULER_DATABASE_URL"] = isolated_database_url(
    _base_settings.SCHEDULER_DATABASE_URL
)


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations_to_test_database() -> None:
    """Create this process's isolated database if needed, then bring it to head.

    The shared `visitdoc_scheduler_test` is provisioned before any suite runs, but a
    namespaced one - a pytest-xdist worker's, or a second concurrent run's - is named
    after a process that does not exist until now, so it asks for its own.
    """
    ensure_database_exists(os.environ["SCHEDULER_DATABASE_URL"])
    alembic_cfg = Config(str(_SCHEDULER_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(_SCHEDULER_ROOT / "alembic"))
    command.upgrade(alembic_cfg, "head")


@pytest_asyncio.fixture(scope="session")
async def _cleaning_engine() -> AsyncIterator[AsyncEngine]:
    """The engine `_clear_scheduling_tables` runs its per-test wipe on, built once.

    Built once rather than per test because connecting is what the wipe costs, not the
    statement: establishing and tearing down a connection to run one `DELETE` measures
    ~80ms against ~20ms for the statement itself, and this suite pays it before every
    one of its tests.

    Deliberately not `scheduler.db.session.engine`, for the reason
    `_clear_scheduling_tables` gives below - and that is exactly what makes holding it
    open for the session safe: `asyncio_default_fixture_loop_scope` is `session`, so
    this fixture and the per-test one that uses it stay on one loop for the whole run,
    and nothing else ever borrows a connection from it.
    """
    from shared_db import create_engine

    engine = create_engine(Settings().SCHEDULER_DATABASE_URL)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _clear_scheduling_tables(_cleaning_engine: AsyncEngine) -> None:
    """Empty every scheduling table before each test.

    The repositories under test issue real commits against the isolated test database,
    and nothing else in this suite reliably cleans them up. The table list comes from
    the schema itself, so a table added later is cleared without anyone remembering to
    add it here.

    `DELETE` rather than `TRUNCATE`, on an engine this fixture borrows rather than
    builds. The tables hold a handful of rows at this point, so `TRUNCATE`'s table
    rewrite and its `ACCESS EXCLUSIVE` lock buy nothing and measure several times the
    deletes; the connection setup a per-test engine paid for measures more than either.
    `RESTART IDENTITY` goes with it and costs nothing: every id in this schema is a
    ULID string, so there is no sequence to restart.

    The deletes go child-first, the reverse of `all_table_names()`, so each runs with
    its referencing rows already gone. It is the ordering, not `CASCADE`, that keeps the
    foreign keys satisfied - a delete that silently took rows from a table not named
    here would be exactly the kind of reach `TRUNCATE ... CASCADE` allowed.

    The engine is not `scheduler.db.session`'s module-level singleton: that engine's
    pool is deliberately disposed per test by `_reset_engine_pool_between_tests` below,
    and binding it to this fixture's own loop first would break that.
    `scheduler.db.session` is also imported lazily for the same reason chat's conftest
    does - it builds its engine from `get_settings()` at import time, so a module-level
    import here would run before the `SCHEDULER_DATABASE_URL` override above.
    """
    from scheduler.domain.models import all_table_names

    async with _cleaning_engine.begin() as connection:
        for table in reversed(all_table_names()):
            await connection.execute(sql_text(f"DELETE FROM {table}"))


@pytest_asyncio.fixture(autouse=True)
async def _reset_engine_pool_between_tests() -> AsyncIterator[None]:
    """Dispose the shared async engine's connection pool after each test.

    `scheduler.db.session.engine` is a module-level singleton; its asyncpg connections
    bind to whichever event loop first uses them. Each `TestClient(app)` instantiation
    can run on its own loop, so without this a later test reusing the same pool on a
    different loop fails with "attached to a different loop" / "another operation is in
    progress". Both this and the session-scoped loop settings in the root
    `pyproject.toml` are required; neither alone is sufficient.
    """
    yield
    from scheduler.db.session import engine

    await engine.dispose()


def new_id() -> str:
    """Return a fresh ULID string, for a test-authored session/chat/entity id."""
    return str(ULID())


def make_practitioner(
    session_id: str,
    *,
    full_name: str = "Dr A",
    specialty: Specialty = practitioner_repository.DEFAULT_SPECIALTY,
    duration_minutes: int = practitioner_repository.DEFAULT_DURATION_MINUTES,
) -> Practitioner:
    """Build an unsaved `Practitioner` with a fresh id."""
    return Practitioner(
        id=new_id(),
        session_id=session_id,
        full_name=full_name,
        specialty=specialty,
        appointment_duration_minutes=duration_minutes,
    )


def make_working_range(
    practitioner_id: str, weekday: int, start: time, end: time
) -> WorkingRange:
    """Build an unsaved `WorkingRange` with a fresh id."""
    return WorkingRange(
        id=new_id(),
        practitioner_id=practitioner_id,
        weekday=weekday,
        start_time=start,
        end_time=end,
    )


def make_patient(
    session_id: str, *, full_name: str = "Ada", chat_id: str | None = None
) -> Patient:
    """Build an unsaved `Patient` with a fresh id and, unless given, a fresh chat id."""
    return Patient(
        id=new_id(),
        session_id=session_id,
        chat_id=chat_id if chat_id is not None else new_id(),
        full_name=full_name,
    )


def make_appointment(
    session_id: str,
    patient_id: str,
    practitioner_id: str,
    starts_at: datetime,
    ends_at: datetime,
    *,
    idempotency_key: str | None = None,
    status: AppointmentStatus = AppointmentStatus.STANDING,
) -> Appointment:
    """Build an unsaved `Appointment` with a fresh id and, unless given, a fresh key.

    `status` defaults to standing, matching the column's own default: a test that does
    not mention status is a test about an appointment that counts.
    """
    return Appointment(
        id=new_id(),
        session_id=session_id,
        patient_id=patient_id,
        practitioner_id=practitioner_id,
        starts_at=starts_at,
        ends_at=ends_at,
        idempotency_key=idempotency_key if idempotency_key is not None else new_id(),
        status=status,
    )


async def seed_practitioner(
    session: AsyncSession,
    session_id: str,
    *,
    full_name: str = "Dr A",
    specialty: Specialty = practitioner_repository.DEFAULT_SPECIALTY,
    duration_minutes: int = practitioner_repository.DEFAULT_DURATION_MINUTES,
    schedule: list[tuple[Weekday, time, time]] | None = None,
) -> Practitioner:
    """Persist a practitioner and its schedule, defaulting to Mon-Fri 09:00-17:00."""
    practitioner = make_practitioner(
        session_id,
        full_name=full_name,
        specialty=specialty,
        duration_minutes=duration_minutes,
    )
    session.add(practitioner)
    await session.commit()
    ranges = DEFAULT_SCHEDULE if schedule is None else schedule
    for weekday, start, end in ranges:
        session.add(make_working_range(practitioner.id, weekday, start, end))
    await session.commit()
    return practitioner


async def seed_patient(
    session: AsyncSession,
    session_id: str,
    *,
    full_name: str = "Ada",
    chat_id: str | None = None,
) -> Patient:
    """Persist a patient."""
    patient = make_patient(session_id, full_name=full_name, chat_id=chat_id)
    session.add(patient)
    await session.commit()
    return patient


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Yield an `AsyncSession` against the isolated test database."""
    from scheduler.db.session import session_factory

    async with session_factory() as session:
        yield session


# The production defaults themselves, not a copy of them: a fixture seeding a schedule
# the repository no longer creates would leave every slot-grid assertion below passing
# against a configuration that does not exist.
DEFAULT_SCHEDULE = list(practitioner_repository.DEFAULT_SCHEDULE)
DEFAULT_SPECIALTY = practitioner_repository.DEFAULT_SPECIALTY
DEFAULT_DURATION_MINUTES = practitioner_repository.DEFAULT_DURATION_MINUTES


@asynccontextmanager
async def admin_api(session_id: str | None = None) -> AsyncIterator[HttpxAsyncClient]:
    """Yield an HTTP client against the admin app, on the test's own event loop.

    `TestClient` runs request handling on a loop of its own, which collides with the
    shared async engine an async test has already bound. Driving the app through
    `ASGITransport` keeps both on one loop.
    """
    from scheduler.main import app

    transport = ASGITransport(app=app)
    async with HttpxAsyncClient(transport=transport, base_url="http://t") as client:
        if session_id is not None:
            client.headers["X-Session-Id"] = session_id
        yield client
