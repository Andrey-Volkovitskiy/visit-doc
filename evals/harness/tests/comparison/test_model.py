"""The comparison's own entities: what each refuses, and what follows from what.

Every model is frozen and refuses a field nobody declared, as the run record and the
report are, so nothing mutates a comparison after it is built. The rules tested
here are the ones a reader of a stored comparison relies on without checking: a metric
with one side missing carries no delta, a case movement is never `unchanged`, and the
derived flags agree with the lists they are derived from.
"""

from datetime import datetime
from typing import Any

import pytest
from golden_harness.cases import Selection, SelectionKind
from golden_harness.comparison.model import (
    AlignmentMovement,
    BandVerdict,
    CaseCoverage,
    CaseMovement,
    Comparison,
    ConditionChange,
    ConditionDelta,
    ExclusionCount,
    MetricMovement,
    MovementDirection,
    MovementGroup,
    RunSide,
)
from golden_harness.record import CorpusRecord, ExclusionReason, RunConditions
from golden_harness.report import MetricFamily
from golden_harness.scoring.alignment import AlignmentTotals
from golden_harness.scoring.metric import Exclusions, Metric
from pydantic import BaseModel, ValidationError

_CONDITIONS = RunConditions(
    classification_model="claude-haiku-4-5-20251001",
    generation_model="claude-sonnet-5",
    embedding_model="voyage-3.5",
    rerank_model="rerank-3",
    retrieval_pool_size=25,
    similarity_floor=0.3,
    similarity_cap=5,
    rerank_floor=0.58,
    rerank_cap=3,
    max_segments=3,
    context_turns=5,
)
_CORPUS = CorpusRecord(live_sha256="a" * 64, pinned_sha256="a" * 64, matched=True)


def _metric(numerator: int = 1, denominator: int = 2, name: str = "m") -> Metric:
    return Metric(
        name=name,
        numerator=numerator,
        denominator=denominator,
        excluded=Exclusions(),
    )


def _side(
    run_id: str = "01RUN",
    *,
    case_ids: list[str] | None = None,
    recorded: list[str] | None = None,
    complete: bool = True,
) -> RunSide:
    ids = case_ids if case_ids is not None else ["G001", "G002"]
    return RunSide(
        run_id=run_id,
        location=f".run/evals/{run_id}",
        started_at=datetime(2026, 9, 15, 10, 0),
        conditions=_CONDITIONS,
        corpus=_CORPUS,
        selection=Selection(kind=SelectionKind.ALL, case_ids=ids),
        recorded_cases=recorded if recorded is not None else list(ids),
        complete=complete,
    )


def _comparison(**fields: Any) -> Comparison:
    base: dict[str, Any] = {
        "base": _side("01BASE"),
        "new": _side("01NEW"),
        "conditions": ConditionDelta(changes=[]),
        "corpus_moved": False,
        "coverage": CaseCoverage(
            common=["G001", "G002"], base_only=[], new_only=[], restricted=False
        ),
        "metrics": [],
        "cases": [],
        "alignment": AlignmentMovement(
            base=AlignmentTotals(aligned=2, unaligned=0, excluded=0, labelled=2),
            new=AlignmentTotals(aligned=2, unaligned=0, excluded=0, labelled=2),
            moved=False,
        ),
        "exclusions": {},
        "band_id": None,
        "band_applicable": False,
        "compared_at": datetime(2026, 9, 16, 12, 0),
        "compare_seconds": 0.5,
    }
    return Comparison(**{**base, **fields})


_MODELS_AND_ONE_VALID_INSTANCE: list[tuple[type[BaseModel], dict[str, Any]]] = [
    (ConditionChange, {"field": "rerank_floor", "base": "0.58", "new": "0.52"}),
    (ConditionDelta, {"changes": []}),
    (
        BandVerdict,
        {"low": 0.1, "high": 0.2, "inside": True},
    ),
    (
        CaseCoverage,
        {"common": ["G001"], "base_only": [], "new_only": [], "restricted": False},
    ),
    (ExclusionCount, {"base": 1, "new": 1, "moved": False}),
]


@pytest.mark.parametrize(
    ("model", "fields"),
    _MODELS_AND_ONE_VALID_INSTANCE,
    ids=lambda value: value.__name__ if isinstance(value, type) else "",
)
def test_every_model_refuses_a_field_nobody_declared(
    model: type[BaseModel], fields: dict[str, Any]
) -> None:
    with pytest.raises(ValidationError):
        model(**fields, surprise="x")


@pytest.mark.parametrize(
    ("model", "fields"),
    _MODELS_AND_ONE_VALID_INSTANCE,
    ids=lambda value: value.__name__ if isinstance(value, type) else "",
)
def test_every_model_is_frozen(model: type[BaseModel], fields: dict[str, Any]) -> None:
    instance = model(**fields)
    field = next(iter(fields))

    with pytest.raises(ValidationError):
        setattr(instance, field, fields[field])


def test_a_run_side_and_a_comparison_refuse_an_undeclared_field() -> None:
    with pytest.raises(ValidationError):
        RunSide(**{**dict(_side()), "surprise": "x"})
    with pytest.raises(ValidationError):
        _comparison(surprise="x")


def test_a_comparison_and_a_run_side_are_frozen() -> None:
    with pytest.raises(ValidationError):
        _side().run_id = "other"
    with pytest.raises(ValidationError):
        _comparison().corpus_moved = True


def test_a_run_side_is_complete_when_it_recorded_every_case_it_selected() -> None:
    side = _side(case_ids=["G001", "G002"], recorded=["G001", "G002"], complete=True)

    assert side.complete


def test_a_run_side_recording_fewer_cases_than_it_selected_is_incomplete() -> None:
    side = _side(case_ids=["G001", "G002"], recorded=["G001"], complete=False)

    assert not side.complete


def test_a_completeness_that_disagrees_with_the_recorded_cases_is_refused() -> None:
    with pytest.raises(ValidationError, match="complete"):
        _side(case_ids=["G001", "G002"], recorded=["G001"], complete=True)
    with pytest.raises(ValidationError, match="complete"):
        _side(case_ids=["G001"], recorded=["G001"], complete=False)


def test_an_empty_condition_delta_says_the_two_runs_agreed() -> None:
    assert ConditionDelta(changes=[]).identical
    assert not ConditionDelta(
        changes=[ConditionChange(field="rerank_floor", base="0.58", new="0.52")]
    ).identical


def test_a_metric_movement_carries_the_difference_between_the_two_values() -> None:
    movement = MetricMovement(
        name="m",
        family=MetricFamily.SERVING,
        base=_metric(1, 4),
        new=_metric(2, 4),
        direction=MovementDirection.DEGRADED,
        denominator_moved=False,
        band=None,
    )

    assert movement.delta == pytest.approx(0.25)


@pytest.mark.parametrize("side", ["base", "new"])
def test_a_metric_missing_on_one_side_is_not_comparable_and_has_no_delta(
    side: str,
) -> None:
    movement = MetricMovement(
        name="m",
        family=MetricFamily.RETRIEVAL,
        base=None if side == "base" else _metric(),
        new=None if side == "new" else _metric(),
        direction=MovementDirection.NOT_COMPARABLE,
        denominator_moved=False,
        band=None,
    )

    assert movement.delta is None


@pytest.mark.parametrize(
    "direction",
    [
        MovementDirection.IMPROVED,
        MovementDirection.DEGRADED,
        MovementDirection.UNCHANGED,
        MovementDirection.DIRECTIONLESS,
    ],
)
def test_a_metric_missing_on_one_side_refuses_any_other_direction(
    direction: MovementDirection,
) -> None:
    with pytest.raises(ValidationError, match="not_comparable"):
        MetricMovement(
            name="m",
            family=MetricFamily.RETRIEVAL,
            base=None,
            new=_metric(),
            direction=direction,
            denominator_moved=False,
            band=None,
        )


def test_a_metric_not_measured_on_one_side_has_no_delta_either() -> None:
    movement = MetricMovement(
        name="m",
        family=MetricFamily.BOOKING,
        base=_metric(0, 0),
        new=_metric(1, 2),
        direction=MovementDirection.NOT_COMPARABLE,
        denominator_moved=True,
        band=None,
    )

    assert movement.base is not None and movement.base.value == "not_measured"
    assert movement.delta is None


def _movement(**fields: Any) -> CaseMovement:
    base: dict[str, Any] = {
        "case_id": "G001",
        "position": 0,
        "group": MovementGroup.VERDICT,
        "base": "abstained_rerank_floor",
        "new": "answered",
        "direction": MovementDirection.IMPROVED,
        "question": "Is parking free?",
        "affects": ["unserved_answerable_share"],
        "varies_on_its_own": False,
    }
    return CaseMovement(**{**base, **fields})


@pytest.mark.parametrize(
    "direction", [MovementDirection.UNCHANGED, MovementDirection.NOT_COMPARABLE]
)
def test_a_case_movement_may_not_be_unchanged_or_not_comparable(
    direction: MovementDirection,
) -> None:
    with pytest.raises(ValidationError, match="direction"):
        _movement(direction=direction)


def test_a_case_movement_whose_two_states_are_the_same_is_refused() -> None:
    with pytest.raises(ValidationError, match="movement"):
        _movement(base="answered", new="answered")


def test_a_case_movement_carries_the_metrics_it_changed_the_contribution_to() -> None:
    assert _movement().affects == ["unserved_answerable_share"]
    assert _movement(affects=[]).affects == []


def test_a_case_movement_may_have_no_position_where_the_group_is_per_turn() -> None:
    movement = _movement(group=MovementGroup.SEGMENTATION, position=None, question=None)

    assert movement.position is None


def test_an_exclusion_movement_is_reported_with_both_states() -> None:
    movement = _movement(
        group=MovementGroup.EXCLUSION,
        base="scored",
        new="run_error",
        direction=MovementDirection.DIRECTIONLESS,
        position=None,
        question=None,
        affects=[],
    )

    assert (movement.base, movement.new) == ("scored", "run_error")


def test_coverage_is_restricted_exactly_when_a_case_is_on_one_side_only() -> None:
    assert not CaseCoverage(
        common=["G001"], base_only=[], new_only=[], restricted=False
    ).restricted
    assert CaseCoverage(
        common=["G001"], base_only=["G002"], new_only=[], restricted=True
    ).restricted
    assert CaseCoverage(
        common=["G001"], base_only=[], new_only=["G003"], restricted=True
    ).restricted


@pytest.mark.parametrize(
    ("base_only", "new_only", "restricted"),
    [([], [], True), (["G002"], [], False), ([], ["G003"], False)],
)
def test_a_restriction_flag_that_disagrees_with_the_lists_is_refused(
    base_only: list[str], new_only: list[str], restricted: bool
) -> None:
    with pytest.raises(ValidationError, match="restricted"):
        CaseCoverage(
            common=["G001"],
            base_only=base_only,
            new_only=new_only,
            restricted=restricted,
        )


def test_alignment_movement_says_whether_any_of_the_four_totals_differ() -> None:
    held = AlignmentTotals(aligned=2, unaligned=0, excluded=0, labelled=2)
    moved = AlignmentTotals(aligned=1, unaligned=1, excluded=0, labelled=2)

    assert not AlignmentMovement(base=held, new=held, moved=False).moved
    assert AlignmentMovement(base=held, new=moved, moved=True).moved
    with pytest.raises(ValidationError, match="moved"):
        AlignmentMovement(base=held, new=moved, moved=False)


def test_an_exclusion_count_says_whether_it_moved() -> None:
    assert not ExclusionCount(base=1, new=1, moved=False).moved
    assert ExclusionCount(base=1, new=2, moved=True).moved
    with pytest.raises(ValidationError, match="moved"):
        ExclusionCount(base=1, new=2, moved=False)


def test_a_band_verdict_refuses_a_range_whose_low_exceeds_its_high() -> None:
    with pytest.raises(ValidationError, match="low"):
        BandVerdict(low=0.3, high=0.2, inside=True)


def test_a_comparison_records_which_two_runs_it_compared() -> None:
    comparison = _comparison()

    assert (comparison.base.run_id, comparison.new.run_id) == ("01BASE", "01NEW")
    assert comparison.base.location == ".run/evals/01BASE"


def test_a_comparison_carries_its_exclusion_counts_by_reason() -> None:
    comparison = _comparison(
        exclusions={
            ExclusionReason.RUN_ERROR: ExclusionCount(base=1, new=2, moved=True)
        }
    )

    assert comparison.exclusions[ExclusionReason.RUN_ERROR].new == 2


def test_a_stored_comparison_carries_both_values_of_every_metric_it_holds() -> None:
    # What FR-025 asks of the stored form is that it holds everything the summary
    # prints. Its JSON is the report's own treatment - each computed value serialized
    # beside the fields it comes from - so a reader needs no run directory.
    comparison = _comparison(
        cases=[_movement()],
        metrics=[
            MetricMovement(
                name="m",
                family=MetricFamily.SERVING,
                base=_metric(1, 4),
                new=_metric(1, 4),
                direction=MovementDirection.UNCHANGED,
                denominator_moved=False,
                band=None,
            )
        ],
    )

    stored = comparison.model_dump(mode="json")

    assert stored["metrics"][0]["base"]["value"] == 0.25
    assert stored["metrics"][0]["new"]["numerator"] == 1
    assert stored["metrics"][0]["direction"] == "unchanged"
    assert stored["cases"][0]["affects"] == ["unserved_answerable_share"]
    assert stored["base"]["location"] == ".run/evals/01BASE"


def test_a_stored_comparison_reads_back_as_the_one_that_was_written() -> None:
    # The report's serialization writes each computed value beside the fields it comes
    # from; reading one back drops them and recomputes them, so the stored record is
    # sufficient to rebuild the comparison itself (FR-025).
    comparison = _comparison(
        cases=[_movement()],
        metrics=[
            MetricMovement(
                name="m",
                family=MetricFamily.SERVING,
                base=_metric(1, 4),
                new=_metric(2, 4),
                direction=MovementDirection.DEGRADED,
                denominator_moved=False,
                band=None,
            )
        ],
    )

    read_back = Comparison.model_validate_json(comparison.model_dump_json())

    assert read_back == comparison
    assert read_back.metrics[0].delta == comparison.metrics[0].delta
