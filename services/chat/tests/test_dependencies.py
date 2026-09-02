from unittest.mock import MagicMock

import voyageai
from chat.api.dependencies import get_voyage_client


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
