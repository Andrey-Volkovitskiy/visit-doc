"""`X-VisitDoc-Trace: off`: one turn untraced, and nothing else.

The header's whole effect is a flag in the turn's own context, which the sampler reads
before anything else. Each clause of the contract (contracts/tracing-headers.md S1-S6)
has its own test: the flag drops every span of its turn, never one of another turn -
concurrent, later, or on a service that does not trace at all - and it is set in one
place only.
"""

import ast
import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from chat.db.session import engine
from chat.domain.schemas import IntentLabel
from chat.main import app
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from langfuse import Langfuse
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs

from .conftest import (
    LOCAL_NOW,
    GatedAnthropicStream,
    chat_id_for,
    fake_anthropic_client,
    finished_spans,
    installed_tracer,
    tracing_settings,
)

_OFF = {"X-VisitDoc-Trace": "off"}
_CHAT_PACKAGE = Path(__file__).resolve().parents[1] / "src" / "chat"
_TURN_MODULE = _CHAT_PACKAGE / "api" / "turn.py"


def _post(client: TestClient, message: str, headers: dict[str, str]) -> Any:
    return client.post(
        "/chat",
        json={
            "chat_id": chat_id_for(client),
            "message": message,
            "local_now": LOCAL_NOW,
        },
        headers=headers,
    )


def _booking_client() -> MagicMock:
    """A turn that makes generations and tool calls, so S1 has them to drop."""
    return fake_anthropic_client(
        intents=[IntentLabel.BOOKING],
        booking_tool_calls=[[("list_practitioners", {})]],
    )


def _received_turn_ids(logs: list[dict[str, Any]]) -> dict[str, str]:
    """Map each received message's text to the turn that received it."""
    return {
        entry["message"]: entry["turn_id"]
        for entry in logs
        if entry["event"] == "turn.message_received"
    }


def _trace_ids(exporter: InMemorySpanExporter) -> set[str]:
    return {
        format(span.context.trace_id, "032x")
        for named in finished_spans(exporter).values()
        for span in named
        if span.context is not None
    }


# --- S1: an untraced turn exports nothing ---------------------------------------------


def test_s1_a_turn_sent_off_exports_no_span_at_all(
    span_exporter: InMemorySpanExporter,
) -> None:
    with (
        patch("chat.main.AsyncAnthropic", return_value=_booking_client()),
        capture_logs() as logs,
        TestClient(app) as client,
    ):
        traced = _post(client, "can I book Friday?", {})
        # The same turn, traced, has generations and tool calls to drop.
        spans = finished_spans(span_exporter)
        assert {"turn", "handle_booking.model[1]", "tool:list_practitioners"} <= set(
            spans
        )
        span_exporter.clear()
        logs.clear()

        untraced = _post(client, "and Saturday?", _OFF)

    assert (traced.status_code, untraced.status_code) == (200, 200)
    assert json.loads(untraced.text.strip().splitlines()[-1])["type"] == "done"
    assert finished_spans(span_exporter) == {}
    events = [entry["event"] for entry in logs]
    assert "turn.completed" in events
    assert "turn.traced" not in events


@pytest.mark.parametrize("value", ["OFF", "  off  ", "Off"])
def test_s1_the_off_value_is_read_trimmed_and_in_any_case(
    span_exporter: InMemorySpanExporter, value: str
) -> None:
    with (
        patch("chat.main.AsyncAnthropic", return_value=fake_anthropic_client()),
        TestClient(app) as client,
    ):
        response = _post(client, "is the clinic open?", {"X-VisitDoc-Trace": value})

    assert response.status_code == 200
    assert finished_spans(span_exporter) == {}


@pytest.mark.parametrize("value", ["yes", "on", "", "offf", "false"])
def test_any_other_trace_header_value_is_rejected_before_anything_is_stored(
    span_exporter: InMemorySpanExporter, value: str
) -> None:
    anthropic_client = fake_anthropic_client()
    with (
        patch("chat.main.AsyncAnthropic", return_value=anthropic_client),
        capture_logs() as logs,
        TestClient(app) as client,
    ):
        response = _post(client, "is the clinic open?", {"X-VisitDoc-Trace": value})
        stored = client.get(f"/chats/{chat_id_for(client)}/messages").json()

    assert response.status_code == 422
    assert stored["messages"] == []
    assert "turn.message_received" not in [entry["event"] for entry in logs]
    anthropic_client.messages.create.assert_not_called()
    assert finished_spans(span_exporter) == {}


# --- S2: the flag never leaks across tasks --------------------------------------------


async def test_s2_a_concurrent_traced_turn_on_another_chat_exports_fully(
    span_exporter: InMemorySpanExporter,
) -> None:
    await engine.dispose()
    gate = asyncio.Event()
    started: list[asyncio.Event] = []

    def _gated_stream(*_args: object, **_kwargs: object) -> GatedAnthropicStream:
        began = asyncio.Event()
        started.append(began)
        return GatedAnthropicStream(["You're ", "welcome!"], gate, started=began)

    client = fake_anthropic_client(intents=[IntentLabel.SMALL_TALK])
    client.messages.stream.side_effect = _gated_stream

    with (
        patch("chat.main.AsyncAnthropic", return_value=client),
        capture_logs(processors=[merge_contextvars]) as logs,
        TestClient(app),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as http:
            off_chat = (await http.post("/chats")).json()["id"]
            on_chat = (await http.post("/chats")).json()["id"]

            def _turn(chat_id: str, message: str, headers: dict[str, str]) -> Any:
                return asyncio.create_task(
                    http.post(
                        "/chat",
                        json={
                            "chat_id": chat_id,
                            "message": message,
                            "local_now": LOCAL_NOW,
                        },
                        headers=headers,
                    )
                )

            untraced = _turn(off_chat, "thanks, off", _OFF)
            traced = _turn(on_chat, "thanks, on", {})

            # Both turns are generating at once before either is let go.
            async def _both_started() -> None:
                while len(started) < 2:
                    await asyncio.sleep(0)
                await asyncio.gather(*(began.wait() for began in started))

            await asyncio.wait_for(_both_started(), timeout=5)
            gate.set()
            responses = await asyncio.wait_for(
                asyncio.gather(untraced, traced), timeout=5
            )

    assert [response.status_code for response in responses] == [200, 200]
    turn_ids = _received_turn_ids(logs)
    assert _trace_ids(span_exporter) == {
        Langfuse.create_trace_id(seed=turn_ids["thanks, on"])
    }
    spans = finished_spans(span_exporter)
    for name in ("turn", "classify_intent.model", "small_talk.model"):
        assert len(spans[name]) == 1, name
    traced_turns = [
        entry["turn_id"] for entry in logs if entry["event"] == "turn.traced"
    ]
    assert traced_turns == [turn_ids["thanks, on"]]


# --- S3: the flag does not outlive its turn -------------------------------------------


def test_s3_the_next_turn_on_the_same_chat_is_traced(
    span_exporter: InMemorySpanExporter,
) -> None:
    with (
        patch("chat.main.AsyncAnthropic", return_value=fake_anthropic_client()),
        capture_logs(processors=[merge_contextvars]) as logs,
        TestClient(app) as client,
    ):
        first = _post(client, "is the clinic open?", _OFF)
        second = _post(client, "and on Sunday?", {})

    assert (first.status_code, second.status_code) == (200, 200)
    turn_ids = _received_turn_ids(logs)
    assert _trace_ids(span_exporter) == {
        Langfuse.create_trace_id(seed=turn_ids["and on Sunday?"])
    }
    assert "turn" in finished_spans(span_exporter)


# --- S4: a superseded untraced turn stays untraced ------------------------------------


async def test_s4_a_superseded_off_turn_exports_nothing_for_it_or_its_cancellation(
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
                    headers=_OFF,
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
    assert finished_spans(span_exporter) == {}


# --- S5: the header only ever turns tracing off ---------------------------------------


def test_s5_a_service_that_does_not_trace_exports_nothing_for_a_plain_turn() -> None:
    exporter = InMemorySpanExporter()
    # Blank after stripping: the service does not trace, whatever a request says.
    with (
        installed_tracer(tracing_settings(LANGFUSE_SECRET_KEY="  "), exporter),
        patch("chat.main.AsyncAnthropic", return_value=fake_anthropic_client()),
        capture_logs() as logs,
        TestClient(app) as client,
    ):
        response = _post(client, "is the clinic open?", {})

    assert response.status_code == 200
    assert exporter.get_finished_spans() == ()
    assert "turn.traced" not in [entry["event"] for entry in logs]


# --- S6: the flag has one setter -----------------------------------------------------


def _parsed(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def _untraced_setters(tree: ast.Module) -> list[ast.Attribute]:
    """Return every `UNTRACED.set` reference in `tree`, called or passed along."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "set"
        and (
            (isinstance(node.value, ast.Name) and node.value.id == "UNTRACED")
            or (isinstance(node.value, ast.Attribute) and node.value.attr == "UNTRACED")
        )
    ]


def test_s6_only_the_turn_route_sets_the_untraced_flag() -> None:
    setters = {
        str(path.relative_to(_CHAT_PACKAGE))
        for path in sorted(_CHAT_PACKAGE.rglob("*.py"))
        if _untraced_setters(_parsed(path))
    }

    assert setters == {"api/turn.py"}


def _innermost_function(
    tree: ast.Module, node: ast.AST
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """Return the innermost function definition `node` sits in."""
    enclosing = [
        function
        for function in ast.walk(tree)
        if isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef)
        and any(inner is node for inner in ast.walk(function))
    ]
    return max(enclosing, key=lambda function: function.lineno)


def test_s6_the_flag_is_set_just_before_the_turns_task_is_created() -> None:
    # In the function that creates the turn's task, and before it does: the task
    # copies its context at creation, so a flag set any later misses the turn.
    tree = _parsed(_TURN_MODULE)
    (setter,) = _untraced_setters(tree)
    (creation,) = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_task"
        and any(
            isinstance(arg, ast.Call)
            and isinstance(arg.func, ast.Name)
            and arg.func.id == "run_pipeline"
            for arg in node.args
        )
    ]

    function = _innermost_function(tree, creation)
    assert function.name == "launch"
    assert _innermost_function(tree, setter) is function
    assert setter.lineno < creation.lineno
