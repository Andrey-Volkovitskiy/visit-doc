"""Correlation-ID binding for a handler's lifetime.

The chat service scopes a `turn_id` to the asyncio task handling one patient turn and
sends it along as `x-turn-id` gRPC metadata. Re-binding it here is what lets one turn's
chat-side and scheduler-side log lines be read together on a single key.

Bound via `structlog.contextvars`, which is scoped to the current asyncio task, so
concurrent handlers never see each other's bound value.
"""

from collections.abc import Generator
from contextlib import contextmanager

from shared_proto.metadata import TURN_ID_METADATA_KEY
from structlog.contextvars import bound_contextvars

__all__ = ["TURN_ID_METADATA_KEY", "bind_turn_id"]


@contextmanager
def bind_turn_id(turn_id: str | None) -> Generator[None]:
    """Bind `turn_id` for the duration of the context.

    A `None` turn id (a caller that sent no metadata, e.g. `grpcurl`) binds nothing
    rather than binding a placeholder, so an absent correlation id is visibly absent in
    the log line instead of looking like a real one.
    """
    if turn_id is None:
        yield
        return
    with bound_contextvars(turn_id=turn_id):
        yield
