"""What a run stores: `run.json` and one `cases/<id>.json` per driven case.

Nothing here is a score. The record holds what happened - the terminal event, the two
stored messages, the produced segmentation, the turn's log events, the scheduler state -
and every judgement is computed from it at scoring time, so a metric definition can
change and be re-applied to every run already on disk.

The service's own enums and `RequestOutcome` are what the record parses through, so a
value the service does not know, or an outcome violating its own invariants, fails when
the record is read rather than being bucketed into a metric.
"""

import os
import tempfile
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from chat.domain.models import AttentionMark
from chat.domain.schemas import IntentLabel, RequestOutcome
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictInt,
    field_validator,
    model_validator,
)
from shared_models.scheduling import AppointmentStatus

from golden_harness.cases import Selection

_RUN_FILE = "run.json"
_CASES_DIR = "cases"


class ExclusionReason(StrEnum):
    """Why a case, or one request of it, is set aside by a metric - one per situation.

    The first seven are decided per turn and stored on the case; the last four are
    decided per request, from the turn's events, at scoring time and are never stored.
    """

    RUN_ERROR = "run_error"
    SILENCED_TURN = "silenced_turn"
    HANDED_OFF_TURN = "handed_off_turn"
    CANCELLED_TURN = "cancelled_turn"
    MISSING_LOG_SLICE = "missing_log_slice"
    UNRESOLVABLE_FIXTURE = "unresolvable_fixture"
    OUTCOME_UNKNOWN = "outcome_unknown"
    RERANKER_UNAVAILABLE = "reranker_unavailable"
    NOT_REACHED_RERANKER = "not_reached_reranker"
    NO_SEARCH = "no_search"
    NOT_ROUTED_TO_FAQ = "not_routed_to_faq"


# The reasons a case file may carry: the ones decided by what the turn did as a whole.
TURN_LEVEL_REASONS: frozenset[ExclusionReason] = frozenset(
    {
        ExclusionReason.RUN_ERROR,
        ExclusionReason.SILENCED_TURN,
        ExclusionReason.HANDED_OFF_TURN,
        ExclusionReason.CANCELLED_TURN,
        ExclusionReason.MISSING_LOG_SLICE,
        ExclusionReason.UNRESOLVABLE_FIXTURE,
        ExclusionReason.OUTCOME_UNKNOWN,
    }
)


class TerminalKind(StrEnum):
    """How a turn's response stream ended.

    `error` is a stream that ended without any of the service's three terminal events.
    """

    DONE = "done"
    SILENT = "silent"
    CANCELLED = "cancelled"
    ERROR = "error"


class Terminal(BaseModel):
    """The event a turn's stream ended with, and that event's own payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: TerminalKind
    payload: dict[str, JsonValue] = Field(default_factory=dict)


class StoredMessage(BaseModel):
    """A message as the chat service stored it.

    `attention_mark` is only ever set on the patient message and `request_outcomes` only
    on the assistant reply - None on a reply means no FAQ half ran for the turn.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    content: str
    attention_mark: AttentionMark | None = None
    request_outcomes: list[RequestOutcome] | None = None


def marked_assistant_failed(patient_message: StoredMessage | None) -> bool:
    """Whether a turn's patient message was stored marked `assistant_failed`."""
    return (
        patient_message is not None
        and patient_message.attention_mark is AttentionMark.ASSISTANT_FAILED
    )


def turn_settled(
    patient_message: StoredMessage | None, assistant_message: StoredMessage | None
) -> bool:
    """Whether what a turn stored shows it ended (FR-041c).

    Ended is a stored reply, or the turn's patient message marked `assistant_failed` -
    the one rule the driver waits on, judges an attempt by, and the report lists
    settled turns by.
    """
    return assistant_message is not None or marked_assistant_failed(patient_message)


class ProducedSegment(BaseModel):
    """One request as the classifier produced it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    position: int
    intent: IntentLabel
    text: str


class ProducedSegmentation(BaseModel):
    """The turn's segmentation, read from its `intent.classified` event.

    `cap_bound` is the classifier's own statement that it combined requests to stay
    within the segment cap, never inferred from how many segments there are.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    segments: list[ProducedSegment] = Field(min_length=1)
    cap_bound: bool

    @model_validator(mode="after")
    def _positions_run_from_zero(self) -> "ProducedSegmentation":
        """Refuse segments whose positions are not 0, 1, 2, ... in order."""
        positions = [segment.position for segment in self.segments]
        if positions != list(range(len(self.segments))):
            raise ValueError(f"segment positions must be 0..n-1 in order: {positions}")
        return self


class AppointmentState(BaseModel):
    """One of the patient's appointments as the scheduler reported it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    practitioner_full_name: str
    starts_at: datetime
    status: AppointmentStatus


class UnplantableSituation(StrEnum):
    """Which situation stopped a case's fixture from planting - one per situation.

    A label problem (`not_on_roster`, a `booking_refused` the label caused) and a
    scheduler that could not be reached or read (`roster_unreadable`,
    `booking_unanswered`, `booking_unreadable`) call for different fixes, so neither
    is folded into the other.
    """

    NOT_ON_ROSTER = "not_on_roster"
    ROSTER_UNREADABLE = "roster_unreadable"
    BOOKING_REFUSED = "booking_refused"
    BOOKING_UNANSWERED = "booking_unanswered"
    BOOKING_UNREADABLE = "booking_unreadable"
    NO_PATIENT = "no_patient"


class Unplantable(BaseModel):
    """Why a fixture's preconditions could not all be planted.

    `detail` is the account for a reader, naming the precondition and any status code.
    `failure_reason` is the scheduler's own `BookingFailureReason` name, and is set
    exactly when a booking was refused; `scheduler_message` is the detail the scheduler
    gave with that refusal, None when it gave none. No other situation carries either.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    situation: UnplantableSituation
    detail: str
    failure_reason: str | None = None
    scheduler_message: str | None = None

    @model_validator(mode="after")
    def _only_a_refusal_carries_the_schedulers_answer(self) -> "Unplantable":
        """Refuse a refusal with no reason, or a scheduler answer on anything else."""
        refused = self.situation is UnplantableSituation.BOOKING_REFUSED
        if refused and self.failure_reason is None:
            raise ValueError("a refused booking must carry its failure_reason")
        if not refused:
            for name in ("failure_reason", "scheduler_message"):
                if getattr(self, name) is not None:
                    raise ValueError(
                        f"only a refused booking carries a {name}, "
                        f"not {self.situation.value}"
                    )
        return self


def _no_terminal_beside_a_broken_stream(
    terminal: Terminal | None, stream_broke_contract: bool
) -> None:
    """Refuse a terminal recorded for a stream that broke the service's contract.

    Raises: ValueError when both are set - such a stream was not read to an ending.
    """
    if stream_broke_contract and terminal is not None:
        raise ValueError(
            "a stream that broke the service's contract records no terminal, "
            f"not {terminal.kind.value}"
        )


class ReplyTurn(BaseModel):
    """The scripted reply's turn, posted in the case's chat once the first turn replied.

    Each field follows the rule of the first turn's own field on `CaseRun`: `terminal`
    is None when no response stream was read to its end, `stream_broke_contract` is
    True exactly when the stream began and then broke the service's contract,
    `patient_message` is None when the service stored no patient message for the reply
    or the thread was not read back, `assistant_message` when it stored no answer to it
    or the thread was not read back, and `events` when no log slice was selected for
    the reply.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    terminal: Terminal | None = None
    stream_broke_contract: bool = False
    patient_message: StoredMessage | None = None
    assistant_message: StoredMessage | None = None
    events: list[dict[str, JsonValue]] | None = None

    @model_validator(mode="after")
    def _a_broken_stream_has_no_terminal(self) -> "ReplyTurn":
        """Refuse a terminal beside a stream that broke the service's contract."""
        _no_terminal_beside_a_broken_stream(self.terminal, self.stream_broke_contract)
        return self


class CorpusRecord(BaseModel):
    """The corpus check a run began with: both digests, and whether they matched."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    live_sha256: str
    pinned_sha256: str
    matched: bool


class RunConditions(BaseModel):
    """The settings the chat service stated in its `service.configured` event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    classification_model: str
    generation_model: str
    embedding_model: str
    rerank_model: str
    retrieval_pool_size: int
    similarity_floor: float
    similarity_cap: int
    rerank_floor: float
    rerank_cap: int
    max_segments: int
    context_turns: int

    @classmethod
    def from_event(cls, event: dict[str, JsonValue]) -> "RunConditions":
        """Build the conditions from a `service.configured` event's fields.

        The event's envelope (`event`, `level`, `timestamp`) is not a condition and is
        left out; every condition field must be present.

        Raises: pydantic.ValidationError when a condition field is missing or mistyped.
        """
        return cls.model_validate(
            {name: event[name] for name in cls.model_fields if name in event}
        )


class Run(BaseModel):
    """`run.json`: the conditions a run was measured under, and the cases recorded.

    `started_at` and `finished_at` are the host's wall clock, for orientation only;
    `drive_seconds` - the sum of the cases' own elapsed times - is the run's duration.
    `clock` is the local time every turn was sent as `local_now`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    started_at: datetime
    finished_at: datetime | None = None
    drive_seconds: float = Field(default=0.0, ge=0.0)
    clock: datetime
    session_id: str
    corpus: CorpusRecord
    selection: Selection
    entry_ids: dict[str, StrictInt] = Field(min_length=1)
    conditions: RunConditions
    labels: dict[str, str]
    cases: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _labels_and_cases_follow_the_selection(self) -> "Run":
        """Refuse digests that are not one per selected case, or an unselected case."""
        selected = set(self.selection.case_ids)
        if set(self.labels) != selected:
            raise ValueError("labels must carry exactly one digest per selected case")
        unselected = sorted(set(self.cases) - selected)
        if unselected:
            raise ValueError(f"recorded cases the run did not select: {unselected}")
        return self


class CaseRun(BaseModel):
    """`cases/<id>.json`: everything one driven case left behind, written once, whole.

    `patient_id` is None when the chat was provisioned with no scheduler patient.
    `terminal` is None when no response stream was read to its end for the measured
    attempt - none was received, or the one received broke the service's contract -
    and `stream_broke_contract` tells those two apart: it is True exactly when the
    attempt's stream began and then broke the contract (FR-041d), False when the
    stream was read to its end, broke off or timed out, or none was received.
    `patient_message` is None when the service stored no patient message for it, and
    `assistant_message` when it stored no reply; both are None as well on an
    `outcome_unknown` case whose thread was not read back. `events` is None when no log
    slice was selected for the turn, and `segments` when the selected slice holds no
    classification. `reply_turn` is the scripted reply's turn, None when no reply was
    posted that may have reached the pipeline - the case's fixture carries none, the
    first turn ended without a reply of its own, or the reply was provably never sent.
    `scheduling_before_reply` is the patient's appointments read after the first turn
    and before the reply was posted, None when that read was not taken.
    `scheduling_after` is None when the patient's appointments were not read after the
    case's last turn. `excluded` carries only a turn-level reason; request-level ones
    are derived when scoring. `unplantable` says why the fixture would not plant, and is
    set only on a case excluded as `unresolvable_fixture` - None there on a case
    recorded before the harness stored it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    chat_id: str
    patient_id: str | None = None
    attempts: int = Field(ge=1)
    elapsed_seconds: float = Field(ge=0.0)
    terminal: Terminal | None = None
    stream_broke_contract: bool = False
    patient_message: StoredMessage | None = None
    assistant_message: StoredMessage | None = None
    segments: ProducedSegmentation | None = None
    events: list[dict[str, JsonValue]] | None = None
    reply_turn: ReplyTurn | None = None
    scheduling_before_reply: list[AppointmentState] | None = None
    scheduling_after: list[AppointmentState] | None = None
    cancelled_after: list[AppointmentState] = Field(default_factory=list)
    excluded: ExclusionReason | None = None
    unplantable: Unplantable | None = None

    @field_validator("excluded")
    @classmethod
    def _only_a_turn_level_reason(
        cls, value: ExclusionReason | None
    ) -> ExclusionReason | None:
        """Refuse a request-level reason, which scoring derives and nothing stores."""
        if value is not None and value not in TURN_LEVEL_REASONS:
            raise ValueError(f"{value.value} is decided per request and is not stored")
        return value

    @model_validator(mode="after")
    def _a_broken_stream_has_no_terminal(self) -> "CaseRun":
        """Refuse a terminal beside a stream that broke the service's contract."""
        _no_terminal_beside_a_broken_stream(self.terminal, self.stream_broke_contract)
        return self

    @model_validator(mode="after")
    def _unplantable_only_on_an_unresolvable_fixture(self) -> "CaseRun":
        """Refuse an unplantable detail on a case its fixture did not exclude."""
        unresolvable = self.excluded is ExclusionReason.UNRESOLVABLE_FIXTURE
        if self.unplantable is not None and not unresolvable:
            raise ValueError(
                "an unplantable detail belongs only to a case excluded as "
                "unresolvable_fixture"
            )
        return self


def write_run(run_dir: Path, run: Run) -> None:
    """Write `run.json` into `run_dir` atomically, replacing any previous one."""
    run_dir.mkdir(parents=True, exist_ok=True)
    write_atomically(run_dir / _RUN_FILE, run.model_dump_json(indent=2))


def read_run(run_dir: Path) -> Run:
    """Read and validate `run.json` from `run_dir`.

    Raises: pydantic.ValidationError when the file does not hold a valid run.
    """
    return Run.model_validate_json((run_dir / _RUN_FILE).read_bytes())


def write_case(run_dir: Path, case_run: CaseRun) -> None:
    """Write `cases/<case_id>.json` into `run_dir` atomically."""
    cases_dir = run_dir / _CASES_DIR
    cases_dir.mkdir(parents=True, exist_ok=True)
    write_atomically(
        cases_dir / f"{case_run.case_id}.json", case_run.model_dump_json(indent=2)
    )


def read_case(run_dir: Path, case_id: str) -> CaseRun:
    """Read and validate one case file from `run_dir`.

    Raises: pydantic.ValidationError when the file does not hold a valid case run.
    """
    return CaseRun.model_validate_json(
        (run_dir / _CASES_DIR / f"{case_id}.json").read_bytes()
    )


def recorded_case_ids(run_dir: Path) -> list[str]:
    """Return the ids of the cases `run_dir` holds a case file for, sorted.

    A directory listing and nothing more: a file is not opened, so an unreadable one is
    still listed, and fails when it is read.
    """
    cases_dir = run_dir / _CASES_DIR
    if not cases_dir.is_dir():
        return []
    return sorted(
        path.stem
        for path in cases_dir.iterdir()
        if path.is_file() and path.suffix == ".json" and not path.name.startswith(".")
    )


def write_atomically(path: Path, text: str) -> None:
    """Replace `path` with `text` so a reader sees the old file or the new, never half.

    The text goes to a hidden temporary file beside `path`, is flushed to disk, and is
    renamed over `path`; a failure anywhere removes the temporary file and re-raises.
    """
    descriptor, name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
