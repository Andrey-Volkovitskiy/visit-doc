from unittest.mock import MagicMock, patch

from chat.core.config import Settings
from chat.rag.chunking import ChunkedText
from chat.rag.indexing import deindex_faq_entry, index_faq_entry


def _settings() -> Settings:
    return Settings(
        DATABASE_URL="postgresql+asyncpg://user:pass@localhost/db",
        QDRANT_URL="http://localhost:6333",
        ANTHROPIC_API_KEY="key",
        VOYAGE_API_KEY="key",
    )


async def test_index_faq_entry_deletes_then_chunks_embeds_upserts_in_order() -> None:
    calls: list[str] = []
    chunks = [ChunkedText(chunk_index=0, chunk_text="hello")]

    async def fake_delete(*_args: object) -> None:
        calls.append("delete")

    def fake_chunk(_content: str) -> list[ChunkedText]:
        calls.append("chunk")
        return chunks

    def fake_embed(
        _texts: list[str], _settings: Settings, input_type: str
    ) -> list[list[float]]:
        calls.append("embed")
        return [[0.1]]

    async def fake_upsert(*_args: object) -> None:
        calls.append("upsert")

    with (
        patch("chat.rag.indexing.delete_by_entry", fake_delete),
        patch("chat.rag.indexing.chunk_content", fake_chunk),
        patch("chat.rag.indexing.embed_texts", fake_embed),
        patch("chat.rag.indexing.upsert_chunks", fake_upsert),
    ):
        await index_faq_entry(MagicMock(), _settings(), 1, "hello")

    assert calls == ["delete", "chunk", "embed", "upsert"]


async def test_deindex_faq_entry_calls_delete_by_entry() -> None:
    with patch("chat.rag.indexing.delete_by_entry") as mock_delete:
        await deindex_faq_entry(MagicMock(), 1)

    mock_delete.assert_called_once()
