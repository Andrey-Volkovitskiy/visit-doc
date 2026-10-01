"""FastAPI application entrypoint."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager

import aiohttp
import uvicorn
from anthropic import AsyncAnthropic
from fastapi import FastAPI
from starlette.routing import Route
from starlette.types import Receive, Scope, Send
from voyageai.client_async import AsyncClient

from chat import observability
from chat.agent.graph import clear_graph_cache
from chat.api.admin import router as admin_router
from chat.api.chats import router as chats_router
from chat.api.console import router as console_router
from chat.api.faq import router as faq_router
from chat.api.oauth import router as oauth_router
from chat.api.oauth import unavailable_response
from chat.api.turn import router as turn_router
from chat.clients.scheduling import create_channel
from chat.connectors.mcp_server import (
    PROTECTED_RESOURCE_PATH,
    build_server,
    build_transport,
)
from chat.connectors.public_address import (
    MCP_PATH,
    ConnectorUnavailable,
    current_connector,
)
from chat.core.config import Settings, get_settings
from chat.core.logging import configure_logging, get_logger
from chat.domain.schemas import MAX_SEGMENTS
from chat.observability.client import build_tracer
from chat.observability.log_bridge import install_log_bridge, uninstall_log_bridge
from chat.rag.embeddings import EMBEDDING_MODEL
from chat.repositories.qdrant_repository import (
    CollectionVectorSizeMismatchError,
    create_client,
    ensure_collection,
)


def _log_configuration(settings: Settings) -> None:
    """State the models, thresholds, caps and limits this process answers turns with.

    Emitted once, before any client is built, so even a start that fails on a
    dependency says what it was configured with. The golden harness takes a run's
    conditions from this event rather than from its own environment, which describes
    the harness and not the process that answered. The field names are a contract with
    that reader. No secret-bearing setting belongs on the list: `tracing_enabled` says
    whether both Langfuse keys are set, never what they are, and it is not a condition -
    a traced and an untraced turn are answered the same way.
    """
    get_logger().info(
        "service.configured",
        classification_model=settings.CLASSIFICATION_MODEL,
        generation_model=settings.GENERATION_MODEL,
        small_talk_model=settings.SMALL_TALK_MODEL,
        embedding_model=EMBEDDING_MODEL,
        rerank_model=settings.RERANK_MODEL,
        retrieval_pool_size=settings.RETRIEVAL_POOL_SIZE,
        similarity_floor=settings.SIMILARITY_FLOOR,
        similarity_cap=settings.SIMILARITY_CAP,
        unreranked_similarity_floor=settings.UNRERANKED_SIMILARITY_FLOOR,
        rerank_floor=settings.RERANK_FLOOR,
        rerank_cap=settings.RERANK_CAP,
        max_segments=MAX_SEGMENTS,
        context_turns=settings.CONTEXT_TURNS,
        tracing_enabled=settings.tracing_enabled,
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

    The tracer is built first, so a start that fails later still shuts it down. Its
    shutdown flushes whatever spans are pending and blocks while it does - each export
    attempt bounded by the exporter's timeout - so it runs on a worker thread; it is
    registered to run before the bridge that reports its failures is removed, and after
    observations stop being recorded.
    """
    settings = get_settings()
    _log_configuration(settings)
    async with AsyncExitStack() as stack:
        install_log_bridge()
        stack.callback(uninstall_log_bridge)
        tracer = build_tracer(settings)
        stack.push_async_callback(asyncio.to_thread, tracer.shutdown)
        observability.install(tracer)
        stack.callback(observability.uninstall)

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

        # The staff connector's MCP transport, built for this lifespan: the SDK's
        # session manager runs once per instance. Routed to rather than mounted, so
        # the sub-app's own lifespan never runs and its manager is entered here.
        # Holds the unavailability instead when there is no connector, so a request
        # is answered with the reason this lifespan decided rather than a second
        # reading of the setting.
        connector = current_connector()
        if isinstance(connector, ConnectorUnavailable):
            app.state.mcp_transport = connector
        else:
            server = build_server(connector)
            transport = build_transport(server, connector)
            await stack.enter_async_context(server.session_manager.run())
            app.state.mcp_transport = transport
        yield


class _ConnectorTransport:
    """Hands a request for the connector's two paths to this lifespan's MCP transport.

    A class, not a function: Starlette treats a plain function given to a route as a
    request handler, and only a callable object as an ASGI app it passes the raw
    request to.
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Forward the request, or answer the connector's fixed `503`.

        The `503` while the lifespan decided the connector is unavailable is the one
        the OAuth routes answer: nothing may be published naming an issuer that does
        not exist.
        """
        transport = scope["app"].state.mcp_transport
        if isinstance(transport, ConnectorUnavailable):
            await unavailable_response(transport)(scope, receive, send)
            return
        await transport(scope, receive, send)


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    configure_logging(get_settings())
    app = FastAPI(title="VisitDoc — Grounded FAQ Chat", lifespan=lifespan)
    app.include_router(admin_router)
    app.include_router(chats_router)
    app.include_router(console_router)
    app.include_router(faq_router)
    app.include_router(oauth_router)
    app.include_router(turn_router)
    # The connector's two paths, and only those: added as routes rather than a mount
    # at `/`, which would hand every unmatched path to the transport and replace this
    # app's own `404`.
    transport = _ConnectorTransport()
    for path in (MCP_PATH, PROTECTED_RESOURCE_PATH):
        app.router.routes.append(Route(path, transport, include_in_schema=False))
    return app


app = create_app()


def main() -> None:
    """Run the app with uvicorn (entrypoint for `python -m chat.main`)."""
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
