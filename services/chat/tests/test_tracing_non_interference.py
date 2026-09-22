"""Tracing never changes a turn, and a broken or stuck exporter never reaches one.

Each scripted turn is run twice against the same fakes - once with nothing traced, once
into a tracer - and everything the turn produces is compared: what the patient was
streamed, what the thread stores, what staff are shown, and every log line apart from
the two that report tracing's own state. Then the same turns are run into an exporter
that fails every export, and one that hangs before failing, and must still come out
the same and on time.
"""

import asyncio
import json
import logging
import re
import threading
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from chat.core.config import Settings
from chat.db.session import engine, session_factory
from chat.domain.schemas import IntentLabel
from chat.main import app
from chat.rag.indexing import publish_revision, remove_entry_chunks
from chat.repositories import chat_repository, faq_repository
from chat.repositories.qdrant_repository import create_client, ensure_collection
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from structlog.testing import capture_logs
from ulid import ULID

from .conftest import (
    async_chat_id_for,
    async_turn,
    fake_anthropic_client,
    fake_embed_texts,
    installed_tracer,
    set_seeded_session,
    tracing_settings,
)

# The only log lines tracing may add to a turn: its own state, never the turn's.
_TRACING_EVENTS = ("turn.traced", "tracing.export_failed")
# Values that differ between two runs of one turn by construction, not by behaviour.
_ULID = re.compile(r"\b[0-9A-HJKMNP-TV-Z]{26}\b")
_MOCK_ID = re.compile(r" id='\d+'")
_VOLATILE_KEYS = ("duration_ms", "until", "created_at")
# How long the stuck exporter holds each export - far longer than any scripted turn.
_EXPORT_BLOCK_SECONDS = 3.0


@dataclass(frozen=True)
class _Scenario:
    message: str
    client: Callable[[], MagicMock]


_SCENARIOS = {
    "faq": _Scenario(
        "when can I visit?",
        lambda: fake_anthropic_client(["Visiting hours are 8am to 5pm."]),
    ),
    "booking": _Scenario(
        "can I book Friday?",
        lambda: fake_anthropic_client(
            intents=[IntentLabel.BOOKING],
            booking_tool_calls=[[("list_practitioners", {})]],
        ),
    ),
    "merged": _Scenario(
        "when can I visit, and can I book Friday?",
        lambda: fake_anthropic_client(
            ["Visiting hours are 8am to 5pm."],
            intents=[IntentLabel.FAQ_QUESTION, IntentLabel.BOOKING],
        ),
    ),
    "small_talk": _Scenario(
        "thanks!",
        lambda: fake_anthropic_client(
            ["You're welcome!"], intents=[IntentLabel.SMALL_TALK]
        ),
    ),
    "stopping": _Scenario(
        "I want to talk to a person",
        lambda: fake_anthropic_client(intents=[IntentLabel.CALL_STAFF]),
    ),
    "all_abstained": _Scenario(
        "do you take cash, and is there parking?",
        lambda: fake_anthropic_client(
            segments=[
                (IntentLabel.FAQ_QUESTION, "do you take cash?"),
                (IntentLabel.FAQ_QUESTION, "is there parking?"),
            ]
        ),
    ),
}


@dataclass(frozen=True)
class _Outcome:
    """Everything one run of a turn produced, with run-specific values normalized."""

    stream: list[Any]
    thread: list[Any]
    conversation: dict[str, Any]
    logs: list[str]
    tracing_logs: list[dict[str, Any]]
    seconds: float
    # When the reply finished arriving, on the `time.perf_counter` clock.
    ended_at: float


@pytest.fixture
async def seeded_entry() -> AsyncIterator[int]:
    """Seed one indexed FAQ entry for the session every run's client adopts."""
    settings = Settings()
    qdrant_client = create_client(settings)
    await ensure_collection(qdrant_client)
    revision = str(ULID())
    async with session_factory() as session:
        seeded_session = await chat_repository.create_session(session)
        entry = await faq_repository.create(
            session, seeded_session.id, "Visiting hours are 8am to 5pm.", revision
        )
    set_seeded_session(seeded_session.id)
    with patch("chat.rag.indexing.embed_texts", fake_embed_texts):
        await publish_revision(
            qdrant_client,
            MagicMock(),
            seeded_session.id,
            entry.id,
            revision,
            "Visiting hours are 8am to 5pm.",
        )
    await engine.dispose()
    yield entry.id
    await engine.dispose()
    await remove_entry_chunks(qdrant_client, seeded_session.id, entry.id)
    async with session_factory() as session:
        await faq_repository.delete(session, seeded_session.id, entry.id)
    await qdrant_client.close()


def _normalized(value: Any) -> Any:
    """Return `value` with ids and timings that differ between runs replaced."""
    if isinstance(value, dict):
        return {
            key: "<volatile>" if key in _VOLATILE_KEYS else _normalized(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_normalized(item) for item in value]
    if isinstance(value, str):
        return _MOCK_ID.sub("", _ULID.sub("<id>", value))
    return value


def _log_line(entry: dict[str, Any]) -> str:
    return json.dumps(_normalized(json.loads(json.dumps(entry, default=str))))


async def _run(
    scenario: _Scenario,
    tracing: AbstractContextManager[object] | None = None,
    *,
    settle: Callable[[], None] | None = None,
) -> _Outcome:
    """Run one scripted turn through the app, traced if `tracing` is given.

    `settle` runs once the turn's records are read and before the app stops.

    Requests go through the test's own loop, with the lifespan held open by one
    `TestClient`, so two runs in one test do not leave the engine bound to a loop
    that has gone.
    """
    await engine.dispose()
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic", return_value=scenario.client()),
        capture_logs() as logs,
        tracing if tracing is not None else nullcontext(),
    ):
        with TestClient(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as client:
                chat_id = await async_chat_id_for(client)
                started = time.perf_counter()
                response = await async_turn(client, scenario.message)
                ended_at = time.perf_counter()
                seconds = ended_at - started
                thread = (await client.get(f"/chats/{chat_id}/messages")).json()[
                    "messages"
                ]
                conversations = (await client.get("/console/conversations")).json()
                if settle is not None:
                    await asyncio.to_thread(settle)
        (conversation,) = [
            c for c in conversations["conversations"] if c["chat_id"] == chat_id
        ]
    await engine.dispose()
    assert response.status_code == 200
    return _Outcome(
        stream=_normalized([json.loads(line) for line in response.text.splitlines()]),
        thread=_normalized(thread),
        conversation={
            "emphasized": conversation["emphasized"],
            "escalated": conversation["escalated"],
            "escalation_reason": conversation["escalation_reason"],
            "assistant_may_reply": conversation["assistant_may_reply"],
            "attention": conversation["attention_since"] is not None,
            "paused": conversation["pause_seconds_remaining"] is not None,
        },
        logs=sorted(
            _log_line(entry) for entry in logs if entry["event"] not in _TRACING_EVENTS
        ),
        tracing_logs=[entry for entry in logs if entry["event"] in _TRACING_EVENTS],
        seconds=seconds,
        ended_at=ended_at,
    )


def _assert_same_turn(untraced: _Outcome, traced: _Outcome) -> None:
    assert traced.stream == untraced.stream
    assert traced.thread == untraced.thread
    assert traced.conversation == untraced.conversation
    assert traced.logs == untraced.logs


@pytest.mark.parametrize("name", list(_SCENARIOS))
async def test_a_traced_turn_is_the_same_turn_as_an_untraced_one(
    seeded_entry: int, name: str
) -> None:
    scenario = _SCENARIOS[name]
    exporter = InMemorySpanExporter()

    untraced = await _run(scenario)
    traced = await _run(scenario, installed_tracer(tracing_settings(), exporter))

    _assert_same_turn(untraced, traced)
    # And it was really traced: a comparison against a run that exported nothing
    # would pass for the wrong reason.
    assert any(span.name == "turn" for span in exporter.get_finished_spans())
    assert [e["event"] for e in traced.tracing_logs] == ["turn.traced"]
    assert untraced.tracing_logs == []


class _FailingExporter(SpanExporter):
    """An exporter whose destination refuses every batch."""

    def __init__(self) -> None:
        self.attempts = 0

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.attempts += 1
        raise ConnectionError("langfuse is unreachable")

    def shutdown(self) -> None:
        return None


class _StuckExporter(SpanExporter):
    """An exporter that hangs far past any turn, then gives the batch up."""

    def __init__(self) -> None:
        self.attempts = 0
        # When each export began, on the `time.perf_counter` clock.
        self.started: list[float] = []

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.attempts += 1
        self.started.append(time.perf_counter())
        time.sleep(_EXPORT_BLOCK_SECONDS)
        raise TimeoutError("langfuse did not answer")

    def shutdown(self) -> None:
        return None


def _forget_earlier_export_failures() -> None:
    """Let the batch processor report an export failure it has already reported.

    OpenTelemetry drops a repeat of the same export error for twenty seconds, per
    process, so a test run soon after another test's failing export would see its own
    failure swallowed - by the SDK, before the bridge could ever report it.
    """
    for name, logger in logging.root.manager.loggerDict.items():
        if not name.startswith("opentelemetry") or not isinstance(
            logger, logging.Logger
        ):
            continue
        for log_filter in logger.filters:
            if hasattr(log_filter, "last_log"):
                del log_filter.last_log


class _ExportingThroughout:
    """Install a tracer over `exporter`, and keep asking it to export while installed.

    Without this nothing would reach the exporter until the tracer shut down, after
    the turn - so a stuck export could never be shown not to hold the turn up.
    `settle` stops asking and waits for the export in flight, so its failure is
    reported while the app that reports it is still running.
    """

    def __init__(self, exporter: SpanExporter) -> None:
        self._context = installed_tracer(tracing_settings(), exporter)  # type: ignore[arg-type]
        self._stop = threading.Event()
        self._flusher: threading.Thread | None = None

    def __enter__(self) -> None:
        _forget_earlier_export_failures()
        tracer = self._context.__enter__()

        def _flush_repeatedly() -> None:
            while not self._stop.is_set():
                tracer.flush()
                self._stop.wait(0.01)

        self._flusher = threading.Thread(target=_flush_repeatedly, daemon=True)
        self._flusher.start()

    def settle(self) -> None:
        self._stop.set()
        if self._flusher is not None:
            self._flusher.join()

    def __exit__(self, *exc: object) -> None:
        self.settle()
        self._context.__exit__(None, None, None)


async def test_an_exporter_that_fails_every_export_changes_nothing_and_is_logged(
    seeded_entry: int,
) -> None:
    scenario = _SCENARIOS["merged"]
    exporter = _FailingExporter()

    untraced = await _run(scenario)
    exporting = _ExportingThroughout(exporter)
    traced = await _run(scenario, exporting, settle=exporting.settle)

    _assert_same_turn(untraced, traced)
    assert exporter.attempts > 0
    assert "tracing.export_failed" in [e["event"] for e in traced.tracing_logs]


async def test_an_exporter_that_hangs_does_not_hold_up_the_turn(
    seeded_entry: int,
) -> None:
    scenario = _SCENARIOS["faq"]
    exporter = _StuckExporter()

    untraced = await _run(scenario)
    exporting = _ExportingThroughout(exporter)
    traced = await _run(scenario, exporting, settle=exporting.settle)

    _assert_same_turn(untraced, traced)
    # An export was stuck while the turn was still running...
    assert exporter.started
    assert exporter.started[0] < traced.ended_at
    # ...and the turn finished well inside it, so no export was waited on.
    assert traced.seconds < untraced.seconds + _EXPORT_BLOCK_SECONDS / 2
    assert "tracing.export_failed" in [e["event"] for e in traced.tracing_logs]
