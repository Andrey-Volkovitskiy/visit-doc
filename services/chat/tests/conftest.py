"""Shared pytest fixtures for chat's unit tests."""

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal, Self
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlsplit, urlunsplit

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from chat.core.config import Settings
from sqlalchemy import text as sql_text
from voyageai.client_async import AsyncClient

_CHAT_ROOT = Path(__file__).resolve().parents[1]
_TEST_SUFFIX = "_test"
_VECTOR_SIZE = 512


def _with_test_suffix(value: str) -> str:
    """Append `_test` to `value`, unless it's already suffixed (idempotent)."""
    return value if value.endswith(_TEST_SUFFIX) else value + _TEST_SUFFIX


def _isolated_test_database_url(url: str) -> str:
    """Return `url` pointed at a `<db>_test` database instead of the dev one."""
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=_with_test_suffix(parts.path)))


# Must run before any other `chat.*` module reads DATABASE_URL/QDRANT_COLLECTION_NAME
# (env vars beat `.env`, so this override reaches every later Settings()/get_settings()
# call). Uses Settings() directly, not get_settings(): it needs the pre-override value,
# and caching it here would freeze the singleton on the dev URL for the whole session.
_base_settings = Settings()
os.environ["DATABASE_URL"] = _isolated_test_database_url(_base_settings.DATABASE_URL)
os.environ["QDRANT_COLLECTION_NAME"] = _with_test_suffix(
    _base_settings.QDRANT_COLLECTION_NAME
)


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations_to_test_database() -> None:
    """Bring the isolated test database's schema to head before any test runs."""
    alembic_cfg = Config(str(_CHAT_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(_CHAT_ROOT / "alembic"))
    command.upgrade(alembic_cfg, "head")


@pytest_asyncio.fixture(autouse=True)
async def _clear_chat_tables() -> None:
    """Truncate `sessions`/`chats`/`messages` before each test.

    Unlike `seeded_entry` below (which deletes its own `FaqEntry` row in teardown),
    nothing else in this suite cleans up `Session`/`Chat`/`Message` rows -
    `chat_repository`'s writes are real commits against the isolated test database
    (docs/testing-strategy.md), so without this every row from every test run stays
    forever, growing the table indefinitely instead of each test starting from an
    empty one. Truncating before rather than after a test also means a run starts
    clean regardless of what an earlier, possibly-crashed run left behind.

    Uses its own throwaway engine rather than `chat.db.session.engine` - that shared
    singleton's pool is deliberately bound/disposed per test by
    `_reset_engine_pool_between_tests` below to track whichever loop a sync test's own
    `TestClient` spins up; touching it here, before the test body runs, would rebind
    it to this fixture's own loop first and break that. Imported lazily, not at module
    level: `chat.db.session` builds its own module-level engine from `get_settings()`
    (cached) as soon as it's imported, so importing it at module level here would
    trigger that *before* this file's own DATABASE_URL override below runs, freezing
    the cached settings on the dev database for the whole session (same hazard the
    override comment below already warns about).
    """
    from chat.db.session import create_engine

    engine = create_engine(Settings())
    try:
        async with engine.begin() as connection:
            await connection.execute(
                sql_text(
                    "TRUNCATE TABLE sessions, chats, messages RESTART IDENTITY CASCADE"
                )
            )
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _reset_engine_pool_between_tests() -> AsyncIterator[None]:
    """Dispose the shared async engine's connection pool after each test.

    `chat.db.session.engine` is a module-level singleton; its asyncpg connections bind
    to whichever event loop first uses them. Each `TestClient(app)` instantiation can
    run on its own loop, so without this, a later test reusing the same pool on a
    different loop fails with "attached to a different loop" / "another operation is
    in progress". Disposing after every test forces the next one to reconnect fresh.
    """
    yield
    from chat.db.session import engine

    await engine.dispose()


async def fake_embed_texts(
    client: AsyncClient,
    texts: list[str],
    input_type: Literal["document", "query"] = "document",
) -> list[list[float]]:
    """Deterministic stand-in for Voyage AI embeddings, for tests with no live API key.

    Text mentioning "visit"/"hours" embeds near one axis, everything else near another —
    enough to exercise the real groundedness threshold against a real local Qdrant,
    without network access or credentials. `client` is accepted (and ignored) only to
    match `embed_texts`'s real signature, now that it takes the shared Voyage client as
    a parameter instead of constructing one internally.
    """

    def vector(text: str) -> list[float]:
        keywords = ("visit", "hours")
        base = [1.0, 0.0] if any(k in text.lower() for k in keywords) else [0.0, 1.0]
        return base + [0.0] * (_VECTOR_SIZE - len(base))

    return [vector(text) for text in texts]


class FakeTextEvent:
    """Stand-in for `anthropic`'s streaming `TextEvent` (`type == "text"`, `.text`)."""

    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class FakeAnthropicStream:
    """Stand-in for `AsyncAnthropic().messages.stream(...)`'s context manager."""

    def __init__(self, tokens: list[str]) -> None:
        self._tokens = tokens

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    def __aiter__(self) -> AsyncIterator[FakeTextEvent]:
        return self._generate()

    async def _generate(self) -> AsyncIterator[FakeTextEvent]:
        for token in self._tokens:
            yield FakeTextEvent(token)


class GatedAnthropicStream:
    """Like `FakeAnthropicStream`, but waits on `gate` before yielding its first token.

    Lets a test deterministically control when a stream "starts producing output",
    to test cancel-and-restart (FR-015) without relying on wall-clock timing
    (research.md #9).
    """

    def __init__(
        self,
        tokens: list[str],
        gate: asyncio.Event,
        *,
        started: asyncio.Event | None = None,
    ) -> None:
        self._tokens = tokens
        self._gate = gate
        self._started = started

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    def __aiter__(self) -> AsyncIterator[FakeTextEvent]:
        return self._generate()

    async def _generate(self) -> AsyncIterator[FakeTextEvent]:
        if self._started is not None:
            self._started.set()
        await self._gate.wait()
        for token in self._tokens:
            yield FakeTextEvent(token)


def fake_anthropic_client_gated(
    tokens: list[str], gate: asyncio.Event, *, started: asyncio.Event | None = None
) -> MagicMock:
    """Like `fake_anthropic_client`, but its stream blocks on `gate` first (FR-015).

    `started` (if given) is set right before the stream starts waiting on `gate`, so
    a test can deterministically know "generation has begun" without a wall-clock
    `sleep` (research.md #9).
    """
    client = MagicMock()
    client.close = AsyncMock()
    client.messages.stream.return_value = GatedAnthropicStream(
        tokens, gate, started=started
    )
    return client


def fake_anthropic_client(
    tokens: list[str] | None = None, *, stream_error: Exception | None = None
) -> MagicMock:
    """Stand-in for `AsyncAnthropic`, set on `chat.main.AsyncAnthropic`'s patched
    return value in tests, now that the real client is constructed once at app startup
    (main.py's lifespan) rather than inline in `answer_faq`. Exposes
    `.messages.stream(...)` returning a `FakeAnthropicStream` of `tokens` (or raising
    `stream_error` if given), and a no-op async `close()` so app shutdown can await it
    like the real client.
    """
    client = MagicMock()
    client.close = AsyncMock()
    if stream_error is not None:
        client.messages.stream.side_effect = stream_error
    else:
        client.messages.stream.return_value = FakeAnthropicStream(tokens or [])
    return client
