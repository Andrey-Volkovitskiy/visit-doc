"""Posting one case's turn, reading back what it stored, and judging the attempt.

A turn is `POST /chat`, answered either by a status line - the request was refused
before any pipeline ran - or by an NDJSON stream ending in exactly one of the service's
three terminal events. What an attempt that did not reach a terminal event amounts to is
decided from what the service stored, never assumed: a timeout does not prove the
server did nothing.
"""

import json
from collections.abc import Collection
from datetime import datetime
from enum import StrEnum

import httpx
from chat.domain.models import AttentionMark, MessageSender
from chat.domain.schemas import (
    ChatCancelledEvent,
    ChatDoneEvent,
    ChatHistoryResponse,
    ChatSilentEvent,
    MessageOut,
)
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from golden_harness.record import StoredMessage, Terminal, TerminalKind, turn_settled

_BODY_EXCERPT = 2000
_TERMINAL_EVENTS: dict[str, tuple[TerminalKind, type[BaseModel]]] = {
    "done": (TerminalKind.DONE, ChatDoneEvent),
    "silent": (TerminalKind.SILENT, ChatSilentEvent),
    "cancelled": (TerminalKind.CANCELLED, ChatCancelledEvent),
}
_TOKEN_EVENT = "token"


class TurnProtocolError(RuntimeError):
    """The service answered in a shape its own contract does not allow."""


class ThreadReadError(RuntimeError):
    """The chat's thread could not be read back."""


class AttemptClass(StrEnum):
    """What one attempt at a case amounts to.

    `NOT_SENT`: the pipeline provably never ran - no connection, or a rate-limit or
    server status instead of a stream. `SENT_NO_ANSWER`: the request may have reached
    the pipeline and nothing measurable came back. `MEASURED`: the turn reached its
    terminal event, or stored what it did. `HARNESS_FAULT`: the service refused a
    request the harness got wrong.
    """

    NOT_SENT = "not_sent"
    SENT_NO_ANSWER = "sent_no_answer"
    MEASURED = "measured"
    HARNESS_FAULT = "harness_fault"


class TurnNotConnected(BaseModel):
    """No connection to the service was established, so nothing was sent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    detail: str


class TurnRefused(BaseModel):
    """The service answered with a status line instead of a stream."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status_code: int
    body: str


class TurnSent(BaseModel):
    """The request was sent; how its response ended.

    `terminal.kind` is `error` exactly when no terminal event arrived, and then
    `interruption` says why - the stream broke, timed out, or ended without one.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    terminal: Terminal
    interruption: str | None

    @model_validator(mode="after")
    def _interruption_follows_the_terminal(self) -> "TurnSent":
        """Refuse an interruption beside a terminal event, or an error without one."""
        broken = self.terminal.kind is TerminalKind.ERROR
        if broken != (self.interruption is not None):
            raise ValueError("an interruption is recorded exactly when no ending came")
        return self


type TurnResult = TurnNotConnected | TurnRefused | TurnSent


class TurnTracing(BaseModel):
    """What one turn asks of its trace: whether to export one, and where to file it.

    Sent as headers on the turn's request, never in its body, so a traced eval turn is
    one filter away in Langfuse and the patient-facing request is unchanged. `traced`
    False asks the service to export nothing for the turn; True asks nothing, since a
    request can only turn its own tracing off. The run and the case are sent either
    way.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    case_id: str
    traced: bool = True

    def headers(self) -> dict[str, str]:
        """Return the request headers that carry this choice to the chat service."""
        headers = {
            "X-VisitDoc-Eval-Run": self.run_id,
            "X-VisitDoc-Eval-Case": self.case_id,
        }
        if not self.traced:
            headers["X-VisitDoc-Trace"] = "off"
        return headers


class ThreadRead(BaseModel):
    """What one turn left in its chat, besides the planted history.

    `patient_message` is None when the service stored no message for the turn, and
    `assistant_message` when it stored no reply.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    patient_message: StoredMessage | None
    assistant_message: StoredMessage | None


async def post_turn(
    client: httpx.AsyncClient,
    chat_id: str,
    message: str,
    local_now: datetime,
    tracing: TurnTracing,
) -> TurnResult:
    """Post `message` to `chat_id` as one turn and read its stream to the end.

    Args:
        local_now: the run clock, sent as the turn's local time.
        tracing: the run and case the turn's trace is filed under.

    Raises: TurnProtocolError when the stream carries a line the service's contract
        does not allow - unparseable, an unknown event, an invalid terminal event, or
        any event after the terminal one.

    The stream is read to its end even after the terminal event: the service closes it
    only once the turn's writes are finished, so a thread read afterwards sees them.
    """
    body = {"chat_id": chat_id, "message": message, "local_now": local_now.isoformat()}
    try:
        async with client.stream(
            "POST", "/chat", json=body, headers=tracing.headers()
        ) as response:
            if response.status_code != 200:
                await response.aread()
                return TurnRefused(
                    status_code=response.status_code,
                    body=response.text[:_BODY_EXCERPT],
                )
            return await _read_stream(response)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
        return TurnNotConnected(detail=_describe(exc))
    except httpx.TransportError as exc:
        return TurnSent(
            terminal=Terminal(kind=TerminalKind.ERROR), interruption=_describe(exc)
        )


async def read_thread(
    client: httpx.AsyncClient,
    chat_id: str,
    message: str,
    planted_ids: Collection[str],
) -> ThreadRead:
    """Read the patient message and reply one turn stored in a chat it had to itself.

    Args:
        message: the text the turn posted.
        planted_ids: the ids of the history planted before the turn, which are skipped.

    Raises: ThreadReadError when the thread cannot be read - a status other than 200,
        or a body that is not JSON or breaks the thread's schema; TurnProtocolError
        when the thread holds more than one patient message or reply besides the
        history, a patient message other than `message`, or a reply with no patient
        message.
    """
    response = await client.get(f"/chats/{chat_id}/messages")
    if response.status_code != 200:
        raise ThreadReadError(
            f"GET /chats/{chat_id}/messages answered {response.status_code}: "
            f"{response.text[:_BODY_EXCERPT]}"
        )
    try:
        thread = ChatHistoryResponse.model_validate_json(response.content)
    except ValidationError as exc:
        raise ThreadReadError(
            f"GET /chats/{chat_id}/messages answered a body that is not a thread: "
            f"{response.text[:_BODY_EXCERPT]}"
        ) from exc
    turn_messages = [m for m in thread.messages if m.id not in planted_ids]
    patients = [m for m in turn_messages if m.sender == MessageSender.PATIENT.value]
    replies = [m for m in turn_messages if m.sender == MessageSender.ASSISTANT.value]

    if len(patients) > 1 or len(replies) > 1:
        raise TurnProtocolError(
            f"chat {chat_id} holds {len(patients)} patient messages and "
            f"{len(replies)} replies besides its history; one turn leaves at most one"
        )
    if patients and patients[0].content != message:
        raise TurnProtocolError(
            f"chat {chat_id} holds a patient message that is not the case's message"
        )
    if replies and not patients:
        raise TurnProtocolError(f"chat {chat_id} holds a reply to no patient message")
    return ThreadRead(
        patient_message=_stored(patients[0]) if patients else None,
        assistant_message=_stored(replies[0]) if replies else None,
    )


def classify_attempt(result: TurnResult, thread: ThreadRead | None) -> AttemptClass:
    """Decide what an attempt amounts to.

    Args:
        thread: what the attempt stored; required for a sent turn that reached no
            terminal event, and not consulted otherwise.

    Raises: ValueError when a sent turn with no terminal event comes with no thread.

    A 429 or any 5xx before a stream is `NOT_SENT`; every other refusal is
    `HARNESS_FAULT`. A turn that reached a terminal event is `MEASURED`. One that did
    not is `MEASURED` when its patient message is marked `assistant_failed` or a reply
    was stored, and `SENT_NO_ANSWER` otherwise.
    """
    if isinstance(result, TurnNotConnected):
        return AttemptClass.NOT_SENT
    if isinstance(result, TurnRefused):
        if result.status_code == 429 or result.status_code >= 500:
            return AttemptClass.NOT_SENT
        return AttemptClass.HARNESS_FAULT
    if result.terminal.kind is not TerminalKind.ERROR:
        return AttemptClass.MEASURED
    if thread is None:
        raise ValueError(
            "a turn with no terminal event is judged by the thread it left"
        )
    if turn_settled(thread.patient_message, thread.assistant_message):
        return AttemptClass.MEASURED
    return AttemptClass.SENT_NO_ANSWER


async def _read_stream(response: httpx.Response) -> TurnSent:
    """Read an NDJSON turn stream to its end and return how it ended.

    Raises: TurnProtocolError when a line breaks the service's contract.
    """
    terminal: Terminal | None = None
    try:
        async for line in response.aiter_lines():
            if not line.strip():
                continue
            event = _parse_line(line)
            if terminal is not None:
                # Nothing may follow a terminal event - a token would add to a reply
                # the service already called finished.
                followed_by = _TOKEN_EVENT if event is None else event.kind.value
                raise TurnProtocolError(
                    f"a {followed_by} event followed the terminal event "
                    f"{terminal.kind.value}"
                )
            if event is None:
                continue
            terminal = event
    except httpx.TransportError as exc:
        if terminal is None:
            return TurnSent(
                terminal=Terminal(kind=TerminalKind.ERROR),
                interruption=_describe(exc),
            )
    if terminal is None:
        return TurnSent(
            terminal=Terminal(kind=TerminalKind.ERROR),
            interruption="the stream ended without a terminal event",
        )
    return TurnSent(terminal=terminal, interruption=None)


def _parse_line(line: str) -> Terminal | None:
    """Parse one stream line: a terminal event, or None for a token.

    Raises: TurnProtocolError when the line is not a token or a valid terminal event.
    """
    try:
        raw = json.loads(line)
    except ValueError as exc:
        raise TurnProtocolError(f"a stream line is not JSON: {line[:200]!r}") from exc
    kind = raw.get("type") if isinstance(raw, dict) else None
    if kind == _TOKEN_EVENT:
        return None
    known = _TERMINAL_EVENTS.get(kind) if isinstance(kind, str) else None
    if known is None:
        raise TurnProtocolError(f"a stream line is no event this build knows: {kind!r}")
    terminal_kind, model = known
    try:
        event = model.model_validate(raw)
    except ValidationError as exc:
        raise TurnProtocolError(f"an invalid {kind} event: {exc}") from exc
    return Terminal(kind=terminal_kind, payload=event.model_dump(mode="json"))


def _stored(message: MessageOut) -> StoredMessage:
    """Carry a listed message into the record's shape."""
    mark = message.attention_mark
    return StoredMessage(
        id=message.id,
        content=message.content,
        attention_mark=AttentionMark(mark) if mark is not None else None,
        request_outcomes=message.request_outcomes,
    )


def _describe(exc: Exception) -> str:
    """Name a transport failure and its message."""
    return f"{type(exc).__name__}: {exc}"
