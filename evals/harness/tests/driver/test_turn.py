"""Posting one turn, reading back what it stored, and deciding what the attempt was."""

import json
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from typing import Any

import httpx
import pytest
from chat.domain.models import AttentionMark
from chat.domain.schemas import AnswerSource, FaqVerdict
from golden_harness.driver.turn import (
    AttemptClass,
    ThreadRead,
    ThreadReadError,
    TurnNotConnected,
    TurnProtocolError,
    TurnRefused,
    TurnSent,
    classify_attempt,
    post_turn,
    read_thread,
)
from golden_harness.record import StoredMessage, TerminalKind
from pydantic import ValidationError

_BASE_URL = "http://localhost:8000"
_CHAT_ID = "01K5CHATTURN00000000000000"
_CLOCK = datetime(2026, 3, 2, 8, 0, 0)
_MESSAGE = "What should I bring?"

_DONE: dict[str, Any] = {
    "type": "done",
    "request_outcomes": [
        {
            "position": 0,
            "question": "What should I bring?",
            "answer": "A photo ID.",
            "verdict": "answered",
            "citations": [{"entry_id": 3, "chunk_index": 0, "chunk_text": "Bring ID."}],
        }
    ],
    "message": None,
    "answer_source": "faq",
}


def _ndjson(*events: dict[str, Any]) -> bytes:
    return b"".join(json.dumps(event).encode() + b"\n" for event in events)


class _Stream(httpx.AsyncByteStream):
    """A response body delivered chunk by chunk, optionally broken after the chunks."""

    def __init__(self, chunks: list[bytes], *, then_raise: Exception | None = None):
        self._chunks = chunks
        self._then_raise = then_raise
        self.exhausted = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk
        if self._then_raise is not None:
            raise self._then_raise
        self.exhausted = True


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=_BASE_URL)


async def _post(handler: Callable[[httpx.Request], httpx.Response]) -> Any:
    async with _client(handler) as client:
        return await post_turn(client, _CHAT_ID, _MESSAGE, _CLOCK)


async def test_post_turn_sends_the_chat_the_message_and_the_run_clock() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, stream=_Stream([_ndjson(_DONE)]))

    await _post(handler)

    assert (seen[0].method, seen[0].url.path) == ("POST", "/chat")
    assert json.loads(seen[0].content) == {
        "chat_id": _CHAT_ID,
        "message": _MESSAGE,
        "local_now": "2026-03-02T08:00:00",
    }


async def test_post_turn_parses_the_stream_to_its_done_event() -> None:
    body = _ndjson(
        {"type": "token", "text": "A photo"}, {"type": "token", "text": " ID."}
    )
    body += _ndjson(_DONE)

    result = await _post(lambda _r: httpx.Response(200, stream=_Stream([body])))

    assert isinstance(result, TurnSent)
    assert result.terminal.kind is TerminalKind.DONE
    assert result.terminal.payload["answer_source"] == AnswerSource.FAQ.value
    outcomes = result.terminal.payload["request_outcomes"]
    assert isinstance(outcomes, list)
    assert outcomes[0] == {**_DONE["request_outcomes"][0]}
    assert result.interruption is None


@pytest.mark.parametrize(
    ("event", "kind"),
    [
        ({"type": "silent"}, TerminalKind.SILENT),
        ({"type": "cancelled"}, TerminalKind.CANCELLED),
    ],
)
async def test_post_turn_recognises_the_silent_and_cancelled_endings(
    event: dict[str, Any], kind: TerminalKind
) -> None:
    result = await _post(
        lambda _r: httpx.Response(200, stream=_Stream([_ndjson(event)]))
    )

    assert isinstance(result, TurnSent)
    assert result.terminal.kind is kind
    assert result.terminal.payload == event


async def test_post_turn_reads_the_stream_to_its_end_after_the_terminal_event() -> None:
    stream = _Stream([_ndjson(_DONE), b""])

    await _post(lambda _r: httpx.Response(200, stream=stream))

    assert stream.exhausted


async def test_a_stream_that_ends_without_a_terminal_event_is_an_error() -> None:
    body = _ndjson({"type": "token", "text": "A photo"})

    result = await _post(lambda _r: httpx.Response(200, stream=_Stream([body])))

    assert isinstance(result, TurnSent)
    assert result.terminal.kind is TerminalKind.ERROR
    assert result.interruption is not None


async def test_a_stream_cut_off_after_it_began_is_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        broken = httpx.RemoteProtocolError("peer closed connection", request=request)
        chunks = [_ndjson({"type": "token", "text": "A"})]
        return httpx.Response(200, stream=_Stream(chunks, then_raise=broken))

    result = await _post(handler)

    assert isinstance(result, TurnSent)
    assert result.terminal.kind is TerminalKind.ERROR
    assert result.interruption is not None
    assert "peer closed connection" in result.interruption


async def test_a_read_timeout_is_a_sent_turn_with_no_ending() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    result = await _post(handler)

    assert isinstance(result, TurnSent)
    assert result.terminal.kind is TerminalKind.ERROR


@pytest.mark.parametrize(
    "error", [httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout]
)
async def test_a_connection_never_established_is_not_connected(
    error: type[httpx.TransportError],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error("refused", request=request)

    assert isinstance(await _post(handler), TurnNotConnected)


async def test_a_status_line_instead_of_a_stream_is_refused_with_its_body() -> None:
    result = await _post(lambda _r: httpx.Response(503, json={"detail": "down"}))

    assert isinstance(result, TurnRefused)
    assert result.status_code == 503
    assert "down" in result.body


@pytest.mark.parametrize(
    "body",
    [
        b"not json\n",
        _ndjson({"type": "surprise"}),
        _ndjson({"type": "done", "answer_source": "nobody"}),
        _ndjson(_DONE, {"type": "cancelled"}),
        _ndjson(_DONE, {"type": "token", "text": " more"}),
    ],
    ids=[
        "not-json",
        "unknown-type",
        "invalid-done",
        "two-terminals",
        "token-after-terminal",
    ],
)
async def test_a_stream_breaking_the_services_contract_is_refused(body: bytes) -> None:
    with pytest.raises(TurnProtocolError):
        await _post(lambda _r: httpx.Response(200, stream=_Stream([body])))


def _message(
    message_id: str,
    sender: str,
    content: str,
    *,
    mark: str | None = None,
    outcomes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": message_id,
        "sender": sender,
        "content": content,
        "request_outcomes": outcomes,
        "attention_mark": mark,
        "created_at": "2026-09-14T10:00:00Z",
    }


_PLANTED = [
    _message("01K5PLANTEDUSER00000000000", "patient", "Earlier question"),
    _message("01K5PLANTEDASSISTANT000000", "assistant", "Earlier answer"),
]
_PATIENT = _message("01K5TURN000000000000000000", "patient", _MESSAGE)
_REPLY = _message(
    "01K5REPLY00000000000000000",
    "assistant",
    "A photo ID.",
    outcomes=_DONE["request_outcomes"],
)


async def _read(messages: list[dict[str, Any]], status: int = 200) -> ThreadRead:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json={"messages": messages})

    async with _client(handler) as client:
        thread = await read_thread(
            client, _CHAT_ID, _MESSAGE, {m["id"] for m in _PLANTED}
        )
    assert (seen[0].method, seen[0].url.path) == ("GET", f"/chats/{_CHAT_ID}/messages")
    return thread


async def test_read_thread_returns_this_turns_patient_message_and_reply() -> None:
    marked = {**_PATIENT, "attention_mark": "corpus_could_not_answer"}

    thread = await _read([*_PLANTED, marked, _REPLY])

    assert thread.patient_message == StoredMessage(
        id=_PATIENT["id"],
        content=_MESSAGE,
        attention_mark=AttentionMark.CORPUS_COULD_NOT_ANSWER,
    )
    assert thread.assistant_message is not None
    assert thread.assistant_message.id == _REPLY["id"]
    outcomes = thread.assistant_message.request_outcomes
    assert outcomes is not None
    assert outcomes[0].verdict is FaqVerdict.ANSWERED


async def test_read_thread_reports_no_reply_when_none_was_stored() -> None:
    thread = await _read([*_PLANTED, _PATIENT])

    assert thread.patient_message is not None
    assert thread.assistant_message is None


async def test_read_thread_reports_no_patient_message_when_none_was_stored() -> None:
    thread = await _read(list(_PLANTED))

    assert thread.patient_message is None
    assert thread.assistant_message is None


@pytest.mark.parametrize(
    "messages",
    [
        [*_PLANTED, _PATIENT, {**_PATIENT, "id": "01K5SECONDTURN000000000000"}],
        [*_PLANTED, {**_PATIENT, "content": "something else"}],
        [*_PLANTED, _PATIENT, _REPLY, {**_REPLY, "id": "01K5SECONDREPLY00000000000"}],
        [*_PLANTED, _REPLY],
    ],
    ids=[
        "two-patient-messages",
        "not-the-case-message",
        "two-replies",
        "reply-to-nothing",
    ],
)
async def test_read_thread_refuses_a_thread_one_turn_could_not_have_left(
    messages: list[dict[str, Any]],
) -> None:
    with pytest.raises(TurnProtocolError):
        await _read(messages)


async def test_read_thread_refuses_a_status_other_than_ok() -> None:
    with pytest.raises(ThreadReadError, match="404"):
        await _read([], status=404)


@pytest.mark.parametrize(
    "body",
    [
        b"<html>Bad Gateway</html>",
        json.dumps({"messages": [{"id": "01K5TURN000000000000000000"}]}).encode(),
    ],
    ids=["not-json", "breaks-the-schema"],
)
async def test_read_thread_refuses_an_ok_body_that_is_not_a_thread(body: bytes) -> None:
    async with _client(lambda _r: httpx.Response(200, content=body)) as client:
        with pytest.raises(ThreadReadError) as raised:
            await read_thread(client, _CHAT_ID, _MESSAGE, set())

    assert isinstance(raised.value.__cause__, ValidationError)


async def test_read_thread_raises_a_transport_error_to_the_run() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    async with _client(handler) as client:
        with pytest.raises(httpx.ReadTimeout):
            await read_thread(client, _CHAT_ID, _MESSAGE, set())


def _sent(kind: TerminalKind) -> TurnSent:
    payload: dict[str, Any] = {} if kind is TerminalKind.ERROR else {"type": kind.value}
    if kind is TerminalKind.DONE:
        payload = _DONE
    return TurnSent.model_validate(
        {
            "terminal": {"kind": kind, "payload": payload},
            "interruption": "broken" if kind is TerminalKind.ERROR else None,
        }
    )


def _thread(*, reply: bool, mark: AttentionMark | None = None) -> ThreadRead:
    return ThreadRead(
        patient_message=StoredMessage(
            id=_PATIENT["id"], content=_MESSAGE, attention_mark=mark
        ),
        assistant_message=(
            StoredMessage(id=_REPLY["id"], content="A photo ID.") if reply else None
        ),
    )


def test_a_connection_never_established_proves_the_pipeline_never_ran() -> None:
    result = TurnNotConnected(detail="refused")

    assert classify_attempt(result, None) is AttemptClass.NOT_SENT


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_a_rate_limit_or_server_status_before_a_stream_is_not_sent(status: int) -> None:
    result = TurnRefused(status_code=status, body="")

    assert classify_attempt(result, None) is AttemptClass.NOT_SENT


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422])
def test_any_other_client_error_status_is_a_harness_fault(status: int) -> None:
    result = TurnRefused(status_code=status, body="chat not found")

    assert classify_attempt(result, None) is AttemptClass.HARNESS_FAULT


@pytest.mark.parametrize(
    "kind", [TerminalKind.DONE, TerminalKind.SILENT, TerminalKind.CANCELLED]
)
def test_a_turn_that_reached_its_terminal_event_is_measured(kind: TerminalKind) -> None:
    assert classify_attempt(_sent(kind), _thread(reply=False)) is AttemptClass.MEASURED


def test_a_broken_stream_with_nothing_stored_is_sent_with_no_answer() -> None:
    result = _sent(TerminalKind.ERROR)

    assert classify_attempt(result, _thread(reply=False)) is AttemptClass.SENT_NO_ANSWER


def test_a_broken_stream_that_stored_no_patient_message_is_sent_with_no_answer() -> (
    None
):
    empty = ThreadRead(patient_message=None, assistant_message=None)

    assert classify_attempt(_sent(TerminalKind.ERROR), empty) is (
        AttemptClass.SENT_NO_ANSWER
    )


@pytest.mark.parametrize("reply", [False, True])
def test_a_broken_stream_marked_assistant_failed_is_measured(reply: bool) -> None:
    thread = _thread(reply=reply, mark=AttentionMark.ASSISTANT_FAILED)

    assert classify_attempt(_sent(TerminalKind.ERROR), thread) is AttemptClass.MEASURED


def test_a_broken_stream_whose_reply_was_stored_is_measured() -> None:
    thread = _thread(reply=True)

    assert classify_attempt(_sent(TerminalKind.ERROR), thread) is AttemptClass.MEASURED


def test_a_broken_stream_cannot_be_classified_without_the_thread() -> None:
    with pytest.raises(ValueError, match="thread"):
        classify_attempt(_sent(TerminalKind.ERROR), None)
