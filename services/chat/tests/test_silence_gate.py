"""The silence gate, asserted as an absence.

FR-009 and FR-015 are not "no reply was stored" - they are "no call was made". A gate
placed one node too late produces no reply and still fails SC-002, and it passes every
positive test in this suite while doing so. So the assertions here are mostly about what
is *missing* from the log: no classification, no retrieval, no groundedness verdict, no
tool dispatch, and no generation.
"""

import json
from unittest.mock import patch

import pytest
import structlog
from chat.core.config import get_settings
from chat.db.session import engine, session_factory
from chat.domain.models import AttentionMark, EscalationReason, Message, MessageSender
from chat.domain.schemas import IntentLabel
from chat.main import app
from chat.repositories import chat_repository
from chat.repositories.chat_repository import ConversationState
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from structlog.testing import capture_logs

from .conftest import LOCAL_NOW, fake_anthropic_client

# Every event a turn emits once it has started deciding anything. None of them may
# appear for a message the assistant is not allowed to answer.
_FORBIDDEN_EVENTS = (
    "intent.classified",
    "turn.retrieval_completed",
    "turn.groundedness_verdict",
    "faq.retrieved",
    "booking.tool_called",
    "booking.model_call",
    "turn.completed",
)


async def _escalated_chat() -> tuple[str, str]:
    """Return a `(session_id, chat_id)` the assistant has been silenced in."""
    async with session_factory() as session:
        session_row = await chat_repository.create_session(session)
        chat = await chat_repository.create_chat(session, session_row.id)
        await chat_repository.set_escalated(
            session,
            chat.id,
            session_row.id,
            EscalationReason.PATIENT_ASKED_FOR_PERSON,
        )
    return session_row.id, chat.id


async def _paused_chat() -> tuple[str, str]:
    """Return a `(session_id, chat_id)` a staff member is leading, with no escalation.

    The gate's other branch, and the one every test in this file used to skip: a pause
    silences the assistant exactly as an escalation does, and nothing about the message
    that arrives during it is different.
    """
    async with session_factory() as session:
        session_row = await chat_repository.create_session(session)
        chat = await chat_repository.create_chat(session, session_row.id)
        await chat_repository.set_paused_until(
            session, chat.id, session_row.id, get_settings().ASSISTANT_PAUSE_SECONDS
        )
    return session_row.id, chat.id


async def _open_chat() -> tuple[str, str]:
    async with session_factory() as session:
        session_row = await chat_repository.create_session(session)
        chat = await chat_repository.create_chat(session, session_row.id)
    return session_row.id, chat.id


async def _state(chat_id: str, session_id: str) -> ConversationState:
    async with session_factory() as session:
        state = await chat_repository.get_conversation_state(
            session, chat_id, session_id
        )
    assert state is not None
    return state


async def _messages(chat_id: str) -> list[Message]:
    async with session_factory() as session:
        return await chat_repository.list_messages(session, chat_id)


class _SilentTurn:
    """One `POST /chat` against a silenced conversation, and what it left behind."""

    def __init__(self, lines: list[dict[str, object]], logs: list[dict]) -> None:
        self.lines = lines
        self.logs = logs

    def events(self) -> list[str]:
        return [str(entry["event"]) for entry in self.logs]


async def _send(session_id: str, chat_id: str, message: str) -> _SilentTurn:
    """Send `message` and return its NDJSON lines plus every log event it produced."""
    anthropic_client = fake_anthropic_client(["this reply must never be generated"])
    # The pool is handed between this test's own loop and the app's; disposing here
    # lets it rebind rather than fail on the first request.
    await engine.dispose()
    with (
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        mock_anthropic_cls.return_value = anthropic_client
        with TestClient(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as http:
                http.cookies.set("visitdoc_session_id", session_id)
                response = await http.post(
                    "/chat",
                    json={
                        "chat_id": chat_id,
                        "message": message,
                        "local_now": LOCAL_NOW,
                    },
                )

    assert response.status_code == 200
    assert anthropic_client.messages.create.await_count == 0, (
        "a silent turn made a model call"
    )
    assert anthropic_client.messages.stream.call_count == 0, (
        "a silent turn started a generation"
    )
    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    return _SilentTurn(lines, list(logs))


async def test_a_silent_turn_terminates_with_the_silent_event_and_nothing_else() -> (
    None
):
    session_id, chat_id = await _escalated_chat()

    turn = await _send(session_id, chat_id, "are you still there?")

    assert turn.lines == [{"type": "silent"}]


@pytest.mark.parametrize("forbidden", _FORBIDDEN_EVENTS)
async def test_a_silent_turn_makes_none_of_the_calls_a_turn_makes(
    forbidden: str,
) -> None:
    # SC-002, and the reason this file exists. Each of these is emitted by a step the
    # gate must precede; a gate inside the graph has already made the first one.
    session_id, chat_id = await _escalated_chat()

    turn = await _send(session_id, chat_id, "are you still there?")

    assert forbidden not in turn.events()


async def test_the_patients_message_is_kept_and_marked_unanswered() -> None:
    # FR-019: the message is not rejected and not lost - it is stored, and it carries
    # the reason nothing answered it.
    session_id, chat_id = await _escalated_chat()

    await _send(session_id, chat_id, "are you still there?")

    messages = await _messages(chat_id)
    assert [m.content for m in messages] == ["are you still there?"]
    assert messages[0].sender == MessageSender.PATIENT
    assert messages[0].attention_mark == AttentionMark.UNANSWERED


async def test_no_assistant_message_is_stored_for_a_silent_turn() -> None:
    session_id, chat_id = await _escalated_chat()

    await _send(session_id, chat_id, "are you still there?")

    messages = await _messages(chat_id)
    assert all(m.sender != MessageSender.ASSISTANT for m in messages)


async def test_a_silent_turn_starts_the_conversation_waiting_if_it_was_not() -> None:
    session_id, chat_id = await _escalated_chat()
    async with session_factory() as session:
        await chat_repository.clear_attention(session, chat_id, session_id)

    await _send(session_id, chat_id, "are you still there?")

    assert (await _state(chat_id, session_id)).attention_since is not None


async def test_a_silent_turn_does_not_restamp_a_conversation_already_waiting() -> None:
    # It has been waiting since the first thing that needed a person; re-stamping would
    # send it to the back of a queue ordered by how long each has waited.
    session_id, chat_id = await _escalated_chat()
    async with session_factory() as session:
        await chat_repository.mark_attention(session, chat_id, session_id)
    waiting_since = (await _state(chat_id, session_id)).attention_since

    await _send(session_id, chat_id, "are you still there?")

    assert (await _state(chat_id, session_id)).attention_since == waiting_since


async def test_the_unanswered_message_is_recorded_with_what_silenced_it() -> None:
    session_id, chat_id = await _escalated_chat()

    turn = await _send(session_id, chat_id, "are you still there?")

    recorded = [e for e in turn.logs if e["event"] == "message.unanswered"]
    assert len(recorded) == 1
    assert recorded[0]["chat_id"] == chat_id
    assert recorded[0]["silenced_by"] == "escalation"


async def test_an_open_conversation_still_runs_the_whole_turn() -> None:
    # The control: the gate must be a gate, not a switch that turned the service off.
    session_id, chat_id = await _open_chat()
    await engine.dispose()
    anthropic_client = fake_anthropic_client(["I can help with that."])

    with (
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        mock_anthropic_cls.return_value = anthropic_client
        with TestClient(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as http:
                http.cookies.set("visitdoc_session_id", session_id)
                response = await http.post(
                    "/chat",
                    json={
                        "chat_id": chat_id,
                        "message": "when are you open?",
                        "local_now": LOCAL_NOW,
                    },
                )

    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert lines[-1]["type"] == "done"
    assert "intent.classified" in [str(entry["event"]) for entry in logs]
    # Nothing was silenced, so nothing carries the mark that records a silence. The
    # message may still carry `corpus_could_not_answer` - this session has an empty
    # corpus, and an abstention against one escalates like any other (FR-003c).
    messages = await _messages(chat_id)
    assert all(m.attention_mark != AttentionMark.UNANSWERED for m in messages)


# --- the same gate, reached by a pause rather than an escalation --------------------


async def test_a_paused_turn_terminates_with_the_silent_event_and_nothing_else() -> (
    None
):
    session_id, chat_id = await _paused_chat()

    turn = await _send(session_id, chat_id, "one more thing before you answer")

    assert turn.lines == [{"type": "silent"}]


@pytest.mark.parametrize("forbidden", _FORBIDDEN_EVENTS)
async def test_a_paused_turn_makes_none_of_the_calls_a_turn_makes(
    forbidden: str,
) -> None:
    # SC-002 again, through the other branch. A gate that read only the escalation
    # column would pass every test above and let the assistant talk over a staff member.
    session_id, chat_id = await _paused_chat()

    turn = await _send(session_id, chat_id, "one more thing before you answer")

    assert forbidden not in turn.events()


async def test_a_message_arriving_during_a_pause_is_kept_and_marked() -> None:
    # SC-003: nothing is lost while the assistant is quiet, whichever silence it is.
    session_id, chat_id = await _paused_chat()

    await _send(session_id, chat_id, "one more thing before you answer")

    messages = await _messages(chat_id)
    assert [m.content for m in messages] == ["one more thing before you answer"]
    assert messages[0].attention_mark == AttentionMark.UNANSWERED
    assert (await _state(chat_id, session_id)).emphasized is True


async def test_a_paused_turn_records_which_silence_was_in_force() -> None:
    # The mark itself does not say which of the two it was, so the record has to.
    session_id, chat_id = await _paused_chat()

    turn = await _send(session_id, chat_id, "one more thing before you answer")

    recorded = [e for e in turn.logs if e["event"] == "message.unanswered"]
    assert len(recorded) == 1
    assert recorded[0]["silenced_by"] == "pause"


async def test_an_escalation_outranks_a_pause_in_the_record() -> None:
    # Both are in force here. `escalation` is the one reported, because it is the one
    # that does not expire - a reader told "pause" would expect this to resolve itself.
    session_id, chat_id = await _escalated_chat()
    async with session_factory() as session:
        await chat_repository.set_paused_until(
            session, chat_id, session_id, get_settings().ASSISTANT_PAUSE_SECONDS
        )

    turn = await _send(session_id, chat_id, "are you still there?")

    recorded = [e for e in turn.logs if e["event"] == "message.unanswered"]
    assert recorded[0]["silenced_by"] == "escalation"


async def test_a_turn_after_the_pause_expires_is_answered_normally() -> None:
    # FR-016: nothing runs when the deadline passes; the next turn simply finds it
    # elapsed, which is the moment the assistant is free again.
    session_id, chat_id = await _paused_chat()
    async with session_factory() as session:
        await chat_repository.set_paused_until(session, chat_id, session_id, -1)
    await engine.dispose()

    anthropic_client = fake_anthropic_client(["I can help with that."])
    with patch("chat.main.AsyncAnthropic") as mock_anthropic_cls:
        mock_anthropic_cls.return_value = anthropic_client
        with TestClient(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as http:
                http.cookies.set("visitdoc_session_id", session_id)
                response = await http.post(
                    "/chat",
                    json={
                        "chat_id": chat_id,
                        "message": "when are you open?",
                        "local_now": LOCAL_NOW,
                    },
                )

    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert lines[-1]["type"] == "done"


# --- what the turn after the silence is told about what it holds back ---------------


async def _post_as_staff(session_id: str, chat_id: str, content: str) -> None:
    """Reply as staff, which is what ends a silence by actually answering it."""
    await engine.dispose()
    with TestClient(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as http:
            http.cookies.set("visitdoc_session_id", session_id)
            response = await http.post(
                f"/console/chats/{chat_id}/messages", json={"content": content}
            )
    assert response.status_code == 201, response.text


async def _booking_prompt(session_id: str, chat_id: str, message: str) -> str:
    """Send `message` into a conversation the assistant may answer again.

    Returns: the one user entry the booking loop put in front of the model - which is
        where the held-back messages are described, if there are any.

    Routed through the booking specialist because that loop's entry is the whole of
    what the model reads; the FAQ path would need a seeded corpus to reach its own.
    """
    await engine.dispose()
    client = fake_anthropic_client(
        ["ignored"], intents=[IntentLabel.BOOKING], booking_reply="Which day suits you?"
    )
    with patch("chat.main.AsyncAnthropic") as mock_anthropic_cls:
        mock_anthropic_cls.return_value = client
        with TestClient(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as http:
                http.cookies.set("visitdoc_session_id", session_id)
                response = await http.post(
                    "/chat",
                    json={
                        "chat_id": chat_id,
                        "message": message,
                        "local_now": LOCAL_NOW,
                    },
                )
    assert response.status_code == 200
    # The booking loop and the classifier share `.messages.create`; only the former
    # sends tools.
    sent = [
        call.kwargs["messages"]
        for call in client.messages.create.await_args_list
        if call.kwargs.get("tools") is not None
    ]
    assert sent, "the booking specialist never ran"
    return str(sent[0][-1]["content"])


async def test_a_pause_that_expired_with_no_reply_is_not_described_as_handled() -> None:
    # The failure this guards: a pause that nobody followed up leaves its messages
    # marked, and the note used to tell the model a person had dealt with them - in a
    # conversation no person had ever spoken in.
    session_id, chat_id = await _paused_chat()
    await _send(session_id, chat_id, "can I move my Tuesday appointment?")
    async with session_factory() as session:
        await chat_repository.set_paused_until(session, chat_id, session_id, -1)
    assert all(m.sender == MessageSender.PATIENT for m in await _messages(chat_id))

    entry = await _booking_prompt(session_id, chat_id, "book me on Friday")

    assert "can I move my Tuesday appointment?" in entry
    assert "already dealt with them" not in entry
    assert "Nobody has answered them" in entry


async def test_the_held_back_message_goes_on_waiting_for_a_person() -> None:
    # It is held back from the answer, not dropped: the mark stays and the conversation
    # stays emphasized, so the staff side still shows it needing someone.
    session_id, chat_id = await _paused_chat()
    await _send(session_id, chat_id, "can I move my Tuesday appointment?")
    async with session_factory() as session:
        await chat_repository.set_paused_until(session, chat_id, session_id, -1)

    await _booking_prompt(session_id, chat_id, "book me on Friday")

    messages = await _messages(chat_id)
    assert messages[0].attention_mark == AttentionMark.UNANSWERED
    assert (await _state(chat_id, session_id)).emphasized is True


async def test_a_message_a_staff_member_answered_leaves_no_window_at_all() -> None:
    # The direction that must not change: a staff reply clears the mark, so the message
    # it answered is an ordinary one again and nothing is held back or annotated.
    session_id, chat_id = await _paused_chat()
    await _send(session_id, chat_id, "can I move my Tuesday appointment?")
    await _post_as_staff(session_id, chat_id, "Yes - I have moved it to Thursday.")
    async with session_factory() as session:
        await chat_repository.set_paused_until(session, chat_id, session_id, -1)

    entry = await _booking_prompt(session_id, chat_id, "book me on Friday")

    assert entry == "book me on Friday"
