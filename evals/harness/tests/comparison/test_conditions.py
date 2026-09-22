"""The condition delta: what differs, in what order, and what it does not stop.

A threshold that differs is usually the change under test, so it is reported and the
comparison continues (FR-009). The corpus hash is a condition too - it changes what
every retrieval metric was measured against - so it is reported, and reported first
and separately, because a reader scanning a list of eleven fields can miss one row
(FR-010).
"""

from collections.abc import Callable
from pathlib import Path

from golden_harness.cases import Case
from golden_harness.comparison.compare import compare
from golden_harness.comparison.conditions import condition_delta, corpus_moved
from golden_harness.record import RunConditions
from golden_harness.report import Report

_CORPUS_FIELD = "corpus_sha256"


def _delta(base: Report, new: Report) -> list[tuple[str, str, str]]:
    return [
        (change.field, change.base, change.new)
        for change in condition_delta(base, new).changes
    ]


def test_two_runs_of_one_build_report_an_empty_delta_as_such(
    scored: Callable[..., Report],
) -> None:
    delta = condition_delta(scored("base"), scored("base"))

    assert delta.changes == []
    assert delta.identical


def test_a_differing_field_is_named_with_both_values(
    scored: Callable[..., Report],
) -> None:
    assert _delta(scored("base"), scored("conditions")) == [
        ("rerank_floor", "0.58", "0.52")
    ]


def test_the_delta_is_directional_from_the_baseline_to_the_new_run(
    scored: Callable[..., Report],
) -> None:
    assert _delta(scored("conditions"), scored("base")) == [
        ("rerank_floor", "0.52", "0.58")
    ]


def test_every_differing_field_appears_in_run_conditions_declaration_order(
    scored: Callable[..., Report],
) -> None:
    base = scored("base")
    moved = base.model_copy(
        update={
            "conditions": base.conditions.model_copy(
                update={
                    "rerank_floor": 0.52,
                    "classification_model": "claude-haiku-other",
                    "context_turns": 7,
                }
            )
        }
    )

    assert [field for field, _, _ in _delta(base, moved)] == [
        name
        for name in RunConditions.model_fields
        if name
        in {
            "classification_model",
            "rerank_floor",
            "context_turns",
        }
    ]


def test_a_moved_corpus_hash_is_a_change_like_any_other_and_comes_first(
    scored: Callable[..., Report],
) -> None:
    base, moved = scored("base"), scored("corpus")

    changes = _delta(base, moved)

    assert changes[0] == (
        _CORPUS_FIELD,
        base.corpus.live_sha256,
        moved.corpus.live_sha256,
    )
    assert len(changes) == 1


def test_a_moved_corpus_hash_is_also_set_apart_from_the_list(
    scored: Callable[..., Report],
) -> None:
    assert corpus_moved(scored("base"), scored("corpus"))
    assert not corpus_moved(scored("base"), scored("base"))
    assert not corpus_moved(scored("base"), scored("conditions"))


def test_the_corpus_hash_leads_a_delta_that_holds_other_fields_too(
    scored: Callable[..., Report],
) -> None:
    base = scored("base")
    moved = scored("corpus")
    moved = moved.model_copy(
        update={
            "conditions": moved.conditions.model_copy(update={"rerank_floor": 0.52})
        }
    )

    assert [field for field, _, _ in _delta(base, moved)] == [
        _CORPUS_FIELD,
        "rerank_floor",
    ]


def test_a_condition_difference_never_stops_the_comparison(
    scored: Callable[..., Report],
) -> None:
    # Nothing here raises: a threshold change is the most common reason to compare two
    # runs, and refusing it would remove the tool's main use (FR-009).
    delta = condition_delta(scored("base"), scored("conditions"))

    assert not delta.identical
    assert len(delta.changes) == 1


def test_a_differing_run_clock_is_reported_as_a_condition(
    scored: Callable[..., Report],
) -> None:
    # Every scheduling fixture's day offset is resolved against the run clock, so two
    # clocks are two sets of expected appointments. A delta that said "the conditions
    # matched in every field" would be saying something untrue about the booking rows.
    base = scored("base")
    later = base.model_copy(update={"clock": base.clock.replace(day=3)})

    assert _delta(base, later) == [
        ("clock", base.clock.isoformat(), later.clock.isoformat())
    ]
    assert not condition_delta(base, later).identical


def test_a_traced_and_an_untraced_run_of_one_build_compare_with_an_empty_delta(
    run_dir: Callable[[str], Path],
    labels: list[Case],
    retraced: Callable[[Path, str], Path],
) -> None:
    # Whether a run was traced changes nothing it measured, so it is never a condition.
    traced = retraced(run_dir("base"), "traced")
    untraced = retraced(run_dir("base"), "untraced_by_request")

    comparison = compare(untraced, traced, labels)

    assert comparison.conditions.changes == []
    assert comparison.conditions.identical
