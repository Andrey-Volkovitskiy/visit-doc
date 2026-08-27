"""Fixtures that cross the chat/scheduler boundary.

These tests run chat's real gRPC client against a real scheduling servicer backed by a
real `visitdoc_scheduler_test` database - the contract the chat unit tier's fakes stand
in for. No service belongs to this tier, so its fixtures live here rather than in either
package's own `conftest.py`.
"""

import os
from collections.abc import AsyncIterator
from pathlib import Path

import grpc
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from scheduler.core.config import Settings as SchedulerSettings
from scheduler.repositories import practitioner_repository
from shared_db import isolated_database_url
from sqlalchemy import text as sql_text
from ulid import ULID

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCHEDULER_ROOT = _REPO_ROOT / "services" / "scheduler"
# Must run before any `scheduler.*` module reads SCHEDULER_DATABASE_URL, exactly as the
# scheduler's own unit conftest does.
os.environ["SCHEDULER_DATABASE_URL"] = isolated_database_url(
    SchedulerSettings().SCHEDULER_DATABASE_URL
)


@pytest.fixture(scope="session", autouse=True)
def _apply_scheduler_migrations() -> None:
    """Bring the isolated scheduler test database's schema to head."""
    alembic_cfg = Config(str(_SCHEDULER_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(_SCHEDULER_ROOT / "alembic"))
    command.upgrade(alembic_cfg, "head")


@pytest_asyncio.fixture(autouse=True)
async def _clear_scheduling_tables() -> None:
    """Truncate every scheduling table before each test.

    The table list comes from the schema itself, as it does in the scheduler's own unit
    conftest - a hand-written list here would be a second one to remember, and the tier
    that forgot would leak rows between tests as a flake blamed on the code under test.
    """
    from scheduler.domain.models import all_table_names
    from shared_db import create_engine

    engine = create_engine(SchedulerSettings().SCHEDULER_DATABASE_URL)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                sql_text(
                    f"TRUNCATE TABLE {', '.join(all_table_names())} "
                    "RESTART IDENTITY CASCADE"
                )
            )
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _reset_engine_pool_between_tests() -> AsyncIterator[None]:
    """Dispose the scheduler engine's connection pool after each test."""
    yield
    from scheduler.db.session import engine

    await engine.dispose()


@pytest_asyncio.fixture
async def scheduling_channel() -> AsyncIterator[grpc.aio.Channel]:
    """Serve the real scheduling servicer and yield a channel to it.

    A loopback socket rather than an in-process shortcut, so chat's client exercises
    its real deadline, metadata, and status handling against the real server.
    """
    from scheduler.grpc.interceptors import LoggingInterceptor
    from scheduler.grpc.servicer import SchedulingServicer
    from shared_proto.scheduling.v1 import scheduling_pb2_grpc

    server = grpc.aio.server(interceptors=[LoggingInterceptor()])
    scheduling_pb2_grpc.add_SchedulingServicer_to_server(  # type: ignore[no-untyped-call]
        SchedulingServicer(), server
    )
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
        yield channel
    await server.stop(0)


def new_id() -> str:
    """Return a fresh ULID string, for a test-authored session/chat/entity id."""
    return str(ULID())


# The production default itself, not a copy: a fixture seeding a schedule the
# repository no longer creates would leave assertions passing against a configuration
# that does not exist.
DEFAULT_SCHEDULE = list(practitioner_repository.DEFAULT_SCHEDULE)
