"""FastAPI application entrypoint."""

from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager

import aiohttp
import uvicorn
from anthropic import AsyncAnthropic
from fastapi import FastAPI
from voyageai.client_async import AsyncClient

from chat.api.chat import router as chat_router
from chat.api.faq import router as faq_router
from chat.core.config import get_settings
from chat.core.logging import configure_logging, get_logger
from chat.repositories.qdrant_repository import create_client, ensure_collection


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Ensure the faq_chunks collection exists; share the Qdrant, Anthropic, and Voyage
    clients on state so every request reuses the same connection pool instead of paying
    fresh HTTP client setup cost per request.

    Qdrant/Anthropic get pooling for free just by reusing the client instance. Voyage's
    `AsyncClient` doesn't: it opens and closes a brand-new `aiohttp.ClientSession` on
    every `embed()` call unless handed a shared session via the `voyageai.aiosession`
    contextvar, so a plain shared `aiohttp.ClientSession` is created and stored on state
    here too - `chat.api.dependencies.get_voyage_client` binds it into the contextvar at
    the start of each request (setting it once here wouldn't reliably reach each
    request's own asyncio task).

    Each client's/session's cleanup is registered on an `AsyncExitStack` right after
    construction, so a later step failing during startup - or one close() raising during
    shutdown - can never leave an earlier one's connections unclosed.

    Raises:
        Exception: propagated from `ensure_collection` if the Qdrant collection can't be
            created or verified during startup.
    """
    settings = get_settings()
    async with AsyncExitStack() as stack:
        client = create_client(settings)
        stack.push_async_callback(client.close)
        try:
            await ensure_collection(client)
        except Exception as exc:
            get_logger().critical(
                "critical.dependency_unreachable",
                dependency="qdrant",
                error_detail=str(exc),
            )
            raise

        anthropic_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        stack.push_async_callback(anthropic_client.close)
        voyage_client = AsyncClient(api_key=settings.VOYAGE_API_KEY)
        voyage_session = aiohttp.ClientSession()
        stack.push_async_callback(voyage_session.close)

        app.state.qdrant_client = client
        app.state.anthropic_client = anthropic_client
        app.state.voyage_client = voyage_client
        app.state.voyage_session = voyage_session
        yield


def create_app() -> FastAPI:
    """Build the FastAPI application. Routers are registered as their features land."""
    configure_logging(get_settings())
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
