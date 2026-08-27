"""Server-side gRPC interceptor: correlation binding and per-call log lines.

One interceptor rather than a decorator per handler, so a new RPC is observable the
moment it is registered and cannot be added without its `rpc.received`/`rpc.completed`
pair.
"""

import time
from collections.abc import Awaitable, Callable
from typing import Any

import grpc

from scheduler.core.correlation import TURN_ID_METADATA_KEY, bind_turn_id
from scheduler.core.logging import get_logger
from scheduler.grpc.converters import ConversionError


def _method_name(handler_call_details: grpc.HandlerCallDetails) -> str:
    """Return the bare RPC name from a fully-qualified `/package.Service/Method`."""
    method = getattr(handler_call_details, "method", "") or ""
    return method.rsplit("/", 1)[-1]


def _turn_id(handler_call_details: grpc.HandlerCallDetails) -> str | None:
    """Return the caller's `x-turn-id` metadata value, or None if it sent none."""
    metadata = getattr(handler_call_details, "invocation_metadata", None) or ()
    for key, value in metadata:
        if key == TURN_ID_METADATA_KEY:
            return str(value)
    return None


# `grpc.aio.ServerInterceptor` and `RpcMethodHandler` are generic in the type stubs but
# not subscriptable at runtime, so the base class is written bare and this alias is a
# PEP 695 `type` statement - lazily evaluated, so the subscript is never executed.
type _Handler = grpc.RpcMethodHandler[Any, Any]


class LoggingInterceptor(grpc.aio.ServerInterceptor):
    """Bind the caller's turn id and emit one `rpc.received`/`rpc.completed` pair.

    The turn id is bound for the whole handler, so every event the handler emits joins
    to the chat-side lines of the same turn without any handler passing it along.
    """

    async def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], Awaitable[_Handler | None]],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> _Handler | None:
        """Wrap the resolved handler in correlation binding and timing."""
        handler = await continuation(handler_call_details)
        if handler is None or not handler.unary_unary:
            return handler

        method = _method_name(handler_call_details)
        turn_id = _turn_id(handler_call_details)
        inner = handler.unary_unary

        async def wrapper(request: Any, context: Any) -> Any:
            """Run the handler with `turn_id` bound, logging entry and exit."""
            logger = get_logger()
            with bind_turn_id(turn_id):
                logger.info(
                    "rpc.received",
                    method=method,
                    session_id=getattr(request, "session_id", None),
                )
                started = time.monotonic()
                try:
                    response = await inner(request, context)
                except ConversionError as exc:
                    # Owned here rather than repeated in every handler: a field the
                    # contract cannot read is a caller defect wherever it appears, so a
                    # newly added RPC answers it correctly by default rather than by
                    # remembering to.
                    logger.error(
                        "rpc.completed",
                        method=method,
                        status=grpc.StatusCode.INVALID_ARGUMENT.name,
                        duration_ms=round((time.monotonic() - started) * 1000, 2),
                    )
                    await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
                except Exception:
                    # `context.abort()` raises, so a handler rejecting a malformed
                    # request arrives here too. Reading the status back off the context
                    # keeps a caller defect logged as INVALID_ARGUMENT rather than as a
                    # server fault an operator would be paged for; a genuinely uncaught
                    # exception has no status set and completes as UNKNOWN.
                    code = context.code()
                    logger.error(
                        "rpc.completed",
                        method=method,
                        status=(code.name if code else grpc.StatusCode.UNKNOWN.name),
                        duration_ms=round((time.monotonic() - started) * 1000, 2),
                    )
                    raise
                logger.info(
                    "rpc.completed",
                    method=method,
                    status=context.code().name if context.code() else "OK",
                    duration_ms=round((time.monotonic() - started) * 1000, 2),
                )
                return response

        return grpc.unary_unary_rpc_method_handler(
            wrapper,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
