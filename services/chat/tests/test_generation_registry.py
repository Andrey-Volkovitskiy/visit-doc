"""Tests for the in-flight generation registry: cancel-and-restart (research.md §9)."""

import asyncio

from chat.agent.generation_registry import (
    clear_if_current,
    register_and_cancel_previous,
)


async def _noop() -> None:
    pass


async def test_registering_new_task_cancels_previous_for_same_chat() -> None:
    started = asyncio.Event()
    was_cancelled = asyncio.Event()

    async def never_completes() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            was_cancelled.set()
            raise

    first_task = asyncio.create_task(never_completes())
    await started.wait()
    await register_and_cancel_previous("chat-1", "turn-1", first_task)

    second_task = asyncio.create_task(_noop())
    await register_and_cancel_previous("chat-1", "turn-2", second_task)
    await second_task

    assert first_task.cancelled()
    assert was_cancelled.is_set()


async def test_clear_if_current_returns_true_for_the_current_task() -> None:
    task = asyncio.create_task(_noop())
    await register_and_cancel_previous("chat-2", "turn-1", task)
    await task

    assert clear_if_current("chat-2", task) is True


async def test_clear_if_current_returns_false_when_task_was_superseded() -> None:
    stale_task = asyncio.create_task(_noop())
    await register_and_cancel_previous("chat-3", "turn-1", stale_task)
    await stale_task

    newer_task = asyncio.create_task(_noop())
    await register_and_cancel_previous("chat-3", "turn-2", newer_task)
    await newer_task

    assert clear_if_current("chat-3", stale_task) is False
    assert clear_if_current("chat-3", newer_task) is True


async def test_new_task_is_current_even_while_previous_cancellation_is_unwinding() -> (
    None
):
    """Regression: a fast new task must see itself as current via `clear_if_current`
    even if it finishes before the previous task's cancellation has fully unwound -
    registration must happen before that await, not after.
    """
    previous_started = asyncio.Event()
    let_previous_finish_unwinding = asyncio.Event()
    new_task_checked_currency = asyncio.Event()
    result: list[bool] = []

    async def slow_to_unwind() -> None:
        previous_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await let_previous_finish_unwinding.wait()
            raise

    async def fast_new_task() -> None:
        result.append(clear_if_current("chat-4", new_task))
        new_task_checked_currency.set()

    async def unblock_previous_once_checked() -> None:
        await new_task_checked_currency.wait()
        let_previous_finish_unwinding.set()

    previous_task = asyncio.create_task(slow_to_unwind())
    await previous_started.wait()
    await register_and_cancel_previous("chat-4", "turn-1", previous_task)

    new_task = asyncio.create_task(fast_new_task())
    unblock_task = asyncio.create_task(unblock_previous_once_checked())

    await register_and_cancel_previous("chat-4", "turn-2", new_task)
    await unblock_task

    assert result == [True]
