"""Every tool call is an observation in the turn's trace, opened by the registry.

Every call goes through `ToolRegistry.dispatch` - including the roster read the booking
node makes before its first model call, which the loop's own dispatch never sees - so
that one seam is where a tool call is recorded, and a tool added later is covered
without a second edit.
"""

import asyncio
import json
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from chat.agent.escalation import EscalationRequests
from chat.agent.handle_booking import handle_booking
from chat.agent.node_logging import node_span
from chat.agent.tools.registry import (
    Tool,
    ToolArgumentError,
    ToolContext,
    ToolRegistry,
    ToolResult,
    UnknownToolError,
)
from chat.core.config import Settings
from chat.domain.models import Message, MessageSender
from chat.domain.schemas import IntentLabel, RequestSegment
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from .conftest import (
    _mock_text_response,
    _mock_tool_use_response,
    finished_spans,
    is_child_of,
    only_span,
    span_attributes,
    span_output,
)

_LEVEL = "langfuse.observation.level"
_STATUS = "langfuse.observation.status_message"


def _context(patient_id: str | None = "01PATIENT") -> ToolContext:
    return ToolContext(
        channel=MagicMock(),
        settings=MagicMock(spec=Settings),
        session_id="01SESSION",
        patient_id=patient_id,
        local_now=datetime(2026, 8, 17, 8, 0),
    )


def _tool(
    name: str,
    result: ToolResult | None = None,
    *,
    raises: Exception | None = None,
    requires_patient: bool = False,
    started: asyncio.Event | None = None,
    release: asyncio.Event | None = None,
) -> Tool:
    async def _handler(_context: ToolContext, _arguments: dict[str, Any]) -> ToolResult:
        if started is not None:
            started.set()
        if release is not None:
            await release.wait()
        if raises is not None:
            raise raises
        return result if result is not None else {"status": "ok"}

    return Tool(
        name=name,
        description=f"the {name} tool",
        input_schema={"type": "object", "properties": {}},
        handler=_handler,
        requires_patient=requires_patient,
    )


def _registry(*tools: Tool, patient_id: str | None = "01PATIENT") -> ToolRegistry:
    return ToolRegistry(list(tools), _context(patient_id))


async def test_a_dispatch_is_a_tool_observation_of_its_arguments_and_result(
    span_exporter: InMemorySpanExporter,
) -> None:
    result = {"status": "ok", "slots": ["2026-08-18T09:00:00"]}
    registry = _registry(_tool("check_availability", result))

    await registry.dispatch("check_availability", {"practitioner_id": "01P"})

    span = only_span(span_exporter, "tool:check_availability")
    attributes = span_attributes(span)
    assert attributes["langfuse.observation.type"] == "tool"
    assert json.loads(attributes["langfuse.observation.input"]) == {
        "practitioner_id": "01P"
    }
    assert span_output(span) == result
    assert attributes[_LEVEL] == "DEFAULT"


@pytest.mark.parametrize("status", ["unknown", "unavailable"])
async def test_a_result_that_is_no_answer_is_a_warning_naming_its_status(
    span_exporter: InMemorySpanExporter, status: str
) -> None:
    registry = _registry(_tool("book_appointment", {"status": status}))

    await registry.dispatch("book_appointment", {})

    attributes = span_attributes(only_span(span_exporter, "tool:book_appointment"))
    assert attributes[_LEVEL] == "WARNING"
    assert attributes[_STATUS] == status


async def test_a_tool_answered_for_a_chat_with_no_patient_is_a_warning(
    span_exporter: InMemorySpanExporter,
) -> None:
    registry = _registry(
        _tool("list_appointments", requires_patient=True), patient_id=None
    )

    result = await registry.dispatch("list_appointments", {})

    span = only_span(span_exporter, "tool:list_appointments")
    assert span_output(span) == result
    assert span_attributes(span)[_LEVEL] == "WARNING"
    assert span_attributes(span)[_STATUS] == "unavailable"


@pytest.mark.parametrize(
    ("raises", "expected_status"),
    [
        (ToolArgumentError("slot is required"), "ToolArgumentError: slot is required"),
        (RuntimeError("handler blew up"), "RuntimeError: handler blew up"),
    ],
)
async def test_a_handler_that_raises_is_an_error(
    span_exporter: InMemorySpanExporter, raises: Exception, expected_status: str
) -> None:
    registry = _registry(_tool("book_appointment", raises=raises))

    with pytest.raises(type(raises)):
        await registry.dispatch("book_appointment", {})

    attributes = span_attributes(only_span(span_exporter, "tool:book_appointment"))
    assert attributes[_LEVEL] == "ERROR"
    assert attributes[_STATUS] == expected_status


async def test_a_tool_the_registry_does_not_hold_is_an_error(
    span_exporter: InMemorySpanExporter,
) -> None:
    registry = _registry(_tool("check_availability"))

    with pytest.raises(UnknownToolError):
        await registry.dispatch("summon_a_doctor", {})

    attributes = span_attributes(only_span(span_exporter, "tool:summon_a_doctor"))
    assert attributes[_LEVEL] == "ERROR"
    assert attributes[_STATUS] == "UnknownToolError: summon_a_doctor"


async def _booking_turn(
    registry: ToolRegistry, calls: list[list[tuple[str, dict[str, object]]]]
) -> None:
    """Run the booking loop under its node, with the model asking for `calls`."""
    responses = [_mock_tool_use_response(batch) for batch in calls]
    client = MagicMock()
    client.messages.create = AsyncMock(
        side_effect=[*responses, _mock_text_response("Anything else?")]
    )
    async with node_span("handle_booking"):
        async for _ in handle_booking(
            client,
            registry,
            [[Message(sender=MessageSender.PATIENT, content="book me in", id="p1")]],
            patient_name="Ada Lovelace",
            local_now="2026-08-17T08:00:00",
            stream=False,
            segments=[RequestSegment(intent=IntentLabel.BOOKING, text="book me in")],
            escalation=EscalationRequests(),
        ):
            pass


async def test_the_roster_read_is_a_tool_call_before_the_first_model_call(
    span_exporter: InMemorySpanExporter,
) -> None:
    registry = _registry(_tool("list_practitioners", {"practitioners": []}))

    await _booking_turn(registry, [])

    spans = finished_spans(span_exporter)
    (node,) = spans["handle_booking"]
    (roster,) = spans["tool:list_practitioners"]
    (first_model_call,) = spans["handle_booking.model[1]"]
    assert is_child_of(roster, node)
    assert roster.end_time is not None
    assert first_model_call.start_time is not None
    assert roster.end_time <= first_model_call.start_time


async def test_tool_calls_dispatched_together_both_nest_under_the_node(
    span_exporter: InMemorySpanExporter,
) -> None:
    # Each call holds until both have started, which only completes if the loop really
    # does run them at once.
    first_started, second_started = asyncio.Event(), asyncio.Event()
    both_started = asyncio.Event()

    async def _release() -> None:
        await first_started.wait()
        await second_started.wait()
        both_started.set()

    releaser = asyncio.create_task(_release())
    registry = _registry(
        _tool("list_practitioners", {"practitioners": []}),
        _tool(
            "check_availability",
            {"status": "ok"},
            started=first_started,
            release=both_started,
        ),
        _tool(
            "list_appointments",
            {"status": "ok"},
            started=second_started,
            release=both_started,
        ),
    )

    await _booking_turn(
        registry, [[("check_availability", {}), ("list_appointments", {})]]
    )
    await releaser

    spans = finished_spans(span_exporter)
    (node,) = spans["handle_booking"]
    for name in ("tool:check_availability", "tool:list_appointments"):
        (call,) = spans[name]
        assert is_child_of(call, node)
