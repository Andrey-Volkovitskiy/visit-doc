"""`node_span()`: one uniform lifecycle record per graph node.

A turn is not a straight line - two specialists can run at once - so "what did this
turn do" is only answerable from a per-node record. Binding `node` into the log
context for the node's duration means every event raised inside it carries the node's
name without any call site passing it, exactly as `turn_id` already works.

Safe under the fan-out: asyncio copies the context into each new task, so concurrent
branches each mutate their own copy and one can never observe or clobber another's.
"""

import asyncio
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from structlog.contextvars import bound_contextvars

from chat.core.logging import get_logger


class NodeResult:
    """A mutable slot for the result payload `node.completed` should carry.

    The payload is only known once the node's work is done, but the span that reports
    it is opened before that work starts - so the node fills this in as it goes and the
    span reads it on the way out.
    """

    def __init__(self) -> None:
        """Start with an empty payload, for the node to fill in as it works."""
        self._payload: dict[str, Any] = {}

    def set(self, **fields: Any) -> None:
        """Merge `fields` into the result payload."""
        self._payload.update(fields)

    @property
    def payload(self) -> dict[str, Any]:
        """Return the payload accumulated so far."""
        return dict(self._payload)


@asynccontextmanager
async def node_span(node: str) -> AsyncGenerator[NodeResult]:
    """Bind `node`, time the body, and emit its lifecycle event on every exit path.

    Yields: a `NodeResult` the node fills in with whatever `node.completed` should
        report.

    Raises: whatever the body raises, re-raised unchanged after `node.failed` is
        emitted - a branch failing is recorded here, but whether the *turn* fails is
        the caller's decision, not this span's.

    A cancelled node emits `node.cancelled` rather than `node.failed`: a turn
    superseded by a newer message is a normal outcome, not an error, and the two must
    stay distinguishable in the log.
    """
    logger = get_logger()
    result = NodeResult()
    started = time.monotonic()
    with bound_contextvars(node=node):
        logger.info("node.started", node=node)
        try:
            yield result
        except asyncio.CancelledError:
            logger.info("node.cancelled", node=node, duration_ms=_elapsed_ms(started))
            raise
        except Exception as exc:
            logger.error(
                "node.failed",
                node=node,
                duration_ms=_elapsed_ms(started),
                error_type=type(exc).__name__,
                error_detail=str(exc),
            )
            raise
        logger.info(
            "node.completed",
            node=node,
            duration_ms=_elapsed_ms(started),
            result=result.payload,
        )


def _elapsed_ms(started: float) -> float:
    """Return milliseconds elapsed since `started`, to two decimal places."""
    return round((time.monotonic() - started) * 1000, 2)
