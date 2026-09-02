"""Fixtures that cross the chat/scheduler boundary.

These tests run chat's real gRPC client against a real scheduling servicer backed by a
real `visitdoc_scheduler_test` database - the contract the chat unit tier's fakes stand
in for. No service belongs to this tier, so its fixtures live here rather than in either
package's own `conftest.py`.
"""

import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Self
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from chat.core.config import Settings as ChatSettings
from chat.domain.schemas import IntentClassificationResult, IntentLabel
from scheduler.core.config import Settings as SchedulerSettings
from scheduler.repositories import practitioner_repository
from shared_db import isolated_database_url, with_test_suffix
from sqlalchemy import text as sql_text
from ulid import ULID

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCHEDULER_ROOT = _REPO_ROOT / "services" / "scheduler"
_CHAT_ROOT = _REPO_ROOT / "services" / "chat"
# Must run before any `scheduler.*` module reads SCHEDULER_DATABASE_URL, exactly as the
# scheduler's own unit conftest does.
os.environ["SCHEDULER_DATABASE_URL"] = isolated_database_url(
    SchedulerSettings().SCHEDULER_DATABASE_URL
)
# The same, for the tests here that drive the chat service's own stores. The isolation
# scheme lives once in `shared_db.testing`, so this tier and chat's own unit tier point
# at the same isolated database rather than each declaring their own rule.
_chat_settings = ChatSettings()
os.environ["DATABASE_URL"] = isolated_database_url(_chat_settings.DATABASE_URL)
os.environ["QDRANT_COLLECTION_NAME"] = with_test_suffix(
    _chat_settings.QDRANT_COLLECTION_NAME
)


@pytest.fixture(scope="session", autouse=True)
def _apply_scheduler_migrations() -> None:
    """Bring the isolated scheduler test database's schema to head."""
    alembic_cfg = Config(str(_SCHEDULER_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(_SCHEDULER_ROOT / "alembic"))
    command.upgrade(alembic_cfg, "head")


@pytest.fixture(scope="session", autouse=True)
def _apply_chat_migrations() -> None:
    """Bring the isolated chat test database's schema to head."""
    alembic_cfg = Config(str(_CHAT_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(_CHAT_ROOT / "alembic"))
    command.upgrade(alembic_cfg, "head")


@pytest_asyncio.fixture(autouse=True)
async def _clear_chat_stores() -> None:
    """Truncate the chat service's tables and empty its collection before each test.

    Same reasoning as the scheduling half below, and the same source for the table
    list: read off the schema, so a table added later is cleaned up without anyone
    remembering to add it here.
    """
    from chat.domain.models import all_table_names
    from chat.repositories.qdrant_repository import COLLECTION_NAME, create_client
    from qdrant_client.http.models import Filter
    from shared_db import create_engine

    engine = create_engine(ChatSettings().DATABASE_URL)
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

    qdrant_client = create_client(ChatSettings())
    try:
        await qdrant_client.delete(
            collection_name=COLLECTION_NAME, points_selector=Filter()
        )
    finally:
        await qdrant_client.close()


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


# --- the two paid boundaries, faked for this tier ------------------------------------
#
# The chat unit tier has its own, richer versions of these. They are not imported from
# there: a package's `conftest.py` is that package's own, and reaching across tiers for
# one would make either tier's harness a dependency of the other's. What is duplicated
# is a test double, not a production declaration - and each stays self-consistent,
# which is all a fake embedding has to be.

# A turn's clock, required on every `POST /chat`. Fixed so a test that does not care
# about time is unaffected by when it runs.
LOCAL_NOW = "2026-08-14T09:00:00"
_VECTOR_SIZE = 512


async def fake_embed_texts(
    _client: object,
    texts: list[str],
    input_type: str = "document",
) -> list[list[float]]:
    """Deterministic stand-in for Voyage embeddings, with no key and no network.

    Text mentioning "visit" or "hours" embeds near one axis and everything else near
    another - enough to exercise the real groundedness threshold against a real Qdrant.
    """

    def vector(text: str) -> list[float]:
        keywords = ("visit", "hours")
        base = [1.0, 0.0] if any(k in text.lower() for k in keywords) else [0.0, 1.0]
        return base + [0.0] * (_VECTOR_SIZE - len(base))

    return [vector(text) for text in texts]


class _FakeTextEvent:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeStream:
    """Stand-in for `AsyncAnthropic().messages.stream(...)`'s context manager."""

    def __init__(self, tokens: list[str]) -> None:
        self._tokens = tokens

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    def __aiter__(self) -> AsyncIterator[_FakeTextEvent]:
        return self._generate()

    async def _generate(self) -> AsyncIterator[_FakeTextEvent]:
        for token in self._tokens:
            yield _FakeTextEvent(token)


def _text_response(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.content = [block]
    return response


def fake_anthropic_client(tokens: list[str] | None = None) -> MagicMock:
    """Stand-in for `AsyncAnthropic`, patched over `chat.main.AsyncAnthropic`.

    Classification always answers `faq_question`, which is what these tests exercise;
    the booking loop is answered with a plain reply so a mixed turn cannot hang.
    """
    client = MagicMock()
    client.close = AsyncMock()
    client.messages.stream.return_value = _FakeStream(tokens or [])

    async def _create(*_args: object, **kwargs: object) -> MagicMock:
        if kwargs.get("tools") is not None:
            return _text_response("Which practitioner would you like to see?")
        return _text_response(
            IntentClassificationResult(
                intents=[IntentLabel.FAQ_QUESTION]
            ).model_dump_json()
        )

    client.messages.create = AsyncMock(side_effect=_create)
    return client
