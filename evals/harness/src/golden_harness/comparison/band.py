"""The noise band: how much these numbers move when nothing changes.

Five full runs of one unchanged build, and what the band keeps from them: every value
per metric in run order with its lowest and highest, and per case that varied, each
state with how many of the five produced it. Keeping all five values costs five floats
and lets a later reader see whether four runs agreed and one did not - a distinction a
range alone destroys.

What it is not is the load-bearing part. It is not a threshold: no run is required to
beat it, and nothing here or later may read a band value as a pass mark (FR-038). It is
not a statistical interval: five observations give a range and a frequency count, not a
standard deviation or a confidence interval, and the band publishes none (FR-032).

A band asserts that its five runs differ *only* by chance, so anything contradicting
that is a refusal rather than a warning - a different count, a run that drove part of
the set, or any difference in conditions, corpus, labels or case set (FR-027, FR-030).

The per-case variation is read through the comparison itself: each of the other four
runs is compared against the first, and the states those comparisons report are counted.
So a band and a comparison can never describe the same case in two different vocabu-
laries, because there is only one place that describes a case's state at all.
"""

from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, model_validator
from ulid import ULID

from golden_harness.cases import Case, SelectionKind
from golden_harness.comparison.cases import Item, case_movements
from golden_harness.comparison.metrics import published_metrics
from golden_harness.comparison.model import CaseMovement, MovementGroup
from golden_harness.record import (
    CaseRun,
    Run,
    RunConditions,
    read_case,
    read_run,
    recorded_case_ids,
)
from golden_harness.report import Report, score
from golden_harness.scoring.metric import NOT_MEASURED

# What FR-027 requires, exactly: a band from another count is a different measurement,
# and the record must not quietly say five.
BAND_RUNS: Final = 5


class BandRefusedError(ValueError):
    """The five runs do not differ only by chance; the message names what differs."""


class MetricObservation(BaseModel):
    """One metric across the band's runs: every value, in run order, and its range."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    values: list[float] = Field(min_length=1)
    low: float
    high: float

    @model_validator(mode="after")
    def _the_range_is_the_values(self) -> "MetricObservation":
        """Refuse a range that is not the lowest and highest value observed."""
        if (self.low, self.high) != (min(self.values), max(self.values)):
            raise ValueError(
                f"{self.name}: [{self.low}, {self.high}] is not the range of "
                f"{self.values}"
            )
        return self


class CaseVariation(BaseModel):
    """One case or request the band saw vary, with how often each state occurred.

    `outcomes` is the frequency count five runs buy over three: "answered in 4 of 5,
    abstained at the rerank floor in 1" is a sentence a range cannot make, and it is
    the one that tells a flaky case from a stable one.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    position: int | None
    group: MovementGroup
    outcomes: dict[str, int]

    @model_validator(mode="after")
    def _a_variation_varied(self) -> "CaseVariation":
        """Refuse a variation with one state: that case did not vary."""
        if len(self.outcomes) < 2:
            raise ValueError(
                f"{self.case_id}: a variation has at least two states, not "
                f"{list(self.outcomes)}"
            )
        return self


class NoiseBand(BaseModel):
    """What five runs of one unchanged build did, and under what conditions.

    Carries no verdict, no threshold and no target: nothing in it says what a run
    *should* score (FR-038).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    band_id: str
    run_ids: list[str]
    conditions: RunConditions
    corpus_sha256: str
    case_ids: list[str]
    metrics: dict[str, MetricObservation]
    varying_cases: list[CaseVariation]
    measured_at: datetime

    def applies_to(self, base: Report, new: Report) -> bool:
        """Whether this band was measured under the conditions both runs ran under.

        All three have to match - the conditions, the corpus and the case set - or the
        range describes some other measurement and nothing may be marked against it
        (FR-033).
        """
        return all(
            self.conditions == report.conditions
            and self.corpus_sha256 == report.corpus.live_sha256
            and self.case_ids == sorted(report.recorded_cases)
            for report in (base, new)
        )

    def ranges(self) -> dict[str, tuple[float, float]]:
        """Return the observed range per metric, for marking a movement against."""
        return {
            name: (observation.low, observation.high)
            for name, observation in self.metrics.items()
        }

    def varying_items(self) -> list[Item]:
        """Return the case and request each variation was observed at."""
        return [
            (variation.case_id, variation.position) for variation in self.varying_cases
        ]


def build_band(
    run_dirs: Sequence[Path],
    cases: Sequence[Case],
    *,
    measured_at: datetime | None = None,
) -> NoiseBand:
    """Build a band from exactly five full runs of one unchanged build.

    Args:
        run_dirs: the five run directories, in the order they were taken.
        cases: the labels all five were taken against.
        measured_at: when the band was measured; now by default.

    Raises: BandRefusedError naming what differs, when the runs are not five, when one
        of them did not drive the whole set, or when their conditions, corpus hashes,
        label digests or case sets differ.
    """
    if len(run_dirs) != BAND_RUNS:
        raise BandRefusedError(
            f"a band is measured from exactly {BAND_RUNS} runs, not {len(run_dirs)}: "
            "a band from another count is a different measurement"
        )
    runs = [read_run(directory) for directory in run_dirs]
    _refuse_a_partial_run(runs)
    _refuse_an_incomplete_run(run_dirs, runs)
    _refuse_a_difference(runs)

    reports = [score(directory, cases) for directory in run_dirs]
    records = [_records(directory) for directory in run_dirs]
    return NoiseBand(
        band_id=str(ULID()),
        run_ids=[run.run_id for run in runs],
        conditions=runs[0].conditions,
        corpus_sha256=runs[0].corpus.live_sha256,
        case_ids=sorted(runs[0].selection.case_ids),
        metrics=_observations(reports),
        varying_cases=_variations(reports, records, cases),
        measured_at=measured_at if measured_at is not None else datetime.now(UTC),
    )


def _records(run_dir: Path) -> list[CaseRun]:
    """Read every case record of one run, in case order."""
    return [read_case(run_dir, case_id) for case_id in recorded_case_ids(run_dir)]


def _refuse_an_incomplete_run(run_dirs: Sequence[Path], runs: Sequence[Run]) -> None:
    """Refuse a run that set out to drive the whole set and recorded part of it.

    A run's selection says what it meant to drive and its records say what it has, and
    it is the records every metric is computed over. A run that stopped partway
    therefore measures each metric over a population of its own, and its value sitting
    in a range beside four full ones would put a population difference inside a number
    that claims to be run-to-run noise - while `case_ids` named a set the band never
    measured. A comparison reports such a run as incomplete and carries on (FR-012); a
    band cannot, because its whole claim is that its five runs differ by chance alone.
    """
    for directory, run in zip(run_dirs, runs, strict=True):
        recorded = recorded_case_ids(directory)
        if not set(run.selection.case_ids) <= set(recorded):
            raise BandRefusedError(
                f"{run.run_id} recorded {len(recorded)} of "
                f"{len(run.selection.case_ids)} selected cases: a band is measured "
                "from five runs that each drove the whole set"
            )


def _refuse_a_partial_run(runs: Sequence[Run]) -> None:
    """Refuse a run that set out to drive part of the set.

    The check is each run's own selection, not merely that the five agree: five
    narrowed runs agree with each other perfectly well and still measure something
    other than the noise over the whole set (FR-027).
    """
    narrowed = [
        run.run_id for run in runs if run.selection.kind is not SelectionKind.ALL
    ]
    if narrowed:
        raise BandRefusedError(
            "a band is measured over the whole set, and these runs drove part of it: "
            f"{', '.join(narrowed)}"
        )


def _refuse_a_difference(runs: Sequence[Run]) -> None:
    """Refuse a difference that would make the five measure a change, not the noise."""
    first = runs[0]
    for run in runs[1:]:
        for field in RunConditions.model_fields:
            was, is_now = (
                getattr(first.conditions, field),
                getattr(run.conditions, field),
            )
            if was != is_now:
                raise BandRefusedError(
                    f"the runs were taken under different conditions: {field} is "
                    f"{was} in {first.run_id} and {is_now} in {run.run_id}"
                )
        if first.corpus.live_sha256 != run.corpus.live_sha256:
            raise BandRefusedError(
                "the runs answered against different corpus text: "
                f"{first.corpus.live_sha256} in {first.run_id} and "
                f"{run.corpus.live_sha256} in {run.run_id}"
            )
        moved = sorted(
            case_id
            for case_id in set(first.labels) & set(run.labels)
            if first.labels[case_id] != run.labels[case_id]
        )
        if moved:
            raise BandRefusedError(
                "the runs were scored against different labels, for these cases: "
                f"{', '.join(moved)} ({first.run_id} and {run.run_id})"
            )
        uncommon = sorted(set(first.selection.case_ids) ^ set(run.selection.case_ids))
        if uncommon:
            raise BandRefusedError(
                "the runs drove different case sets, so a metric's range would be "
                f"taken over different populations: {', '.join(uncommon)} "
                f"({first.run_id} and {run.run_id})"
            )


def _observations(reports: Sequence[Report]) -> dict[str, MetricObservation]:
    """Collect each metric's five values, in run order, with its observed range.

    A metric any run left unmeasured is left out rather than counted as a zero: a band
    has to say how much a *measured* number moves, and an empty denominator is not one.
    """
    published = [published_metrics(report) for report in reports]
    observations: dict[str, MetricObservation] = {}
    for name in published[0]:
        values: list[float] = []
        for run in published:
            metric = run.get(name)
            if metric is None or metric[1].value == NOT_MEASURED:
                values = []
                break
            values.append(float(metric[1].value))
        if values:
            observations[name] = MetricObservation(
                name=name, values=values, low=min(values), high=max(values)
            )
    return observations


def _variations(
    reports: Sequence[Report],
    records: Sequence[Sequence[CaseRun]],
    cases: Sequence[Case],
) -> list[CaseVariation]:
    """Count each varying case's states across the five runs.

    Every other run is compared against the first, through the comparison's own
    movement derivation, so the states counted here are the states a comparison would
    report - not a second description of the same thing.
    """
    seen: dict[tuple[str, int | None, MovementGroup, str], list[str]] = {}
    for index in range(1, len(reports)):
        movements = case_movements(
            reports[0],
            reports[index],
            base_cases=records[0],
            new_cases=records[index],
            labels=cases,
        )
        for movement in movements:
            key = _key(movement)
            states = seen.setdefault(key, [])
            # The other runs that raised no movement on this key are counted below,
            # once every comparison is in: each of them stayed at the first run's
            # state.
            states.append(movement.new)

    variations: list[CaseVariation] = []
    for (case_id, position, group, first_state), states in sorted(
        seen.items(), key=lambda entry: (entry[0][0], entry[0][1] or 0, entry[0][2])
    ):
        held = len(reports) - 1 - len(states)
        outcomes = Counter([first_state] * (held + 1))
        outcomes.update(states)
        variations.append(
            CaseVariation(
                case_id=case_id,
                position=position,
                group=group,
                outcomes=dict(outcomes),
            )
        )
    return variations


def _key(movement: CaseMovement) -> tuple[str, int | None, MovementGroup, str]:
    """Identify what a movement is about, including the state the first run was in.

    The first run's state is part of the key because one case and position can carry
    two movements of one group - a retrieval rank moves at each stage separately - and
    their two baseline states are what tells them apart.
    """
    return (movement.case_id, movement.position, movement.group, movement.base)
