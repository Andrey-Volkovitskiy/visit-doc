"""Booking metrics: did the booking half call what it had to, and did the booking land.

Two numbers, over the cases carrying a booking request that no case-scoped reason set
aside.

- **Tool-selection correctness** (D1) is scored once per turn, not per request: the
  booking loop is handed every booking request of a turn at once, and a
  `booking.tool_called` event belongs to the loop rather than to one request. A turn
  scores when it called every tool in the union of its booking requests' labelled
  tools. A tool no label names is not a miss. For a case whose scripted reply was
  posted, the calls of both its turns count together, as one booking half. A
  `booking.roster_read` event counts as a `list_practitioners` call: the booking node
  makes that call itself before the model's first request and puts the roster in the
  prompt, so a turn that answers "who works here" from it has called the tool without
  the model calling it again. A `booking.roster_unread` does not count - the read
  failed, and the prompt said the roster was unknown.
- **End-to-end task success** (D2) compares the patient's appointments after the case's
  last turn with the fixture's expectation, under a perfect one-to-one matching: every
  expected entry matches exactly one appointment and no appointment is left over. A
  scripted reply is optional to the case: the driver posts it only when the first turn
  did not already leave the expected appointments, so the last turn is the first one
  when it did and the reply otherwise. What the first turn did on the way is not
  scored. A failure names both halves - what was expected and not found, and what was
  found and not expected - since too little and too much are opposite defects.

A case whose fixture could not be planted, or whose outcome is unknown, is excluded from
both, with its reason, and never counted as a failure.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Final

from pydantic import BaseModel, ConfigDict

from golden_harness.cases import AppointmentRef, BookingTool, Case
from golden_harness.record import AppointmentState, CaseRun, ExclusionReason
from golden_harness.scoring.metric import CASE_SCOPED_REASONS, Exclusions, Metric

TOOL_SELECTION_CORRECTNESS: Final = "tool_selection_correctness"
END_TO_END_TASK_SUCCESS: Final = "end_to_end_task_success"
BOOKING_METRICS: Final = (TOOL_SELECTION_CORRECTNESS, END_TO_END_TASK_SUCCESS)

_TOOL_CALLED_EVENT: Final = "booking.tool_called"
# The booking node's own `list_practitioners` call, logged only when it returned a
# roster. Its name is a data contract with `chat.agent.handle_booking._read_roster`.
_ROSTER_READ_EVENT: Final = "booking.roster_read"
_ROSTER_READ_TOOL: Final = BookingTool.LIST_PRACTITIONERS.value

TOOL_SELECTION_STATEMENT: Final = (
    "tool_selection_correctness is scored once per turn's booking half, against the "
    "union of the tools its booking requests are labelled with. The booking loop is "
    "handed all of a turn's booking requests at once, and the log attributes a tool "
    "call to the loop rather than to one request, so no per-request number is "
    "published. A tool called that no label names is not a miss. For a case whose "
    "scripted reply was posted, the tools called in both of its turns count together, "
    "as one booking half. The reply is posted only when the first turn did not already "
    "leave the expected appointments, so a case the loop finished in one turn is "
    "scored on that turn's calls alone. The roster the booking node reads into its "
    "prompt before the model's first request counts as a list_practitioners call; a "
    "roster read that failed does not."
)


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
    """A fixture case whose appointments after its last turn did not match `expect`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
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
        events, or a reply turn or skipped reply its label carries no reply for.
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

        expect, appointments = _last_read(case, case_run)
        matching = match_post_state(expect, appointments, clock)
        if matching.matched:
            task_successes += 1
        else:
            failures.append(
                TaskFailure(
                    case_id=case.id,
                    unmatched_expected=matching.unmatched_expected,
                    unaccounted_appointments=matching.unaccounted_appointments,
                )
            )

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


def _last_read(
    case: Case, case_run: CaseRun
) -> tuple[list[AppointmentRef], list[AppointmentState]]:
    """Return a fixture case's expectation beside its appointments after its last turn.

    Returns: the fixture's expected entries, and the appointments read after the case's
        last turn.

    Raises: ValueError when the case has no fixture or no post-state, or when a posted
        or skipped reply is recorded for a label that carries no reply.
    """
    fixture = case.scheduling
    if fixture is None or case_run.scheduling_after is None:
        raise ValueError(f"{case.id} has no fixture or no recorded post-state")
    replied = case_run.reply_turn is not None or case_run.reply_skipped
    if replied and fixture.reply is None:
        raise ValueError(f"{case.id} recorded a reply its label carries no reply for")
    return fixture.expect, case_run.scheduling_after


def _tool_selection_miss(case: Case, case_run: CaseRun) -> ToolSelectionMiss | None:
    """Return the booking half's tool-selection miss, or None when it called every tool.

    The booking half of a case whose reply was posted is both turns: their calls are
    read together, in first-call order. A successful roster read is one of those calls,
    under `list_practitioners`.

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
        if event.get("event") == _ROSTER_READ_EVENT:
            name: object = _ROSTER_READ_TOOL
        elif event.get("event") == _TOOL_CALLED_EVENT:
            name = event.get("tool_name")
        else:
            continue
        if isinstance(name, str) and name not in called:
            called.append(name)
    missing = [tool for tool in labelled if tool.value not in called]
    if not missing:
        return None
    return ToolSelectionMiss(
        case_id=case.id, labelled=labelled, called=called, missing=missing
    )
