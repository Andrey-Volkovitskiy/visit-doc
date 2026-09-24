"""Fixtures that cross the chat/scheduler boundary.

These tests run chat's real gRPC client against a real scheduling servicer backed by a
real `visitdoc_scheduler_test` database - the contract the chat unit tier's fakes stand
in for. No service belongs to this tier, so its fixtures live here rather than in either
package's own `conftest.py`.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any, NoReturn, Self
from unittest.mock import AsyncMock, MagicMock, patch

import grpc
import pytest
import pytest_asyncio
import uvicorn
from alembic import command
from alembic.config import Config
from anthropic.types import Usage
from chat.core.config import Settings as ChatSettings
from chat.domain.schemas import (
    IntentClassificationResult,
    IntentLabel,
    RequestSegment,
)
from scheduler.core.config import Settings as SchedulerSettings
from scheduler.repositories import practitioner_repository
from shared_db import ensure_database_exists, isolated_database_url, isolated_name
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
os.environ["QDRANT_COLLECTION_NAME"] = isolated_name(
    _chat_settings.QDRANT_COLLECTION_NAME
)
# The repo's `.env` carries a developer's real Langfuse keys, and an app this tier
# builds would otherwise export its turns to their project - and wait on a flush to it
# at every lifespan's end. Blank, not unset: an unset variable falls through to `.env`.
# The same rule as the chat suite's `conftest.py`, restated here because one tier's
# conftest is not a dependency of another's.
os.environ["LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LANGFUSE_SECRET_KEY"] = ""


@pytest.fixture(scope="session", autouse=True)
def _apply_scheduler_migrations() -> None:
    """Create this process's isolated scheduler database if needed, then bring it to
    head - the same order this tier's per-service counterparts use."""
    ensure_database_exists(os.environ["SCHEDULER_DATABASE_URL"])
    alembic_cfg = Config(str(_SCHEDULER_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(_SCHEDULER_ROOT / "alembic"))
    command.upgrade(alembic_cfg, "head")


@pytest.fixture(scope="session", autouse=True)
def _apply_chat_migrations() -> None:
    """Create this process's isolated chat database if needed, then bring it to head."""
    ensure_database_exists(os.environ["DATABASE_URL"])
    alembic_cfg = Config(str(_CHAT_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(_CHAT_ROOT / "alembic"))
    command.upgrade(alembic_cfg, "head")


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _ensure_qdrant_collection_exists() -> None:
    """Create the isolated Qdrant collection once per session, if missing.

    The per-test clear below empties the collection's *points*, which is a 404 against
    a Qdrant that has never seen this collection - a fresh CI container, or a
    workstation whose test collection was dropped. Creating it is therefore this
    tier's own job, not something inherited from whichever suite happened to run
    first. Session-scoped for the same reason chat's unit tier scopes it that way:
    the collection's existence doesn't change from test to test, only its contents do.
    """
    from chat.repositories.qdrant_repository import create_client, ensure_collection

    qdrant_client = create_client(ChatSettings())
    try:
        await ensure_collection(qdrant_client)
    finally:
        await qdrant_client.close()


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


class PaidAPICallInTestError(BaseException):
    """Raised when a test in this tier reaches a real paid API instead of its fake.

    A `BaseException` for the reason chat's unit tier gives for its own: production code
    absorbs every `Exception` from a degradable dependency - `rerank_chunks` converts
    each to None by requirement - and a guard such a handler can swallow lets the test
    pass on the degraded path having attempted a live, billed request.

    Declared again rather than imported from `services/chat/tests/conftest.py`, for the
    same reason as the fakes below: one tier's harness is not a dependency of another's.
    """


# Every call chat makes that costs money, keyed by the SDK attribute it goes through -
# the same list as the chat unit tier's `_PAID_API_CALLS`. A new paid call belongs in
# both, in the change that introduces it.
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
    "plus a valid key to pass at all. Patch `chat.main.AsyncAnthropic` with this "
    "tier's `fake_anthropic_client(...)` and `chat.rag.<module>.embed_texts` with "
    "`fake_embed_texts` - see docs/testing-strategy.md."
)


@pytest.fixture(autouse=True)
def _paid_apis_are_blocked() -> Iterator[None]:
    """Fail any test in this tier that reaches Anthropic or Voyage for real.

    Every test here that drives a turn patches `chat.main.AsyncAnthropic` and
    `embed_texts` by hand, and nothing but this fixture notices one that forgets: the
    app's lifespan builds a real client, and the call goes out. Blocked on the SDK
    class rather than on chat's wrappers, so a client built anywhere is covered.
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
def _reranking_keeps_what_it_is_given() -> "Iterator[None]":
    """Fake the reranking boundary for every test in this tier.

    Required, not convenient. `_paid_apis_are_blocked` makes reaching the real
    reranker fail the test rather than bill it - which is why its error is not an
    `Exception`, since `rerank_chunks` converts every `Exception` to None by
    requirement. The guard makes the omission loud; this fake is what makes an
    ordinary FAQ turn in this tier not need one.

    The default is a working reranker that keeps the shortlist in the order it was
    given, matching `services/chat/tests/conftest.py`.
    """
    from chat.rag.pipeline import ScoredChunk

    async def keep_all(
        _client: object, _query: str, chunks: "list[ScoredChunk]", **_kwargs: object
    ) -> "list[ScoredChunk]":
        return [chunk.with_rerank_score(0.9) for chunk in chunks]

    with patch("chat.agent.answer_faq.rerank_chunks", new=keep_all):
        yield


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


@pytest_asyncio.fixture
async def scheduler_http() -> AsyncIterator[str]:
    """Serve the scheduler's real practitioner API on a loopback port.

    Only that router, without the service's own lifespan: the lifespan starts a gRPC
    server on a fixed port, and this tier already runs one of those on a port of its
    own. What is under test is the REST surface chat's proxy speaks to, and this is
    that surface, unmodified.
    """
    from fastapi import FastAPI
    from scheduler.api.practitioners import router as practitioners_router

    app = FastAPI()
    app.include_router(practitioners_router)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await serving


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


async def fake_embed_texts(
    _client: object,
    texts: list[str],
    input_type: str = "document",
) -> list[list[float]]:
    """Deterministic stand-in for Voyage embeddings, with no key and no network.

    Text mentioning "visit" or "hours" embeds near one axis and everything else near
    another - enough to exercise the real similarity floor against a real Qdrant.
    """

    def vector(text: str) -> list[float]:
        keywords = ("visit", "hours")
        base = [1.0, 0.0] if any(k in text.lower() for k in keywords) else [0.0, 1.0]
        return base + [0.0] * (_vector_size() - len(base))

    return [vector(text) for text in texts]


class _FakeTextEvent:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeFinalMessage:
    """Stand-in for the `Message` `get_final_message()` resolves to.

    Two fields, because two are read: `stop_reason`, to learn that the model stopped
    because it ran out of room rather than because it was finished, and `usage`, what
    the call spent, which a generation's trace records. The SDK's own `Usage` rather
    than a mock - a trace built from a `MagicMock` attribute would claim a token count
    nobody measured.
    """

    def __init__(self, stop_reason: str) -> None:
        self.stop_reason = stop_reason
        self.usage = Usage(
            input_tokens=120, output_tokens=40, cache_read_input_tokens=0
        )


class _FakeStream:
    """Stand-in for `AsyncAnthropic().messages.stream(...)`'s context manager.

    `stop_reason` is `end_turn` - a reply that finished on its own. This tier has no
    test about the truncation path; it needs the method to exist because every
    generating node reads it, and a fake missing it fails the turn instead.
    """

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

    async def get_final_message(self) -> _FakeFinalMessage:
        return _FakeFinalMessage("end_turn")


def _text_response(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.content = [block]
    # Set rather than left to the mock: a caller reads it to decide whether the
    # response was cut off at the cap, and a `MagicMock` is neither of the two things
    # that field can say.
    response.stop_reason = "end_turn"
    return response


def _trailing_text(messages: object) -> str:
    """Return the trailing entry's text from a rendered message list.

    What an unsplit message's one request carries, so this tier retrieves for the
    words the patient actually sent rather than for a placeholder.
    """
    if not isinstance(messages, list) or not messages:
        return "(empty)"
    content = messages[-1].get("content") if isinstance(messages[-1], dict) else None
    return content if isinstance(content, str) and content else "(empty)"


def fake_anthropic_client(tokens: list[str] | None = None) -> MagicMock:
    """Stand-in for `AsyncAnthropic`, patched over `chat.main.AsyncAnthropic`.

    Classification always answers one `faq_question` request carrying the message's
    own text, which is what these tests exercise; the booking loop is answered with a
    plain reply so a mixed turn cannot hang.
    """
    client = MagicMock()
    client.close = AsyncMock()
    client.messages.stream.return_value = _FakeStream(tokens or [])

    async def _create(*_args: object, **kwargs: object) -> MagicMock:
        if kwargs.get("tools") is not None:
            return _text_response("Which practitioner would you like to see?")
        return _text_response(
            IntentClassificationResult(
                segments=[
                    RequestSegment(
                        intent=IntentLabel.FAQ_QUESTION,
                        text=_trailing_text(kwargs.get("messages")),
                    )
                ]
            ).model_dump_json()
        )

    client.messages.create = AsyncMock(side_effect=_create)
    return client
