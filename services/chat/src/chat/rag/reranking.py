"""Cross-encoder reranking: score a shortlist against the question it answers.

The seam between the pipeline and a reranking provider, shaped like `rag/embeddings.py`
- domain types in, domain types out, so no provider wire shape reaches the node that
calls it.

This module scores; it does not gate. The floor lives in `rag/pipeline.py`, which is
what keeps "no scores were obtained" and "scores were obtained and none cleared the
floor" two different answers - the first falls back and answers, the second abstains.
"""

import asyncio
import time

from voyageai import error as voyage_error
from voyageai.client_async import AsyncClient

from chat.core.logging import get_logger
from chat.rag.pipeline import ScoredChunk


async def rerank_chunks(
    client: AsyncClient,
    query: str,
    chunks: list[ScoredChunk],
    *,
    model: str,
    top_k: int,
    timeout_seconds: float,
) -> list[ScoredChunk] | None:
    """Score `chunks` against `query` with a cross-encoder.

    Args:
        top_k: how many scored results to ask the provider for. Not a gate - the gate
            is applied afterwards, against the floor.
        timeout_seconds: a deadline on obtaining the scores at all, covering any
            retrying the client does internally rather than one attempt at it.

    Returns: the same chunks, each carrying its `rerank_score`, or None when no scores
        were obtained - a transport error, a refusal, a rate limit, an unusable or
        partial response, or the deadline. None is reserved for exactly that: an empty
        list here would mean the provider was asked to score nothing.

    Raises: nothing. A reranking failure costs the turn its precision stage, never the
        turn itself, so every failure is converted to None and logged here.

    A late answer is discarded rather than applied: the turn has already moved on, and
    reordering a shortlist mid-generation would change what its citations mean.
    """
    logger = get_logger()
    if not chunks:
        return []

    started = time.perf_counter()
    try:
        response = await asyncio.wait_for(
            client.rerank(
                query, [c.chunk_text for c in chunks], model=model, top_k=top_k
            ),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        logger.error(
            "faq.reranking_unavailable",
            reason="timeout",
            timeout_seconds=timeout_seconds,
            candidate_count=len(chunks),
        )
        return None
    except Exception as exc:  # noqa: BLE001 - degrades the answer, never the turn
        logger.error(
            "faq.reranking_unavailable",
            reason=_reason_for(exc),
            error=str(exc),
            timeout_seconds=timeout_seconds,
            candidate_count=len(chunks),
        )
        return None

    scored = _apply_scores(chunks, response)
    if scored is None:
        logger.error(
            "faq.reranking_unavailable",
            reason="unusable_response",
            error="response did not score every candidate exactly once",
            timeout_seconds=timeout_seconds,
            candidate_count=len(chunks),
        )
        return None

    logger.info(
        "faq.reranking_completed",
        model=model,
        candidate_count=len(chunks),
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
        scores=[
            {
                "entry_id": c.faq_entry_id,
                "chunk_index": c.chunk_index,
                "similarity_score": c.similarity_score,
                "rerank_score": c.rerank_score,
            }
            for c in scored
        ],
    )
    return scored


def _reason_for(exc: Exception) -> str:
    """Classify `exc` into the closed set of reasons the failure event reports.

    On the exception's type, never on words in its message. A substring test is not a
    classifier: "rate" appears inside "strategy", "auth" inside "author", so a message
    that merely mentions a file path decides the reason. This field is read as a metric,
    so a wrong one is worse than a coarse one.
    """
    if isinstance(exc, voyage_error.RateLimitError):
        return "rate_limited"
    if isinstance(exc, voyage_error.AuthenticationError | PermissionError):
        return "refused"
    if isinstance(
        exc,
        voyage_error.APIConnectionError
        | voyage_error.ServiceUnavailableError
        | voyage_error.ServerError
        | ConnectionError
        | TimeoutError,
    ):
        return "transport"
    if isinstance(
        exc, voyage_error.InvalidRequestError | voyage_error.MalformedRequestError
    ):
        return "unusable_response"
    # Anything the provider does not raise as one of its own: a bug here, a test guard,
    # something in the event loop. Named as itself rather than filed under a cause it
    # was not, so a run of these reads as "look at this", not "the vendor is down".
    return "unexpected"


def _apply_scores(
    chunks: list[ScoredChunk], response: object
) -> list[ScoredChunk] | None:
    """Attach each result's score to the chunk its index refers to.

    Returns: the scored chunks in the provider's own relevance order, or None if the
        response is unusable - missing its results, referring to a chunk that was not
        sent, or leaving any candidate unscored. A chunk with no score would have to be
        read as either zero or as passing, and both are inventions, so a partial
        response is refused whole rather than half-used.
    """
    results = getattr(response, "results", None)
    if results is None:
        return None

    scored: list[ScoredChunk] = []
    seen: set[int] = set()
    for result in results:
        index = getattr(result, "index", None)
        score = getattr(result, "relevance_score", None)
        if index is None or score is None or not 0 <= index < len(chunks):
            return None
        if index in seen:
            return None
        seen.add(index)
        scored.append(chunks[index].with_rerank_score(float(score)))

    if len(seen) != len(chunks):
        return None
    return scored
