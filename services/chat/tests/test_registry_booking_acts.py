"""The registry records every write a tool attempts, around the handler, in one place.

One test per row of contracts/booking-acts.md's recording table, plus the mechanism's
invariants: a row exists before the handler runs (1), no row means no request (2), and
arguments that could not be read leave no trace (6).
"""

import asyncio
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from chat.agent.booking_acts import (
    ActHandle,
    DatabaseBookingActRecorder,
    PlannedAct,
    Settlement,
    settlement_from,
)
from chat.agent.tools.registry import (
    _NO_PATIENT_RESULT,
    Tool,
    ToolArgumentError,
    ToolContext,
    ToolRegistry,
    ToolResult,
    required_id_argument,
)
from chat.core.config import Settings
from chat.db.session import session_factory
from chat.domain.models import (
    BookingAct,
    BookingActOperation,
    BookingActOutcome,
    MessageSender,
)
from chat.repositories import chat_repository
from sqlalchemy import select
from structlog.testing import capture_logs
from ulid import ULID

_OSLER = "01PRACT0000000000000000000"
_STARTS_AT = datetime(2027, 1, 12, 10, 0)
_ARGUMENTS = {"practitioner_id": _OSLER}
_WRITE = "book_something"
_READ = "read_something"


class _SpyRecorder:
    """A recorder that remembers every call and can be told to fail any write."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.unsettled: set[str] = set()
        self.fail_begin: Exception | None = None
        self.fail_not_sent: Exception | None = None
        self.fail_settle: Exception | None = None
        self._names = {_OSLER: "William Osler"}

    def learn_practitioners(self, roster: list[object]) -> None:
        self.calls.append(("learn_practitioners", roster))

    def name_of(self, practitioner_id: str) -> str | None:
        return self._names.get(practitioner_id)

    async def begin(self, planned: PlannedAct) -> ActHandle:
        self.calls.append(("begin", planned))
        if self.fail_begin is not None:
            raise self.fail_begin
        handle = ActHandle(act_id=f"act-{len(self.calls)}")
        self.unsettled.add(handle.act_id)
        return handle

    async def record_not_sent(self, planned: PlannedAct) -> None:
        self.calls.append(("record_not_sent", planned))
        if self.fail_not_sent is not None:
            raise self.fail_not_sent

    async def settle(self, handle: ActHandle, settlement: Settlement) -> None:
        self.calls.append(("settle", (handle, settlement)))
        if self.fail_settle is not None:
            raise self.fail_settle
        self.unsettled.discard(handle.act_id)

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


def _plan(context: ToolContext, arguments: dict[str, Any]) -> PlannedAct:
    return PlannedAct.book(
        practitioner_id=required_id_argument(arguments, "practitioner_id"),
        starts_at=_STARTS_AT,
        name_of=context.acts.name_of,
    )


class _Handler:
    """A write handler that checks, as it starts, that its act is already recorded."""

    def __init__(
        self,
        recorder: _SpyRecorder | None,
        result: ToolResult,
        *,
        raises: BaseException | None = None,
        started: asyncio.Event | None = None,
        release: asyncio.Event | None = None,
    ) -> None:
        self.recorder = recorder
        self.result = result
        self.raises = raises
        self.started = started
        self.release = release
        self.calls = 0
        self.unsettled_when_called: set[str] | None = None

    async def __call__(
        self, _context: ToolContext, _arguments: dict[str, Any]
    ) -> ToolResult:
        self.calls += 1
        if self.recorder is not None:
            # Invariant 1: the act is on record, unsettled, before anything is sent.
            self.unsettled_when_called = set(self.recorder.unsettled)
            assert self.recorder.unsettled, "the handler ran before its act existed"
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            await self.release.wait()
        if self.raises is not None:
            raise self.raises
        return self.result


def _registry(
    handler: _Handler,
    recorder: object,
    *,
    patient_id: str | None = "01PATIENT",
    read_handler: _Handler | None = None,
) -> ToolRegistry:
    tools = [
        Tool(
            name=_WRITE,
            description="a write",
            input_schema={"type": "object", "properties": {}},
            handler=handler,
            requires_patient=True,
            writes=True,
            plan_act=_plan,
        ),
        Tool(
            name=_READ,
            description="a read",
            input_schema={"type": "object", "properties": {}},
            handler=read_handler or _Handler(None, {"practitioners": []}),
            requires_patient=True,
        ),
    ]
    context = ToolContext(
        channel=MagicMock(),
        settings=MagicMock(spec=Settings),
        session_id="01SESSION",
        patient_id=patient_id,
        local_now=datetime(2026, 8, 17, 8, 0),
        acts=recorder,  # type: ignore[arg-type]
    )
    return ToolRegistry(tools, context)


def _booked() -> ToolResult:
    return {
        "status": "booked",
        "appointment": {
            "id": "01APPT00000000000000000000",
            "practitioner_full_name": "Sir William Osler",
            "starts_at": "2027-01-12T10:00:00",
            "ends_at": "2027-01-12T11:00:00",
        },
    }


# --- arguments invalid: no row, and the error raises (invariant 6) -----------------


async def test_arguments_that_cannot_be_read_record_nothing_and_raise() -> None:
    # A malformed call is not an attempt: it could not have reached the schedule, so it
    # leaves no trace in the record.
    recorder = _SpyRecorder()
    handler = _Handler(recorder, _booked())

    with pytest.raises(ToolArgumentError, match="practitioner_id"):
        await _registry(handler, recorder, patient_id="01PATIENT").dispatch(
            _WRITE, {"practitioner_id": "not an id"}
        )

    assert recorder.calls == []
    assert handler.calls == 0


async def test_unreadable_arguments_in_a_chat_with_no_patient_answer_as_before() -> (
    None
):
    # FR-021a: the model must see exactly what it saw before 016. A write in a chat
    # with no patient record was answered as such whatever its arguments, so recording
    # does not get to turn that answer into an argument error - and a malformed call is
    # still no attempt, so nothing is recorded either.
    recorder = _SpyRecorder()
    handler = _Handler(recorder, _booked())

    result = await _registry(handler, recorder, patient_id=None).dispatch(
        _WRITE, {"practitioner_id": "not an id"}
    )

    assert result == _NO_PATIENT_RESULT
    assert recorder.calls == []
    assert handler.calls == 0


# --- no patient: a not-sent row, and the handler is not called ---------------------


async def test_a_chat_with_no_patient_records_the_attempt_as_not_sent() -> None:
    recorder = _SpyRecorder()
    handler = _Handler(recorder, _booked())

    result = await _registry(handler, recorder, patient_id=None).dispatch(
        _WRITE, _ARGUMENTS
    )

    assert result == _NO_PATIENT_RESULT
    assert handler.calls == 0
    assert recorder.names() == ["record_not_sent"]
    planned = recorder.calls[0][1]
    assert isinstance(planned, PlannedAct)
    assert planned.practitioner_id == _OSLER
    assert planned.practitioner_full_name == "William Osler"


async def test_a_write_not_requiring_a_patient_runs_recorded_without_one() -> None:
    # The no-patient answer belongs to a tool that declared `requires_patient`, exactly
    # as it does on the unrecorded path. A write that did not declare it is sent, and
    # recorded around its handler like any other.
    recorder = _SpyRecorder()
    handler = _Handler(recorder, _booked())
    registry = ToolRegistry(
        [
            Tool(
                name=_WRITE,
                description="a write",
                input_schema={"type": "object", "properties": {}},
                handler=handler,
                writes=True,
                plan_act=_plan,
            )
        ],
        ToolContext(
            channel=MagicMock(),
            settings=MagicMock(spec=Settings),
            session_id="01SESSION",
            patient_id=None,
            local_now=datetime(2026, 8, 17, 8, 0),
            acts=recorder,  # type: ignore[arg-type]
        ),
    )

    result = await registry.dispatch(_WRITE, _ARGUMENTS)

    assert result == _booked()
    assert handler.calls == 1
    assert recorder.names() == ["begin", "settle"]


async def test_a_not_sent_record_that_fails_is_logged_and_still_answers() -> None:
    # Nothing reached the scheduler, so no act can be missing: the call still answers
    # exactly as it would have.
    recorder = _SpyRecorder()
    recorder.fail_not_sent = RuntimeError("store down")

    with capture_logs() as logs:
        result = await _registry(
            _Handler(recorder, _booked()), recorder, patient_id=None
        ).dispatch(_WRITE, _ARGUMENTS)

    assert result == _NO_PATIENT_RESULT
    (failed,) = [e for e in logs if e["event"] == "booking_act.record_failed"]
    assert failed["log_level"] == "error"
    assert failed["tool_name"] == _WRITE
    assert failed["operation"] == BookingActOperation.BOOK
    assert failed["stage"] == "not_sent"
    assert failed["error_type"] == "RuntimeError"
    assert failed["error_detail"] == "store down"


# --- begin fails: no row, no request (invariant 2) --------------------------------


async def test_an_act_that_cannot_be_recorded_is_never_sent() -> None:
    recorder = _SpyRecorder()
    recorder.fail_begin = RuntimeError("store down")
    handler = _Handler(recorder, _booked())

    with capture_logs() as logs:
        result = await _registry(handler, recorder).dispatch(_WRITE, _ARGUMENTS)

    assert handler.calls == 0
    assert result["status"] == "unavailable"
    assert recorder.names() == ["begin"]
    (failed,) = [e for e in logs if e["event"] == "booking_act.record_failed"]
    assert failed["log_level"] == "error"
    assert failed["tool_name"] == _WRITE
    assert failed["operation"] == BookingActOperation.BOOK
    assert failed["stage"] == "begin"
    assert failed["error_type"] == "RuntimeError"
    assert failed["error_detail"] == "store down"


async def test_an_unrecorded_act_tells_the_model_nothing_was_changed() -> None:
    # "Unavailable" is a claim that nothing happened, and here it is true: the request
    # never left. The explanation says so without naming the record, which the agent
    # never sees.
    recorder = _SpyRecorder()
    recorder.fail_begin = RuntimeError("store down")

    result = await _registry(_Handler(recorder, _booked()), recorder).dispatch(
        _WRITE, _ARGUMENTS
    )

    assert "nothing" in result["explanation"].lower()
    assert "record" not in result["explanation"].lower()


# --- the handler answers: settled from its answer, returned unchanged --------------


@pytest.mark.parametrize(
    ("result", "outcome"),
    [
        (_booked(), BookingActOutcome.DONE),
        (
            {
                "status": "changed",
                "change": "cancelled",
                "appointment": _booked()["appointment"],
                "previous_starts_at": "2027-01-12T10:00:00",
                "previous_practitioner_full_name": "Sir William Osler",
            },
            BookingActOutcome.DONE,
        ),
        (
            {"status": "unchanged", "appointment": _booked()["appointment"]},
            BookingActOutcome.UNCHANGED,
        ),
        (
            {"status": "refused", "reason": "practitioner_busy", "explanation": "x"},
            BookingActOutcome.REFUSED,
        ),
        ({"status": "unavailable", "explanation": "x"}, BookingActOutcome.NOT_SENT),
        ({"status": "unknown", "explanation": "x"}, BookingActOutcome.UNKNOWN),
    ],
    ids=["booked", "changed", "unchanged", "refused", "unavailable", "unknown"],
)
async def test_each_answer_settles_its_act_and_reaches_the_model_unchanged(
    result: ToolResult, outcome: BookingActOutcome
) -> None:
    recorder = _SpyRecorder()
    handler = _Handler(recorder, result)

    returned = await _registry(handler, recorder).dispatch(_WRITE, _ARGUMENTS)

    assert returned == result
    assert handler.calls == 1
    assert recorder.names() == ["begin", "settle"]
    handle, settlement = recorder.calls[1][1]  # type: ignore[misc]
    assert handle == ActHandle(act_id="act-1")
    assert settlement == settlement_from(BookingActOperation.BOOK, result)
    assert settlement.outcome is outcome
    assert recorder.unsettled == set()


async def test_the_handler_runs_only_once_its_act_is_recorded() -> None:
    recorder = _SpyRecorder()
    handler = _Handler(recorder, _booked())

    await _registry(handler, recorder).dispatch(_WRITE, _ARGUMENTS)

    assert handler.unsettled_when_called == {"act-1"}


# --- the handler raises, or the turn is cancelled: left unsettled ------------------


async def test_a_handler_that_raises_leaves_its_act_unsettled() -> None:
    recorder = _SpyRecorder()
    handler = _Handler(recorder, _booked(), raises=RuntimeError("wire broke"))

    with pytest.raises(RuntimeError, match="wire broke"):
        await _registry(handler, recorder).dispatch(_WRITE, _ARGUMENTS)

    assert recorder.names() == ["begin"]
    assert recorder.unsettled == {"act-1"}


async def test_a_turn_cancelled_mid_write_leaves_its_act_unsettled() -> None:
    recorder = _SpyRecorder()
    started = asyncio.Event()
    handler = _Handler(recorder, _booked(), started=started, release=asyncio.Event())

    call = asyncio.create_task(
        _registry(handler, recorder).dispatch(_WRITE, _ARGUMENTS)
    )
    await asyncio.wait_for(started.wait(), timeout=5)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call

    assert recorder.names() == ["begin"]
    assert recorder.unsettled == {"act-1"}


# --- settle fails: the answer is returned unchanged ---------------------------------


async def test_a_settle_that_fails_is_logged_and_the_answer_still_returns() -> None:
    recorder = _SpyRecorder()
    recorder.fail_settle = RuntimeError("store down")
    result = _booked()

    with capture_logs() as logs:
        returned = await _registry(_Handler(recorder, result), recorder).dispatch(
            _WRITE, _ARGUMENTS
        )

    assert returned == result
    assert recorder.unsettled == {"act-1"}
    (failed,) = [e for e in logs if e["event"] == "booking_act.settle_failed"]
    assert failed["log_level"] == "error"
    assert failed["tool_name"] == _WRITE
    assert failed["operation"] == BookingActOperation.BOOK
    assert failed["act_id"] == "act-1"
    assert failed["outcome"] == BookingActOutcome.DONE
    assert failed["error_type"] == "RuntimeError"
    assert failed["error_detail"] == "store down"


# --- a read tool: no recorder call -------------------------------------------------


@pytest.mark.parametrize("patient_id", ["01PATIENT", None])
async def test_a_read_records_nothing(patient_id: str | None) -> None:
    recorder = _SpyRecorder()
    read = _Handler(None, {"practitioners": []})

    result = await _registry(
        _Handler(recorder, _booked()),
        recorder,
        patient_id=patient_id,
        read_handler=read,
    ).dispatch(_READ, {})

    assert recorder.calls == []
    if patient_id is None:
        # The patient short-circuit is unchanged for a tool with nothing to record.
        assert result == _NO_PATIENT_RESULT
        assert read.calls == 0
    else:
        assert result == {"practitioners": []}


# --- invariant 1 against the real store -------------------------------------------


async def test_the_act_is_committed_before_the_handler_sends_anything() -> None:
    async with session_factory() as session:
        owner = await chat_repository.create_session(session)
        chat = await chat_repository.create_chat(session, owner.id)
        message = await chat_repository.create_message(
            session,
            id=str(ULID()),
            chat_id=chat.id,
            session_id=owner.id,
            sender=MessageSender.PATIENT,
            content="book me in",
        )
    assert message is not None
    recorder = DatabaseBookingActRecorder(
        session_factory, session_id=owner.id, chat_id=chat.id, message_id=message.id
    )
    seen: list[BookingAct] = []

    async def handler(_context: ToolContext, _arguments: dict[str, Any]) -> ToolResult:
        # Another session, so only what was committed is visible.
        async with session_factory() as session:
            seen.extend(
                (
                    await session.execute(
                        select(BookingAct).where(BookingAct.chat_id == chat.id)
                    )
                ).scalars()
            )
        return _booked()

    registry = ToolRegistry(
        [
            Tool(
                name=_WRITE,
                description="a write",
                input_schema={"type": "object", "properties": {}},
                handler=handler,
                requires_patient=True,
                writes=True,
                plan_act=_plan,
            )
        ],
        ToolContext(
            channel=MagicMock(),
            settings=MagicMock(spec=Settings),
            session_id=owner.id,
            patient_id="01PATIENT",
            local_now=datetime(2026, 8, 17, 8, 0),
            acts=recorder,
        ),
    )

    await registry.dispatch(_WRITE, _ARGUMENTS)

    (before,) = seen
    assert before.outcome is None
    assert before.message_id == message.id
    async with session_factory() as session:
        after = await session.get(BookingAct, before.id)
    assert after is not None
    assert after.outcome == BookingActOutcome.DONE
