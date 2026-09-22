"""FAQ retrieval: embed a query and fetch the turn's observation pool from Qdrant."""

from qdrant_client import AsyncQdrantClient
from voyageai.client_async import AsyncClient

from chat.core.config import get_settings
from chat.core.errors import TurnPipelineError
from chat.core.logging import get_logger
from chat.observability import ObservationType, step
from chat.rag.embeddings import EMBEDDING_MODEL, embed_texts
from chat.rag.pipeline import ScoredChunk
from chat.repositories.qdrant_repository import search


async def search_faq(
    qdrant_client: AsyncQdrantClient,
    voyage_client: AsyncClient,
    query: str,
    session_id: str,
    live_revisions: list[str],
) -> list[ScoredChunk]:
    """Embed `query` and return the turn's whole observation pool, in score order.

    Args:
        session_id: The session whose corpus this search may reach. Passed beside the
            revisions rather than inferred from them, and both become terms on the
            search itself.
        live_revisions: Every revision that session currently publishes. An empty list
            means the session has no corpus, which is the ordinary starting state of
            every session - not a failed read, which raises instead.

    Returns: every candidate the search returned, unfiltered, each carrying its
        similarity score and no rerank score yet. Nothing is dropped here: the
        similarity gate drops what it drops, after recording it, so a threshold can be
        argued about against candidates that were actually seen.

    Raises: TurnPipelineError wrapping any failure in embedding or retrieval.

    Returns immediately on an empty `live_revisions`: no filter value could match, so
    embedding the query and searching would spend two dependencies to learn what the
    empty list already said.

    The embedding and the search are each a step of the request's trace; the search's
    output is the pool exactly as it came back.
    """
    logger = get_logger()
    if not live_revisions:
        logger.info("turn.retrieval_skipped_empty_corpus")
        return []

    with step("faq.embed", input=query) as embedded:
        try:
            vectors = await embed_texts(voyage_client, [query], input_type="query")
        except Exception as exc:
            raise TurnPipelineError("embedding", exc) from exc
        embedded.set_output(
            {
                "model": EMBEDDING_MODEL,
                "dimension": len(vectors[0]) if vectors else None,
            }
        )
    logger.info("turn.message_embedded")

    limit = get_settings().RETRIEVAL_POOL_SIZE
    with step(
        "faq.search",
        as_type=ObservationType.RETRIEVER,
        input={"query": query, "pool_size": limit},
    ) as searched:
        try:
            chunks = await search(
                qdrant_client, session_id, vectors[0], live_revisions, limit=limit
            )
        except Exception as exc:
            raise TurnPipelineError("retrieval", exc) from exc
        searched.set_output(
            {
                "pool_returned": len(chunks),
                "candidates": [
                    {
                        "entry_id": chunk.faq_entry_id,
                        "chunk_index": chunk.chunk_index,
                        "similarity_score": chunk.score,
                    }
                    for chunk in chunks
                ],
            }
        )

    return [
        ScoredChunk(
            faq_entry_id=chunk.faq_entry_id,
            chunk_index=chunk.chunk_index,
            chunk_text=chunk.chunk_text,
            similarity_score=chunk.score,
        )
        for chunk in chunks
    ]
