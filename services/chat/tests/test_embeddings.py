from unittest.mock import AsyncMock, MagicMock, patch

from chat.rag.embeddings import embed_texts


async def test_embed_texts_returns_one_vector_per_input_string() -> None:
    fake_result = MagicMock(embeddings=[[0.1, 0.2], [0.3, 0.4]])
    client = MagicMock()
    client.embed = AsyncMock(return_value=fake_result)

    vectors = await embed_texts(client, ["a", "b"])

    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    client.embed.assert_called_once()


async def test_embed_texts_never_constructs_its_own_client() -> None:
    """`embed_texts` must not build a Voyage client itself (finding #6) - the caller
    passes an already-constructed, shared client instead, so the same connection pool
    is reused across calls rather than rebuilt on every call.
    """
    fake_result = MagicMock(embeddings=[[0.1]])
    client = MagicMock()
    client.embed = AsyncMock(return_value=fake_result)

    with patch("chat.rag.embeddings.AsyncClient") as mock_client_cls:
        await embed_texts(client, ["a"])
        await embed_texts(client, ["b"])

    mock_client_cls.assert_not_called()
