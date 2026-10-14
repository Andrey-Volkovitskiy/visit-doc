"""`search_faq` returns the merged observation pool whole, for the gates to narrow.

It runs both branches - dense and BM25 - merges them, and hands back everything they
returned, so the shortlist gate discards only candidates something recorded.
"""

from unittest.mock import AsyncMock, patch

from chat.core.config import get_settings
from chat.rag.pipeline import ScoredChunk
from chat.rag.retriever import search_faq
from chat.repositories.qdrant_repository import (
    VECTOR_SIZE,
    RetrievedChunk,
    SearchBranches,
)

_SESSION = "01JQ0000000000000000000000"
_REVISIONS = ["01JQ1111111111111111111111"]


def _retrieved(score: float, index: int) -> RetrievedChunk:
    return RetrievedChunk(
        faq_entry_id=1, chunk_index=index, chunk_text=f"chunk {index}", score=score
    )


async def _run(
    dense: list[RetrievedChunk], lexical: list[RetrievedChunk] | None = None
) -> tuple[list[ScoredChunk], AsyncMock]:
    search = AsyncMock(return_value=SearchBranches(dense=dense, lexical=lexical or []))
    embed = AsyncMock(return_value=[[0.1] * VECTOR_SIZE])
    with (
        patch("chat.rag.retriever.embed_texts", embed),
        patch("chat.rag.retriever.search", search),
    ):
        chunks = await search_faq(
            AsyncMock(), AsyncMock(), "a question", _SESSION, _REVISIONS
        )
    return chunks, search


async def test_search_faq_asks_each_branch_for_the_configured_pool_size() -> None:
    _, search = await _run([])

    assert search.await_args.kwargs["limit"] == get_settings().RETRIEVAL_POOL_SIZE


async def test_search_faq_hands_the_query_text_to_the_lexical_branch() -> None:
    # BM25 is computed from the words, not the embedding, so the text must reach it.
    _, search = await _run([])

    assert "a question" in search.await_args.args


async def test_the_pool_is_wider_than_the_shortlist_cap() -> None:
    # Otherwise the cap discards only candidates it never fetched.
    settings = get_settings()

    assert settings.RETRIEVAL_POOL_SIZE > settings.SIMILARITY_CAP


async def test_a_chunk_only_the_lexical_branch_found_reaches_the_pool() -> None:
    # The reason the branch exists: a chunk dense search did not return at all.
    chunks, _ = await _run([_retrieved(0.9, 0)], [_retrieved(7.5, 4)])

    lexical_only = next(c for c in chunks if c.chunk_index == 4)
    assert lexical_only.similarity_score is None
    assert lexical_only.lexical_score == 7.5
    assert lexical_only.lexical_rank == 1


async def test_the_pool_is_the_dense_order_then_what_only_bm25_found() -> None:
    dense = [_retrieved(0.9, 0), _retrieved(0.8, 1)]
    lexical = [_retrieved(9.0, 2), _retrieved(8.0, 1)]

    chunks, _ = await _run(dense, lexical)

    assert [c.chunk_index for c in chunks] == [0, 1, 2]
    assert chunks[1].lexical_rank == 2


async def test_retrieved_chunks_are_lifted_into_scored_chunks() -> None:
    # The repository's row type stops at the repository boundary; the pipeline works in
    # its own domain type, which has somewhere to put a rerank score later.
    chunks, _ = await _run([_retrieved(0.9, 0)])

    assert isinstance(chunks[0], ScoredChunk)
    assert chunks[0].similarity_score == 0.9
    assert chunks[0].dense_rank == 1
    assert chunks[0].rerank_score is None
    assert chunks[0].chunk_text == "chunk 0"


async def test_an_empty_corpus_costs_no_embedding_and_no_search() -> None:
    search = AsyncMock(return_value=SearchBranches(dense=[], lexical=[]))
    embed = AsyncMock(return_value=[[0.1] * VECTOR_SIZE])
    with (
        patch("chat.rag.retriever.embed_texts", embed),
        patch("chat.rag.retriever.search", search),
    ):
        chunks = await search_faq(AsyncMock(), AsyncMock(), "q", _SESSION, [])

    assert chunks == []
    embed.assert_not_awaited()
    search.assert_not_awaited()
