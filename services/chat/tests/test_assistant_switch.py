"""The two-minute pause, and the switch that ends or starts one.

Two silences exist and only one of them expires. The pause is a stored *deadline*, so it
survives a reload, a second tab and a restart, and two tabs count it down together. An
escalation has no deadline at all - it is ended by a staff message or by the switch, and
by nothing else, which is the behaviour that most looks like a bug and is most
deliberately not one.

Both arrows into the pause write the same column with the same value: there is no
"manually paused" state distinct from "paused by a reply", and only the log records
which gesture it was.
"""

from typing import Any
from unittest.mock import patch

import pytest
from chat.core.config import get_settings
from chat.db.session import engine, session_factory
from chat.domain.models import AttentionMark, EscalationReason, MessageSender
from chat.main import app
from chat.repositories import chat_repository
from chat.repositories.chat_repository import ConversationState
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient, Response
from structlog.testing import capture_logs
from ulid import ULID

from .conftest import fake_anthropic_client

_PAUSE_SECONDS = get_settings().ASSISTANT_PAUSE_SECONDS


async def _chat(*, escalated: EscalationReason | None = None) -> tuple[str, str]:
    async with session_factory() as session:
        session_row = await chat_repository.create_session(session)
        chat = await chat_repository.create_chat(session, session_row.id)
        if escalated is not None:
            await chat_repository.set_escalated(
                session, chat.id, session_row.id, escalated
            )
            await chat_repository.mark_attention(session, chat.id, session_row.id)
    await engine.dispose()
    return session_row.id, chat.id


async def _marked_message(session_id: str, chat_id: str, mark: AttentionMark) -> str:
    message_id = str(ULID())
    async with session_factory() as session:
        await chat_repository.create_message(
            session,
            id=message_id,
            chat_id=chat_id,
            session_id=session_id,
            sender=MessageSender.PATIENT,
            content="is anyone there?",
        )
        await chat_repository.set_attention_mark(
            session, chat_id, session_id, message_id, mark
        )
    await engine.dispose()
    return message_id


async def _mark_of(message_id: str) -> str | None:
    async with session_factory() as session:
        from chat.domain.models import Message

        message = await session.get(Message, message_id)
        return None if message is None else message.attention_mark


async def _state(chat_id: str, session_id: str) -> ConversationState:
    async with session_factory() as session:
        state = await chat_repository.get_conversation_state(
            session, chat_id, session_id
        )
    assert state is not None
    return state


async def _set_pause(chat_id: str, session_id: str, seconds: int) -> None:
    async with session_factory() as session:
        await chat_repository.set_paused_until(session, chat_id, session_id, seconds)
    await engine.dispose()


async def _call(
    session_id: str | None, chat_id: str, *, path: str, body: dict[str, object]
) -> Response:
    await engine.dispose()
    with patch("chat.main.AsyncAnthropic") as mock_anthropic_cls:
        mock_anthropic_cls.return_value = fake_anthropic_client()
        with TestClient(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as http:
                if session_id is not None:
                    http.cookies.set("visitdoc_session_id", session_id)
                return await http.post(f"/console/chats/{chat_id}/{path}", json=body)


async def _post_staff(
    session_id: str, chat_id: str, content: str = "On it."
) -> Response:
    return await _call(session_id, chat_id, path="messages", body={"content": content})


async def _switch(session_id: str | None, chat_id: str, enabled: bool) -> Response:
    return await _call(session_id, chat_id, path="assistant", body={"enabled": enabled})


# --- the pause a staff message writes -----------------------------------------------


@pytest.mark.parametrize("escalated", [None, EscalationReason.PATIENT_ASKED_FOR_PERSON])
async def test_a_staff_message_pauses_the_assistant_either_way(
    escalated: EscalationReason | None,
) -> None:
    # FR-013: the pause is what stops the assistant talking over a staff member, and a
    # staff member leading an ordinary conversation needs it exactly as much.
    session_id, chat_id = await _chat(escalated=escalated)

    await _post_staff(session_id, chat_id)

    state = await _state(chat_id, session_id)
    assert state.may_assistant_reply is False
    assert state.pause_seconds_remaining is not None
    assert 0 < state.pause_seconds_remaining <= _PAUSE_SECONDS


async def test_a_further_staff_message_restarts_the_pause() -> None:
    # FR-014: a sequence of staff messages is one lead, not several, and the assistant
    # must not cut in between two of them.
    session_id, chat_id = await _chat()
    await _post_staff(session_id, chat_id, "First half of what I wanted to say.")
    await _set_pause(chat_id, session_id, 5)

    await _post_staff(session_id, chat_id, "And the second half.")

    remaining = (await _state(chat_id, session_id)).pause_seconds_remaining
    assert remaining is not None
    assert remaining > 5


async def test_the_pause_lifts_by_itself_with_no_staff_action() -> None:
    # FR-016: nothing runs when the deadline passes - the next turn simply finds it
    # elapsed, which is the moment the resumption becomes observable.
    session_id, chat_id = await _chat()
    await _set_pause(chat_id, session_id, -1)

    state = await _state(chat_id, session_id)

    assert state.may_assistant_reply is True
    assert state.pause_seconds_remaining is None


async def test_the_pause_survives_a_restart_with_its_time_still_running() -> None:
    # FR-018/SC-006: a stored deadline, not a timer. A restarted process finds it
    # exactly as it was, which is also why two tabs can count it down together.
    session_id, chat_id = await _chat()
    await _post_staff(session_id, chat_id)
    before = (await _state(chat_id, session_id)).pause_seconds_remaining

    # Every pool and in-memory registry dropped: what a restart actually costs.
    await engine.dispose()

    after = (await _state(chat_id, session_id)).pause_seconds_remaining
    assert before is not None
    assert after is not None
    assert after <= before
    assert (await _state(chat_id, session_id)).may_assistant_reply is False


# --- the switch ---------------------------------------------------------------------


async def test_turning_the_assistant_on_ends_both_silences() -> None:
    session_id, chat_id = await _chat(
        escalated=EscalationReason.PATIENT_ASKED_FOR_PERSON
    )
    await _set_pause(chat_id, session_id, _PAUSE_SECONDS)

    response = await _switch(session_id, chat_id, True)

    assert response.status_code == 200
    assert response.json()["assistant_may_reply"] is True
    assert response.json()["pause_seconds_remaining"] is None
    state = await _state(chat_id, session_id)
    assert state.escalated_at is None
    assert state.escalation_reason is None
    assert state.assistant_paused_until is None


async def test_turning_the_assistant_on_does_not_answer_the_patient() -> None:
    # FR-017b/FR-029a, and the assertion that the two axes really are separate: taking
    # the silence away is not the same act as answering, so the conversation is still
    # emphasized and its messages still marked.
    session_id, chat_id = await _chat(
        escalated=EscalationReason.PATIENT_ASKED_FOR_PERSON
    )
    marked = await _marked_message(session_id, chat_id, AttentionMark.UNANSWERED)
    waiting_since = (await _state(chat_id, session_id)).attention_since

    await _switch(session_id, chat_id, True)

    state = await _state(chat_id, session_id)
    assert state.may_assistant_reply is True
    assert state.emphasized is True
    assert state.attention_since == waiting_since
    assert await _mark_of(marked) == AttentionMark.UNANSWERED


async def test_turning_on_an_assistant_that_is_already_on_changes_nothing() -> None:
    session_id, chat_id = await _chat()

    response = await _switch(session_id, chat_id, True)

    assert response.status_code == 200
    state = await _state(chat_id, session_id)
    assert state.may_assistant_reply is True
    assert state.escalated_at is None
    assert state.attention_since is None


async def test_turning_the_assistant_off_writes_the_same_pause_a_message_writes() -> (
    None
):
    # There is no "manually paused" state distinct from "paused by a reply": both
    # arrows write the same column with the same value, and only the log says which
    # gesture it was.
    session_id, by_message = await _chat()
    session_two, by_switch = await _chat()

    await _post_staff(session_id, by_message)
    response = await _switch(session_two, by_switch, False)

    assert response.status_code == 200
    from_message = (await _state(by_message, session_id)).pause_seconds_remaining
    from_switch = (await _state(by_switch, session_two)).pause_seconds_remaining
    assert from_message is not None
    assert from_switch is not None
    assert abs(from_message - from_switch) <= 1


async def test_turning_the_assistant_off_restarts_a_running_pause() -> None:
    session_id, chat_id = await _chat()
    await _set_pause(chat_id, session_id, 5)

    response = await _switch(session_id, chat_id, False)

    remaining = (await _state(chat_id, session_id)).pause_seconds_remaining
    assert remaining is not None
    assert remaining > 5
    # What the switch answers is the state the write left, so the tab that flipped it
    # starts its countdown from the same deadline the next poll will report.
    assert response.json()["assistant_may_reply"] is False
    answered = response.json()["pause_seconds_remaining"]
    assert answered is not None
    assert answered >= remaining


async def test_turning_the_assistant_off_can_never_escalate_a_conversation() -> None:
    # An escalation records that the assistant asked for a person, which is a fact
    # about what happened. A staff member taking a conversation is not that.
    session_id, chat_id = await _chat()

    await _switch(session_id, chat_id, False)

    state = await _state(chat_id, session_id)
    assert state.escalated_at is None
    assert state.escalation_reason is None


async def test_neither_direction_touches_the_emphasis_or_a_mark() -> None:
    # FR-029a: neither turning the assistant off nor turning it back on answers
    # anybody, so what is waiting stays waiting throughout.
    session_id, chat_id = await _chat()
    marked = await _marked_message(session_id, chat_id, AttentionMark.UNANSWERED)
    async with session_factory() as session:
        await chat_repository.mark_attention(session, chat_id, session_id)
    await engine.dispose()
    waiting_since = (await _state(chat_id, session_id)).attention_since

    await _switch(session_id, chat_id, False)
    await _switch(session_id, chat_id, True)

    state = await _state(chat_id, session_id)
    assert state.emphasized is True
    assert state.attention_since == waiting_since
    assert await _mark_of(marked) == AttentionMark.UNANSWERED


async def test_a_chat_from_another_session_is_reported_as_never_having_existed() -> (
    None
):
    _, chat_id = await _chat()
    other_session_id, _ = await _chat()

    assert (await _switch(other_session_id, chat_id, True)).status_code == 404
    assert (await _switch(None, chat_id, True)).status_code == 404


@pytest.mark.parametrize("enabled", [True, False])
async def test_a_chat_deleted_while_the_switch_ran_is_reported_the_same_way(
    enabled: bool,
) -> None:
    # The window the resolve cannot close: another tab, or the admin sweep, deletes the
    # conversation after it resolves and before the write lands, so the write matches no
    # row and nothing is silenced or resumed. Answering 200 there would tell a staff
    # member the switch they flipped had been applied to a conversation that is gone.
    session_id, chat_id = await _chat()
    lock = chat_repository.lock_chat

    async def delete_after_locking(*args: Any, **kwargs: Any) -> None:
        await lock(*args, **kwargs)
        async with session_factory() as session:
            await chat_repository.delete_chat(session, chat_id, session_id)

    with patch.object(chat_repository, "lock_chat", delete_after_locking):
        response = await _switch(session_id, chat_id, enabled)

    assert response.status_code == 404
    assert response.json()["detail"] == "chat not found"


async def test_a_switch_that_silenced_nothing_records_no_pause() -> None:
    # The same window, read from the log instead of the status line. The write matched
    # no row, so no deadline was written - and an `assistant.paused` entry there is a
    # silence an operator would count, and would go looking for behind the patient's
    # next unanswered message, having never happened.
    session_id, chat_id = await _chat()
    lock = chat_repository.lock_chat

    async def delete_after_locking(*args: Any, **kwargs: Any) -> None:
        await lock(*args, **kwargs)
        async with session_factory() as session:
            await chat_repository.delete_chat(session, chat_id, session_id)

    with (
        patch.object(chat_repository, "lock_chat", delete_after_locking),
        capture_logs() as logs,
    ):
        response = await _switch(session_id, chat_id, False)

    assert response.status_code == 404
    assert [entry for entry in logs if entry["event"] == "assistant.paused"] == []


async def test_a_switch_that_resumed_nothing_records_no_resumption() -> None:
    # The same window in the on direction, which reads the state it records from before
    # writing rather than being told it by the write: the conversation is deleted after
    # that read, so the clears match no row and nothing is resumed. `escalation.ended`
    # is the worse of the two entries there - it carries a duration, so a wait that was
    # never ended inflates every count and response time taken over the event.
    session_id, chat_id = await _chat(
        escalated=EscalationReason.PATIENT_ASKED_FOR_PERSON
    )
    await _set_pause(chat_id, session_id, _PAUSE_SECONDS)
    clear_escalation = chat_repository.clear_escalation

    async def delete_after_clearing(*args: Any, **kwargs: Any) -> None:
        await clear_escalation(*args, **kwargs)
        async with session_factory() as session:
            await chat_repository.delete_chat(session, chat_id, session_id)

    with (
        patch.object(chat_repository, "clear_escalation", delete_after_clearing),
        capture_logs() as logs,
    ):
        response = await _switch(session_id, chat_id, True)

    assert response.status_code == 404
    recorded = [entry["event"] for entry in logs]
    assert "assistant.resumed" not in recorded
    assert "escalation.ended" not in recorded


# --- an escalation has no deadline --------------------------------------------------


async def test_an_escalation_left_far_beyond_the_pause_is_still_silent() -> None:
    # SC-002b/FR-009: no amount of time passing ends an escalation, and an
    # implementation that gave it the pause's deadline would silently resume answering
    # a patient who asked for a person.
    session_id, chat_id = await _chat(
        escalated=EscalationReason.PATIENT_ASKED_FOR_PERSON
    )
    # Well past any pause that could have been running.
    await _set_pause(chat_id, session_id, -(_PAUSE_SECONDS * 10))

    state = await _state(chat_id, session_id)

    assert state.may_assistant_reply is False
    assert state.escalated_at is not None
    assert state.pause_seconds_remaining is None


@pytest.mark.parametrize("ended_by", ["staff_message", "switch"])
async def test_exactly_two_things_end_an_escalation(ended_by: str) -> None:
    # FR-009a. Both are deliberate acts by a person; no clock is one of them.
    session_id, chat_id = await _chat(
        escalated=EscalationReason.PATIENT_ASKED_FOR_PERSON
    )

    if ended_by == "staff_message":
        await _post_staff(session_id, chat_id)
    else:
        await _switch(session_id, chat_id, True)

    assert (await _state(chat_id, session_id)).escalated_at is None


async def test_a_staff_message_is_not_a_resume() -> None:
    # It ends an escalation and starts a pause, so the assistant stays silent across
    # it - which is why `assistant.resumed` is not what a post records.
    session_id, chat_id = await _chat(
        escalated=EscalationReason.PATIENT_ASKED_FOR_PERSON
    )

    with capture_logs() as logs:
        await _post_staff(session_id, chat_id)

    events = [entry["event"] for entry in logs]
    assert "assistant.paused" in events
    assert "assistant.resumed" not in events
    assert (await _state(chat_id, session_id)).may_assistant_reply is False


# --- the records --------------------------------------------------------------------


async def test_a_pause_records_which_gesture_started_it() -> None:
    session_id, by_message = await _chat()
    session_two, by_switch = await _chat()

    with capture_logs() as logs:
        await _post_staff(session_id, by_message)
        await _switch(session_two, by_switch, False)

    paused = [entry for entry in logs if entry["event"] == "assistant.paused"]
    assert [entry["paused_by"] for entry in paused] == ["staff_message", "switch"]
    assert all(entry["restarted"] is False for entry in paused)


async def test_a_restarted_pause_is_recorded_as_a_restart() -> None:
    # What makes a sequence of staff messages legible as one lead rather than several.
    session_id, chat_id = await _chat()
    await _post_staff(session_id, chat_id, "First half.")

    with capture_logs() as logs:
        await _post_staff(session_id, chat_id, "Second half.")

    paused = [entry for entry in logs if entry["event"] == "assistant.paused"]
    assert len(paused) == 1
    assert paused[0]["restarted"] is True


@pytest.mark.parametrize("gesture", ["staff_message", "switch"])
async def test_a_pause_records_the_deadline_it_actually_wrote(gesture: str) -> None:
    # `until` is the record of when the assistant may speak again, so it has to be the
    # deadline the row now holds. It is reported by the write that set it rather than
    # read back afterwards, and a value assembled anywhere else - from this process's
    # clock, say - would drift from the one the gate obeys.
    session_id, chat_id = await _chat()

    with capture_logs() as logs:
        if gesture == "staff_message":
            await _post_staff(session_id, chat_id)
        else:
            await _switch(session_id, chat_id, False)

    paused = [entry for entry in logs if entry["event"] == "assistant.paused"]
    assert len(paused) == 1
    assert (
        paused[0]["until"] == (await _state(chat_id, session_id)).assistant_paused_until
    )


async def test_turning_the_assistant_on_records_a_resume() -> None:
    session_id, chat_id = await _chat()
    await _set_pause(chat_id, session_id, _PAUSE_SECONDS)

    with capture_logs() as logs:
        await _switch(session_id, chat_id, True)

    resumed = [entry for entry in logs if entry["event"] == "assistant.resumed"]
    assert len(resumed) == 1
    assert resumed[0]["resumed_by"] == "switch"
    assert resumed[0]["chat_id"] == chat_id


async def test_a_pause_expiring_clears_no_mark_and_no_emphasis() -> None:
    # SC-008 lists a pause expiring among the things that must clear neither. Nothing
    # runs when the deadline passes - it is a comparison, not an event - so there is no
    # write path that could touch them, and this is what says so.
    session_id, chat_id = await _chat()
    marked = await _marked_message(session_id, chat_id, AttentionMark.UNANSWERED)
    permanent = await _marked_message(
        session_id, chat_id, AttentionMark.CORPUS_COULD_NOT_ANSWER
    )
    async with session_factory() as session:
        await chat_repository.mark_attention(session, chat_id, session_id)
    await engine.dispose()
    waiting_since = (await _state(chat_id, session_id)).attention_since

    await _set_pause(chat_id, session_id, -1)

    state = await _state(chat_id, session_id)
    assert state.may_assistant_reply is True
    assert state.emphasized is True
    assert state.attention_since == waiting_since
    assert await _mark_of(marked) == AttentionMark.UNANSWERED
    assert await _mark_of(permanent) == AttentionMark.CORPUS_COULD_NOT_ANSWER


# --- what one gesture costs ---------------------------------------------------------


@pytest.mark.parametrize(
    ("gesture", "reads"),
    [("switch_off", 0), ("switch_on", 1), ("staff_message", 1)],
)
async def test_a_staff_gesture_reads_the_state_only_where_it_must(
    gesture: str, reads: int
) -> None:
    # Every one of these reads runs inside the chat's advisory lock, on the single
    # pinned connection holding it, while every open tab polls the console every two
    # seconds. So each write reports what it did - the deadline it wrote, the pause it
    # replaced, the state it left - and the only read left is the one no write can
    # answer: what a resumption ended, which is knowable only from the state it found.
    session_id, chat_id = await _chat(
        escalated=EscalationReason.PATIENT_ASKED_FOR_PERSON
    )
    reads_taken = 0
    read_state = chat_repository.get_conversation_state

    async def counted(*args: Any, **kwargs: Any) -> ConversationState | None:
        nonlocal reads_taken
        reads_taken += 1
        return await read_state(*args, **kwargs)

    with patch.object(chat_repository, "get_conversation_state", counted):
        if gesture == "staff_message":
            response = await _post_staff(session_id, chat_id)
        else:
            response = await _switch(session_id, chat_id, gesture == "switch_on")

    assert response.status_code in (200, 201)
    assert reads_taken == reads


async def test_a_failed_lock_release_does_not_undo_the_switch() -> None:
    # The same shape as a staff post: the pause is committed inside the locked section,
    # so a release that freed nothing would otherwise answer 500 for a switch that has
    # already moved - and the staff member would flip it back and forth to find out.
    session_id, chat_id = await _chat()
    not_held = chat_repository.ChatLockNotHeldError("held nothing")

    with (
        capture_logs() as logs,
        patch.object(chat_repository, "unlock_chat", side_effect=not_held),
    ):
        response = await _switch(session_id, chat_id, False)

    assert response.status_code == 200
    assert response.json()["assistant_may_reply"] is False
    assert (await _state(chat_id, session_id)).may_assistant_reply is False
    failed = [e for e in logs if e["event"] == "chat.lock_release_failed"]
    assert len(failed) == 1
    assert failed[0]["log_level"] == "error"
    assert failed[0]["chat_id"] == chat_id
