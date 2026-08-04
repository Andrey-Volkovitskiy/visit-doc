"""`search_faq` retriever — same signature Phase 1's MCP tool will wrap."""

from qdrant_client import AsyncQdrantClient

from chat.core.config import Settings
from chat.rag.embeddings import embed_texts
from chat.repositories.qdrant_repository import RetrievedChunk, search


async def search_faq(
    client: AsyncQdrantClient, settings: Settings, query: str, limit: int = 5
) -> list[RetrievedChunk]:
    """Embed `query` and return the nearest FAQ chunks, with their similarity scores."""
    vectors = embed_texts([query], settings, input_type="query")
    return await search(client, vectors[0], limit=limit)
