"""Tests that the autouse paid-API guard in `conftest.py` is actually armed.

Without these, the guard failing open - a renamed SDK attribute, a `patch` target that
silently no longer resolves - would look exactly like a suite that never calls a paid
API, which is the state it exists to distinguish from.
"""

import pytest
from anthropic import AsyncAnthropic
from voyageai.client_async import AsyncClient as VoyageAsyncClient

from .conftest import PaidAPICallInTestError

_NOT_A_REAL_KEY = "not-a-real-key"


async def test_a_real_anthropic_generation_call_is_blocked() -> None:
    client = AsyncAnthropic(api_key=_NOT_A_REAL_KEY)

    with pytest.raises(PaidAPICallInTestError, match=r"messages\.stream"):
        async with client.messages.stream(
            model="claude-sonnet-5",
            max_tokens=16,
            messages=[{"role": "user", "content": "hello"}],
        ):
            pass


async def test_a_real_anthropic_classification_call_is_blocked() -> None:
    client = AsyncAnthropic(api_key=_NOT_A_REAL_KEY)

    with pytest.raises(PaidAPICallInTestError, match=r"messages\.create"):
        await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=16,
            messages=[{"role": "user", "content": "hello"}],
        )


async def test_a_real_voyage_embedding_call_is_blocked() -> None:
    client = VoyageAsyncClient(api_key=_NOT_A_REAL_KEY)

    with pytest.raises(PaidAPICallInTestError, match="Voyage"):
        await client.embed(["hello"], model="voyage-3.5-lite")


async def test_a_real_voyage_reranking_call_is_blocked() -> None:
    client = VoyageAsyncClient(api_key=_NOT_A_REAL_KEY)

    with pytest.raises(PaidAPICallInTestError, match="Voyage rerank"):
        await client.rerank("a question", ["a chunk"], model="rerank-3")


async def test_the_block_names_what_to_patch_instead() -> None:
    client = AsyncAnthropic(api_key=_NOT_A_REAL_KEY)

    with pytest.raises(PaidAPICallInTestError) as blocked:
        await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=16,
            messages=[{"role": "user", "content": "hello"}],
        )

    assert "fake_anthropic_client" in str(blocked.value)


async def test_the_block_survives_production_code_that_swallows_every_exception() -> (
    None
):
    """`rerank_chunks` absorbs every `Exception` by requirement - including this guard.

    So the guard is not one: it is a `BaseException`, and this is what holds it there.
    Were it an `Exception`, the call below would return `None` and the test would pass
    while a live, billed request had been attempted.
    """
    from chat.rag.pipeline import ScoredChunk
    from chat.rag.reranking import rerank_chunks

    client = VoyageAsyncClient(api_key=_NOT_A_REAL_KEY)
    chunk = ScoredChunk(
        faq_entry_id=1, chunk_index=0, chunk_text="a chunk", similarity_score=0.9
    )

    with pytest.raises(PaidAPICallInTestError, match="Voyage rerank"):
        await rerank_chunks(
            client, "a question", [chunk], model="rerank-3", top_k=1, timeout_seconds=5
        )
