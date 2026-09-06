"""`search_faq` returns the observation pool whole, for the gates to narrow.

The retriever stopped being a filter in this phase. It fetches wider than the
similarity cap and hands back everything it got, including candidates that will not
survive the floor - because a gate can only be calibrated against candidates something
recorded, and one that discards only what it never fetched cannot be argued up or down.
"""

from unittest.mock import AsyncMock, patch

from chat.core.config import get_settings
from chat.rag.pipeline import ScoredChunk
from chat.rag.retriever import search_faq
from chat.repositories.qdrant_repository import VECTOR_SIZE, RetrievedChunk

_SESSION = "01JQ0000000000000000000000"
_REVISIONS = ["01JQ1111111111111111111111"]


def _retrieved(score: float, index: int) -> RetrievedChunk:
    return RetrievedChunk(
        faq_entry_id=1, chunk_index=index, chunk_text=f"chunk {index}", score=score
    )


async def _run(found: list[RetrievedChunk]) -> tuple[list[ScoredChunk], AsyncMock]:
    search = AsyncMock(return_value=found)
    embed = AsyncMock(return_value=[[0.1] * VECTOR_SIZE])
    with (
        patch("chat.rag.retriever.embed_texts", embed),
        patch("chat.rag.retriever.search", search),
    ):
        chunks = await search_faq(
            AsyncMock(), AsyncMock(), "a question", _SESSION, _REVISIONS
        )
    return chunks, search


async def test_search_faq_asks_for_the_configured_pool_size() -> None:
    _, search = await _run([])

    assert search.await_args.kwargs["limit"] == get_settings().RETRIEVAL_POOL_SIZE


async def test_the_pool_is_wider_than_the_similarity_cap() -> None:
    # Otherwise the cap discards only candidates it never fetched, which is the state
    # this phase exists to leave behind.
    settings = get_settings()

    assert settings.RETRIEVAL_POOL_SIZE > settings.SIMILARITY_CAP


async def test_sub_floor_candidates_are_returned_not_dropped() -> None:
    # The gate drops them, not the search - and only after logging what it dropped.
    floor = get_settings().SIMILARITY_FLOOR
    found = [_retrieved(0.9, 0), _retrieved(floor - 0.2, 1), _retrieved(0.01, 2)]

    chunks, _ = await _run(found)

    assert [c.chunk_index for c in chunks] == [0, 1, 2]
    assert any(c.similarity_score < floor for c in chunks)


async def test_retrieved_chunks_are_lifted_into_scored_chunks() -> None:
    # The repository's row type stops at the repository boundary; the pipeline works in
    # its own domain type, which has somewhere to put a rerank score later.
    chunks, _ = await _run([_retrieved(0.9, 0)])

    assert isinstance(chunks[0], ScoredChunk)
    assert chunks[0].similarity_score == 0.9
    assert chunks[0].rerank_score is None
    assert chunks[0].chunk_text == "chunk 0"


async def test_an_empty_corpus_costs_no_embedding_and_no_search() -> None:
    search = AsyncMock(return_value=[])
    embed = AsyncMock(return_value=[[0.1] * VECTOR_SIZE])
    with (
        patch("chat.rag.retriever.embed_texts", embed),
        patch("chat.rag.retriever.search", search),
    ):
        chunks = await search_faq(AsyncMock(), AsyncMock(), "q", _SESSION, [])

    assert chunks == []
    embed.assert_not_awaited()
    search.assert_not_awaited()
