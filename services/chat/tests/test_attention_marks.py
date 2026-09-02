"""The mark on a message, and the two properties its kind alone decides.

FR-027c's grid is the whole contract here: whether a kind silences the assistant and
whether it ever clears are different questions, and they disagree on two of the four
kinds. Nothing else in this feature is as easy to implement as one flag and as wrong.
"""

import json
from unittest.mock import patch

from chat.agent.escalation import EscalationRequests, apply_escalation
from chat.db.session import engine, session_factory
from chat.domain.models import (
    CLEARABLE_MARKS,
    AttentionMark,
    EscalationReason,
    Message,
    MessageSender,
)
from chat.main import app
from chat.repositories import chat_repository
from chat.repositories.chat_repository import ConversationState
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient, Response
from ulid import ULID

from .conftest import LOCAL_NOW, fake_anthropic_client


async def _chat() -> tuple[str, str]:
    """Return a fresh `(session_id, chat_id)`."""
    async with session_factory() as session:
        session_row = await chat_repository.create_session(session)
        chat = await chat_repository.create_chat(session, session_row.id)
    return session_row.id, chat.id


async def _message(session_id: str, chat_id: str, mark: AttentionMark | None) -> str:
    message_id = str(ULID())
    async with session_factory() as session:
        await chat_repository.create_message(
            session,
            id=message_id,
            chat_id=chat_id,
            sender=MessageSender.PATIENT,
            content="does it matter what I say",
        )
        if mark is not None:
            await chat_repository.set_attention_mark(
                session, chat_id, session_id, message_id, mark
            )
    return message_id


async def _mark_of(message_id: str) -> str | None:
    async with session_factory() as session:
        message = await session.get(Message, message_id)
        return None if message is None else message.attention_mark


async def test_a_staff_message_clears_every_clearable_mark_at_once() -> None:
    # However many accumulated: a person spoke, and that is what those marks asked for.
    session_id, chat_id = await _chat()
    clearable = [
        await _message(session_id, chat_id, AttentionMark.PATIENT_ASKED_FOR_PERSON),
        await _message(session_id, chat_id, AttentionMark.UNANSWERED),
        await _message(session_id, chat_id, AttentionMark.UNANSWERED),
    ]

    async with session_factory() as session:
        cleared = await chat_repository.clear_clearable_marks(
            session, chat_id, session_id
        )

    assert cleared == 3
    for message_id in clearable:
        assert await _mark_of(message_id) is None


async def test_permanent_marks_survive_a_staff_message() -> None:
    # A staff member answering the patient does not mean the corpus gained the entry it
    # was missing, or that the failure did not happen.
    session_id, chat_id = await _chat()
    corpus = await _message(session_id, chat_id, AttentionMark.CORPUS_COULD_NOT_ANSWER)
    failed = await _message(session_id, chat_id, AttentionMark.ASSISTANT_FAILED)

    async with session_factory() as session:
        cleared = await chat_repository.clear_clearable_marks(
            session, chat_id, session_id
        )

    assert cleared == 0
    assert await _mark_of(corpus) == AttentionMark.CORPUS_COULD_NOT_ANSWER
    assert await _mark_of(failed) == AttentionMark.ASSISTANT_FAILED


async def test_clearing_one_chats_marks_leaves_another_chats_alone() -> None:
    my_session, mine = await _chat()
    their_session, theirs = await _chat()
    ours = await _message(my_session, mine, AttentionMark.UNANSWERED)
    other = await _message(their_session, theirs, AttentionMark.UNANSWERED)

    async with session_factory() as session:
        await chat_repository.clear_clearable_marks(session, mine, my_session)

    assert await _mark_of(ours) is None
    assert await _mark_of(other) == AttentionMark.UNANSWERED


async def test_the_clearable_set_is_exactly_the_two_the_grid_names() -> None:
    # The predicate of the clearing statement, as a value. If a permanent kind ever
    # joined it, the two tests above would still pass for the kinds they name while a
    # diagnostic record was being erased.
    assert {mark.value for mark in CLEARABLE_MARKS} == {
        "patient_asked_for_person",
        "unanswered",
    }


# --- whose message, and whose conversation ------------------------------------------
#
# Both statements that touch a mark carry the owning session in their own `WHERE`. A
# message id is unique, which says nothing about who may write to the row it names -
# and a mark is exactly what a caller would address by an id taken from a request body.


async def test_a_mark_is_not_set_on_another_sessions_message() -> None:
    my_session, my_chat = await _chat()
    their_session, their_chat = await _chat()
    theirs = await _message(their_session, their_chat, None)

    async with session_factory() as session:
        await chat_repository.set_attention_mark(
            session, my_chat, my_session, theirs, AttentionMark.UNANSWERED
        )

    assert await _mark_of(theirs) is None


async def test_a_mark_is_not_set_through_another_sessions_chat() -> None:
    # The chat named is the one the message really belongs to, and the session named is
    # not its owner - so the pair, not the message id alone, is what has to refuse.
    my_session, _ = await _chat()
    their_session, their_chat = await _chat()
    theirs = await _message(their_session, their_chat, None)

    async with session_factory() as session:
        await chat_repository.set_attention_mark(
            session, their_chat, my_session, theirs, AttentionMark.UNANSWERED
        )

    assert await _mark_of(theirs) is None


async def test_clearing_marks_leaves_another_sessions_conversation_alone() -> None:
    my_session, _ = await _chat()
    their_session, their_chat = await _chat()
    theirs = await _message(their_session, their_chat, AttentionMark.UNANSWERED)

    async with session_factory() as session:
        cleared = await chat_repository.clear_clearable_marks(
            session, their_chat, my_session
        )

    assert cleared == 0
    assert await _mark_of(theirs) == AttentionMark.UNANSWERED


# --- 007 (US2): the two axes at conversation level, which are not one --------------
#
# FR-003d and FR-027e are the pair that catches an implementation which collapsed them.
# A failure emphasizes without silencing; a permanent mark outlives the emphasis that
# accompanied it. One column carrying both passes everything else in this file.


async def _state(chat_id: str, session_id: str) -> ConversationState:
    async with session_factory() as session:
        state = await chat_repository.get_conversation_state(
            session, chat_id, session_id
        )
    assert state is not None
    return state


async def _post_as_staff(session_id: str, chat_id: str, content: str) -> Response:
    await engine.dispose()
    with patch("chat.main.AsyncAnthropic") as mock_anthropic_cls:
        mock_anthropic_cls.return_value = fake_anthropic_client()
        with TestClient(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as http:
                http.cookies.set("visitdoc_session_id", session_id)
                return await http.post(
                    f"/console/chats/{chat_id}/messages", json={"content": content}
                )


async def _send_as_patient(session_id: str, chat_id: str, content: str) -> Response:
    await engine.dispose()
    with patch("chat.main.AsyncAnthropic") as mock_anthropic_cls:
        mock_anthropic_cls.return_value = fake_anthropic_client(["Of course."])
        with TestClient(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as http:
                http.cookies.set("visitdoc_session_id", session_id)
                return await http.post(
                    "/chat",
                    json={
                        "chat_id": chat_id,
                        "message": content,
                        "local_now": LOCAL_NOW,
                    },
                )


async def _failed_conversation() -> tuple[str, str, str]:
    """Return `(session_id, chat_id, message_id)` after one `assistant_failed` call."""
    session_id, chat_id = await _chat()
    message_id = await _message(session_id, chat_id, None)
    requests = EscalationRequests()
    requests.record(EscalationReason.ASSISTANT_FAILED)
    async with session_factory() as session:
        await apply_escalation(session, chat_id, session_id, message_id, requests)
    return session_id, chat_id, message_id


async def test_a_failure_emphasizes_the_conversation_without_silencing_it() -> None:
    session_id, chat_id, message_id = await _failed_conversation()

    state = await _state(chat_id, session_id)

    assert state.emphasized is True
    assert state.escalated_at is None
    assert state.may_assistant_reply is True
    assert await _mark_of(message_id) == AttentionMark.ASSISTANT_FAILED


async def test_a_failed_conversation_answers_the_patients_very_next_message() -> None:
    # A transient outage must not cost a conversation its assistant until a human
    # intervenes: the patient may retry immediately, and the retry is answered.
    session_id, chat_id, _ = await _failed_conversation()

    response = await _send_as_patient(session_id, chat_id, "let me try that again")

    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert lines[-1]["type"] == "done"
    assert lines[-1]["type"] != "silent"


async def test_a_failed_conversation_stays_emphasized_until_a_person_speaks() -> None:
    session_id, chat_id, _ = await _failed_conversation()

    await _send_as_patient(session_id, chat_id, "let me try that again")

    assert (await _state(chat_id, session_id)).emphasized is True


async def test_a_staff_reply_ends_the_emphasis_and_keeps_the_permanent_mark() -> None:
    # SC-009f: the mark is a record that the failure happened, and a person answering
    # the patient does not make it not have happened.
    session_id, chat_id, message_id = await _failed_conversation()

    await _post_as_staff(session_id, chat_id, "Sorry about that - I've got it now.")

    state = await _state(chat_id, session_id)
    assert state.emphasized is False
    assert await _mark_of(message_id) == AttentionMark.ASSISTANT_FAILED


async def test_a_conversation_holding_only_permanent_marks_is_not_emphasized() -> None:
    # FR-027e. The marks are still on their messages and still say why they are there;
    # what they no longer do is claim a person is still needed.
    session_id, chat_id = await _chat()
    corpus = await _message(session_id, chat_id, AttentionMark.CORPUS_COULD_NOT_ANSWER)
    failed = await _message(session_id, chat_id, AttentionMark.ASSISTANT_FAILED)
    async with session_factory() as session:
        await chat_repository.mark_attention(session, chat_id, session_id)

    await _post_as_staff(session_id, chat_id, "Both of those are dealt with.")

    state = await _state(chat_id, session_id)
    assert state.emphasized is False
    assert await _mark_of(corpus) == AttentionMark.CORPUS_COULD_NOT_ANSWER
    assert await _mark_of(failed) == AttentionMark.ASSISTANT_FAILED


async def test_the_switch_answers_nobody_in_either_direction() -> None:
    # FR-029a: taking a conversation is not answering it, and handing it back is not
    # answering it either. Every mark it holds is exactly where it was afterwards -
    # which is what makes the control safe to use freely.
    session_id, chat_id = await _chat()
    marks = [
        await _message(session_id, chat_id, AttentionMark.PATIENT_ASKED_FOR_PERSON),
        await _message(session_id, chat_id, AttentionMark.CORPUS_COULD_NOT_ANSWER),
        await _message(session_id, chat_id, AttentionMark.ASSISTANT_FAILED),
        await _message(session_id, chat_id, AttentionMark.UNANSWERED),
    ]
    async with session_factory() as session:
        await chat_repository.mark_attention(session, chat_id, session_id)
    before = [await _mark_of(message_id) for message_id in marks]

    await _switch(session_id, chat_id, enabled=False)
    await _switch(session_id, chat_id, enabled=True)

    assert [await _mark_of(message_id) for message_id in marks] == before
    assert (await _state(chat_id, session_id)).emphasized is True


async def _switch(session_id: str, chat_id: str, *, enabled: bool) -> None:
    await engine.dispose()
    with patch("chat.main.AsyncAnthropic") as mock_anthropic_cls:
        mock_anthropic_cls.return_value = fake_anthropic_client()
        with TestClient(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as http:
                http.cookies.set("visitdoc_session_id", session_id)
                await http.post(
                    f"/console/chats/{chat_id}/assistant", json={"enabled": enabled}
                )
