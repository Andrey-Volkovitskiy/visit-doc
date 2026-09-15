"""Booking metrics: did the booking half call what it had to, and did the booking land.

Two numbers, over the cases carrying a booking request that no case-scoped reason set
aside.

- **Tool-selection correctness** (D1) is scored once per turn, not per request: the
  booking loop is handed every booking request of a turn at once, and a
  `booking.tool_called` event belongs to the loop rather than to one request. A turn
  scores when it called every tool in the union of its booking requests' labelled
  tools. A tool no label names is not a miss. For a case with a scripted reply, the
  calls of both its turns count together, as one booking half.
- **End-to-end task success** (D2) compares the patient's appointments after the case's
  last turn with the fixture's expectation, under a perfect one-to-one matching: every
  expected entry matches exactly one appointment and no appointment is left over. A
  case with a scripted reply is read twice: after its first turn its appointments must
  still match its preconditions restated as standing, under the same matching, and
  after the reply they must match the expectation. A failure names the read it was
  found in and both halves - what was expected and not found, and what was found and
  not expected - since too little and too much are opposite defects.

A case whose fixture could not be planted, or whose outcome is unknown, is excluded from
both, with its reason, and never counted as a failure.
"""

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict
from shared_models.scheduling import AppointmentStatus

from golden_harness.cases import AppointmentRef, BookingTool, Case
from golden_harness.record import AppointmentState, CaseRun, ExclusionReason
from golden_harness.scoring.metric import CASE_SCOPED_REASONS, Exclusions, Metric

TOOL_SELECTION_CORRECTNESS: Final = "tool_selection_correctness"
END_TO_END_TASK_SUCCESS: Final = "end_to_end_task_success"
BOOKING_METRICS: Final = (TOOL_SELECTION_CORRECTNESS, END_TO_END_TASK_SUCCESS)

_TOOL_CALLED_EVENT: Final = "booking.tool_called"

TOOL_SELECTION_STATEMENT: Final = (
    "tool_selection_correctness is scored once per turn's booking half, against the "
    "union of the tools its booking requests are labelled with. The booking loop is "
    "handed all of a turn's booking requests at once, and the log attributes a tool "
    "call to the loop rather than to one request, so no per-request number is "
    "published. A tool called that no label names is not a miss. For a case with a "
    "scripted reply, the tools called in both of its turns count together, as one "
    "booking half: the loop is specified to ask in the first turn and act in the "
    "second."
)


class PostStateRead(StrEnum):
    """Which read of a fixture case's appointments a failure was found in.

    Named after the record field the read is stored in: `scheduling_before_reply` is
    taken after a reply case's first turn, `scheduling_after` after a case's last turn.
    """

    BEFORE_REPLY = "scheduling_before_reply"
    AFTER = "scheduling_after"


class ToolSelectionMiss(BaseModel):
    """A turn whose booking half did not call every labelled tool.

    `labelled` is the union of the turn's booking requests' tools in label order,
    `called` every tool name the booking half's events record in first-call order - both
    turns' events for a case whose reply was posted - and
    `missing` the labelled tools never called.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    labelled: list[BookingTool]
    called: list[str]
    missing: list[BookingTool]


class PostStateMatching(BaseModel):
    """How a post-state compares with an expectation under a maximum matching.

    `unmatched_expected` are the expected entries no appointment was matched to, and
    `unaccounted_appointments` the appointments matched to no entry, each in its
    original order.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    unmatched_expected: list[AppointmentRef]
    unaccounted_appointments: list[AppointmentState]

    @property
    def matched(self) -> bool:
        """Whether every entry and every appointment was matched."""
        return not self.unmatched_expected and not self.unaccounted_appointments


class TaskFailure(BaseModel):
    """One read of a fixture case whose appointments did not match what it expects.

    `read` names the read. The expectation of the read before a reply is the fixture's
    preconditions restated as standing, so `unmatched_expected` holds those entries
    there. A reply case whose two reads both failed has one failure per read.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    read: PostStateRead
    unmatched_expected: list[AppointmentRef]
    unaccounted_appointments: list[AppointmentState]


class BookingScores(BaseModel):
    """The booking metrics over a run, and the cases behind their shortfalls."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_selection_correctness: Metric
    tool_selection_misses: list[ToolSelectionMiss]
    tool_selection_statement: str
    end_to_end_task_success: Metric
    task_failures: list[TaskFailure]

    def metrics(self) -> list[Metric]:
        """Return both booking metrics, in the order a report lists them."""
        return [self.tool_selection_correctness, self.end_to_end_task_success]


def score_booking(
    cases: Sequence[Case], case_runs: Sequence[CaseRun], *, clock: datetime
) -> BookingScores:
    """Score a run's booking cases.

    Args:
        clock: the run's clock, whose date every fixture's day offset counts from.

    Raises: ValueError when a recorded case has no label, or when a booking case no
        exclusion set aside has no log events, no post-state, a reply turn with no log
        events, a reply turn and a read before it not recorded together, or a reply
        turn its label carries no reply for.
    """
    labels = {case.id: case for case in cases}
    exclusions: list[ExclusionReason] = []
    tool_hits = task_successes = scored = 0
    misses: list[ToolSelectionMiss] = []
    failures: list[TaskFailure] = []

    for case_run in case_runs:
        case = labels.get(case_run.case_id)
        if case is None:
            raise ValueError(f"{case_run.case_id} is recorded but not labelled")
        if not case.has_booking_request:
            continue
        reason = case_run.excluded
        if reason is not None and reason in CASE_SCOPED_REASONS:
            exclusions.append(reason)
            continue
        scored += 1

        miss = _tool_selection_miss(case, case_run)
        if miss is None:
            tool_hits += 1
        else:
            misses.append(miss)

        found: list[TaskFailure] = []
        for read, expect, appointments in _reads(case, case_run):
            matching = match_post_state(expect, appointments, clock)
            if not matching.matched:
                found.append(
                    TaskFailure(
                        case_id=case.id,
                        read=read,
                        unmatched_expected=matching.unmatched_expected,
                        unaccounted_appointments=matching.unaccounted_appointments,
                    )
                )
        if found:
            failures.extend(found)
        else:
            task_successes += 1

    excluded = Exclusions.tally(exclusions)
    return BookingScores(
        tool_selection_correctness=Metric(
            name=TOOL_SELECTION_CORRECTNESS,
            numerator=tool_hits,
            denominator=scored,
            excluded=excluded,
        ),
        tool_selection_misses=misses,
        tool_selection_statement=TOOL_SELECTION_STATEMENT,
        end_to_end_task_success=Metric(
            name=END_TO_END_TASK_SUCCESS,
            numerator=task_successes,
            denominator=scored,
            excluded=excluded,
        ),
        task_failures=failures,
    )


def match_post_state(
    expect: Sequence[AppointmentRef],
    appointments: Sequence[AppointmentState],
    clock: datetime,
) -> PostStateMatching:
    """Match expected entries to appointments one-to-one, as many as can be matched.

    An entry matches an appointment with the same practitioner and status, on the date
    the entry names on `clock`, and - when the entry names a time - starting then.

    The matching is a maximum bipartite matching found by augmenting paths, so whether
    the post-state matched never depends on the order of the entries or appointments.
    """
    candidates = [
        [i for i, a in enumerate(appointments) if _matches(entry, a, clock)]
        for entry in expect
    ]
    owner: dict[int, int] = {}

    def augment(entry: int, seen: set[int]) -> bool:
        for appointment in candidates[entry]:
            if appointment in seen:
                continue
            seen.add(appointment)
            holder = owner.get(appointment)
            if holder is None or augment(holder, seen):
                owner[appointment] = entry
                return True
        return False

    for entry in range(len(expect)):
        augment(entry, set())

    matched_entries = set(owner.values())
    return PostStateMatching(
        unmatched_expected=[
            ref for i, ref in enumerate(expect) if i not in matched_entries
        ],
        unaccounted_appointments=[
            a for i, a in enumerate(appointments) if i not in owner
        ],
    )


def _matches(
    entry: AppointmentRef, appointment: AppointmentState, clock: datetime
) -> bool:
    """Whether one appointment satisfies every field one expected entry states."""
    start_time = entry.start_time()
    return (
        appointment.practitioner_full_name == entry.practitioner
        and appointment.status == entry.status
        and appointment.starts_at.date() == entry.date_on(clock)
        and (start_time is None or appointment.starts_at.time() == start_time)
    )


def _reads(
    case: Case, case_run: CaseRun
) -> list[tuple[PostStateRead, list[AppointmentRef], list[AppointmentState]]]:
    """Return each read of a fixture case beside what it must match, in read order.

    A case whose reply was posted is read after its first turn, against its
    preconditions restated as standing, and after the reply against its expectation;
    any other case once, after its last turn, against its expectation.

    Raises: ValueError when the case has no fixture or no post-state, when a reply turn
        and the read before it are not recorded together, or when a reply turn is
        recorded for a label that carries no reply.
    """
    fixture = case.scheduling
    if fixture is None or case_run.scheduling_after is None:
        raise ValueError(f"{case.id} has no fixture or no recorded post-state")
    reads = [(PostStateRead.AFTER, fixture.expect, case_run.scheduling_after)]
    if case_run.reply_turn is None and case_run.scheduling_before_reply is None:
        return reads
    if fixture.reply is None:
        raise ValueError(f"{case.id} recorded a reply its label carries no reply for")
    before = case_run.scheduling_before_reply
    if case_run.reply_turn is None or before is None:
        raise ValueError(
            f"{case.id} recorded a reply turn and the read before it, one without the "
            "other"
        )
    standing = [
        entry.model_copy(update={"status": AppointmentStatus.STANDING})
        for entry in fixture.given
    ]
    return [(PostStateRead.BEFORE_REPLY, standing, before), *reads]


def _tool_selection_miss(case: Case, case_run: CaseRun) -> ToolSelectionMiss | None:
    """Return the booking half's tool-selection miss, or None when it called every tool.

    The booking half of a case whose reply was posted is both turns: their calls are
    read together, in first-call order.

    Raises: ValueError when the turn, or a posted reply's turn, has no log events.
    """
    if case_run.events is None:
        raise ValueError(f"{case.id} has no log events and no case-scoped exclusion")
    turns = [case_run.events]
    if case_run.reply_turn is not None:
        if case_run.reply_turn.events is None:
            raise ValueError(
                f"{case.id} has no log events for its reply turn and no case-scoped "
                "exclusion"
            )
        turns.append(case_run.reply_turn.events)
    labelled: list[BookingTool] = []
    for request in case.requests:
        for tool in request.tools or []:
            if tool not in labelled:
                labelled.append(tool)
    called: list[str] = []
    for event in (event for events in turns for event in events):
        name = event.get("tool_name")
        is_call = event.get("event") == _TOOL_CALLED_EVENT and isinstance(name, str)
        if is_call and name not in called:
            called.append(str(name))
    missing = [tool for tool in labelled if tool.value not in called]
    if not missing:
        return None
    return ToolSelectionMiss(
        case_id=case.id, labelled=labelled, called=called, missing=missing
    )
