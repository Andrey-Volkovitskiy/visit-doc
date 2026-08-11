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
from chat.domain.schemas import IntentClassificationResult, IntentLabel
from sqlalchemy import text as sql_text
from voyageai.client_async import AsyncClient

_CHAT_ROOT = Path(__file__).resolve().parents[1]
_TEST_SUFFIX = "_test"
_VECTOR_SIZE = 512


def _with_test_suffix(value: str) -> str:
    """Append `_test` to `value`, unless it's already suffixed (idempotent)."""
    return value if value.endswith(_TEST_SUFFIX) else value + _TEST_SUFFIX


def _mock_text_response(text: str) -> MagicMock:
    """Build a mocked Anthropic `.messages.create(...)` response whose sole content
    block is a `text`-type block carrying `text` - the shape both
    `fake_classify_intent_client`'s and `fake_anthropic_client_sequence`'s mocked
    classification responses share.
    """
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text
    response = MagicMock()
    response.content = [text_block]
    return response


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


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _ensure_qdrant_collection_exists() -> None:
    """Create the isolated Qdrant collection once per session, if missing.

    Split out from `_clear_chat_tables` below so the common per-test path only pays
    for a point-level clear, not a `collection_exists` round trip on every one of
    this suite's 170+ tests - the collection's mere *existence* doesn't change test
    to test, only its contents do.
    """
    from chat.repositories.qdrant_repository import create_client, ensure_collection

    qdrant_client = create_client(Settings())
    try:
        await ensure_collection(qdrant_client)
    finally:
        await qdrant_client.close()


@pytest_asyncio.fixture(autouse=True)
async def _clear_chat_tables() -> None:
    """Truncate `sessions`/`chats`/`messages`/`faq_entries` and empty the isolated
    Qdrant collection before each test.

    Nothing else in this suite reliably cleans up `Session`/`Chat`/`Message` rows -
    `chat_repository`'s writes are real commits against the isolated test database
    (docs/testing-strategy.md). `FaqEntry`/its Qdrant chunks are *supposed* to be
    self-cleaned by whichever fixture creates them (`seeded_entry` below deletes its
    row and deindexes its points in teardown) - but that only runs on a clean exit.
    An interrupted run (a killed `pytest` process, a crash, Ctrl-C) skips the
    teardown entirely and leaves both a stray `FaqEntry` row and a stray Qdrant point
    behind - a real incident: one survived a killed process and both broke
    `test_test_isolation.py`'s own "empty at test start" invariant on a later run and
    fed an unrelated test's groundedness check a false-positive retrieval match,
    since it happened to share `seeded_entry`'s own placeholder content. Clearing
    all four unconditionally before every test - one combined `TRUNCATE`, sharing one
    throwaway connection, rather than a separate one per table - means a run starts
    clean regardless of what an earlier, possibly-crashed run left behind, without
    paying for a second engine/connection round trip on top of the first.

    Both clients are built lazily and thrown away, not the shared
    `chat.db.session.engine`/`chat.repositories.qdrant_repository` singletons.
    `chat.db.session`: that shared engine's pool is deliberately bound/disposed per
    test by `_reset_engine_pool_between_tests` below to track whichever loop a sync
    test's own `TestClient` spins up; touching it here, before the test body runs,
    would rebind it to this fixture's own loop first and break that. Also imported
    lazily, not at module level: `chat.db.session` builds its own module-level engine
    from `get_settings()` (cached) as soon as it's imported, so importing it at
    module level here would trigger that *before* this file's own `DATABASE_URL`
    override above runs, freezing the cached settings on the dev database for the
    whole session (same hazard the override comment above already warns about).
    `qdrant_repository.COLLECTION_NAME` has the identical `get_settings()`-at-import
    hazard for `QDRANT_COLLECTION_NAME`, hence the same lazy-import treatment.
    """
    from chat.db.session import create_engine
    from chat.repositories.qdrant_repository import COLLECTION_NAME, create_client
    from qdrant_client.http.models import Filter

    engine = create_engine(Settings())
    try:
        async with engine.begin() as connection:
            await connection.execute(
                sql_text(
                    "TRUNCATE TABLE sessions, chats, messages, faq_entries "
                    "RESTART IDENTITY CASCADE"
                )
            )
    finally:
        await engine.dispose()

    qdrant_client = create_client(Settings())
    try:
        # An empty `Filter()` (no conditions) matches every point - the collection
        # itself already exists, ensured once per session above.
        await qdrant_client.delete(
            collection_name=COLLECTION_NAME, points_selector=Filter()
        )
    finally:
        await qdrant_client.close()


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
    `sleep` (research.md #9). Classification (`.messages.create`) still defaults to a
    confident `faq_question` result, ungated - only the generation stream is gated.
    """
    client = MagicMock()
    client.close = AsyncMock()
    client.messages.stream.return_value = GatedAnthropicStream(
        tokens, gate, started=started
    )
    fake_classify_intent_client([IntentLabel.FAQ_QUESTION], client=client)
    return client


def fake_anthropic_client(
    tokens: list[str] | None = None,
    *,
    stream_error: Exception | None = None,
    intents: list[IntentLabel] | None = None,
    classify_error: Exception | None = None,
    classify_gate: asyncio.Event | None = None,
    classify_started: asyncio.Event | None = None,
) -> MagicMock:
    """Stand-in for `AsyncAnthropic`, set on `chat.main.AsyncAnthropic`'s patched
    return value in tests, now that the real client is constructed once at app startup
    (main.py's lifespan) rather than inline in `answer_faq`. Exposes
    `.messages.stream(...)` returning a `FakeAnthropicStream` of `tokens` (or raising
    `stream_error` if given), and a no-op async `close()` so app shutdown can await it
    like the real client.

    Also exposes `.messages.create(...)` (`classify_intent()`'s call, research.md #3)
    since `graph.py`'s `classify_intent_node` runs on every turn before `answer_faq`,
    on this same shared client (research.md #1) - defaulting to a confident
    `faq_question` classification so a test only needs to pass `intents`/
    `classify_error`/`classify_gate`/`classify_started` when that's specifically what
    it's exercising; otherwise classification silently succeeding must never be
    mistaken for a real assertion on it (docs/testing-strategy.md's mocking-discipline
    note).
    """
    client = MagicMock()
    client.close = AsyncMock()
    if stream_error is not None:
        client.messages.stream.side_effect = stream_error
    else:
        client.messages.stream.return_value = FakeAnthropicStream(tokens or [])
    fake_classify_intent_client(
        intents if intents is not None else [IntentLabel.FAQ_QUESTION],
        call_error=classify_error,
        gate=classify_gate,
        started=classify_started,
        client=client,
    )
    return client


def fake_classify_intent_client(
    intents: list[IntentLabel] | None = None,
    *,
    call_error: Exception | None = None,
    gate: asyncio.Event | None = None,
    started: asyncio.Event | None = None,
    client: MagicMock | None = None,
) -> MagicMock:
    """Stand-in for `AsyncAnthropic` when only `.messages.create(...)` is exercised, via
    `classify_intent()`'s structured-output call (research.md #3). Exposes
    `.messages.create(...)` returning a mocked response whose sole content block's
    `.text` is `IntentClassificationResult(intents=intents).model_dump_json()`, or
    raising `call_error` if given. `intents=None` (with no `call_error`) simulates a
    response whose content doesn't validate against the schema (malformed text) -
    `classify_intent()` must raise for that case too, not just an outright API error.
    `gate` (if given) is awaited before the call resolves/raises, letting a test
    deterministically suspend the call mid-flight to exercise cancellation
    (research.md #2), the same role `fake_anthropic_client_gated` plays for streaming.
    `started` (if given) is set right before waiting on `gate`, so a test can
    deterministically know classification has begun without a wall-clock `sleep`.
    `client` (if given) is configured in place instead of building a fresh `MagicMock`
    - lets `fake_anthropic_client`/`fake_anthropic_client_gated` layer a default
    classification onto a client they've already set `.messages.stream`/`.close` on.
    """
    if client is None:
        client = MagicMock()
        client.close = AsyncMock()

    async def _create(*_args: object, **_kwargs: object) -> MagicMock:
        if gate is not None:
            if started is not None:
                started.set()
            await gate.wait()
        if call_error is not None:
            raise call_error
        text = (
            IntentClassificationResult(intents=intents).model_dump_json()
            if intents is not None
            else "not valid json"
        )
        return _mock_text_response(text)

    # Wrapped in AsyncMock (side_effect=_create) rather than assigned directly, so
    # `.call_args_list` stays available - lets a test assert on what context a call
    # actually received (e.g. FR-006's merged-burst window), not just its hardcoded
    # return value (docs/testing-strategy.md's mocking-discipline note).
    client.messages.create = AsyncMock(side_effect=_create)
    return client


def fake_anthropic_client_sequence(
    intents_sequence: list[list[IntentLabel]], tokens: list[str] | None = None
) -> MagicMock:
    """Like `fake_anthropic_client`, but returns a different classification result on
    each successive `.messages.create(...)` call, in `intents_sequence` order - lets
    one shared mocked client (matching one real app lifespan/session, like
    `TestClient`'s) simulate several distinct patient messages each getting their own
    recorded intent (US3's reviewability scenario).
    """
    client = fake_anthropic_client(tokens)

    def _response_for(intents: list[IntentLabel]) -> MagicMock:
        text = IntentClassificationResult(intents=intents).model_dump_json()
        return _mock_text_response(text)

    responses = [_response_for(intents) for intents in intents_sequence]
    client.messages.create = AsyncMock(side_effect=responses)
    return client
