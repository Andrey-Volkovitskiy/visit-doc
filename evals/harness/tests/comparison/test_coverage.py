"""Coverage: what the two runs had in common, and what only one of them recorded.

The intersection is what every metric is computed over (FR-023), and the two exclusive
lists are what the report names so that a narrowed comparison is never read as a
statement about the whole set (FR-022). A run that recorded fewer cases than it selected
is reported as incomplete rather than refused (FR-012).
"""

from collections.abc import Callable
from pathlib import Path

from golden_harness.cases import Case
from golden_harness.comparison.coverage import case_coverage, run_side
from golden_harness.record import read_run

type Runs = Callable[[str], Path]


def test_two_runs_over_one_case_set_are_not_restricted(run_dir: Runs) -> None:
    coverage = case_coverage(run_dir("base"), run_dir("verdict"))

    assert coverage.common == ["G801", "G802", "G803", "G804", "G805", "G806"]
    assert coverage.base_only == [] and coverage.new_only == []
    assert not coverage.restricted


def test_a_case_only_the_baseline_recorded_is_named_on_its_own_side(
    run_dir: Runs,
) -> None:
    coverage = case_coverage(run_dir("base"), run_dir("absent"))

    assert coverage.base_only == ["G806"]
    assert coverage.new_only == []
    assert "G806" not in coverage.common
    assert coverage.restricted


def test_a_case_only_the_new_run_recorded_is_named_on_its_own_side(
    run_dir: Runs,
) -> None:
    coverage = case_coverage(run_dir("absent"), run_dir("base"))

    assert coverage.new_only == ["G806"]
    assert coverage.base_only == []
    assert coverage.restricted


def test_the_common_set_is_the_intersection_in_case_order(run_dir: Runs) -> None:
    coverage = case_coverage(run_dir("absent"), run_dir("incomplete"))

    assert coverage.common == sorted(coverage.common)
    assert "G806" not in coverage.common


def test_a_complete_run_says_so(run_dir: Runs) -> None:
    directory = run_dir("base")

    side = run_side(directory, read_run(directory))

    assert side.complete
    assert side.location == str(directory)
    assert len(side.recorded_cases) == len(side.selection.case_ids)


def test_a_run_holding_fewer_records_than_it_selected_is_incomplete(
    run_dir: Runs,
) -> None:
    directory = run_dir("incomplete")

    side = run_side(directory, read_run(directory))

    assert not side.complete
    assert len(side.recorded_cases) == 5
    assert len(side.selection.case_ids) == 6


def test_an_incomplete_run_is_compared_rather_than_refused(
    run_dir: Runs, labels: list[Case]
) -> None:
    from golden_harness.comparison.compare import compare

    comparison = compare(run_dir("base"), run_dir("incomplete"), labels)

    assert not comparison.new.complete
    assert comparison.coverage.base_only == ["G806"]
    assert comparison.metrics
