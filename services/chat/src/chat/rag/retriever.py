"""`search_faq` retriever — same signature Phase 1's MCP tool will wrap."""

from qdrant_client import AsyncQdrantClient

from chat.core.config import Settings
from chat.core.logging import get_logger
from chat.rag.embeddings import embed_texts
from chat.repositories.qdrant_repository import RetrievedChunk, search


class TurnPipelineError(Exception):
    """Tags which pipeline step failed during a chat turn (FR-005).

    Raised, not logged, at the point of failure - `api/chat.py` is the one place
    that turns this into a `turn.error` log entry (FR-014's "one centralized place"
    spirit: attribution happens where it occurs, the log call happens once).
    """

    def __init__(self, pipeline_step: str, cause: Exception) -> None:
        super().__init__(str(cause))
        self.pipeline_step = pipeline_step
        self.cause = cause


async def search_faq(
    client: AsyncQdrantClient, settings: Settings, query: str, limit: int = 5
) -> list[RetrievedChunk]:
    """Embed `query` and return the nearest FAQ chunks, with their similarity scores.

    Raises: TurnPipelineError wrapping any failure in embedding or retrieval (FR-005).
    """
    logger = get_logger()

    try:
        vectors = await embed_texts([query], settings, input_type="query")
    except Exception as exc:
        raise TurnPipelineError("embedding", exc) from exc
    logger.info("turn.message_embedded")

    try:
        chunks = await search(client, vectors[0], limit=limit)
    except Exception as exc:
        raise TurnPipelineError("retrieval", exc) from exc

    logger.info(
        "turn.retrieval_completed",
        retrieved_chunks=[
            {
                "entry_id": chunk.faq_entry_id,
                "chunk_index": chunk.chunk_index,
                "score": chunk.score,
                "chunk_text": chunk.chunk_text,
            }
            for chunk in chunks
        ],
    )
    return chunks
