"""FastAPI application entrypoint."""

from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager

import aiohttp
import uvicorn
from anthropic import AsyncAnthropic
from fastapi import FastAPI
from voyageai.client_async import AsyncClient

from chat.agent.graph import clear_graph_cache
from chat.api.admin import router as admin_router
from chat.api.chats import router as chats_router
from chat.api.console import router as console_router
from chat.api.faq import router as faq_router
from chat.api.turn import router as turn_router
from chat.clients.scheduling import create_channel
from chat.core.config import get_settings
from chat.core.logging import configure_logging, get_logger
from chat.repositories.qdrant_repository import (
    CollectionVectorSizeMismatchError,
    create_client,
    ensure_collection,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Ensure the faq_chunks collection exists, then share the Qdrant, Anthropic, and
    Voyage clients on app state so every request reuses the same connection pool
    instead of paying fresh HTTP client setup cost per request.

    Raises:
        CollectionVectorSizeMismatchError: if Qdrant already holds a collection whose
            vectors are a different width than this build embeds.
        Exception: propagated from `ensure_collection` if the Qdrant collection can't be
            created or verified during startup.

    Qdrant and Anthropic get pooling for free by reusing the client instance. Voyage's
    `AsyncClient` doesn't - it opens a fresh `aiohttp.ClientSession` per call unless
    handed a shared session via the `voyageai.aiosession` contextvar - so a plain shared
    `aiohttp.ClientSession` is also created and stored on state here, bound into that
    contextvar per-request elsewhere and reused by the console's practitioner proxy,
    which is the other thing this service speaks HTTP to. Every object that owns
    connections - the Qdrant client, the Anthropic client, that session, the scheduling
    channel - has its cleanup registered on an `AsyncExitStack` right after
    construction, so a later step failing during startup, or one close() raising during
    shutdown, can never leave an earlier one's connections unclosed. The two Voyage
    clients are not among them: they own nothing to close, borrowing that shared session
    for the duration of each call.
    """
    settings = get_settings()
    async with AsyncExitStack() as stack:
        qdrant_client = create_client(settings)
        stack.push_async_callback(qdrant_client.close)
        try:
            await ensure_collection(qdrant_client)
        except CollectionVectorSizeMismatchError as exc:
            # Not an outage, and filing it as one sends an operator to a datastore that
            # is up and answering. Qdrant replied; what it said is that the collection
            # it already holds cannot store this build's vectors, which is fixed by
            # recreating it and re-indexing, not by restarting anything.
            get_logger().critical(
                "critical.qdrant_collection_mismatch",
                dependency="qdrant",
                error_detail=str(exc),
            )
            raise
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
        # Reranking gets its own client, pinning retries off rather than inheriting
        # them: its deadline has to bound the whole call, and `timeout x attempts` is
        # exactly the wall-clock that cap exists to prevent. The SDK happens to default
        # to no retries today, so this pins a property rather than changing one - which
        # is the point of a second object, since the embedding client above is free to
        # start retrying (its failures fail the turn) without dragging this deadline
        # along with it.
        rerank_client = AsyncClient(api_key=settings.VOYAGE_API_KEY, max_retries=0)
        # One pool, two callers: Voyage's client is handed it through its contextvar,
        # and the console's practitioner proxy sends its own requests over it. A second
        # session would be a second set of connections for no reason.
        http_session = aiohttp.ClientSession()
        stack.push_async_callback(http_session.close)

        # A gRPC channel is a connection pool, so it is built once and shared like the
        # clients above. Deliberately *not* connected eagerly: the scheduler being down
        # must never stop this service from starting and answering FAQ questions.
        scheduling_channel = create_channel(settings)
        stack.push_async_callback(scheduling_channel.close)

        app.state.qdrant_client = qdrant_client
        app.state.anthropic_client = anthropic_client
        app.state.voyage_client = voyage_client
        app.state.rerank_client = rerank_client
        app.state.http_session = http_session
        app.state.scheduling_channel = scheduling_channel
        # Registered before the yield so it runs on the way out whatever happens: the
        # compiled-graph cache keys on the clients above, and would otherwise keep this
        # lifecycle's closed clients reachable for the life of the process.
        stack.callback(clear_graph_cache)
        yield


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    configure_logging(get_settings())
    app = FastAPI(title="VisitDoc — Grounded FAQ Chat", lifespan=lifespan)
    app.include_router(admin_router)
    app.include_router(chats_router)
    app.include_router(console_router)
    app.include_router(faq_router)
    app.include_router(turn_router)
    return app


app = create_app()


def main() -> None:
    """Run the app with uvicorn (entrypoint for `python -m chat.main`)."""
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
