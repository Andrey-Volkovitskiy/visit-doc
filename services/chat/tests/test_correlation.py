import asyncio

import structlog
from chat.core.correlation import bind_operation_id, bind_turn_id
from structlog.testing import capture_logs


def test_bind_turn_id_present_only_inside_its_context() -> None:
    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        with bind_turn_id():
            structlog.get_logger().info("turn.message_received")
        structlog.get_logger().info("outside.context")

    assert "turn_id" in logs[0]
    assert "turn_id" not in logs[1]


def test_bind_operation_id_present_only_inside_its_context() -> None:
    with (
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
        bind_operation_id(),
    ):
        structlog.get_logger().info("faq.entry_created")

    assert "operation_id" in logs[0]
    assert "turn_id" not in logs[0]


def test_turn_id_and_operation_id_never_both_bound() -> None:
    with (
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
        bind_turn_id(),
    ):
        structlog.get_logger().info("turn.message_received")

    assert "operation_id" not in logs[0]


async def test_concurrent_turn_ids_never_leak_between_tasks() -> None:
    # capture_logs isn't task-safe (it mutates global structlog config), so this
    # asserts directly on the per-task contextvars snapshot instead.
    seen: dict[str, str] = {}

    async def run(name: str) -> None:
        with bind_turn_id() as turn_id:
            await asyncio.sleep(0)
            assert structlog.contextvars.get_contextvars()["turn_id"] == turn_id
            seen[name] = turn_id

    await asyncio.gather(run("a"), run("b"))

    assert seen["a"] != seen["b"]
