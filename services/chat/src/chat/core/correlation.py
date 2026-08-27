"""Correlation-ID binding: `turn_id` (chat) / `operation_id` (FAQ management).

Bound via `structlog.contextvars`, which is scoped to the current asyncio task -
matching FastAPI's per-request task model, so concurrent requests never see each
other's bound value.
"""

import time
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

from structlog.contextvars import bound_contextvars
from ulid import ULID

# When the currently-bound turn started. A plain `ContextVar`, not a bound log field:
# it is read once, to measure the turn, rather than rendered on every line. Like
# `structlog.contextvars`, it is scoped to the current asyncio task, so concurrent
# turns never read each other's start.
_turn_started: ContextVar[float] = ContextVar("turn_started")


@contextmanager
def bind_turn_id() -> Generator[str]:
    """Bind a fresh `turn_id` for the duration of the context.

    Turn means a single patient message and the assistant reply it produces, including
    any intermediate steps (embedding, retrieval, groundedness check, generation). A
    turn's `turn_id` is logged on every log line for that turn, so a log reader can
    tell which lines belong to the same turn.

    A chat turn's `turn_id` and a FAQ operation's `operation_id` are mutually
    exclusive - this only ever binds `turn_id`.
    """
    turn_id = str(ULID())
    token = _turn_started.set(time.monotonic())
    try:
        with bound_contextvars(turn_id=turn_id):
            yield turn_id
    finally:
        _turn_started.reset(token)


def turn_elapsed_ms() -> float | None:
    """Return milliseconds since this turn's `turn_id` was bound.

    Returns: the elapsed milliseconds, or None when no turn is bound - which is not a
        duration of zero but the absence of one, and is reported by leaving the field
        off the log line rather than logging it as null.
    """
    started = _turn_started.get(None)
    if started is None:
        return None
    return round((time.monotonic() - started) * 1000, 2)


@contextmanager
def bind_operation_id() -> Generator[str]:
    """Bind a fresh `operation_id` for the duration of the context.

    Operation means a single FAQ management action, such as creating or updating a FAQ
    entry. An operation's `operation_id` is logged on every log line for that
    operation, so a log reader can tell which lines belong to the same operation.
    """
    operation_id = str(ULID())
    with bound_contextvars(operation_id=operation_id):
        yield operation_id
