"""Indexing orchestration: (re-)index an entry's chunks, or remove them."""

from qdrant_client import AsyncQdrantClient
from voyageai.client_async import AsyncClient

from chat.core.logging import get_logger
from chat.rag.chunking import chunk_content
from chat.rag.embeddings import embed_texts
from chat.repositories.qdrant_repository import delete_by_entry, upsert_chunks


class FaqOperationError(Exception):
    """Tags which sub-step of a FAQ operation failed (FR-007, FR-022).

    Raised, not logged, at the point of failure - `api/faq.py` is the one place
    that turns this into a `faq.operation_failed` log entry (FR-014's "one
    centralized place" spirit).
    """

    def __init__(self, failed_step: str, cause: Exception) -> None:
        super().__init__(str(cause))
        self.failed_step = failed_step
        self.cause = cause


async def index_faq_entry(
    client: AsyncQdrantClient,
    voyage_client: AsyncClient,
    faq_entry_id: int,
    content: str,
) -> None:
    """(Re-)index `content`'s chunks for `faq_entry_id`.

    Always delete-then-upsert: safe for a brand-new entry (delete is a no-op) and for a
    re-index after an update (data-model.md's Update lifecycle) — the one indexing path
    both create and update use.

    Raises: FaqOperationError wrapping any failure, tagged "chunking", "embedding", or
        "persist" (FR-007, FR-022).
    """
    logger = get_logger()

    try:
        await delete_by_entry(client, faq_entry_id)
    except Exception as exc:
        raise FaqOperationError("persist", exc) from exc

    try:
        chunks = chunk_content(content)
    except Exception as exc:
        raise FaqOperationError("chunking", exc) from exc
    logger.info("faq.content_chunked", chunk_count=len(chunks))

    if not chunks:
        return

    texts = [chunk.chunk_text for chunk in chunks]
    try:
        vectors = await embed_texts(voyage_client, texts, input_type="document")
    except Exception as exc:
        raise FaqOperationError("embedding", exc) from exc
    logger.info("faq.chunks_embedded", chunk_count=len(chunks))

    try:
        await upsert_chunks(client, faq_entry_id, chunks, vectors)
    except Exception as exc:
        raise FaqOperationError("persist", exc) from exc


async def deindex_faq_entry(client: AsyncQdrantClient, faq_entry_id: int) -> None:
    """Remove all indexed chunks for `faq_entry_id`.

    Raises: FaqOperationError tagged "persist", wrapping any failure (FR-007).
    """
    try:
        await delete_by_entry(client, faq_entry_id)
    except Exception as exc:
        raise FaqOperationError("persist", exc) from exc
