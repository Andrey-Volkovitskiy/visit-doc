"""The gate and verdict contract, from `specs/008-*/contracts/pipeline-gates.md`.

Every case here is derived from that document's tables, not from the implementation:
the floor/cap boundaries, the five rows of the decision table, and one test per stated
invariant. These functions are pure, so the contract is testable in full without a
client, a clock, or a network call.
"""

import pytest
from chat.domain.schemas import FaqVerdict
from chat.rag.pipeline import (
    PipelineOutcome,
    ScoredChunk,
    apply_rerank_gate,
    apply_similarity_gate,
    decide,
)

_FLOOR = 0.3
_CAP = 5
_RERANK_FLOOR = 0.4
_RERANK_CAP = 3


def _chunk(
    similarity: float, *, index: int = 0, rerank: float | None = None, text: str = "t"
) -> ScoredChunk:
    return ScoredChunk(
        faq_entry_id=1,
        chunk_index=index,
        chunk_text=text,
        similarity_score=similarity,
        rerank_score=rerank,
    )


def _ids(chunks: list[ScoredChunk]) -> list[int]:
    return [c.chunk_index for c in chunks]


# --------------------------------------------------------------------------
# apply_similarity_gate
# --------------------------------------------------------------------------


def test_similarity_floor_is_inclusive_at_the_boundary() -> None:
    result = apply_similarity_gate([_chunk(_FLOOR)], floor=_FLOOR, cap=_CAP)

    assert len(result.kept) == 1


def test_similarity_floor_rejects_just_below_the_boundary() -> None:
    result = apply_similarity_gate([_chunk(_FLOOR - 0.001)], floor=_FLOOR, cap=_CAP)

    assert result.kept == []
    assert len(result.dropped_by_floor) == 1


def test_similarity_floor_applies_per_chunk_not_to_the_best_one() -> None:
    # The defect this replaces: one chunk at 0.9 used to admit four weak ones with it.
    pool = [_chunk(0.9, index=0), _chunk(0.1, index=1), _chunk(0.2, index=2)]

    result = apply_similarity_gate(pool, floor=_FLOOR, cap=_CAP)

    assert _ids(result.kept) == [0]
    assert sorted(_ids(result.dropped_by_floor)) == [1, 2]


def test_similarity_cap_is_applied_after_the_floor() -> None:
    pool = [_chunk(0.9 - i / 100, index=i) for i in range(8)]

    result = apply_similarity_gate(pool, floor=_FLOOR, cap=3)

    assert _ids(result.kept) == [0, 1, 2]
    assert _ids(result.dropped_by_cap) == [3, 4, 5, 6, 7]
    assert result.dropped_by_floor == []


def test_dropped_by_floor_and_dropped_by_cap_are_not_conflated() -> None:
    # One says the bar is too high, the other says it is too low. A reader tuning a
    # threshold looks at exactly one of them.
    pool = [_chunk(0.9, index=0), _chunk(0.8, index=1), _chunk(0.05, index=2)]

    result = apply_similarity_gate(pool, floor=_FLOOR, cap=1)

    assert _ids(result.kept) == [0]
    assert _ids(result.dropped_by_cap) == [1]
    assert _ids(result.dropped_by_floor) == [2]


def test_similarity_gate_returns_descending_score_order() -> None:
    pool = [_chunk(0.4, index=0), _chunk(0.9, index=1), _chunk(0.6, index=2)]

    result = apply_similarity_gate(pool, floor=_FLOOR, cap=_CAP)

    assert _ids(result.kept) == [1, 2, 0]


def test_similarity_gate_on_an_empty_pool_returns_empty() -> None:
    result = apply_similarity_gate([], floor=_FLOOR, cap=_CAP)

    assert result.kept == []
    assert result.dropped_by_floor == []
    assert result.dropped_by_cap == []


def test_similarity_gate_never_raises_on_a_short_pool() -> None:
    # A pool smaller than the cap is the ordinary case for a small corpus, not an error.
    result = apply_similarity_gate([_chunk(0.5)], floor=_FLOOR, cap=25)

    assert len(result.kept) == 1


# --------------------------------------------------------------------------
# apply_rerank_gate
# --------------------------------------------------------------------------


def test_rerank_floor_is_inclusive_at_the_boundary() -> None:
    result = apply_rerank_gate(
        [_chunk(0.9, rerank=_RERANK_FLOOR)], floor=_RERANK_FLOOR, cap=_RERANK_CAP
    )

    assert len(result.kept) == 1


def test_rerank_floor_rejects_just_below_the_boundary() -> None:
    result = apply_rerank_gate(
        [_chunk(0.9, rerank=_RERANK_FLOOR - 0.001)],
        floor=_RERANK_FLOOR,
        cap=_RERANK_CAP,
    )

    assert result.kept == []
    assert len(result.dropped_by_floor) == 1


def test_rerank_cap_is_applied_after_the_floor() -> None:
    scored = [_chunk(0.9, index=i, rerank=0.9 - i / 100) for i in range(5)]

    result = apply_rerank_gate(scored, floor=_RERANK_FLOOR, cap=_RERANK_CAP)

    assert _ids(result.kept) == [0, 1, 2]
    assert _ids(result.dropped_by_cap) == [3, 4]


def test_reranked_order_wins_over_similarity_order() -> None:
    # The stage's entire purpose. The chunk the bi-encoder liked least is the one the
    # cross-encoder liked most, and the cross-encoder decides.
    scored = [
        _chunk(0.9, index=0, rerank=0.41),
        _chunk(0.4, index=1, rerank=0.99),
    ]

    result = apply_rerank_gate(scored, floor=_RERANK_FLOOR, cap=_RERANK_CAP)

    assert _ids(result.kept) == [1, 0]


def test_rerank_gate_on_an_empty_list_returns_empty() -> None:
    result = apply_rerank_gate([], floor=_RERANK_FLOOR, cap=_RERANK_CAP)

    assert result.kept == []
    assert result.dropped_by_floor == []


def test_an_unscored_chunk_is_dropped_rather_than_treated_as_passing() -> None:
    # A missing score is not a passing one.
    result = apply_rerank_gate(
        [_chunk(0.9, rerank=None)], floor=_RERANK_FLOOR, cap=_RERANK_CAP
    )

    assert result.kept == []
    assert len(result.dropped_by_floor) == 1


# --------------------------------------------------------------------------
# decide() - the five rows of the decision table
# --------------------------------------------------------------------------


def test_row_1_empty_corpus_abstains_at_the_corpus() -> None:
    outcome = decide([], [], None, corpus_empty=True)

    assert outcome.verdict is FaqVerdict.ABSTAINED_EMPTY_CORPUS
    assert outcome.survivors == []


def test_row_2_a_searched_corpus_that_matched_nothing_abstains_at_the_pool() -> None:
    outcome = decide([], [], None, corpus_empty=False)

    assert outcome.verdict is FaqVerdict.ABSTAINED_EMPTY_POOL
    assert outcome.survivors == []
    assert outcome.observed == []


def test_row_3_nothing_cleared_the_similarity_floor() -> None:
    observed = [_chunk(0.1)]

    outcome = decide(observed, [], None, corpus_empty=False)

    assert outcome.verdict is FaqVerdict.ABSTAINED_SIMILARITY_FLOOR
    assert outcome.survivors == []
    assert outcome.observed == observed


def test_row_4_no_rerank_scores_obtained_answers_unreranked() -> None:
    considered = [_chunk(0.9, index=0), _chunk(0.5, index=1)]

    outcome = decide(considered, considered, None, corpus_empty=False)

    assert outcome.verdict is FaqVerdict.ANSWERED_UNRERANKED
    assert outcome.survivors == considered


def test_row_5_scored_but_none_cleared_the_rerank_floor() -> None:
    considered = [_chunk(0.9)]

    outcome = decide(considered, considered, [], corpus_empty=False)

    assert outcome.verdict is FaqVerdict.ABSTAINED_RERANK_FLOOR
    assert outcome.survivors == []


def test_row_6_reranked_survivors_answer() -> None:
    considered = [_chunk(0.9, index=0), _chunk(0.5, index=1)]
    reranked = [_chunk(0.5, index=1, rerank=0.8)]

    outcome = decide(considered, considered, reranked, corpus_empty=False)

    assert outcome.verdict is FaqVerdict.ANSWERED
    assert outcome.survivors == reranked


def test_none_and_empty_reranked_are_different_answers() -> None:
    # The distinction the fallback turns on: None means the reranker did not answer,
    # [] means it answered "none of these". Conflating them would make a degraded
    # answer and an abstention indistinguishable.
    considered = [_chunk(0.9)]

    fallback = decide(considered, considered, None, corpus_empty=False)
    abstention = decide(considered, considered, [], corpus_empty=False)

    assert fallback.verdict is not abstention.verdict
    assert fallback.verdict.answered
    assert not abstention.verdict.answered


def test_empty_corpus_and_similarity_miss_are_different_verdicts() -> None:
    # Both abstain identically; only the record differs. One is fixed by adding
    # entries, the other by rewriting one or lowering the floor.
    empty = decide([], [], None, corpus_empty=True)
    miss = decide([_chunk(0.1)], [], None, corpus_empty=False)

    assert empty.verdict is not miss.verdict
    assert not empty.verdict.answered
    assert not miss.verdict.answered


def test_an_unmatched_search_and_a_similarity_miss_are_different_verdicts() -> None:
    # A corpus with live revisions the search returned no chunk of is an index behind
    # the rows, not a floor set too high - and no floor rejected anything to lower.
    unmatched = decide([], [], None, corpus_empty=False)
    miss = decide([_chunk(0.1)], [], None, corpus_empty=False)

    assert unmatched.verdict is not miss.verdict
    assert unmatched.verdict is not FaqVerdict.ABSTAINED_EMPTY_CORPUS
    assert not unmatched.verdict.answered


# --------------------------------------------------------------------------
# Invariants - one test per clause of the contract
# --------------------------------------------------------------------------


def _outcomes() -> list[PipelineOutcome]:
    considered = [_chunk(0.9, index=0), _chunk(0.5, index=1)]
    reranked = [_chunk(0.9, index=0, rerank=0.8)]
    return [
        decide([], [], None, corpus_empty=True),
        decide([], [], None, corpus_empty=False),
        decide([_chunk(0.1)], [], None, corpus_empty=False),
        decide(considered, considered, None, corpus_empty=False),
        decide(considered, considered, [], corpus_empty=False),
        decide(considered, considered, reranked, corpus_empty=False),
    ]


@pytest.mark.parametrize("outcome", _outcomes())
def test_survivors_are_non_empty_exactly_when_the_verdict_answered(
    outcome: PipelineOutcome,
) -> None:
    assert bool(outcome.survivors) is outcome.verdict.answered


@pytest.mark.parametrize("outcome", _outcomes())
def test_survivors_are_a_subset_of_considered_which_is_a_subset_of_observed(
    outcome: PipelineOutcome,
) -> None:
    # Both halves, and no escape hatch for an empty `considered`: survivors with
    # nothing considered behind them is exactly the corruption this clause forbids,
    # and a disjunct excusing it would let that case through unnoticed.
    observed = {(c.faq_entry_id, c.chunk_index) for c in outcome.observed}
    considered = {(c.faq_entry_id, c.chunk_index) for c in outcome.considered}
    survivors = {(c.faq_entry_id, c.chunk_index) for c in outcome.survivors}

    assert survivors <= considered
    assert considered <= observed


def test_an_answered_verdict_respects_the_rerank_cap() -> None:
    considered = [_chunk(0.9, index=i) for i in range(5)]
    gate = apply_rerank_gate(
        [c.with_rerank_score(0.9) for c in considered],
        floor=_RERANK_FLOOR,
        cap=_RERANK_CAP,
    )

    outcome = decide(considered, considered, gate.kept, corpus_empty=False)

    assert outcome.verdict is FaqVerdict.ANSWERED
    assert len(outcome.survivors) <= _RERANK_CAP


def test_an_unreranked_verdict_respects_the_similarity_cap() -> None:
    pool = [_chunk(0.9, index=i) for i in range(8)]
    gate = apply_similarity_gate(pool, floor=_FLOOR, cap=_CAP)

    outcome = decide(pool, gate.kept, None, corpus_empty=False)

    assert outcome.verdict is FaqVerdict.ANSWERED_UNRERANKED
    assert len(outcome.survivors) <= _CAP


def test_every_answered_survivor_carries_a_rerank_score() -> None:
    considered = [_chunk(0.9)]
    reranked = [considered[0].with_rerank_score(0.8)]

    outcome = decide(considered, considered, reranked, corpus_empty=False)

    assert all(c.rerank_score is not None for c in outcome.survivors)


def test_every_unreranked_survivor_carries_no_rerank_score() -> None:
    # Absent, never zero: a chunk the cross-encoder never saw was not judged at all.
    considered = [_chunk(0.9)]

    outcome = decide(considered, considered, None, corpus_empty=False)

    assert all(c.rerank_score is None for c in outcome.survivors)
