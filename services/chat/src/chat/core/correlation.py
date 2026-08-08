"""Correlation-ID binding: `turn_id` (chat) / `operation_id` (FAQ management).

Bound via `structlog.contextvars`, which is scoped to the current asyncio task -
matching FastAPI's per-request task model, so concurrent requests never see each
other's bound value (FR-006, FR-021).
"""

from collections.abc import Generator
from contextlib import contextmanager

from structlog.contextvars import bound_contextvars
from ulid import ULID


@contextmanager
def bind_turn_id() -> Generator[str]:
    """Bind a fresh `turn_id` for the duration of the context (FR-006).

    Turn means a single patient message and the assistant reply it produces, including
    any intermediate steps (embedding, retrieval, groundedness check, generation). A
    turn's `turn_id` is logged on every log line for that turn, so a log reader can
    tell which lines belong to the same turn.

    A chat turn's `turn_id` and a FAQ operation's `operation_id` are mutually
    exclusive (data-model.md) - this only ever binds `turn_id`.
    """
    turn_id = str(ULID())
    with bound_contextvars(turn_id=turn_id):
        yield turn_id


@contextmanager
def bind_operation_id() -> Generator[str]:
    """Bind a fresh `operation_id` for the duration of the context (FR-021).

    Operation means a single FAQ management action, such as creating or updating a FAQ
    entry. An operation's `operation_id` is logged on every log line for that
    operation, so a log reader can tell which lines belong to the same operation.
    """
    operation_id = str(ULID())
    with bound_contextvars(operation_id=operation_id):
        yield operation_id
