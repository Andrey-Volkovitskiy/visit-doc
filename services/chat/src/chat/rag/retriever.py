"""FAQ retrieval: embed a query and search Qdrant for the nearest chunks."""

from qdrant_client import AsyncQdrantClient
from voyageai.client_async import AsyncClient

from chat.core.logging import get_logger
from chat.rag.embeddings import embed_texts
from chat.repositories.qdrant_repository import RetrievedChunk, search


class TurnPipelineError(Exception):
    """Tags which pipeline step failed during a chat turn.

    Raised, not logged, at the point of failure - logging happens once, centrally,
    by the caller.
    """

    def __init__(self, pipeline_step: str, cause: Exception) -> None:
        super().__init__(str(cause))
        self.pipeline_step = pipeline_step
        self.cause = cause


async def search_faq(
    qdrant_client: AsyncQdrantClient,
    voyage_client: AsyncClient,
    query: str,
    limit: int = 5,
) -> list[RetrievedChunk]:
    """Embed `query` and return the nearest FAQ chunks, with their similarity scores.

    Raises: TurnPipelineError wrapping any failure in embedding or retrieval.
    """
    logger = get_logger()

    try:
        vectors = await embed_texts(voyage_client, [query], input_type="query")
    except Exception as exc:
        raise TurnPipelineError("embedding", exc) from exc
    logger.info("turn.message_embedded")

    try:
        chunks = await search(qdrant_client, vectors[0], limit=limit)
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
