"""Scoring a stored run into its report, and rendering the report for a reader.

Scoring reads `run.json` and the case files and nothing else, so a run recorded weeks
ago - or with the stack down - scores the same as the day it was driven. It refuses
labels whose scored fields changed since the run was taken, because the stored turns
answered those labels and no others. It never writes into the stored run: its output is
`report.json` and `report.md` beside it, replaced on every re-score.

The report carries every metric the contracts define, split into those a registered
scorer computed and those named as not computed.
"""

import json
import time
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict

from golden_harness.cases import AppointmentRef, Case, Selection, label_digests
from golden_harness.record import (
    AppointmentState,
    CaseRun,
    CorpusRecord,
    ExclusionReason,
    RunConditions,
    StoredMessage,
    Unplantable,
    marked_assistant_failed,
    read_case,
    read_run,
    recorded_case_ids,
    turn_settled,
    write_atomically,
)
from golden_harness.scoring.alignment import AlignmentTotals, align_run
from golden_harness.scoring.booking import (
    BOOKING_METRICS,
    BookingScores,
    PostStateRead,
    TaskFailure,
    ToolSelectionMiss,
    score_booking,
)
from golden_harness.scoring.classification import (
    CLASSIFICATION_METRICS,
    ClassificationScores,
    score_classification,
)
from golden_harness.scoring.metric import NOT_MEASURED, Exclusions, Metric
from golden_harness.scoring.retrieval import (
    RERANK_HIT_AT_5,
    RETRIEVAL_METRICS,
    RetrievalScores,
    score_retrieval,
)
from golden_harness.scoring.serving import (
    SERVING_METRICS,
    Abstention,
    Answer,
    ServingScores,
    UnservedRequest,
    score_serving,
)

REPORT_JSON: Final = "report.json"
REPORT_MD: Final = "report.md"


class MetricFamily(StrEnum):
    """The groups the contracts define metrics in."""

    CLASSIFICATION = "classification"
    RETRIEVAL = "retrieval"
    SERVING = "serving"
    BOOKING = "booking"


# Every metric the contracts define, by family, in the order a report lists them.
METRICS_BY_FAMILY: Final[dict[MetricFamily, tuple[str, ...]]] = {
    MetricFamily.CLASSIFICATION: CLASSIFICATION_METRICS,
    MetricFamily.RETRIEVAL: RETRIEVAL_METRICS,
    MetricFamily.SERVING: SERVING_METRICS,
    MetricFamily.BOOKING: BOOKING_METRICS,
}

# The families a scorer exists for; every other family's metrics are not computed.
REGISTERED_FAMILIES: Final = frozenset(
    {
        MetricFamily.CLASSIFICATION,
        MetricFamily.RETRIEVAL,
        MetricFamily.SERVING,
        MetricFamily.BOOKING,
    }
)

# Published beside the two zero-target serving shares (FR-033): a reader who sees one
# move without the other must not take it for a contradiction.
SHARED_NUMERATOR_STATEMENT: Final = (
    "unserved_answerable_share and wrong_abstention_share share part of their "
    "numerator - an abstention on a labelled-answerable request counts in both - and "
    "differ in their denominator: the first asks how much of what could be served was "
    "not, over the labelled-answerable requests whose turn was permitted to answer; "
    "the second asks how often an abstention was wrong, over every abstention the run "
    "produced. A run can move one without moving the other."
)


class RecordedTurn(StrEnum):
    """Which of a case's turns: the first, or the scripted reply's (FR-037b)."""

    FIRST = "first"
    REPLY = "reply"


class LabelDigestMismatchError(ValueError):
    """The labels differ from those the run was taken against; names the cases."""


class UnresolvableFixture(BaseModel):
    """A case excluded because its fixture would not plant, and why it would not.

    `unplantable` is None for a case recorded before the harness stored why.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    unplantable: Unplantable | None


class Report(BaseModel):
    """What scoring computed from one stored run.

    The run's own `conditions`, `selection`, `labels`, `corpus` and `clock` are carried
    verbatim, since a number measured under other conditions is not comparable to one
    measured under these. `exclusions` counts the recorded cases by their stored
    turn-level reason, and `unresolvable_fixtures` lists the cases excluded as
    `unresolvable_fixture` with why each would not plant, so a label problem is told
    apart from a scheduler that could not be reached. `assistant_failed_with_reply`
    names, once each in selection order, the cases with a turn marked
    `assistant_failed` that still stored a reply, and
    `assistant_failed_with_reply_turns` says which of each such case's turns it was.
    `contract_broken_then_settled` names, the same way, the cases with a turn whose
    stream broke the service's contract and that then settled - stored a reply, or was
    marked `assistant_failed` - and `contract_broken_then_settled_turns` which turns
    (FR-041d); a stream that only broke off or timed out is not among them.
    `drive_seconds` is the sum of the cases' own elapsed times, and `score_seconds` how
    long this scoring took.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    session_id: str
    clock: datetime
    corpus: CorpusRecord
    conditions: RunConditions
    selection: Selection
    labels: dict[str, str]
    recorded_cases: list[str]
    alignment: AlignmentTotals
    exclusions: Exclusions
    unresolvable_fixtures: list[UnresolvableFixture]
    classification: ClassificationScores
    retrieval: RetrievalScores
    serving: ServingScores
    booking: BookingScores
    cases_needing_retry: int
    assistant_failed_with_reply: list[str]
    assistant_failed_with_reply_turns: dict[str, list[RecordedTurn]]
    contract_broken_then_settled: list[str]
    contract_broken_then_settled_turns: dict[str, list[RecordedTurn]]
    not_computed: list[str]
    drive_seconds: float
    score_seconds: float


def score_run(run_dir: Path, cases: Sequence[Case]) -> Report:
    """Score the run stored in `run_dir` against `cases`; write its report beside it.

    Args:
        cases: the labels; must hold every case the run selected, with the scored
            fields the run recorded digests of.

    Raises: LabelDigestMismatchError, before anything is computed, when a selected case
        is missing from `cases` or its digest differs from the run's; ValueError when
        the run holds a case file for a case it did not select; ConservationError when
        alignment does not account for every labelled request.
    """
    started = time.perf_counter()
    run = read_run(run_dir)
    by_id = {case.id: case for case in cases}
    selected = [by_id[i] for i in run.selection.case_ids if i in by_id]
    digests = label_digests(selected)
    mismatched = [
        case_id
        for case_id in run.selection.case_ids
        if digests.get(case_id) != run.labels.get(case_id)
    ]
    if mismatched:
        raise LabelDigestMismatchError(
            "these cases' labels differ from the ones the run was taken against: "
            f"{', '.join(mismatched)}"
        )

    on_disk = recorded_case_ids(run_dir)
    unselected = sorted(set(on_disk) - set(run.selection.case_ids))
    if unselected:
        raise ValueError(f"case files for cases the run did not select: {unselected}")
    recorded = [case_id for case_id in run.selection.case_ids if case_id in on_disk]
    case_runs = [read_case(run_dir, case_id) for case_id in recorded]

    run_alignment = align_run(selected, case_runs)
    classification = score_classification(selected, run_alignment, case_runs)
    retrieval = score_retrieval(
        selected,
        run_alignment,
        case_runs,
        entry_ids=run.entry_ids,
        similarity_cap=run.conditions.similarity_cap,
    )
    serving = score_serving(selected, run_alignment, case_runs)
    booking = score_booking(selected, case_runs, clock=run.clock)
    failed_turns = {
        case_run.case_id: turns
        for case_run in case_runs
        if (turns := _assistant_failed_turns_with_reply(case_run))
    }
    broken_turns = {
        case_run.case_id: turns
        for case_run in case_runs
        if (turns := _contract_broken_turns_that_settled(case_run))
    }
    registered = {
        name for family in REGISTERED_FAMILIES for name in METRICS_BY_FAMILY[family]
    }
    report = Report(
        run_id=run.run_id,
        session_id=run.session_id,
        clock=run.clock,
        corpus=run.corpus,
        conditions=run.conditions,
        selection=run.selection,
        labels=run.labels,
        recorded_cases=recorded,
        alignment=run_alignment.totals,
        exclusions=Exclusions.tally(
            case_run.excluded for case_run in case_runs if case_run.excluded is not None
        ),
        unresolvable_fixtures=[
            UnresolvableFixture(
                case_id=case_run.case_id, unplantable=case_run.unplantable
            )
            for case_run in case_runs
            if case_run.excluded is ExclusionReason.UNRESOLVABLE_FIXTURE
        ],
        classification=classification,
        retrieval=retrieval,
        serving=serving,
        booking=booking,
        cases_needing_retry=sum(1 for case_run in case_runs if case_run.attempts > 1),
        assistant_failed_with_reply=list(failed_turns),
        assistant_failed_with_reply_turns=failed_turns,
        contract_broken_then_settled=list(broken_turns),
        contract_broken_then_settled_turns=broken_turns,
        not_computed=[
            name
            for names in METRICS_BY_FAMILY.values()
            for name in names
            if name not in registered
        ],
        drive_seconds=sum(case_run.elapsed_seconds for case_run in case_runs),
        score_seconds=time.perf_counter() - started,
    )
    write_atomically(run_dir / REPORT_JSON, to_json(report))
    write_atomically(run_dir / REPORT_MD, render_summary(report))
    return report


type _StoredTurn = tuple[RecordedTurn, bool, StoredMessage | None, StoredMessage | None]


def _stored_turns(case_run: CaseRun) -> list[_StoredTurn]:
    """Return each recorded turn of a case, with whether its stream broke the contract.

    Returns: the first turn, then the reply's when one was recorded, each with its
        `stream_broke_contract`, its patient message and its reply
    """
    turns: list[_StoredTurn] = [
        (
            RecordedTurn.FIRST,
            case_run.stream_broke_contract,
            case_run.patient_message,
            case_run.assistant_message,
        )
    ]
    if case_run.reply_turn is not None:
        reply = case_run.reply_turn
        turns.append(
            (
                RecordedTurn.REPLY,
                reply.stream_broke_contract,
                reply.patient_message,
                reply.assistant_message,
            )
        )
    return turns


def _assistant_failed_turns_with_reply(case_run: CaseRun) -> list[RecordedTurn]:
    """Return the case's turns marked `assistant_failed` that still stored a reply."""
    return [
        turn
        for turn, _broke, patient, assistant in _stored_turns(case_run)
        if _failed_yet_replied(patient, assistant)
    ]


def _contract_broken_turns_that_settled(case_run: CaseRun) -> list[RecordedTurn]:
    """Return the case's turns whose stream broke the contract and that then settled.

    Settled is what FR-041c waits for, read from what the turn stored: a reply, or the
    patient message marked `assistant_failed`. A turn recorded without either did not
    settle - it is `outcome_unknown` - and is not returned.
    """
    return [
        turn
        for turn, broke, patient, assistant in _stored_turns(case_run)
        if broke and turn_settled(patient, assistant)
    ]


def _failed_yet_replied(
    patient: StoredMessage | None, assistant: StoredMessage | None
) -> bool:
    """Say whether a turn is marked `assistant_failed` beside a stored reply."""
    return marked_assistant_failed(patient) and assistant is not None


def to_json(report: Report) -> str:
    """Render the report as JSON with sorted keys, so equal reports are equal bytes."""
    return json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


def render_summary(report: Report) -> str:
    """Render the report as Markdown, in the order it should be read.

    The conditions come first, the alignment totals second and the exclusions third,
    since each changes what the metrics after them can be taken to mean.
    """
    conditions = report.conditions.model_dump()
    lines = [
        f"# Golden-set report - run {report.run_id}",
        "",
        "## Conditions",
        "",
        f"- Clock: {report.clock.isoformat()}",
        f"- Session: {report.session_id}",
        (
            f"- Corpus: live {report.corpus.live_sha256}, pinned "
            f"{report.corpus.pinned_sha256}, matched: {report.corpus.matched}"
        ),
        f"- Selection: {_selection(report.selection)}",
        (
            f"- Recorded cases: {len(report.recorded_cases)} of "
            f"{len(report.selection.case_ids)}"
        ),
        *(f"- {name}: {value}" for name, value in conditions.items()),
        "",
        "## Alignment",
        "",
        "| aligned | unaligned | excluded | labelled |",
        "|---|---|---|---|",
        (
            f"| {report.alignment.aligned} | {report.alignment.unaligned} "
            f"| {report.alignment.excluded} | {report.alignment.labelled} |"
        ),
        "",
        "## Exclusions",
        "",
        *_exclusion_lines(report.exclusions),
        "",
        "### Fixtures that would not plant",
        "",
        *_unresolvable_fixture_lines(report.unresolvable_fixtures),
        "",
        "## Metrics",
        "",
        "| metric | value | numerator / denominator | excluded |",
        "|---|---|---|---|",
        *(_metric_row(metric) for metric in _classification_metrics(report)),
        "",
        (
            "Unaligned requests beside intent accuracy: "
            f"{report.classification.unaligned_requests}"
        ),
        "",
        "### Segmentation disagreements",
        "",
        *_disagreement_lines(report.classification),
        "",
        "### Turns combined to stay within the segment cap",
        "",
        _listed(report.classification.cap_bound_cases),
        "",
        *_retrieval_lines(report.retrieval),
        "",
        *_serving_lines(report.serving),
        "",
        *_booking_lines(report.booking),
        "",
        "## Runs and attempts",
        "",
        f"- Cases needing more than one attempt: {report.cases_needing_retry}",
        (
            "- Turns marked assistant_failed that still replied: "
            f"{_turns_listed(report.assistant_failed_with_reply_turns)}"
        ),
        (
            "- Streams that broke the service's contract and then settled: "
            f"{_turns_listed(report.contract_broken_then_settled_turns)}"
        ),
        f"- Drive seconds: {report.drive_seconds:.2f}",
        f"- Score seconds: {report.score_seconds:.4f}",
        "",
        "## Not computed",
        "",
        *(f"- {name}" for name in report.not_computed),
    ]
    if not report.not_computed:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def _classification_metrics(report: Report) -> list[Metric]:
    """Return the classification metrics in the order they are listed."""
    scores = report.classification
    return [
        scores.request_count_accuracy,
        scores.intent_accuracy,
        scores.exact_segmentation_match,
    ]


def _metric_row(metric: Metric) -> str:
    """Render one metric as a table row."""
    value = metric.value
    shown = value if value == NOT_MEASURED else f"{value:.3f}"
    numerator = (
        f"{metric.numerator:.3f}"
        if isinstance(metric.numerator, float)
        else str(metric.numerator)
    )
    excluded = ", ".join(
        f"{reason.value} {count}" for reason, count in metric.excluded.counts.items()
    )
    return (
        f"| {metric.name} | {shown} | {numerator} / {metric.denominator} "
        f"| {excluded or '-'} |"
    )


_TABLE_HEADER: Final = (
    "| metric | value | numerator / denominator | excluded |",
    "|---|---|---|---|",
)


def _retrieval_lines(scores: RetrievalScores) -> list[str]:
    """Render the retrieval section: both stages, gate survival, the hit@5 statement.

    The statement follows the table, so it is read after the value it qualifies.
    """
    lines = ["## Retrieval", "", *_TABLE_HEADER]
    lines.extend(_metric_row(metric) for metric in scores.metrics())
    lines.append("")
    if scores.rerank_hit_at_5_statement is not None:
        lines.extend([f"On {RERANK_HIT_AT_5}: {scores.rerank_hit_at_5_statement}", ""])
    lines.append(
        "Unaligned labelled-answerable requests, scored at neither stage: "
        f"{scores.unaligned_requests}"
    )
    return lines


def _serving_lines(scores: ServingScores) -> list[str]:
    """Render the serving section: both shares, the requests behind them, verdicts."""
    c1, c2 = scores.unserved_answerable_share, scores.wrong_abstention_share
    causes = ", ".join(
        f"{cause.value} {count}" for cause, count in scores.unserved_by_cause.items()
    )
    return [
        "## Serving",
        "",
        *_TABLE_HEADER,
        _metric_row(c1),
        _metric_row(c2),
        "",
        SHARED_NUMERATOR_STATEMENT,
        "",
        f"Denominators: {c1.name} {c1.denominator}, {c2.name} {c2.denominator}.",
        "",
        "### Unserved answerable requests",
        "",
        f"By cause: {causes}.",
        "",
        *_unserved_lines(scores.unserved),
        "",
        "### Not permitted to answer (handed off or silenced; out of the denominator)",
        "",
        _listed(scores.not_permitted_to_answer),
        "",
        "### Wrong abstentions",
        "",
        *_abstention_lines(scores.wrong_abstentions),
        "",
        "### Degraded answers (answered_unreranked)",
        "",
        *_answer_lines(scores.degraded_answers),
        "",
        "### Answers on labelled gaps",
        "",
        *_answer_lines(scores.answers_on_labelled_gaps),
        "",
        "### Verdict distribution",
        "",
        *_TABLE_HEADER,
        *(_metric_row(metric) for metric in scores.verdict_distribution.metrics()),
    ]


def _booking_lines(scores: BookingScores) -> list[str]:
    """Render the booking section: both metrics, the statement, and what fell short.

    The per-booking-half statement follows the table, so it is read after the value it
    qualifies.
    """
    return [
        "## Booking",
        "",
        *_TABLE_HEADER,
        *(_metric_row(metric) for metric in scores.metrics()),
        "",
        scores.tool_selection_statement,
        "",
        "### Tool-selection misses",
        "",
        *_miss_lines(scores.tool_selection_misses),
        "",
        "### End-to-end failures",
        "",
        *_failure_lines(scores.task_failures),
    ]


def _miss_lines(misses: Sequence[ToolSelectionMiss]) -> list[str]:
    """Render each turn that left a labelled tool uncalled, beside what it called."""
    if not misses:
        return ["None."]
    return [
        f"- {m.case_id}: missing {', '.join(t.value for t in m.missing)}; "
        f"called {', '.join(m.called) or 'nothing'}"
        for m in misses
    ]


# How a failure line names the read it was found in.
_READ_NAMES: Final[dict[PostStateRead, str]] = {
    PostStateRead.BEFORE_REPLY: "after the first turn, before the reply",
    PostStateRead.AFTER: "after the last turn",
}


def _failure_lines(failures: Sequence[TaskFailure]) -> list[str]:
    """Render each failed read: which read it was, what was missing, what was extra."""
    if not failures:
        return ["None."]
    return [
        f"- {f.case_id}, read {_READ_NAMES[f.read]}: expected, not found: "
        f"{'; '.join(_expected(e) for e in f.unmatched_expected) or 'none'} | "
        "found, not expected: "
        f"{'; '.join(_found(a) for a in f.unaccounted_appointments) or 'none'}"
        for f in failures
    ]


def _expected(entry: AppointmentRef) -> str:
    """Describe an expected appointment in the label's own terms."""
    time = f" {entry.time}" if entry.time is not None else ""
    status = f" {entry.status.value}" if entry.status is not None else ""
    return f"{entry.practitioner} {entry.day}{time}{status}"


def _found(appointment: AppointmentState) -> str:
    """Describe an appointment as the scheduler reported it."""
    return (
        f"{appointment.practitioner_full_name} {appointment.starts_at.isoformat()} "
        f"{appointment.status.value}"
    )


def _unserved_lines(requests: Sequence[UnservedRequest]) -> list[str]:
    """Render each unserved request with its cause, and its gate for an abstention."""
    if not requests:
        return ["None."]
    return [
        f"- {r.case_id} [{r.position}] {r.cause.value}"
        + (f" at {r.gate.value}" if r.gate is not None else "")
        + f": {r.question}"
        for r in requests
    ]


def _abstention_lines(abstentions: Sequence[Abstention]) -> list[str]:
    """Render each abstention with the gate it stopped at."""
    if not abstentions:
        return ["None."]
    return [
        f"- {a.case_id} [{a.position}] at {a.gate.value}: {a.question}"
        for a in abstentions
    ]


def _answer_lines(answers: Sequence[Answer]) -> list[str]:
    """Render each listed answer with its verdict."""
    if not answers:
        return ["None."]
    return [
        f"- {a.case_id} [{a.position}] {a.verdict.value}: {a.question}" for a in answers
    ]


def _exclusion_lines(exclusions: Exclusions) -> list[str]:
    """Render the per-reason exclusion counts, sorted by reason."""
    if not exclusions.counts:
        return ["None."]
    return [
        f"- {reason.value}: {count}"
        for reason, count in sorted(exclusions.counts.items())
    ]


def _unresolvable_fixture_lines(fixtures: Sequence[UnresolvableFixture]) -> list[str]:
    """Render each unresolvable fixture with its situation and detail."""
    if not fixtures:
        return ["None."]
    return [
        f"- {f.case_id} {f.unplantable.situation.value}: {f.unplantable.detail}"
        if f.unplantable is not None
        else f"- {f.case_id}: not recorded"
        for f in fixtures
    ]


def _disagreement_lines(scores: ClassificationScores) -> list[str]:
    """Render each disagreeing case with its labelled and produced intents."""
    if not scores.disagreements:
        return ["None."]
    return [
        f"- {d.case_id}: labelled {[i.value for i in d.labelled]}, "
        f"produced {[i.value for i in d.produced]}"
        for d in scores.disagreements
    ]


def _selection(selection: Selection) -> str:
    """Describe how the run's cases were chosen."""
    chosen = f"{selection.kind.value}"
    if selection.family is not None:
        chosen += f" {selection.family}"
    return f"{chosen} ({len(selection.case_ids)} cases)"


def _turns_listed(turns_by_case: dict[str, list[RecordedTurn]]) -> str:
    """Render each case id with the turns it names, or say there are none."""
    return _listed(
        [
            f"{case_id} ({', '.join(turn.value for turn in turns)})"
            for case_id, turns in turns_by_case.items()
        ]
    )


def _listed(case_ids: Sequence[str]) -> str:
    """Render a list of case ids, or say there are none."""
    return ", ".join(case_ids) if case_ids else "none"
