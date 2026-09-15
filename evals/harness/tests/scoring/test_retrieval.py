"""Retrieval metrics over the hand-written `fixtures/runs/us2` run.

Expected values, counted by hand from the fixture's case files and `labels.json`. A rank
is 1-based in that stage's own list: `candidates` order for similarity, `scores` sorted
by descending `rerank_score` for rerank. "-" is a scored request with no cited chunk
ranked.

    case      label                     similarity   kept?  rerank
    G921      referral                  2            yes    1  (scores listed 2nd)
    G922      referral                  2            yes    1
    G923      gap                       not scored (FR-028)
    G924      telehealth                1            yes    reranker_unavailable
    G925      payment                   no_search (empty corpus)
    G926      arrival-time              unaligned - not scored
    G927      hours-location            handed_off_turn
    G928 p0   insurance-plans           1            yes    1
    G929      gap                       not scored
    G930      out-of-network, oop-rates 3 (2nd cite) yes    1
    G931      what-to-bring             3            no     not_reached_reranker
    G932 p0   hours-location            2            yes    1  (events interleaved)
    G932 p1   payment                   2            yes    2
    G933      hours-location            not_routed_to_faq (produced as small_talk)
    G934      arrival-time              -            no     not_reached_reranker

    similarity: 9 scored   hit@1 2   hit@3 8   hit@5 8   MRR sum 14/3
                excluded   no_search 1, not_routed_to_faq 1, handed_off_turn 1
    survival:   7 / 9
    rerank:     6 scored   hit@1 5   hit@3 6   hit@5 6   MRR sum 5.5
                excluded   no_search 1, not_routed_to_faq 1, handed_off_turn 1,
                           reranker_unavailable 1, not_reached_reranker 2
"""

from pathlib import Path

import pytest
from golden_harness.cases import Case, load_cases
from golden_harness.record import (
    CaseRun,
    ExclusionReason,
    read_case,
    read_run,
    recorded_case_ids,
)
from golden_harness.scoring.alignment import align_run
from golden_harness.scoring.retrieval import (
    RetrievalRequest,
    RetrievalScores,
    Stage,
    score_retrieval,
)
from pydantic import JsonValue

_RUN = Path(__file__).resolve().parents[1] / "fixtures" / "runs" / "us2"
_SCHEMA = Path(__file__).resolve().parents[4] / "evals" / "golden" / "schema.json"


def _labels() -> list[Case]:
    return load_cases(_RUN / "labels.json", _SCHEMA)


def _case_runs() -> list[CaseRun]:
    return [read_case(_RUN, case_id) for case_id in recorded_case_ids(_RUN)]


def _score(
    case_runs: list[CaseRun] | None = None,
    *,
    entry_ids: dict[str, int] | None = None,
    similarity_cap: int | None = None,
) -> RetrievalScores:
    runs = case_runs if case_runs is not None else _case_runs()
    run = read_run(_RUN)
    labels = _labels()
    return score_retrieval(
        labels,
        align_run(labels, runs),
        runs,
        entry_ids=entry_ids if entry_ids is not None else run.entry_ids,
        similarity_cap=(
            similarity_cap
            if similarity_cap is not None
            else run.conditions.similarity_cap
        ),
    )


def _request(
    scores: RetrievalScores, case_id: str, position: int = 0
) -> RetrievalRequest:
    matches = [
        r for r in scores.requests if (r.case_id, r.position) == (case_id, position)
    ]
    assert len(matches) == 1, f"{case_id}/{position}: {matches}"
    return matches[0]


def _with_events(case_id: str, events: list[dict[str, JsonValue]]) -> list[CaseRun]:
    return [
        run.model_copy(update={"events": events}) if run.case_id == case_id else run
        for run in _case_runs()
    ]


def _events_of(case_id: str) -> list[dict[str, JsonValue]]:
    events = read_case(_RUN, case_id).events
    assert events is not None
    return [dict(event) for event in events]


# --- the ranked lists, per stage --------------------------------------------------


def test_as1_the_similarity_rank_is_the_position_in_candidates() -> None:
    request = _request(_score(), "G921")

    assert request.similarity.rank == 2
    assert request.similarity.excluded is None


def test_as1_the_rerank_rank_sorts_scores_by_descending_rerank_score() -> None:
    request = _request(_score(), "G921")

    assert request.rerank.rank == 1
    assert request.rerank.excluded is None


def test_segments_are_joined_by_segment_not_by_event_order() -> None:
    scores = _score()

    assert _request(scores, "G932", 0).similarity.rank == 2
    assert _request(scores, "G932", 1).similarity.rank == 2
    assert _request(scores, "G932", 0).rerank.rank == 1
    assert _request(scores, "G932", 1).rerank.rank == 2


def test_the_first_chunk_of_any_cited_entry_decides_the_rank() -> None:
    request = _request(_score(), "G930")

    assert request.similarity.rank == 3
    assert request.rerank.rank == 1


def test_a_request_with_no_cited_chunk_ranked_has_no_rank_and_is_still_scored() -> None:
    request = _request(_score(), "G934")

    assert request.similarity.rank is None
    assert request.similarity.excluded is None


# --- hit@k and MRR ----------------------------------------------------------------


def test_as1_is_a_hit_at_3_and_5_but_not_at_1() -> None:
    without_g921 = [run for run in _case_runs() if run.case_id != "G921"]
    full, reduced = _score(), _score(without_g921)

    assert full.similarity_hit_at_1.numerator == reduced.similarity_hit_at_1.numerator
    assert (
        full.similarity_hit_at_3.numerator == reduced.similarity_hit_at_3.numerator + 1
    )
    assert (
        full.similarity_hit_at_5.numerator == reduced.similarity_hit_at_5.numerator + 1
    )
    assert full.similarity_mrr.numerator == pytest.approx(
        reduced.similarity_mrr.numerator + 0.5
    )
    assert full.similarity_mrr.denominator == reduced.similarity_mrr.denominator + 1


def test_a_request_with_no_cited_chunk_ranked_contributes_zero_to_mrr() -> None:
    without_g934 = [run for run in _case_runs() if run.case_id != "G934"]
    full, reduced = _score(), _score(without_g934)

    assert full.similarity_mrr.numerator == pytest.approx(
        reduced.similarity_mrr.numerator
    )
    assert full.similarity_mrr.denominator == reduced.similarity_mrr.denominator + 1
    assert full.similarity_hit_at_5.numerator == reduced.similarity_hit_at_5.numerator


def test_similarity_stage_values() -> None:
    scores = _score()

    assert [
        (m.numerator, m.denominator)
        for m in (
            scores.similarity_hit_at_1,
            scores.similarity_hit_at_3,
            scores.similarity_hit_at_5,
        )
    ] == [(2, 9), (8, 9), (8, 9)]
    assert scores.similarity_mrr.numerator == pytest.approx(14 / 3)
    assert scores.similarity_mrr.denominator == 9


def test_rerank_stage_values() -> None:
    scores = _score()

    assert [
        (m.numerator, m.denominator)
        for m in (
            scores.rerank_hit_at_1,
            scores.rerank_hit_at_3,
            scores.rerank_hit_at_5,
        )
    ] == [(5, 6), (6, 6), (6, 6)]
    assert scores.rerank_mrr.numerator == pytest.approx(5.5)
    assert scores.rerank_mrr.denominator == 6


def test_the_two_stages_are_never_pooled() -> None:
    scores = _score()

    similarity = {m.name: m for m in scores.stage_metrics(Stage.SIMILARITY)}
    rerank = {m.name: m for m in scores.stage_metrics(Stage.RERANK)}
    assert set(similarity) == {
        "similarity_hit_at_1",
        "similarity_hit_at_3",
        "similarity_hit_at_5",
        "similarity_mrr",
    }
    assert set(rerank) == {
        "rerank_hit_at_1",
        "rerank_hit_at_3",
        "rerank_hit_at_5",
        "rerank_mrr",
    }
    assert similarity["similarity_mrr"].denominator == 9
    assert rerank["rerank_mrr"].denominator == 6


# --- who is scored ----------------------------------------------------------------


def test_only_aligned_answerable_faq_requests_are_scored() -> None:
    scored = {(r.case_id, r.position) for r in _score().requests}

    assert ("G923", 0) not in scored
    assert ("G929", 0) not in scored
    assert ("G926", 0) not in scored
    assert ("G926", 1) not in scored
    assert ("G928", 1) not in scored


def test_unaligned_answerable_requests_are_counted_beside_the_metrics() -> None:
    assert _score().unaligned_requests == 1


def test_reranker_unavailable_excludes_the_rerank_stage_only() -> None:
    request = _request(_score(), "G924")

    assert request.similarity.excluded is None
    assert request.similarity.rank == 1
    assert request.rerank.excluded is ExclusionReason.RERANKER_UNAVAILABLE
    assert request.rerank.rank is None


def test_an_empty_corpus_is_no_search_in_both_stages() -> None:
    request = _request(_score(), "G925")

    assert request.similarity.excluded is ExclusionReason.NO_SEARCH
    assert request.rerank.excluded is ExclusionReason.NO_SEARCH


def test_a_faq_request_produced_under_another_intent_is_not_routed_to_faq() -> None:
    request = _request(_score(), "G933")

    assert request.similarity.excluded is ExclusionReason.NOT_ROUTED_TO_FAQ
    assert request.rerank.excluded is ExclusionReason.NOT_ROUTED_TO_FAQ


def test_a_handed_off_turn_is_excluded_from_both_stages() -> None:
    request = _request(_score(), "G927")

    assert request.similarity.excluded is ExclusionReason.HANDED_OFF_TURN
    assert request.rerank.excluded is ExclusionReason.HANDED_OFF_TURN


def test_a_gate_dropped_request_is_still_scored_for_similarity() -> None:
    request = _request(_score(), "G931")

    assert request.similarity.excluded is None
    assert request.similarity.rank == 3
    assert request.survived_similarity_gate is False
    assert request.rerank.excluded is ExclusionReason.NOT_REACHED_RERANKER


def test_the_rerank_stage_scores_only_requests_whose_cited_chunk_was_kept() -> None:
    scores = _score()

    rerank_scored = {
        (r.case_id, r.position) for r in scores.requests if r.rerank.excluded is None
    }
    assert rerank_scored == {
        ("G921", 0),
        ("G922", 0),
        ("G928", 0),
        ("G930", 0),
        ("G932", 0),
        ("G932", 1),
    }


def test_stage_exclusions_are_published_by_reason() -> None:
    scores = _score()

    similarity = {
        ExclusionReason.NO_SEARCH: 1,
        ExclusionReason.NOT_ROUTED_TO_FAQ: 1,
        ExclusionReason.HANDED_OFF_TURN: 1,
    }
    rerank = {
        **similarity,
        ExclusionReason.RERANKER_UNAVAILABLE: 1,
        ExclusionReason.NOT_REACHED_RERANKER: 2,
    }
    for metric in scores.stage_metrics(Stage.SIMILARITY):
        assert metric.excluded.counts == similarity
    for metric in scores.stage_metrics(Stage.RERANK):
        assert metric.excluded.counts == rerank
    assert scores.similarity_gate_survival.excluded.counts == similarity


def test_not_reached_reranker_takes_precedence_over_reranker_unavailable() -> None:
    events = [
        (
            {
                "event": "faq.reranking_unavailable",
                "level": "error",
                "segment": 0,
                "reason": "timeout",
                "timeout_seconds": 2.0,
                "candidate_count": 2,
            }
            if event["event"] == "faq.reranking_completed"
            else event
        )
        for event in _events_of("G931")
        if event["event"] != "faq.rerank_gate"
    ]

    request = _request(_score(_with_events("G931", events)), "G931")

    assert request.rerank.excluded is ExclusionReason.NOT_REACHED_RERANKER


# --- similarity-gate survival -----------------------------------------------------


def test_gate_survival_counts_the_gate_dropped_request_as_not_surviving() -> None:
    metric = _score().similarity_gate_survival

    assert (metric.numerator, metric.denominator) == (7, 9)


# --- the hit@5 statement ----------------------------------------------------------


def test_rerank_hit_at_5_carries_the_by_construction_statement_while_the_cap_is_5() -> (
    None
):
    statement = _score().rerank_hit_at_5_statement

    assert statement is not None
    assert "1 by construction" in statement
    assert "similarity_cap" in statement
    assert "5" in statement


def test_the_statement_follows_the_cap_read_from_the_runs_conditions() -> None:
    assert _score(similarity_cap=3).rerank_hit_at_5_statement is not None
    assert _score(similarity_cap=6).rerank_hit_at_5_statement is None


# --- the slug to entry-id map -----------------------------------------------------


def test_cited_slugs_are_compared_through_the_runs_entry_id_map() -> None:
    entry_ids = dict(read_run(_RUN).entry_ids)
    entry_ids["referral"] = 104

    request = _request(_score(entry_ids=entry_ids), "G921")

    assert request.similarity.rank == 1


def test_a_cited_slug_missing_from_the_entry_id_map_is_refused() -> None:
    entry_ids = {
        slug: entry_id
        for slug, entry_id in read_run(_RUN).entry_ids.items()
        if slug != "referral"
    }

    with pytest.raises(ValueError, match="referral"):
        _score(entry_ids=entry_ids)


# --- records the scorer cannot read -----------------------------------------------


def test_a_faq_request_with_no_retrieval_event_and_no_skip_event_is_refused() -> None:
    events = [
        event
        for event in _events_of("G921")
        if event.get("segment") is None or event["event"] == "faq.verdict"
    ]

    with pytest.raises(ValueError, match="G921"):
        _score(_with_events("G921", events))


def test_a_segment_with_two_retrieval_events_is_refused() -> None:
    events = _events_of("G921")
    retrieval = next(e for e in events if e["event"] == "faq.retrieval_completed")
    events.insert(2, retrieval)

    with pytest.raises(ValueError, match="G921"):
        _score(_with_events("G921", events))


def test_a_kept_cited_chunk_with_no_rerank_event_of_either_kind_is_refused() -> None:
    events = [
        event
        for event in _events_of("G921")
        if event["event"] not in ("faq.reranking_completed", "faq.rerank_gate")
    ]

    with pytest.raises(ValueError, match="G921"):
        _score(_with_events("G921", events))
