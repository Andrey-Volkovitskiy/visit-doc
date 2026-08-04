"""Shared pytest fixtures for chat's unit tests."""

import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit, urlunsplit

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from chat.core.config import Settings

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


def fake_embed_texts(
    texts: list[str],
    settings: Settings,
    input_type: Literal["document", "query"] = "document",
) -> list[list[float]]:
    """Deterministic stand-in for Voyage AI embeddings, for tests with no live API key.

    Text mentioning "visit"/"hours" embeds near one axis, everything else near another —
    enough to exercise the real groundedness threshold against a real local Qdrant,
    without network access or credentials.
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
