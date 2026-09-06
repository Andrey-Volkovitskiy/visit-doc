from unittest.mock import MagicMock

import voyageai
from chat.api.dependencies import get_rerank_client, get_voyage_client


def test_get_voyage_client_binds_shared_session_into_aiosession_contextvar() -> None:
    session = object()
    client = object()
    request = MagicMock()
    request.app.state.http_session = session
    request.app.state.voyage_client = client

    try:
        result = get_voyage_client(request)
        assert result is client
        assert voyageai.aiosession.get() is session
    finally:
        voyageai.aiosession.set(None)


def test_get_rerank_client_binds_shared_session_into_aiosession_contextvar() -> None:
    session = object()
    request = MagicMock()
    request.app.state.http_session = session
    request.app.state.rerank_client = object()

    try:
        result = get_rerank_client(request)
        assert result is request.app.state.rerank_client
        assert voyageai.aiosession.get() is session
    finally:
        voyageai.aiosession.set(None)


def test_the_rerank_client_is_not_the_embedding_client() -> None:
    # Separate objects on purpose: reranking pins the client's own retries off so the
    # deadline bounds the whole call rather than one attempt of it, and the embedding
    # path is free to retry without moving that bound.
    request = MagicMock()
    request.app.state.http_session = object()
    request.app.state.voyage_client = object()
    request.app.state.rerank_client = object()

    try:
        assert get_rerank_client(request) is not get_voyage_client(request)
    finally:
        voyageai.aiosession.set(None)
