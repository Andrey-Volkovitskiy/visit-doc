from unittest.mock import AsyncMock, MagicMock, patch

from chat.core.config import Settings
from chat.rag.embeddings import embed_texts


def _settings() -> Settings:
    return Settings(
        DATABASE_URL="postgresql+asyncpg://user:pass@localhost/db",
        QDRANT_URL="http://localhost:6333",
        ANTHROPIC_API_KEY="key",
        VOYAGE_API_KEY="key",
    )


async def test_embed_texts_returns_one_vector_per_input_string() -> None:
    fake_result = MagicMock(embeddings=[[0.1, 0.2], [0.3, 0.4]])
    with patch("chat.rag.embeddings.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.embed = AsyncMock(return_value=fake_result)

        vectors = await embed_texts(["a", "b"], _settings())

    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    mock_client_cls.return_value.embed.assert_called_once()
