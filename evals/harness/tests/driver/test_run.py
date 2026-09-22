"""Driving a run: one case at a time, resumable, and retried only when unmeasured.

The stack is a stub: it hands out chats, answers turns from a per-case script, and
appends to a real log file the lines the chat service would, so the log slice is read
exactly as it is against a live service.
"""

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from chat.domain.models import AttentionMark
from chat.domain.schemas import FaqEntry
from golden_harness.cases import (
    AppointmentRef,
    Case,
    HistoryEntry,
    LabelError,
    label_digests,
    select,
)
from golden_harness.corpus import CorpusPin, load_pin
from golden_harness.driver import run as run_module
from golden_harness.driver.logslice import ConditionsMissingError, JsonLogMissingError
from golden_harness.driver.run import (
    DEFAULT_CLOCK,
    DEFAULT_SETTLE,
    MAX_ATTEMPTS,
    SETTLE_INTERVAL_SECONDS,
    SETTLE_TIMEOUT_SECONDS,
    ConditionsChangedError,
    CorpusMismatchError,
    EntryIdsChangedError,
    HarnessFaultError,
    LabelsChangedError,
    PostStateUnreadableError,
    ServiceRestartedError,
    SettleWait,
    TurnUnsettledError,
    drive_run,
    resume_run,
)
from golden_harness.driver.scheduling import (
    CleanupFailedError,
    SchedulingCallError,
    Unplantable,
)
from golden_harness.driver.session import (
    ChatIdentity,
    ChatNotFoundError,
    OpenedSession,
    SessionError,
)
from golden_harness.driver.turn import (
    ThreadRead,
    ThreadReadError,
    TurnNotConnected,
    TurnProtocolError,
    TurnRefused,
    TurnResult,
    TurnSent,
    TurnTracing,
    read_thread,
)
from golden_harness.record import (
    AppointmentState,
    CaseRun,
    ExclusionReason,
    RunTracing,
    StoredMessage,
    TerminalKind,
    UnplantableSituation,
    read_case,
    read_run,
    recorded_case_ids,
    write_run,
)
from pydantic import ValidationError
from shared_models.scheduling import AppointmentStatus

_PIN = load_pin(
    Path(__file__).resolve().parents[4] / "evals" / "golden" / "corpus.json"
)
_SESSION_ID = "01K5RUNSESSION000000000000"
_PLANTED_AT = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)

_CONDITIONS: dict[str, Any] = {
    "classification_model": "claude-haiku-4-5-20251001",
    "generation_model": "claude-sonnet-5",
    "embedding_model": "voyage-3.5",
    "rerank_model": "rerank-3",
    "retrieval_pool_size": 25,
    "similarity_floor": 0.3,
    "similarity_cap": 5,
    "rerank_floor": 0.58,
    "rerank_cap": 3,
    "max_segments": 3,
    "context_turns": 5,
}

_DONE: dict[str, Any] = {
    "type": "done",
    "request_outcomes": None,
    "message": None,
    "answer_source": "small_talk",
}


def _case(
    case_id: str,
    *intents: str,
    history: bool = False,
    scheduling: dict[str, Any] | None = None,
) -> Case:
    requests = [
        {"intent": intent, "gist": "g"}
        | ({"answerable": False, "cites": []} if intent == "faq_question" else {})
        | ({"tools": ["book_appointment"]} if intent == "booking" else {})
        for intent in intents or ("small_talk",)
    ]
    raw: dict[str, Any] = {
        "id": case_id,
        "family": "test",
        "message": f"message of {case_id}",
        "requests": requests,
        "source": "new",
    }
    if scheduling is not None:
        raw["scheduling"] = scheduling
    if history:
        raw["history"] = [
            {"role": "assistant", "text": "Our clinic opens at 8am."},
            {"role": "user", "text": "ok"},
        ]
    return Case.model_validate(raw)


def _configured(**overrides: Any) -> dict[str, Any]:
    return {
        "event": "service.configured",
        "level": "info",
        "timestamp": "2026-09-14T09:00:00Z",
        **_CONDITIONS,
        **overrides,
    }


def _append(log: Path, *lines: dict[str, Any] | str) -> None:
    with log.open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write((line if isinstance(line, str) else json.dumps(line)) + "\n")


def _trace_of(message_id: str) -> str:
    """The trace id the stub service logs for the turn that stored `message_id`."""
    return f"TRACE-{message_id}"


@dataclass
class Attempt:
    """What one attempt at a case does, as the stub stack plays it."""

    outcome: str = "done"
    intents: tuple[str, ...] = ("small_talk",)
    cap_bound: bool = False
    logged: bool = True
    restart: dict[str, Any] | None = None
    status: int = 404
    seconds: float = 1.5
    # Appointments the turn leaves behind for its patient, as "YYYY-MM-DDTHH:MM" starts
    # with William Osler, whatever the turn's outcome.
    books: tuple[str, ...] = ()
    # Whether the turn cancels every appointment its patient holds standing, whatever
    # the turn's outcome.
    cancels: bool = False
    # The booking tools the turn logs a `booking.tool_called` event for, in order.
    tools: tuple[str, ...] = ()
    # For a `no_answer` or `protocol_error` turn - the latter stores its patient
    # message, then breaks the stream's contract mid-answer: the poll of its thread,
    # after the turn's own read, on which the thread first shows the turn ended - None
    # for never - and how: a stored "reply", or the patient message marked
    # "assistant_failed".
    settles_after: int | None = 1
    settles_with: str = "reply"
    # The `outcome` of each `turn.completed` the turn logs, in order: none, or several,
    # break the service's once-per-turn rule.
    completed: tuple[str, ...] = ("faq",)
    # The `trace_id` of each `turn.traced` the turn logs in place of the stub service's
    # own one, when set: several, or one that is no string, break the log contract.
    traced_as: tuple[Any, ...] | None = None


@dataclass
class FakeStack:
    """The chat service as the run driver sees it, scripted per case."""

    log: Path
    artifacts: Path
    scripts: dict[str, list[Attempt]] = field(default_factory=dict)
    json_log: bool = True
    corpus_edit: bool = False
    entry_id_base: int = 100
    roster: frozenset[str] = frozenset({"William Osler", "Andreas Vesalius"})
    patientless: bool = False
    unreadable_post_state: set[str] = field(default_factory=set)
    failing_release: set[str] = field(default_factory=set)
    appointments: dict[str, list[AppointmentState]] = field(default_factory=dict)
    # A `service.configured` line logged during the n-th call of a stack method.
    restarts_during: dict[tuple[str, int], dict[str, Any]] = field(default_factory=dict)
    # An error the n-th call of a stack method raises, once its restart is logged.
    failures_during: dict[tuple[str, int], Exception] = field(default_factory=dict)
    # Lines the log is truncated in place to during the n-th call of a stack method,
    # before its restart is logged - what `dev-services.sh`'s `> "$log"` does.
    replacements_during: dict[tuple[str, int], list[dict[str, Any] | str]] = field(
        default_factory=dict
    )
    # A body the n-th `read_thread` call's `GET` answers 200 with, read by the real
    # thread reader rather than the scripted thread.
    thread_bodies: dict[int, bytes] = field(default_factory=dict)
    # The patients the chat database holds for the run session's chats.
    session_patient_ids: list[str] = field(default_factory=list)
    calls: list[tuple[Any, ...]] = field(default_factory=list)
    # Every posted message, with the chat it was posted to, in order.
    messages: list[tuple[str, str]] = field(default_factory=list)
    # What each posted turn asked of its trace, in posting order.
    tracings: list[TurnTracing] = field(default_factory=list)
    # Whether the service exports traces: each logged turn then logs `turn.traced`.
    tracing_enabled: bool = False
    timeline: list[tuple[Any, ...]] = field(default_factory=list)
    elapsed: float = 0.0
    _chats: int = 0
    # Every turn a chat stored, oldest first: a reply case's chat holds two.
    _threads: dict[str, list[ThreadRead]] = field(default_factory=dict)
    _chat_case: dict[str, str] = field(default_factory=dict)
    _last_posted: str | None = None
    _patient_case: dict[str, str] = field(default_factory=dict)
    _call_counts: dict[str, int] = field(default_factory=dict)
    # A chat's unanswered turn still running: its index in the chat's turns, its
    # script, its late reply, and how many times its thread was read since it posted.
    _running: dict[str, tuple[int, Attempt, StoredMessage, int]] = field(
        default_factory=dict
    )

    def _maybe_restart(self, method: str) -> None:
        number = self._call_counts.get(method, 0) + 1
        self._call_counts[method] = number
        replacement = self.replacements_during.get((method, number))
        if replacement is not None:
            with self.log.open("r+b") as handle:
                handle.truncate(0)
            _append(self.log, *replacement)
        restart = self.restarts_during.get((method, number))
        if restart is not None:
            _append(self.log, restart)
        failure = self.failures_during.get((method, number))
        if failure is not None:
            raise failure

    def timer(self) -> float:
        return self.elapsed

    async def sleep(self, seconds: float) -> None:
        self.timeline.append(("sleep", seconds))
        self.elapsed += seconds

    def _log_service_line(self, event: str, **fields: Any) -> None:
        if self.json_log:
            _append(self.log, {"event": event, "level": "info", **fields})
        else:
            _append(self.log, f"\x1b[2m2026-09-14\x1b[0m [info] {event}")

    async def open_session(self) -> OpenedSession:
        self.calls.append(("open_session",))
        self._maybe_restart("open_session")
        self._log_service_line("chat.created", chat_id="first")
        return OpenedSession(
            session_id=_SESSION_ID, chat_id="01K5FIRSTCHAT0000000000000"
        )

    def restore_session(self, session_id: str) -> None:
        self.calls.append(("restore_session", session_id))

    async def new_chat(self) -> str:
        self._chats += 1
        chat_id = f"01K5CHAT{self._chats:018d}"
        self.calls.append(("new_chat", chat_id))
        self._maybe_restart("new_chat")
        self.timeline.append(("new_chat", chat_id))
        _append(self.log, 'INFO:     127.0.0.1:1 - "POST /chats HTTP/1.1" 201 Created')
        self._log_service_line("chat.created", chat_id=chat_id)
        return chat_id

    async def live_corpus(self) -> list[FaqEntry]:
        self.calls.append(("live_corpus",))
        self._maybe_restart("live_corpus")
        return [
            FaqEntry(
                id=self.entry_id_base + entry.index,
                content=entry.text + (" edited" if self.corpus_edit and i == 1 else ""),
                created_at=_PLANTED_AT,
                updated_at=_PLANTED_AT,
            )
            for i, entry in enumerate(_PIN.entries)
        ]

    async def chat_identity(self, session_id: str, chat_id: str) -> ChatIdentity:
        self.calls.append(("chat_identity", session_id, chat_id))
        self.timeline.append(("chat_identity", chat_id))
        self._maybe_restart("chat_identity")
        patient = None if self.patientless else f"PAT-{chat_id}"
        return ChatIdentity(session_id=session_id, patient_id=patient)

    async def session_patients(self, session_id: str) -> list[str]:
        self.calls.append(("session_patients", session_id))
        return list(self.session_patient_ids)

    async def plant_history(
        self,
        session_id: str,
        chat_id: str,
        history: Sequence[HistoryEntry],
    ) -> list[str]:
        self.calls.append(("plant_history", session_id, chat_id, len(history)))
        self._maybe_restart("plant_history")
        return [f"PLANTED-{chat_id}-{i}" for i in range(len(history))]

    async def post_turn(
        self, chat_id: str, message: str, local_now: datetime, tracing: TurnTracing
    ) -> TurnResult:
        # A case's own message names it; a scripted reply is posted in the chat the
        # case's message was.
        if message.startswith("message of "):
            case_id = message.removeprefix("message of ")
            self._chat_case[chat_id] = case_id
        else:
            case_id = self._chat_case[chat_id]
        self.calls.append(("post_turn", case_id, chat_id, local_now))
        self.messages.append((chat_id, message))
        self.tracings.append(tracing)
        self.timeline.append(("post_turn", case_id, chat_id))
        self._maybe_restart("post_turn")
        self._patient_case[f"PAT-{chat_id}"] = case_id
        if self._last_posted is not None and self._last_posted != case_id:
            assert self._last_posted in self._recorded(), (
                f"{case_id} was posted before {self._last_posted} was written"
            )
        self._last_posted = case_id

        script = self.scripts.setdefault(case_id, [])
        attempt = script.pop(0) if script else Attempt()
        self.elapsed += attempt.seconds
        for start in attempt.books:
            self.appointments.setdefault(f"PAT-{chat_id}", []).append(
                _appointment(start, "standing")
            )
        if attempt.cancels:
            self.appointments[f"PAT-{chat_id}"] = [
                a.model_copy(update={"status": AppointmentStatus.CANCELLED})
                for a in self.appointments.get(f"PAT-{chat_id}", [])
            ]
        stored = len(self._threads.get(chat_id, []))
        suffix = "" if stored == 0 else f"-{stored + 1}"
        message_id = f"MSG-{chat_id}{suffix}"
        patient = StoredMessage(id=message_id, content=message)
        reply = StoredMessage(id=f"REPLY-{chat_id}{suffix}", content="Hello.")

        if attempt.outcome == "not_sent":
            return TurnNotConnected(detail="connection refused")
        if attempt.outcome == "refused":
            return TurnRefused(status_code=attempt.status, body="chat not found")

        if attempt.restart is not None:
            _append(self.log, attempt.restart)
        if attempt.logged and attempt.outcome != "silent":
            self._log_turn(message_id, message, attempt, traced=tracing.traced)

        terminal: dict[str, Any]
        thread = ThreadRead(patient_message=patient, assistant_message=reply)
        if attempt.outcome == "done":
            terminal = {"kind": "done", "payload": _DONE}
        elif attempt.outcome == "hand_off":
            terminal = {
                "kind": "done",
                "payload": {**_DONE, "answer_source": "hand_off"},
            }
        elif attempt.outcome == "silent":
            terminal = {"kind": "silent", "payload": {"type": "silent"}}
            thread = ThreadRead(
                patient_message=patient.model_copy(
                    update={"attention_mark": AttentionMark.UNANSWERED}
                ),
                assistant_message=None,
            )
        elif attempt.outcome == "cancelled":
            terminal = {"kind": "cancelled", "payload": {"type": "cancelled"}}
            thread = ThreadRead(patient_message=patient, assistant_message=None)
        elif attempt.outcome in ("no_answer", "protocol_error"):
            terminal = {"kind": "error", "payload": {}}
            thread = ThreadRead(patient_message=patient, assistant_message=None)
        elif attempt.outcome == "failed_no_reply":
            terminal = {"kind": "error", "payload": {}}
            thread = ThreadRead(
                patient_message=patient.model_copy(
                    update={"attention_mark": AttentionMark.ASSISTANT_FAILED}
                ),
                assistant_message=None,
            )
        elif attempt.outcome == "failed_with_reply":
            terminal = {"kind": "done", "payload": _DONE}
            thread = ThreadRead(
                patient_message=patient.model_copy(
                    update={"attention_mark": AttentionMark.ASSISTANT_FAILED}
                ),
                assistant_message=reply,
            )
        else:
            raise AssertionError(f"no such scripted outcome: {attempt.outcome}")
        self._threads.setdefault(chat_id, []).append(thread)
        if attempt.outcome in ("no_answer", "protocol_error"):
            index = len(self._threads[chat_id]) - 1
            self._running[chat_id] = (index, attempt, reply, -1)
        if attempt.outcome == "protocol_error":
            raise TurnProtocolError("a stream line is no event this build knows")
        interruption = "stream broke" if terminal["kind"] == "error" else None
        return TurnSent.model_validate(
            {"terminal": terminal, "interruption": interruption}
        )

    async def read_thread(
        self, chat_id: str, message: str, planted_ids: Sequence[str]
    ) -> ThreadRead:
        self.calls.append(("read_thread", chat_id, message, sorted(planted_ids)))
        self.timeline.append(("read_thread", chat_id))
        self._maybe_restart("read_thread")
        body = self.thread_bodies.get(self._call_counts["read_thread"])
        if body is not None:
            transport = httpx.MockTransport(
                lambda _r: httpx.Response(200, content=body)
            )
            async with httpx.AsyncClient(
                transport=transport, base_url="http://chat"
            ) as client:
                return await read_thread(client, chat_id, message, planted_ids)
        self._settle_on_this_read(chat_id)
        # What the real reader does: every message not skipped belongs to this turn,
        # and a turn leaves at most one patient message and one reply.
        turns = self._threads[chat_id]
        patients = [
            t.patient_message
            for t in turns
            if t.patient_message is not None and t.patient_message.id not in planted_ids
        ]
        replies = [
            t.assistant_message
            for t in turns
            if t.assistant_message is not None
            and t.assistant_message.id not in planted_ids
        ]
        if len(patients) > 1 or len(replies) > 1:
            raise TurnProtocolError(f"{len(patients)} patient messages besides history")
        if patients and patients[0].content != message:
            raise TurnProtocolError("the patient message is not the posted message")
        return ThreadRead(
            patient_message=patients[0] if patients else None,
            assistant_message=replies[0] if replies else None,
        )

    async def plant(
        self,
        session_id: str,
        patient_id: str,
        given: Sequence[AppointmentRef],
        clock: datetime,
    ) -> list[AppointmentState] | Unplantable:
        assert session_id == _SESSION_ID
        assert clock == DEFAULT_CLOCK
        self.timeline.append(("plant", patient_id, len(given)))
        self._maybe_restart("plant")
        unknown = [
            ref.practitioner for ref in given if ref.practitioner not in self.roster
        ]
        if unknown:
            return Unplantable(
                situation=UnplantableSituation.NOT_ON_ROSTER,
                detail=f"not on the roster: {unknown}",
            )
        planted = [
            _appointment(
                f"{clock.date() + timedelta(days=int(ref.day[:-1]))}T{ref.time}",
                "standing",
            )
            for ref in given
        ]
        self.appointments.setdefault(patient_id, []).extend(planted)
        return planted

    async def read_post_state(
        self, session_id: str, patient_id: str, clock: datetime
    ) -> list[AppointmentState]:
        self.timeline.append(("read_post_state", patient_id))
        self._maybe_restart("read_post_state")
        if self._patient_case.get(patient_id) in self.unreadable_post_state:
            raise SchedulingCallError("the scheduler is unreachable")
        return list(self.appointments.get(patient_id, []))

    async def release(
        self, session_id: str, patient_id: str, clock: datetime
    ) -> list[AppointmentState]:
        case_id = self._patient_case.get(patient_id)
        self.timeline.append(("release", patient_id))
        self._maybe_restart("release")
        assert case_id is None or case_id not in self._recorded(), (
            f"{case_id} was written before its patient was released"
        )
        held = self.appointments.get(patient_id, [])
        standing = [a for a in held if a.status == "standing"]
        cancelled = [
            a.model_copy(update={"status": AppointmentStatus.CANCELLED})
            for a in standing
        ]
        if patient_id in self.failing_release or case_id in self.failing_release:
            raise CleanupFailedError("the scheduler refused", cancelled=[])
        self.appointments[patient_id] = [
            a for a in held if a.status != "standing"
        ] + cancelled
        return cancelled

    def _settle_on_this_read(self, chat_id: str) -> None:
        running = self._running.get(chat_id)
        if running is None:
            return
        index, attempt, reply, reads = running
        reads += 1
        self._running[chat_id] = (index, attempt, reply, reads)
        if attempt.settles_after is None or reads < attempt.settles_after:
            return
        del self._running[chat_id]
        thread = self._threads[chat_id][index]
        assert thread.patient_message is not None
        if attempt.settles_with == "reply":
            settled = thread.model_copy(update={"assistant_message": reply})
        else:
            failed = thread.patient_message.model_copy(
                update={"attention_mark": AttentionMark.ASSISTANT_FAILED}
            )
            settled = thread.model_copy(update={"patient_message": failed})
        self._threads[chat_id][index] = settled

    def names(self) -> list[str]:
        return [entry[0] for entry in self.timeline]

    def _log_turn(
        self, message_id: str, message: str, attempt: Attempt, *, traced: bool
    ) -> None:
        # A browser user's turn interleaves with the case's, as the spec permits.
        other = {"turn_id": "SOMEONE-ELSE", "level": "info"}
        _append(
            self.log,
            {
                "event": "turn.message_received",
                "turn_id": message_id,
                "message": message,
                "message_ids_unified": [message_id],
            },
            {**other, "event": "turn.message_received", "message_ids_unified": ["X"]},
            {
                "event": "intent.classified",
                "turn_id": message_id,
                "segments": [
                    {"position": i, "intent": intent, "text": f"segment {i}"}
                    for i, intent in enumerate(attempt.intents)
                ],
                "cap_bound": attempt.cap_bound,
            },
            'INFO:     127.0.0.1:1 - "POST /chat HTTP/1.1" 200 OK',
            {**other, "event": "intent.classified", "segments": [], "cap_bound": False},
            *(
                {"event": "turn.traced", "turn_id": message_id, "trace_id": traced}
                for traced in self._traced_as(message_id, attempt, traced)
            ),
            *(
                [{**other, "event": "turn.traced", "trace_id": "SOMEONE-ELSES"}]
                if self.tracing_enabled
                else []
            ),
            *(
                {
                    "event": "booking.tool_called",
                    "turn_id": message_id,
                    "tool_name": tool,
                }
                for tool in attempt.tools
            ),
            *(
                {"event": "turn.completed", "turn_id": message_id, "outcome": outcome}
                for outcome in attempt.completed
            ),
        )

    def _traced_as(
        self, message_id: str, attempt: Attempt, requested: bool
    ) -> tuple[Any, ...]:
        if attempt.traced_as is not None:
            return attempt.traced_as
        exported = self.tracing_enabled and requested
        return (_trace_of(message_id),) if exported else ()

    def _recorded(self) -> list[str]:
        runs = [path for path in self.artifacts.iterdir() if path.is_dir()]
        return recorded_case_ids(runs[0]) if runs else []

    def posted(self) -> list[tuple[str, str, datetime]]:
        return [call[1:] for call in self.calls if call[0] == "post_turn"]


@pytest.fixture
def log(tmp_path: Path) -> Path:
    path = tmp_path / "chat.log"
    _append(
        path,
        "INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)",
        _configured(),
    )
    return path


@pytest.fixture
def artifacts(tmp_path: Path) -> Path:
    path = tmp_path / "evals"
    path.mkdir()
    return path


def _stack(log: Path, artifacts: Path, **kwargs: Any) -> FakeStack:
    return FakeStack(log=log, artifacts=artifacts, **kwargs)


def _appointment(starts_at: str, status: str) -> AppointmentState:
    return AppointmentState.model_validate(
        {
            "practitioner_full_name": "William Osler",
            "starts_at": starts_at,
            "status": status,
        }
    )


async def _drive(
    stack: FakeStack,
    cases: list[Case],
    *,
    ids: list[str] | None = None,
    pin: CorpusPin = _PIN,
    **kwargs: Any,
) -> Path:
    # Every wait is played on the stack's own clock, so no test spends real time.
    kwargs.setdefault("settle", SettleWait(sleep=stack.sleep))
    return await drive_run(
        cases,
        select(cases, ids=ids),
        stack=stack,
        log_path=stack.log,
        pin=pin,
        artifacts_dir=stack.artifacts,
        timer=stack.timer,
        **kwargs,
    )


async def _resume(stack: FakeStack, run_dir: Path, cases: list[Case]) -> Path:
    return await resume_run(
        run_dir,
        cases,
        stack=stack,
        log_path=stack.log,
        pin=_PIN,
        timer=stack.timer,
        settle=SettleWait(sleep=stack.sleep),
    )


def _case_run(run_dir: Path, case_id: str) -> CaseRun:
    return read_case(run_dir, case_id)


_THREE = [_case("G001"), _case("G002"), _case("G003")]


# --- ordering and the clock ---------------------------------------------------------


async def test_cases_are_driven_strictly_one_after_another(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts)

    run_dir = await _drive(stack, _THREE)

    assert [case_id for case_id, _chat, _now in stack.posted()] == [
        "G001",
        "G002",
        "G003",
    ]
    assert recorded_case_ids(run_dir) == ["G001", "G002", "G003"]


async def test_every_turn_is_sent_the_same_pinned_clock_by_default(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts)

    run_dir = await _drive(stack, _THREE)

    assert DEFAULT_CLOCK == datetime(2026, 3, 2, 8, 0, 0)
    assert {now for _case_id, _chat, now in stack.posted()} == {DEFAULT_CLOCK}
    assert read_run(run_dir).clock == DEFAULT_CLOCK


async def test_each_case_runs_in_its_own_chat(log: Path, artifacts: Path) -> None:
    stack = _stack(log, artifacts)

    run_dir = await _drive(stack, _THREE)

    chats = [chat for _case_id, chat, _now in stack.posted()]
    assert len(set(chats)) == 3
    assert [_case_run(run_dir, c).chat_id for c in ("G001", "G002", "G003")] == chats
    assert _case_run(run_dir, "G001").patient_id == f"PAT-{chats[0]}"


async def test_history_is_planted_before_the_turn_and_excluded_from_the_thread_read(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts)

    await _drive(stack, [_case("G034", history=True)])

    names = [call[0] for call in stack.calls]
    assert names.index("plant_history") < names.index("post_turn")
    plant = next(call for call in stack.calls if call[0] == "plant_history")
    assert plant[1] == _SESSION_ID
    assert plant[3] == 2
    read = next(call for call in stack.calls if call[0] == "read_thread")
    assert read[3] == [f"PLANTED-{plant[2]}-0", f"PLANTED-{plant[2]}-1"]


# --- run.json -----------------------------------------------------------------------


async def test_run_json_records_selection_digests_conditions_and_the_session(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts)
    cases = [*_THREE, _case("G004")]

    run_dir = await _drive(stack, cases, ids=["G001", "G003"])

    run = read_run(run_dir)
    assert run_dir.parent == artifacts
    assert run_dir.name == run.run_id
    assert run.session_id == _SESSION_ID
    assert run.selection == select(cases, ids=["G001", "G003"])
    assert run.labels == label_digests([cases[0], cases[2]])
    assert run.conditions.model_dump() == _CONDITIONS
    assert run.corpus.matched
    assert run.entry_ids == {e.id: 100 + e.index for e in _PIN.entries}
    assert run.cases == ["G001", "G003"]
    assert run.finished_at is not None


async def test_the_conditions_are_the_latest_settings_event_before_the_run_began(
    log: Path, artifacts: Path
) -> None:
    _append(log, _configured(rerank_floor=0.61))
    stack = _stack(log, artifacts)

    run_dir = await _drive(stack, [_case("G001")])

    assert read_run(run_dir).conditions.rerank_floor == 0.61


async def test_a_log_with_no_settings_event_stops_before_the_first_turn(
    tmp_path: Path, artifacts: Path
) -> None:
    empty = tmp_path / "empty.log"
    _append(empty, "INFO:     Uvicorn running on http://127.0.0.1:8000")
    stack = _stack(empty, artifacts)

    with pytest.raises(ConditionsMissingError):
        await _drive(stack, [_case("G001")])

    assert stack.posted() == []
    assert list(artifacts.iterdir()) == []


async def test_drive_seconds_and_elapsed_seconds_count_every_attempt(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log,
        artifacts,
        scripts={
            "G001": [Attempt(outcome="not_sent", seconds=2.0), Attempt(seconds=3.0)],
            "G002": [Attempt(seconds=0.5)],
        },
    )

    run_dir = await _drive(stack, _THREE[:2])

    assert _case_run(run_dir, "G001").elapsed_seconds == 5.0
    assert _case_run(run_dir, "G002").elapsed_seconds == 0.5
    assert read_run(run_dir).drive_seconds == 5.5


# --- what stops a run before its first turn -----------------------------------------


async def test_a_corpus_mismatch_stops_before_any_turn_and_writes_no_run(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts, corpus_edit=True)

    with pytest.raises(CorpusMismatchError, match=_PIN.entries[1].id):
        await _drive(stack, _THREE)

    assert stack.posted() == []
    assert not any(artifacts.rglob("run.json"))


async def test_a_service_not_logging_json_stops_before_any_turn(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts, json_log=False)

    with pytest.raises(JsonLogMissingError):
        await _drive(stack, _THREE)

    assert stack.posted() == []
    assert not any(artifacts.rglob("cases"))


# --- retries ------------------------------------------------------------------------


async def test_an_unmeasured_attempt_is_driven_again_in_a_fresh_chat(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts, scripts={"G001": [Attempt(outcome="not_sent")]})

    run_dir = await _drive(stack, [_case("G001")])

    chats = [chat for _case_id, chat, _now in stack.posted()]
    assert len(chats) == 2
    assert chats[0] != chats[1]
    case_run = _case_run(run_dir, "G001")
    assert case_run.attempts == 2
    assert case_run.chat_id == chats[1]
    assert case_run.excluded is None


async def test_a_case_still_unmeasured_after_three_attempts_is_a_run_error(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts, scripts={"G001": [Attempt(outcome="not_sent")] * 4})

    run_dir = await _drive(stack, [_case("G001"), _case("G002")])

    assert len([p for p in stack.posted() if p[0] == "G001"]) == 3
    case_run = _case_run(run_dir, "G001")
    assert case_run.attempts == 3
    assert case_run.excluded is ExclusionReason.RUN_ERROR
    assert _case_run(run_dir, "G002").excluded is None


async def test_an_assistant_failed_turn_with_no_reply_is_driven_once_as_a_run_error(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log, artifacts, scripts={"G001": [Attempt(outcome="failed_no_reply")]}
    )

    run_dir = await _drive(stack, [_case("G001")])

    assert len(stack.posted()) == 1
    case_run = _case_run(run_dir, "G001")
    assert (case_run.attempts, case_run.excluded) == (1, ExclusionReason.RUN_ERROR)


async def test_an_assistant_failed_turn_that_stored_a_reply_is_driven_once_and_kept(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log, artifacts, scripts={"G001": [Attempt(outcome="failed_with_reply")]}
    )

    run_dir = await _drive(stack, [_case("G001")])

    assert len(stack.posted()) == 1
    case_run = _case_run(run_dir, "G001")
    assert (case_run.attempts, case_run.excluded) == (1, None)
    assert case_run.assistant_message is not None
    assert case_run.patient_message is not None
    assert case_run.patient_message.attention_mark == "assistant_failed"


async def test_a_harness_fault_stops_the_run_unretried_and_writes_no_case(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log, artifacts, scripts={"G002": [Attempt(outcome="refused", status=422)]}
    )

    with pytest.raises(HarnessFaultError) as raised:
        await _drive(stack, _THREE)

    assert raised.value.status_code == 422
    assert "chat not found" in str(raised.value)
    assert [p[0] for p in stack.posted()] == ["G001", "G002"]
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == ["G001"]


# --- what a case records ------------------------------------------------------------


async def test_a_measured_case_records_its_turn_and_only_its_own_events(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log,
        artifacts,
        scripts={
            "G001": [Attempt(intents=("faq_question", "small_talk"), cap_bound=True)]
        },
    )

    run_dir = await _drive(stack, [_case("G001", "faq_question", "small_talk")])

    case_run = _case_run(run_dir, "G001")
    assert case_run.terminal is not None
    assert case_run.terminal.kind is TerminalKind.DONE
    assert case_run.segments is not None
    assert [s.intent.value for s in case_run.segments.segments] == [
        "faq_question",
        "small_talk",
    ]
    assert case_run.segments.cap_bound is True
    assert case_run.events is not None
    assert {event.get("turn_id") for event in case_run.events} == {
        f"MSG-{case_run.chat_id}"
    }
    assert case_run.excluded is None


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        ("hand_off", ExclusionReason.HANDED_OFF_TURN),
        ("silent", ExclusionReason.SILENCED_TURN),
        ("cancelled", ExclusionReason.CANCELLED_TURN),
    ],
)
async def test_the_terminal_event_decides_the_turn_shape_exclusions(
    log: Path, artifacts: Path, outcome: str, reason: ExclusionReason
) -> None:
    stack = _stack(log, artifacts, scripts={"G001": [Attempt(outcome=outcome)]})

    run_dir = await _drive(stack, [_case("G001")])

    case_run = _case_run(run_dir, "G001")
    assert case_run.excluded is reason
    assert case_run.attempts == 1


async def test_a_turn_whose_events_cannot_be_selected_is_a_missing_log_slice(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts, scripts={"G001": [Attempt(logged=False)]})

    run_dir = await _drive(stack, [_case("G001")])

    case_run = _case_run(run_dir, "G001")
    assert case_run.excluded is ExclusionReason.MISSING_LOG_SLICE
    assert case_run.events is None
    assert case_run.segments is None


async def test_a_produced_classification_failed_is_a_run_error(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log, artifacts, scripts={"G001": [Attempt(intents=("classification_failed",))]}
    )

    run_dir = await _drive(stack, [_case("G001")])

    assert _case_run(run_dir, "G001").excluded is ExclusionReason.RUN_ERROR


async def test_a_restart_with_other_settings_inside_a_slice_stops_without_the_case(
    log: Path, artifacts: Path
) -> None:
    changed = _configured(similarity_floor=0.4)
    stack = _stack(log, artifacts, scripts={"G002": [Attempt(restart=changed)]})

    with pytest.raises(ServiceRestartedError, match="G002"):
        await _drive(stack, _THREE)

    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == ["G001"]
    assert read_run(run_dir).cases == ["G001"]


async def test_a_restart_with_the_same_settings_inside_a_slice_is_recorded(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts, scripts={"G001": [Attempt(restart=_configured())]})

    run_dir = await _drive(stack, [_case("G001")])

    assert _case_run(run_dir, "G001").excluded is None


async def test_the_run_never_deletes_its_session(log: Path, artifacts: Path) -> None:
    stack = _stack(log, artifacts)

    await _drive(stack, _THREE)

    assert {call[0] for call in stack.calls} <= {
        "open_session",
        "new_chat",
        "live_corpus",
        "chat_identity",
        "plant_history",
        "post_turn",
        "read_thread",
    }


# --- traces (014) --------------------------------------------------------------------


async def test_every_turn_names_its_run_and_its_case(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts, scripts={"G042": _confirmed_cancel()})

    run_dir = await _drive(stack, [_case("G001"), _reply_case("G042")])

    run_id = read_run(run_dir).run_id
    # The reply turn is the case's too, so it names the same case.
    assert [(t.run_id, t.case_id) for t in stack.tracings] == [
        (run_id, "G001"),
        (run_id, "G042"),
        (run_id, "G042"),
    ]


async def test_a_driven_case_names_the_trace_of_its_turn(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts, tracing_enabled=True)

    run_dir = await _drive(stack, [_case("G001"), _case("G002")])

    for case_id in ("G001", "G002"):
        case_run = _case_run(run_dir, case_id)
        assert case_run.patient_message is not None
        message_id = case_run.patient_message.id
        assert case_run.traces == {message_id: _trace_of(message_id)}


async def test_a_reply_case_names_the_trace_of_each_of_its_turns(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log, artifacts, scripts={"G042": _confirmed_cancel()}, tracing_enabled=True
    )

    run_dir = await _drive(stack, [_reply_case("G042")])

    case_run = _case_run(run_dir, "G042")
    chat = case_run.chat_id
    assert case_run.traces == {
        f"MSG-{chat}": _trace_of(f"MSG-{chat}"),
        f"MSG-{chat}-2": _trace_of(f"MSG-{chat}-2"),
    }


async def test_a_turn_the_service_did_not_trace_names_no_trace(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts)

    run_dir = await _drive(stack, [_case("G001")])

    assert _case_run(run_dir, "G001").traces == {}


async def test_a_case_re_driven_on_resume_names_only_the_traces_it_drove(
    log: Path, artifacts: Path
) -> None:
    # G002's first attempt is posted and traced, then a restart under other settings
    # stops the run before its case is written; the resumed run drives it again.
    changed = _configured(similarity_floor=0.4)
    stack = _stack(
        log,
        artifacts,
        scripts={"G002": [Attempt(restart=changed)]},
        tracing_enabled=True,
    )
    with pytest.raises(ServiceRestartedError):
        await _drive(stack, _THREE)
    (run_dir,) = artifacts.iterdir()
    (abandoned,) = [chat for case_id, chat, _now in stack.posted() if case_id == "G002"]
    _append(log, _configured())

    resumed = _stack(log, artifacts, tracing_enabled=True, _chats=10)
    await _resume(resumed, run_dir, _THREE)

    case_run = _case_run(run_dir, "G002")
    assert case_run.chat_id != abandoned
    assert case_run.patient_message is not None
    message_id = case_run.patient_message.id
    assert case_run.traces == {message_id: _trace_of(message_id)}


async def test_a_trace_breaking_the_log_contract_stops_a_case_with_no_fixture(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts, scripts={"G001": [Attempt(traced_as=("a", "b"))]})

    with pytest.raises(ValidationError):
        await _drive(stack, [_case("G001"), _case("G002")])

    assert [p[0] for p in stack.posted()] == ["G001"]
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == []


async def test_a_fixture_case_whose_trace_breaks_the_record_is_outcome_unknown(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts, scripts={"G042": [Attempt(traced_as=(7,))]})

    with pytest.raises(ValidationError):
        await _drive(stack, [_booking_case("G042"), _case("G043")])

    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == ["G042"]
    case_run = _case_run(run_dir, "G042")
    assert case_run.excluded is ExclusionReason.OUTCOME_UNKNOWN
    assert case_run.traces == {}


@pytest.mark.parametrize("shape", ["fixture", "no-fixture", "reply-turn"])
async def test_a_turn_whose_outcome_is_unknown_still_names_its_trace(
    log: Path, artifacts: Path, shape: str
) -> None:
    never_settles = Attempt(outcome="no_answer", settles_after=None)
    case, script = {
        "fixture": (_booking_case("G042"), [never_settles]),
        "no-fixture": (_case("G042"), [never_settles]),
        "reply-turn": (_reply_case("G042"), [Attempt(), never_settles]),
    }[shape]
    turns = len(script)
    stack = _stack(log, artifacts, scripts={"G042": script}, tracing_enabled=True)
    settle = SettleWait(timeout_seconds=20.0, interval_seconds=5.0, sleep=stack.sleep)

    with pytest.raises(TurnUnsettledError):
        await _drive(stack, [case, _case("G043")], settle=settle)

    (run_dir,) = artifacts.iterdir()
    case_run = _case_run(run_dir, "G042")
    assert case_run.excluded is ExclusionReason.OUTCOME_UNKNOWN
    posted = [f"MSG-{case_run.chat_id}", f"MSG-{case_run.chat_id}-2"][:turns]
    assert case_run.traces == {
        message_id: _trace_of(message_id) for message_id in posted
    }


@pytest.mark.parametrize("reply", [False, True])
async def test_a_turn_whose_segmentation_breaks_the_record_still_names_its_trace(
    log: Path, artifacts: Path, reply: bool
) -> None:
    broken = Attempt(intents=("no_such_intent",))
    case = _reply_case("G042") if reply else _booking_case("G042")
    script = [Attempt(), broken] if reply else [broken]
    turns = len(script)
    stack = _stack(log, artifacts, scripts={"G042": script}, tracing_enabled=True)

    with pytest.raises(ValidationError):
        await _drive(stack, [case, _case("G043")])

    (run_dir,) = artifacts.iterdir()
    case_run = _case_run(run_dir, "G042")
    assert case_run.excluded is ExclusionReason.OUTCOME_UNKNOWN
    posted = [f"MSG-{case_run.chat_id}", f"MSG-{case_run.chat_id}-2"][:turns]
    assert case_run.traces == {
        message_id: _trace_of(message_id) for message_id in posted
    }


@pytest.mark.parametrize(
    "never_sent",
    [Attempt(outcome="not_sent"), Attempt(outcome="refused", status=503)],
    ids=["not-connected", "refused-before-a-stream"],
)
async def test_a_case_whose_every_attempt_was_never_sent_names_no_trace(
    log: Path, artifacts: Path, never_sent: Attempt
) -> None:
    # Nothing reached the pipeline, so no turn ran and no thread names one to select.
    stack = _stack(
        log,
        artifacts,
        scripts={"G001": [never_sent] * MAX_ATTEMPTS},
        tracing_enabled=True,
    )

    run_dir = await _drive(stack, [_case("G001")])

    case_run = _case_run(run_dir, "G001")
    assert case_run.excluded is ExclusionReason.RUN_ERROR
    assert case_run.traces == {}


# --- whether a run was traced (014, US3) --------------------------------------------


@pytest.mark.parametrize(
    ("stated", "requested", "expected"),
    [
        ({"tracing_enabled": True}, True, RunTracing.TRACED),
        ({"tracing_enabled": False}, True, RunTracing.UNTRACED_SERVICE_OFF),
        ({}, True, RunTracing.UNTRACED_SERVICE_OFF),
        ({"tracing_enabled": True}, False, RunTracing.UNTRACED_BY_REQUEST),
        ({"tracing_enabled": False}, False, RunTracing.UNTRACED_BY_REQUEST),
        ({}, False, RunTracing.UNTRACED_BY_REQUEST),
    ],
)
async def test_a_run_records_whether_it_was_traced_and_why_not(
    log: Path,
    artifacts: Path,
    stated: dict[str, Any],
    requested: bool,
    expected: RunTracing,
) -> None:
    _append(log, _configured(**stated))
    stack = _stack(log, artifacts)

    run_dir = await _drive(stack, [_case("G001")], tracing_requested=requested)

    assert read_run(run_dir).tracing is expected


async def test_a_run_is_traced_unless_it_asks_not_to_be(
    log: Path, artifacts: Path
) -> None:
    _append(log, _configured(tracing_enabled=True))
    stack = _stack(log, artifacts)

    run_dir = await _drive(stack, [_case("G001")])

    assert read_run(run_dir).tracing is RunTracing.TRACED
    assert [t.traced for t in stack.tracings] == [True]


async def test_an_untraced_run_sends_every_turn_off(log: Path, artifacts: Path) -> None:
    _append(log, _configured(tracing_enabled=True))
    stack = _stack(
        log, artifacts, scripts={"G042": _confirmed_cancel()}, tracing_enabled=True
    )

    run_dir = await _drive(
        stack, [_case("G001"), _reply_case("G042")], tracing_requested=False
    )

    assert [t.traced for t in stack.tracings] == [False, False, False]
    # Still filed under the run and the case: harmless when nothing is exported.
    run_id = read_run(run_dir).run_id
    assert {(t.run_id, t.case_id) for t in stack.tracings} == {
        (run_id, "G001"),
        (run_id, "G042"),
    }
    assert _case_run(run_dir, "G001").traces == {}
    assert _case_run(run_dir, "G042").traces == {}


@pytest.mark.parametrize(
    ("started", "restarted"),
    [(True, False), (False, True), (None, True)],
)
async def test_a_restart_changing_whether_the_service_traces_stops_the_run(
    log: Path, artifacts: Path, started: bool | None, restarted: bool
) -> None:
    if started is not None:
        _append(log, _configured(tracing_enabled=started))
    stack = _stack(
        log,
        artifacts,
        restarts_during={("new_chat", 2): _configured(tracing_enabled=restarted)},
    )

    with pytest.raises(ServiceRestartedError, match="tracing_enabled"):
        await _drive(stack, _THREE)

    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == ["G001"]


async def test_a_restart_changing_whether_the_service_traces_spares_an_untraced_run(
    log: Path, artifacts: Path
) -> None:
    _append(log, _configured(tracing_enabled=True))
    stack = _stack(
        log,
        artifacts,
        restarts_during={("new_chat", 2): _configured(tracing_enabled=False)},
    )

    run_dir = await _drive(stack, _THREE, tracing_requested=False)

    assert recorded_case_ids(run_dir) == ["G001", "G002", "G003"]


async def test_a_restart_stating_the_same_settings_and_tracing_is_passed_over(
    log: Path, artifacts: Path
) -> None:
    _append(log, _configured(tracing_enabled=True))
    stack = _stack(
        log,
        artifacts,
        restarts_during={("new_chat", 2): _configured(tracing_enabled=True)},
    )

    run_dir = await _drive(stack, _THREE)

    assert recorded_case_ids(run_dir) == ["G001", "G002", "G003"]
    assert read_run(run_dir).tracing is RunTracing.TRACED


async def test_a_restart_with_other_settings_still_stops_an_untraced_run(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts, restarts_during={("new_chat", 2): _OTHER_SETTINGS})

    with pytest.raises(ServiceRestartedError, match="similarity_floor"):
        await _drive(stack, _THREE, tracing_requested=False)


@pytest.mark.parametrize(
    ("started", "now"),
    [(True, False), (True, None), (False, True), (None, True)],
)
async def test_a_resumed_run_refuses_a_service_that_now_traces_otherwise(
    log: Path, artifacts: Path, started: bool | None, now: bool | None
) -> None:
    if started is not None:
        _append(log, _configured(tracing_enabled=started))
    run_dir = await _interrupted_run(log, artifacts)
    _append(log, _configured() if now is None else _configured(tracing_enabled=now))
    stack = _stack(log, artifacts)

    with pytest.raises(ConditionsChangedError, match="tracing_enabled"):
        await _resume(stack, run_dir, _THREE)

    assert stack.posted() == []


async def test_a_resumed_traced_run_goes_on_tracing(log: Path, artifacts: Path) -> None:
    _append(log, _configured(tracing_enabled=True))
    run_dir = await _interrupted_run(log, artifacts)
    _append(log, _configured(tracing_enabled=True))
    stack = _stack(log, artifacts)

    await _resume(stack, run_dir, _THREE)

    assert recorded_case_ids(run_dir) == ["G001", "G002", "G003"]
    assert [t.traced for t in stack.tracings] == [True, True]
    assert read_run(run_dir).tracing is RunTracing.TRACED


@pytest.mark.parametrize("now", [True, False, None])
async def test_a_resumed_untraced_run_resumes_and_still_sends_every_turn_off(
    log: Path, artifacts: Path, now: bool | None
) -> None:
    _append(log, _configured(tracing_enabled=True))
    run_dir = await _interrupted_run(log, artifacts, tracing_requested=False)
    _append(log, _configured() if now is None else _configured(tracing_enabled=now))
    stack = _stack(log, artifacts)

    await _resume(stack, run_dir, _THREE)

    assert recorded_case_ids(run_dir) == ["G001", "G002", "G003"]
    assert [t.traced for t in stack.tracings] == [False, False]
    assert read_run(run_dir).tracing is RunTracing.UNTRACED_BY_REQUEST


# --- resume -------------------------------------------------------------------------


async def _interrupted_run(log: Path, artifacts: Path, **kwargs: Any) -> Path:
    """A run of G001-G003 stopped by a harness fault at G002, with G001 recorded."""
    stack = _stack(
        log, artifacts, scripts={"G002": [Attempt(outcome="refused", status=404)]}
    )
    with pytest.raises(HarnessFaultError):
        await _drive(stack, _THREE, **kwargs)
    (run_dir,) = artifacts.iterdir()
    return run_dir


async def test_a_resumed_run_drives_only_what_is_not_recorded(
    log: Path, artifacts: Path
) -> None:
    run_dir = await _interrupted_run(log, artifacts)
    stack = _stack(log, artifacts)

    await _resume(stack, run_dir, _THREE)

    assert [p[0] for p in stack.posted()] == ["G002", "G003"]
    assert recorded_case_ids(run_dir) == ["G001", "G002", "G003"]
    assert read_run(run_dir).cases == ["G001", "G002", "G003"]


async def test_a_resumed_run_reuses_the_session_clock_selection_and_entry_ids(
    log: Path, artifacts: Path
) -> None:
    clock = datetime(2026, 3, 9, 8, 0, 0)
    run_dir = await _interrupted_run(log, artifacts, clock=clock)
    before = read_run(run_dir)
    stack = _stack(log, artifacts)

    await _resume(stack, run_dir, _THREE)

    assert ("restore_session", before.session_id) in stack.calls
    assert ("open_session",) not in stack.calls
    assert {now for _case_id, _chat, now in stack.posted()} == {clock}
    after = read_run(run_dir)
    assert after.selection == before.selection
    assert after.entry_ids == before.entry_ids
    assert after.run_id == before.run_id
    assert after.clock == clock


async def test_a_resumed_run_verifies_the_corpus_pin_again_before_its_first_turn(
    log: Path, artifacts: Path
) -> None:
    run_dir = await _interrupted_run(log, artifacts)
    stack = _stack(log, artifacts, corpus_edit=True)

    with pytest.raises(CorpusMismatchError):
        await _resume(stack, run_dir, _THREE)

    assert ("live_corpus",) in stack.calls
    assert stack.posted() == []


async def test_a_resumed_run_stops_when_the_service_now_states_other_settings(
    log: Path, artifacts: Path
) -> None:
    run_dir = await _interrupted_run(log, artifacts)
    _append(log, _configured(rerank_cap=4))
    stack = _stack(log, artifacts)

    with pytest.raises(ConditionsChangedError, match="rerank_cap"):
        await _resume(stack, run_dir, _THREE)

    assert stack.posted() == []


async def test_a_resumed_run_stops_when_the_services_settings_cannot_be_read(
    log: Path, artifacts: Path
) -> None:
    # A build that dropped a condition field is a changed condition, stopped as one.
    run_dir = await _interrupted_run(log, artifacts)
    _append(log, {k: v for k, v in _configured().items() if k != "rerank_cap"})
    stack = _stack(log, artifacts)

    with pytest.raises(ConditionsChangedError, match="rerank_cap") as stopped:
        await _resume(stack, run_dir, _THREE)

    assert isinstance(stopped.value.__cause__, ValidationError)
    assert stack.posted() == []


async def test_a_resumed_run_stops_when_a_selected_label_changed(
    log: Path, artifacts: Path
) -> None:
    run_dir = await _interrupted_run(log, artifacts)
    relabelled = [_THREE[0], _case("G002", "call_staff"), _THREE[2]]
    stack = _stack(log, artifacts)

    with pytest.raises(LabelsChangedError, match="G002"):
        await _resume(stack, run_dir, relabelled)

    assert stack.posted() == []


async def test_a_resumed_run_stops_before_a_turn_when_the_service_no_longer_logs_json(
    log: Path, artifacts: Path
) -> None:
    run_dir = await _interrupted_run(log, artifacts)
    stack = _stack(log, artifacts, json_log=False)

    with pytest.raises(JsonLogMissingError):
        await _resume(stack, run_dir, _THREE)

    assert stack.posted() == []
    assert recorded_case_ids(run_dir) == ["G001"]


async def test_a_resumed_run_stops_when_the_pinned_texts_map_to_other_entry_ids(
    log: Path, artifacts: Path
) -> None:
    run_dir = await _interrupted_run(log, artifacts)
    stack = _stack(log, artifacts, entry_id_base=500)

    with pytest.raises(EntryIdsChangedError):
        await _resume(stack, run_dir, _THREE)

    assert stack.posted() == []


async def test_a_resumed_run_counts_a_case_file_run_json_never_recorded(
    log: Path, artifacts: Path
) -> None:
    run_dir = await _interrupted_run(log, artifacts)
    stopped = read_run(run_dir)
    elapsed = _case_run(run_dir, "G001").elapsed_seconds
    write_run(run_dir, stopped.model_copy(update={"cases": [], "drive_seconds": 0.0}))
    stack = _stack(log, artifacts, scripts={"G002": [Attempt(seconds=2.0)]})

    await _resume(stack, run_dir, _THREE)

    assert [p[0] for p in stack.posted()] == ["G002", "G003"]
    resumed = read_run(run_dir)
    assert resumed.cases == ["G001", "G002", "G003"]
    assert resumed.drive_seconds == elapsed + 2.0 + 1.5


# --- scheduling fixtures and cleanup --------------------------------------------------

_TOMORROW_AT_TEN = {"practitioner": "William Osler", "day": "+1d", "time": "10:00"}
_CANCEL_TOMORROW = {
    "given": [_TOMORROW_AT_TEN],
    "expect": [{**_TOMORROW_AT_TEN, "status": "cancelled"}],
}


def _booking_case(case_id: str, scheduling: dict[str, Any] | None = None) -> Case:
    return _case(
        case_id,
        "booking",
        scheduling=scheduling if scheduling is not None else _CANCEL_TOMORROW,
    )


def _between(names: list[str], first: str, then: str) -> list[str]:
    start = names.index(first)
    return names[start : names.index(then, start) + 1]


@pytest.fixture
def offsets(monkeypatch: pytest.MonkeyPatch) -> list[FakeStack]:
    """Record every log offset the driver takes onto the stack's timeline."""
    stacks: list[FakeStack] = []
    real = run_module.log_offset

    def recorded(path: Path) -> int:
        for stack in stacks:
            stack.timeline.append(("log_offset",))
        return real(path)

    monkeypatch.setattr(run_module, "log_offset", recorded)
    return stacks


async def test_a_fixture_is_planted_before_the_turns_log_offset_and_read_after_it(
    log: Path, artifacts: Path, offsets: list[FakeStack]
) -> None:
    stack = _stack(log, artifacts)
    offsets.append(stack)

    run_dir = await _drive(stack, [_booking_case("G042")])

    names = stack.names()
    assert _between(names, "chat_identity", "post_turn")[-3:] == [
        "plant",
        "log_offset",
        "post_turn",
    ]
    assert names.index("read_post_state") > names.index("read_thread")
    case_run = _case_run(run_dir, "G042")
    assert case_run.scheduling_after == [
        _appointment("2026-03-03T10:00:00", "standing")
    ]
    assert case_run.excluded is None


async def test_the_post_state_is_what_the_turn_left_behind(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log, artifacts, scripts={"G088": [Attempt(books=("2026-03-09T09:00",))]}
    )

    run_dir = await _drive(stack, [_booking_case("G088", {"given": [], "expect": []})])

    assert _case_run(run_dir, "G088").scheduling_after == [
        _appointment("2026-03-09T09:00:00", "standing")
    ]


async def test_an_unresolvable_fixture_is_recorded_without_posting_a_turn(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts, roster=frozenset({"Andreas Vesalius"}))

    run_dir = await _drive(stack, [_booking_case("G042"), _case("G043")])

    assert [p[0] for p in stack.posted()] == ["G043"]
    case_run = _case_run(run_dir, "G042")
    assert case_run.excluded is ExclusionReason.UNRESOLVABLE_FIXTURE
    assert (case_run.attempts, case_run.terminal, case_run.events) == (1, None, None)
    assert case_run.patient_id is not None
    assert "read_post_state" not in stack.names()
    assert ("release", case_run.patient_id) in stack.timeline


async def test_a_fixture_case_whose_chat_has_no_patient_is_unresolvable(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts, patientless=True)

    run_dir = await _drive(stack, [_booking_case("G042")])

    assert stack.posted() == []
    assert _case_run(run_dir, "G042").excluded is ExclusionReason.UNRESOLVABLE_FIXTURE
    assert {"plant", "release"}.isdisjoint(stack.names())


async def test_an_unresolvable_fixture_records_why_it_would_not_plant(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts, roster=frozenset({"Andreas Vesalius"}))

    run_dir = await _drive(stack, [_booking_case("G042")])

    assert _case_run(run_dir, "G042").unplantable == Unplantable(
        situation=UnplantableSituation.NOT_ON_ROSTER,
        detail="not on the roster: ['William Osler']",
    )


async def test_a_fixture_case_whose_chat_has_no_patient_records_that_situation(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts, patientless=True)

    run_dir = await _drive(stack, [_booking_case("G042")])

    unplantable = _case_run(run_dir, "G042").unplantable
    assert unplantable is not None
    assert unplantable.situation is UnplantableSituation.NO_PATIENT


async def test_a_planted_or_unfixtured_case_records_no_unplantable_detail(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts)

    run_dir = await _drive(stack, [_booking_case("G042"), _case("G043")])

    assert _case_run(run_dir, "G042").unplantable is None
    assert _case_run(run_dir, "G043").unplantable is None


async def test_a_case_with_no_fixture_plants_nothing_and_reads_no_post_state(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts)

    run_dir = await _drive(stack, [_case("G001")])

    assert {"plant", "read_post_state"}.isdisjoint(stack.names())
    assert _case_run(run_dir, "G001").scheduling_after is None


async def test_a_fixture_case_not_sent_is_driven_again_and_planted_for_the_new_patient(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log, artifacts, scripts={"G042": [Attempt(outcome="not_sent"), Attempt()]}
    )

    run_dir = await _drive(stack, [_booking_case("G042")])

    chats = [chat for _case_id, chat, _now in stack.posted()]
    assert len(chats) == 2
    plants = [entry[1] for entry in stack.timeline if entry[0] == "plant"]
    assert plants == [f"PAT-{chat}" for chat in chats]
    # The failed attempt's planted appointment is released before the retry plants.
    names = stack.names()
    first_release = stack.timeline.index(("release", f"PAT-{chats[0]}"))
    second_plant = [i for i, n in enumerate(names) if n == "plant"][1]
    assert first_release < second_plant
    case_run = _case_run(run_dir, "G042")
    assert (case_run.attempts, case_run.chat_id, case_run.excluded) == (
        2,
        chats[1],
        None,
    )


async def test_a_fixture_case_sent_with_no_answer_that_settles_is_scored_not_retried(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log,
        artifacts,
        scripts={"G042": [Attempt(outcome="no_answer", books=("2026-03-04T11:00",))]},
    )

    run_dir = await _drive(stack, [_booking_case("G042")])

    assert len(stack.posted()) == 1
    case_run = _case_run(run_dir, "G042")
    assert (case_run.attempts, case_run.excluded) == (1, None)
    assert case_run.scheduling_after == [
        _appointment("2026-03-03T10:00:00", "standing"),
        _appointment("2026-03-04T11:00:00", "standing"),
    ]
    assert case_run.cancelled_after == [
        _appointment("2026-03-03T10:00:00", "cancelled"),
        _appointment("2026-03-04T11:00:00", "cancelled"),
    ]


async def test_an_outcome_unknown_case_whose_post_state_cannot_be_read_stores_none(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log,
        artifacts,
        scripts={"G042": [Attempt(outcome="no_answer", settles_after=None)]},
        unreadable_post_state={"G042"},
    )

    with pytest.raises(TurnUnsettledError):
        await _drive(stack, [_booking_case("G042")])

    (run_dir,) = artifacts.iterdir()
    case_run = _case_run(run_dir, "G042")
    assert case_run.excluded is ExclusionReason.OUTCOME_UNKNOWN
    assert case_run.scheduling_after is None
    assert "read_post_state" in stack.names()


async def test_a_case_with_no_fixture_sent_with_no_answer_that_settles_is_not_retried(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts, scripts={"G001": [Attempt(outcome="no_answer")]})

    run_dir = await _drive(stack, [_case("G001")])

    assert len(stack.posted()) == 1
    assert (
        _case_run(run_dir, "G001").attempts,
        _case_run(run_dir, "G001").excluded,
    ) == (
        1,
        None,
    )


async def test_release_follows_the_post_state_read_and_precedes_the_case_file(
    log: Path, artifacts: Path
) -> None:
    # FakeStack.release itself asserts the case file is not yet written.
    stack = _stack(log, artifacts)

    run_dir = await _drive(stack, [_booking_case("G042"), _case("G043")])

    names = stack.names()
    assert _between(names, "read_thread", "release")[-2:] == [
        "read_post_state",
        "release",
    ]
    released = [entry[1] for entry in stack.timeline if entry[0] == "release"]
    assert released == [f"PAT-{chat}" for _c, chat, _n in stack.posted()]
    assert _case_run(run_dir, "G042").cancelled_after == [
        _appointment("2026-03-03T10:00:00", "cancelled")
    ]
    assert _case_run(run_dir, "G043").cancelled_after == []


async def test_a_case_with_no_fixture_that_booked_has_its_booking_cancelled(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log, artifacts, scripts={"G001": [Attempt(books=("2026-03-05T09:00",))]}
    )

    run_dir = await _drive(stack, [_case("G001")])

    case_run = _case_run(run_dir, "G001")
    assert case_run.cancelled_after == [
        _appointment("2026-03-05T09:00:00", "cancelled")
    ]
    assert case_run.scheduling_after is None


async def test_a_chat_with_no_patient_is_not_released(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts, patientless=True)

    await _drive(stack, [_case("G001")])

    assert "release" not in stack.names()


@dataclass
class _ProvisionedByTheTurn(FakeStack):
    """A stack whose chats have no patient until a turn is posted into them."""

    async def chat_identity(self, session_id: str, chat_id: str) -> ChatIdentity:
        identity = await super().chat_identity(session_id, chat_id)
        if any(chat == chat_id for chat, _message in self.messages):
            return identity
        return identity.model_copy(update={"patient_id": None})


async def test_a_patient_the_turn_provisioned_is_released_and_recorded(
    log: Path, artifacts: Path
) -> None:
    # `POST /chat` provisions a patient for a chat created while the scheduler was
    # unreachable, so the turn can book with a patient the pre-turn read never saw.
    stack = _ProvisionedByTheTurn(
        log=log,
        artifacts=artifacts,
        scripts={"G001": [Attempt(books=("2026-03-05T09:00",))]},
    )

    run_dir = await _drive(stack, [_case("G001")])

    case_run = _case_run(run_dir, "G001")
    assert case_run.patient_id is not None
    assert ("release", case_run.patient_id) in stack.timeline
    assert case_run.cancelled_after == [
        _appointment("2026-03-05T09:00:00", "cancelled")
    ]


async def test_a_stop_after_the_turn_releases_the_patient_the_turn_provisioned(
    log: Path, artifacts: Path
) -> None:
    # The same provisioning, on a stop that leaves the case unwritten: the release
    # before the stop reads the patient again rather than trusting the pre-turn read.
    error = ThreadReadError("GET /chats/x/messages answered 500")
    stack = _ProvisionedByTheTurn(
        log=log,
        artifacts=artifacts,
        scripts={"G001": [Attempt(books=("2026-03-05T09:00",))]},
        failures_during={("read_thread", 1): error},
    )

    with pytest.raises(ThreadReadError) as raised:
        await _drive(stack, [_case("G001")])

    assert raised.value is error
    assert stack.timeline[-1] == ("release", f"PAT-{_last_chat(stack)}")


async def test_a_cleanup_that_fails_writes_the_case_and_stops_the_run(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts, failing_release={"G002"})

    with pytest.raises(CleanupFailedError):
        await _drive(stack, _THREE)

    assert [p[0] for p in stack.posted()] == ["G001", "G002"]
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == ["G001", "G002"]
    assert read_run(run_dir).cases == ["G001", "G002"]


async def test_a_measured_fixture_case_whose_post_state_cannot_be_read_stops_the_run(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts, unreadable_post_state={"G042"})

    with pytest.raises(PostStateUnreadableError, match="G042"):
        await _drive(stack, [_booking_case("G042"), _case("G043")])

    assert [p[0] for p in stack.posted()] == ["G042"]
    assert "release" in stack.names()
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == []


async def test_a_resumed_run_releases_every_recorded_patient_before_driving(
    log: Path, artifacts: Path
) -> None:
    run_dir = await _interrupted_run(log, artifacts)
    recorded_patient = _case_run(run_dir, "G001").patient_id
    stack = _stack(log, artifacts)

    await _resume(stack, run_dir, _THREE)

    names = stack.names()
    assert names[0] == "release"
    assert stack.timeline[0] == ("release", recorded_patient)
    assert names.index("release") < names.index("post_turn")


async def test_a_resumed_run_whose_release_fails_drives_nothing(
    log: Path, artifacts: Path
) -> None:
    run_dir = await _interrupted_run(log, artifacts)
    recorded_patient = _case_run(run_dir, "G001").patient_id
    assert recorded_patient is not None
    stack = _stack(log, artifacts, failing_release={recorded_patient})

    with pytest.raises(CleanupFailedError):
        await _resume(stack, run_dir, _THREE)

    assert stack.posted() == []


async def test_an_unplantable_label_stops_the_run_before_the_session_opens(
    log: Path, artifacts: Path
) -> None:
    saturday = {"practitioner": "William Osler", "day": "+5d", "time": "10:00"}
    scheduling = {"given": [saturday], "expect": [{**saturday, "status": "standing"}]}
    stack = _stack(log, artifacts)

    with pytest.raises(LabelError, match="G042"):
        await _drive(stack, [_booking_case("G042", scheduling)])

    assert stack.calls == []
    assert list(artifacts.iterdir()) == []


async def test_a_harness_fault_on_a_fixture_case_releases_its_patient_before_stopping(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log, artifacts, scripts={"G042": [Attempt(outcome="refused", status=422)]}
    )

    with pytest.raises(HarnessFaultError):
        await _drive(stack, [_booking_case("G042")])

    (chat,) = [chat for _case_id, chat, _now in stack.posted()]
    assert ("release", f"PAT-{chat}") in stack.timeline
    assert stack.appointments[f"PAT-{chat}"] == [
        _appointment("2026-03-03T10:00:00", "cancelled")
    ]


async def test_a_cleanup_failing_between_attempts_records_a_run_error_and_stops(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log,
        artifacts,
        scripts={"G042": [Attempt(outcome="not_sent"), Attempt()]},
        failing_release={"G042"},
    )

    with pytest.raises(CleanupFailedError):
        await _drive(stack, [_booking_case("G042"), _case("G043")])

    assert [p[0] for p in stack.posted()] == ["G042"]
    (run_dir,) = artifacts.iterdir()
    case_run = _case_run(run_dir, "G042")
    assert (case_run.attempts, case_run.excluded) == (1, ExclusionReason.RUN_ERROR)
    assert read_run(run_dir).cases == ["G042"]


# --- a restart outside a turn's slice (FR-047c) -------------------------------------

_OTHER_SETTINGS = _configured(similarity_floor=0.4)


async def test_a_restart_with_other_settings_before_the_first_turn_stops_the_run(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log, artifacts, restarts_during={("live_corpus", 1): _OTHER_SETTINGS}
    )

    with pytest.raises(ServiceRestartedError, match="similarity_floor"):
        await _drive(stack, _THREE)

    assert stack.posted() == []
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == []


async def test_a_restart_with_other_settings_before_a_resumed_runs_first_turn_stops_it(
    log: Path, artifacts: Path
) -> None:
    run_dir = await _interrupted_run(log, artifacts)
    stack = _stack(log, artifacts, restarts_during={("release", 1): _OTHER_SETTINGS})

    with pytest.raises(ServiceRestartedError, match="similarity_floor"):
        await _resume(stack, run_dir, _THREE)

    assert stack.posted() == []
    assert recorded_case_ids(run_dir) == ["G001"]


async def test_a_restart_with_other_settings_while_a_chat_is_set_up_stops_its_turn(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log, artifacts, restarts_during={("plant_history", 2): _OTHER_SETTINGS}
    )

    with pytest.raises(ServiceRestartedError, match="G002"):
        await _drive(stack, _THREE)

    assert [p[0] for p in stack.posted()] == ["G001"]
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == ["G001"]
    assert read_run(run_dir).cases == ["G001"]


async def test_a_restart_with_other_settings_while_planting_stops_and_releases(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts, restarts_during={("plant", 1): _OTHER_SETTINGS})

    with pytest.raises(ServiceRestartedError, match="G042"):
        await _drive(stack, [_booking_case("G042")])

    assert stack.posted() == []
    (chat,) = [call[1] for call in stack.calls if call[0] == "new_chat"]
    assert ("release", f"PAT-{chat}") in stack.timeline
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == []


async def test_a_restart_whose_settings_cannot_be_read_stops_and_releases_as_other(
    log: Path, artifacts: Path
) -> None:
    # A build that renamed or dropped a condition field restarted: that is a restart
    # under other settings, handled as one - not a validation error that skips the
    # release a restart owes the planted patient.
    unreadable = {k: v for k, v in _OTHER_SETTINGS.items() if k != "similarity_floor"}
    stack = _stack(log, artifacts, restarts_during={("plant", 1): unreadable})

    with pytest.raises(ServiceRestartedError, match="G042") as stopped:
        await _drive(stack, [_booking_case("G042")])

    assert isinstance(stopped.value.__cause__, ValidationError)
    assert stack.posted() == []
    (chat,) = [call[1] for call in stack.calls if call[0] == "new_chat"]
    assert ("release", f"PAT-{chat}") in stack.timeline
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == []


async def test_a_restart_with_other_settings_between_two_attempts_stops_the_retry(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log,
        artifacts,
        scripts={"G001": [Attempt(outcome="not_sent")]},
        restarts_during={("new_chat", 2): _OTHER_SETTINGS},
    )

    with pytest.raises(ServiceRestartedError, match="G001"):
        await _drive(stack, _THREE)

    assert len(stack.posted()) == 1
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == []


@pytest.mark.parametrize(
    ("case", "stack_options"),
    [
        pytest.param(
            _case("G001"),
            {
                "scripts": {"G001": [Attempt(outcome="not_sent")] * 3},
                "restarts_during": {("post_turn", 3): _OTHER_SETTINGS},
            },
            id="unmeasured",
        ),
        pytest.param(
            _booking_case("G001"),
            {
                "scripts": {
                    "G001": [
                        Attempt(
                            outcome="no_answer",
                            restart=_OTHER_SETTINGS,
                            settles_after=None,
                        )
                    ]
                }
            },
            id="outcome-unknown",
        ),
        pytest.param(
            _booking_case("G001"),
            {
                "roster": frozenset({"Andreas Vesalius"}),
                "restarts_during": {("plant", 1): _OTHER_SETTINGS},
            },
            id="unplanted",
        ),
    ],
)
async def test_a_restart_with_other_settings_in_an_attempt_with_no_slice_stops(
    log: Path, artifacts: Path, case: Case, stack_options: dict[str, Any]
) -> None:
    stack = _stack(log, artifacts, **stack_options)

    with pytest.raises(ServiceRestartedError, match="G001"):
        await _drive(stack, [case, _case("G002")])

    assert "G002" not in [p[0] for p in stack.posted()]
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == []


async def test_a_restart_with_other_settings_during_a_cases_cleanup_stops_unwritten(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts, restarts_during={("release", 1): _OTHER_SETTINGS})

    with pytest.raises(ServiceRestartedError, match="G001"):
        await _drive(stack, _THREE)

    assert [p[0] for p in stack.posted()] == ["G001"]
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == []


async def test_a_restart_that_truncated_the_log_and_grew_it_back_past_the_check_stops(
    log: Path, artifacts: Path
) -> None:
    # Foreign lines enough to carry the replaced log past every offset checked so far.
    padding = ['INFO:     127.0.0.1:1 - "POST /chats HTTP/1.1" 201 Created'] * 400
    stack = _stack(
        log,
        artifacts,
        replacements_during={("new_chat", 2): [_OTHER_SETTINGS, *padding]},
    )

    with pytest.raises(ServiceRestartedError, match="G002"):
        await _drive(stack, _THREE)

    assert [p[0] for p in stack.posted()] == ["G001"]
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == ["G001"]


async def test_a_turn_whose_log_was_truncated_and_grown_back_is_a_missing_log_slice(
    log: Path, artifacts: Path
) -> None:
    padding = ['INFO:     127.0.0.1:1 - "POST /chats HTTP/1.1" 201 Created'] * 400
    stack = _stack(
        log,
        artifacts,
        replacements_during={("post_turn", 1): [_configured(), *padding]},
    )

    run_dir = await _drive(stack, [_case("G001")])

    case_run = _case_run(run_dir, "G001")
    assert case_run.excluded is ExclusionReason.MISSING_LOG_SLICE
    assert case_run.events is None


async def test_a_restart_with_the_same_settings_outside_a_slice_is_driven_through(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log,
        artifacts,
        scripts={"G002": [Attempt(outcome="not_sent")]},
        restarts_during={
            ("live_corpus", 1): _configured(),
            ("plant_history", 2): _configured(),
            ("new_chat", 3): _configured(),
            ("release", 1): _configured(),
        },
    )

    run_dir = await _drive(stack, _THREE)

    assert recorded_case_ids(run_dir) == ["G001", "G002", "G003"]
    assert {_case_run(run_dir, c).excluded for c in ("G001", "G002", "G003")} == {None}


async def test_a_restart_with_other_settings_and_a_failed_cleanup_stop_unwritten(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log,
        artifacts,
        failing_release={"G001"},
        restarts_during={("release", 1): _OTHER_SETTINGS},
    )

    with pytest.raises(CleanupFailedError) as raised:
        await _drive(stack, _THREE)

    assert isinstance(raised.value.__cause__, ServiceRestartedError)
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == []


# --- an error the service or its database answers with (FR-048) ----------------------


@pytest.mark.parametrize(
    ("method", "error"),
    [
        ("open_session", SessionError("POST /chats answered 503")),
        ("open_session", httpx.ConnectError("connection refused")),
        ("live_corpus", SessionError("GET /faq answered 500")),
        ("live_corpus", httpx.ReadTimeout("timed out")),
    ],
)
async def test_an_error_opening_the_session_stops_before_any_turn_and_writes_no_run(
    log: Path, artifacts: Path, method: str, error: Exception
) -> None:
    stack = _stack(log, artifacts, failures_during={(method, 1): error})

    with pytest.raises(type(error)):
        await _drive(stack, _THREE)

    assert stack.posted() == []
    assert list(artifacts.iterdir()) == []


@pytest.mark.parametrize(
    ("method", "error"),
    [
        ("new_chat", SessionError("POST /chats minted a new session")),
        ("new_chat", httpx.ConnectError("connection refused")),
        ("chat_identity", ChatNotFoundError("no chat")),
        ("plant_history", ChatNotFoundError("no chat")),
        ("read_thread", ThreadReadError("GET /chats/x/messages answered 500")),
        ("read_thread", TurnProtocolError("two replies")),
        ("read_thread", httpx.ReadTimeout("timed out")),
    ],
)
async def test_an_error_driving_a_case_stops_the_run_with_that_case_unwritten(
    log: Path, artifacts: Path, method: str, error: Exception
) -> None:
    stack = _stack(log, artifacts, failures_during={(method, 2): error})

    with pytest.raises(type(error)):
        await _drive(stack, _THREE)

    assert "G003" not in [p[0] for p in stack.posted()]
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == ["G001"]
    assert read_run(run_dir).cases == ["G001"]


# --- a stop after the chat's patient is read releases it (FR-041b) -------------------


def _last_chat(stack: FakeStack) -> str:
    return [call[1] for call in stack.calls if call[0] == "new_chat"][-1]


@pytest.mark.parametrize(
    ("method", "error"),
    [
        ("plant_history", ChatNotFoundError("no chat")),
        ("read_thread", ThreadReadError("GET /chats/x/messages answered 500")),
        ("read_thread", TurnProtocolError("two replies")),
        ("read_thread", httpx.ReadTimeout("timed out")),
    ],
)
async def test_an_error_after_the_patient_is_read_releases_it_before_stopping(
    log: Path, artifacts: Path, method: str, error: Exception
) -> None:
    stack = _stack(log, artifacts, failures_during={(method, 1): error})

    with pytest.raises(type(error)) as raised:
        await _drive(stack, [_case("G001"), _case("G002")])

    assert raised.value is error
    assert stack.timeline[-1] == ("release", f"PAT-{_last_chat(stack)}")
    assert "G002" not in [p[0] for p in stack.posted()]
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == []


async def test_a_segmentation_breaking_the_record_releases_the_patient_and_stops(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log, artifacts, scripts={"G001": [Attempt(intents=("no_such_intent",))]}
    )

    with pytest.raises(ValidationError):
        await _drive(stack, [_case("G001"), _case("G002")])

    assert stack.timeline[-1] == ("release", f"PAT-{_last_chat(stack)}")
    assert [p[0] for p in stack.posted()] == ["G001"]
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == []


@pytest.mark.parametrize(
    ("stack_options", "stopped_by"),
    [
        pytest.param(
            {"failures_during": {("read_thread", 1): ThreadReadError("answered 500")}},
            ThreadReadError,
            id="thread-unread",
        ),
        pytest.param(
            {"scripts": {"G001": [Attempt(outcome="refused", status=422)]}},
            HarnessFaultError,
            id="harness-fault",
        ),
        pytest.param(
            {"scripts": {"G001": [Attempt(intents=("no_such_intent",))]}},
            ValidationError,
            id="segmentation",
        ),
    ],
)
async def test_a_release_failing_after_such_an_error_is_chained_to_it(
    log: Path,
    artifacts: Path,
    stack_options: dict[str, Any],
    stopped_by: type[Exception],
) -> None:
    stack = _stack(log, artifacts, failing_release={"G001"}, **stack_options)

    with pytest.raises(CleanupFailedError) as raised:
        await _drive(stack, [_case("G001")])

    assert isinstance(raised.value.__cause__, stopped_by)
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == []


async def test_a_resumed_run_releases_every_patient_of_its_sessions_chats(
    log: Path, artifacts: Path
) -> None:
    run_dir = await _interrupted_run(log, artifacts)
    recorded_patient = _case_run(run_dir, "G001").patient_id
    assert recorded_patient is not None
    stack = _stack(
        log,
        artifacts,
        session_patient_ids=["PAT-UNRECORDED-ATTEMPT", recorded_patient],
    )

    await _resume(stack, run_dir, _THREE)

    assert ("session_patients", read_run(run_dir).session_id) in stack.calls
    names = stack.names()
    before_the_first_turn = stack.timeline[: names.index("post_turn")]
    released = [entry[1] for entry in before_the_first_turn if entry[0] == "release"]
    assert released == [recorded_patient, "PAT-UNRECORDED-ATTEMPT"]


# --- a fixture case whose turn was sent and then broke the run (FR-007d) -------------


@pytest.mark.parametrize(
    ("method", "error", "terminal"),
    [
        (
            "read_thread",
            ThreadReadError("GET /chats/x/messages answered 500"),
            TerminalKind.DONE,
        ),
        ("read_thread", TurnProtocolError("two replies"), TerminalKind.DONE),
        ("read_thread", httpx.ReadTimeout("timed out"), TerminalKind.DONE),
    ],
)
async def test_a_sent_fixture_turn_that_stops_the_run_is_written_as_outcome_unknown(
    log: Path,
    artifacts: Path,
    method: str,
    error: Exception,
    terminal: TerminalKind | None,
) -> None:
    # FakeStack.release itself asserts the case file is not yet written.
    stack = _stack(log, artifacts, failures_during={(method, 1): error})

    with pytest.raises(type(error)) as raised:
        await _drive(stack, [_booking_case("G042"), _case("G043")])

    assert raised.value is error
    assert [p[0] for p in stack.posted()] == ["G042"]
    names = stack.names()
    assert names.index("read_post_state") < names.index("release")
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == ["G042"]
    assert read_run(run_dir).cases == ["G042"]
    case_run = _case_run(run_dir, "G042")
    assert (case_run.attempts, case_run.excluded) == (
        1,
        ExclusionReason.OUTCOME_UNKNOWN,
    )
    assert (case_run.terminal.kind if case_run.terminal else None) is terminal
    assert case_run.scheduling_after == [
        _appointment("2026-03-03T10:00:00", "standing")
    ]
    assert case_run.cancelled_after == [
        _appointment("2026-03-03T10:00:00", "cancelled")
    ]


async def test_a_sent_fixture_turn_stopping_the_run_with_no_post_state_is_still_written(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log,
        artifacts,
        unreadable_post_state={"G042"},
        failures_during={("read_thread", 1): ThreadReadError("answered 500")},
    )

    with pytest.raises(ThreadReadError):
        await _drive(stack, [_booking_case("G042")])

    (run_dir,) = artifacts.iterdir()
    case_run = _case_run(run_dir, "G042")
    assert case_run.excluded is ExclusionReason.OUTCOME_UNKNOWN
    assert case_run.scheduling_after is None


async def test_a_sent_fixture_turn_stopping_the_run_whose_cleanup_fails_chains_both(
    log: Path, artifacts: Path
) -> None:
    error = ThreadReadError("answered 500")
    stack = _stack(
        log,
        artifacts,
        failing_release={"G042"},
        failures_during={("read_thread", 1): error},
    )

    with pytest.raises(CleanupFailedError) as raised:
        await _drive(stack, [_booking_case("G042")])

    assert raised.value.__cause__ is error
    (run_dir,) = artifacts.iterdir()
    assert _case_run(run_dir, "G042").excluded is ExclusionReason.OUTCOME_UNKNOWN


async def test_a_resumed_run_does_not_post_a_sent_fixture_turn_that_stopped_it_again(
    log: Path, artifacts: Path
) -> None:
    first = _stack(
        log,
        artifacts,
        failures_during={("read_thread", 1): ThreadReadError("answered 500")},
    )
    cases = [_booking_case("G042"), _case("G043")]
    with pytest.raises(ThreadReadError):
        await _drive(first, cases)
    (run_dir,) = artifacts.iterdir()
    stack = _stack(log, artifacts)

    await _resume(stack, run_dir, cases)

    assert [p[0] for p in stack.posted()] == ["G043"]


async def test_a_sent_fixture_turn_stopping_the_run_during_a_restart_stays_unwritten(
    log: Path, artifacts: Path
) -> None:
    error = ThreadReadError("answered 500")
    stack = _stack(
        log,
        artifacts,
        restarts_during={("read_thread", 1): _OTHER_SETTINGS},
        failures_during={("read_thread", 1): error},
    )

    with pytest.raises(ServiceRestartedError) as raised:
        await _drive(stack, [_booking_case("G042")])

    assert raised.value.__cause__ is error
    assert "release" in stack.names()
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == []


# --- a thread body that does not parse (FR-007d, FR-041b) ----------------------------

_UNPARSEABLE_THREADS = pytest.mark.parametrize(
    "body",
    [b"<html>Bad Gateway</html>", b'{"messages": [{"id": "MSG"}]}'],
    ids=["not-json", "breaks-the-schema"],
)


@_UNPARSEABLE_THREADS
async def test_an_unparseable_thread_releases_a_case_with_no_fixture_and_stops(
    log: Path, artifacts: Path, body: bytes
) -> None:
    stack = _stack(log, artifacts, thread_bodies={1: body})

    with pytest.raises(ThreadReadError):
        await _drive(stack, [_case("G001"), _case("G002")])

    assert stack.timeline[-1] == ("release", f"PAT-{_last_chat(stack)}")
    assert [p[0] for p in stack.posted()] == ["G001"]
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == []


@_UNPARSEABLE_THREADS
async def test_an_unparseable_thread_writes_a_fixture_case_as_outcome_unknown(
    log: Path, artifacts: Path, body: bytes
) -> None:
    # FakeStack.release itself asserts the case file is not yet written.
    first = _stack(log, artifacts, thread_bodies={1: body})
    cases = [_booking_case("G042"), _case("G043")]

    with pytest.raises(ThreadReadError):
        await _drive(first, cases)

    names = first.names()
    assert names.index("read_post_state") < names.index("release")
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == ["G042"]
    case_run = _case_run(run_dir, "G042")
    assert case_run.excluded is ExclusionReason.OUTCOME_UNKNOWN
    assert case_run.cancelled_after == [
        _appointment("2026-03-03T10:00:00", "cancelled")
    ]
    stack = _stack(log, artifacts)

    await _resume(stack, run_dir, cases)

    assert [p[0] for p in stack.posted()] == ["G043"]


# --- a fixture case whose logged segmentation breaks the record (FR-007d) ------------


async def test_a_fixture_case_whose_segmentation_breaks_the_record_is_outcome_unknown(
    log: Path, artifacts: Path
) -> None:
    # FakeStack.release itself asserts the case file is not yet written.
    stack = _stack(
        log, artifacts, scripts={"G042": [Attempt(intents=("no_such_intent",))]}
    )

    with pytest.raises(ValidationError):
        await _drive(stack, [_booking_case("G042"), _case("G043")])

    assert [p[0] for p in stack.posted()] == ["G042"]
    names = stack.names()
    assert names.index("read_post_state") < names.index("release")
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == ["G042"]
    assert read_run(run_dir).cases == ["G042"]
    case_run = _case_run(run_dir, "G042")
    assert (case_run.attempts, case_run.excluded) == (
        1,
        ExclusionReason.OUTCOME_UNKNOWN,
    )
    assert case_run.segments is None
    assert case_run.scheduling_after == [
        _appointment("2026-03-03T10:00:00", "standing")
    ]
    assert case_run.cancelled_after == [
        _appointment("2026-03-03T10:00:00", "cancelled")
    ]


# --- a scripted reply, posted as a second turn (FR-037b) ------------------------------

_YES = "Yes, please go ahead."
_CANCEL_TOMORROW_ON_A_YES = {**_CANCEL_TOMORROW, "reply": _YES}


def _reply_case(case_id: str) -> Case:
    return _booking_case(case_id, _CANCEL_TOMORROW_ON_A_YES)


def _confirmed_cancel() -> list[Attempt]:
    """The exchange the booking loop is specified for: ask first, cancel on the yes."""
    return [
        Attempt(intents=("booking",), tools=("list_my_appointments",)),
        Attempt(
            intents=("booking",),
            tools=("list_my_appointments", "cancel_appointment"),
            cancels=True,
        ),
    ]


_PLANTED = _appointment("2026-03-03T10:00:00", "standing")
_CANCELLED = _appointment("2026-03-03T10:00:00", "cancelled")


async def test_a_reply_case_reads_after_its_first_turn_then_posts_the_reply_in_its_chat(
    log: Path, artifacts: Path, offsets: list[FakeStack]
) -> None:
    # FakeStack.release itself asserts the case file is not yet written.
    stack = _stack(log, artifacts, scripts={"G042": _confirmed_cancel()})
    offsets.append(stack)

    run_dir = await _drive(stack, [_reply_case("G042")])

    names = stack.names()
    first_post, reply_post = [i for i, n in enumerate(names) if n == "post_turn"]
    assert names[first_post + 1 : reply_post + 1] == [
        "read_thread",
        "read_post_state",
        "log_offset",
        "post_turn",
    ]
    assert names[reply_post + 1 :] == ["read_thread", "read_post_state", "release"]
    (chat,) = {chat for _case_id, chat, _now in stack.posted()}
    assert stack.messages == [(chat, "message of G042"), (chat, _YES)]
    assert {now for _case_id, _chat, now in stack.posted()} == {DEFAULT_CLOCK}

    case_run = _case_run(run_dir, "G042")
    assert (case_run.attempts, case_run.chat_id, case_run.excluded) == (1, chat, None)
    assert case_run.scheduling_before_reply == [_PLANTED]
    assert case_run.scheduling_after == [_CANCELLED]
    assert case_run.cancelled_after == []
    assert case_run.patient_message is not None
    assert case_run.patient_message.id == f"MSG-{chat}"


async def test_the_reply_turn_records_its_own_stored_messages_and_its_own_events(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts, scripts={"G042": _confirmed_cancel()})

    run_dir = await _drive(stack, [_reply_case("G042")])

    case_run = _case_run(run_dir, "G042")
    chat = case_run.chat_id
    reply_turn = case_run.reply_turn
    assert reply_turn is not None
    assert reply_turn.terminal is not None
    assert reply_turn.terminal.kind is TerminalKind.DONE
    assert reply_turn.patient_message == StoredMessage(id=f"MSG-{chat}-2", content=_YES)
    assert reply_turn.assistant_message is not None
    assert reply_turn.assistant_message.id == f"REPLY-{chat}-2"
    assert reply_turn.events is not None
    assert {event.get("turn_id") for event in reply_turn.events} == {f"MSG-{chat}-2"}
    assert [e["tool_name"] for e in reply_turn.events if "tool_name" in e] == [
        "list_my_appointments",
        "cancel_appointment",
    ]
    # The first turn's own fields are its own, not the reply's.
    assert case_run.events is not None
    assert {event.get("turn_id") for event in case_run.events} == {f"MSG-{chat}"}
    assert case_run.assistant_message is not None
    assert case_run.assistant_message.id == f"REPLY-{chat}"


async def test_the_reply_turns_thread_read_skips_the_history_and_the_first_turn(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts, scripts={"G042": _confirmed_cancel()})
    case = _booking_case("G042", _CANCEL_TOMORROW_ON_A_YES)
    case = case.model_copy(
        update={
            "history": [HistoryEntry.model_validate({"role": "user", "text": "hi"})]
        }
    )

    await _drive(stack, [case])

    (chat,) = {chat for _case_id, chat, _now in stack.posted()}
    reads = [call for call in stack.calls if call[0] == "read_thread"]
    assert [(call[2], call[3]) for call in reads] == [
        ("message of G042", [f"PLANTED-{chat}-0"]),
        (_YES, sorted([f"PLANTED-{chat}-0", f"MSG-{chat}", f"REPLY-{chat}"])),
    ]


async def test_elapsed_seconds_covers_both_turns_of_a_reply_case(
    log: Path, artifacts: Path
) -> None:
    first, reply = _confirmed_cancel()
    stack = _stack(
        log,
        artifacts,
        scripts={
            "G042": [
                replace(first, seconds=2.0),
                replace(reply, seconds=5.0),
            ]
        },
    )

    run_dir = await _drive(stack, [_reply_case("G042")])

    assert _case_run(run_dir, "G042").elapsed_seconds == 7.0
    assert read_run(run_dir).drive_seconds == 7.0


async def test_a_case_whose_fixture_has_no_reply_still_drives_exactly_one_turn(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts)

    run_dir = await _drive(stack, [_booking_case("G042")])

    assert len(stack.posted()) == 1
    assert stack.names().count("read_post_state") == 1
    case_run = _case_run(run_dir, "G042")
    assert (case_run.reply_turn, case_run.scheduling_before_reply) == (None, None)
    assert case_run.scheduling_after == [_PLANTED]


async def test_a_first_turn_that_already_did_the_task_posts_no_reply(
    log: Path, artifacts: Path
) -> None:
    # The fixture expects the cancellation, and the first turn made it: the reply would
    # answer a question the loop never asked.
    stack = _stack(log, artifacts, scripts={"G042": [Attempt(cancels=True), Attempt()]})

    run_dir = await _drive(stack, [_reply_case("G042")])

    assert len(stack.posted()) == 1
    names = stack.names()
    assert names[names.index("post_turn") + 1 :] == [
        "read_thread",
        "read_post_state",
        "read_post_state",
        "release",
    ]
    case_run = _case_run(run_dir, "G042")
    assert (case_run.reply_turn, case_run.reply_skipped) == (None, True)
    assert case_run.scheduling_before_reply == [_CANCELLED]
    assert case_run.scheduling_after == [_CANCELLED]
    assert case_run.excluded is None


async def test_a_first_turn_that_wrote_something_else_still_gets_the_reply(
    log: Path, artifacts: Path
) -> None:
    # G042 expects its appointment cancelled; a first turn that left it standing and
    # asked has not done the task, so the reply is posted.
    stack = _stack(log, artifacts, scripts={"G042": _confirmed_cancel()})

    run_dir = await _drive(stack, [_reply_case("G042")])

    assert len(stack.posted()) == 2
    case_run = _case_run(run_dir, "G042")
    assert case_run.reply_skipped is False
    assert case_run.reply_turn is not None


# --- when the reply is not posted -----------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        ("hand_off", ExclusionReason.HANDED_OFF_TURN),
        ("silent", ExclusionReason.SILENCED_TURN),
        ("cancelled", ExclusionReason.CANCELLED_TURN),
        ("failed_no_reply", ExclusionReason.RUN_ERROR),
    ],
)
async def test_the_reply_is_not_posted_when_the_first_turn_ended_without_a_reply(
    log: Path, artifacts: Path, outcome: str, reason: ExclusionReason
) -> None:
    stack = _stack(log, artifacts, scripts={"G042": [Attempt(outcome=outcome)]})

    run_dir = await _drive(stack, [_reply_case("G042"), _case("G043")])

    assert [p[0] for p in stack.posted()] == ["G042", "G043"]
    assert _YES not in [message for _chat, message in stack.messages]
    case_run = _case_run(run_dir, "G042")
    assert (case_run.attempts, case_run.excluded) == (1, reason)
    assert (case_run.reply_turn, case_run.scheduling_before_reply) == (None, None)
    assert case_run.scheduling_after == [_PLANTED]
    assert case_run.cancelled_after == [_CANCELLED]


# --- retries: an attempt is the whole exchange ----------------------------------------


async def test_a_first_turn_not_sent_re_drives_both_turns_in_a_fresh_chat(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log,
        artifacts,
        scripts={"G042": [Attempt(outcome="not_sent"), *_confirmed_cancel()]},
    )

    run_dir = await _drive(stack, [_reply_case("G042")])

    chats = [chat for _case_id, chat, _now in stack.posted()]
    assert len(chats) == 3
    assert chats[0] != chats[1] == chats[2]
    assert stack.messages[1:] == [(chats[1], "message of G042"), (chats[1], _YES)]
    case_run = _case_run(run_dir, "G042")
    assert (case_run.attempts, case_run.chat_id, case_run.excluded) == (
        2,
        chats[1],
        None,
    )
    assert case_run.reply_turn is not None


async def test_a_reply_not_sent_re_drives_both_turns_in_a_fresh_chat(
    log: Path, artifacts: Path
) -> None:
    first, reply = _confirmed_cancel()
    stack = _stack(
        log,
        artifacts,
        scripts={"G042": [first, Attempt(outcome="not_sent"), first, reply]},
    )

    run_dir = await _drive(stack, [_reply_case("G042")])

    chats = [chat for _case_id, chat, _now in stack.posted()]
    assert stack.messages == [
        (chats[0], "message of G042"),
        (chats[0], _YES),
        (chats[2], "message of G042"),
        (chats[2], _YES),
    ]
    assert chats[0] != chats[2]
    # The failed exchange's patient is released before the retry plants.
    first_release = stack.timeline.index(("release", f"PAT-{chats[0]}"))
    second_plant = stack.timeline.index(("plant", f"PAT-{chats[2]}", 1))
    assert first_release < second_plant
    case_run = _case_run(run_dir, "G042")
    assert (case_run.attempts, case_run.chat_id, case_run.excluded) == (
        2,
        chats[2],
        None,
    )
    assert case_run.scheduling_before_reply == [_PLANTED]
    assert case_run.scheduling_after == [_CANCELLED]
    assert case_run.cancelled_after == [_CANCELLED]


async def test_a_reply_never_sent_in_any_attempt_is_a_run_error(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log,
        artifacts,
        scripts={"G042": [Attempt(), Attempt(outcome="not_sent")] * MAX_ATTEMPTS},
    )

    run_dir = await _drive(stack, [_reply_case("G042"), _case("G043")])

    assert [p[0] for p in stack.posted()] == ["G042"] * (2 * MAX_ATTEMPTS) + ["G043"]
    case_run = _case_run(run_dir, "G042")
    assert (case_run.attempts, case_run.excluded) == (
        MAX_ATTEMPTS,
        ExclusionReason.RUN_ERROR,
    )
    assert case_run.reply_turn is None
    assert case_run.scheduling_before_reply == [_PLANTED]
    assert case_run.scheduling_after == [_PLANTED]


async def test_a_reply_sent_with_no_answer_that_settles_is_scored_and_not_retried(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log,
        artifacts,
        scripts={"G042": [Attempt(), Attempt(outcome="no_answer", cancels=True)]},
    )

    run_dir = await _drive(stack, [_reply_case("G042"), _case("G043")])

    assert [p[0] for p in stack.posted()] == ["G042", "G042", "G043"]
    names = stack.names()
    assert names.index("read_post_state", names.index("post_turn") + 1) < names.index(
        "release"
    )
    case_run = _case_run(run_dir, "G042")
    assert (case_run.attempts, case_run.excluded) == (1, None)
    assert case_run.scheduling_before_reply == [_PLANTED]
    assert case_run.scheduling_after == [_CANCELLED]
    assert case_run.reply_turn is not None
    assert case_run.reply_turn.terminal is not None
    assert case_run.reply_turn.terminal.kind is TerminalKind.ERROR
    assert case_run.reply_turn.assistant_message is not None


async def test_an_assistant_failed_reply_that_stored_no_answer_is_a_run_error(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log,
        artifacts,
        scripts={"G042": [Attempt(), Attempt(outcome="failed_no_reply")]},
    )

    run_dir = await _drive(stack, [_reply_case("G042")])

    assert len(stack.posted()) == 2
    case_run = _case_run(run_dir, "G042")
    assert (case_run.attempts, case_run.excluded) == (1, ExclusionReason.RUN_ERROR)
    assert case_run.reply_turn is not None
    assert case_run.reply_turn.assistant_message is None
    assert case_run.reply_turn.patient_message is not None
    assert case_run.reply_turn.patient_message.attention_mark == "assistant_failed"
    assert case_run.scheduling_after == [_PLANTED]


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        ("silent", ExclusionReason.SILENCED_TURN),
        ("cancelled", ExclusionReason.CANCELLED_TURN),
    ],
)
async def test_a_reply_turn_that_ended_silent_or_cancelled_excludes_the_case(
    log: Path, artifacts: Path, outcome: str, reason: ExclusionReason
) -> None:
    stack = _stack(
        log, artifacts, scripts={"G042": [Attempt(), Attempt(outcome=outcome)]}
    )

    run_dir = await _drive(stack, [_reply_case("G042")])

    case_run = _case_run(run_dir, "G042")
    assert (case_run.attempts, case_run.excluded) == (1, reason)
    assert case_run.reply_turn is not None
    assert case_run.scheduling_after == [_PLANTED]


async def test_a_reply_turn_whose_events_cannot_be_selected_is_a_missing_log_slice(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts, scripts={"G042": [Attempt(), Attempt(logged=False)]})

    run_dir = await _drive(stack, [_reply_case("G042")])

    case_run = _case_run(run_dir, "G042")
    assert case_run.excluded is ExclusionReason.MISSING_LOG_SLICE
    assert case_run.reply_turn is not None
    assert case_run.reply_turn.events is None
    assert case_run.events is not None


async def test_a_reply_turn_that_produced_classification_failed_is_a_run_error(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log,
        artifacts,
        scripts={"G042": [Attempt(), Attempt(intents=("classification_failed",))]},
    )

    run_dir = await _drive(stack, [_reply_case("G042")])

    assert _case_run(run_dir, "G042").excluded is ExclusionReason.RUN_ERROR


async def test_a_handed_off_reply_turn_records_handed_off_turn_on_the_case(
    log: Path, artifacts: Path
) -> None:
    # FR-037b: the reply's hand-off is the case's. Its scope (FR-018a) sets the case
    # aside from retrieval and serving, the first turn's included, and leaves it scored
    # for classification and booking - so both reads are still stored.
    stack = _stack(
        log, artifacts, scripts={"G042": [Attempt(), Attempt(outcome="hand_off")]}
    )

    run_dir = await _drive(stack, [_reply_case("G042"), _case("G043")])

    assert [p[0] for p in stack.posted()] == ["G042", "G042", "G043"]
    case_run = _case_run(run_dir, "G042")
    assert (case_run.attempts, case_run.excluded) == (
        1,
        ExclusionReason.HANDED_OFF_TURN,
    )
    assert case_run.reply_turn is not None
    assert case_run.reply_turn.terminal is not None
    assert case_run.reply_turn.terminal.payload["answer_source"] == "hand_off"
    assert case_run.reply_turn.events is not None
    assert case_run.events is not None
    assert case_run.scheduling_before_reply == [_PLANTED]
    assert case_run.scheduling_after == [_PLANTED]


async def test_a_handed_off_reply_turn_keeps_the_first_turns_wider_exclusion(
    log: Path, artifacts: Path
) -> None:
    # A first turn's case-scoped reason already sets the case aside from every metric;
    # a hand-off, whose scope is narrower, does not replace it.
    stack = _stack(
        log,
        artifacts,
        scripts={"G042": [Attempt(logged=False), Attempt(outcome="hand_off")]},
    )

    run_dir = await _drive(stack, [_reply_case("G042")])

    case_run = _case_run(run_dir, "G042")
    assert case_run.excluded is ExclusionReason.MISSING_LOG_SLICE
    assert case_run.reply_turn is not None


async def test_the_first_turns_own_exclusion_is_kept_when_the_reply_was_posted(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log,
        artifacts,
        scripts={"G042": [Attempt(logged=False), Attempt(outcome="silent")]},
    )

    run_dir = await _drive(stack, [_reply_case("G042")])

    assert len(stack.posted()) == 2
    case_run = _case_run(run_dir, "G042")
    assert case_run.excluded is ExclusionReason.MISSING_LOG_SLICE
    assert case_run.reply_turn is not None


# --- a reply turn that breaks the run -----------------------------------------------


async def test_a_harness_fault_on_the_reply_releases_the_patient_and_stops_unwritten(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log,
        artifacts,
        scripts={"G042": [Attempt(), Attempt(outcome="refused", status=422)]},
    )

    with pytest.raises(HarnessFaultError):
        await _drive(stack, [_reply_case("G042"), _case("G043")])

    assert [p[0] for p in stack.posted()] == ["G042", "G042"]
    assert stack.timeline[-1] == ("release", f"PAT-{_last_chat(stack)}")
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == []


async def test_a_read_after_the_first_turn_that_fails_stops_before_the_reply(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(log, artifacts, unreadable_post_state={"G042"})

    with pytest.raises(PostStateUnreadableError, match="G042"):
        await _drive(stack, [_reply_case("G042"), _case("G043")])

    assert [p[0] for p in stack.posted()] == ["G042"]
    assert stack.timeline[-1] == ("release", f"PAT-{_last_chat(stack)}")
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == []


async def test_a_restart_with_other_settings_before_the_reply_stops_without_posting_it(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log,
        artifacts,
        restarts_during={("read_post_state", 1): _OTHER_SETTINGS},
    )

    with pytest.raises(ServiceRestartedError, match="G042"):
        await _drive(stack, [_reply_case("G042")])

    assert len(stack.posted()) == 1
    assert "release" in stack.names()
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == []


async def test_a_restart_with_other_settings_inside_the_reply_turn_stops_unwritten(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log,
        artifacts,
        scripts={"G042": [Attempt(), Attempt(restart=_OTHER_SETTINGS)]},
    )

    with pytest.raises(ServiceRestartedError, match="G042"):
        await _drive(stack, [_reply_case("G042")])

    assert len(stack.posted()) == 2
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == []


@pytest.mark.parametrize(
    ("method", "error", "terminal"),
    [
        ("read_thread", ThreadReadError("answered 500"), TerminalKind.DONE),
        ("read_thread", httpx.ReadTimeout("timed out"), TerminalKind.DONE),
    ],
)
async def test_a_reply_turn_that_breaks_the_run_is_written_as_outcome_unknown(
    log: Path,
    artifacts: Path,
    method: str,
    error: Exception,
    terminal: TerminalKind | None,
) -> None:
    # FakeStack.release itself asserts the case file is not yet written.
    stack = _stack(
        log,
        artifacts,
        scripts={"G042": _confirmed_cancel()},
        failures_during={(method, 2): error},
    )
    cases = [_reply_case("G042"), _case("G043")]

    with pytest.raises(type(error)) as raised:
        await _drive(stack, cases)

    assert raised.value is error
    assert [p[0] for p in stack.posted()] == ["G042", "G042"]
    names = stack.names()
    assert names[-2:] == ["read_post_state", "release"]
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == ["G042"]
    case_run = _case_run(run_dir, "G042")
    assert (case_run.attempts, case_run.excluded) == (
        1,
        ExclusionReason.OUTCOME_UNKNOWN,
    )
    assert case_run.scheduling_before_reply == [_PLANTED]
    assert case_run.reply_turn is not None
    reply_terminal = case_run.reply_turn.terminal
    assert (reply_terminal.kind if reply_terminal else None) is terminal
    assert case_run.reply_turn.patient_message is None
    resumed = _stack(log, artifacts)

    await _resume(resumed, run_dir, cases)

    assert [p[0] for p in resumed.posted()] == ["G043"]


async def test_a_reply_turn_whose_segmentation_breaks_the_record_is_outcome_unknown(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log,
        artifacts,
        scripts={"G042": [Attempt(), Attempt(intents=("no_such_intent",))]},
    )

    with pytest.raises(ValidationError):
        await _drive(stack, [_reply_case("G042"), _case("G043")])

    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == ["G042"]
    case_run = _case_run(run_dir, "G042")
    assert case_run.excluded is ExclusionReason.OUTCOME_UNKNOWN
    assert case_run.reply_turn is not None
    assert case_run.reply_turn.assistant_message is not None
    assert case_run.scheduling_after == [_PLANTED]


# --- an unanswered turn settles before it is released (FR-041c) -----------------------


def _after_the_last_post(stack: FakeStack) -> list[str]:
    names = stack.names()
    last_post = len(names) - 1 - names[::-1].index("post_turn")
    return names[last_post + 1 :]


@pytest.mark.parametrize("settles_with", ["reply", "assistant_failed"])
async def test_an_unanswered_fixture_turn_is_polled_until_it_settles_then_released(
    log: Path, artifacts: Path, settles_with: str
) -> None:
    # FakeStack.release itself asserts the case file is not yet written.
    stack = _stack(
        log,
        artifacts,
        scripts={
            "G042": [
                Attempt(outcome="no_answer", settles_after=3, settles_with=settles_with)
            ]
        },
    )

    run_dir = await _drive(stack, [_booking_case("G042"), _case("G043")])

    posts = [i for i, name in enumerate(stack.names()) if name == "post_turn"]
    assert stack.names()[posts[0] + 1 : posts[1]][:9] == [
        "read_thread",
        "sleep",
        "read_thread",
        "sleep",
        "read_thread",
        "sleep",
        "read_thread",
        "read_post_state",
        "release",
    ]
    assert {e[1] for e in stack.timeline if e[0] == "sleep"} == {
        SETTLE_INTERVAL_SECONDS
    }
    case_run = _case_run(run_dir, "G042")
    settled_as = None if settles_with == "reply" else ExclusionReason.RUN_ERROR
    assert (case_run.attempts, case_run.excluded) == (1, settled_as)
    assert case_run.cancelled_after == [_CANCELLED]


async def test_each_poll_reads_the_unanswered_turns_own_chat_and_message(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log,
        artifacts,
        scripts={"G042": [Attempt(outcome="no_answer", settles_after=2)]},
    )
    case = _booking_case("G042").model_copy(
        update={
            "history": [HistoryEntry.model_validate({"role": "user", "text": "hi"})]
        }
    )

    await _drive(stack, [case])

    (chat,) = {chat for _case_id, chat, _now in stack.posted()}
    reads = [call[1:] for call in stack.calls if call[0] == "read_thread"]
    assert reads == [(chat, "message of G042", [f"PLANTED-{chat}-0"])] * 3


async def test_an_unanswered_reply_turn_is_polled_until_it_settles_then_released(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log,
        artifacts,
        scripts={"G042": [Attempt(), Attempt(outcome="no_answer", settles_after=2)]},
    )

    run_dir = await _drive(stack, [_reply_case("G042"), _case("G043")])

    assert [p[0] for p in stack.posted()] == ["G042", "G042", "G043"]
    names = stack.names()
    reply_post = [i for i, name in enumerate(names) if name == "post_turn"][1]
    assert names[reply_post + 1 : reply_post + 7] == [
        "read_thread",
        "sleep",
        "read_thread",
        "sleep",
        "read_thread",
        "read_post_state",
    ]
    assert names[reply_post + 7] == "release"
    (chat,) = {chat for case_id, chat, _now in stack.posted() if case_id == "G042"}
    reads = [c for c in stack.calls if c[0] == "read_thread" and c[1] == chat]
    polls = reads[1:]
    assert {(call[2], tuple(call[3])) for call in polls} == {
        (_YES, tuple(sorted([f"MSG-{chat}", f"REPLY-{chat}"])))
    }
    assert _case_run(run_dir, "G042").excluded is None


async def test_an_unanswered_turn_of_a_case_with_no_fixture_settles_before_release(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log,
        artifacts,
        scripts={"G001": [Attempt(outcome="no_answer", settles_after=2), Attempt()]},
    )

    run_dir = await _drive(stack, [_case("G001")])

    assert _after_the_last_post(stack) == [
        "read_thread",
        "sleep",
        "read_thread",
        "sleep",
        "read_thread",
        "release",
    ]
    assert len(stack.posted()) == 1
    assert _case_run(run_dir, "G001").attempts == 1


async def test_a_turn_that_came_back_is_never_polled(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log,
        artifacts,
        scripts={"G042": _confirmed_cancel(), "G001": [Attempt(outcome="not_sent")]},
    )

    await _drive(stack, [_reply_case("G042"), _case("G001"), _booking_case("G043")])

    assert "sleep" not in stack.names()


def test_the_settle_wait_is_bounded_by_named_constants_on_the_real_clock() -> None:
    assert SettleWait() == DEFAULT_SETTLE
    assert DEFAULT_SETTLE.timeout_seconds == SETTLE_TIMEOUT_SECONDS
    assert DEFAULT_SETTLE.interval_seconds == SETTLE_INTERVAL_SECONDS
    assert (SETTLE_TIMEOUT_SECONDS, SETTLE_INTERVAL_SECONDS) == (60.0, 5.0)
    assert DEFAULT_SETTLE.sleep is asyncio.sleep


_NEVER_SETTLES = Attempt(outcome="no_answer", settles_after=None)


@pytest.mark.parametrize(
    ("case", "script", "fixture"),
    [
        pytest.param(_booking_case("G042"), [_NEVER_SETTLES], True, id="fixture"),
        pytest.param(_case("G042"), [_NEVER_SETTLES], False, id="no-fixture"),
        pytest.param(
            _reply_case("G042"), [Attempt(), _NEVER_SETTLES], True, id="reply-turn"
        ),
        pytest.param(
            _booking_case("G042"),
            [replace(_NEVER_SETTLES, outcome="protocol_error")],
            True,
            id="contract-break",
        ),
        pytest.param(
            _case("G042"),
            [replace(_NEVER_SETTLES, outcome="protocol_error")],
            False,
            id="contract-break-no-fixture",
        ),
        pytest.param(
            _reply_case("G042"),
            [Attempt(), replace(_NEVER_SETTLES, outcome="protocol_error")],
            True,
            id="contract-break-reply-turn",
        ),
    ],
)
async def test_a_turn_unsettled_at_the_bound_is_written_as_outcome_unknown_and_stops(
    log: Path,
    artifacts: Path,
    case: Case,
    script: list[Attempt],
    fixture: bool,
) -> None:
    stack = _stack(log, artifacts, scripts={"G042": list(script)})
    settle = SettleWait(timeout_seconds=20.0, interval_seconds=5.0, sleep=stack.sleep)

    with pytest.raises(TurnUnsettledError, match="G042"):
        await _drive(stack, [case, _case("G043")], settle=settle)

    assert [p[0] for p in stack.posted()] == ["G042"] * len(script)
    after = _after_the_last_post(stack)
    assert after[: 1 + 2 * 4] == ["read_thread", *(["sleep", "read_thread"] * 4)]
    # The turn may still write, so its patient is not released: the resume sweep is.
    assert "release" not in stack.names()
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == ["G042"]
    assert read_run(run_dir).cases == ["G042"]
    case_run = _case_run(run_dir, "G042")
    assert (case_run.attempts, case_run.excluded) == (
        1,
        ExclusionReason.OUTCOME_UNKNOWN,
    )
    assert case_run.cancelled_after == []
    assert (case_run.scheduling_after is not None) is fixture
    assert ("read_post_state" in after) is fixture


async def test_a_resumed_run_releases_an_unsettled_turns_patient_and_does_not_re_post(
    log: Path, artifacts: Path
) -> None:
    first = _stack(log, artifacts, scripts={"G042": [_NEVER_SETTLES]})
    cases = [_booking_case("G042"), _case("G043")]
    with pytest.raises(TurnUnsettledError):
        await _drive(first, cases)
    (run_dir,) = artifacts.iterdir()
    patient = _case_run(run_dir, "G042").patient_id
    stack = _stack(log, artifacts)

    await _resume(stack, run_dir, cases)

    assert stack.timeline[0] == ("release", patient)
    assert [p[0] for p in stack.posted()] == ["G043"]


@pytest.mark.parametrize(
    "error",
    [
        ThreadReadError("GET /chats/x/messages answered 500"),
        TurnProtocolError("two replies"),
        httpx.ReadTimeout("timed out"),
    ],
)
@pytest.mark.parametrize(
    ("case", "script"),
    [
        pytest.param(_booking_case("G042"), [_NEVER_SETTLES], id="first-turn"),
        pytest.param(_reply_case("G042"), [Attempt(), _NEVER_SETTLES], id="reply-turn"),
    ],
)
async def test_a_poll_that_cannot_read_the_thread_writes_a_fixture_case_and_stops(
    log: Path,
    artifacts: Path,
    error: Exception,
    case: Case,
    script: list[Attempt],
) -> None:
    # The turn's own thread reads come first; the failing read is its first poll.
    stack = _stack(
        log,
        artifacts,
        scripts={"G042": list(script)},
        failures_during={("read_thread", len(script) + 1): error},
    )

    with pytest.raises(type(error)) as raised:
        await _drive(stack, [case, _case("G043")])

    assert raised.value is error
    assert [p[0] for p in stack.posted()] == ["G042"] * len(script)
    assert _after_the_last_post(stack)[:3] == ["read_thread", "sleep", "read_thread"]
    assert "release" not in stack.names()
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == ["G042"]
    case_run = _case_run(run_dir, "G042")
    assert case_run.excluded is ExclusionReason.OUTCOME_UNKNOWN
    assert case_run.scheduling_after == [_PLANTED]
    assert case_run.cancelled_after == []


async def test_a_poll_that_cannot_read_the_thread_stops_an_unfixtured_case_unwritten(
    log: Path, artifacts: Path
) -> None:
    error = ThreadReadError("GET /chats/x/messages answered 500")
    stack = _stack(
        log,
        artifacts,
        scripts={"G001": [_NEVER_SETTLES]},
        failures_during={("read_thread", 2): error},
    )

    with pytest.raises(ThreadReadError) as raised:
        await _drive(stack, [_case("G001"), _case("G002")])

    assert raised.value is error
    assert "release" not in stack.names()
    assert [p[0] for p in stack.posted()] == ["G001"]
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == []


# --- a turn that settles is judged as a completed turn (FR-041c, FR-007b) -----------


@pytest.mark.parametrize(
    ("settles_with", "excluded"),
    [("reply", None), ("assistant_failed", ExclusionReason.RUN_ERROR)],
)
@pytest.mark.parametrize("fixture", [True, False], ids=["fixture", "no-fixture"])
async def test_a_first_turn_that_settles_is_judged_as_a_completed_turn(
    log: Path,
    artifacts: Path,
    settles_with: str,
    excluded: ExclusionReason | None,
    fixture: bool,
) -> None:
    script = Attempt(
        outcome="no_answer",
        intents=("booking",),
        tools=("list_my_appointments",),
        settles_after=2,
        settles_with=settles_with,
    )
    stack = _stack(log, artifacts, scripts={"G042": [script]})
    case = _booking_case("G042") if fixture else _case("G042", "booking")

    run_dir = await _drive(stack, [case, _case("G043")])

    # Never retried, and the run goes on to the next case.
    assert [p[0] for p in stack.posted()] == ["G042", "G043"]
    case_run = _case_run(run_dir, "G042")
    assert (case_run.attempts, case_run.excluded) == (1, excluded)
    assert case_run.terminal is not None
    assert case_run.terminal.kind is TerminalKind.ERROR
    (chat,) = {chat for case_id, chat, _now in stack.posted() if case_id == "G042"}
    assert case_run.patient_message is not None
    assert case_run.patient_message.id == f"MSG-{chat}"
    stored_reply = case_run.assistant_message is not None
    assert stored_reply is (settles_with == "reply")
    marked = case_run.patient_message.attention_mark is AttentionMark.ASSISTANT_FAILED
    assert marked is (settles_with == "assistant_failed")
    # The slice is taken by the turn's own offset and turn_id, as for any turn.
    assert case_run.events is not None
    assert {event["turn_id"] for event in case_run.events} == {f"MSG-{chat}"}
    assert case_run.segments is not None
    assert [s.intent for s in case_run.segments.segments] == ["booking"]
    if fixture:
        assert case_run.scheduling_after == [_PLANTED]
        assert case_run.cancelled_after == [_CANCELLED]
    else:
        assert case_run.scheduling_after is None


@pytest.mark.parametrize("outcome", ["no_answer", "protocol_error"])
async def test_a_first_turn_that_settles_with_a_reply_continues_to_its_reply_turn(
    log: Path, artifacts: Path, outcome: str
) -> None:
    confirm, cancel = _confirmed_cancel()
    stack = _stack(
        log,
        artifacts,
        scripts={"G042": [replace(confirm, outcome=outcome), cancel]},
    )

    run_dir = await _drive(stack, [_reply_case("G042"), _case("G043")])

    assert [p[0] for p in stack.posted()] == ["G042", "G042", "G043"]
    assert [message for _chat, message in stack.messages][1] == _YES
    case_run = _case_run(run_dir, "G042")
    assert (case_run.attempts, case_run.excluded) == (1, None)
    assert case_run.scheduling_before_reply == [_PLANTED]
    assert case_run.scheduling_after == [_CANCELLED]
    assert case_run.reply_turn is not None
    assert case_run.reply_turn.events is not None


@pytest.mark.parametrize(
    ("settles_with", "excluded"),
    [("reply", None), ("assistant_failed", ExclusionReason.RUN_ERROR)],
)
async def test_a_reply_turn_that_settles_is_judged_as_a_completed_turn(
    log: Path, artifacts: Path, settles_with: str, excluded: ExclusionReason | None
) -> None:
    confirm, cancel = _confirmed_cancel()
    stack = _stack(
        log,
        artifacts,
        scripts={
            "G042": [
                confirm,
                replace(
                    cancel,
                    outcome="no_answer",
                    settles_after=2,
                    settles_with=settles_with,
                ),
            ]
        },
    )

    run_dir = await _drive(stack, [_reply_case("G042"), _case("G043")])

    assert [p[0] for p in stack.posted()] == ["G042", "G042", "G043"]
    case_run = _case_run(run_dir, "G042")
    assert (case_run.attempts, case_run.excluded) == (1, excluded)
    reply = case_run.reply_turn
    assert reply is not None
    assert reply.patient_message is not None
    assert (reply.assistant_message is not None) is (settles_with == "reply")
    assert reply.events is not None
    assert {event["turn_id"] for event in reply.events} == {reply.patient_message.id}
    assert case_run.scheduling_after == [_CANCELLED]
    names = stack.names()
    assert names.index("read_post_state", names.index("sleep")) < names.index("release")


# --- a stream that broke the contract mid-answer waits too (FR-041c) ---------------


@pytest.mark.parametrize(
    ("case", "script", "fixture"),
    [
        pytest.param(
            _booking_case("G042"),
            [Attempt(outcome="protocol_error", settles_after=2)],
            True,
            id="first-turn",
        ),
        pytest.param(
            _case("G042"),
            [Attempt(outcome="protocol_error", settles_after=2)],
            False,
            id="first-turn-no-fixture",
        ),
        pytest.param(
            _reply_case("G042"),
            [Attempt(), Attempt(outcome="protocol_error", settles_after=2)],
            True,
            id="reply-turn",
        ),
    ],
)
async def test_a_contract_break_mid_stream_settles_before_release_and_is_then_judged(
    log: Path,
    artifacts: Path,
    case: Case,
    script: list[Attempt],
    fixture: bool,
) -> None:
    # FakeStack.release itself asserts the case file is not yet written.
    stack = _stack(log, artifacts, scripts={"G042": list(script)})

    run_dir = await _drive(stack, [case, _case("G043")])

    assert [p[0] for p in stack.posted()] == ["G042"] * len(script) + ["G043"]
    names = stack.names()
    last_post = [i for i, name in enumerate(names) if name == "post_turn"][-2]
    assert names[last_post + 1 : names.index("release", last_post)] == [
        "read_thread",
        "sleep",
        "read_thread",
        "sleep",
        "read_thread",
        *(["read_post_state"] if fixture else []),
    ]
    case_run = _case_run(run_dir, "G042")
    assert (case_run.attempts, case_run.excluded) == (1, None)
    # No terminal event was read to its end: the stream broke before one could be.
    turn = case_run.reply_turn if case_run.reply_turn is not None else case_run
    assert turn.terminal is None
    assert turn.assistant_message is not None
    assert turn.events is not None


@pytest.mark.parametrize("outcome", ["no_answer", "protocol_error"])
@pytest.mark.parametrize(
    ("case", "script", "written"),
    [
        pytest.param(_booking_case("G042"), [], True, id="first-turn"),
        pytest.param(_case("G042"), [], False, id="first-turn-no-fixture"),
        pytest.param(_reply_case("G042"), [Attempt()], True, id="reply-turn"),
    ],
)
async def test_an_unwhole_answer_whose_own_thread_read_fails_keeps_its_patient(
    log: Path,
    artifacts: Path,
    outcome: str,
    case: Case,
    script: list[Attempt],
    written: bool,
) -> None:
    # The turn was not seen to end, so it is treated as a poll that cannot read the
    # thread: the run stops, and the patient is not released.
    error = ThreadReadError("GET /chats/x/messages answered 500")
    stack = _stack(
        log,
        artifacts,
        scripts={"G042": [*script, Attempt(outcome=outcome, settles_after=None)]},
        failures_during={("read_thread", len(script) + 1): error},
    )

    with pytest.raises(ThreadReadError) as raised:
        await _drive(stack, [case, _case("G043")])

    assert raised.value is error
    assert "release" not in stack.names()
    assert "sleep" not in stack.names()
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == (["G042"] if written else [])
    if written:
        case_run = _case_run(run_dir, "G042")
        assert case_run.excluded is ExclusionReason.OUTCOME_UNKNOWN
        assert case_run.scheduling_after == [_PLANTED]
        assert case_run.cancelled_after == []


# --- every poll checks for a restart (FR-041c, FR-047c) -----------------------------


_WAITED_ON = pytest.mark.parametrize(
    ("case", "script"),
    [
        pytest.param(_booking_case("G042"), [_NEVER_SETTLES], id="first-turn"),
        pytest.param(_case("G042"), [_NEVER_SETTLES], id="first-turn-no-fixture"),
        pytest.param(_reply_case("G042"), [Attempt(), _NEVER_SETTLES], id="reply-turn"),
        pytest.param(
            _booking_case("G042"),
            [replace(_NEVER_SETTLES, outcome="protocol_error")],
            id="contract-break",
        ),
    ],
)


@_WAITED_ON
async def test_a_restart_with_other_settings_while_a_turn_is_waited_on_stops_that_poll(
    log: Path, artifacts: Path, case: Case, script: list[Attempt]
) -> None:
    # The restart is logged during the second poll's read, well inside the 60s bound.
    stack = _stack(
        log,
        artifacts,
        scripts={"G042": list(script)},
        restarts_during={("read_thread", len(script) + 2): _OTHER_SETTINGS},
    )

    with pytest.raises(ServiceRestartedError, match="G042"):
        await _drive(stack, [case, _case("G043")])

    assert [p[0] for p in stack.posted()] == ["G042"] * len(script)
    # Stopped on that poll, not after the bound; the restart ended the process running
    # the turn, so nothing of it can land later and its patient is released.
    assert _after_the_last_post(stack) == [
        "read_thread",
        "sleep",
        "read_thread",
        "sleep",
        "read_thread",
        "release",
    ]
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == []
    assert read_run(run_dir).cases == []


async def test_a_turn_stopped_by_a_restart_while_waited_on_is_re_posted_on_resume(
    log: Path, artifacts: Path
) -> None:
    first = _stack(
        log,
        artifacts,
        scripts={"G042": [_NEVER_SETTLES]},
        restarts_during={("read_thread", 2): _OTHER_SETTINGS},
    )
    cases = [_booking_case("G042"), _case("G043")]
    with pytest.raises(ServiceRestartedError):
        await _drive(first, cases)
    (run_dir,) = artifacts.iterdir()
    _append(log, _configured())
    stack = _stack(log, artifacts)

    await _resume(stack, run_dir, cases)

    assert [p[0] for p in stack.posted()] == ["G042", "G043"]
    assert _case_run(run_dir, "G042").excluded is None


async def test_a_poll_that_cannot_read_the_thread_during_a_restart_stops_as_the_restart(
    log: Path, artifacts: Path
) -> None:
    error = httpx.ConnectError("connection refused")
    stack = _stack(
        log,
        artifacts,
        scripts={"G042": [_NEVER_SETTLES]},
        restarts_during={("read_thread", 2): _OTHER_SETTINGS},
        failures_during={("read_thread", 2): error},
    )

    with pytest.raises(ServiceRestartedError) as raised:
        await _drive(stack, [_booking_case("G042"), _case("G043")])

    assert raised.value.__cause__ is error
    assert _after_the_last_post(stack) == [
        "read_thread",
        "sleep",
        "read_thread",
        "release",
    ]
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == []


async def test_a_restart_with_the_same_settings_while_a_turn_is_waited_on_is_passed(
    log: Path, artifacts: Path
) -> None:
    stack = _stack(
        log,
        artifacts,
        scripts={"G042": [Attempt(outcome="no_answer", settles_after=3)]},
        restarts_during={("read_thread", 2): _configured()},
    )

    run_dir = await _drive(stack, [_booking_case("G042"), _case("G043")])

    assert [p[0] for p in stack.posted()] == ["G042", "G043"]
    assert _case_run(run_dir, "G042").excluded is None


# --- a restart under other settings exempts a posted case from FR-007d ----------------


async def test_a_posted_fixture_case_caught_by_a_restart_is_re_posted_on_resume(
    log: Path, artifacts: Path
) -> None:
    first = _stack(log, artifacts, scripts={"G042": [Attempt(restart=_OTHER_SETTINGS)]})
    cases = [_booking_case("G042"), _case("G043")]
    with pytest.raises(ServiceRestartedError, match="G042"):
        await _drive(first, cases)
    (run_dir,) = artifacts.iterdir()
    assert recorded_case_ids(run_dir) == []
    (stopped_chat,) = [chat for _case_id, chat, _now in first.posted()]
    # The service comes back under the run's own settings.
    _append(log, _configured())
    stack = _stack(log, artifacts, session_patient_ids=[f"PAT-{stopped_chat}"])

    await _resume(stack, run_dir, cases)

    assert [p[0] for p in stack.posted()] == ["G042", "G043"]
    names = stack.names()
    assert stack.timeline[0] == ("release", f"PAT-{stopped_chat}")
    assert names.index("release") < names.index("post_turn")
    assert _case_run(run_dir, "G042").excluded is None


# --- a settled turn's hand-off is read from its own turn.completed (FR-041d) ---------

# How a turn's answer can fail to arrive whole and the turn still end: its stream broke
# off or broke the contract, and its thread showed it ended on its own read (0) or on
# the first poll (1).
_UNWHOLE_AND_ENDED = [
    pytest.param(outcome, settles_after, id=f"{outcome}-ended-on-read-{settles_after}")
    for outcome in ("no_answer", "protocol_error")
    for settles_after in (0, 1)
]


@pytest.mark.parametrize(("outcome", "settles_after"), _UNWHOLE_AND_ENDED)
async def test_a_settled_first_turn_logged_as_handed_off_posts_no_reply(
    log: Path, artifacts: Path, outcome: str, settles_after: int
) -> None:
    confirm, cancel = _confirmed_cancel()
    handed_off = replace(
        confirm,
        outcome=outcome,
        settles_after=settles_after,
        completed=("handed_off",),
    )
    stack = _stack(log, artifacts, scripts={"G042": [handed_off, cancel]})

    run_dir = await _drive(stack, [_reply_case("G042"), _case("G043")])

    assert [p[0] for p in stack.posted()] == ["G042", "G043"]
    assert _YES not in [message for _chat, message in stack.messages]
    case_run = _case_run(run_dir, "G042")
    # A handed-off first turn: excluded from retrieval and serving, and scored on
    # booking against the appointments read after it (FR-037b, FR-018a).
    assert (case_run.attempts, case_run.excluded) == (
        1,
        ExclusionReason.HANDED_OFF_TURN,
    )
    assert (case_run.reply_turn, case_run.scheduling_before_reply) == (None, None)
    assert case_run.scheduling_after == [_PLANTED]
    assert case_run.cancelled_after == [_CANCELLED]
    assert case_run.events is not None


@pytest.mark.parametrize(("outcome", "settles_after"), _UNWHOLE_AND_ENDED)
async def test_a_settled_first_turn_of_a_case_with_no_fixture_logged_as_handed_off(
    log: Path, artifacts: Path, outcome: str, settles_after: int
) -> None:
    script = Attempt(
        outcome=outcome, settles_after=settles_after, completed=("handed_off",)
    )
    stack = _stack(log, artifacts, scripts={"G042": [script]})

    run_dir = await _drive(stack, [_case("G042"), _case("G043")])

    case_run = _case_run(run_dir, "G042")
    assert (case_run.attempts, case_run.excluded) == (
        1,
        ExclusionReason.HANDED_OFF_TURN,
    )


@pytest.mark.parametrize(("outcome", "settles_after"), _UNWHOLE_AND_ENDED)
async def test_a_settled_reply_turn_logged_as_handed_off_records_handed_off_turn(
    log: Path, artifacts: Path, outcome: str, settles_after: int
) -> None:
    confirm, cancel = _confirmed_cancel()
    handed_off = replace(
        cancel,
        outcome=outcome,
        settles_after=settles_after,
        cancels=False,
        completed=("handed_off",),
    )
    stack = _stack(log, artifacts, scripts={"G042": [confirm, handed_off]})

    run_dir = await _drive(stack, [_reply_case("G042"), _case("G043")])

    assert [p[0] for p in stack.posted()] == ["G042", "G042", "G043"]
    case_run = _case_run(run_dir, "G042")
    assert (case_run.attempts, case_run.excluded) == (
        1,
        ExclusionReason.HANDED_OFF_TURN,
    )
    assert case_run.reply_turn is not None
    assert case_run.reply_turn.events is not None
    assert case_run.scheduling_before_reply == [_PLANTED]
    assert case_run.scheduling_after == [_PLANTED]


# Slices that do not say the turn handed off: another outcome, no `turn.completed` at
# all, or several - the service logs exactly one per turn, so several say nothing.
_NOT_LOGGED_AS_HANDED_OFF = [
    pytest.param(("faq",), id="faq"),
    pytest.param(("booking",), id="booking"),
    pytest.param(("merged",), id="merged"),
    pytest.param((), id="no-turn-completed"),
    pytest.param(("handed_off", "booking"), id="several-disagreeing"),
    pytest.param(("handed_off", "handed_off"), id="several-agreeing"),
]


@pytest.mark.parametrize("completed", _NOT_LOGGED_AS_HANDED_OFF)
@pytest.mark.parametrize(("outcome", "settles_after"), _UNWHOLE_AND_ENDED)
async def test_a_settled_first_turn_not_logged_as_handed_off_goes_on_to_its_reply(
    log: Path,
    artifacts: Path,
    outcome: str,
    settles_after: int,
    completed: tuple[str, ...],
) -> None:
    confirm, cancel = _confirmed_cancel()
    first = replace(
        confirm, outcome=outcome, settles_after=settles_after, completed=completed
    )
    stack = _stack(log, artifacts, scripts={"G042": [first, cancel]})

    run_dir = await _drive(stack, [_reply_case("G042"), _case("G043")])

    assert [p[0] for p in stack.posted()] == ["G042", "G042", "G043"]
    case_run = _case_run(run_dir, "G042")
    assert (case_run.attempts, case_run.excluded) == (1, None)
    assert case_run.reply_turn is not None
    assert case_run.scheduling_after == [_CANCELLED]


@pytest.mark.parametrize("completed", _NOT_LOGGED_AS_HANDED_OFF)
@pytest.mark.parametrize(("outcome", "settles_after"), _UNWHOLE_AND_ENDED)
async def test_a_settled_reply_turn_not_logged_as_handed_off_is_not_a_hand_off(
    log: Path,
    artifacts: Path,
    outcome: str,
    settles_after: int,
    completed: tuple[str, ...],
) -> None:
    confirm, cancel = _confirmed_cancel()
    reply = replace(
        cancel, outcome=outcome, settles_after=settles_after, completed=completed
    )
    stack = _stack(log, artifacts, scripts={"G042": [confirm, reply]})

    run_dir = await _drive(stack, [_reply_case("G042")])

    case_run = _case_run(run_dir, "G042")
    assert (case_run.attempts, case_run.excluded) == (1, None)
    assert case_run.reply_turn is not None
    assert case_run.scheduling_after == [_CANCELLED]


async def test_a_delivered_terminal_event_decides_the_hand_off_not_the_log(
    log: Path, artifacts: Path
) -> None:
    # A `done` that is not a hand-off is believed over a log saying otherwise, on both
    # turns; a delivered hand-off is believed over a log naming another outcome.
    confirm, cancel = _confirmed_cancel()
    stack = _stack(
        log,
        artifacts,
        scripts={
            "G042": [
                replace(confirm, completed=("handed_off",)),
                replace(cancel, completed=("handed_off",)),
            ],
            "G043": [Attempt(outcome="hand_off", completed=("faq",))],
        },
    )

    run_dir = await _drive(stack, [_reply_case("G042"), _case("G043")])

    assert [p[0] for p in stack.posted()] == ["G042", "G042", "G043"]
    assert _case_run(run_dir, "G042").excluded is None
    assert _case_run(run_dir, "G043").excluded is ExclusionReason.HANDED_OFF_TURN


# --- whether a turn's stream broke the contract is recorded (FR-041d) ---------------


@pytest.mark.parametrize(
    ("outcome", "broke"),
    [("protocol_error", True), ("no_answer", False), ("done", False)],
)
async def test_a_first_turn_records_whether_its_stream_broke_the_contract(
    log: Path, artifacts: Path, outcome: str, broke: bool
) -> None:
    confirm, cancel = _confirmed_cancel()
    stack = _stack(
        log, artifacts, scripts={"G042": [replace(confirm, outcome=outcome), cancel]}
    )

    run_dir = await _drive(stack, [_reply_case("G042")])

    case_run = _case_run(run_dir, "G042")
    assert case_run.excluded is None
    assert case_run.stream_broke_contract is broke
    assert case_run.reply_turn is not None
    assert case_run.reply_turn.stream_broke_contract is False


@pytest.mark.parametrize(
    ("outcome", "broke"),
    [("protocol_error", True), ("no_answer", False), ("done", False)],
)
async def test_a_reply_turn_records_whether_its_stream_broke_the_contract(
    log: Path, artifacts: Path, outcome: str, broke: bool
) -> None:
    confirm, cancel = _confirmed_cancel()
    stack = _stack(
        log, artifacts, scripts={"G042": [confirm, replace(cancel, outcome=outcome)]}
    )

    run_dir = await _drive(stack, [_reply_case("G042")])

    case_run = _case_run(run_dir, "G042")
    assert case_run.excluded is None
    assert case_run.stream_broke_contract is False
    assert case_run.reply_turn is not None
    assert case_run.reply_turn.stream_broke_contract is broke


@pytest.mark.parametrize(
    ("case", "script"),
    [
        pytest.param(
            _booking_case("G042"),
            [Attempt(outcome="protocol_error", settles_after=None)],
            id="first-turn",
        ),
        pytest.param(
            _case("G042"),
            [Attempt(outcome="protocol_error", settles_after=None)],
            id="first-turn-no-fixture",
        ),
        pytest.param(
            _reply_case("G042"),
            [Attempt(), Attempt(outcome="protocol_error", settles_after=None)],
            id="reply-turn",
        ),
    ],
)
async def test_an_unsettled_stream_that_broke_the_contract_is_recorded_as_broken(
    log: Path, artifacts: Path, case: Case, script: list[Attempt]
) -> None:
    stack = _stack(log, artifacts, scripts={"G042": list(script)})

    with pytest.raises(TurnUnsettledError):
        await _drive(stack, [case])

    (run_dir,) = artifacts.iterdir()
    case_run = _case_run(run_dir, "G042")
    assert case_run.excluded is ExclusionReason.OUTCOME_UNKNOWN
    turn = case_run.reply_turn if case_run.reply_turn is not None else case_run
    assert (turn.terminal, turn.stream_broke_contract) == (None, True)


@pytest.mark.parametrize(
    ("case", "script", "failing_read"),
    [
        pytest.param(
            _booking_case("G042"),
            [Attempt(outcome="protocol_error")],
            1,
            id="first-turn",
        ),
        pytest.param(
            _reply_case("G042"),
            [Attempt(), Attempt(outcome="protocol_error")],
            2,
            id="reply-turn",
        ),
    ],
)
async def test_a_broken_stream_whose_own_thread_read_fails_is_recorded_as_broken(
    log: Path, artifacts: Path, case: Case, script: list[Attempt], failing_read: int
) -> None:
    error = ThreadReadError("the thread is unreadable")
    stack = _stack(
        log,
        artifacts,
        scripts={"G042": list(script)},
        failures_during={("read_thread", failing_read): error},
    )

    with pytest.raises(ThreadReadError):
        await _drive(stack, [case])

    (run_dir,) = artifacts.iterdir()
    case_run = _case_run(run_dir, "G042")
    assert case_run.excluded is ExclusionReason.OUTCOME_UNKNOWN
    turn = case_run.reply_turn if case_run.reply_turn is not None else case_run
    assert (turn.terminal, turn.stream_broke_contract) == (None, True)
