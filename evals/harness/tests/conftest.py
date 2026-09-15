"""The chat test database, for the harness tests that read or write it.

What `services/chat/tests/conftest.py` does for the database and nothing else - that
conftest does not apply outside `services/chat/tests`. The fixtures here are not
autouse: the scorers and most of the driver are pure, and only a test that asks for
`chat_db` needs Postgres at all.
"""

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from chat.core.config import Settings
from shared_db import ensure_database_exists, isolated_database_url, isolated_name
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

_CHAT_ROOT = Path(__file__).resolve().parents[3] / "services" / "chat"

# Must run before any `chat.*` module reads its settings, for the reason the chat
# suite's conftest gives: env vars beat `.env`, and `get_settings()` caches its first
# reading. Both overrides are the chat suite's own, restated rather than half-copied -
# this suite shares a process with that one under `make test-unit`, and a harness test
# importing `chat.main` first must not freeze the cached settings on the dev collection.
_base_settings = Settings()
os.environ["DATABASE_URL"] = isolated_database_url(_base_settings.DATABASE_URL)
os.environ["QDRANT_COLLECTION_NAME"] = isolated_name(
    _base_settings.QDRANT_COLLECTION_NAME
)

# Child-first, so each delete runs with its referencing rows already gone.
_TABLES_A_TEST_WRITES = ("messages", "chats", "sessions")


@pytest.fixture(scope="session")
def _migrated_chat_database() -> str:
    """Create this process's isolated chat database if needed, then bring it to head.

    Returns: the database URL the harness tests connect to
    """
    url = os.environ["DATABASE_URL"]
    ensure_database_exists(url)
    alembic_cfg = Config(str(_CHAT_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(_CHAT_ROOT / "alembic"))
    command.upgrade(alembic_cfg, "head")
    return url


@pytest_asyncio.fixture(scope="session")
async def _chat_engine(_migrated_chat_database: str) -> AsyncIterator[AsyncEngine]:
    """One engine on the chat test database for the whole session.

    Deliberately not `chat.db.session.engine`, whose pool binds to whichever loop first
    uses it - a test serving a request through the chat app's `TestClient` does that.
    """
    from shared_db import create_engine

    engine = create_engine(_migrated_chat_database)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def chat_db(
    _chat_engine: AsyncEngine,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A session factory on the chat test database, emptied of chats before the test.

    Cleared before rather than after, so a run interrupted mid-test cannot leave rows
    for the next one to trip over.
    """
    from shared_db import create_session_factory

    async with _chat_engine.begin() as connection:
        for table in _TABLES_A_TEST_WRITES:
            await connection.execute(sql_text(f"DELETE FROM {table}"))
    yield create_session_factory(_chat_engine)
