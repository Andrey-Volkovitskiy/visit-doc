from chat.rag.groundedness import is_grounded
from chat.repositories.qdrant_repository import RetrievedChunk


def _chunk(score: float) -> RetrievedChunk:
    return RetrievedChunk(faq_entry_id=1, chunk_index=0, chunk_text="text", score=score)


def test_above_threshold_is_grounded() -> None:
    assert is_grounded([_chunk(0.9)])


def test_below_threshold_is_not_grounded() -> None:
    assert not is_grounded([_chunk(0.1)])


def test_no_chunks_is_not_grounded() -> None:
    assert not is_grounded([])
