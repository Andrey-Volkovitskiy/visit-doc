"""Indexing orchestration: (re-)index an entry's chunks, or remove them."""

from qdrant_client import AsyncQdrantClient
from voyageai.client_async import AsyncClient

from chat.core.logging import get_logger
from chat.rag.chunking import chunk_content
from chat.rag.embeddings import embed_texts
from chat.repositories.qdrant_repository import delete_by_entry, upsert_chunks


class FaqOperationError(Exception):
    """Tags which sub-step of a FAQ operation failed.

    Raised, not logged, at the point of failure - logging happens once, centrally,
    by the caller.
    """

    def __init__(self, failed_step: str, cause: Exception) -> None:
        super().__init__(str(cause))
        self.failed_step = failed_step
        self.cause = cause


async def index_faq_entry(
    qdrant_client: AsyncQdrantClient,
    voyage_client: AsyncClient,
    faq_entry_id: int,
    content: str,
) -> None:
    """(Re-)index `content`'s chunks for `faq_entry_id`.

    Raises: FaqOperationError wrapping any failure, tagged "chunking", "embedding",
        or "persist".

    Always delete-then-upsert: safe for a brand-new entry (delete is a no-op) and for
    a re-index after an update - the one indexing path both create and update use.
    """
    logger = get_logger()

    try:
        await delete_by_entry(qdrant_client, faq_entry_id)
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
        await upsert_chunks(qdrant_client, faq_entry_id, chunks, vectors)
    except Exception as exc:
        raise FaqOperationError("persist", exc) from exc


async def deindex_faq_entry(
    qdrant_client: AsyncQdrantClient, faq_entry_id: int
) -> None:
    """Remove all indexed chunks for `faq_entry_id`.

    Raises: FaqOperationError tagged "persist", wrapping any failure.
    """
    try:
        await delete_by_entry(qdrant_client, faq_entry_id)
    except Exception as exc:
        raise FaqOperationError("persist", exc) from exc
