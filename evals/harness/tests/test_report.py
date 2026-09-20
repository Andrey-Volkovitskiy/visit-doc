"""The report over the hand-written `fixtures/runs/us1` run.

Every scoring call works on a copy of the fixture: scoring writes `report.json` and
`report.md` beside the run, and the fixture directory is committed test data.
"""

import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from golden_harness.cases import Case, load_cases
from golden_harness.record import ExclusionReason, Unplantable, read_run
from golden_harness.report import (
    SHARED_NUMERATOR_STATEMENT,
    LabelDigestMismatchError,
    Report,
    render_summary,
    score,
    score_run,
    to_json,
)
from golden_harness.scoring.booking import TOOL_SELECTION_STATEMENT
from golden_harness.scoring.metric import Metric

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "runs" / "us1"
_US2 = Path(__file__).resolve().parent / "fixtures" / "runs" / "us2"
_US3 = Path(__file__).resolve().parent / "fixtures" / "runs" / "us3"
_SCHEMA = Path(__file__).resolve().parents[3] / "evals" / "golden" / "schema.json"

_NOT_COMPUTED_WITHOUT_US2_AND_US3 = {
    "similarity_hit_at_1",
    "similarity_hit_at_3",
    "similarity_hit_at_5",
    "similarity_mrr",
    "rerank_hit_at_1",
    "rerank_hit_at_3",
    "rerank_hit_at_5",
    "rerank_mrr",
    "similarity_gate_survival",
    "unserved_answerable_share",
    "wrong_abstention_share",
    "verdict_distribution",
    "tool_selection_correctness",
    "end_to_end_task_success",
}


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    copy = tmp_path / "us1"
    shutil.copytree(_FIXTURE, copy)
    return copy


def _labels(path: Path | None = None) -> list[Case]:
    return load_cases(path if path is not None else _FIXTURE / "labels.json", _SCHEMA)


def _edited_labels(tmp_path: Path, case_id: str, field: str, value: object) -> Path:
    raw = json.loads((_FIXTURE / "labels.json").read_text(encoding="utf-8"))
    for case in raw:
        if case["id"] != case_id:
            continue
        if field == "note":
            case["note"] = value
        else:
            case["requests"][0][field] = value
    path = tmp_path / "edited-labels.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def _snapshot(run_dir: Path) -> dict[str, bytes]:
    files = [run_dir / "run.json", *sorted((run_dir / "cases").iterdir())]
    return {path.name: path.read_bytes() for path in files}


def _without_score_seconds(text: str) -> str:
    return re.sub(r'^\s*"score_seconds": .*\n', "", text, flags=re.MULTILINE)


def test_the_report_carries_the_runs_conditions_and_selection_verbatim(
    run_dir: Path,
) -> None:
    report = score_run(run_dir, _labels())
    run = read_run(run_dir)

    assert report.run_id == run.run_id
    assert report.conditions == run.conditions
    assert report.selection == run.selection
    assert report.labels == run.labels
    assert report.corpus == run.corpus
    assert report.clock == run.clock


def test_alignment_totals_account_for_every_labelled_request(run_dir: Path) -> None:
    totals = score_run(run_dir, _labels()).alignment

    assert (totals.aligned, totals.unaligned, totals.excluded) == (9, 2, 3)
    assert totals.labelled == 14


def test_exclusions_are_counted_per_reason_with_silenced_and_cancelled_distinct(
    run_dir: Path,
) -> None:
    exclusions = score_run(run_dir, _labels()).exclusions

    assert exclusions.counts == {
        ExclusionReason.RUN_ERROR: 1,
        ExclusionReason.HANDED_OFF_TURN: 1,
        ExclusionReason.SILENCED_TURN: 1,
        ExclusionReason.CANCELLED_TURN: 1,
    }


def test_the_report_counts_cases_that_needed_more_than_one_attempt(
    run_dir: Path,
) -> None:
    assert score_run(run_dir, _labels()).cases_needing_retry == 1


def test_the_report_lists_assistant_failed_turns_that_still_replied(
    run_dir: Path,
) -> None:
    assert score_run(run_dir, _labels()).assistant_failed_with_reply == ["G909"]


def _with_failed_reply_turn(run_dir: Path, case_id: str) -> dict[str, Any]:
    """Give the case a reply turn marked `assistant_failed` beside a stored reply."""
    path = run_dir / "cases" / f"{case_id}.json"
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    raw["reply_turn"] = {
        "terminal": raw["terminal"],
        "patient_message": {
            **raw["patient_message"],
            "id": "01K5MSGREPLY00000000000000",
            "attention_mark": "assistant_failed",
        },
        "assistant_message": {
            **raw["assistant_message"],
            "id": "01K5RPLREPLY00000000000000",
        },
        "events": None,
    }
    path.write_text(json.dumps(raw), encoding="utf-8")
    return raw


def test_a_reply_turn_marked_assistant_failed_that_stored_a_reply_is_listed(
    run_dir: Path,
) -> None:
    raw = _with_failed_reply_turn(run_dir, "G901")
    assert raw["patient_message"]["attention_mark"] is None

    report = score_run(run_dir, _labels())

    assert report.assistant_failed_with_reply == ["G901", "G909"]
    assert report.assistant_failed_with_reply_turns == {
        "G901": ["reply"],
        "G909": ["first"],
    }
    listed = json.loads(to_json(report))
    assert listed["assistant_failed_with_reply_turns"] == {
        "G901": ["reply"],
        "G909": ["first"],
    }
    summary = render_summary(report)
    (line,) = [
        line
        for line in summary.splitlines()
        if line.startswith("- Turns marked assistant_failed that still replied:")
    ]
    assert line.endswith(": G901 (reply), G909 (first)")


def test_a_case_whose_both_turns_failed_yet_replied_is_listed_once_naming_both(
    run_dir: Path,
) -> None:
    _with_failed_reply_turn(run_dir, "G909")

    report = score_run(run_dir, _labels())

    assert report.assistant_failed_with_reply == ["G909"]
    assert report.assistant_failed_with_reply_turns == {"G909": ["first", "reply"]}
    assert "still replied: G909 (first, reply)" in render_summary(report)


def test_a_changed_intent_stops_scoring_and_names_the_case(
    run_dir: Path, tmp_path: Path
) -> None:
    labels = _labels(_edited_labels(tmp_path, "G908", "intent", "not_authorized"))

    with pytest.raises(LabelDigestMismatchError, match="G908"):
        score_run(run_dir, labels)

    assert not (run_dir / "report.json").exists()
    assert not (run_dir / "report.md").exists()


def test_a_label_missing_a_selected_case_stops_scoring_and_names_it(
    run_dir: Path,
) -> None:
    labels = [case for case in _labels() if case.id != "G905"]

    with pytest.raises(LabelDigestMismatchError, match="G905"):
        score_run(run_dir, labels)


@pytest.mark.parametrize(("field", "value"), [("gist", "reworded"), ("note", "new")])
def test_an_edited_gist_or_note_scores_normally(
    run_dir: Path, tmp_path: Path, field: str, value: str
) -> None:
    labels = _labels(_edited_labels(tmp_path, "G908", field, value))

    report = score_run(run_dir, labels)

    assert report.alignment.labelled == 14


def test_drive_seconds_is_the_sum_of_the_cases_not_the_wall_clock_span(
    run_dir: Path,
) -> None:
    report = score_run(run_dir, _labels())
    run = read_run(run_dir)

    assert run.finished_at is not None
    assert report.drive_seconds == 42.0
    assert report.drive_seconds != (run.finished_at - run.started_at).total_seconds()


def test_scoring_times_itself_and_leaves_the_stored_run_untouched(
    run_dir: Path,
) -> None:
    before = _snapshot(run_dir)

    report = score_run(run_dir, _labels())

    assert report.score_seconds >= 0.0
    assert _snapshot(run_dir) == before
    assert "score_seconds" not in (run_dir / "run.json").read_text(encoding="utf-8")


def test_scoring_writes_the_report_beside_the_run(run_dir: Path) -> None:
    report = score_run(run_dir, _labels())

    assert (run_dir / "report.json").read_text(encoding="utf-8") == to_json(report)
    assert json.loads(to_json(report))["run_id"] == report.run_id
    assert (run_dir / "report.md").read_text(encoding="utf-8") == render_summary(report)


def test_not_computed_names_every_metric_no_registered_scorer_produced(
    run_dir: Path,
) -> None:
    report = score_run(run_dir, _labels())

    assert report.not_computed == []
    assert report.classification is not None


def test_scoring_the_same_run_twice_is_byte_identical_apart_from_score_seconds(
    run_dir: Path,
) -> None:
    score_run(run_dir, _labels())
    first = (run_dir / "report.json").read_text(encoding="utf-8")
    score_run(run_dir, _labels())
    second = (run_dir / "report.json").read_text(encoding="utf-8")

    assert '"score_seconds"' in first
    assert _without_score_seconds(first) == _without_score_seconds(second)


def test_the_summary_publishes_each_metrics_numerator_and_denominator(
    run_dir: Path,
) -> None:
    report = score_run(run_dir, _labels())
    summary = render_summary(report)

    assert report.classification is not None
    for metric in (
        report.classification.request_count_accuracy,
        report.classification.intent_accuracy,
        report.classification.exact_segmentation_match,
    ):
        assert metric.name in summary
        assert f"{metric.numerator} / {metric.denominator}" in summary
    for name in _NOT_COMPUTED_WITHOUT_US2_AND_US3:
        assert name in summary


def test_the_summary_reads_conditions_then_alignment_then_exclusions_then_metrics(
    run_dir: Path,
) -> None:
    summary = render_summary(score_run(run_dir, _labels()))

    headings = ["## Conditions", "## Alignment", "## Exclusions", "## Metrics"]
    positions = [summary.index(heading) for heading in headings]
    assert positions == sorted(positions)


def test_a_case_file_for_a_case_the_run_did_not_select_is_refused(
    run_dir: Path,
) -> None:
    shutil.copy(run_dir / "cases" / "G901.json", run_dir / "cases" / "G999.json")

    with pytest.raises(ValueError, match="G999"):
        score_run(run_dir, _labels())


@pytest.fixture
def us2_dir(tmp_path: Path) -> Path:
    copy = tmp_path / "us2"
    shutil.copytree(_US2, copy)
    return copy


def _us2_labels() -> list[Case]:
    return load_cases(_US2 / "labels.json", _SCHEMA)


def _row(summary: str, name: str) -> str:
    rows = [line for line in summary.splitlines() if line.startswith(f"| {name} |")]
    assert len(rows) == 1, f"{name}: {rows}"
    return rows[0]


def test_retrieval_and_serving_metrics_are_reported_with_their_exclusions(
    us2_dir: Path,
) -> None:
    report = score_run(us2_dir, _us2_labels())

    retrieval = {m.name: m for m in report.retrieval.metrics()}
    serving = {m.name: m for m in report.serving.metrics()}
    assert set(retrieval) == {
        "similarity_hit_at_1",
        "similarity_hit_at_3",
        "similarity_hit_at_5",
        "similarity_mrr",
        "rerank_hit_at_1",
        "rerank_hit_at_3",
        "rerank_hit_at_5",
        "rerank_mrr",
        "similarity_gate_survival",
    }
    assert {"unserved_answerable_share", "wrong_abstention_share"} <= set(serving)
    assert retrieval["similarity_mrr"].denominator == 9
    assert (
        retrieval["rerank_mrr"].excluded.counts[ExclusionReason.NOT_REACHED_RERANKER]
        == 2
    )
    assert (
        serving["unserved_answerable_share"].numerator,
        serving["unserved_answerable_share"].denominator,
    ) == (6, 12)
    assert report.serving.verdict_distribution.total == 13


def test_the_summary_publishes_every_retrieval_and_serving_row(
    us2_dir: Path,
) -> None:
    report = score_run(us2_dir, _us2_labels())
    summary = render_summary(report)

    metrics: list[Metric] = [*report.retrieval.metrics(), *report.serving.metrics()]
    assert any(m.name.startswith("verdict_distribution.") for m in metrics)
    for metric in metrics:
        row = _row(summary, metric.name)
        assert f"/ {metric.denominator} |" in row
        for reason, count in metric.excluded.counts.items():
            assert f"{reason.value} {count}" in row


def test_the_summary_states_c1_and_c2_share_a_numerator_and_gives_both_denominators(
    us2_dir: Path,
) -> None:
    report = score_run(us2_dir, _us2_labels())
    summary = render_summary(report)

    assert "share part of" in SHARED_NUMERATOR_STATEMENT
    assert SHARED_NUMERATOR_STATEMENT in summary
    denominators = [line for line in summary.splitlines() if "Denominators:" in line]
    assert len(denominators) == 1
    assert "unserved_answerable_share 12" in denominators[0]
    assert "wrong_abstention_share 5" in denominators[0]


def test_the_summary_says_rerank_hit_at_5_is_1_by_construction_beside_it(
    us2_dir: Path,
) -> None:
    report = score_run(us2_dir, _us2_labels())
    summary = render_summary(report)

    statement = report.retrieval.rerank_hit_at_5_statement
    assert statement is not None
    lines = summary.splitlines()
    row = lines.index(_row(summary, "rerank_hit_at_5"))
    assert statement in "\n".join(lines[row:])
    assert summary.index(statement) > summary.index("| rerank_hit_at_5 |")


def test_the_summary_names_the_requests_behind_the_serving_values(
    us2_dir: Path,
) -> None:
    summary = render_summary(score_run(us2_dir, _us2_labels()))

    assert "G927" in summary
    assert "What paperwork is needed for a visit?" in summary
    assert "rerank_floor" in summary
    assert "Good morning! How early should I arrive?" in summary
    assert "Is there parking at the clinic?" in summary
    assert "Can I see the doctor over video?" in summary


@pytest.fixture
def us3_dir(tmp_path: Path) -> Path:
    copy = tmp_path / "us3"
    shutil.copytree(_US3, copy)
    return copy


def _us3_labels() -> list[Case]:
    return load_cases(_US3 / "labels.json", _SCHEMA)


def test_booking_metrics_are_reported_with_their_exclusions(us3_dir: Path) -> None:
    report = score_run(us3_dir, _us3_labels())

    booking = {m.name: m for m in report.booking.metrics()}
    assert set(booking) == {"tool_selection_correctness", "end_to_end_task_success"}
    success = booking["end_to_end_task_success"]
    assert (success.numerator, success.denominator) == (5, 8)
    assert booking["tool_selection_correctness"].denominator == 8
    assert report.not_computed == []


def test_the_summary_publishes_the_booking_rows_and_the_booking_half_statement(
    us3_dir: Path,
) -> None:
    report = score_run(us3_dir, _us3_labels())
    summary = render_summary(report)

    for metric in report.booking.metrics():
        row = _row(summary, metric.name)
        assert f"{metric.numerator} / {metric.denominator} |" in row
        for reason, count in metric.excluded.counts.items():
            assert f"{reason.value} {count}" in row
    assert TOOL_SELECTION_STATEMENT in summary
    assert summary.index(TOOL_SELECTION_STATEMENT) > summary.index(
        "| tool_selection_correctness |"
    )


def test_the_summary_names_both_halves_of_every_booking_failure(
    us3_dir: Path,
) -> None:
    summary = render_summary(score_run(us3_dir, _us3_labels()))

    failures = summary[summary.index("### End-to-end failures") :]
    g945 = next(line for line in failures.splitlines() if line.startswith("- G945"))
    assert "William Osler +4d 10:00 cancelled" in g945
    assert "William Osler 2026-03-06T10:00:00 standing" in g945
    assert "G943" in failures
    misses = summary[summary.index("### Tool-selection misses") :]
    assert "G945" in misses
    assert "cancel_appointment" in misses


_BUSY = Unplantable.model_validate(
    {
        "situation": "booking_refused",
        "detail": "William Osler +1d 10:00: refused: "
        "BOOKING_FAILURE_REASON_PRACTITIONER_BUSY taken",
        "failure_reason": "BOOKING_FAILURE_REASON_PRACTITIONER_BUSY",
        "scheduler_message": "taken",
    }
)


def _with_unplantable(run_dir: Path, case_id: str, unplantable: Unplantable) -> None:
    path = run_dir / "cases" / f"{case_id}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["unplantable"] = unplantable.model_dump(mode="json")
    path.write_text(json.dumps(raw), encoding="utf-8")


def test_the_report_lists_each_unresolvable_fixture_with_why_it_would_not_plant(
    us3_dir: Path,
) -> None:
    _with_unplantable(us3_dir, "G946", _BUSY)

    report = score_run(us3_dir, _us3_labels())

    assert [(f.case_id, f.unplantable) for f in report.unresolvable_fixtures] == [
        ("G946", _BUSY)
    ]
    listed = json.loads(to_json(report))["unresolvable_fixtures"]
    assert listed == [{"case_id": "G946", "unplantable": _BUSY.model_dump(mode="json")}]


def test_an_unresolvable_fixture_recorded_without_its_detail_is_still_listed(
    us3_dir: Path,
) -> None:
    report = score_run(us3_dir, _us3_labels())

    assert [(f.case_id, f.unplantable) for f in report.unresolvable_fixtures] == [
        ("G946", None)
    ]
    summary = render_summary(report)
    section = summary[summary.index("### Fixtures that would not plant") :]
    assert "- G946: not recorded" in section


def test_a_run_with_no_unresolvable_fixture_lists_none(run_dir: Path) -> None:
    report = score_run(run_dir, _labels())

    assert report.unresolvable_fixtures == []
    summary = render_summary(report)
    section = summary[summary.index("### Fixtures that would not plant") :]
    assert section.splitlines()[2] == "None."


def test_the_summary_names_each_unresolvable_fixtures_situation_and_detail(
    us3_dir: Path,
) -> None:
    _with_unplantable(us3_dir, "G946", _BUSY)

    summary = render_summary(score_run(us3_dir, _us3_labels()))

    exclusions = summary[summary.index("## Exclusions") : summary.index("## Metrics")]
    assert f"- G946 booking_refused: {_BUSY.detail}" in exclusions


# --- a case with a scripted reply (FR-037b) -------------------------------------------


def test_the_summary_says_tool_selection_spans_both_turns_of_a_reply_case(
    us3_dir: Path,
) -> None:
    summary = render_summary(score_run(us3_dir, _us3_labels()))

    booking = summary[summary.index("## Booking") : summary.index("### Tool-selection")]
    assert "scripted reply" in booking
    assert "both of its turns" in booking


def test_the_summary_scores_a_reply_case_on_its_last_read(us3_dir: Path) -> None:
    summary = render_summary(score_run(us3_dir, _us3_labels()))

    failures = summary[summary.index("### End-to-end failures") :].splitlines()
    assert not [line for line in failures if line.startswith("- G950")]
    (g951,) = [line for line in failures if line.startswith("- G951")]
    assert "after the last turn" in g951
    assert "William Osler +7d 09:00 standing" in g951
    assert "found, not expected: none" in g951
    (g945,) = [line for line in failures if line.startswith("- G945")]
    assert "after the last turn" in g945


def _with_reply_turns_duplicating_each_turn(run_dir: Path) -> None:
    """Give every case a reply turn whose events repeat the first turn's own.

    A scorer that read them would see each retrieval event twice, which the log
    contract refuses, or count a second reply's segmentation.
    """
    for path in sorted((run_dir / "cases").iterdir()):
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["reply_turn"] = {
            "terminal": raw["terminal"],
            "patient_message": raw["patient_message"],
            "assistant_message": None,
            "events": raw["events"],
        }
        path.write_text(json.dumps(raw), encoding="utf-8")


def test_classification_and_retrieval_read_the_first_turn_only(
    us2_dir: Path,
) -> None:
    before = score_run(us2_dir, _us2_labels())
    _with_reply_turns_duplicating_each_turn(us2_dir)

    after = score_run(us2_dir, _us2_labels())

    assert after.classification == before.classification
    assert after.retrieval == before.retrieval
    assert after.alignment == before.alignment


# --- streams that broke the service's contract and then settled (FR-041d) -----------

_BROKEN_STREAMS_LINE = "- Streams that broke the service's contract and then settled:"


def _with_first_turn_stream(run_dir: Path, case_id: str, **fields: Any) -> None:
    """Overwrite the case's first-turn fields with `fields`."""
    path = run_dir / "cases" / f"{case_id}.json"
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    raw.update(fields)
    path.write_text(json.dumps(raw), encoding="utf-8")


def _with_reply_turn_stream(run_dir: Path, case_id: str, **fields: Any) -> None:
    """Give the case a reply turn that stored a reply and ended as `fields` say."""
    path = run_dir / "cases" / f"{case_id}.json"
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    raw["reply_turn"] = {
        "patient_message": {
            **raw["patient_message"],
            "id": "01K5MSGREPLY00000000000000",
            "attention_mark": None,
        },
        "assistant_message": {
            **raw["assistant_message"],
            "id": "01K5RPLREPLY00000000000000",
        },
        "events": None,
        **fields,
    }
    path.write_text(json.dumps(raw), encoding="utf-8")


def _broken_streams_line(summary: str) -> str:
    (line,) = [line for line in summary.splitlines() if line.startswith("- Streams")]
    return line


def test_a_run_with_no_broken_stream_lists_none(run_dir: Path) -> None:
    report = score_run(run_dir, _labels())

    assert report.contract_broken_then_settled == []
    assert report.contract_broken_then_settled_turns == {}
    assert _broken_streams_line(render_summary(report)) == (
        f"{_BROKEN_STREAMS_LINE} none"
    )


def test_a_first_or_reply_turn_whose_stream_broke_the_contract_and_settled_is_listed(
    run_dir: Path,
) -> None:
    # G901's first turn stored its reply; G903's reply turn did; G902's first turn and
    # G904's reply turn were only timed out or cut off, and are not listed.
    _with_first_turn_stream(run_dir, "G901", terminal=None, stream_broke_contract=True)
    _with_reply_turn_stream(run_dir, "G903", terminal=None, stream_broke_contract=True)
    cut_off = {"kind": "error", "payload": {}}
    _with_first_turn_stream(run_dir, "G902", terminal=cut_off)
    _with_reply_turn_stream(run_dir, "G904", terminal=cut_off)

    report = score_run(run_dir, _labels())

    assert report.contract_broken_then_settled == ["G901", "G903"]
    assert report.contract_broken_then_settled_turns == {
        "G901": ["first"],
        "G903": ["reply"],
    }
    listed = json.loads(to_json(report))
    assert listed["contract_broken_then_settled"] == ["G901", "G903"]
    assert listed["contract_broken_then_settled_turns"] == {
        "G901": ["first"],
        "G903": ["reply"],
    }
    assert _broken_streams_line(render_summary(report)) == (
        f"{_BROKEN_STREAMS_LINE} G901 (first), G903 (reply)"
    )


def test_a_broken_stream_that_settled_on_the_assistant_failed_mark_is_listed(
    run_dir: Path,
) -> None:
    # G909's first turn is marked assistant_failed beside its reply; drop the reply, so
    # the mark alone says the turn ended.
    _with_first_turn_stream(
        run_dir,
        "G909",
        terminal=None,
        stream_broke_contract=True,
        assistant_message=None,
        excluded="run_error",
    )

    report = score_run(run_dir, _labels())

    assert report.contract_broken_then_settled_turns == {"G909": ["first"]}


def test_a_case_whose_both_streams_broke_the_contract_is_listed_once_naming_both(
    run_dir: Path,
) -> None:
    _with_first_turn_stream(run_dir, "G901", terminal=None, stream_broke_contract=True)
    _with_reply_turn_stream(run_dir, "G901", terminal=None, stream_broke_contract=True)

    report = score_run(run_dir, _labels())

    assert report.contract_broken_then_settled == ["G901"]
    assert report.contract_broken_then_settled_turns == {"G901": ["first", "reply"]}
    assert "settled: G901 (first, reply)" in render_summary(report)


def test_a_broken_stream_that_never_settled_is_not_listed(run_dir: Path) -> None:
    # Written as outcome unknown: the thread showed neither a reply nor the mark.
    _with_first_turn_stream(
        run_dir,
        "G901",
        terminal=None,
        stream_broke_contract=True,
        assistant_message=None,
        segments=None,
        events=None,
        excluded="outcome_unknown",
    )
    _with_reply_turn_stream(
        run_dir,
        "G903",
        terminal=None,
        stream_broke_contract=True,
        patient_message=None,
        assistant_message=None,
    )

    report = score_run(run_dir, _labels())

    assert report.contract_broken_then_settled == []
    assert report.contract_broken_then_settled_turns == {}


# The pure entry point (spec 013 T003-T005): `score` computes a report and writes
# nothing, so a run under `specs/` can be scored where it sits, and takes a restriction
# so two runs covering different case sets can be scored over the cases they share.


def _tree(run_dir: Path) -> dict[str, tuple[int, bytes]]:
    """Every file under `run_dir` with its modification time and its bytes."""
    return {
        str(path.relative_to(run_dir)): (path.stat().st_mtime_ns, path.read_bytes())
        for path in sorted(run_dir.rglob("*"))
        if path.is_file()
    }


def _comparable(report: Report) -> dict[str, Any]:
    """The report without the one field two scorings of one run may differ in."""
    listed: dict[str, Any] = json.loads(to_json(report))
    del listed["score_seconds"]
    return listed


def test_the_pure_entry_point_writes_nothing_into_the_run_it_scores(
    run_dir: Path,
) -> None:
    before = _tree(run_dir)

    score(run_dir, _labels())

    assert _tree(run_dir) == before
    assert not (run_dir / "report.json").exists()
    assert not (run_dir / "report.md").exists()


def test_score_run_still_writes_both_artifacts_beside_the_run(run_dir: Path) -> None:
    report = score_run(run_dir, _labels())

    assert (run_dir / "report.json").read_text(encoding="utf-8") == to_json(report)
    assert (run_dir / "report.md").read_text(encoding="utf-8") == render_summary(report)


def test_the_pure_entry_point_and_the_writing_one_produce_the_same_report(
    run_dir: Path,
) -> None:
    pure = score(run_dir, _labels())
    written = score_run(run_dir, _labels())

    assert _comparable(pure) == _comparable(written)


def test_a_restricted_scoring_computes_every_metric_over_only_those_cases(
    run_dir: Path,
) -> None:
    only = ["G901", "G902"]

    report = score(run_dir, _labels(), only=only)

    assert report.recorded_cases == only
    # Four labelled requests between them - G901's two aligned, G902's two unaligned -
    # and neither case excluded, so the restricted totals are those two cases' alone.
    assert report.alignment.labelled == 4
    assert (report.alignment.aligned, report.alignment.unaligned) == (2, 2)
    assert report.alignment.excluded == 0
    assert report.exclusions.counts == {}
    assert report.classification.request_count_accuracy.denominator == 2


def test_a_restriction_that_names_no_case_is_refused(run_dir: Path) -> None:
    with pytest.raises(ValueError, match="restriction"):
        score(run_dir, _labels(), only=[])


def test_a_restriction_naming_a_case_the_run_did_not_record_is_refused(
    run_dir: Path,
) -> None:
    with pytest.raises(ValueError, match="G999"):
        score(run_dir, _labels(), only=["G901", "G999"])


def test_a_restriction_leaves_the_runs_own_conditions_and_selection_intact(
    run_dir: Path,
) -> None:
    run = read_run(run_dir)

    report = score(run_dir, _labels(), only=["G901"])

    assert report.selection == run.selection
    assert report.conditions == run.conditions
    assert report.labels == run.labels
