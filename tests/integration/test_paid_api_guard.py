"""Tests that this tier's autouse paid-API guard in `conftest.py` is actually armed.

The same checks as `services/chat/tests/test_paid_api_guard.py`, against this tier's
own copy of the guard: a `patch` target that silently stops resolving would look exactly
like a tier that never calls a paid API.
"""

import pytest
from anthropic import AsyncAnthropic
from chat.core.config import Settings as ChatSettings
from chat.rag.pipeline import ScoredChunk
from chat.rag.reranking import rerank_chunks
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

    with pytest.raises(PaidAPICallInTestError, match="Voyage embed"):
        await client.embed(["hello"], model="voyage-3.5-lite")


async def test_a_real_voyage_reranking_call_is_blocked() -> None:
    client = VoyageAsyncClient(api_key=_NOT_A_REAL_KEY)

    with pytest.raises(PaidAPICallInTestError, match="Voyage rerank"):
        await client.rerank("a question", ["a chunk"], model="rerank-3")


async def test_the_block_survives_production_code_that_swallows_every_exception() -> (
    None
):
    """`rerank_chunks` swallows every `Exception`; the guard is not one."""
    client = VoyageAsyncClient(api_key=_NOT_A_REAL_KEY)
    chunk = ScoredChunk(
        faq_entry_id=1, chunk_index=0, chunk_text="a chunk", similarity_score=0.9
    )

    with pytest.raises(PaidAPICallInTestError, match="Voyage rerank"):
        await rerank_chunks(
            client, "a question", [chunk], model="rerank-3", top_k=1, timeout_seconds=5
        )


def test_this_tier_traces_nothing() -> None:
    """No turn this tier drives may reach a developer's Langfuse project.

    The keys live in the repo's `.env`, so an app built here would export its turns to
    them - and wait on a flush to the network at every lifespan's end. `conftest.py`
    blanks both; this is what says so out loud.
    """
    assert ChatSettings().tracing_enabled is False
