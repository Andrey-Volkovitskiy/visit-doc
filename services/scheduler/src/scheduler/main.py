"""FastAPI application entrypoint, hosting the gRPC server alongside it."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from scheduler.api.health import router as health_router
from scheduler.api.patients import router as patients_router
from scheduler.api.practitioners import router as practitioners_router
from scheduler.api.specialties import router as specialties_router
from scheduler.core.config import get_settings
from scheduler.core.logging import configure_logging
from scheduler.grpc.server import start_server, stop_server


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Run the `grpc.aio` server for as long as the HTTP app is serving.

    Both surfaces serve the same domain objects over the same session factory, so they
    share one process and one event loop rather than being split into two deployables.
    `lifespan` already owns startup/shutdown ordering, which is exactly where a gRPC
    server's `start()`/`stop(grace)` belongs - a shutdown stops accepting new calls and
    lets in-flight ones finish before the process exits.
    """
    settings = get_settings()
    server = await start_server(settings)
    try:
        yield
    finally:
        await stop_server(server)


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    configure_logging(get_settings())
    app = FastAPI(title="VisitDoc — Scheduling", lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(specialties_router)
    app.include_router(practitioners_router)
    app.include_router(patients_router)
    return app


app = create_app()


def main() -> None:
    """Run the app with uvicorn (entrypoint for `python -m scheduler.main`)."""
    uvicorn.run(app, host="0.0.0.0", port=get_settings().SCHEDULER_HTTP_PORT)


if __name__ == "__main__":
    main()
