import asyncio

import pytest
import structlog
from chat.agent.node_logging import node_span
from chat.core.logging import get_logger
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs


async def test_a_normal_body_emits_started_then_completed() -> None:
    with capture_logs() as logs:
        async with node_span("answer_faq"):
            pass

    assert [entry["event"] for entry in logs] == ["node.started", "node.completed"]
    assert all(entry["node"] == "answer_faq" for entry in logs)


async def test_completed_carries_the_result_the_node_filled_in() -> None:
    with capture_logs() as logs:
        async with node_span("handle_booking") as result:
            result.set(outcome="booked", iterations=2)

    completed = next(e for e in logs if e["event"] == "node.completed")
    assert completed["result"] == {"outcome": "booked", "iterations": 2}


async def test_completed_carries_a_duration() -> None:
    with capture_logs() as logs:
        async with node_span("answer_faq"):
            pass

    completed = next(e for e in logs if e["event"] == "node.completed")
    assert completed["duration_ms"] >= 0


async def test_a_raising_body_emits_failed_and_re_raises() -> None:
    with capture_logs() as logs, pytest.raises(RuntimeError):
        async with node_span("handle_booking"):
            raise RuntimeError("boom")

    failed = next(e for e in logs if e["event"] == "node.failed")
    assert failed["error_type"] == "RuntimeError"
    assert failed["error_detail"] == "boom"
    assert "node.completed" not in [e["event"] for e in logs]


async def test_a_cancelled_body_emits_cancelled_not_failed() -> None:
    with capture_logs() as logs, pytest.raises(asyncio.CancelledError):
        async with node_span("answer_faq"):
            raise asyncio.CancelledError

    events = [entry["event"] for entry in logs]
    assert "node.cancelled" in events
    assert "node.failed" not in events
    assert "node.completed" not in events


async def test_events_raised_inside_the_span_inherit_the_node_name() -> None:
    with capture_logs([merge_contextvars]) as logs:
        async with node_span("answer_faq"):
            get_logger().info("faq.retrieved", chunk_count=3)

    retrieved = next(e for e in logs if e["event"] == "faq.retrieved")
    assert retrieved["node"] == "answer_faq"


async def test_the_node_binding_does_not_outlive_the_span() -> None:
    structlog.contextvars.clear_contextvars()
    async with node_span("answer_faq"):
        pass

    with capture_logs([merge_contextvars]) as logs:
        get_logger().info("turn.completed")

    assert "node" not in logs[0]


async def test_concurrent_spans_do_not_see_each_others_node_binding() -> None:
    structlog.contextvars.clear_contextvars()

    async def run(name: str) -> None:
        async with node_span(name):
            # Yield the loop mid-span, so both nodes are open at once and either
            # could clobber the other's binding if they shared one context.
            await asyncio.sleep(0)
            get_logger().info("probe", probe_for=name)

    with capture_logs([merge_contextvars]) as logs:
        await asyncio.gather(run("answer_faq"), run("handle_booking"))

    probes = {
        entry["probe_for"]: entry["node"] for entry in logs if "probe_for" in entry
    }
    assert probes == {"answer_faq": "answer_faq", "handle_booking": "handle_booking"}
