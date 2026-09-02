"""Shared FastAPI dependency helpers for route handlers."""

import voyageai
from fastapi import Request
from voyageai.client_async import AsyncClient


def get_voyage_client(request: Request) -> AsyncClient:
    """Return the shared Voyage client, binding its pooled session for this request.

    `voyageai.aiosession` is a `contextvars.ContextVar`: a value set once at app
    startup isn't guaranteed to propagate into each request's own asyncio task, so it
    must be set here, within the same task that will go on to call `embed_texts`.
    """
    voyageai.aiosession.set(request.app.state.http_session)
    client: AsyncClient = request.app.state.voyage_client
    return client
