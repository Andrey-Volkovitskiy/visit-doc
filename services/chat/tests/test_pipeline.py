"""The merge, gate and verdict contract.

The gates and the decision table come from `specs/008-*/contracts/pipeline-gates.md`;
since 018 the first gate keeps the dense top and adds the lexical top, with no floor.
These functions are pure, so the contract is testable in full without a client, a
clock, or a network call.
"""

from dataclasses import replace

import pytest
from chat.domain.schemas import FaqVerdict
from chat.rag.pipeline import (
    GateResult,
    PipelineOutcome,
    ScoredChunk,
    apply_rerank_gate,
    apply_shortlist_gate,
    decide,
    merge_branches,
)

_CAP = 5
_RERANK_FLOOR = 0.4
_RERANK_CAP = 3


def _chunk(
    similarity: float | None,
    *,
    index: int = 0,
    rerank: float | None = None,
    text: str = "t",
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
# merge_branches
# --------------------------------------------------------------------------


def _dense(*indexes: int) -> list[ScoredChunk]:
    """The dense branch's hits, best first, each carrying a cosine score."""
    return [_chunk(0.9 - rank / 10, index=i) for rank, i in enumerate(indexes)]


def _lexical(*indexes: int) -> list[ScoredChunk]:
    """The lexical branch's hits, best first, carrying a BM25 score and no cosine."""
    return [
        ScoredChunk(
            faq_entry_id=1,
            chunk_index=i,
            chunk_text="t",
            similarity_score=None,
            lexical_score=10.0 - rank,
        )
        for rank, i in enumerate(indexes)
    ]


def test_the_merged_pool_keeps_the_dense_order_whatever_the_lexical_branch_says() -> (
    None
):
    # The lexical branch ranks them the other way round. It adds candidates; it never
    # reorders what dense search found, which is what let it displace the right chunk.
    merged = merge_branches(_dense(0, 1, 2), _lexical(2, 1, 0))

    assert _ids(merged) == [0, 1, 2]


def test_chunks_only_the_lexical_branch_found_follow_in_lexical_order() -> None:
    merged = merge_branches(_dense(0), _lexical(4, 0, 3))

    assert _ids(merged) == [0, 4, 3]


def test_each_merged_chunk_keeps_its_rank_and_score_in_each_branch() -> None:
    merged = {c.chunk_index: c for c in merge_branches(_dense(0, 1), _lexical(1, 2))}

    assert (merged[0].dense_rank, merged[0].lexical_rank) == (1, None)
    assert (merged[1].dense_rank, merged[1].lexical_rank) == (2, 1)
    assert (merged[2].dense_rank, merged[2].lexical_rank) == (None, 2)
    assert merged[1].similarity_score == pytest.approx(0.8)
    assert merged[1].lexical_score == 10.0
    assert merged[0].lexical_score is None


def test_a_chunk_only_the_lexical_branch_found_has_no_cosine_score() -> None:
    # Not 0.0 and not its BM25 score: the dense branch never scored it, and a cosine
    # floor read against a made-up number would pass or fail it for no reason.
    merged = merge_branches(_dense(0), _lexical(5))

    lexical_only = next(c for c in merged if c.chunk_index == 5)
    assert lexical_only.similarity_score is None


def test_merging_never_returns_a_chunk_twice() -> None:
    merged = merge_branches(_dense(0, 1, 2), _lexical(2, 1, 0, 3))

    assert sorted(_ids(merged)) == [0, 1, 2, 3]


def test_merging_two_empty_branches_is_empty() -> None:
    assert merge_branches([], []) == []


def test_merging_one_empty_branch_keeps_the_other_in_its_own_order() -> None:
    assert _ids(merge_branches(_dense(3, 1, 2), [])) == [3, 1, 2]
    assert _ids(merge_branches([], _lexical(3, 1, 2))) == [3, 1, 2]


# --------------------------------------------------------------------------
# apply_shortlist_gate
# --------------------------------------------------------------------------


def _gate(
    pool: list[ScoredChunk], dense_cap: int = _CAP, lexical_cap: int = 3
) -> GateResult:
    return apply_shortlist_gate(pool, dense_cap=dense_cap, lexical_cap=lexical_cap)


def test_the_shortlist_keeps_the_dense_top_whatever_the_lexical_branch_returned() -> (
    None
):
    # The guarantee the variant exists for: BM25 adds to the shortlist and never takes
    # a dense hit's place, so nothing dense retrieval finds can be lost to it.
    pool = merge_branches(_dense(*range(8)), _lexical(7, 6, 5, 4, 3, 2, 1, 0))

    result = _gate(pool, lexical_cap=3)

    assert {0, 1, 2, 3, 4} <= set(_ids(result.kept))


def test_the_shortlist_adds_the_lexical_top_dense_search_ranked_lower() -> None:
    pool = merge_branches(_dense(*range(10)), _lexical(9, 8, 0, 7))

    result = _gate(pool, lexical_cap=3)

    assert _ids(result.kept) == [0, 1, 2, 3, 4, 8, 9]
    assert _ids(result.dropped_by_cap) == [5, 6, 7]


def test_the_shortlist_adds_a_lexical_top_chunk_dense_search_never_returned() -> None:
    pool = merge_branches(_dense(0, 1), _lexical(7))

    result = _gate(pool)

    assert _ids(result.kept) == [0, 1, 7]


def test_a_lexical_top_chunk_already_on_the_dense_top_takes_no_extra_place() -> None:
    # The lexical top is counted by rank, not topped up from further down: when both
    # branches agree, the shortlist is just the dense top.
    pool = merge_branches(_dense(*range(8)), _lexical(0, 1, 2, 6))

    result = _gate(pool, lexical_cap=3)

    assert _ids(result.kept) == [0, 1, 2, 3, 4]


def test_the_shortlist_keeps_the_pool_order() -> None:
    pool = merge_branches(_dense(0, 1, 2), _lexical(9, 2))

    assert _ids(_gate(pool, dense_cap=1, lexical_cap=2).kept) == [0, 2, 9]


def test_the_shortlist_admits_a_chunk_with_no_cosine_score() -> None:
    result = _gate(merge_branches([], _lexical(0)))

    assert len(result.kept) == 1
    assert result.kept[0].similarity_score is None


def test_the_shortlist_drops_nothing_by_a_floor() -> None:
    # A low cosine on the dense top is still on the dense top; the rerank floor is what
    # decides an abstention.
    low = [replace(c, similarity_score=-0.5) for c in merge_branches(_dense(0), [])]

    result = _gate(low)

    assert len(result.kept) == 1
    assert result.dropped_by_floor == []


def test_the_shortlist_on_an_empty_pool_returns_empty() -> None:
    result = _gate([])

    assert result.kept == []
    assert result.dropped_by_cap == []


def test_the_shortlist_never_raises_on_a_short_pool() -> None:
    # A pool smaller than the caps is the ordinary case for a small corpus.
    result = _gate(merge_branches(_dense(0), []), dense_cap=25)

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

# The floor a reranker outage answers through. An input to a pure function here, not a
# restatement of the setting: each test says which side of it its chunks sit on.
_FALLBACK_FLOOR = 0.25


def test_row_1_empty_corpus_abstains_at_the_corpus() -> None:
    outcome = decide([], [], None, corpus_empty=True, unreranked_floor=_FALLBACK_FLOOR)

    assert outcome.verdict is FaqVerdict.ABSTAINED_EMPTY_CORPUS
    assert outcome.survivors == []


def test_row_2_a_searched_corpus_that_matched_nothing_abstains_at_the_pool() -> None:
    outcome = decide([], [], None, corpus_empty=False, unreranked_floor=_FALLBACK_FLOOR)

    assert outcome.verdict is FaqVerdict.ABSTAINED_EMPTY_POOL
    assert outcome.survivors == []
    assert outcome.observed == []


def test_row_3_nothing_cleared_the_similarity_floor() -> None:
    observed = [_chunk(0.1)]

    outcome = decide(
        observed, [], None, corpus_empty=False, unreranked_floor=_FALLBACK_FLOOR
    )

    assert outcome.verdict is FaqVerdict.ABSTAINED_SIMILARITY_FLOOR
    assert outcome.survivors == []
    assert outcome.observed == observed


def test_row_4_no_rerank_scores_obtained_answers_unreranked() -> None:
    considered = [_chunk(0.9, index=0), _chunk(0.5, index=1)]

    outcome = decide(
        considered,
        considered,
        None,
        corpus_empty=False,
        unreranked_floor=_FALLBACK_FLOOR,
    )

    assert outcome.verdict is FaqVerdict.ANSWERED_UNRERANKED
    assert outcome.survivors == considered


def test_row_4_an_unreranked_answer_keeps_only_chunks_above_the_fallback_floor() -> (
    None
):
    above = _chunk(_FALLBACK_FLOOR + 0.1, index=0)
    below = _chunk(_FALLBACK_FLOOR - 0.1, index=1)

    outcome = decide(
        [above, below],
        [above, below],
        None,
        corpus_empty=False,
        unreranked_floor=_FALLBACK_FLOOR,
    )

    assert outcome.verdict is FaqVerdict.ANSWERED_UNRERANKED
    assert outcome.survivors == [above]
    assert outcome.considered == [above, below]


def test_row_4_an_unreranked_answer_never_stands_on_a_chunk_with_no_cosine() -> None:
    # A chunk only the lexical branch found has no cosine score, and a missing score is
    # not a passing one: with the reranker down nothing else has judged it.
    scored = _chunk(_FALLBACK_FLOOR + 0.1, index=0)
    lexical_only = _chunk(None, index=1)

    outcome = decide(
        [lexical_only, scored],
        [lexical_only, scored],
        None,
        corpus_empty=False,
        unreranked_floor=_FALLBACK_FLOOR,
    )

    assert outcome.verdict is FaqVerdict.ANSWERED_UNRERANKED
    assert outcome.survivors == [scored]


def test_row_4_an_unreranked_shortlist_of_lexical_only_chunks_abstains() -> None:
    considered = [_chunk(None, index=0), _chunk(None, index=1)]

    outcome = decide(
        considered,
        considered,
        None,
        corpus_empty=False,
        unreranked_floor=_FALLBACK_FLOOR,
    )

    assert outcome.verdict is FaqVerdict.ABSTAINED_SIMILARITY_FLOOR
    assert outcome.survivors == []


def test_row_4_an_unreranked_shortlist_wholly_below_the_fallback_floor_abstains() -> (
    None
):
    # Nothing judged the shortlist, so it is never answered from as the cap left it.
    considered = [_chunk(_FALLBACK_FLOOR - 0.1)]

    outcome = decide(
        considered,
        considered,
        None,
        corpus_empty=False,
        unreranked_floor=_FALLBACK_FLOOR,
    )

    assert outcome.verdict is FaqVerdict.ABSTAINED_SIMILARITY_FLOOR
    assert outcome.survivors == []
    assert outcome.considered == considered


def test_row_5_scored_but_none_cleared_the_rerank_floor() -> None:
    considered = [_chunk(0.9)]

    outcome = decide(
        considered, considered, [], corpus_empty=False, unreranked_floor=_FALLBACK_FLOOR
    )

    assert outcome.verdict is FaqVerdict.ABSTAINED_RERANK_FLOOR
    assert outcome.survivors == []


def test_row_6_reranked_survivors_answer() -> None:
    considered = [_chunk(0.9, index=0), _chunk(0.5, index=1)]
    reranked = [_chunk(0.5, index=1, rerank=0.8)]

    outcome = decide(
        considered,
        considered,
        reranked,
        corpus_empty=False,
        unreranked_floor=_FALLBACK_FLOOR,
    )

    assert outcome.verdict is FaqVerdict.ANSWERED
    assert outcome.survivors == reranked


def test_none_and_empty_reranked_are_different_answers() -> None:
    # The distinction the fallback turns on: None means the reranker did not answer,
    # [] means it answered "none of these". Conflating them would make a degraded
    # answer and an abstention indistinguishable.
    considered = [_chunk(0.9)]

    fallback = decide(
        considered,
        considered,
        None,
        corpus_empty=False,
        unreranked_floor=_FALLBACK_FLOOR,
    )
    abstention = decide(
        considered, considered, [], corpus_empty=False, unreranked_floor=_FALLBACK_FLOOR
    )

    assert fallback.verdict is not abstention.verdict
    assert fallback.verdict.answered
    assert not abstention.verdict.answered


def test_empty_corpus_and_similarity_miss_are_different_verdicts() -> None:
    # Both abstain identically; only the record differs. One is fixed by adding
    # entries, the other by rewriting one or lowering the floor.
    empty = decide([], [], None, corpus_empty=True, unreranked_floor=_FALLBACK_FLOOR)
    miss = decide(
        [_chunk(0.1)], [], None, corpus_empty=False, unreranked_floor=_FALLBACK_FLOOR
    )

    assert empty.verdict is not miss.verdict
    assert not empty.verdict.answered
    assert not miss.verdict.answered


def test_an_unmatched_search_and_a_similarity_miss_are_different_verdicts() -> None:
    # A corpus with live revisions the search returned no chunk of is an index behind
    # the rows, not a floor set too high - and no floor rejected anything to lower.
    unmatched = decide(
        [], [], None, corpus_empty=False, unreranked_floor=_FALLBACK_FLOOR
    )
    miss = decide(
        [_chunk(0.1)], [], None, corpus_empty=False, unreranked_floor=_FALLBACK_FLOOR
    )

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
        decide([], [], None, corpus_empty=True, unreranked_floor=_FALLBACK_FLOOR),
        decide([], [], None, corpus_empty=False, unreranked_floor=_FALLBACK_FLOOR),
        decide(
            [_chunk(0.1)],
            [],
            None,
            corpus_empty=False,
            unreranked_floor=_FALLBACK_FLOOR,
        ),
        decide(
            considered,
            considered,
            None,
            corpus_empty=False,
            unreranked_floor=_FALLBACK_FLOOR,
        ),
        decide(
            considered,
            considered,
            [],
            corpus_empty=False,
            unreranked_floor=_FALLBACK_FLOOR,
        ),
        decide(
            considered,
            considered,
            reranked,
            corpus_empty=False,
            unreranked_floor=_FALLBACK_FLOOR,
        ),
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

    outcome = decide(
        considered,
        considered,
        gate.kept,
        corpus_empty=False,
        unreranked_floor=_FALLBACK_FLOOR,
    )

    assert outcome.verdict is FaqVerdict.ANSWERED
    assert len(outcome.survivors) <= _RERANK_CAP


def test_an_unreranked_verdict_respects_the_similarity_cap() -> None:
    pool = merge_branches([_chunk(0.9, index=i) for i in range(8)], [])
    gate = _gate(pool)

    outcome = decide(
        pool, gate.kept, None, corpus_empty=False, unreranked_floor=_FALLBACK_FLOOR
    )

    assert outcome.verdict is FaqVerdict.ANSWERED_UNRERANKED
    assert len(outcome.survivors) <= _CAP


def test_every_answered_survivor_carries_a_rerank_score() -> None:
    considered = [_chunk(0.9)]
    reranked = [considered[0].with_rerank_score(0.8)]

    outcome = decide(
        considered,
        considered,
        reranked,
        corpus_empty=False,
        unreranked_floor=_FALLBACK_FLOOR,
    )

    assert all(c.rerank_score is not None for c in outcome.survivors)


def test_every_unreranked_survivor_carries_no_rerank_score() -> None:
    # Absent, never zero: a chunk the cross-encoder never saw was not judged at all.
    considered = [_chunk(0.9)]

    outcome = decide(
        considered,
        considered,
        None,
        corpus_empty=False,
        unreranked_floor=_FALLBACK_FLOOR,
    )

    assert all(c.rerank_score is None for c in outcome.survivors)
