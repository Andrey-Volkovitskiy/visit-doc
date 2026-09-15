"""The metric value: three numbers always published, and `not_measured` over nothing."""

import json

import pytest
from golden_harness.record import ExclusionReason
from golden_harness.scoring.metric import (
    CASE_SCOPED_REASONS,
    NOT_MEASURED,
    Exclusions,
    Metric,
)


def test_the_case_scoped_reasons_are_exactly_six() -> None:
    assert {reason.value for reason in CASE_SCOPED_REASONS} == {
        "run_error",
        "silenced_turn",
        "cancelled_turn",
        "missing_log_slice",
        "unresolvable_fixture",
        "outcome_unknown",
    }


def test_a_handed_off_turn_is_not_case_scoped() -> None:
    assert ExclusionReason.HANDED_OFF_TURN not in CASE_SCOPED_REASONS


def test_a_metric_value_is_its_numerator_over_its_denominator() -> None:
    metric = Metric(name="A1", numerator=3, denominator=4, excluded=Exclusions())

    assert metric.value == 0.75


@pytest.mark.parametrize("numerator", [0, 0.0])
def test_an_empty_denominator_is_not_measured_never_zero_or_one(
    numerator: float,
) -> None:
    metric = Metric(
        name="A2", numerator=numerator, denominator=0, excluded=Exclusions()
    )

    assert metric.value == "not_measured"
    assert metric.value == NOT_MEASURED
    assert metric.value != 0.0
    assert metric.value != 1.0


def test_a_perfect_and_a_zero_value_are_still_numbers() -> None:
    assert (
        Metric(name="m", numerator=0, denominator=2, excluded=Exclusions()).value == 0.0
    )
    assert (
        Metric(name="m", numerator=2, denominator=2, excluded=Exclusions()).value == 1.0
    )


def test_exclusions_are_counted_by_reason() -> None:
    exclusions = Exclusions.tally(
        [
            ExclusionReason.SILENCED_TURN,
            ExclusionReason.CANCELLED_TURN,
            ExclusionReason.SILENCED_TURN,
        ]
    )

    assert exclusions.counts == {
        ExclusionReason.SILENCED_TURN: 2,
        ExclusionReason.CANCELLED_TURN: 1,
    }
    assert exclusions.total == 3


def test_a_metric_always_publishes_numerator_denominator_and_exclusions() -> None:
    measured = Metric(
        name="A1",
        numerator=1,
        denominator=2,
        excluded=Exclusions.tally([ExclusionReason.RUN_ERROR]),
    )
    unmeasured = Metric(
        name="A2",
        numerator=0,
        denominator=0,
        excluded=Exclusions.tally([ExclusionReason.MISSING_LOG_SLICE] * 2),
    )

    for metric, value in ((measured, 0.5), (unmeasured, "not_measured")):
        published = json.loads(metric.model_dump_json())
        assert published["numerator"] == metric.numerator
        assert published["denominator"] == metric.denominator
        assert published["value"] == value
        assert published["excluded"]["counts"] == {
            reason.value: count for reason, count in metric.excluded.counts.items()
        }
        assert published["excluded"]["total"] == metric.excluded.total


def test_a_metric_with_nothing_excluded_still_publishes_its_exclusions() -> None:
    published = json.loads(
        Metric(
            name="A1", numerator=1, denominator=1, excluded=Exclusions()
        ).model_dump_json()
    )

    assert published["excluded"] == {"counts": {}, "total": 0}


def test_a_numerator_larger_than_its_denominator_is_refused() -> None:
    with pytest.raises(ValueError):
        Metric(name="m", numerator=3, denominator=2, excluded=Exclusions())


def test_a_negative_count_is_refused() -> None:
    with pytest.raises(ValueError):
        Metric(name="m", numerator=-1, denominator=2, excluded=Exclusions())
    with pytest.raises(ValueError):
        Exclusions(counts={ExclusionReason.RUN_ERROR: -1})
