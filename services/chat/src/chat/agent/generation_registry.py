"""Cancel-and-restart in-flight generation registry.

A module-level `dict[chat_id, (turn_id, asyncio.Task)]`, not a database table -
purely in-memory, process-local coordination. Never survives a process restart, and
doesn't need to: a restart simply means no generation is in-flight for any chat,
which is also the correct starting state.
"""

import asyncio
from contextlib import suppress

from chat.core.logging import get_logger

_in_flight: dict[str, tuple[str, "asyncio.Task[None]"]] = {}


async def register_and_cancel_previous(
    chat_id: str, turn_id: str, task: "asyncio.Task[None]"
) -> None:
    """Cancel any task already registered for `chat_id`, then register `task`.

    Registers `task` as current before awaiting the previous task's cancellation
    (not after) - `task` is already running the moment `asyncio.create_task` schedules
    it, so registering it first ensures `clear_if_current` never sees it as stale, even
    if it completes while the previous task's cancellation is still unwinding. Awaits
    that cancellation before returning regardless, so the previous pipeline has fully
    unwound - nothing partial left running - before the caller proceeds. Logs
    `turn.cancelled` when a previous turn actually gets superseded like this, so a
    patient seeing no reply to an earlier message is traceable to this, not silently
    indistinguishable from the message simply never having been sent.
    """
    previous = _in_flight.get(chat_id)
    _in_flight[chat_id] = (turn_id, task)
    if previous is not None and not previous[1].done():
        previous_turn_id, previous_task = previous
        get_logger().info(
            "turn.cancelled",
            chat_id=chat_id,
            cancelled_turn_id=previous_turn_id,
            superseding_turn_id=turn_id,
        )
        previous_task.cancel()
        with suppress(asyncio.CancelledError):
            await previous_task


def clear_if_current(chat_id: str, task: "asyncio.Task[None]") -> bool:
    """Remove `task` from the registry for `chat_id`, only if it's still current.

    Returns: True if `task` was current (now cleared), False if it had already been
        superseded by a newer registration.
    """
    current = _in_flight.get(chat_id)
    if current is not None and current[1] is task:
        del _in_flight[chat_id]
        return True
    return False
