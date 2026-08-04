"""FastAPI application entrypoint."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from chat.api.chat import router as chat_router
from chat.api.faq import router as faq_router
from chat.core.config import get_settings
from chat.repositories.qdrant_repository import create_client, ensure_collection


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Ensure the faq_chunks collection exists; share the client on state."""
    client = create_client(get_settings())
    await ensure_collection(client)
    app.state.qdrant_client = client
    yield
    await client.close()


def create_app() -> FastAPI:
    """Build the FastAPI application. Routers are registered as their features land."""
    app = FastAPI(title="VisitDoc — Grounded FAQ Chat", lifespan=lifespan)
    app.include_router(chat_router)
    app.include_router(faq_router)
    return app


app = create_app()


def main() -> None:
    """Run the app with uvicorn (entrypoint for `python -m chat.main`)."""
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
