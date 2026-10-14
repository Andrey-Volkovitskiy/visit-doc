"""FAQ retrieval: embed a query, search both branches, and fuse them into one pool."""

from qdrant_client import AsyncQdrantClient
from voyageai.client_async import AsyncClient

from chat.core.config import get_settings
from chat.core.errors import TurnPipelineError
from chat.core.logging import get_logger
from chat.observability import ObservationType, step
from chat.rag.embeddings import EMBEDDING_MODEL, embed_texts
from chat.rag.pipeline import RRF_K, ScoredChunk, fuse_rrf
from chat.repositories.qdrant_repository import RetrievedChunk, search

# What this build's retrieval is, as `service.configured` states it: dense and BM25
# branches fused by Reciprocal Rank Fusion. There is no other path to choose.
RETRIEVAL_METHOD = "hybrid_rrf"


async def search_faq(
    qdrant_client: AsyncQdrantClient,
    voyage_client: AsyncClient,
    query: str,
    session_id: str,
    live_revisions: list[str],
) -> list[ScoredChunk]:
    """Embed `query`, search both branches, and return the fused observation pool.

    Args:
        session_id: The session whose corpus this search may reach. Passed beside the
            revisions rather than inferred from them, and both become terms on the
            search itself.
        live_revisions: Every revision that session currently publishes. An empty list
            means the session has no corpus, which is the ordinary starting state of
            every session - not a failed read, which raises instead.

    Returns: every candidate either branch returned, once each, best fused rank first,
        each carrying its rank and score in each branch and no rerank score yet.
        Nothing is dropped here: the shortlist gate drops what it drops, after
        recording it, so the cap can be argued about against candidates that were
        actually seen.

    Raises: TurnPipelineError wrapping any failure in embedding or retrieval.

    Returns immediately on an empty `live_revisions`: no filter value could match, so
    embedding the query and searching would spend two dependencies to learn what the
    empty list already said.

    The embedding and the search are each a step of the request's trace; the search's
    output is the fused pool, with each candidate's rank in each branch. Each branch
    returns up to `RETRIEVAL_POOL_SIZE`, so the fused pool holds at most twice that.
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
            branches = await search(
                qdrant_client,
                session_id,
                vectors[0],
                query,
                live_revisions,
                limit=limit,
            )
        except Exception as exc:
            raise TurnPipelineError("retrieval", exc) from exc
        pool = fuse_rrf(
            [_scored(c, dense=True) for c in branches.dense],
            [_scored(c, dense=False) for c in branches.lexical],
            k=RRF_K,
        )
        searched.set_output(
            {
                "dense_returned": len(branches.dense),
                "lexical_returned": len(branches.lexical),
                "pool_returned": len(pool),
                "candidates": [
                    {
                        "entry_id": chunk.faq_entry_id,
                        "chunk_index": chunk.chunk_index,
                        "dense_rank": chunk.dense_rank,
                        "lexical_rank": chunk.lexical_rank,
                        "fused_score": chunk.fused_score,
                    }
                    for chunk in pool
                ],
            }
        )

    return pool


def _scored(chunk: RetrievedChunk, *, dense: bool) -> ScoredChunk:
    """Lift one branch's hit into the pipeline's type, its score in that branch's field.

    Args:
        dense: whether the dense branch returned it, which makes its score a cosine;
            otherwise it is a BM25 score.
    """
    return ScoredChunk(
        faq_entry_id=chunk.faq_entry_id,
        chunk_index=chunk.chunk_index,
        chunk_text=chunk.chunk_text,
        similarity_score=chunk.score if dense else None,
        lexical_score=None if dense else chunk.score,
    )
