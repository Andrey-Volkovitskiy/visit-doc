"""The comparison end to end: what it produces, what it refuses, and what it reads.

The last test here is the one the purity check cannot make: renaming both runs'
`report.json` and finding every movement unchanged is what shows the comparison
re-scores its inputs rather than parsing what a previous scoring wrote (FR-003,
research R1).
"""

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from golden_harness.cases import Case
from golden_harness.comparison.compare import NoCommonCasesError, compare
from golden_harness.comparison.model import (
    Comparison,
    MetricMovement,
    MovementDirection,
)
from golden_harness.report import LabelDigestMismatchError

type Runs = Callable[[str], Path]


def _tree(directory: Path) -> dict[str, tuple[int, bytes]]:
    return {
        str(path.relative_to(directory)): (path.stat().st_mtime_ns, path.read_bytes())
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _timeless(comparison: Comparison) -> dict[str, object]:
    stored = comparison.model_dump(mode="json")
    del stored["compared_at"]
    del stored["compare_seconds"]
    return stored


def test_a_run_compared_with_itself_moved_nothing(
    run_dir: Runs, labels: list[Case]
) -> None:
    comparison = compare(run_dir("base"), run_dir("base"), labels)

    assert comparison.cases == []
    assert comparison.conditions.identical
    assert not comparison.corpus_moved
    assert not comparison.alignment.moved
    assert all(
        movement.direction is MovementDirection.UNCHANGED
        for movement in comparison.metrics
    )


def test_a_comparison_records_both_runs_and_where_they_were_read_from(
    run_dir: Runs, labels: list[Case]
) -> None:
    base, new = run_dir("base"), run_dir("verdict")

    comparison = compare(base, new, labels)

    assert comparison.base.location == str(base)
    assert comparison.new.location == str(new)
    assert comparison.base.run_id != comparison.new.run_id
    assert comparison.base.complete and comparison.new.complete


def test_a_comparison_reports_the_alignment_totals_and_the_exclusion_counts(
    run_dir: Runs, labels: list[Case]
) -> None:
    comparison = compare(run_dir("base"), run_dir("excluded"), labels)

    assert comparison.alignment.moved
    assert comparison.alignment.base.excluded == 0
    assert comparison.alignment.new.excluded == 1
    assert comparison.exclusions["run_error"].new == 1
    assert comparison.exclusions["run_error"].moved


def test_differing_label_digests_stop_the_comparison_naming_the_cases(
    run_dir: Runs, labels: list[Case]
) -> None:
    with pytest.raises(LabelDigestMismatchError, match="G802"):
        compare(run_dir("base"), run_dir("labels"), labels)


def test_a_stopped_comparison_reports_no_metric_at_all(
    run_dir: Runs, labels: list[Case]
) -> None:
    with pytest.raises(LabelDigestMismatchError) as stopped:
        compare(run_dir("labels"), run_dir("base"), labels)

    assert "similarity_hit_at_1" not in str(stopped.value)


def test_a_movement_under_an_unchanged_metric_is_still_reported(
    run_dir: Runs, labels: list[Case]
) -> None:
    # Moving the cited chunk from second to first place changes no hit@3, hit@5 or
    # gate survival, and the case behind it is reported all the same (FR-018).
    comparison = compare(run_dir("base"), run_dir("rank"), labels)
    held = [
        movement
        for movement in comparison.metrics
        if movement.name == "similarity_hit_at_3"
    ]

    assert held and held[0].direction is MovementDirection.UNCHANGED
    assert [movement.case_id for movement in comparison.cases] == ["G802"]
    assert any(
        "similarity_hit_at_3" not in movement.affects for movement in comparison.cases
    )


def test_comparing_the_same_pair_twice_produces_the_same_record(
    run_dir: Runs, labels: list[Case]
) -> None:
    once = compare(run_dir("base"), run_dir("verdict"), labels)
    twice = compare(run_dir("base"), run_dir("verdict"), labels)

    assert _timeless(once) == _timeless(twice)


def test_a_comparison_writes_into_neither_input_run(
    run_dir: Runs, labels: list[Case]
) -> None:
    base, new = run_dir("base"), run_dir("verdict")
    before = (_tree(base), _tree(new))

    compare(base, new, labels)

    assert (_tree(base), _tree(new)) == before


def test_the_comparison_never_reads_either_runs_stored_report(
    run_dir: Runs, labels: list[Case], tmp_path: Path
) -> None:
    base, new = tmp_path / "base", tmp_path / "verdict"
    shutil.copytree(run_dir("base"), base)
    shutil.copytree(run_dir("verdict"), new)
    expected = _timeless(compare(base, new, labels))
    for directory in (base, new):
        (directory / "report.json").write_text('{"nonsense": true}', encoding="utf-8")
        (directory / "report.md").write_text("# not a report\n", encoding="utf-8")

    comparison = compare(base, new, labels)

    assert _timeless(comparison) == expected


def test_a_comparison_of_two_runs_with_a_moved_corpus_says_so(
    run_dir: Runs, labels: list[Case]
) -> None:
    comparison = compare(run_dir("base"), run_dir("corpus"), labels)

    assert comparison.corpus_moved
    assert comparison.conditions.changes[0].field == "corpus_sha256"


def test_a_comparison_without_a_band_marks_nothing(
    run_dir: Runs, labels: list[Case]
) -> None:
    comparison = compare(run_dir("base"), run_dir("verdict"), labels)

    assert comparison.band_id is None
    assert not comparison.band_applicable
    assert all(movement.band is None for movement in comparison.metrics)
    assert not any(movement.varies_on_its_own for movement in comparison.cases)


# Narrowing (US3): a few cases driven and compared against a wider baseline, without
# anyone reading the result as a statement about the whole set.


def _narrowed(source: Path, destination: Path, keep: list[str]) -> Path:
    """Copy a fixture run, keeping only the named cases' records."""
    shutil.copytree(source, destination)
    for record in (destination / "cases").iterdir():
        if record.stem not in keep:
            record.unlink()
    return destination


def test_a_narrowed_run_is_scored_over_the_common_cases_on_both_sides(
    run_dir: Runs, labels: list[Case], tmp_path: Path
) -> None:
    kept = ["G801", "G802"]
    narrow = _narrowed(run_dir("verdict"), tmp_path / "narrow", kept)
    whole = compare(run_dir("base"), run_dir("verdict"), labels)

    comparison = compare(run_dir("base"), narrow, labels)

    assert comparison.coverage.common == kept
    unserved = _metric(comparison, "unserved_answerable_share")
    assert unserved.base is not None and unserved.new is not None
    # Two answerable requests among the two common cases, on both sides - never the
    # baseline's own denominator of four (FR-023).
    assert unserved.base.denominator == 2
    assert unserved.new.denominator == 2
    assert _metric(whole, "unserved_answerable_share").base is not None
    assert _metric(whole, "unserved_answerable_share").base.denominator == 4  # type: ignore[union-attr]


def _metric(comparison: Comparison, name: str) -> MetricMovement:
    (movement,) = [movement for movement in comparison.metrics if movement.name == name]
    return movement


def test_a_case_only_one_run_recorded_is_named_and_counts_towards_no_metric(
    run_dir: Runs, labels: list[Case], tmp_path: Path
) -> None:
    narrow = _narrowed(run_dir("base"), tmp_path / "narrow", ["G801", "G802"])

    comparison = compare(run_dir("base"), narrow, labels)

    assert comparison.coverage.base_only == ["G803", "G804", "G805", "G806"]
    assert comparison.coverage.restricted
    # The booking case is on one side only, so both booking metrics have nothing to be
    # computed over rather than the baseline's own value.
    tool = _metric(comparison, "tool_selection_correctness")
    assert tool.base is not None and tool.base.denominator == 0


def test_two_runs_with_no_case_in_common_stop_rather_than_report_no_movement(
    run_dir: Runs, labels: list[Case], tmp_path: Path
) -> None:
    one = _narrowed(run_dir("base"), tmp_path / "one", ["G801"])
    other = _narrowed(run_dir("base"), tmp_path / "other", ["G802"])

    with pytest.raises(NoCommonCasesError, match="no case in common"):
        compare(one, other, labels)


def test_a_narrowed_comparison_still_reports_every_metric(
    run_dir: Runs, labels: list[Case], tmp_path: Path
) -> None:
    narrow = _narrowed(run_dir("base"), tmp_path / "narrow", ["G801", "G802"])

    comparison = compare(run_dir("base"), narrow, labels)

    assert {movement.name for movement in comparison.metrics} >= {
        "unserved_answerable_share",
        "similarity_hit_at_1",
        "tool_selection_correctness",
    }
