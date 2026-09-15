"""Driving a run: every selected case posted as a full turn, one at a time, recorded.

A run opens one session, verifies its corpus against the pin, and writes `run.json`
before any turn is posted - so a run that cannot be a measurement of the labels stops
having spent no turn. Each case then gets a fresh chat, its history, one posted turn, a
read of what the turn stored, and the turn's own slice of the log; the case file is
written whole before the next case begins.

An attempt is repeated only when it measured nothing, in a fresh chat each time, and at
most `MAX_ATTEMPTS` times. A turn that completed and failed is a result, never retried.
A request the service refused as malformed is the harness's own fault and stops the run.

A case carrying a scheduling fixture has its preconditions planted in its chat's patient
before the turn is posted, and its patient's appointments read once the turn has ended.
A fixture carrying a scripted reply has them read once the first turn has ended with a
reply of its own, then the reply posted as a second turn in the same chat, and read
again once that has ended; an attempt is the whole exchange, so a retry drives both
turns again in a fresh chat. Every case whose chat has a patient then has that patient's
standing appointments cancelled before its file is written, so no case's appointments
stand in a later case's way. A cleanup that cannot complete stops the run once the case
is written. Each stop named in `_drive_case` that comes once a chat's patient has been
read releases that patient first; for a fixture case whose turn was posted before its
thread could not be read back, or its logged segmentation broke the record's shape, the
case is also written as outcome unknown before the run stops, so a resumed run does not
post it again.

A turn whose answer did not arrive whole - its stream broke off, timed out or broke the
service's contract - is polled every `SETTLE_INTERVAL_SECONDS`, within
`SETTLE_TIMEOUT_SECONDS`, until its chat holds a reply to it or its patient message is
marked `assistant_failed`; only then is its patient's calendar read and released, and
the turn is judged as a completed one - its hand-off read from its own `turn.completed`,
since it delivered no terminal event to say so. One that never settles is written as
outcome unknown with its patient unreleased, and stops the run. Every poll checks the
log for a restart under other settings, and stops the run on the poll that sees one.

Resuming reads `run.json` back, refuses to continue under changed labels, settings or
corpus, releases every recorded case's patient and every other patient of the session's
chats, and drives only the cases with no case file yet.
"""

import asyncio
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, NoReturn, Protocol

import httpx
from chat.agent.compose_answer import HANDED_OFF_OUTCOME
from chat.core.config import Settings
from chat.domain.schemas import AnswerSource, FaqEntry, IntentLabel
from pydantic import ValidationError
from shared_db import create_engine, create_session_factory
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from ulid import ULID

from golden_harness.cases import (
    AppointmentRef,
    Case,
    HistoryEntry,
    LabelError,
    Selection,
    label_digests,
    validate_plantable,
)
from golden_harness.corpus import CorpusCheck, CorpusPin, map_entry_ids, verify
from golden_harness.driver import history as planting
from golden_harness.driver import scheduling, session, turn
from golden_harness.driver.logslice import (
    LogEvent,
    SliceMissing,
    log_offset,
    preceding_bytes,
    produced_segmentation,
    read_slice,
    require_json_log,
    restarts_since,
    select_turn,
    service_conditions,
)
from golden_harness.driver.scheduling import CleanupFailedError, SchedulingCallError
from golden_harness.driver.session import (
    ChatIdentity,
    ChatNotFoundError,
    OpenedSession,
)
from golden_harness.driver.turn import (
    AttemptClass,
    ThreadRead,
    ThreadReadError,
    TurnProtocolError,
    TurnRefused,
    TurnResult,
    TurnSent,
    classify_attempt,
)
from golden_harness.record import (
    AppointmentState,
    CaseRun,
    CorpusRecord,
    ExclusionReason,
    ProducedSegmentation,
    ReplyTurn,
    Run,
    RunConditions,
    Terminal,
    TerminalKind,
    Unplantable,
    UnplantableSituation,
    marked_assistant_failed,
    read_case,
    read_run,
    recorded_case_ids,
    turn_settled,
    write_case,
    write_run,
)

# A Monday at 08:00, so the whole working week lies ahead of every turn.
DEFAULT_CLOCK: Final = datetime(2026, 3, 2, 8, 0, 0)
MAX_ATTEMPTS: Final = 3

# How long a turn's stream may stay silent: a composed reply streams nothing until it is
# written, and a booking loop may make several tool calls first.
_READ_TIMEOUT_SECONDS: Final = 180.0
_CONNECT_TIMEOUT_SECONDS: Final = 10.0

# How long a turn whose answer did not arrive whole is waited for to settle, and how
# often its chat is polled meanwhile - both as FR-041c states them. The chat service
# states no deadline for a turn, so the bound is a decision rather than a derived value.
SETTLE_TIMEOUT_SECONDS: Final = 60.0
SETTLE_INTERVAL_SECONDS: Final = 5.0

# The event a turn's shape is logged in. The shape a hand-off is logged as is the chat
# service's own public `HANDED_OFF_OUTCOME`, so the two cannot drift apart (FR-041d).
_COMPLETED_EVENT: Final = "turn.completed"


@dataclass(frozen=True)
class SettleWait:
    """How an unanswered turn is waited for: the bound, the poll interval, the sleep.

    The bound is measured on the run's `timer`; `sleep` is what waits between polls.
    """

    timeout_seconds: float = SETTLE_TIMEOUT_SECONDS
    interval_seconds: float = SETTLE_INTERVAL_SECONDS
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep


DEFAULT_SETTLE: Final = SettleWait()


class RunStoppedError(RuntimeError):
    """The run cannot go on as a measurement of the labels it was started under."""


class CorpusMismatchError(RunStoppedError):
    """The session's live corpus is not the pinned corpus."""

    def __init__(self, check: CorpusCheck) -> None:
        """Name the pinned entries whose text moved, and the live ones nobody pinned."""
        super().__init__(
            f"the live corpus ({check.live_sha256}) is not the pinned corpus "
            f"({check.pinned_sha256}); moved entries: {check.moved_entry_ids}, "
            f"unpinned live entries: {check.unpinned_live_entry_ids}"
        )
        self.check = check


class EntryIdsChangedError(RunStoppedError):
    """The pinned texts now map to other entry ids than the run recorded."""


class HarnessFaultError(RunStoppedError):
    """The service refused a turn the harness got wrong; nothing was measured."""

    def __init__(self, case_id: str, status_code: int, body: str) -> None:
        """Carry the refused case, the status and the service's own body."""
        super().__init__(
            f"{case_id}: the service refused the turn with {status_code}: {body}"
        )
        self.case_id = case_id
        self.status_code = status_code
        self.body = body


class ConditionsChangedError(RunStoppedError):
    """The service now states other settings than the run was measured under."""


class LabelsChangedError(RunStoppedError):
    """A selected case's scored fields no longer match the digest the run recorded."""


class ServiceRestartedError(RunStoppedError):
    """The service restarted mid-run with other settings than the run's."""


class PostStateUnreadableError(RunStoppedError):
    """A completed booking turn's appointments could not be read from the scheduler."""


class TurnUnsettledError(RunStoppedError):
    """A turn whose answer did not arrive had still not visibly ended at the bound."""


class Stack(Protocol):
    """The running chat service, as a run drives it."""

    async def open_session(self) -> OpenedSession:
        """Mint the run's session, and keep it for every later call."""
        ...

    def restore_session(self, session_id: str) -> None:
        """Carry an existing session for every later call, minting none."""
        ...

    async def new_chat(self) -> str:
        """Create a fresh chat in the session and return its id."""
        ...

    async def live_corpus(self) -> list[FaqEntry]:
        """Read the session's corpus in the service's listing order."""
        ...

    async def chat_identity(self, session_id: str, chat_id: str) -> ChatIdentity:
        """Read a chat's scheduler patient within its session."""
        ...

    async def session_patients(self, session_id: str) -> list[str]:
        """Read the scheduler patient of every chat in a session, once each."""
        ...

    async def plant_history(
        self, session_id: str, chat_id: str, history: Sequence[HistoryEntry]
    ) -> list[str]:
        """Plant a case's history into a chat before its turn; return the ids."""
        ...

    async def post_turn(
        self, chat_id: str, message: str, local_now: datetime
    ) -> TurnResult:
        """Post one turn and read its stream to the end."""
        ...

    async def read_thread(
        self, chat_id: str, message: str, planted_ids: Sequence[str]
    ) -> ThreadRead:
        """Read back the patient message and reply the turn stored."""
        ...

    async def plant(
        self,
        session_id: str,
        patient_id: str,
        given: Sequence[AppointmentRef],
        clock: datetime,
    ) -> list[AppointmentState] | Unplantable:
        """Book a fixture's preconditions for a patient, or say why not."""
        ...

    async def read_post_state(
        self, session_id: str, patient_id: str, clock: datetime
    ) -> list[AppointmentState]:
        """Read every appointment a patient holds."""
        ...

    async def release(
        self, session_id: str, patient_id: str, clock: datetime
    ) -> list[AppointmentState]:
        """Cancel what a patient holds standing, returning what was cancelled."""
        ...


class LiveStack:
    """The stack over HTTP to the chat service and a connection to its database."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        db: async_sessionmaker[AsyncSession],
        scheduler: Any,
    ) -> None:
        """Drive the service, its database and its scheduler.

        Args:
            client: an HTTP client to the chat service.
            db: sessions on the chat service's database.
            scheduler: the scheduling service's gRPC stub.
        """
        self._client = client
        self._db = db
        self._scheduler = scheduler

    async def open_session(self) -> OpenedSession:
        """Mint the run's session, and keep it for every later call."""
        return await session.open_run_session(self._client)

    def restore_session(self, session_id: str) -> None:
        """Carry an existing session for every later call, minting none."""
        session.restore_session(self._client, session_id)

    async def new_chat(self) -> str:
        """Create a fresh chat in the session and return its id."""
        return await session.new_case_chat(self._client)

    async def live_corpus(self) -> list[FaqEntry]:
        """Read the session's corpus in the service's listing order."""
        return await session.live_corpus(self._client)

    async def chat_identity(self, session_id: str, chat_id: str) -> ChatIdentity:
        """Read a chat's scheduler patient within its session."""
        async with self._db() as db:
            return await session.chat_identity(db, session_id, chat_id)

    async def session_patients(self, session_id: str) -> list[str]:
        """Read the scheduler patient of every chat in a session, once each."""
        async with self._db() as db:
            return await session.session_patient_ids(db, session_id)

    async def plant_history(
        self, session_id: str, chat_id: str, history: Sequence[HistoryEntry]
    ) -> list[str]:
        """Plant a case's history just before the database's own present.

        The turn's patient message is stamped by the database's clock, so the history
        is placed before that clock rather than this host's, which may disagree.
        """
        async with self._db() as db:
            before = await db.scalar(select(func.now()))
            if before is None:
                raise RuntimeError("the chat database returned no current time")
            return await planting.plant_history(
                db, session_id, chat_id, history, before=before
            )

    async def post_turn(
        self, chat_id: str, message: str, local_now: datetime
    ) -> TurnResult:
        """Post one turn and read its stream to the end."""
        return await turn.post_turn(self._client, chat_id, message, local_now)

    async def read_thread(
        self, chat_id: str, message: str, planted_ids: Sequence[str]
    ) -> ThreadRead:
        """Read back the patient message and reply the turn stored."""
        return await turn.read_thread(self._client, chat_id, message, planted_ids)

    async def plant(
        self,
        session_id: str,
        patient_id: str,
        given: Sequence[AppointmentRef],
        clock: datetime,
    ) -> list[AppointmentState] | Unplantable:
        """Book a fixture's preconditions for a patient, or say why not."""
        return await scheduling.plant(
            self._scheduler,
            session_id=session_id,
            patient_id=patient_id,
            given=given,
            clock=clock,
        )

    async def read_post_state(
        self, session_id: str, patient_id: str, clock: datetime
    ) -> list[AppointmentState]:
        """Read every appointment a patient holds.

        Raises: SchedulingCallError when the appointments cannot be read.
        """
        return await scheduling.read_post_state(
            self._scheduler, session_id=session_id, patient_id=patient_id, clock=clock
        )

    async def release(
        self, session_id: str, patient_id: str, clock: datetime
    ) -> list[AppointmentState]:
        """Cancel every appointment a patient holds standing, returning those cancelled.

        Raises: CleanupFailedError when any of them cannot be cancelled.
        """
        return await scheduling.release(
            self._scheduler, session_id=session_id, patient_id=patient_id, clock=clock
        )


@asynccontextmanager
async def live_stack(base_url: str) -> AsyncGenerator[Stack]:
    """Open the stack: the chat service at `base_url`, its database and its scheduler.

    The database URL and the scheduler's address are the chat service's own
    `DATABASE_URL` and `SCHEDULING_GRPC_TARGET` settings.
    """
    settings = Settings()
    engine = create_engine(settings.DATABASE_URL)
    timeout = httpx.Timeout(_CONNECT_TIMEOUT_SECONDS, read=_READ_TIMEOUT_SECONDS)
    try:
        async with (
            httpx.AsyncClient(base_url=base_url, timeout=timeout) as client,
            scheduling.scheduling_stub(settings.SCHEDULING_GRPC_TARGET) as scheduler,
        ):
            yield LiveStack(client, create_session_factory(engine), scheduler)
    finally:
        await engine.dispose()


async def drive_run(
    cases: Sequence[Case],
    selection: Selection,
    *,
    stack: Stack,
    log_path: Path,
    pin: CorpusPin,
    artifacts_dir: Path,
    clock: datetime = DEFAULT_CLOCK,
    timer: Callable[[], float] = time.monotonic,
    settle: SettleWait = DEFAULT_SETTLE,
) -> Path:
    """Start a run over `selection` and drive every case of it.

    Args:
        cases: the labels `selection` was taken from.
        log_path: the chat service's log, written as JSON lines.
        clock: the local time every turn is sent.
        timer: a monotonic clock, in seconds, that case durations are measured with,
            and the settle bound.
        settle: how a turn whose answer did not arrive is waited for before its patient
            is released (FR-041c).

    Returns: the run's directory under `artifacts_dir`

    Raises: ValueError when `selection` names a case `cases` does not hold; LabelError
        when a selected fixture would not plant on `clock`, before anything is sent;
        ConditionsMissingError when the log states no settings before the run begins;
        JsonLogMissingError when the service is not logging JSON; CorpusMismatchError
        when the live corpus is not the pinned one, with no `run.json` written;
        UnmappedCorpusEntryError when a pinned text has no single live entry;
        SessionError or httpx.HTTPError when the session cannot be opened or its corpus
        read, with no `run.json` written; ServiceRestartedError when the service
        restarted with other settings before the first turn; and HarnessFaultError,
        ServiceRestartedError, PostStateUnreadableError, CleanupFailedError,
        SessionError, ChatNotFoundError, ThreadReadError, TurnProtocolError,
        httpx.HTTPError or pydantic.ValidationError (a turn's logged segmentation
        breaking the record's shape), or TurnUnsettledError, which stop the run between
        cases.
    """
    selected, missing = _selected(cases, selection)
    if missing:
        raise ValueError(f"the selection names cases the labels lack: {missing}")
    for case in selected:
        if case.scheduling is None:
            continue
        try:
            validate_plantable(case.scheduling, clock)
        except LabelError as exc:
            raise LabelError(f"{case.id}: {exc}") from exc

    started = log_offset(log_path)
    started_preceding = preceding_bytes(log_path, started)
    conditions = RunConditions.from_event(service_conditions(log_path, started))
    opened = await stack.open_session()
    require_json_log(log_path, started)
    check, entry_ids = _verified_corpus(await stack.live_corpus(), pin)

    run = Run(
        run_id=str(ULID()),
        started_at=datetime.now(UTC),
        clock=clock,
        session_id=opened.session_id,
        corpus=CorpusRecord(
            live_sha256=check.live_sha256,
            pinned_sha256=check.pinned_sha256,
            matched=check.matched,
        ),
        selection=selection,
        entry_ids=entry_ids,
        conditions=conditions,
        labels=label_digests(selected),
    )
    run_dir = artifacts_dir / run.run_id
    write_run(run_dir, run)
    watch = _RestartWatch(log_path, conditions, started, started_preceding)
    await _drive_cases(run_dir, run, selected, stack, log_path, timer, settle, watch)
    return run_dir


async def resume_run(
    run_dir: Path,
    cases: Sequence[Case],
    *,
    stack: Stack,
    log_path: Path,
    pin: CorpusPin,
    timer: Callable[[], float] = time.monotonic,
    settle: SettleWait = DEFAULT_SETTLE,
) -> Path:
    """Continue a stored run in its own session, clock and selection.

    Returns: `run_dir`

    Raises: LabelsChangedError when a selected case is missing from `cases` or its
        digest changed; ConditionsMissingError when the log states no settings;
        ConditionsChangedError when the service states other settings than `run.json`;
        CorpusMismatchError or EntryIdsChangedError when the corpus is not the one the
        run verified; CleanupFailedError when a recorded case's patient, or any other
        patient of the session's chats, cannot be released, before any case is driven;
        and whatever stops `drive_run` between cases.

    A stopped attempt's patient is released before the run stops, but that release can
    itself fail, so every patient of the run session's chats is released again here.
    """
    run = read_run(run_dir)
    selected, missing = _selected(cases, run.selection)
    digests = label_digests(selected)
    changed = sorted(
        missing
        + [
            case_id
            for case_id, digest in digests.items()
            if run.labels[case_id] != digest
        ]
    )
    if changed:
        raise LabelsChangedError(
            f"selected cases whose labels changed since the run began: {changed}"
        )

    offset = log_offset(log_path)
    preceding = preceding_bytes(log_path, offset)
    conditions = RunConditions.from_event(service_conditions(log_path, offset))
    if conditions != run.conditions:
        raise ConditionsChangedError(
            "the service now states other settings than the run was measured under: "
            f"{_differences(run.conditions, conditions)}"
        )

    stack.restore_session(run.session_id)
    _check, entry_ids = _verified_corpus(await stack.live_corpus(), pin)
    if entry_ids != run.entry_ids:
        raise EntryIdsChangedError(
            f"the pinned entries now map to {entry_ids}, not {run.entry_ids}"
        )

    reconciled, recorded = _reconciled(run_dir, run)
    in_session = await stack.session_patients(run.session_id)
    patients = (*(case_run.patient_id for case_run in recorded), *in_session)
    for patient_id in dict.fromkeys(patients):
        if patient_id is not None:
            await stack.release(run.session_id, patient_id, run.clock)

    watch = _RestartWatch(log_path, conditions, offset, preceding)
    await _drive_cases(
        run_dir, reconciled, selected, stack, log_path, timer, settle, watch
    )
    return run_dir


class _RestartWatch:
    """The part of the log already checked for a restart under other settings.

    Invariant: every complete line from the offset the run's conditions were read at up
    to `offset` has been checked, in the log whose bytes just before `offset` are
    `preceding`. `check` reads on from `offset` and advances both, so a line is never
    skipped between two checks, whatever the driver did in between. The driver checks
    before each turn is posted, on every poll of a turn waited on to settle, and before
    each case is written, so no turn is posted, and no case recorded, past an unchecked
    line.

    A log replaced since the last check is read from its start: one now shorter than
    `offset`, or one truncated in place and grown back past it that holds other bytes
    before it. Two replacements are not told apart, and their lines before `offset` go
    unchecked: one holding the very bytes before `offset` it replaced, and one made in
    the instant between an offset being taken and its `preceding` bytes being read that
    has already grown past it.
    """

    def __init__(
        self,
        log_path: Path,
        conditions: RunConditions,
        offset: int,
        preceding: bytes,
    ) -> None:
        """Watch `log_path` from `offset` against the run's `conditions`.

        Args:
            preceding: the bytes the log held just before `offset` when it was taken.
        """
        self._log_path = log_path
        self._conditions = conditions
        self.offset = offset
        self._preceding = preceding

    def check(self, case_id: str) -> None:
        """Check every complete line logged since the last check.

        A restart stating the run's own settings is not a change, and is passed over.

        Raises: ServiceRestartedError naming `case_id` and each differing setting when a
            `service.configured` event states other settings than the run's.
        """
        restarts, self.offset = restarts_since(
            self._log_path, self.offset, preceding=self._preceding
        )
        self._preceding = preceding_bytes(self._log_path, self.offset)
        for restart in restarts:
            restarted = RunConditions.from_event(restart)
            if restarted != self._conditions:
                raise ServiceRestartedError(
                    f"{case_id}: the service restarted with other settings: "
                    f"{_differences(self._conditions, restarted)}"
                )


@dataclass(frozen=True)
class _Attempt:
    """One turn of an attempt at a case: its chat, what came back, what it amounts to.

    `message` is the text the turn posted. `result` is the error itself when the
    turn's stream began and then broke the service's contract, so no ending was read.
    `thread` is None when no stream was received, so there was nothing to read back.
    `skipped` is the ids of the messages the chat held before the turn, which the thread
    read passed over. `slice_preceding` is the bytes the log held just before
    `slice_offset`.
    """

    chat_id: str
    identity: ChatIdentity
    message: str
    result: TurnResult | TurnProtocolError
    thread: ThreadRead | None
    skipped: tuple[str, ...]
    slice_offset: int
    slice_preceding: bytes
    verdict: AttemptClass

    @property
    def stream_broke_contract(self) -> bool:
        """Whether the turn's stream began and then broke the service's contract."""
        return isinstance(self.result, TurnProtocolError)


@dataclass(frozen=True)
class _Broken:
    """An attempt an error stopped once its chat's patient had been read.

    `posted` is whether the error came after the turn was posted, so that the turn may
    have reached the pipeline. `result` is how the turn's stream ended, set only when
    the error came once the stream had been read to its end. `unsettled` is whether
    the turn's answer did not arrive whole, so that the turn was not seen to end and its
    patient must not be released (FR-041c). `stream_broke_contract` is whether the
    turn's stream began and then broke the service's contract.
    """

    chat_id: str
    identity: ChatIdentity
    error: Exception
    posted: bool
    result: TurnSent | None
    unsettled: bool = False
    stream_broke_contract: bool = False


@dataclass(frozen=True)
class _Driven:
    """A case driven to its record, and what must stop the run once it is written.

    `stopped_by` is the error that ended the case, None when none did; `cleanup_failure`
    is the release that could not complete, None when every release did.
    """

    case_run: CaseRun
    cleanup_failure: CleanupFailedError | None
    stopped_by: Exception | None = None


@dataclass(frozen=True)
class _Replied:
    """A reply case's record once its reply was driven, and what that turn amounts to.

    `verdict` is `MEASURED` when the reply turn was measured - a turn that settled
    included; `NOT_SENT` when it provably never reached the pipeline, so the exchange
    may be driven again; and `SENT_NO_ANSWER` when its outcome is unknown, with
    `stopped_by` the error that must stop the run once the case is written.
    `keep_patient` is whether the reply turn was not seen to end, so its patient must
    not be released.
    """

    case_run: CaseRun
    verdict: AttemptClass
    stopped_by: Exception | None = None
    keep_patient: bool = False


@dataclass(frozen=True)
class _Unplanted:
    """An attempt that stopped before its turn: the case's fixture would not plant."""

    chat_id: str
    identity: ChatIdentity
    unplantable: Unplantable


async def _drive_cases(
    run_dir: Path,
    run: Run,
    selected: Sequence[Case],
    stack: Stack,
    log_path: Path,
    timer: Callable[[], float],
    settle: SettleWait,
    watch: _RestartWatch,
) -> None:
    """Drive every selected case with no case file, in selection order, one at a time.

    Each case file is written, and `run.json` updated, before the next case begins.

    Raises: the error that ended a case, once that case has been written - or
        CleanupFailedError chained to it when its cleanup failed too; CleanupFailedError
        once the case whose cleanup failed has been written; ServiceRestartedError when
        the service restarted with other settings before the case was written, which is
        then left unwritten - chained to the error that ended the case, if one did, or
        replaced by CleanupFailedError chained to it when that case's cleanup failed;
        and whatever `_drive_case` raises, with that case unwritten.
    """
    recorded = set(recorded_case_ids(run_dir))
    for case in selected:
        if case.id in recorded:
            continue
        driven = await _drive_case(case, run, stack, log_path, timer, settle, watch)
        case_run, cleanup_failure = driven.case_run, driven.cleanup_failure
        try:
            watch.check(case.id)
        except ServiceRestartedError as restarted:
            if cleanup_failure is not None:
                raise cleanup_failure from restarted
            if driven.stopped_by is not None:
                raise restarted from driven.stopped_by
            raise
        write_case(run_dir, case_run)
        run = run.model_copy(
            update={
                "cases": [*run.cases, case.id],
                "drive_seconds": run.drive_seconds + case_run.elapsed_seconds,
            }
        )
        write_run(run_dir, run)
        if driven.stopped_by is not None:
            if cleanup_failure is not None:
                raise cleanup_failure from driven.stopped_by
            raise driven.stopped_by
        if cleanup_failure is not None:
            raise cleanup_failure
    write_run(run_dir, run.model_copy(update={"finished_at": datetime.now(UTC)}))


async def _drive_case(
    case: Case,
    run: Run,
    stack: Stack,
    log_path: Path,
    timer: Callable[[], float],
    settle: SettleWait,
    watch: _RestartWatch,
) -> _Driven:
    """Drive one case until an attempt measures something, or the attempts run out.

    Returns: the case's record, with what its cleanups cancelled, the cleanup failure
        and the error that must stop the run once the record is written, if any

    An attempt that provably never reached the pipeline is driven again in a fresh
    chat, once its patient is released. For a fixture carrying a reply, the attempt is
    the whole exchange: a reply that was never sent drives both turns again. A fixture
    case whose turn was posted and then could not be read back, or logged a segmentation
    breaking the record's shape, is recorded as outcome unknown: its appointments are
    read and released, and the error is returned to stop the run once it is written. A
    cleanup failing between two attempts records the case as a run error, since it
    cannot be driven again.

    A turn whose answer did not arrive whole (`SENT_NO_ANSWER`: a stream that broke off,
    timed out or broke the service's contract, first turn or reply) is not released or
    read for its appointments until its chat shows it ended (FR-041c). One that ends is
    judged as a completed turn - its thread, slice and appointments read as for any
    other, its hand-off read from its slice (`_handed_off`), a reply case going on to
    its reply unless it handed off - and is never retried. One that has not ended at
    the settle bound is written as outcome unknown, fixture or not, with its
    appointments read when it has a fixture and its patient not released - a release
    could not keep a turn still running from writing after it, and would record a
    cleanup the calendar cannot vouch for - and TurnUnsettledError is returned to stop
    the run once it is written. A thread read failing for such a turn, its own read or a
    poll, stops the run by that error under the rules for a thread read that fails
    after any posted turn - written as outcome unknown for a fixture case, left
    unwritten otherwise - except that the patient is not released either, for the same
    reason. A resumed run's sweep releases every such patient before it drives another
    case. A restart with other settings seen while such a turn is waited on stops the
    run on that poll, with the patient released and the case unwritten.

    Raises: HarnessFaultError when the service refuses a turn as malformed;
        ServiceRestartedError when the service restarted with other settings before a
        turn was posted, or while a turn whose answer did not arrive whole was waited
        on - chained to a poll's read error when that read failed too;
        PostStateUnreadableError when a completed turn's appointments
        cannot be read - after the case's last turn, or after the first turn of a case
        with a reply, which is then not posted; pydantic.ValidationError when a turn's
        logged segmentation breaks the record's shape and the case has no fixture;
        ChatNotFoundError, TurnProtocolError, ThreadReadError or httpx.HTTPError when an
        attempt's thread read or chat raised it and the case has no fixture or its turn
        was not posted - with the patient not released when the turn's answer did not
        arrive whole; CleanupFailedError, chained to any of these, when
        the attempt's patient cannot be released. None of these leaves a record of the
        case.
    """
    started = timer()
    cancelled: list[AppointmentState] = []

    async def finish(
        case_run: CaseRun,
        identity: ChatIdentity,
        stopped_by: Exception | None = None,
        *,
        release: bool = True,
    ) -> _Driven:
        failure: CleanupFailedError | None = None
        # A chat created while the scheduler was unreachable has no patient, and the
        # turn's own request provisions one - so the patient read before the turn is
        # read again, or what that turn booked would stand in a later case's way.
        identity = await _current_identity(stack, run, case_run.chat_id, identity)
        try:
            if release:
                cancelled.extend(await _release(stack, run, identity))
        except CleanupFailedError as exc:
            cancelled.extend(exc.cancelled)
            failure = exc
        update = {
            "patient_id": identity.patient_id,
            "cancelled_after": list(cancelled),
            "elapsed_seconds": timer() - started,
        }
        return _Driven(case_run.model_copy(update=update), failure, stopped_by)

    for number in range(1, MAX_ATTEMPTS + 1):
        attempt = await _attempt(case, run, stack, log_path, watch)
        if isinstance(attempt, _Unplanted):
            return await finish(
                _unplanted(case, attempt, number, timer() - started),
                attempt.identity,
            )

        if isinstance(attempt, _Broken):
            if case.scheduling is None or not attempt.posted:
                if attempt.unsettled:
                    # As for a poll that cannot read the thread: the turn was not seen
                    # to end, so its patient is kept.
                    raise attempt.error
                await _release_then_stop(
                    stack, run, attempt.chat_id, attempt.identity, attempt.error
                )
            # The turn may have booked something, so a resumed run must not post it
            # again: it is written as outcome unknown before the error stops the run.
            after = await _post_state(stack, run, attempt.identity)
            return await finish(
                _broken(case, attempt, number, timer() - started, after),
                attempt.identity,
                attempt.error,
                release=not attempt.unsettled,
            )

        if attempt.verdict is AttemptClass.HARNESS_FAULT:
            refused = attempt.result
            assert isinstance(refused, TurnRefused)
            await _release_then_stop(
                stack,
                run,
                attempt.chat_id,
                attempt.identity,
                HarnessFaultError(case.id, refused.status_code, refused.body),
            )

        if attempt.verdict is AttemptClass.SENT_NO_ANSWER:
            try:
                settled = await _settle(case.id, stack, attempt, timer, settle, watch)
            except ServiceRestartedError as exc:
                # The restart ended the process running the turn, so nothing of it can
                # land later: the patient is released and the case left unwritten.
                await _release_then_stop(
                    stack, run, attempt.chat_id, attempt.identity, exc
                )
            if not isinstance(settled, _Attempt):
                if case.scheduling is None and not isinstance(
                    settled, TurnUnsettledError
                ):
                    # A thread that cannot be read stops the run, as after any posted
                    # turn - but the turn was not seen to end, so its patient is kept.
                    raise settled
                # The turn may still write, so it is not driven again and its patient
                # is not released; it is written as outcome unknown before the run
                # stops, and a resumed run's sweep releases the patient.
                after = (
                    await _post_state(stack, run, attempt.identity)
                    if case.scheduling is not None
                    else None
                )
                unknown = _unmeasured(case, attempt, number, timer() - started)
                return await finish(
                    unknown.model_copy(
                        update={
                            "excluded": ExclusionReason.OUTCOME_UNKNOWN,
                            "scheduling_after": after,
                        }
                    ),
                    attempt.identity,
                    settled,
                    release=False,
                )
            # Its outcome is now known: it is judged as a completed turn, never retried.
            attempt = settled

        if attempt.verdict is AttemptClass.MEASURED:
            try:
                case_run = _measured(case, attempt, number, timer() - started, log_path)
            except ValidationError as exc:
                if case.scheduling is None:
                    await _release_then_stop(
                        stack, run, attempt.chat_id, attempt.identity, exc
                    )
                # The turn may have booked something, so a resumed run must not post
                # it again: it is written as outcome unknown before the error stops it.
                after = await _post_state(stack, run, attempt.identity)
                return await finish(
                    _unsegmented(case, attempt, number, timer() - started, after),
                    attempt.identity,
                    exc,
                )
            if case.scheduling is None:
                return await finish(case_run, attempt.identity)
            reply = case.scheduling.reply
            if reply is not None and _ended_with_a_reply(attempt, case_run.events):
                replied = await _drive_reply(
                    case,
                    reply,
                    case_run,
                    attempt,
                    run,
                    stack,
                    log_path,
                    watch,
                    timer,
                    settle,
                )
                case_run = replied.case_run
                if replied.verdict is AttemptClass.SENT_NO_ANSWER:
                    # The reply may have written something, so the exchange is not
                    # driven again; what the patient holds is stored if it can be read.
                    # A reply that did not settle keeps its patient.
                    after = await _post_state(stack, run, attempt.identity)
                    return await finish(
                        case_run.model_copy(update={"scheduling_after": after}),
                        attempt.identity,
                        replied.stopped_by,
                        release=not replied.keep_patient,
                    )
                if replied.verdict is AttemptClass.NOT_SENT and number < MAX_ATTEMPTS:
                    # The whole exchange is driven again in a fresh chat, once this
                    # attempt's patient is released.
                    driven = await finish(case_run, attempt.identity)
                    if driven.cleanup_failure is not None:
                        return driven
                    continue
            after = await _post_state(stack, run, attempt.identity)
            if after is None:
                await _release_then_stop(
                    stack,
                    run,
                    attempt.chat_id,
                    attempt.identity,
                    PostStateUnreadableError(
                        f"{case.id}: the patient's appointments could not be read"
                    ),
                )
            return await finish(
                case_run.model_copy(update={"scheduling_after": after}),
                attempt.identity,
            )

        # What is left provably never reached the pipeline.
        unmeasured = _unmeasured(case, attempt, number, timer() - started)
        if number == MAX_ATTEMPTS:
            return await finish(unmeasured, attempt.identity)
        # Driven again in a fresh chat: the failed attempt's stored message would
        # otherwise be read by the classifier as part of the retried turn. Its patient
        # is released first, so a precondition it holds cannot refuse the next plant.
        driven = await finish(unmeasured, attempt.identity)
        if driven.cleanup_failure is not None:
            return driven
    raise AssertionError("the attempt loop always returns")


async def _attempt(
    case: Case, run: Run, stack: Stack, log_path: Path, watch: _RestartWatch
) -> _Attempt | _Unplanted | _Broken:
    """Make one attempt at a case in a fresh chat of the run's session.

    Returns: the attempt; why its fixture would not plant - in which case no turn was
        posted; or the error that stopped it once the chat's patient had been read,
        whose patient is not yet released

    Raises: JsonLogMissingError when creating the chat logged no JSON event;
        ServiceRestartedError when the service restarted with other settings before the
        turn was posted, which is then not posted and its patient released.
    """
    chat_offset = log_offset(log_path)
    chat_id = await stack.new_chat()
    # Checked per chat rather than once per run: a resumed run has posted nothing yet,
    # and a service restarted between two cases may have come back logging for people.
    require_json_log(log_path, chat_offset)
    identity = await stack.chat_identity(run.session_id, chat_id)
    try:
        planted = await stack.plant_history(run.session_id, chat_id, case.history or [])
    except ChatNotFoundError as exc:
        return _Broken(chat_id, identity, exc, posted=False, result=None)

    if case.scheduling is not None:
        if identity.patient_id is None:
            return _Unplanted(
                chat_id,
                identity,
                Unplantable(
                    situation=UnplantableSituation.NO_PATIENT,
                    detail="the chat has no scheduler patient",
                ),
            )
        preconditions = await stack.plant(
            run.session_id, identity.patient_id, case.scheduling.given, run.clock
        )
        if isinstance(preconditions, Unplantable):
            return _Unplanted(chat_id, identity, preconditions)

    return await _turn(
        case.id, run, stack, log_path, watch, identity, chat_id, case.message, planted
    )


async def _turn(
    case_id: str,
    run: Run,
    stack: Stack,
    log_path: Path,
    watch: _RestartWatch,
    identity: ChatIdentity,
    chat_id: str,
    message: str,
    skipped: Sequence[str],
) -> _Attempt | _Broken:
    """Post one turn of a case into its chat and read back what the turn stored.

    Args:
        skipped: the ids of the messages the chat holds before the turn, which reading
            the turn's thread passes over.

    Returns: the turn - a stream that broke the service's contract carried as its
        result, and judged by the thread like a stream that broke off; or the error that
        stopped it once it was posted, whose patient is not yet released

    Raises: ServiceRestartedError when the service restarted with other settings before
        the turn was posted, which is then not posted and its patient released.
    """
    # The offset is taken before the check reads on to the end, so no line falls between
    # the lines checked and the turn's slice.
    slice_offset = log_offset(log_path)
    slice_preceding = preceding_bytes(log_path, slice_offset)
    try:
        watch.check(case_id)
    except ServiceRestartedError as exc:
        await _release_then_stop(stack, run, chat_id, identity, exc)
    result: TurnResult | TurnProtocolError
    try:
        result = await stack.post_turn(chat_id, message, run.clock)
    except TurnProtocolError as exc:
        # The stream began and broke mid-answer, so the turn may still be running: it is
        # read back and judged like a stream that broke off (FR-041c).
        result = exc
    thread: ThreadRead | None = None
    if isinstance(result, TurnSent | TurnProtocolError):
        try:
            thread = await stack.read_thread(chat_id, message, skipped)
        except (ThreadReadError, TurnProtocolError, httpx.HTTPError) as exc:
            return _Broken(
                chat_id,
                identity,
                exc,
                posted=True,
                result=result if isinstance(result, TurnSent) else None,
                unsettled=not _arrived_whole(result),
                stream_broke_contract=isinstance(result, TurnProtocolError),
            )
    return _Attempt(
        chat_id=chat_id,
        identity=identity,
        message=message,
        result=result,
        thread=thread,
        skipped=tuple(skipped),
        slice_offset=slice_offset,
        slice_preceding=slice_preceding,
        verdict=_classified(result, thread),
    )


def _ended_with_a_reply(attempt: _Attempt, events: Sequence[LogEvent] | None) -> bool:
    """Whether a turn ended with a reply of its own, so a scripted reply may answer it.

    Args:
        events: the turn's own log slice, None when none was selected.

    That is a stored reply, with a `done` terminal event that is not a hand-off - or,
    for a turn whose terminal event never arrived and that stored its reply all the
    same, with no terminal event at all and no hand-off in its slice (`_handed_off`).
    """
    result, thread = attempt.result, attempt.thread
    if not isinstance(result, TurnSent | TurnProtocolError) or thread is None:
        return False
    if thread.assistant_message is None:
        return False
    terminal = _terminal(result)
    if terminal is not None and terminal.kind in _UNANSWERED_ENDINGS:
        return False
    return not _handed_off(terminal, events)


# The terminal events that end a turn with no reply of its own.
_UNANSWERED_ENDINGS: Final = frozenset({TerminalKind.SILENT, TerminalKind.CANCELLED})


def _handed_off(terminal: Terminal | None, events: Sequence[LogEvent] | None) -> bool:
    """Whether a turn that ended handed off.

    Args:
        terminal: the terminal the turn records - None, or `error`, when no terminal
            event was delivered.
        events: the turn's own log slice, None when none was selected.

    A turn that delivered its terminal event is judged by that event alone: a `done`
    whose `answer_source` is `hand_off`. One that delivered none is judged by its own
    slice (FR-041d): it handed off exactly when the slice holds one `turn.completed`
    and that event's `outcome` is `handed_off`. A slice holding no `turn.completed`, or
    several - the service logs one per turn, so several say nothing - or no slice at
    all, does not say the turn handed off.
    """
    if terminal is not None and terminal.kind is not TerminalKind.ERROR:
        source = terminal.payload.get("answer_source")
        return (
            terminal.kind is TerminalKind.DONE and source == AnswerSource.HAND_OFF.value
        )
    if events is None:
        return False
    completed = [event for event in events if event["event"] == _COMPLETED_EVENT]
    return len(completed) == 1 and completed[0].get("outcome") == HANDED_OFF_OUTCOME


async def _drive_reply(
    case: Case,
    reply: str,
    first: CaseRun,
    attempt: _Attempt,
    run: Run,
    stack: Stack,
    log_path: Path,
    watch: _RestartWatch,
    timer: Callable[[], float],
    settle: SettleWait,
) -> _Replied:
    """Read a reply case's appointments after its first turn, then post the reply.

    Args:
        first: the record of the measured first turn.
        attempt: the first turn, which ended with a reply of its own.

    Returns: `first` carrying the appointments read after it, the reply's turn once a
        reply was posted that may have reached the pipeline, and the exclusion of the
        whole exchange; with what the reply turn amounts to. A reply provably never
        sent makes the case a run error, and one whose outcome is unknown makes it
        outcome unknown, whatever the first turn was excluded as. A reply whose answer
        did not arrive whole is waited for until it settles (FR-041c): one that settles
        is measured like any other, and one that does not - or whose thread cannot be
        read while it has not - is outcome unknown with its patient kept. A measured
        reply is
        judged by the first turn's rules, and its reason recorded only when the first
        turn has none. A reply that handed off is recorded as `handed_off_turn` like
        any other reason (FR-037b): its scope sets the whole case aside from retrieval
        and serving, the first turn's included, and leaves classification and booking
        scored (FR-018a).

    Raises: PostStateUnreadableError when the appointments after the first turn cannot
        be read; ServiceRestartedError when the service restarted with other settings
        before the reply was posted, or while it was waited on to settle;
        HarnessFaultError when the service refuses the
        reply as malformed; CleanupFailedError, chained to any of these, when the
        patient cannot be released. The patient is released before each of these, and
        no record of the case is left.
    """
    before = await _post_state(stack, run, attempt.identity)
    if before is None:
        await _release_then_stop(
            stack,
            run,
            attempt.chat_id,
            attempt.identity,
            PostStateUnreadableError(
                f"{case.id}: the patient's appointments could not be read after its "
                "first turn"
            ),
        )
    recorded = first.model_copy(update={"scheduling_before_reply": before})
    thread = attempt.thread
    assert thread is not None
    answered = [
        message.id
        for message in (thread.patient_message, thread.assistant_message)
        if message is not None
    ]
    second = await _turn(
        case.id,
        run,
        stack,
        log_path,
        watch,
        attempt.identity,
        attempt.chat_id,
        reply,
        (*attempt.skipped, *answered),
    )

    if isinstance(second, _Broken):
        terminal = second.result.terminal if second.result is not None else None
        return _Replied(
            recorded.model_copy(
                update={
                    "reply_turn": ReplyTurn(
                        terminal=terminal,
                        stream_broke_contract=second.stream_broke_contract,
                    ),
                    "excluded": ExclusionReason.OUTCOME_UNKNOWN,
                }
            ),
            AttemptClass.SENT_NO_ANSWER,
            second.error,
            keep_patient=second.unsettled,
        )
    if second.verdict is AttemptClass.HARNESS_FAULT:
        refused = second.result
        assert isinstance(refused, TurnRefused)
        await _release_then_stop(
            stack,
            run,
            attempt.chat_id,
            attempt.identity,
            HarnessFaultError(case.id, refused.status_code, refused.body),
        )
    if second.verdict is AttemptClass.NOT_SENT:
        return _Replied(
            recorded.model_copy(update={"excluded": ExclusionReason.RUN_ERROR}),
            AttemptClass.NOT_SENT,
        )

    reply_thread = second.thread
    assert reply_thread is not None
    reply_turn = ReplyTurn(
        terminal=_terminal(second.result),
        stream_broke_contract=second.stream_broke_contract,
        patient_message=reply_thread.patient_message,
        assistant_message=reply_thread.assistant_message,
    )
    unknown = recorded.model_copy(
        update={
            "reply_turn": reply_turn,
            "excluded": ExclusionReason.OUTCOME_UNKNOWN,
        }
    )
    if second.verdict is AttemptClass.SENT_NO_ANSWER:
        try:
            settled = await _settle(case.id, stack, second, timer, settle, watch)
        except ServiceRestartedError as exc:
            await _release_then_stop(stack, run, attempt.chat_id, attempt.identity, exc)
        if not isinstance(settled, _Attempt):
            return _Replied(
                unknown, AttemptClass.SENT_NO_ANSWER, settled, keep_patient=True
            )
        second, reply_thread = settled, settled.thread
        assert reply_thread is not None
        reply_turn = reply_turn.model_copy(
            update={
                "patient_message": reply_thread.patient_message,
                "assistant_message": reply_thread.assistant_message,
            }
        )
        unknown = unknown.model_copy(update={"reply_turn": reply_turn})
    try:
        events, segments = _turn_slice(second, log_path)
    except ValidationError as exc:
        return _Replied(unknown, AttemptClass.SENT_NO_ANSWER, exc)
    reason = _turn_exclusion(_terminal(second.result), reply_thread, segments, events)
    return _Replied(
        recorded.model_copy(
            update={
                "reply_turn": reply_turn.model_copy(update={"events": events}),
                "excluded": first.excluded if first.excluded is not None else reason,
            }
        ),
        AttemptClass.MEASURED,
    )


async def _settle(
    case_id: str,
    stack: Stack,
    turn: _Attempt,
    timer: Callable[[], float],
    settle: SettleWait,
    watch: _RestartWatch,
) -> (
    _Attempt
    | TurnUnsettledError
    | ThreadReadError
    | TurnProtocolError
    | httpx.HTTPError
):
    """Poll the chat of a turn whose answer did not arrive until the turn has ended.

    Ended is what the chat stored (FR-041c): a reply to the turn's patient message, or
    that message marked `assistant_failed`. The thread does not say which message a
    reply answers (`MessageOut` carries no `reply_to_message_ids`), so the reply is
    recognised by the chat's shape instead: each read passes over every message the
    chat held before the turn, and `read_thread` refuses a thread holding more than one
    patient message or reply besides them, or a reply with no patient message, so a
    reply it returns can only answer this turn's patient message. The turn's own read
    already found neither, so the first poll waits one interval.

    Every poll checks the log for a restart under other settings (FR-041c), after its
    read, so the wait stops on the poll that sees one rather than at the bound.

    Raises: ServiceRestartedError when a poll sees a restart with other settings -
        chained to that poll's read error when its read failed too.

    Returns: the turn once it has ended, carrying the thread the poll read and judged
        as measured; the error that stopped the wait otherwise -
        TurnUnsettledError when it had not ended once `settle.timeout_seconds` passed on
        `timer`; ThreadReadError, TurnProtocolError or httpx.HTTPError when a poll could
        not read the thread, which leaves the turn no more settled than the bound
        running out does.
    """
    deadline = timer() + settle.timeout_seconds
    while timer() < deadline:
        await settle.sleep(settle.interval_seconds)
        failure: ThreadReadError | TurnProtocolError | httpx.HTTPError | None = None
        try:
            thread = await stack.read_thread(turn.chat_id, turn.message, turn.skipped)
        except (ThreadReadError, TurnProtocolError, httpx.HTTPError) as exc:
            failure = exc
        # Checked after the read, whatever it returned, so a restart logged during it
        # stops this poll - and a read that failed beside one is reported as the
        # restart, chained to the read's error.
        try:
            watch.check(case_id)
        except ServiceRestartedError as restarted:
            if failure is not None:
                raise restarted from failure
            raise
        if failure is not None:
            return failure
        if turn_settled(thread.patient_message, thread.assistant_message):
            return replace(turn, thread=thread, verdict=AttemptClass.MEASURED)
    return TurnUnsettledError(
        f"{case_id}: the turn posted to chat {turn.chat_id} stored no reply and no "
        f"assistant_failed mark within {settle.timeout_seconds:g}s of its answer not "
        "arriving, so it may still write; its patient was not released, and a resumed "
        "run releases it before driving another case"
    )


def _classified(
    result: TurnResult | TurnProtocolError, thread: ThreadRead | None
) -> AttemptClass:
    """Decide what a turn amounts to, a stream that broke the contract included.

    A stream that broke the contract is judged as one that broke off: `MEASURED` when
    its thread shows the turn ended, `SENT_NO_ANSWER` otherwise.

    Raises: ValueError when a stream that broke off, or broke the contract, comes with
        no thread.
    """
    if isinstance(result, TurnProtocolError):
        result = TurnSent(
            terminal=Terminal(kind=TerminalKind.ERROR), interruption=str(result)
        )
    return classify_attempt(result, thread)


def _arrived_whole(result: TurnResult | TurnProtocolError) -> bool:
    """Whether a posted turn's answer arrived whole: a terminal event, read to the end.

    A read timeout, a stream cut off, and a stream that broke the contract are not.
    """
    return (
        isinstance(result, TurnSent) and result.terminal.kind is not TerminalKind.ERROR
    )


def _terminal(result: TurnResult | TurnProtocolError) -> Terminal | None:
    """The terminal a turn records: None when no stream was read to its end.

    A stream that broke the contract has none, whatever it carried before it broke - an
    ending the harness did not read whole is not recorded as one.
    """
    return result.terminal if isinstance(result, TurnSent) else None


async def _post_state(
    stack: Stack, run: Run, identity: ChatIdentity
) -> list[AppointmentState] | None:
    """Read the patient's appointments, or None when they cannot be read."""
    if identity.patient_id is None:
        return None
    try:
        return await stack.read_post_state(
            run.session_id, identity.patient_id, run.clock
        )
    except SchedulingCallError:
        return None


async def _current_identity(
    stack: Stack, run: Run, chat_id: str, identity: ChatIdentity
) -> ChatIdentity:
    """Return the chat's identity as it stands now when `identity` names no patient.

    `POST /chat` provisions a patient for a chat that has none, so a chat read as
    patientless before its turn may hold one after it. A chat with a patient keeps it,
    and is not read again. A chat no longer found keeps `identity`.
    """
    if identity.patient_id is not None:
        return identity
    try:
        return await stack.chat_identity(run.session_id, chat_id)
    except ChatNotFoundError:
        return identity


async def _release(
    stack: Stack, run: Run, identity: ChatIdentity
) -> list[AppointmentState]:
    """Cancel what the chat's patient holds standing; nothing for a chat with none.

    Raises: CleanupFailedError when any of it cannot be cancelled.
    """
    if identity.patient_id is None:
        return []
    return await stack.release(run.session_id, identity.patient_id, run.clock)


async def _release_then_stop(
    stack: Stack, run: Run, chat_id: str, identity: ChatIdentity, error: Exception
) -> NoReturn:
    """Release the patient of an attempt that will not be recorded, then stop the run.

    The patient is read again when `identity` names none, as a recorded case's is: a
    turn posted into a patientless chat provisions one, and may have booked with it.

    Raises: `error`; or CleanupFailedError, chained to it, when the release fails.
    """
    identity = await _current_identity(stack, run, chat_id, identity)
    try:
        await _release(stack, run, identity)
    except CleanupFailedError as failed:
        raise failed from error
    raise error


def _unplanted(
    case: Case, attempt: _Unplanted, attempts: int, elapsed: float
) -> CaseRun:
    """Record a case whose fixture would not plant, and why, with no turn posted."""
    return CaseRun(
        case_id=case.id,
        chat_id=attempt.chat_id,
        patient_id=attempt.identity.patient_id,
        attempts=attempts,
        elapsed_seconds=elapsed,
        excluded=ExclusionReason.UNRESOLVABLE_FIXTURE,
        unplantable=attempt.unplantable,
    )


def _broken(
    case: Case,
    attempt: _Broken,
    attempts: int,
    elapsed: float,
    after: list[AppointmentState] | None,
) -> CaseRun:
    """Record a fixture case whose posted turn an error stopped, as outcome unknown.

    What was read before the error is kept: how the stream ended, when it was read to
    its end, and the patient's appointments, when they could be read.
    """
    return CaseRun(
        case_id=case.id,
        chat_id=attempt.chat_id,
        patient_id=attempt.identity.patient_id,
        attempts=attempts,
        elapsed_seconds=elapsed,
        terminal=attempt.result.terminal if attempt.result is not None else None,
        stream_broke_contract=attempt.stream_broke_contract,
        scheduling_after=after,
        excluded=ExclusionReason.OUTCOME_UNKNOWN,
    )


def _unsegmented(
    case: Case,
    attempt: _Attempt,
    attempts: int,
    elapsed: float,
    after: list[AppointmentState] | None,
) -> CaseRun:
    """Record a fixture case whose logged segmentation broke the record, as unknown.

    What was read before the error is kept: how the stream ended, what the thread
    stored, and the patient's appointments, when they could be read.
    """
    thread = attempt.thread
    assert thread is not None
    return CaseRun(
        case_id=case.id,
        chat_id=attempt.chat_id,
        patient_id=attempt.identity.patient_id,
        attempts=attempts,
        elapsed_seconds=elapsed,
        terminal=_terminal(attempt.result),
        stream_broke_contract=attempt.stream_broke_contract,
        patient_message=thread.patient_message,
        assistant_message=thread.assistant_message,
        scheduling_after=after,
        excluded=ExclusionReason.OUTCOME_UNKNOWN,
    )


def _measured(
    case: Case,
    attempt: _Attempt,
    attempts: int,
    elapsed: float,
    log_path: Path,
) -> CaseRun:
    """Record a measured attempt with its log slice and its turn-level exclusion.

    A restart inside the slice is not looked for here: the driver's restart watch
    reads every line of the log, the slice's included.
    """
    thread = attempt.thread
    assert thread is not None
    terminal = _terminal(attempt.result)
    events, segments = _turn_slice(attempt, log_path)
    return CaseRun.model_validate(
        {
            "case_id": case.id,
            "chat_id": attempt.chat_id,
            "patient_id": attempt.identity.patient_id,
            "attempts": attempts,
            "elapsed_seconds": elapsed,
            "terminal": terminal,
            "stream_broke_contract": attempt.stream_broke_contract,
            "patient_message": thread.patient_message,
            "assistant_message": thread.assistant_message,
            "segments": segments,
            "events": events,
            "excluded": _turn_exclusion(terminal, thread, segments, events),
        }
    )


def _turn_slice(
    attempt: _Attempt, log_path: Path
) -> tuple[list[LogEvent] | None, ProducedSegmentation | None]:
    """Read one turn's own events out of the log, and the segmentation they record.

    Returns: the events of the one turn that received the attempt's patient message,
        None when no slice could be read or selected; and that turn's segmentation,
        None when it has no events or they hold no single classification

    Raises: pydantic.ValidationError when the logged segmentation breaks the record's
        shape.
    """
    thread = attempt.thread
    patient = thread.patient_message if thread is not None else None
    if patient is None:
        return None, None
    slice_events = read_slice(
        log_path, attempt.slice_offset, preceding=attempt.slice_preceding
    )
    if isinstance(slice_events, SliceMissing):
        return None, None
    selected = select_turn(slice_events, patient.id)
    if isinstance(selected, SliceMissing):
        return None, None
    segmentation = produced_segmentation(selected)
    if isinstance(segmentation, SliceMissing):
        return list(selected), None
    return list(selected), segmentation


def _unmeasured(
    case: Case, attempt: _Attempt, attempts: int, elapsed: float
) -> CaseRun:
    """Record a case whose every attempt measured nothing, as a run error."""
    thread = attempt.thread
    return CaseRun(
        case_id=case.id,
        chat_id=attempt.chat_id,
        patient_id=attempt.identity.patient_id,
        attempts=attempts,
        elapsed_seconds=elapsed,
        terminal=_terminal(attempt.result),
        stream_broke_contract=attempt.stream_broke_contract,
        patient_message=thread.patient_message if thread is not None else None,
        assistant_message=thread.assistant_message if thread is not None else None,
        excluded=ExclusionReason.RUN_ERROR,
    )


def _turn_exclusion(
    terminal: Terminal | None,
    thread: ThreadRead,
    segments: ProducedSegmentation | None,
    events: Sequence[LogEvent] | None,
) -> ExclusionReason | None:
    """Decide the turn-level exclusion of a measured turn, or None when it has none.

    Args:
        events: the turn's own log slice, None when none was selected.

    In order: a silent or cancelled ending; a turn marked `assistant_failed` that stored
    no reply; a turn whose classification could not be read from the log; a produced
    `classification_failed`; and a hand-off - by the terminal event when one was
    delivered, by the turn's own `turn.completed` otherwise (`_handed_off`).
    """
    if terminal is not None and terminal.kind is TerminalKind.SILENT:
        return ExclusionReason.SILENCED_TURN
    if terminal is not None and terminal.kind is TerminalKind.CANCELLED:
        return ExclusionReason.CANCELLED_TURN
    failed = marked_assistant_failed(thread.patient_message)
    if failed and thread.assistant_message is None:
        return ExclusionReason.RUN_ERROR
    if segments is None:
        return ExclusionReason.MISSING_LOG_SLICE
    if any(s.intent is IntentLabel.CLASSIFICATION_FAILED for s in segments.segments):
        return ExclusionReason.RUN_ERROR
    handed_off = _handed_off(terminal, events)
    return ExclusionReason.HANDED_OFF_TURN if handed_off else None


def _selected(
    cases: Sequence[Case], selection: Selection
) -> tuple[list[Case], list[str]]:
    """Look up a selection's cases.

    Returns: the selected cases found, in selection order, and the selected ids with no
        case
    """
    by_id = {case.id: case for case in cases}
    found = [by_id[case_id] for case_id in selection.case_ids if case_id in by_id]
    missing = [case_id for case_id in selection.case_ids if case_id not in by_id]
    return found, missing


def _verified_corpus(
    live: Sequence[FaqEntry], pin: CorpusPin
) -> tuple[CorpusCheck, dict[str, int]]:
    """Verify a live corpus against the pin and map each pinned slug to its entry.

    Returns: the corpus check, and each pinned slug mapped to the live entry id

    Raises: CorpusMismatchError when the corpus does not match the pin;
        UnmappedCorpusEntryError when a pinned text has no single live entry.
    """
    check = verify(live, pin)
    if not check.matched:
        raise CorpusMismatchError(check)
    return check, map_entry_ids(live, pin)


def _reconciled(run_dir: Path, run: Run) -> tuple[Run, list[CaseRun]]:
    """Rebuild `run.json`'s recorded cases and drive time from the case files on disk.

    Returns: the rebuilt run, and the recorded cases it was rebuilt from, in selection
        order - each file read once, for the resume sweep to take its patient from

    A run stopped between writing a case file and updating `run.json` would otherwise
    resume with that case missing from both.
    """
    on_disk = set(recorded_case_ids(run_dir))
    recorded = [
        read_case(run_dir, case_id)
        for case_id in run.selection.case_ids
        if case_id in on_disk
    ]
    rebuilt = run.model_copy(
        update={
            "cases": [case_run.case_id for case_run in recorded],
            "drive_seconds": sum(case_run.elapsed_seconds for case_run in recorded),
        }
    )
    return rebuilt, recorded


def _differences(recorded: RunConditions, stated: RunConditions) -> dict[str, str]:
    """Name every condition that differs, with its recorded and its stated value."""
    before, after = recorded.model_dump(), stated.model_dump()
    return {
        name: f"{before[name]!r} -> {after[name]!r}"
        for name in before
        if before[name] != after[name]
    }
