"""The summary: the order it is read in, and what it is allowed to claim.

The order is part of the contract because it is what stops a reader drawing the wrong
conclusion from a real number - the conditions and the coverage restriction come before
any metric, and a moved exclusion count explains a moved denominator before a metric
does. The language rules are part of it for the same reason: without a band, nothing in
the output may call a movement a regression (FR-036).
"""

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from golden_harness.cases import Case
from golden_harness.comparison.compare import compare
from golden_harness.comparison.model import CaseMovement, Comparison, MovementGroup
from golden_harness.comparison.render import render_comparison
from golden_harness.record import ExclusionReason

type Runs = Callable[[str], Path]

_SECTIONS = (
    "## Runs",
    "## Conditions",
    "## Coverage",
    "## Alignment and exclusions",
    "## Metrics",
    "## Case movements",
    "## Cases that moved under an unchanged metric",
)


def _positions(summary: str, *headings: str) -> list[int]:
    found = []
    for heading in headings:
        assert heading in summary, f"the summary has no {heading!r} section"
        found.append(summary.index(heading))
    return found


def test_the_summary_follows_the_order_the_contract_fixes(
    run_dir: Runs, labels: list[Case]
) -> None:
    summary = render_comparison(compare(run_dir("base"), run_dir("verdict"), labels))

    positions = _positions(summary, *_SECTIONS)

    assert positions == sorted(positions)


def test_both_runs_are_named_with_their_locations_before_anything_else(
    run_dir: Runs, labels: list[Case]
) -> None:
    comparison = compare(run_dir("base"), run_dir("verdict"), labels)

    summary = render_comparison(comparison)

    assert comparison.base.run_id in summary
    assert comparison.base.location in summary
    assert comparison.new.location in summary


def test_identical_conditions_are_stated_rather_than_left_to_silence(
    run_dir: Runs, labels: list[Case]
) -> None:
    summary = render_comparison(compare(run_dir("base"), run_dir("base"), labels))

    assert "conditions matched" in summary.lower()


def test_a_moved_condition_names_the_field_and_both_values(
    run_dir: Runs, labels: list[Case]
) -> None:
    summary = render_comparison(compare(run_dir("base"), run_dir("conditions"), labels))

    assert "rerank_floor" in summary
    assert "0.58" in summary and "0.52" in summary


def test_a_moved_corpus_is_called_out_apart_from_the_field_list(
    run_dir: Runs, labels: list[Case]
) -> None:
    summary = render_comparison(compare(run_dir("base"), run_dir("corpus"), labels))

    assert "corpus" in summary.lower()
    lines = [line for line in summary.splitlines() if "corpus" in line.lower()]
    assert any("moved" in line.lower() or "changed" in line.lower() for line in lines)


def test_every_metric_is_printed_with_both_values_and_both_fractions(
    run_dir: Runs, labels: list[Case]
) -> None:
    comparison = compare(run_dir("base"), run_dir("verdict"), labels)

    summary = render_comparison(comparison)

    for movement in comparison.metrics:
        assert movement.name in summary
    assert "3 / 4" in summary


def test_a_metric_with_an_empty_denominator_is_rendered_as_not_measured(
    run_dir: Runs, labels: list[Case], tmp_path: Path
) -> None:
    # Two runs whose common cases carry no booking: both booking metrics are measured
    # over an empty denominator, which is `not_measured` and never 0.
    faq_only = _copy_without(
        run_dir("base"), tmp_path / "faq-only", keep=["G801", "G802"]
    )

    summary = render_comparison(compare(run_dir("base"), faq_only, labels))

    assert "not_measured" in summary


def _copy_without(source: Path, destination: Path, *, keep: list[str]) -> Path:
    """Copy a fixture run, keeping only the named cases' records."""
    shutil.copytree(source, destination)
    for record in (destination / "cases").iterdir():
        if record.stem not in keep:
            record.unlink()
    return destination


def test_a_metric_no_scorer_computed_is_rendered_as_not_computed(
    run_dir: Runs, labels: list[Case]
) -> None:
    comparison = compare(run_dir("base"), run_dir("verdict"), labels)
    absent = comparison.model_copy(
        update={
            "metrics": [
                movement.model_copy(update={"new": None, "direction": "not_comparable"})
                if movement.name == "end_to_end_task_success"
                else movement
                for movement in comparison.metrics
            ]
        }
    )

    summary = render_comparison(absent)

    assert "not computed" in summary.lower()


@pytest.mark.parametrize("forbidden", ["regression", "better", "worse"])
def test_without_a_band_no_metric_is_called_a_regression_or_better_or_worse(
    run_dir: Runs, labels: list[Case], forbidden: str
) -> None:
    summary = render_comparison(compare(run_dir("base"), run_dir("verdict"), labels))

    metric_section = summary.split("## Metrics", 1)[1].split("## Case movements", 1)[0]

    assert forbidden not in metric_section.lower()


def test_a_case_movement_is_described_against_its_own_label(
    run_dir: Runs, labels: list[Case]
) -> None:
    summary = render_comparison(compare(run_dir("base"), run_dir("verdict"), labels))

    movements = summary.split("## Case movements", 1)[1]

    assert "G802" in movements
    assert "abstained_rerank_floor" in movements and "answered" in movements
    assert "labelled answerable" in movements
    assert "What should I bring?" in movements


def test_case_movements_are_grouped_by_what_changed(
    run_dir: Runs, labels: list[Case]
) -> None:
    summary = render_comparison(compare(run_dir("base"), run_dir("excluded"), labels))

    assert "### exclusion" in summary


def test_a_metric_names_the_movements_that_affect_it(
    run_dir: Runs, labels: list[Case]
) -> None:
    summary = render_comparison(compare(run_dir("base"), run_dir("verdict"), labels))

    metric_section = summary.split("## Metrics", 1)[1].split("## Case movements", 1)[0]

    assert "G802" in metric_section


def _metric_rows(summary: str) -> dict[str, str]:
    """Return each metric row's cases column, by metric name."""
    section = summary.split("## Metrics", 1)[1].split("## Case movements", 1)[0]
    rows: dict[str, str] = {}
    for line in section.splitlines():
        if not line.startswith("| ") or line.startswith("| metric"):
            continue
        columns = [cell.strip() for cell in line.strip("|").split("|")]
        rows[columns[0]] = columns[-1]
    return rows


@pytest.mark.parametrize("new", ["verdict", "rank", "excluded", "segmentation"])
def test_the_cases_a_metric_names_are_exactly_the_movements_naming_it(
    run_dir: Runs, labels: list[Case], new: str
) -> None:
    # The two readings - by metric and by kind of change - are one list seen from two
    # sides, not two lists that can disagree (FR-017, contracts/comparison-record.md).
    comparison = compare(run_dir("base"), run_dir(new), labels)

    rows = _metric_rows(render_comparison(comparison))

    for movement in comparison.metrics:
        expected = sorted(
            {
                f"{case.case_id} [{case.position}]"
                if case.position is not None
                else case.case_id
                for case in comparison.cases
                if movement.name in case.affects
            }
        )
        assert rows[movement.name] == (", ".join(expected) if expected else "none")


def test_a_per_request_metric_names_the_position_of_the_movement_behind_it(
    run_dir: Runs, labels: list[Case]
) -> None:
    summary = render_comparison(compare(run_dir("base"), run_dir("verdict"), labels))

    rows = _metric_rows(summary)

    assert rows["unserved_answerable_share"] == "G802 [0]"


def test_a_per_turn_metric_names_the_case_alone(
    run_dir: Runs, labels: list[Case]
) -> None:
    summary = render_comparison(compare(run_dir("base"), run_dir("tool"), labels))

    rows = _metric_rows(summary)

    assert rows["tool_selection_correctness"] == "G804"


def test_churn_under_an_unchanged_metric_gets_its_own_section(
    run_dir: Runs, labels: list[Case]
) -> None:
    # Excluding G805 takes its contribution out of metrics whose value does not move
    # with it - the delta of zero that hides a population change (FR-018).
    summary = render_comparison(compare(run_dir("base"), run_dir("excluded"), labels))

    churn = summary.split("## Cases that moved under an unchanged metric", 1)[1]

    assert "G805" in churn


def test_the_summary_renders_from_a_stored_comparison_with_no_run_present(
    run_dir: Runs, labels: list[Case], tmp_path: Path
) -> None:
    base, new = tmp_path / "base", tmp_path / "verdict"
    shutil.copytree(run_dir("base"), base)
    shutil.copytree(run_dir("verdict"), new)
    stored = compare(base, new, labels).model_dump(mode="json")
    shutil.rmtree(base)
    shutil.rmtree(new)

    summary = render_comparison(Comparison.model_validate(stored))

    assert "G802" in summary
    assert "unserved_answerable_share" in summary


def test_the_restriction_and_its_count_are_printed_before_any_metric(
    run_dir: Runs, labels: list[Case], tmp_path: Path
) -> None:
    narrow = _copy_without(run_dir("base"), tmp_path / "narrow", keep=["G801", "G802"])

    summary = render_comparison(compare(run_dir("base"), narrow, labels))

    restriction = summary.index("Restricted")
    assert restriction < summary.index("## Metrics")
    assert "2 cases both runs recorded" in summary
    assert "G803, G804, G805, G806" in summary


def test_an_unrestricted_comparison_says_both_runs_covered_the_same_cases(
    run_dir: Runs, labels: list[Case]
) -> None:
    summary = render_comparison(compare(run_dir("base"), run_dir("verdict"), labels))

    assert "Both runs recorded the same 6 cases." in summary


def test_an_incomplete_run_is_named_as_incomplete_with_its_count(
    run_dir: Runs, labels: list[Case]
) -> None:
    summary = render_comparison(compare(run_dir("base"), run_dir("incomplete"), labels))

    (line,) = [line for line in summary.splitlines() if line.startswith("- New:")]

    assert "incomplete" in line
    assert "5 of 6" in line


def test_a_complete_run_is_named_as_complete(run_dir: Runs, labels: list[Case]) -> None:
    summary = render_comparison(compare(run_dir("base"), run_dir("verdict"), labels))

    assert all(
        "complete" in line and "incomplete" not in line
        for line in summary.splitlines()
        if line.startswith(("- Base:", "- New:"))
    )


def test_a_movement_on_a_labelled_gap_is_not_described_as_labelled_answerable(
    run_dir: Runs, labels: list[Case]
) -> None:
    # The clause names what the label asks. A request labelled `answerable: false` is a
    # gap, and calling it answerable would state the opposite of the label the
    # direction was taken from.
    comparison = compare(run_dir("base"), run_dir("verdict"), labels)
    on_a_gap = comparison.model_copy(
        update={
            "cases": [
                movement.model_copy(update={"labelled": "a gap"})
                if movement.group is MovementGroup.VERDICT
                else movement
                for movement in comparison.cases
            ]
        }
    )

    summary = render_comparison(on_a_gap)

    assert "labelled a gap" in summary
    assert "labelled answerable" not in summary


def _relabelled(comparison: Comparison, **fields: object) -> str:
    """Render the comparison with its first case movement's fields replaced."""
    first, *rest = comparison.cases
    moved = CaseMovement(**{**dict(first), **fields})
    return render_comparison(comparison.model_copy(update={"cases": [moved, *rest]}))


def test_a_movement_on_a_case_a_run_set_aside_names_that_exclusion(
    run_dir: Runs, labels: list[Case]
) -> None:
    # The direction stands - the patient did get a different reply - and the clause is
    # what stops a reader taking it for a metric that moved.
    comparison = compare(run_dir("base"), run_dir("verdict"), labels)

    summary = _relabelled(comparison, new_excluded=ExclusionReason.MISSING_LOG_SLICE)

    (line,) = [line for line in summary.splitlines() if line.startswith("- G802")]
    assert "degraded against its label" in line or "improved against its label" in line
    assert "set aside in the new run (missing_log_slice)" in line


def test_a_movement_set_aside_in_the_baseline_names_that_side(
    run_dir: Runs, labels: list[Case]
) -> None:
    comparison = compare(run_dir("base"), run_dir("verdict"), labels)

    summary = _relabelled(comparison, base_excluded=ExclusionReason.RUN_ERROR)

    assert "set aside in the baseline (run_error)" in summary


def test_a_movement_set_aside_in_both_runs_names_both(
    run_dir: Runs, labels: list[Case]
) -> None:
    comparison = compare(run_dir("base"), run_dir("verdict"), labels)

    summary = _relabelled(
        comparison,
        base_excluded=ExclusionReason.RUN_ERROR,
        new_excluded=ExclusionReason.CANCELLED_TURN,
    )

    assert "set aside in both runs (run_error -> cancelled_turn)" in summary


def test_a_case_set_aside_for_one_reason_in_both_runs_says_it_once(
    run_dir: Runs, labels: list[Case]
) -> None:
    comparison = compare(run_dir("base"), run_dir("verdict"), labels)

    summary = _relabelled(
        comparison,
        base_excluded=ExclusionReason.RUN_ERROR,
        new_excluded=ExclusionReason.RUN_ERROR,
    )

    assert "set aside in both runs (run_error)" in summary


def test_an_exclusion_movement_does_not_repeat_itself(
    run_dir: Runs, labels: list[Case]
) -> None:
    # Its two states already are the two exclusions; a clause naming them again would
    # print the same fact twice on one line.
    summary = render_comparison(compare(run_dir("base"), run_dir("excluded"), labels))

    section = summary.split("### exclusion", 1)[1].split("\n##", 1)[0]
    (line,) = [line for line in section.splitlines() if line.startswith("- G805")]
    assert "scored -> run_error" in line
    assert "set aside in" not in line


def test_a_movement_on_a_case_neither_run_set_aside_says_nothing_about_it(
    run_dir: Runs, labels: list[Case]
) -> None:
    summary = render_comparison(compare(run_dir("base"), run_dir("verdict"), labels))

    assert "set aside in" not in summary


def test_each_run_is_named_with_whether_it_was_traced(
    run_dir: Runs, labels: list[Case], retraced: Callable[[Path, str], Path]
) -> None:
    traced = retraced(run_dir("base"), "traced")

    comparison = compare(run_dir("base"), traced, labels)
    summary = render_comparison(comparison)

    (base_line,) = [line for line in summary.splitlines() if line.startswith("- Base:")]
    (new_line,) = [line for line in summary.splitlines() if line.startswith("- New:")]
    assert "untraced_service_off" in base_line
    assert "traced" in new_line and "untraced" not in new_line
    # Beside the ids, not among the conditions.
    conditions = summary[summary.index("## Conditions") : summary.index("## Coverage")]
    assert "traced" not in conditions


def test_a_stored_comparison_from_before_tracing_still_reads(
    run_dir: Runs, labels: list[Case]
) -> None:
    comparison = compare(run_dir("base"), run_dir("verdict"), labels)
    raw = comparison.model_dump(mode="json")
    for side in ("base", "new"):
        del raw[side]["tracing"]

    reread = Comparison.model_validate(raw)

    assert reread.base.tracing.value == "untraced_service_off"
