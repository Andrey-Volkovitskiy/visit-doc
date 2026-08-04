"""Indexing orchestration: (re-)index an entry's chunks, or remove them."""

from qdrant_client import AsyncQdrantClient

from chat.core.config import Settings
from chat.rag.chunking import chunk_content
from chat.rag.embeddings import embed_texts
from chat.repositories.qdrant_repository import delete_by_entry, upsert_chunks


async def index_faq_entry(
    client: AsyncQdrantClient, settings: Settings, faq_entry_id: int, content: str
) -> None:
    """(Re-)index `content`'s chunks for `faq_entry_id`.

    Always delete-then-upsert: safe for a brand-new entry (delete is a no-op) and for a
    re-index after an update (data-model.md's Update lifecycle) — the one indexing path
    both create and update use.
    """
    await delete_by_entry(client, faq_entry_id)
    chunks = chunk_content(content)
    if not chunks:
        return
    texts = [chunk.chunk_text for chunk in chunks]
    vectors = embed_texts(texts, settings, input_type="document")
    await upsert_chunks(client, faq_entry_id, chunks, vectors)


async def deindex_faq_entry(client: AsyncQdrantClient, faq_entry_id: int) -> None:
    """Remove all indexed chunks for `faq_entry_id`."""
    await delete_by_entry(client, faq_entry_id)
