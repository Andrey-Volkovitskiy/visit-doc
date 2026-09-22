"""A turn's trace, driven through the app: its root, its tree, and how it ended.

Every turn exports one trace whose id is derived from the turn's own id, so any log
line of the turn leads to it. The root carries what the turn was asked and what it
logged on completion; a turn that failed or was superseded says which on the root, not
only somewhere inside it.
"""

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from chat.agent import generation_registry
from chat.api.session_cookie import COOKIE_NAME
from chat.api.turn import ReplyOutcome
from chat.core.config import Settings
from chat.db.session import engine, session_factory
from chat.domain.schemas import IntentLabel
from chat.main import app
from chat.rag.indexing import publish_revision, remove_entry_chunks
from chat.repositories import chat_repository, faq_repository
from chat.repositories.qdrant_repository import create_client, ensure_collection
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from langfuse import Langfuse
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs
from ulid import ULID

from .conftest import (
    LOCAL_NOW,
    GatedAnthropicStream,
    chat_id_for,
    fake_anthropic_client,
    fake_embed_texts,
    finished_spans,
    is_child_of,
    set_seeded_session,
    span_attributes,
    span_output,
    turn,
)

_ENTRY_CONTENT = "Visiting hours are 8am to 5pm."
# Keys a captured entry carries from the bound context rather than from its event.
_CONTEXT_KEYS = ("event", "log_level", "turn_id", "node", "segment")


@pytest.fixture
async def seeded_entry() -> AsyncIterator[int]:
    """Seed one indexed FAQ entry for the session the next client adopts."""
    settings = Settings()
    qdrant_client = create_client(settings)
    await ensure_collection(qdrant_client)
    revision = str(ULID())
    async with session_factory() as session:
        seeded_session = await chat_repository.create_session(session)
        entry = await faq_repository.create(
            session, seeded_session.id, _ENTRY_CONTENT, revision
        )
    set_seeded_session(seeded_session.id)
    with patch("chat.rag.indexing.embed_texts", fake_embed_texts):
        await publish_revision(
            qdrant_client,
            MagicMock(),
            seeded_session.id,
            entry.id,
            revision,
            _ENTRY_CONTENT,
        )
    # A sync test's `TestClient` handles requests on a loop of its own, so the pool
    # this fixture bound is released before the test uses the engine.
    await engine.dispose()
    yield entry.id
    await engine.dispose()
    await remove_entry_chunks(qdrant_client, seeded_session.id, entry.id)
    async with session_factory() as session:
        await faq_repository.delete(session, seeded_session.id, entry.id)
    await qdrant_client.close()


def _fields(entry: dict[str, Any]) -> dict[str, Any]:
    """Return what a captured log entry carried, without its event and context."""
    return {k: v for k, v in entry.items() if k not in _CONTEXT_KEYS}


def _jsonable(value: object) -> object:
    """Return `value` as it reads back from a trace: through JSON and out again."""
    return json.loads(json.dumps(value, default=str))


def _one(logs: list[dict[str, Any]], event: str) -> dict[str, Any]:
    (entry,) = [e for e in logs if e["event"] == event]
    return entry


def _trace_ids(spans: dict[str, list[ReadableSpan]]) -> set[str]:
    return {
        format(span.context.trace_id, "032x")
        for named in spans.values()
        for span in named
        if span.context is not None
    }


def _metadata(span: ReadableSpan, key: str) -> Any:
    raw = span_attributes(span)[f"langfuse.observation.metadata.{key}"]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _run_one_turn(
    anthropic_client: MagicMock, message: str, *, fails: bool = False
) -> tuple[list[dict[str, Any]], str, str]:
    """Send one message through the app; return the log, the chat id, the session id.

    A turn that `fails` breaks its stream, which the client would otherwise re-raise.
    """
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic", return_value=anthropic_client),
        capture_logs(processors=[merge_contextvars]) as logs,
        TestClient(app, raise_server_exceptions=not fails) as client,
    ):
        response = turn(client, message)
        chat_id = chat_id_for(client)
        session_id = client.cookies[COOKIE_NAME]
    if not fails:
        assert response.status_code == 200
    return logs, chat_id, session_id


# --- C1/C2: one trace per turn, and what it is filed under ---------------------------


def test_a_turn_exports_one_trace_seeded_from_its_id(
    span_exporter: InMemorySpanExporter,
) -> None:
    logs, chat_id, session_id = _run_one_turn(
        fake_anthropic_client(), "is the clinic open on Saturday?"
    )

    turn_id = _one(logs, "turn.message_received")["turn_id"]
    spans = finished_spans(span_exporter)
    trace_id = Langfuse.create_trace_id(seed=turn_id)
    assert _trace_ids(spans) == {trace_id}
    (root,) = spans["turn"]
    # The seeded parent carries only the trace id: nothing exported sits above the root.
    assert not any(
        is_child_of(root, span) for named in spans.values() for span in named
    )
    for named in spans.values():
        for span in named:
            attributes = span_attributes(span)
            assert attributes["session.id"] == chat_id
            assert (
                attributes["user.id"]
                == hashlib.sha256(session_id.encode()).hexdigest()[:32]
            )
            # The session id is the patient's cookie value - a credential - so it must
            # reach no attribute of any span, under any name.
            assert not [
                name for name, value in attributes.items() if session_id in str(value)
            ]
            assert attributes["langfuse.trace.name"] == "turn"
            assert attributes["langfuse.environment"] == "development"
            assert attributes["langfuse.trace.metadata.turn_id"] == turn_id
    traced = _one(logs, "turn.traced")
    assert traced["trace_id"] == trace_id
    assert traced["turn_id"] == turn_id


def test_the_root_carries_the_message_and_what_the_turn_logged(
    span_exporter: InMemorySpanExporter,
) -> None:
    message = "is the clinic open on Saturday?"
    logs, _, _ = _run_one_turn(fake_anthropic_client(), message)

    (root,) = finished_spans(span_exporter)["turn"]
    attributes = span_attributes(root)
    assert attributes["langfuse.observation.input"] == message
    assert span_output(root) == _jsonable(_fields(_one(logs, "turn.completed")))
    assert _metadata(root, "intent_classified") == _jsonable(
        _fields(_one(logs, "intent.classified"))
    )
    assert _metadata(root, "turn_outcome") == "completed"
    assert attributes["langfuse.observation.level"] == "DEFAULT"


def test_every_node_of_the_turn_nests_under_the_root(
    span_exporter: InMemorySpanExporter,
) -> None:
    _run_one_turn(fake_anthropic_client(), "is the clinic open on Saturday?")

    spans = finished_spans(span_exporter)
    (root,) = spans["turn"]
    for node in ("classify_intent", "answer_faq", "compose_answer"):
        (span,) = spans[node]
        assert is_child_of(span, root)


def test_a_paused_conversation_exports_nothing_for_its_message(
    span_exporter: InMemorySpanExporter,
) -> None:
    with (
        patch("chat.main.AsyncAnthropic", return_value=fake_anthropic_client()),
        capture_logs() as logs,
        TestClient(app) as client,
    ):
        chat_id = chat_id_for(client)
        paused = client.post(
            f"/console/chats/{chat_id}/assistant", json={"enabled": False}
        )
        assert paused.status_code == 200
        response = turn(client, "is anyone there?")

    assert json.loads(response.text.strip()) == {"type": "silent"}
    assert finished_spans(span_exporter) == {}
    assert "turn.traced" not in [e["event"] for e in logs]


def test_an_untraced_service_logs_no_trace_for_its_turn() -> None:
    # No tracer installed: the lifespan builds a disabled one from the test settings.
    with (
        patch("chat.main.AsyncAnthropic", return_value=fake_anthropic_client()),
        capture_logs() as logs,
        TestClient(app) as client,
    ):
        turn(client, "is the clinic open on Saturday?")

    assert "turn.traced" not in [e["event"] for e in logs]


# --- C3: the whole tree of one mixed turn --------------------------------------------


def test_a_mixed_turn_exports_its_whole_tree(
    span_exporter: InMemorySpanExporter, seeded_entry: int
) -> None:
    client = fake_anthropic_client(
        ["Visiting hours are 8am to 5pm."],
        intents=[IntentLabel.FAQ_QUESTION, IntentLabel.BOOKING],
        booking_tool_calls=[[("list_practitioners", {})]],
    )

    _run_one_turn(client, "when can I visit, and can I book Friday?")

    spans = finished_spans(span_exporter)
    (root,) = spans["turn"]
    nodes = {}
    for node in ("classify_intent", "answer_faq", "handle_booking", "compose_answer"):
        (nodes[node],) = spans[node]
        assert is_child_of(nodes[node], root)
    assert is_child_of(spans["classify_intent.model"][0], nodes["classify_intent"])
    (request,) = spans["request[0]"]
    assert is_child_of(request, nodes["answer_faq"])
    for step in (
        "faq.embed",
        "faq.search",
        "faq.similarity_gate",
        "faq.rerank_gate",
        "faq.verdict",
        "answer_faq.model",
    ):
        (span,) = spans[step]
        assert is_child_of(span, request), step
    # The roster read the node makes before its loop, and the call the model asked for.
    roster_reads = spans["tool:list_practitioners"]
    assert len(roster_reads) == 2
    assert all(is_child_of(span, nodes["handle_booking"]) for span in roster_reads)
    for iteration in (1, 2):
        (model,) = spans[f"handle_booking.model[{iteration}]"]
        assert is_child_of(model, nodes["handle_booking"])
    assert is_child_of(spans["compose_answer.model"][0], nodes["compose_answer"])


def test_a_single_specialist_turn_makes_no_composing_call(
    span_exporter: InMemorySpanExporter, seeded_entry: int
) -> None:
    _run_one_turn(fake_anthropic_client(["Visiting hours are 8am to 5pm."]), "hours?")

    spans = finished_spans(span_exporter)
    assert "answer_faq.model" in spans
    assert "compose_answer" in spans
    assert "compose_answer.model" not in spans


def test_an_all_abstained_turn_makes_no_composing_call(
    span_exporter: InMemorySpanExporter,
) -> None:
    # An empty corpus abstains every request, and the turn collapses to its constant.
    _run_one_turn(
        fake_anthropic_client(
            segments=[
                (IntentLabel.FAQ_QUESTION, "when can I visit?"),
                (IntentLabel.FAQ_QUESTION, "do you take cash?"),
            ]
        ),
        "when can I visit, and do you take cash?",
    )

    spans = finished_spans(span_exporter)
    assert {"request[0]", "request[1]"} <= set(spans)
    assert "answer_faq.model" not in spans
    assert "compose_answer.model" not in spans


# --- C4: how a turn that did not complete says so -----------------------------------


def test_a_turn_whose_generation_fails_is_exported_marked_failed(
    span_exporter: InMemorySpanExporter,
) -> None:
    client = fake_anthropic_client(
        intents=[IntentLabel.SMALL_TALK], stream_error=RuntimeError("model down")
    )

    logs, _, _ = _run_one_turn(client, "thanks!", fails=True)

    assert "turn.error" in [e["event"] for e in logs]
    spans = finished_spans(span_exporter)
    (generation,) = spans["small_talk.model"]
    assert span_attributes(generation)["langfuse.observation.level"] == "ERROR"
    (root,) = spans["turn"]
    assert _metadata(root, "turn_outcome") == "failed"
    assert span_attributes(root)["langfuse.observation.level"] == "ERROR"
    assert "turn.traced" in [e["event"] for e in logs]


async def test_a_turn_superseded_by_a_staff_post_is_marked_cancelled_not_failed(
    span_exporter: InMemorySpanExporter,
) -> None:
    await engine.dispose()
    gate, started = asyncio.Event(), asyncio.Event()
    client = fake_anthropic_client(intents=[IntentLabel.SMALL_TALK])
    client.messages.stream.return_value = GatedAnthropicStream(
        ["You're ", "welcome!"], gate, started=started
    )

    with (
        patch("chat.main.AsyncAnthropic", return_value=client),
        TestClient(app),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as http:
            chat_id = (await http.post("/chats")).json()["id"]
            pending = asyncio.create_task(
                http.post(
                    "/chat",
                    json={
                        "chat_id": chat_id,
                        "message": "thanks!",
                        "local_now": LOCAL_NOW,
                    },
                )
            )
            await asyncio.wait_for(started.wait(), timeout=5)
            await http.post(
                f"/console/chats/{chat_id}/messages",
                json={"content": "I've got this one."},
            )
            gate.set()
            response = await asyncio.wait_for(pending, timeout=5)

    assert json.loads(response.text.strip().splitlines()[-1]) == {"type": "cancelled"}
    (root,) = finished_spans(span_exporter)["turn"]
    attributes = span_attributes(root)
    assert attributes["langfuse.observation.level"] == "WARNING"
    assert attributes["langfuse.observation.status_message"] == "cancelled"
    assert _metadata(root, "turn_outcome") == "cancelled"


async def test_a_trace_that_cannot_be_opened_still_ends_the_turn() -> None:
    # The stream's end and the deregistration are registered before the root opens: a
    # trace that failed to open would otherwise leave the patient's stream waiting on a
    # sentinel nothing sends, and the chat's registry holding a finished task.
    await engine.dispose()
    with (
        patch("chat.api.turn.turn_trace", side_effect=RuntimeError("trace refused")),
        patch("chat.main.AsyncAnthropic", return_value=fake_anthropic_client()),
        TestClient(app),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as http:
            chat_id = (await http.post("/chats")).json()["id"]
            with pytest.raises(RuntimeError, match="trace refused"):
                await asyncio.wait_for(
                    http.post(
                        "/chat",
                        json={
                            "chat_id": chat_id,
                            "message": "thanks!",
                            "local_now": LOCAL_NOW,
                        },
                    ),
                    timeout=5,
                )

    assert chat_id not in generation_registry._in_flight


@pytest.mark.parametrize(
    "outcome",
    [outcome for outcome in ReplyOutcome if outcome is not ReplyOutcome.STORED],
)
def test_a_turn_whose_reply_was_not_stored_is_marked_cancelled(
    span_exporter: InMemorySpanExporter, outcome: ReplyOutcome
) -> None:
    # A takeover that lands after the turn deregistered raises nothing into it: the
    # write declines, and the patient is sent `cancelled` all the same.
    with (
        patch("chat.api.turn._persist_outcome", AsyncMock(return_value=outcome)),
        patch("chat.main.AsyncAnthropic", return_value=fake_anthropic_client()),
        TestClient(app) as client,
    ):
        response = turn(client, "thanks!")

    assert json.loads(response.text.strip().splitlines()[-1]) == {"type": "cancelled"}
    (root,) = finished_spans(span_exporter)["turn"]
    attributes = span_attributes(root)
    # The same marking a superseded turn's root gets: to the patient the two endings
    # are one, and the trace does not tell them apart by level either.
    assert attributes["langfuse.observation.level"] == "WARNING"
    assert attributes["langfuse.observation.status_message"] == "cancelled"
    assert _metadata(root, "turn_outcome") == "cancelled"


# --- Eval turns: the run and case a trace belongs to ---------------------------------

_RUN_ID = "01M321DWRXSVSY7GW9RY3CR9YW"
_CASE_ID = "G-a-01"
_EVAL_HEADERS = {"X-VisitDoc-Eval-Run": _RUN_ID, "X-VisitDoc-Eval-Case": _CASE_ID}


def _post_turn(
    client: TestClient, message: str, headers: dict[str, str]
) -> tuple[int, str]:
    """Send one message to the client's chat with `headers`; return the chat id too."""
    chat_id = chat_id_for(client)
    response = client.post(
        "/chat",
        json={"chat_id": chat_id, "message": message, "local_now": LOCAL_NOW},
        headers=headers,
    )
    return response.status_code, chat_id


def test_an_eval_turn_is_filed_under_its_run_and_case(
    span_exporter: InMemorySpanExporter,
) -> None:
    with (
        patch("chat.main.AsyncAnthropic", return_value=fake_anthropic_client()),
        TestClient(app) as client,
    ):
        status, _ = _post_turn(client, "is the clinic open?", _EVAL_HEADERS)

    assert status == 200
    spans = finished_spans(span_exporter)
    assert spans
    for named in spans.values():
        for span in named:
            attributes = span_attributes(span)
            assert attributes["langfuse.environment"] == "eval"
            assert attributes["langfuse.trace.metadata.eval_run_id"] == _RUN_ID
            assert attributes["langfuse.trace.metadata.eval_case_id"] == _CASE_ID


def test_a_turn_without_eval_headers_carries_no_eval_metadata(
    span_exporter: InMemorySpanExporter,
) -> None:
    with (
        patch("chat.main.AsyncAnthropic", return_value=fake_anthropic_client()),
        TestClient(app) as client,
    ):
        status, _ = _post_turn(client, "is the clinic open?", {})

    assert status == 200
    spans = finished_spans(span_exporter)
    assert spans
    for named in spans.values():
        for span in named:
            attributes = span_attributes(span)
            assert attributes["langfuse.environment"] == "development"
            assert "langfuse.trace.metadata.eval_run_id" not in attributes
            assert "langfuse.trace.metadata.eval_case_id" not in attributes


@pytest.mark.parametrize(
    "headers",
    [
        {"X-VisitDoc-Eval-Run": _RUN_ID},
        {"X-VisitDoc-Eval-Case": _CASE_ID},
        {**_EVAL_HEADERS, "X-VisitDoc-Eval-Run": "not-a-run-id"},
        {**_EVAL_HEADERS, "X-VisitDoc-Eval-Run": _RUN_ID.lower()},
        {**_EVAL_HEADERS, "X-VisitDoc-Eval-Run": _RUN_ID + "0"},
        {**_EVAL_HEADERS, "X-VisitDoc-Eval-Case": "G-A-01"},
        {**_EVAL_HEADERS, "X-VisitDoc-Eval-Case": "G-a-1"},
        {**_EVAL_HEADERS, "X-VisitDoc-Eval-Case": "case-1"},
        {**_EVAL_HEADERS, "X-VisitDoc-Eval-Case": ""},
    ],
)
def test_malformed_eval_headers_are_rejected_before_anything_is_stored(
    span_exporter: InMemorySpanExporter, headers: dict[str, str]
) -> None:
    anthropic_client = fake_anthropic_client()
    with (
        patch("chat.main.AsyncAnthropic", return_value=anthropic_client),
        capture_logs() as logs,
        TestClient(app) as client,
    ):
        status, chat_id = _post_turn(client, "is the clinic open?", headers)
        stored = client.get(f"/chats/{chat_id}/messages").json()["messages"]

    assert status == 422
    assert stored == []
    assert "turn.message_received" not in [e["event"] for e in logs]
    anthropic_client.messages.create.assert_not_called()
    assert finished_spans(span_exporter) == {}
