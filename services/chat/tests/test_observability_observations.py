"""The observation API every call site uses, and how each outcome is recorded.

A step that completes, fails or is cancelled has to say which in the trace:
OpenTelemetry alone records a cancellation as nothing at all, so a superseded turn would
read as a successful one. The failure's text is redacted before it is recorded, and the
API is a pass-through when no tracer is installed - the body always runs.
"""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from anthropic.types import Usage
from chat import observability
from chat.observability import (
    UNTRACED,
    TraceDirective,
    generation,
    record,
    step,
    tool_call,
    turn_trace,
)
from langfuse import Langfuse
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from shared_logging import LogLevel
from structlog.testing import capture_logs

from .conftest import finished_spans, installed_tracer, tracing_settings

_LEVEL = "langfuse.observation.level"
_STATUS = "langfuse.observation.status_message"
_OUTPUT = "langfuse.observation.output"
_INPUT = "langfuse.observation.input"
_TYPE = "langfuse.observation.type"


def _only(exporter: InMemorySpanExporter, name: str) -> ReadableSpan:
    (span,) = finished_spans(exporter)[name]
    return span


def _attributes(span: ReadableSpan) -> dict[str, Any]:
    return dict(span.attributes or {})


def test_a_completed_step_is_recorded_at_the_default_level(
    span_exporter: InMemorySpanExporter,
) -> None:
    with step("faq.verdict"):
        pass

    attributes = _attributes(_only(span_exporter, "faq.verdict"))
    assert attributes[_LEVEL] == "DEFAULT"
    assert _STATUS not in attributes
    assert attributes[_TYPE] == "span"


def test_a_step_that_raises_is_an_error_with_its_redacted_text_and_re_raises() -> None:
    exporter = InMemorySpanExporter()
    settings = tracing_settings(ANTHROPIC_API_KEY="sk-ant-0bs3rv3d-k3y")

    with installed_tracer(settings, exporter):
        with pytest.raises(ValueError, match="boom"), step("faq.embed"):
            raise ValueError("boom sk-ant-0bs3rv3d-k3y")

        span = _only(exporter, "faq.embed")

    attributes = _attributes(span)
    assert attributes[_LEVEL] == "ERROR"
    assert attributes[_STATUS] == "ValueError: boom ***REDACTED***"
    # The failure is recorded by level and message, never as an exception event: an
    # event is out of reach of the export mask, and carries the raw text and stack.
    assert span.events == ()


async def test_a_cancelled_step_is_a_warning_marked_cancelled_and_re_raises(
    span_exporter: InMemorySpanExporter,
) -> None:
    started = asyncio.Event()

    async def _body() -> None:
        with step("handle_booking"):
            started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(_body())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    attributes = _attributes(_only(span_exporter, "handle_booking"))
    assert attributes[_LEVEL] == "WARNING"
    assert attributes[_STATUS] == "cancelled"


def test_a_warning_sets_the_level_and_status_and_the_step_keeps_it(
    span_exporter: InMemorySpanExporter,
) -> None:
    with step("faq.rerank") as observed:
        observed.warn("timeout")

    attributes = _attributes(_only(span_exporter, "faq.rerank"))
    assert attributes[_LEVEL] == "WARNING"
    assert attributes[_STATUS] == "timeout"


def test_record_logs_the_event_with_exactly_the_payload_and_outputs_it(
    span_exporter: InMemorySpanExporter,
) -> None:
    payload = {"kept": 2, "floor": 0.25, "candidates": [{"entry_id": 7, "score": 0.4}]}

    with capture_logs() as logs, step("faq.similarity_gate") as observed:
        record(observed, "faq.similarity_gate", payload)

    (entry,) = [log for log in logs if log["event"] == "faq.similarity_gate"]
    assert entry["log_level"] == "info"
    assert {k: v for k, v in entry.items() if k not in ("event", "log_level")} == (
        payload
    )
    exported = _attributes(_only(span_exporter, "faq.similarity_gate"))
    assert json.loads(exported[_OUTPUT]) == payload


def test_record_logs_at_the_level_asked_for(
    span_exporter: InMemorySpanExporter,
) -> None:
    with capture_logs() as logs, step("faq.rerank") as observed:
        record(
            observed,
            "faq.reranking_unavailable",
            {"reason": "timeout"},
            level=LogLevel.ERROR,
        )

    (entry,) = logs
    assert entry["log_level"] == "error"


def test_record_still_logs_when_nothing_is_traced() -> None:
    with capture_logs() as logs, step("faq.verdict") as observed:
        record(observed, "faq.verdict", {"verdict": "answered"})

    assert logs == [
        {"event": "faq.verdict", "log_level": "info", "verdict": "answered"}
    ]


async def test_children_of_concurrent_tasks_nest_under_the_enclosing_step(
    span_exporter: InMemorySpanExporter,
) -> None:
    async def _child(name: str) -> None:
        with step(name):
            await asyncio.sleep(0)

    with step("answer_faq"):
        await asyncio.gather(_child("request[0]"), _child("request[1]"))

    spans = finished_spans(span_exporter)
    (parent,) = spans["answer_faq"]
    assert parent.context is not None
    for name in ("request[0]", "request[1]"):
        (child,) = spans[name]
        assert child.parent is not None
        assert child.parent.span_id == parent.context.span_id


def test_the_body_runs_and_nothing_is_recorded_with_no_tracer_installed() -> None:
    ran = []

    with step("faq.verdict") as observed:
        observed.set_output({"verdict": "answered"})
        observed.warn("never seen")
        ran.append(True)

    assert ran == [True]
    assert not observed.recording


def test_a_turn_trace_is_seeded_from_the_turn_id_and_carries_the_trace_attributes(
    span_exporter: InMemorySpanExporter,
) -> None:
    turn_id = "01K5TURN0000000000000000AB"

    with turn_trace(
        turn_id,
        chat_id="01K5CHAT0000000000000000AB",
        session_id="01K5SESS0000000000000000AB",
        directive=TraceDirective(),
        input="Is the clinic open on Saturday?",
    ) as root:
        with step("classify_intent"):
            pass
        recording = root.recording
        trace_id = root.trace_id

    assert recording
    assert trace_id == Langfuse.create_trace_id(seed=turn_id)
    spans = finished_spans(span_exporter)
    (root_span,) = spans["turn"]
    (child,) = spans["classify_intent"]
    assert root_span.context is not None
    assert format(root_span.context.trace_id, "032x") == trace_id
    for span in (root_span, child):
        attributes = _attributes(span)
        assert attributes["session.id"] == "01K5CHAT0000000000000000AB"
        # A digest of the session id, never the id itself: the id is the session's
        # cookie value, and a trace is read by whoever can open the Langfuse project.
        assert (
            attributes["user.id"]
            == hashlib.sha256(b"01K5SESS0000000000000000AB").hexdigest()[:32]
        )
        assert attributes["langfuse.trace.name"] == "turn"
        assert attributes["langfuse.environment"] == "development"
        assert attributes["langfuse.trace.metadata.turn_id"] == turn_id
        assert "langfuse.trace.metadata.eval_run_id" not in attributes
    assert _attributes(root_span)[_INPUT] == "Is the clinic open on Saturday?"


def test_an_eval_turn_is_filed_under_the_eval_environment_with_its_ids(
    span_exporter: InMemorySpanExporter,
) -> None:
    directive = TraceDirective(
        eval_run_id="01K5RUN00000000000000000AB", eval_case_id="G-a-01"
    )

    with turn_trace(
        "01K5TURN0000000000000000CD",
        chat_id="c",
        session_id="s",
        directive=directive,
        input="hi",
    ):
        pass

    attributes = _attributes(_only(span_exporter, "turn"))
    assert attributes["langfuse.environment"] == "eval"
    assert attributes["langfuse.trace.metadata.eval_run_id"] == (
        "01K5RUN00000000000000000AB"
    )
    assert attributes["langfuse.trace.metadata.eval_case_id"] == "G-a-01"


def test_a_turn_trace_reports_it_is_not_recording_when_nothing_traces_it() -> None:
    with turn_trace(
        "01K5TURN0000000000000000EF",
        chat_id="c",
        session_id="s",
        directive=TraceDirective(),
        input="hi",
    ) as root:
        pass

    assert not root.recording
    # No id for a trace that was never exported: a well-formed one would be a claim.
    assert root.trace_id is None


def test_a_generation_records_its_model_input_output_and_usage(
    span_exporter: InMemorySpanExporter,
) -> None:
    with (
        step("classify_intent"),
        generation(
            "classify_intent.model",
            model="claude-haiku-4-5-20251001",
            input={"system": "Classify.", "messages": [{"role": "user"}]},
            model_parameters={"max_tokens": 512},
        ) as observed,
    ):
        observed.record_completion(
            output=[{"type": "text", "text": "{}"}],
            usage=Usage(input_tokens=120, output_tokens=40, cache_read_input_tokens=8),
            stop_reason="end_turn",
        )

    attributes = _attributes(_only(span_exporter, "classify_intent.model"))
    assert attributes[_TYPE] == "generation"
    assert attributes["langfuse.observation.model.name"] == "claude-haiku-4-5-20251001"
    assert json.loads(attributes["langfuse.observation.model.parameters"]) == {
        "max_tokens": 512
    }
    assert json.loads(attributes[_INPUT]) == {
        "system": "Classify.",
        "messages": [{"role": "user"}],
    }
    assert json.loads(attributes[_OUTPUT]) == [{"type": "text", "text": "{}"}]
    assert json.loads(attributes["langfuse.observation.usage_details"]) == {
        "input": 120,
        "output": 40,
        "cache_read_input_tokens": 8,
    }
    assert attributes[_LEVEL] == "DEFAULT"


def test_a_generation_leaves_out_a_cache_count_the_provider_did_not_report(
    span_exporter: InMemorySpanExporter,
) -> None:
    with generation(
        "small_talk.model", model="m", input={}, model_parameters={}
    ) as observed:
        observed.record_completion(
            output="hi",
            usage=Usage(input_tokens=3, output_tokens=4),
            stop_reason="end_turn",
        )

    usage = _attributes(_only(span_exporter, "small_talk.model"))[
        "langfuse.observation.usage_details"
    ]
    assert json.loads(usage) == {"input": 3, "output": 4}


def test_a_generation_stopped_by_max_tokens_is_a_warning(
    span_exporter: InMemorySpanExporter,
) -> None:
    with generation(
        "answer_faq.model", model="m", input={}, model_parameters={}
    ) as observed:
        observed.record_completion(
            output="cut",
            usage=Usage(input_tokens=3, output_tokens=4),
            stop_reason="max_tokens",
        )

    attributes = _attributes(_only(span_exporter, "answer_faq.model"))
    assert attributes[_LEVEL] == "WARNING"
    assert attributes[_STATUS] == "max_tokens"


def test_a_streamed_generation_records_when_its_first_token_arrived(
    span_exporter: InMemorySpanExporter,
) -> None:
    first_token = datetime(2026, 9, 22, 9, 0, 1, tzinfo=UTC)

    with generation(
        "compose_answer.model", model="m", input={}, model_parameters={}
    ) as observed:
        observed.record_completion(
            output="hello",
            usage=Usage(input_tokens=3, output_tokens=4),
            stop_reason="end_turn",
            completion_start_time=first_token,
        )

    attributes = _attributes(_only(span_exporter, "compose_answer.model"))
    completion_start = attributes["langfuse.observation.completion_start_time"]
    assert json.loads(completion_start).startswith("2026-09-22T09:00:01")


def test_a_streamed_generation_records_its_first_marked_token_not_a_later_one(
    span_exporter: InMemorySpanExporter,
) -> None:
    first, later = (
        datetime(2026, 9, 22, 9, 0, 1, tzinfo=UTC),
        datetime(2026, 9, 22, 9, 0, 7, tzinfo=UTC),
    )

    with (
        generation(
            "small_talk.model", model="m", input={}, model_parameters={}
        ) as observed,
        patch("chat.observability.datetime") as clock,
    ):
        clock.now.side_effect = [first, later]
        observed.mark_token()
        observed.mark_token()
        observed.record_completion(
            output="hello",
            usage=Usage(input_tokens=3, output_tokens=4),
            stop_reason="end_turn",
        )

    attributes = _attributes(_only(span_exporter, "small_talk.model"))
    completion_start = attributes["langfuse.observation.completion_start_time"]
    assert json.loads(completion_start).startswith("2026-09-22T09:00:01")


def test_an_untraced_observation_never_builds_what_it_would_record(
    span_exporter: InMemorySpanExporter,
) -> None:
    # The sampler drops every span of an untraced turn; copying and redacting the
    # payloads those spans would have carried is work nothing reads.
    token = UNTRACED.set(True)
    try:
        with (
            patch(
                "chat.observability._recordable", wraps=observability._recordable
            ) as recordable,
            step("faq.search", input={"query": "q"}) as observed,
        ):
            observed.set_output({"pool_returned": 0})
            observed.set_metadata(kept=[])
    finally:
        UNTRACED.reset(token)

    recordable.assert_not_called()
    assert finished_spans(span_exporter) == {}


def test_a_tool_call_is_a_tool_observation_with_its_arguments_as_input(
    span_exporter: InMemorySpanExporter,
) -> None:
    with tool_call("list_practitioners", {"specialty": "cardiology"}) as observed:
        observed.set_output({"status": "ok"})

    attributes = _attributes(_only(span_exporter, "tool:list_practitioners"))
    assert attributes[_TYPE] == "tool"
    assert json.loads(attributes[_INPUT]) == {"specialty": "cardiology"}
    assert json.loads(attributes[_OUTPUT]) == {"status": "ok"}


def test_the_api_follows_the_installed_tracer_not_the_sdks_global_client() -> None:
    # `get_client()` hands back a disabled client once a process holds two, which is
    # exactly the state a test suite - and a reloaded dev server - is in.
    first, second = InMemorySpanExporter(), InMemorySpanExporter()

    with installed_tracer(tracing_settings(), first):
        with installed_tracer(tracing_settings(), second):
            with step("inner"):
                pass
            inner = finished_spans(second)
        assert observability.installed() is None

    assert list(inner) == ["inner"]
    assert first.get_finished_spans() == ()
