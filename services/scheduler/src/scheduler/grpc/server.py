"""Construction and lifecycle of the `grpc.aio` server.

Kept apart from `main.py` so the wiring - which servicer, which interceptors, which
port - is readable in one place, and so a test can build a server against an in-process
channel with the same wiring the real process uses.
"""

import grpc
from shared_proto.scheduling.v1 import scheduling_pb2_grpc

from scheduler.core.config import Settings
from scheduler.core.logging import get_logger
from scheduler.grpc.interceptors import LoggingInterceptor
from scheduler.grpc.servicer import SchedulingServicer

# How long a shutdown waits for in-flight calls to finish before dropping them. Every
# call this service serves is a short database round trip, so a few seconds is ample
# and a hung one should not hold the process open.
_GRACE_PERIOD_SECONDS = 5.0


def create_server(settings: Settings) -> grpc.aio.Server:
    """Build the gRPC server with the scheduling servicer and its interceptor bound.

    The returned server is not started; the caller owns its lifecycle.
    """
    server = grpc.aio.server(interceptors=[LoggingInterceptor()])
    # protoc emits this registration helper without annotations, and there is no
    # generated stub for the `_grpc` module to give it any - the one call site pays for
    # that rather than the whole module opting out of strict typing.
    scheduling_pb2_grpc.add_SchedulingServicer_to_server(  # type: ignore[no-untyped-call]
        SchedulingServicer(), server
    )
    server.add_insecure_port(f"[::]:{settings.SCHEDULER_GRPC_PORT}")
    return server


async def start_server(settings: Settings) -> grpc.aio.Server:
    """Build and start the gRPC server, returning it for later shutdown."""
    server = create_server(settings)
    await server.start()
    get_logger().info("grpc.started", port=settings.SCHEDULER_GRPC_PORT)
    return server


async def stop_server(server: grpc.aio.Server) -> None:
    """Stop `server`, letting in-flight calls finish within the grace period."""
    await server.stop(_GRACE_PERIOD_SECONDS)
    get_logger().info("grpc.stopped")
