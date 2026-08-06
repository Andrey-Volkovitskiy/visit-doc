from unittest.mock import MagicMock, patch

import structlog
from chat.core.correlation import bind_operation_id
from chat.rag.chunking import ChunkedText
from chat.rag.indexing import deindex_faq_entry, index_faq_entry
from structlog.testing import capture_logs


async def test_index_faq_entry_deletes_then_chunks_embeds_upserts_in_order() -> None:
    calls: list[str] = []
    chunks = [ChunkedText(chunk_index=0, chunk_text="hello")]

    async def fake_delete(*_args: object) -> None:
        calls.append("delete")

    def fake_chunk(_content: str) -> list[ChunkedText]:
        calls.append("chunk")
        return chunks

    async def fake_embed(
        _client: object, _texts: list[str], input_type: str
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
        await index_faq_entry(MagicMock(), MagicMock(), 1, "hello")

    assert calls == ["delete", "chunk", "embed", "upsert"]


async def test_index_faq_entry_logs_substeps_correlated_by_operation_id() -> None:
    chunks = [
        ChunkedText(chunk_index=0, chunk_text="hello"),
        ChunkedText(chunk_index=1, chunk_text="world"),
    ]

    with (
        patch("chat.rag.indexing.delete_by_entry"),
        patch("chat.rag.indexing.chunk_content", return_value=chunks),
        patch("chat.rag.indexing.embed_texts", return_value=[[0.1], [0.2]]),
        patch("chat.rag.indexing.upsert_chunks"),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
        bind_operation_id(),
    ):
        await index_faq_entry(MagicMock(), MagicMock(), 1, "hello world")

    operation_ids = {entry["operation_id"] for entry in logs}
    events = {entry["event"]: entry for entry in logs}

    assert len(operation_ids) == 1
    assert list(events) == ["faq.content_chunked", "faq.chunks_embedded"]
    assert events["faq.content_chunked"]["chunk_count"] == 2
    assert events["faq.chunks_embedded"]["chunk_count"] == 2


async def test_deindex_faq_entry_calls_delete_by_entry() -> None:
    with patch("chat.rag.indexing.delete_by_entry") as mock_delete:
        await deindex_faq_entry(MagicMock(), 1)

    mock_delete.assert_called_once()


async def test_deindex_faq_entry_logs_no_chunking_or_embedding_substeps() -> None:
    with (
        patch("chat.rag.indexing.delete_by_entry"),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
        bind_operation_id(),
    ):
        await deindex_faq_entry(MagicMock(), 1)

    events = {entry["event"] for entry in logs}
    assert "faq.content_chunked" not in events
    assert "faq.chunks_embedded" not in events
