"""Serving metrics over the hand-written `fixtures/runs/us2` run.

Expected values, counted by hand from the fixture's case files and `labels.json`:

    case  label                  produced outcome(s)          C1           C2
    G921  answerable             answered                     served       -
    G922  answerable             abstained_rerank_floor       abstained    wrong
    G923  gap                    abstained_similarity_floor   -            right
    G924  answerable             answered_unreranked          served       -  (degraded)
    G925  answerable             abstained_empty_corpus       abstained    wrong
    G926  small_talk, answerable 1 produced, answered         count mismatch
    G927  answerable, call_staff handed off                   not permitted (FR-031a)
    G928  answerable, not_authorized answered                  served       -
    G929  gap                    answered                     -            -  (SC-010)
    G930  answerable             answered                     served       -
    G931  answerable             abstained_rerank_floor       abstained    wrong
    G932  answerable x2          answered x2                  served x2    -
    G933  answerable             produced as small_talk       misclassified
    G934  answerable             abstained_rerank_floor       abstained    wrong

    C1: 6 / 12   abstained 4 (G922 G925 G931 G934), lost_to_count_mismatch 1 (G926),
                 misclassified 1 (G933); excluded handed_off_turn 1; not permitted: G927
    C2: 4 / 5    (G923's abstention is on a gap)
    C3: answered 7, answered_unreranked 1, abstained_empty_corpus 1,
        abstained_empty_pool 0, abstained_similarity_floor 1, abstained_rerank_floor 3
"""

from pathlib import Path

from chat.domain.schemas import FaqVerdict
from golden_harness.cases import Case, load_cases
from golden_harness.record import CaseRun, ExclusionReason, read_case, recorded_case_ids
from golden_harness.scoring.alignment import align_run
from golden_harness.scoring.serving import (
    ServingScores,
    StoppingGate,
    UnservedCause,
    score_serving,
)

_RUN = Path(__file__).resolve().parents[1] / "fixtures" / "runs" / "us2"
_SCHEMA = Path(__file__).resolve().parents[4] / "evals" / "golden" / "schema.json"


def _labels() -> list[Case]:
    return load_cases(_RUN / "labels.json", _SCHEMA)


def _case_runs() -> list[CaseRun]:
    return [read_case(_RUN, case_id) for case_id in recorded_case_ids(_RUN)]


def _score(case_runs: list[CaseRun] | None = None) -> ServingScores:
    runs = case_runs if case_runs is not None else _case_runs()
    labels = _labels()
    return score_serving(labels, align_run(labels, runs), runs)


def _without(*case_ids: str) -> list[CaseRun]:
    return [run for run in _case_runs() if run.case_id not in case_ids]


# --- C1 -------------------------------------------------------------------------------


def test_c1_counts_labelled_answerable_requests_in_cases_permitted_to_answer() -> None:
    metric = _score().unserved_answerable_share

    assert (metric.numerator, metric.denominator) == (6, 12)


def test_c1_excludes_the_handed_off_case_by_name_and_counts_it() -> None:
    scores = _score()

    assert scores.not_permitted_to_answer == ["G927"]
    assert scores.unserved_answerable_share.excluded.counts == {
        ExclusionReason.HANDED_OFF_TURN: 1
    }
    reduced = _score(_without("G927")).unserved_answerable_share
    assert scores.unserved_answerable_share.denominator == reduced.denominator


def test_c1_keeps_a_case_pairing_the_question_with_an_unauthorized_request() -> None:
    full = _score().unserved_answerable_share
    reduced = _score(_without("G928")).unserved_answerable_share

    assert "G928" not in _score().not_permitted_to_answer
    assert full.denominator == reduced.denominator + 1
    assert full.numerator == reduced.numerator


def test_c1_breaks_its_numerator_down_by_cause() -> None:
    assert _score().unserved_by_cause == {
        UnservedCause.ABSTAINED: 4,
        UnservedCause.LOST_TO_COUNT_MISMATCH: 1,
        UnservedCause.MISCLASSIFIED: 1,
    }


def test_c1_counts_an_unaligned_answerable_request_as_lost_to_count_mismatch() -> None:
    full = _score().unserved_answerable_share
    reduced = _score(_without("G926")).unserved_answerable_share

    assert full.denominator == reduced.denominator + 1
    assert full.numerator == reduced.numerator + 1


def test_c1_counts_answered_unreranked_as_answered() -> None:
    full = _score().unserved_answerable_share
    reduced = _score(_without("G924")).unserved_answerable_share

    assert full.denominator == reduced.denominator + 1
    assert full.numerator == reduced.numerator


def test_c1_lists_each_unserved_request_with_its_question_and_cause() -> None:
    unserved = {(u.case_id, u.position): u for u in _score().unserved}

    assert sorted(unserved) == [
        ("G922", 0),
        ("G925", 0),
        ("G926", 1),
        ("G931", 0),
        ("G933", 0),
        ("G934", 0),
    ]
    as2 = unserved[("G922", 0)]
    assert as2.cause is UnservedCause.ABSTAINED
    assert as2.gate is StoppingGate.RERANK_FLOOR
    # The produced question, not the patient's own words.
    assert unserved[("G931", 0)].question == "What paperwork is needed for a visit?"
    assert unserved[("G925", 0)].gate is StoppingGate.EMPTY_CORPUS
    mismatch = unserved[("G926", 1)]
    assert mismatch.question == "Good morning! How early should I arrive?"
    assert mismatch.cause is UnservedCause.LOST_TO_COUNT_MISMATCH
    assert mismatch.gate is None
    misclassified = unserved[("G933", 0)]
    assert misclassified.cause is UnservedCause.MISCLASSIFIED
    assert misclassified.question == "Thanks - and where exactly are you?"
    assert misclassified.gate is None


def test_c1_excludes_a_silenced_case_by_name_and_counts_it() -> None:
    runs = [
        run.model_copy(update={"excluded": ExclusionReason.SILENCED_TURN})
        if run.case_id == "G921"
        else run
        for run in _case_runs()
    ]

    scores = _score(runs)

    assert scores.not_permitted_to_answer == ["G921", "G927"]
    assert scores.unserved_answerable_share.excluded.counts == {
        ExclusionReason.SILENCED_TURN: 1,
        ExclusionReason.HANDED_OFF_TURN: 1,
    }
    assert scores.unserved_answerable_share.denominator == 11


# --- C2 -------------------------------------------------------------------------------


def test_c2_is_abstentions_on_answerable_labels_over_every_abstention() -> None:
    metric = _score().wrong_abstention_share

    assert (metric.numerator, metric.denominator) == (4, 5)


def test_c2_puts_an_abstention_on_a_gap_in_the_denominator_only() -> None:
    full = _score().wrong_abstention_share
    reduced = _score(_without("G923")).wrong_abstention_share

    assert full.denominator == reduced.denominator + 1
    assert full.numerator == reduced.numerator


def test_c2_never_counts_answered_unreranked_as_an_abstention() -> None:
    full = _score().wrong_abstention_share
    reduced = _score(_without("G924")).wrong_abstention_share

    assert (full.numerator, full.denominator) == (
        reduced.numerator,
        reduced.denominator,
    )


def test_c2_lists_each_wrong_abstention_with_its_question_and_gate() -> None:
    wrong = {(w.case_id, w.position): w for w in _score().wrong_abstentions}

    assert sorted(wrong) == [("G922", 0), ("G925", 0), ("G931", 0), ("G934", 0)]
    assert wrong[("G931", 0)].question == "What paperwork is needed for a visit?"
    assert wrong[("G922", 0)].gate is StoppingGate.RERANK_FLOOR
    assert wrong[("G925", 0)].gate is StoppingGate.EMPTY_CORPUS


def test_c2_sets_the_handed_off_case_aside() -> None:
    assert _score().wrong_abstention_share.excluded.counts == {
        ExclusionReason.HANDED_OFF_TURN: 1
    }


def test_nothing_is_listed_behind_a_zero() -> None:
    served = _without("G922", "G925", "G926", "G931", "G933", "G934")

    scores = _score(served)

    assert scores.unserved_answerable_share.numerator == 0
    assert scores.unserved == []
    assert scores.wrong_abstention_share.numerator == 0
    assert scores.wrong_abstentions == []


# --- degraded answers, answers on gaps, the distribution ---------------------------


def test_answered_unreranked_is_listed_as_a_degraded_answer() -> None:
    degraded = _score().degraded_answers

    assert [(d.case_id, d.position) for d in degraded] == [("G924", 0)]
    assert degraded[0].question == "Can I see the doctor over video?"


def test_an_answer_on_a_labelled_gap_is_listed() -> None:
    gaps = _score().answers_on_labelled_gaps

    assert [(g.case_id, g.position) for g in gaps] == [("G929", 0)]
    assert gaps[0].question == "Is there parking at the clinic?"


def test_the_verdict_distribution_carries_every_verdict_including_zeros() -> None:
    distribution = _score().verdict_distribution

    assert distribution.counts == {
        FaqVerdict.ANSWERED: 7,
        FaqVerdict.ANSWERED_UNRERANKED: 1,
        FaqVerdict.ABSTAINED_EMPTY_CORPUS: 1,
        FaqVerdict.ABSTAINED_EMPTY_POOL: 0,
        FaqVerdict.ABSTAINED_SIMILARITY_FLOOR: 1,
        FaqVerdict.ABSTAINED_RERANK_FLOOR: 3,
        FaqVerdict.ABSTAINED_GENERATION: 0,
    }
    # Named rather than counted: a verdict added to the enum and left out of the
    # distribution would be a row no report ever shows.
    assert set(distribution.counts) == set(FaqVerdict)
    assert distribution.total == 13
    assert distribution.excluded.counts == {ExclusionReason.HANDED_OFF_TURN: 1}


def test_the_verdict_distribution_publishes_one_metric_per_verdict() -> None:
    metrics = {m.name: m for m in _score().verdict_distribution.metrics()}

    assert set(metrics) == {
        f"verdict_distribution.{verdict.value}" for verdict in FaqVerdict
    }
    empty_pool = metrics["verdict_distribution.abstained_empty_pool"]
    assert (empty_pool.numerator, empty_pool.denominator) == (0, 13)


# --- a labelled question the classifier split into two FAQ questions -----------------


def _split_g930(second_verdict: FaqVerdict) -> list[CaseRun]:
    """G930 recorded as two produced FAQ halves: its own answered outcome at position 0,
    and a second half at position 1 with `second_verdict`."""
    g930 = read_case(_RUN, "G930")
    assert g930.segments is not None and g930.assistant_message is not None
    (only,) = g930.segments.segments
    (answered,) = g930.assistant_message.request_outcomes or []
    abstained = not second_verdict.answered
    second = answered.model_copy(
        update={
            "position": 1,
            "question": "What are your out-of-pocket rates?",
            "verdict": second_verdict,
            "answer": None if abstained else answered.answer,
            "citations": [] if abstained else answered.citations,
        }
    )
    halves = g930.segments.model_copy(
        update={
            "segments": [
                only,
                only.model_copy(update={"position": 1, "text": second.question}),
            ]
        }
    )
    message = g930.assistant_message.model_copy(
        update={"request_outcomes": [answered, second]}
    )
    return [
        run.model_copy(update={"segments": halves, "assistant_message": message})
        if run.case_id == "G930"
        else run
        for run in _case_runs()
    ]


def test_a_split_question_both_of_whose_halves_were_answered_is_served() -> None:
    scores = _score(_split_g930(FaqVerdict.ANSWERED))

    assert "G930" not in {u.case_id for u in scores.unserved}
    assert (
        scores.unserved_answerable_share.numerator,
        scores.unserved_answerable_share.denominator,
    ) == (6, 12)


def test_a_split_question_with_an_abstained_half_is_unserved_at_that_half() -> None:
    scores = _score(_split_g930(FaqVerdict.ABSTAINED_RERANK_FLOOR))

    (unserved,) = [u for u in scores.unserved if u.case_id == "G930"]
    assert (unserved.position, unserved.cause, unserved.gate) == (
        0,
        UnservedCause.ABSTAINED,
        StoppingGate.RERANK_FLOOR,
    )
    assert unserved.question == "What are your out-of-pocket rates?"
    # One labelled question, so one unserved request - not one per half.
    assert scores.unserved_answerable_share.denominator == 12


def test_an_abstained_half_of_an_answerable_question_is_a_wrong_abstention() -> None:
    scores = _score(_split_g930(FaqVerdict.ABSTAINED_RERANK_FLOOR))

    assert ("G930", 1) in {(a.case_id, a.position) for a in scores.wrong_abstentions}
    assert scores.wrong_abstention_share.denominator == 6
