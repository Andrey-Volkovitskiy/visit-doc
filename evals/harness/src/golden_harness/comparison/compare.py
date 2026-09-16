"""The entry point: two stored runs and the labels in, one `Comparison` out.

Three properties hold by construction here rather than by care.

It **re-scores both runs** through the pure `score`, so it never reads either run's
stored `report.json` and never writes into either directory (research R1, R2). A
comparison is a read, and the new run may be the committed baseline under `specs/`.

It scores both sides over the **cases both runs recorded**, by restricting the scoring
rather than filtering its result, so every denominator, exclusion count and alignment
total is computed over the common cases by construction (FR-023, research R3).

And it lets the **label refusal propagate** rather than restating it: scoring already
refuses two runs taken against different labels, so this command and `make eval-score`
cannot disagree about which labels are acceptable (FR-011, research R6).
"""

import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from golden_harness.cases import Case
from golden_harness.comparison.band import NoiseBand
from golden_harness.comparison.cases import case_movements
from golden_harness.comparison.conditions import condition_delta, corpus_moved
from golden_harness.comparison.coverage import case_coverage, run_side
from golden_harness.comparison.metrics import metric_movements
from golden_harness.comparison.model import (
    AlignmentMovement,
    Comparison,
    ExclusionCount,
)
from golden_harness.record import CaseRun, ExclusionReason, read_case, read_run
from golden_harness.report import Report, score


class NoCommonCasesError(ValueError):
    """The two runs recorded no case in common, so there is nothing to compare."""


def compare(
    base_dir: Path,
    new_dir: Path,
    cases: Sequence[Case],
    *,
    band: NoiseBand | None = None,
) -> Comparison:
    """Compare two stored runs, scoring both over the cases they both recorded.

    Args:
        cases: the labels both runs are scored against.
        band: measured noise to mark each movement against, when one was given. It is
            applied only when its conditions, corpus and case set match both runs;
            otherwise the comparison says so and marks nothing (FR-033).

    Returns: the comparison, whose `compared_at` and `compare_seconds` are the only
        fields a second comparison of the same pair may differ in (FR-007)

    Raises: NoCommonCasesError when the runs share no case (FR-024);
        LabelDigestMismatchError when the two runs were taken against different labels;
        FileNotFoundError when a run directory holds no `run.json`.
    """
    started = time.perf_counter()
    base_run, new_run = read_run(base_dir), read_run(new_dir)
    coverage = case_coverage(base_dir, new_dir)
    if not coverage.common:
        raise NoCommonCasesError(
            f"{base_run.run_id} and {new_run.run_id} recorded no case in common: "
            f"{len(coverage.base_only)} cases on the baseline side only, "
            f"{len(coverage.new_only)} on the new side only"
        )

    base_report = score(base_dir, cases, only=coverage.common)
    new_report = score(new_dir, cases, only=coverage.common)
    base_records = _records(base_dir, coverage.common)
    new_records = _records(new_dir, coverage.common)

    applicable = band is not None and band.applies_to(base_report, new_report)
    return Comparison(
        base=run_side(base_dir, base_run),
        new=run_side(new_dir, new_run),
        conditions=condition_delta(base_report, new_report),
        corpus_moved=corpus_moved(base_report, new_report),
        coverage=coverage,
        metrics=metric_movements(
            base_report,
            new_report,
            bands=band.ranges() if applicable and band is not None else None,
        ),
        cases=case_movements(
            base_report,
            new_report,
            base_cases=base_records,
            new_cases=new_records,
            labels=cases,
            varying=band.varying_items() if applicable and band is not None else (),
        ),
        alignment=AlignmentMovement(
            base=base_report.alignment,
            new=new_report.alignment,
            moved=base_report.alignment != new_report.alignment,
        ),
        exclusions=_exclusions(base_report, new_report),
        band_id=band.band_id if band is not None else None,
        band_applicable=applicable,
        compared_at=datetime.now(UTC),
        compare_seconds=time.perf_counter() - started,
    )


def _records(run_dir: Path, case_ids: Sequence[str]) -> list[CaseRun]:
    """Read the case records the comparison compares, in the common order."""
    return [read_case(run_dir, case_id) for case_id in case_ids]


def _exclusions(base: Report, new: Report) -> dict[ExclusionReason, ExclusionCount]:
    """Count each exclusion reason on both sides, including one only one side saw.

    A reason neither run recorded is absent rather than a pair of zeros: the report
    names what happened, and eleven zero rows would bury the one that moved.
    """
    counts: dict[ExclusionReason, ExclusionCount] = {}
    for reason in sorted(set(base.exclusions.counts) | set(new.exclusions.counts)):
        was = base.exclusions.counts.get(reason, 0)
        is_now = new.exclusions.counts.get(reason, 0)
        counts[reason] = ExclusionCount(base=was, new=is_now, moved=was != is_now)
    return counts
