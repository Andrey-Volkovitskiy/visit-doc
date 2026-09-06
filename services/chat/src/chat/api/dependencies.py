"""Shared FastAPI dependency helpers for route handlers."""

import voyageai
from fastapi import Request
from voyageai.client_async import AsyncClient


def _bind_pooled_session(request: Request) -> None:
    """Bind the app's shared aiohttp session into Voyage's session contextvar.

    `voyageai.aiosession` is a `contextvars.ContextVar`: a value set once at app startup
    isn't guaranteed to propagate into each request's own asyncio task, so it must be
    set here, within the same task that will go on to call the client. Every accessor
    below does it, so a route reaching for one client alone still gets the pool.
    """
    voyageai.aiosession.set(request.app.state.http_session)


def get_rerank_client(request: Request) -> AsyncClient:
    """Return the shared reranking client, binding its pooled session for this request.

    A different client from `get_voyage_client`'s, not a different accessor for the same
    one: reranking pins the SDK's retries off so its deadline covers the whole call
    rather than one attempt of it, and the embedding client is free to retry without
    that.
    """
    _bind_pooled_session(request)
    client: AsyncClient = request.app.state.rerank_client
    return client


def get_voyage_client(request: Request) -> AsyncClient:
    """Return the shared Voyage client, binding its pooled session for this request."""
    _bind_pooled_session(request)
    client: AsyncClient = request.app.state.voyage_client
    return client
