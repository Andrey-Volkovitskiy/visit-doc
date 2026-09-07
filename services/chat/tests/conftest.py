"""Shared pytest fixtures for chat's unit tests."""

import asyncio
import os
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Literal, NoReturn, Self
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from chat.agent.compose_answer import _SYSTEM_PROMPT as COMPOSE_SYSTEM_PROMPT
from chat.api.session_cookie import COOKIE_NAME
from chat.core.config import Settings
from chat.domain.schemas import IntentClassificationResult, IntentLabel
from fastapi.testclient import TestClient
from httpx import AsyncClient as HttpxAsyncClient
from httpx import Response
from qdrant_client import AsyncQdrantClient
from shared_db import ensure_database_exists, isolated_database_url, isolated_name
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine
from voyageai.client_async import AsyncClient

_CHAT_ROOT = Path(__file__).resolve().parents[1]


def _vector_size() -> int:
    """Return production's embedding dimension, read lazily.

    Derived from production rather than restated: a hand-typed copy goes on building
    vectors of the old width after the embedding model moves, and every Qdrant write
    then fails on a dimension mismatch.

    Imported inside the function, not at module scope: `qdrant_repository` reads
    `get_settings()` at import time, so touching it before the `_test`-suffix overrides
    below would freeze this suite on the *dev* collection.
    """
    from chat.repositories.qdrant_repository import VECTOR_SIZE

    return VECTOR_SIZE


# What the mocked booking loop says when a test hasn't asked for anything specific.
# The loop ends on the first response carrying no tool_use block, so this is a
# one-iteration turn with no tool calls.
DEFAULT_BOOKING_REPLY = "Which practitioner would you like to see?"


def _mock_tool_use_response(calls: list[tuple[str, dict[str, object]]]) -> MagicMock:
    """Build a mocked response whose content is `tool_use` blocks, one per call."""
    blocks = []
    for index, (name, arguments) in enumerate(calls):
        block = MagicMock()
        block.type = "tool_use"
        block.id = f"toolu_{index}"
        block.name = name
        block.input = arguments
        blocks.append(block)
    response = MagicMock()
    response.content = blocks
    return response


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


# Must run before any other `chat.*` module reads DATABASE_URL/QDRANT_COLLECTION_NAME
# (env vars beat `.env`, so this override reaches every later Settings()/get_settings()
# call). Uses Settings() directly, not get_settings(): it needs the pre-override value,
# and caching it here would freeze the singleton on the dev URL for the whole session.
_base_settings = Settings()
os.environ["DATABASE_URL"] = isolated_database_url(_base_settings.DATABASE_URL)
os.environ["QDRANT_COLLECTION_NAME"] = isolated_name(
    _base_settings.QDRANT_COLLECTION_NAME
)


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations_to_test_database() -> None:
    """Create this process's isolated database if needed, then bring it to head.

    The shared `visitdoc_chat_test` is provisioned before any suite runs, but a
    namespaced one - a pytest-xdist worker's, or a second concurrent run's - is named
    after a process that does not exist until now, so it asks for its own.
    """
    ensure_database_exists(os.environ["DATABASE_URL"])
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


@pytest_asyncio.fixture(scope="session")
async def _cleaning_engine() -> AsyncIterator[AsyncEngine]:
    """The engine `_clear_chat_tables` runs its per-test wipe on, built once.

    Built once rather than per test because connecting is what the wipe costs, not the
    statement: establishing and tearing down a connection to run one `DELETE` measures
    ~80ms, against ~20ms for the statement itself, and this suite pays it before every
    one of its 1200+ tests.

    Deliberately *not* `chat.db.session.engine`, for the reason `_clear_chat_tables`
    gives below: that singleton's pool is bound and disposed per test by
    `_reset_engine_pool_between_tests` to track whichever loop a sync test's own
    `TestClient` spins up, and touching it here would rebind it to this fixture's loop
    first. A dedicated engine has no such problem, and that is exactly what makes
    holding it open for the session safe: `asyncio_default_fixture_loop_scope` is
    `session`, so this fixture and the per-test one that uses it stay on one loop for
    the whole run, and nothing else ever borrows a connection from it.

    Imported inside the function, like everything else here that reaches `chat.*`:
    `shared_db.create_engine` is harmless at module scope, but `Settings()` must not be
    read until this file's `DATABASE_URL` override above has run.
    """
    from shared_db import create_engine

    engine = create_engine(Settings().DATABASE_URL)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def _cleaning_qdrant_client() -> AsyncIterator[AsyncQdrantClient]:
    """The Qdrant client `_clear_chat_tables` empties the collection through.

    Built once per session, not per test.

    Same reason as `_cleaning_engine` above, and a starker ratio: building the client
    and closing it again measures ~118ms of the ~128ms a per-test clear used to cost,
    for a delete that is ~10ms on a client already connected.

    `create_client` is imported lazily rather than at module scope because importing
    `chat.repositories.qdrant_repository` reads `get_settings()` for `COLLECTION_NAME`
    at import time - the hazard `_clear_chat_tables` documents below.
    """
    from chat.repositories.qdrant_repository import create_client

    client = create_client(Settings())
    try:
        yield client
    finally:
        await client.close()


@pytest_asyncio.fixture(autouse=True)
async def _clear_chat_tables(
    _cleaning_engine: AsyncEngine, _cleaning_qdrant_client: AsyncQdrantClient
) -> None:
    """Empty `sessions`/`chats`/`messages`/`faq_entries` and the isolated Qdrant
    collection before each test.

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
    since it happened to share `seeded_entry`'s own placeholder content. Clearing all
    four unconditionally before every test means a run starts clean regardless of what
    an earlier, possibly-crashed run left behind.

    `DELETE` rather than `TRUNCATE`, and both stores reached through a client this
    fixture borrows rather than builds. The tables hold a handful of rows at this
    point, so `TRUNCATE`'s table-rewrite and `ACCESS EXCLUSIVE` lock buy nothing and
    measure ~3x the four `DELETE`s; the connection setup a per-test engine and Qdrant
    client paid for measures more than either. What that costs is `RESTART IDENTITY`:
    `faq_entries.id` no longer restarts at 1 each test. Nothing depends on it - every
    test reads the id back from the response or row that created it - and a test that
    hardcoded one would be asserting on the order its neighbours ran in.

    The deletes go child-first, the reverse of `all_table_names()`, so each runs with
    its referencing rows already gone. It is the ordering, not `CASCADE`, that keeps
    the foreign keys satisfied - a delete that silently took rows from a table not
    named here would be exactly the kind of reach `TRUNCATE ... CASCADE` allowed.

    Neither client is the shared `chat.db.session.engine`/`qdrant_repository`
    singleton. `chat.db.session`: that shared engine's pool is deliberately
    bound/disposed per test by `_reset_engine_pool_between_tests` below to track
    whichever loop a sync test's own `TestClient` spins up; touching it here, before
    the test body runs, would rebind it to this fixture's own loop first and break
    that. Also imported lazily, not at module level: `chat.db.session` builds its own
    module-level engine from `get_settings()` (cached) as soon as it's imported, so
    importing it at module level here would trigger that *before* this file's own
    `DATABASE_URL` override above runs, freezing the cached settings on the dev
    database for the whole session (same hazard the override comment above already
    warns about). `qdrant_repository.COLLECTION_NAME` has the identical
    `get_settings()`-at-import hazard for `QDRANT_COLLECTION_NAME`, hence the same
    lazy-import treatment.
    """
    from chat.domain.models import all_table_names
    from chat.repositories.qdrant_repository import COLLECTION_NAME
    from qdrant_client.http.models import Filter

    async with _cleaning_engine.begin() as connection:
        for table in reversed(all_table_names()):
            await connection.execute(sql_text(f"DELETE FROM {table}"))

    # An empty `Filter()` (no conditions) matches every point - the collection
    # itself already exists, ensured once per session above.
    await _cleaning_qdrant_client.delete(
        collection_name=COLLECTION_NAME, points_selector=Filter()
    )


# The scheduling client functions `_scheduler_is_unreachable_by_default` replaces, named
# once so nothing has to restate them: the fixture below builds its fake from this
# tuple, and `test_scheduling_client.py` - which un-fakes the boundary because the
# client itself is what it tests - reads it back through the
# `faked_scheduling_function_names` fixture and asserts it names exactly the public
# coroutines `chat.clients.scheduling` defines. So a client function added without an
# entry here fails that test rather than reaching a real channel from some other test.
FAKED_SCHEDULING_FUNCTIONS = (
    "ensure_session_provisioned",
    "list_practitioners",
    "check_availability",
    "book_appointment",
    "reschedule_appointment",
    "cancel_appointment",
    "list_appointments",
    "delete_patient_for_chat",
    "delete_session",
)


@pytest.fixture
def faked_scheduling_function_names() -> tuple[str, ...]:
    """The client function names `_scheduler_is_unreachable_by_default` replaces."""
    return FAKED_SCHEDULING_FUNCTIONS


@pytest.fixture(autouse=True)
def _scheduler_is_unreachable_by_default() -> Iterator[None]:
    """Fake the scheduling boundary for every chat unit test that does not override it.

    No scheduler runs alongside this tier, and the app's gRPC channel is bound to the
    lifespan's event loop rather than the test's - so an unfaked call would fail on the
    loop rather than on the thing a test is actually about. Unreachable is also the
    honest default: it is what a chat service with no scheduler running really sees.

    Every public coroutine the client module defines is faked, not just the provisioning
    one - `test_scheduling_client.py` asserts that tuple and that set are equal. All
    nine are reachable from an HTTP request today (three from `/chats`, `/admin` and the
    provisioning dependency, six from the booking tools a `POST /chat` turn can call),
    and a test exercising a path that reaches an unfaked one would dial the real channel
    - the lifespan's, with no scheduler behind it - and fail on that rather than on the
    thing it was actually about. A test that wants a specific outcome patches over this.

    The fake is applied to `chat.clients.scheduling` itself, which is the object every
    caller reaches these functions through (`from chat.clients import scheduling`, then
    `scheduling.<name>(...)`) - so one patch per name covers every importer.
    """
    from chat.clients import scheduling
    from chat.clients.scheduling import SchedulingUnavailableError

    def _unreachable() -> AsyncMock:
        return AsyncMock(
            side_effect=SchedulingUnavailableError(
                "no scheduler in tests", outcome_unknown=False
            )
        )

    with patch.multiple(
        scheduling, **{name: _unreachable() for name in FAKED_SCHEDULING_FUNCTIONS}
    ):
        yield


@pytest.fixture(autouse=True)
def _new_sessions_start_empty(request: pytest.FixtureRequest) -> Iterator[None]:
    """Keep the starter corpus out of every test that has not asked for it.

    `POST /chats` plants `DEFAULT_FAQ_ENTRIES` in a session it creates, so without this
    every test that mints a session through it would answer from nine entries it never
    mentioned - and a test written to check an abstention against an empty corpus would
    quietly become a grounded one. The corpus a test retrieves against has to be the
    corpus that test built.

    A test that is about the seeding itself carries `@pytest.mark.seeds_default_corpus`
    and gets the real thing, embeddings included - which is why the fake here replaces
    the seeding step rather than the embedding call underneath it: the marked tests
    still need `chat.rag.indexing.embed_texts` free to fake as they please.
    """
    if "seeds_default_corpus" in request.keywords:
        yield
        return
    with patch("chat.api.chats.seed_default_corpus", new=AsyncMock()):
        yield


class PaidAPICallInTestError(BaseException):
    """Raised when a test reaches a real paid API instead of that API's fake.

    Deliberately not an `Exception`. Production code is entitled to absorb failures of
    a degradable dependency - `rag/reranking.py` converts every `Exception` to `None`
    by requirement - and a guard an `except Exception` can swallow stops being a guard
    exactly where the omission is hardest to notice: the test still passes, having
    quietly taken the degraded path. Inheriting from `BaseException` puts it outside
    every such handler, so reaching a paid call fails the test wherever it happens.
    """


# Every call this codebase makes that costs money, keyed by the SDK attribute it goes
# through. Blocked on the SDK class rather than on this codebase's own wrappers, so a
# client built anywhere - including one the app's own lifespan constructs in a test
# that forgot to patch it - is covered by the same guard.
_PAID_API_CALLS = {
    "anthropic.resources.messages.AsyncMessages.create": (
        "Anthropic messages.create (intent classification, or the booking tool loop)"
    ),
    "anthropic.resources.messages.AsyncMessages.stream": (
        "Anthropic messages.stream (answer generation)"
    ),
    "voyageai.client_async.AsyncClient.embed": "Voyage embed (embeddings)",
    "voyageai.client_async.AsyncClient.rerank": "Voyage rerank (reranking)",
}

_PAID_API_REMEDY = (
    "Tests must never call a paid API: it bills real money on every run, and both its "
    "latency and its output vary, so the test is non-deterministic and needs network "
    "plus a valid key to pass at all. Patch `chat.main.AsyncAnthropic` with "
    "`fake_anthropic_client(...)` and `chat.rag.<module>.embed_texts` with "
    "`fake_embed_texts` - see docs/testing-strategy.md."
)


@pytest.fixture(autouse=True)
def _paid_apis_are_blocked() -> Iterator[None]:
    """Fail any test that reaches Anthropic or Voyage for real.

    A test only caring about an earlier pipeline stage still runs the later ones, which
    call these APIs unconditionally - so an unmocked call is not "that path is
    untested", it is a live, billed, non-deterministic call attributed to whatever the
    test was actually about. This turns that into an immediate, named failure at the
    call site instead.

    A test that legitimately needs a real call - there is none in this tier - would
    have to opt out by patching over this fixture, which is the point: it cannot happen
    by omission.

    A new paid call (a new SDK method, or a new provider) belongs in `_PAID_API_CALLS`
    in the same change that introduces it, exactly as a new scheduling call belongs in
    `_scheduler_is_unreachable_by_default`.
    """

    def _blocked(api: str) -> Callable[..., NoReturn]:
        def raise_paid_api_error(*_args: Any, **_kwargs: Any) -> NoReturn:
            raise PaidAPICallInTestError(f"this test called {api}. {_PAID_API_REMEDY}")

        return raise_paid_api_error

    with ExitStack() as stack:
        for target, api in _PAID_API_CALLS.items():
            stack.enter_context(patch(target, new=_blocked(api)))
        yield


@pytest.fixture(autouse=True)
def _reranking_keeps_what_it_is_given() -> Iterator[None]:
    """Fake the reranking boundary for every test, scoring each chunk above the floor.

    Required, not convenient. `rerank_chunks` converts *every* `Exception` to None so a
    reranker outage costs the answer its precision stage rather than the turn, which is
    why `PaidAPICallInTestError` is not one - the guard would otherwise be swallowed and
    a test reaching reranking would quietly answer `answered_unreranked` instead of
    failing. The guard makes the omission loud; this fake is what makes an ordinary FAQ
    test not need one.

    The default is a *working* reranker that keeps the shortlist in the order it was
    given, so an ordinary FAQ test still sees `answered`. A test about degradation
    patches over this with None; a test about ordering patches its own scores in.
    """
    from chat.rag.pipeline import ScoredChunk

    async def keep_all(
        _client: object,
        _query: str,
        chunks: list[ScoredChunk],
        **_kwargs: object,
    ) -> list[ScoredChunk]:
        return [chunk.with_rerank_score(0.9) for chunk in chunks]

    with patch("chat.agent.answer_faq.rerank_chunks", new=keep_all):
        yield


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
        return base + [0.0] * (_vector_size() - len(base))

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
    compose_error: Exception | None = None,
    intents: list[IntentLabel] | None = None,
    classify_error: Exception | None = None,
    classify_gate: asyncio.Event | None = None,
    classify_started: asyncio.Event | None = None,
    booking_reply: str = DEFAULT_BOOKING_REPLY,
    booking_error: Exception | None = None,
    booking_tool_calls: list[list[tuple[str, dict[str, object]]]] | None = None,
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

    `stream_error` fails every streaming call; `compose_error` fails only the composing
    step's. A mixed-intent turn streams twice on this one client - the FAQ specialist
    first, then the merge - so failing both would never reach the second, and the
    merge's own failure handling would be untestable.
    """
    client = MagicMock()
    client.close = AsyncMock()

    if compose_error is not None:

        def _stream(*_args: object, **kwargs: object) -> FakeAnthropicStream:
            # `.messages.stream` serves two callers: the FAQ specialist and the
            # composing step, told apart by the system prompt each sends - the same
            # trick `_create` below uses for classification and the booking loop.
            if kwargs.get("system") == COMPOSE_SYSTEM_PROMPT:
                raise compose_error
            return FakeAnthropicStream(tokens or [])

        client.messages.stream.side_effect = _stream
    elif stream_error is not None:
        client.messages.stream.side_effect = stream_error
    else:
        client.messages.stream.return_value = FakeAnthropicStream(tokens or [])
    fake_classify_intent_client(
        intents if intents is not None else [IntentLabel.FAQ_QUESTION],
        call_error=classify_error,
        gate=classify_gate,
        started=classify_started,
        client=client,
        booking_reply=booking_reply,
        booking_error=booking_error,
        booking_tool_calls=booking_tool_calls,
    )
    return client


def fake_classify_intent_client(
    intents: list[IntentLabel] | None = None,
    *,
    call_error: Exception | None = None,
    gate: asyncio.Event | None = None,
    started: asyncio.Event | None = None,
    client: MagicMock | None = None,
    booking_reply: str = DEFAULT_BOOKING_REPLY,
    booking_error: Exception | None = None,
    booking_tool_calls: list[list[tuple[str, dict[str, object]]]] | None = None,
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
    `booking_error` (if given) is raised by the booking loop's call only, leaving
    classification working - a turn cannot reach the loop without being classified
    into it first.
    """
    if client is None:
        client = MagicMock()
        client.close = AsyncMock()

    # One entry per booking-loop iteration that should issue tool calls; the loop then
    # falls through to the plain-text reply above and stops.
    booking_responses = [
        _mock_tool_use_response(calls) for calls in (booking_tool_calls or [])
    ]

    async def _create(*_args: object, **kwargs: object) -> MagicMock:
        # `.messages.create` serves two callers: `classify_intent` (structured output)
        # and the booking loop (tool use). They are told apart by the parameter each
        # sends, so one mocked client can stand in for both on a mixed-intent turn.
        if kwargs.get("tools") is not None:
            if booking_error is not None:
                raise booking_error
            if booking_responses:
                return booking_responses.pop(0)
            return _mock_text_response(booking_reply)
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
    intents_sequence: list[list[IntentLabel]],
    tokens: list[str] | None = None,
    booking_reply: str = DEFAULT_BOOKING_REPLY,
) -> MagicMock:
    """Like `fake_anthropic_client`, but returns a different classification result on
    each successive classification call, in `intents_sequence` order - lets one shared
    mocked client (matching one real app lifespan/session, like `TestClient`'s)
    simulate several distinct patient messages each getting their own recorded intent.

    A booking-loop call is answered with `booking_reply` and does *not* consume an entry
    from the sequence: the two callers share `.messages.create` but are told apart by
    the parameter each sends, so a booking turn must not shift the classifications the
    later turns are supposed to get.
    """
    client = fake_anthropic_client(tokens)
    remaining = [
        _mock_text_response(
            IntentClassificationResult(intents=intents).model_dump_json()
        )
        for intents in intents_sequence
    ]

    async def _create(*_args: object, **kwargs: object) -> MagicMock:
        if kwargs.get("tools") is not None:
            return _mock_text_response(booking_reply)
        return remaining.pop(0)

    client.messages.create = AsyncMock(side_effect=_create)
    return client


# A turn's clock, required on every `POST /chat`. Fixed so a test that does not care
# about time is unaffected by when it runs.
LOCAL_NOW = "2026-08-14T09:00:00"
_CHAT_ID_ATTR = "_visitdoc_chat_id"
_chat_id_lock = asyncio.Lock()


# The session a seeding fixture built its corpus for. FAQ entries belong to exactly one
# session, so a client that minted its own would retrieve nothing and every grounded
# test would silently become an abstention test. Set by `seed_faq_entry`, cleared after
# each test by `_reset_seeded_session`.
_seeded_session_id: str | None = None


def set_seeded_session(session_id: str | None) -> None:
    """Record the session a seeded corpus belongs to, for `chat_id_for` to adopt."""
    global _seeded_session_id
    _seeded_session_id = session_id


@pytest.fixture(autouse=True)
def _reset_seeded_session() -> Iterator[None]:
    yield
    set_seeded_session(None)


def seeded_session_id() -> str:
    """Return the session a seeding fixture built its corpus for.

    Raises: AssertionError if no seeding fixture is active - a test asking for this
        without one would otherwise retrieve against an empty corpus and quietly
        become an abstention test.
    """
    assert _seeded_session_id is not None, "no seeded corpus in this test"
    return _seeded_session_id


def adopt_seeded_session(client: TestClient | HttpxAsyncClient) -> None:
    """Point `client` at the session a seeding fixture built its corpus for.

    Only needed by a test that creates its chats directly rather than through
    `chat_id_for`/`turn`, which adopt it on the client's behalf.
    """
    if _seeded_session_id is not None and COOKIE_NAME not in client.cookies:
        client.cookies.set(COOKIE_NAME, _seeded_session_id)


def chat_id_for(client: TestClient) -> str:
    """Return the client's chat, creating one on first use.

    A chat is an explicit resource created by `POST /chats`, so every turn needs one to
    address. Caching it on the client keeps a multi-turn test talking to the same chat.

    If a seeding fixture has built a corpus for a particular session, the client adopts
    that session's cookie before creating its chat - an unrecognized cookie would mint
    a new session, whose corpus is empty, and the seeded entry would be unreachable.
    """
    chat_id = getattr(client, _CHAT_ID_ATTR, None)
    if chat_id is None:
        if _seeded_session_id is not None and COOKIE_NAME not in client.cookies:
            client.cookies.set(COOKIE_NAME, _seeded_session_id)
        chat_id = client.post("/chats").json()["id"]
        setattr(client, _CHAT_ID_ATTR, chat_id)
    return str(chat_id)


def turn(client: TestClient, message: str) -> Response:
    """Send one message to the client's chat."""
    return client.post(
        "/chat",
        json={
            "chat_id": chat_id_for(client),
            "message": message,
            "local_now": LOCAL_NOW,
        },
    )


async def async_chat_id_for(client: HttpxAsyncClient) -> str:
    """Return the async client's chat, creating one on first use.

    Locked because several tests launch concurrent turns: without it, two tasks could
    each read "no chat yet" across the `await` and create their own.
    """
    async with _chat_id_lock:
        chat_id = getattr(client, _CHAT_ID_ATTR, None)
        if chat_id is None:
            if _seeded_session_id is not None and COOKIE_NAME not in client.cookies:
                client.cookies.set(COOKIE_NAME, _seeded_session_id)
            chat_id = (await client.post("/chats")).json()["id"]
            setattr(client, _CHAT_ID_ATTR, chat_id)
        return str(chat_id)


async def async_turn(client: HttpxAsyncClient, message: str) -> Response:
    """Send one message to the async client's chat."""
    return await client.post(
        "/chat",
        json={
            "chat_id": await async_chat_id_for(client),
            "message": message,
            "local_now": LOCAL_NOW,
        },
    )
