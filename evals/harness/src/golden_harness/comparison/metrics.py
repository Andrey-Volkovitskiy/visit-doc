"""Metric movements: pairing the two runs' metrics, and saying which way each moved.

Every metric either run published appears, unchanged ones included (FR-013, FR-014),
because "this held steady" and "this was never computed" are different facts and a
report that prints only what moved cannot tell them apart.

Direction comes from a declared table, never from a heuristic on the metric's name, and
the table has three values rather than two. A metric with no target at all - the six
rows of the verdict distribution, which count what the run produced rather than what it
should have produced - moves `directionless`: "more answers" is an improvement only if
answering was right, which is exactly what the two zero-target shares already measure,
and calling a distribution row an improvement would be the forced direction FR-019
forbids for cases.
"""

from collections.abc import Iterator
from enum import StrEnum
from typing import Final

from chat.domain.schemas import FaqVerdict

from golden_harness.comparison.model import (
    BandVerdict,
    MetricMovement,
    MovementDirection,
)
from golden_harness.report import METRICS_BY_FAMILY, MetricFamily, Report
from golden_harness.scoring.metric import NOT_MEASURED, Metric
from golden_harness.scoring.serving import (
    UNSERVED_ANSWERABLE_SHARE,
    VERDICT_DISTRIBUTION,
    WRONG_ABSTENTION_SHARE,
)


class Polarity(StrEnum):
    """Which way a metric has to move to be an improvement, where one exists."""

    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"
    NONE = "none"


def _polarity_table() -> dict[str, Polarity]:
    """Build the polarity of every published metric name, once."""
    table: dict[str, Polarity] = {}
    for names in METRICS_BY_FAMILY.values():
        for name in names:
            if name == VERDICT_DISTRIBUTION:
                table.update(
                    {
                        f"{VERDICT_DISTRIBUTION}.{verdict.value}": Polarity.NONE
                        for verdict in FaqVerdict
                    }
                )
                continue
            table[name] = Polarity.HIGHER_IS_BETTER
    table[UNSERVED_ANSWERABLE_SHARE] = Polarity.LOWER_IS_BETTER
    table[WRONG_ABSTENTION_SHARE] = Polarity.LOWER_IS_BETTER
    return table


# Data, not a rule applied to a name: adding a metric means adding a row here, and a
# metric with no row is reported unmarked rather than guessed at.
POLARITY: Final[dict[str, Polarity]] = _polarity_table()


def published_metrics(report: Report) -> dict[str, tuple[MetricFamily, Metric]]:
    """Return every metric the report computed, by name, with its family.

    A metric the report names in `not_computed` is absent: no scorer produced it, and
    absent is what the movement reads as `not_comparable` rather than as a zero.
    """
    absent = set(report.not_computed)
    published: dict[str, tuple[MetricFamily, Metric]] = {}
    for family, metrics in _by_family(report):
        for metric in metrics:
            if metric.name in absent or metric.name.split(".")[0] in absent:
                continue
            published[metric.name] = (family, metric)
    return published


def _by_family(report: Report) -> Iterator[tuple[MetricFamily, list[Metric]]]:
    """Yield each family's metrics in the order the report itself lists them."""
    yield (
        MetricFamily.CLASSIFICATION,
        [
            report.classification.request_count_accuracy,
            report.classification.intent_accuracy,
            report.classification.exact_segmentation_match,
        ],
    )
    yield MetricFamily.RETRIEVAL, report.retrieval.metrics()
    yield MetricFamily.SERVING, report.serving.metrics()
    yield MetricFamily.BOOKING, report.booking.metrics()


def metric_movements(
    base: Report,
    new: Report,
    *,
    bands: dict[str, tuple[float, float]] | None = None,
) -> list[MetricMovement]:
    """Pair the two runs' metrics, in report order, one movement each.

    Args:
        bands: the observed range per metric name, when a band applies to both runs.
            A metric the band holds no observation for is reported unmarked rather
            than assumed stable (FR-034).
    """
    on_base, on_new = published_metrics(base), published_metrics(new)
    movements: list[MetricMovement] = []
    for name in _names_in_report_order(on_base, on_new):
        base_pair, new_pair = on_base.get(name), on_new.get(name)
        family = (base_pair or new_pair)[0]  # type: ignore[index]
        base_metric = base_pair[1] if base_pair is not None else None
        new_metric = new_pair[1] if new_pair is not None else None
        movements.append(
            MetricMovement(
                name=name,
                family=family,
                base=base_metric,
                new=new_metric,
                direction=_direction(name, base_metric, new_metric),
                denominator_moved=_denominator_moved(base_metric, new_metric),
                band=_verdict(name, new_metric, bands),
            )
        )
    return movements


def _names_in_report_order(
    on_base: dict[str, tuple[MetricFamily, Metric]],
    on_new: dict[str, tuple[MetricFamily, Metric]],
) -> list[str]:
    """Return every metric either side published, in the report's own order.

    The baseline's order leads, since a reader comparing two reports reads down the
    baseline's; a metric only the new run published follows in its own order.
    """
    names = list(on_base)
    names.extend(name for name in on_new if name not in on_base)
    return names


def _direction(name: str, base: Metric | None, new: Metric | None) -> MovementDirection:
    """Say which way a metric moved, or why that question has no answer here."""
    if base is None or new is None:
        return MovementDirection.NOT_COMPARABLE
    was, is_now = base.value, new.value
    if was == NOT_MEASURED or is_now == NOT_MEASURED:
        # An empty denominator is not a number, so neither a difference nor a
        # direction can be taken from it - even when both sides are empty, where
        # "unchanged" would suggest a value that held.
        return MovementDirection.NOT_COMPARABLE
    if was == is_now:
        return MovementDirection.UNCHANGED
    polarity = POLARITY.get(name, Polarity.NONE)
    if polarity is Polarity.NONE:
        return MovementDirection.DIRECTIONLESS
    rose = float(is_now) > float(was)
    improved = rose if polarity is Polarity.HIGHER_IS_BETTER else not rose
    return MovementDirection.IMPROVED if improved else MovementDirection.DEGRADED


def _denominator_moved(base: Metric | None, new: Metric | None) -> bool:
    """Whether the population the metric was computed over changed.

    False when either side did not compute the metric: there is no population to
    compare, and a `True` there would read as a movement nobody measured.
    """
    if base is None or new is None:
        return False
    return base.denominator != new.denominator


def _verdict(
    name: str, new: Metric | None, bands: dict[str, tuple[float, float]] | None
) -> BandVerdict | None:
    """Mark the new value against the band's observed range, where both exist."""
    if bands is None or new is None:
        return None
    observed = bands.get(name)
    if observed is None:
        return None
    value = new.value
    if value == NOT_MEASURED:
        return None
    low, high = observed
    return BandVerdict(low=low, high=high, inside=low <= float(value) <= high)
