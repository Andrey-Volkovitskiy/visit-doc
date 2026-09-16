"""Metric movements: every metric on both sides, and what may be said about the move.

Three rules are load-bearing. Every metric either run published appears, unchanged ones
included, because "this held steady" and "this was never computed" are different facts
(FR-013, FR-014). Direction comes from a declared polarity table, never from the
metric's name. And a metric one run did not compute, or did not measure, is
`not_comparable` with no delta rather than a difference against zero (FR-015).
"""

from collections.abc import Callable

import pytest
from chat.domain.schemas import FaqVerdict
from golden_harness.comparison.metrics import POLARITY, Polarity, metric_movements
from golden_harness.comparison.model import MetricMovement, MovementDirection
from golden_harness.report import METRICS_BY_FAMILY, Report
from golden_harness.scoring.serving import (
    UNSERVED_ANSWERABLE_SHARE,
    VERDICT_DISTRIBUTION,
    WRONG_ABSTENTION_SHARE,
)


def _by_name(movements: list[MetricMovement]) -> dict[str, MetricMovement]:
    return {movement.name: movement for movement in movements}


def _published_names() -> set[str]:
    names = {name for family in METRICS_BY_FAMILY.values() for name in family}
    names.discard(VERDICT_DISTRIBUTION)
    return names


def test_every_metric_either_run_published_appears(
    scored: Callable[..., Report],
) -> None:
    movements = metric_movements(scored("base"), scored("verdict"))

    assert _published_names() <= set(_by_name(movements))


def test_the_verdict_distribution_appears_as_its_published_rows(
    scored: Callable[..., Report],
) -> None:
    movements = _by_name(metric_movements(scored("base"), scored("verdict")))

    assert f"{VERDICT_DISTRIBUTION}.answered" in movements
    assert f"{VERDICT_DISTRIBUTION}.abstained_rerank_floor" in movements
    assert VERDICT_DISTRIBUTION not in movements


def test_metrics_are_listed_in_the_reports_own_family_order(
    scored: Callable[..., Report],
) -> None:
    movements = metric_movements(scored("base"), scored("base"))

    families = [movement.family for movement in movements]

    assert families == sorted(families, key=list(METRICS_BY_FAMILY).index)


def test_an_unchanged_metric_is_still_reported_with_both_values(
    scored: Callable[..., Report],
) -> None:
    movements = _by_name(metric_movements(scored("base"), scored("base")))
    held = movements["similarity_hit_at_1"]

    assert held.direction is MovementDirection.UNCHANGED
    assert held.base is not None and held.new is not None
    assert (held.base.numerator, held.base.denominator) == (3, 4)
    assert held.delta == 0.0


def test_a_zero_target_share_falling_is_an_improvement(
    scored: Callable[..., Report],
) -> None:
    # The verdict variant answers a request the baseline abstained on: C1 falls.
    movement = _by_name(metric_movements(scored("base"), scored("verdict")))[
        UNSERVED_ANSWERABLE_SHARE
    ]

    assert POLARITY[UNSERVED_ANSWERABLE_SHARE] is Polarity.LOWER_IS_BETTER
    assert movement.direction is MovementDirection.IMPROVED


def test_a_zero_target_share_rising_is_a_degradation(
    scored: Callable[..., Report],
) -> None:
    movement = _by_name(metric_movements(scored("verdict"), scored("base")))[
        UNSERVED_ANSWERABLE_SHARE
    ]

    assert movement.direction is MovementDirection.DEGRADED


def test_an_ordinary_metric_rising_is_an_improvement(
    scored: Callable[..., Report],
) -> None:
    # The rank variant puts the cited chunk first: hit@1 rises.
    movement = _by_name(metric_movements(scored("base"), scored("rank")))[
        "similarity_hit_at_1"
    ]

    assert POLARITY["similarity_hit_at_1"] is Polarity.HIGHER_IS_BETTER
    assert movement.direction is MovementDirection.IMPROVED


def test_both_zero_target_shares_are_the_only_metrics_that_improve_downward() -> None:
    downward = {
        name
        for name, polarity in POLARITY.items()
        if polarity is Polarity.LOWER_IS_BETTER
    }

    assert downward == {UNSERVED_ANSWERABLE_SHARE, WRONG_ABSTENTION_SHARE}


def test_a_verdict_distribution_row_moves_without_a_direction() -> None:
    # A distribution row is a population count, not a target: more answers is better
    # only if answering was right, which the two zero-target shares already measure.
    for verdict_row in (
        f"{VERDICT_DISTRIBUTION}.answered",
        f"{VERDICT_DISTRIBUTION}.abstained_rerank_floor",
    ):
        assert POLARITY[verdict_row] is Polarity.NONE


def test_a_directionless_metric_that_moved_is_reported_as_directionless(
    scored: Callable[..., Report],
) -> None:
    movement = _by_name(metric_movements(scored("base"), scored("verdict")))[
        f"{VERDICT_DISTRIBUTION}.answered"
    ]

    assert movement.delta is not None and movement.delta > 0
    assert movement.direction is MovementDirection.DIRECTIONLESS


def test_every_published_metric_has_a_declared_polarity() -> None:
    assert _published_names() <= set(POLARITY)


def test_a_moved_denominator_is_reported_apart_from_the_numerator(
    scored: Callable[..., Report],
) -> None:
    # Excluding G805 takes a request out of the denominators without any behaviour
    # moving: the numerator of hit@1 falls with it, and the flag is what says so.
    movement = _by_name(metric_movements(scored("base"), scored("excluded")))[
        "similarity_hit_at_1"
    ]

    assert movement.denominator_moved
    assert movement.base is not None and movement.new is not None
    assert (movement.base.denominator, movement.new.denominator) == (4, 3)


def test_a_numerator_moving_alone_leaves_the_denominator_flag_unset(
    scored: Callable[..., Report],
) -> None:
    movement = _by_name(metric_movements(scored("base"), scored("rank")))[
        "similarity_hit_at_1"
    ]

    assert not movement.denominator_moved


def test_a_denominator_moving_under_an_unchanged_value_is_still_flagged(
    scored: Callable[..., Report],
) -> None:
    movement = _by_name(metric_movements(scored("base"), scored("excluded")))[
        WRONG_ABSTENTION_SHARE
    ]

    assert movement.base is not None and movement.new is not None
    assert movement.base.value == movement.new.value
    assert movement.direction is MovementDirection.UNCHANGED


@pytest.mark.parametrize("side", ["base", "new"])
def test_a_metric_one_run_did_not_compute_is_not_comparable(
    scored: Callable[..., Report], side: str
) -> None:
    computed = scored("base")
    absent = computed.model_copy(update={"not_computed": ["end_to_end_task_success"]})
    pair = (absent, computed) if side == "base" else (computed, absent)

    movement = _by_name(metric_movements(*pair))["end_to_end_task_success"]

    assert movement.direction is MovementDirection.NOT_COMPARABLE
    assert movement.delta is None
    assert (movement.base is None) == (side == "base")


def test_a_metric_not_measured_on_one_side_is_not_comparable(
    scored: Callable[..., Report],
) -> None:
    # A run with no booking case measures neither booking metric: an empty denominator
    # is `not_measured`, which is not a value a difference can be taken from.
    with_booking = scored("base")
    without = scored("base", only=["G801", "G802"])

    movement = _by_name(metric_movements(without, with_booking))[
        "tool_selection_correctness"
    ]

    assert movement.base is not None and movement.base.value == "not_measured"
    assert movement.direction is MovementDirection.NOT_COMPARABLE
    assert movement.delta is None


def test_the_polarity_table_is_a_declaration_and_not_a_default() -> None:
    # A table that fell back to "higher is better" for a name it did not know would
    # report the next lower-is-better metric backwards and silently. Every row is
    # written out, so an undeclared name has none - and `_direction` reads that as no
    # direction at all rather than as a guess.
    distribution = {f"{VERDICT_DISTRIBUTION}.{verdict.value}" for verdict in FaqVerdict}

    assert set(POLARITY) == _published_names() | distribution
    assert "a_metric_nobody_declared" not in POLARITY
