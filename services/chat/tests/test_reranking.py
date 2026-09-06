"""The reranking port contract, from `specs/008-*/contracts/reranking-port.md`.

`rerank_chunks` is the seam between the pipeline and a cross-encoder. It returns scored
chunks, or None - and only None - for "no scores were obtained". It never returns an
empty list and never raises: an empty list is the *gate's* answer ("scored and
rejected"), and conflating the two would make a degraded answer and an abstention
indistinguishable.
"""

import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from chat.rag.pipeline import ScoredChunk
from chat.rag.reranking import rerank_chunks
from voyageai import error as voyage_error

_MODEL = "rerank-3"

# The closed set from specs/008-*/contracts/log-events.md. Restated here on purpose:
# this is the contract's own copy, and the test below is what holds the two equal.
DOCUMENTED_REASONS = {
    "timeout",
    "transport",
    "refused",
    "rate_limited",
    "unusable_response",
    "unexpected",
}


def _chunk(index: int, similarity: float = 0.9) -> ScoredChunk:
    return ScoredChunk(
        faq_entry_id=1,
        chunk_index=index,
        chunk_text=f"chunk {index}",
        similarity_score=similarity,
    )


def _response(*pairs: tuple[int, float]) -> SimpleNamespace:
    """Build a provider response: (index into the input list, relevance score)."""
    return SimpleNamespace(
        results=[
            SimpleNamespace(index=i, document=f"chunk {i}", relevance_score=s)
            for i, s in pairs
        ]
    )


async def _rerank(
    client: AsyncMock, chunks: list[ScoredChunk], *, timeout: float = 5.0
) -> list[ScoredChunk] | None:
    return await rerank_chunks(
        client, "a question", chunks, model=_MODEL, top_k=5, timeout_seconds=timeout
    )


async def test_scores_land_on_the_right_chunks_by_identity() -> None:
    # By identity, not by list position: the provider returns results in *its* order,
    # referencing input positions. Matching by output position would silently attach
    # the best score to whichever chunk happened to be first.
    chunks = [_chunk(0), _chunk(1), _chunk(2)]
    client = AsyncMock()
    client.rerank.return_value = _response((2, 0.91), (0, 0.55), (1, 0.12))

    scored = await _rerank(client, chunks)

    assert scored is not None
    by_index = {c.chunk_index: c.rerank_score for c in scored}
    assert by_index == {0: 0.55, 1: 0.12, 2: 0.91}


async def test_the_similarity_score_survives_reranking() -> None:
    # Both scores are needed later: the completion record carries each citation's pair,
    # and a disagreement between them is the whole thesis of the phase.
    client = AsyncMock()
    client.rerank.return_value = _response((0, 0.7))

    scored = await _rerank(client, [_chunk(0, similarity=0.42)])

    assert scored is not None
    assert scored[0].similarity_score == 0.42
    assert scored[0].rerank_score == 0.7


async def test_a_response_missing_a_chunk_returns_none_not_a_partial_list() -> None:
    # A chunk with no score would have to be treated as either zero or as passing, and
    # both are inventions. Partial scoring is refused outright.
    client = AsyncMock()
    client.rerank.return_value = _response((0, 0.9))

    scored = await _rerank(client, [_chunk(0), _chunk(1)])

    assert scored is None


async def test_a_response_referencing_an_unknown_index_returns_none() -> None:
    client = AsyncMock()
    client.rerank.return_value = _response((0, 0.9), (7, 0.8))

    scored = await _rerank(client, [_chunk(0)])

    assert scored is None


async def test_an_all_low_scoring_response_is_still_a_list_not_none() -> None:
    # This function scores; it does not gate. "Everything scored badly" must stay
    # distinguishable from "nothing was scored", because only the second falls back.
    client = AsyncMock()
    client.rerank.return_value = _response((0, 0.001))

    scored = await _rerank(client, [_chunk(0)])

    assert scored is not None
    assert scored[0].rerank_score == 0.001


@pytest.mark.parametrize(
    "failure",
    [
        ConnectionError("transport"),
        PermissionError("refused"),
        RuntimeError("rate limited"),
        ValueError("unusable response"),
    ],
)
async def test_every_failure_class_returns_none_and_raises_nothing(
    failure: Exception,
) -> None:
    client = AsyncMock()
    client.rerank.side_effect = failure

    scored = await _rerank(client, [_chunk(0)])

    assert scored is None


async def test_a_malformed_response_returns_none() -> None:
    client = AsyncMock()
    client.rerank.return_value = SimpleNamespace()  # no `.results` at all

    scored = await _rerank(client, [_chunk(0)])

    assert scored is None


async def test_a_slow_client_yields_none_within_the_deadline() -> None:
    async def never_returns(*_args: object, **_kwargs: object) -> object:
        await asyncio.sleep(30)
        raise AssertionError("should have been abandoned at the deadline")

    client = AsyncMock()
    client.rerank = never_returns

    started = asyncio.get_running_loop().time()
    scored = await _rerank(client, [_chunk(0)], timeout=0.05)
    elapsed = asyncio.get_running_loop().time() - started

    assert scored is None
    assert elapsed < 5.0


async def test_a_failing_call_is_not_retried() -> None:
    # A retry in front of a fallback spends the deadline twice to reach the answer it
    # already had.
    client = AsyncMock()
    client.rerank.side_effect = ConnectionError("down")

    await _rerank(client, [_chunk(0)])

    assert client.rerank.await_count == 1


async def test_a_failure_leaves_no_state_that_affects_the_next_call() -> None:
    # No breaker, no counter, no cooldown: turn 100 of an outage behaves exactly as
    # turn 1 did, and recovery needs nothing reset.
    client = AsyncMock()
    client.rerank.side_effect = [ConnectionError("down"), _response((0, 0.8))]

    first = await _rerank(client, [_chunk(0)])
    second = await _rerank(client, [_chunk(0)])

    assert first is None
    assert second is not None
    assert second[0].rerank_score == 0.8


async def test_an_empty_shortlist_is_never_sent_to_the_provider() -> None:
    client = AsyncMock()

    scored = await _rerank(client, [])

    assert scored is None
    client.rerank.assert_not_awaited()


async def test_no_input_at_all_ever_makes_this_return_an_empty_list() -> None:
    # `[]` is the gate's answer - "scored and rejected", which abstains - so this
    # function returning it for any reason would collapse the fallback into an
    # abstention. Every path here is a populated list or None.
    client = AsyncMock()
    client.rerank.return_value = SimpleNamespace(results=[])

    assert await _rerank(client, []) is None
    assert await _rerank(client, [_chunk(0)]) is None


@pytest.mark.parametrize(
    "results",
    [
        42,  # not a sequence at all
        [SimpleNamespace(index="0", relevance_score=0.5)],  # index is not a number
        [SimpleNamespace(index=0, relevance_score="high")],  # score is not a number
    ],
)
async def test_a_response_this_cannot_read_returns_none_rather_than_raising(
    results: object,
) -> None:
    # Unusable in exactly the sense a short response is unusable. Letting the
    # conversion raise would turn a degraded answer into a failed turn, which is the
    # one thing this port promises never to do.
    client = AsyncMock()
    client.rerank.return_value = SimpleNamespace(results=results)

    assert await _rerank(client, [_chunk(0)]) is None


# --- the failure reason is a metric, so it is classified on type, not on words -------


async def test_the_reason_is_not_decided_by_words_in_the_message() -> None:
    """A message mentioning a file path must not be filed as a rate limit.

    This is a regression: the first version matched substrings, and "rate" inside
    "docs/testing-strategy.md" made every guard failure report `rate_limited`. The
    reason is read as a metric, so a confidently wrong one is worse than a coarse one.
    """
    from structlog.testing import capture_logs

    client = AsyncMock()
    client.rerank.side_effect = RuntimeError("see docs/testing-strategy.md for author")

    with capture_logs() as logs:
        assert await _rerank(client, [_chunk(0)]) is None

    event = next(e for e in logs if e["event"] == "faq.reranking_unavailable")
    assert event["reason"] == "unexpected"


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (voyage_error.RateLimitError("slow down"), "rate_limited"),
        (voyage_error.AuthenticationError("bad key"), "refused"),
        (voyage_error.APIConnectionError("no route"), "transport"),
        (voyage_error.ServiceUnavailableError("503"), "transport"),
        (voyage_error.ServerError("500"), "transport"),
        # Not a builtin `TimeoutError`: the provider's own timeout is a `VoyageError`,
        # so classifying on the builtin alone files a vendor timeout as `unexpected`.
        (voyage_error.Timeout("request timed out"), "transport"),
        (voyage_error.InvalidRequestError("bad body"), "unusable_response"),
        (voyage_error.MalformedRequestError("422"), "unusable_response"),
        (voyage_error.APIError("unparseable body"), "unusable_response"),
    ],
)
async def test_each_provider_failure_reports_its_own_reason(
    failure: Exception, expected: str
) -> None:
    from structlog.testing import capture_logs

    client = AsyncMock()
    client.rerank.side_effect = failure

    with capture_logs() as logs:
        assert await _rerank(client, [_chunk(0)]) is None

    event = next(e for e in logs if e["event"] == "faq.reranking_unavailable")
    assert event["reason"] == expected


async def test_the_completed_event_reports_how_long_the_call_took() -> None:
    # The only per-stage latency signal for this call, and what SC-009's claim rests on.
    from structlog.testing import capture_logs

    client = AsyncMock()
    client.rerank.return_value = _response((0, 0.8))

    with capture_logs() as logs:
        await _rerank(client, [_chunk(0)])

    event = next(e for e in logs if e["event"] == "faq.reranking_completed")
    assert isinstance(event["duration_ms"], float)
    assert event["duration_ms"] >= 0


def test_every_reason_the_code_can_emit_is_in_the_documented_set() -> None:
    """The emitted reasons and the contract's closed set must be the same set.

    Equality, not inclusion, because each direction is its own defect: a reason the code
    emits but the contract omits is an undocumented value in a field Phase 2 reads as a
    metric, and a documented reason nothing emits is a category an operator waits for
    that never arrives. The two drifted apart once already - `unexpected` was added to
    the code when the classifier stopped matching substrings, and the contract kept
    listing five.
    """
    import inspect

    from chat.rag import reranking

    classifier = inspect.getsource(reranking._reason_for)
    caller = inspect.getsource(reranking.rerank_chunks)
    emitted = set(re.findall(r'return "([a-z_]+)"', classifier))
    emitted |= set(re.findall(r'reason="([a-z_]+)"', caller))

    assert emitted == DOCUMENTED_REASONS
