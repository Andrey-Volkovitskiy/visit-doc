"""Classification metrics over the hand-written `fixtures/runs/us1` run.

Expected values, counted by hand from the fixture's case files and `labels.json`:

    case  labelled                produced                    stored exclusion
    G901  faq, faq                faq, faq                    -
    G902  small_talk, faq         faq                         -
    G903  faq                     classification_failed       run_error
    G904  faq, urgent_condition   faq, urgent_condition       handed_off_turn
    G905  faq                     (silent: not classified)    silenced_turn
    G906  faq                     faq                         cancelled_turn
    G907  faq, faq, unknown       faq, faq, faq (cap_bound)   -
    G908  small_talk              small_talk (attempts: 2)    -
    G909  faq                     faq (assistant_failed+reply) -

    Cases not excluded: G901 G902 G904 G907 G908 G909 = 6
    Excluded cases: run_error 1 (G903), silenced_turn 1 (G905), cancelled_turn 1 (G906)

    A1 request-count accuracy  5 / 6   (G902 produced 1 of 2)
    A2 intent accuracy         8 / 9   aligned requests: G901 2, G904 2, G907 3,
                                       G908 1, G909 1; G907's third is wrong.
                                       Unaligned: 2 (G902)
    A3 exact segmentation      4 / 6   (G902 unaligned, G907 wrong at position 2)

    Disagreements: G902, G907.  cap_bound: G907.
"""

import json
from pathlib import Path

import pytest
from chat.domain.schemas import IntentLabel
from golden_harness.cases import Case, load_cases
from golden_harness.record import CaseRun, ExclusionReason, read_case, recorded_case_ids
from golden_harness.scoring.alignment import align_run
from golden_harness.scoring.classification import (
    ClassificationScores,
    score_classification,
)

_RUN = Path(__file__).resolve().parents[1] / "fixtures" / "runs" / "us1"
_SCHEMA = Path(__file__).resolve().parents[4] / "evals" / "golden" / "schema.json"


def _labels() -> list[Case]:
    return load_cases(_RUN / "labels.json", _SCHEMA)


def _case_runs() -> list[CaseRun]:
    return [read_case(_RUN, case_id) for case_id in recorded_case_ids(_RUN)]


def _score(case_runs: list[CaseRun] | None = None) -> ClassificationScores:
    runs = case_runs if case_runs is not None else _case_runs()
    labels = _labels()
    return score_classification(labels, align_run(labels, runs), runs)


def test_request_count_accuracy_counts_cases_whose_counts_match() -> None:
    metric = _score().request_count_accuracy

    assert (metric.numerator, metric.denominator) == (5, 6)


def test_intent_accuracy_is_over_aligned_requests_beside_the_unaligned_count() -> None:
    scores = _score()

    assert (scores.intent_accuracy.numerator, scores.intent_accuracy.denominator) == (
        8,
        9,
    )
    assert scores.unaligned_requests == 2


def test_exact_segmentation_match_needs_the_count_and_every_intent() -> None:
    metric = _score().exact_segmentation_match

    assert (metric.numerator, metric.denominator) == (4, 6)


def test_case_level_metrics_publish_their_exclusions_by_reason() -> None:
    scores = _score()

    expected = {
        ExclusionReason.RUN_ERROR: 1,
        ExclusionReason.SILENCED_TURN: 1,
        ExclusionReason.CANCELLED_TURN: 1,
    }
    assert scores.request_count_accuracy.excluded.counts == expected
    assert scores.exact_segmentation_match.excluded.counts == expected
    assert scores.intent_accuracy.excluded.counts == expected


def test_classification_failed_is_a_run_error_in_no_numerator_or_denominator() -> None:
    without_g903 = [run for run in _case_runs() if run.case_id != "G903"]

    full, reduced = _score(), _score(without_g903)

    for name in (
        "request_count_accuracy",
        "intent_accuracy",
        "exact_segmentation_match",
    ):
        with_it, without_it = getattr(full, name), getattr(reduced, name)
        assert (with_it.numerator, with_it.denominator) == (
            without_it.numerator,
            without_it.denominator,
        )
    assert "G903" not in {d.case_id for d in full.disagreements}


def test_a_classification_failed_segment_with_no_run_error_is_refused() -> None:
    runs = [
        run.model_copy(update={"excluded": None}) if run.case_id == "G903" else run
        for run in _case_runs()
    ]

    with pytest.raises(ValueError, match="G903"):
        _score(runs)


def test_the_handed_off_case_is_scored_for_classification() -> None:
    without_g904 = [run for run in _case_runs() if run.case_id != "G904"]

    full, reduced = _score(), _score(without_g904)

    assert full.request_count_accuracy.denominator == (
        reduced.request_count_accuracy.denominator + 1
    )
    assert full.intent_accuracy.denominator == reduced.intent_accuracy.denominator + 2
    assert full.intent_accuracy.numerator == reduced.intent_accuracy.numerator + 2


def test_an_assistant_failed_turn_that_stored_a_reply_is_scored() -> None:
    without_g909 = [run for run in _case_runs() if run.case_id != "G909"]

    full, reduced = _score(), _score(without_g909)

    assert full.exact_segmentation_match.denominator == (
        reduced.exact_segmentation_match.denominator + 1
    )
    assert full.exact_segmentation_match.numerator == (
        reduced.exact_segmentation_match.numerator + 1
    )


def test_the_disagreement_list_names_each_case_with_both_intent_sequences() -> None:
    disagreements = {d.case_id: d for d in _score().disagreements}

    assert sorted(disagreements) == ["G902", "G907"]
    assert disagreements["G902"].labelled == [
        IntentLabel.SMALL_TALK,
        IntentLabel.FAQ_QUESTION,
    ]
    assert disagreements["G902"].produced == [IntentLabel.FAQ_QUESTION]
    assert disagreements["G907"].labelled == [
        IntentLabel.FAQ_QUESTION,
        IntentLabel.FAQ_QUESTION,
        IntentLabel.UNKNOWN,
    ]
    assert disagreements["G907"].produced == [IntentLabel.FAQ_QUESTION] * 3


def test_the_disagreement_list_carries_no_gist_and_no_produced_text() -> None:
    labels = {case.id: case for case in _labels()}
    runs = {run.case_id: run for run in _case_runs()}

    rendered = json.dumps([d.model_dump(mode="json") for d in _score().disagreements])

    for case_id in ("G902", "G907"):
        for request in labels[case_id].requests:
            assert request.gist not in rendered
        segmentation = runs[case_id].segments
        assert segmentation is not None
        for segment in segmentation.segments:
            assert segment.text not in rendered
    assert "gist" not in rendered
    assert "text" not in rendered


def test_a_gist_or_produced_text_never_decides_a_disagreement() -> None:
    runs = []
    for run in _case_runs():
        if run.segments is None:
            runs.append(run)
            continue
        renamed = [
            s.model_copy(update={"text": "entirely different words"})
            for s in run.segments.segments
        ]
        runs.append(
            run.model_copy(
                update={
                    "segments": run.segments.model_copy(update={"segments": renamed})
                }
            )
        )

    assert sorted(d.case_id for d in _score(runs).disagreements) == ["G902", "G907"]


def test_cap_bound_turns_are_counted_and_listed() -> None:
    scores = _score()

    assert scores.cap_bound_cases == ["G907"]
