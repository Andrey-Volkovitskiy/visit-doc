"""The one escalation implementation: its collector, its precedence, its transition.

Four callers reach this capability and none of them writes it
(contracts/agent-tools.md). Each records a request into one per-turn collector;
`apply_escalation()` applies the resolved result once, after the turn has run. The tests
below are about that shape - that two requests resolve the same way whichever order they
arrived in, that a second call never overwrites the reason that first silenced a
conversation, and that a corpus gap and a failure both emphasize without silencing.
"""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from chat.agent.escalation import (
    HANDOFF_MESSAGE,
    EscalationRequests,
    apply_escalation,
)
from chat.agent.tools.registry import ToolContext, ToolRegistry
from chat.agent.tools.staff_tools import ESCALATE_TO_STAFF, STAFF_TOOLS
from chat.core.config import Settings
from chat.db.session import engine, session_factory
from chat.domain.models import AttentionMark, EscalationReason, Message, MessageSender
from chat.domain.schemas import IntentLabel
from chat.main import app
from chat.repositories import chat_repository
from chat.repositories.chat_repository import ConversationState
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from structlog.testing import capture_logs
from ulid import ULID

from .conftest import LOCAL_NOW, fake_anthropic_client

_ASKED = EscalationReason.PATIENT_ASKED_FOR_PERSON
_CORPUS = EscalationReason.CORPUS_COULD_NOT_ANSWER
_FAILED = EscalationReason.ASSISTANT_FAILED


async def _chat() -> tuple[str, str]:
    """Return a fresh `(session_id, chat_id)`."""
    async with session_factory() as session:
        session_row = await chat_repository.create_session(session)
        chat = await chat_repository.create_chat(session, session_row.id)
    return session_row.id, chat.id


async def _patient_message(session_id: str, chat_id: str) -> str:
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
    return message_id


async def _state(chat_id: str, session_id: str) -> ConversationState:
    async with session_factory() as session:
        state = await chat_repository.get_conversation_state(
            session, chat_id, session_id
        )
    assert state is not None
    return state


async def _mark_of(message_id: str) -> str | None:
    async with session_factory() as session:
        message = await session.get(Message, message_id)
        return None if message is None else message.attention_mark


async def _apply(
    chat_id: str, session_id: str, message_id: str, requests: EscalationRequests
) -> None:
    """Apply `requests` the way a turn does - establishing the takeover fact first.

    A turn reads that fact once, under the chat's lock, and hands it to
    `apply_escalation`; this stands in for the turn, so the tests below still exercise
    the predicate and not a boolean the test chose.
    """
    async with session_factory() as session:
        read = await chat_repository.get_takeover_since(
            session, chat_id, session_id, message_id
        )
        await apply_escalation(
            session,
            chat_id,
            session_id,
            message_id,
            requests,
            taken_over=read is chat_repository.TakeoverRead.TAKEN_OVER,
        )


async def _post_as_staff(session_id: str, chat_id: str) -> None:
    """Take the conversation as a staff member does, through the console's own route."""
    await engine.dispose()
    with patch("chat.main.AsyncAnthropic") as mock_anthropic_cls:
        mock_anthropic_cls.return_value = fake_anthropic_client()
        with TestClient(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as http:
                http.cookies.set("visitdoc_session_id", session_id)
                response = await http.post(
                    f"/console/chats/{chat_id}/messages",
                    json={"content": "I've got this one - let me take a look."},
                )
    assert response.status_code == 201


# --- The collector: precedence, and independence from arrival order ------------------


def test_the_conversations_reason_is_the_highest_precedence_silencing_one() -> None:
    requests = EscalationRequests()
    requests.record(_FAILED)
    requests.record(_CORPUS)
    requests.record(_ASKED)

    assert requests.conversation_reason is _ASKED


def test_a_failure_alone_resolves_to_no_conversation_reason() -> None:
    # FR-003d: it emphasizes without silencing, so there is nothing for the
    # conversation's silencing state to be set to.
    requests = EscalationRequests()
    requests.record(_FAILED)

    assert requests.conversation_reason is None
    assert requests.message_mark is AttentionMark.ASSISTANT_FAILED


def test_a_corpus_gap_alone_resolves_to_no_conversation_reason() -> None:
    # FR-003d: a question the documents do not cover is one answer missing, not the
    # assistant being the wrong thing to talk to - so it calls staff and the patient
    # goes on asking. The mark is still set: the question still needs a person.
    requests = EscalationRequests()
    requests.record(_CORPUS)

    assert requests.conversation_reason is None
    assert requests.message_mark is AttentionMark.CORPUS_COULD_NOT_ANSWER


def test_a_corpus_gap_beside_a_request_for_a_person_still_silences() -> None:
    # The one reason that silences decides for the turn, whichever half recorded first.
    requests = EscalationRequests()
    requests.record(_CORPUS)
    requests.record(_ASKED)

    assert requests.conversation_reason is _ASKED


def test_no_request_resolves_to_neither_a_reason_nor_a_mark() -> None:
    requests = EscalationRequests()

    assert requests.conversation_reason is None
    assert requests.message_mark is None


@pytest.mark.parametrize(
    ("recorded", "expected"),
    [
        ([_CORPUS, _FAILED], AttentionMark.CORPUS_COULD_NOT_ANSWER),
        ([_ASKED, _FAILED], AttentionMark.PATIENT_ASKED_FOR_PERSON),
        ([_ASKED, _CORPUS], AttentionMark.PATIENT_ASKED_FOR_PERSON),
    ],
)
def test_the_messages_mark_follows_the_declared_precedence(
    recorded: list[EscalationReason], expected: AttentionMark
) -> None:
    requests = EscalationRequests()
    for reason in recorded:
        requests.record(reason)

    assert requests.message_mark is expected


@pytest.mark.parametrize(
    "pair", [(_CORPUS, _FAILED), (_ASKED, _FAILED), (_ASKED, _CORPUS)]
)
def test_two_requests_resolve_identically_whichever_order_they_arrived_in(
    pair: tuple[EscalationReason, EscalationReason],
) -> None:
    # Two specialists can run concurrently and record in either order (research #5), so
    # the interleaving must not decide what a patient's conversation ends up in.
    first, second = pair
    forwards = EscalationRequests()
    forwards.record(first)
    forwards.record(second)
    backwards = EscalationRequests()
    backwards.record(second)
    backwards.record(first)

    assert forwards.conversation_reason is backwards.conversation_reason
    assert forwards.message_mark is backwards.message_mark


def test_every_recorded_request_is_kept_however_the_precedence_resolved() -> None:
    # The precedence decides the mark, not the record: FR-033 wants one log entry per
    # call, so nothing may be dropped on the way in.
    requests = EscalationRequests()
    requests.record(_FAILED)
    requests.record(_ASKED)
    requests.record(_FAILED)

    assert list(requests.recorded) == [_FAILED, _ASKED, _FAILED]


# --- The transition -----------------------------------------------------------------


async def test_a_silencing_reason_sets_escalation_attention_and_the_mark() -> None:
    session_id, chat_id = await _chat()
    message_id = await _patient_message(session_id, chat_id)
    requests = EscalationRequests()
    requests.record(_ASKED)

    await _apply(chat_id, session_id, message_id, requests)

    state = await _state(chat_id, session_id)
    assert state.escalated_at is not None
    assert state.escalation_reason == _ASKED
    assert state.attention_since is not None
    assert state.may_assistant_reply is False
    assert await _mark_of(message_id) == AttentionMark.PATIENT_ASKED_FOR_PERSON


async def test_assistant_failed_emphasizes_without_silencing() -> None:
    # FR-003d. The single most collapsible rule in the feature: one column carrying
    # both axes passes every other test in this file and fails this one.
    session_id, chat_id = await _chat()
    message_id = await _patient_message(session_id, chat_id)
    requests = EscalationRequests()
    requests.record(_FAILED)

    await _apply(chat_id, session_id, message_id, requests)

    state = await _state(chat_id, session_id)
    assert state.escalated_at is None
    assert state.escalation_reason is None
    assert state.attention_since is not None
    assert state.may_assistant_reply is True
    assert await _mark_of(message_id) == AttentionMark.ASSISTANT_FAILED


async def test_a_corpus_gap_emphasizes_without_silencing() -> None:
    # FR-003d. The patient is told the knowledge base does not cover it and that staff
    # have the question; the conversation stays open, so the next thing they ask is
    # answered rather than stored unanswered against a staff member who has not read
    # the first one yet.
    session_id, chat_id = await _chat()
    message_id = await _patient_message(session_id, chat_id)
    requests = EscalationRequests()
    requests.record(_CORPUS)

    await _apply(chat_id, session_id, message_id, requests)

    state = await _state(chat_id, session_id)
    assert state.escalated_at is None
    assert state.escalation_reason is None
    assert state.attention_since is not None
    assert state.may_assistant_reply is True
    assert await _mark_of(message_id) == AttentionMark.CORPUS_COULD_NOT_ANSWER


async def test_a_second_escalation_keeps_the_first_reason() -> None:
    # FR-007: the reason that silenced the conversation is the one a staff member
    # reads, and a later call must not rewrite the history it records - neither the
    # reason nor the moment it started. Only one reason silences (FR-003d), so the
    # guard against a *different* reason overwriting the stored one is exercised
    # against the repository directly, in `test_chat_repository.py`.
    session_id, chat_id = await _chat()
    first_message = await _patient_message(session_id, chat_id)
    first = EscalationRequests()
    first.record(_ASKED)
    await _apply(chat_id, session_id, first_message, first)
    escalated_at = (await _state(chat_id, session_id)).escalated_at

    second_message = await _patient_message(session_id, chat_id)
    second = EscalationRequests()
    second.record(_ASKED)
    await _apply(chat_id, session_id, second_message, second)

    state = await _state(chat_id, session_id)
    assert state.escalation_reason == _ASKED
    assert state.escalated_at == escalated_at


async def test_a_later_call_does_not_restamp_how_long_it_has_waited() -> None:
    session_id, chat_id = await _chat()
    first_message = await _patient_message(session_id, chat_id)
    first = EscalationRequests()
    first.record(_ASKED)
    await _apply(chat_id, session_id, first_message, first)
    waiting_since = (await _state(chat_id, session_id)).attention_since

    second_message = await _patient_message(session_id, chat_id)
    second = EscalationRequests()
    second.record(_FAILED)
    await _apply(chat_id, session_id, second_message, second)

    assert (await _state(chat_id, session_id)).attention_since == waiting_since


async def test_an_empty_collector_transitions_nothing_and_marks_nothing() -> None:
    session_id, chat_id = await _chat()
    message_id = await _patient_message(session_id, chat_id)

    await _apply(chat_id, session_id, message_id, EscalationRequests())

    state = await _state(chat_id, session_id)
    assert state.escalated_at is None
    assert state.attention_since is None
    assert await _mark_of(message_id) is None


async def test_a_call_decided_before_a_staff_post_is_not_applied_after_it() -> None:
    # The turn decides during the graph and applies once it has completed, so a staff
    # member can answer in between. Applying it afterwards would re-escalate and
    # re-mark a conversation a person is already handling - and since an escalation has
    # no deadline, the patient's next message would then be stored unanswered against
    # the very staff member replying to them.
    session_id, chat_id = await _chat()
    message_id = await _patient_message(session_id, chat_id)
    requests = EscalationRequests()
    requests.record(_ASKED)

    await _post_as_staff(session_id, chat_id)
    await _apply(chat_id, session_id, message_id, requests)

    state = await _state(chat_id, session_id)
    assert state.escalated_at is None
    assert state.attention_since is None
    assert state.emphasized is False
    assert await _mark_of(message_id) is None


async def test_a_call_raised_before_the_staff_post_it_preceded_still_applies() -> None:
    # The mirror of the test above, and what stops it from being satisfied by never
    # escalating a conversation that has ever held a staff message: only a post *newer*
    # than the message the turn answered means a person took it over during this turn.
    session_id, chat_id = await _chat()
    await _post_as_staff(session_id, chat_id)
    async with session_factory() as session:
        await chat_repository.clear_pause(session, chat_id, session_id)
    message_id = await _patient_message(session_id, chat_id)
    requests = EscalationRequests()
    requests.record(_ASKED)

    await _apply(chat_id, session_id, message_id, requests)

    state = await _state(chat_id, session_id)
    assert state.escalation_reason == _ASKED
    assert await _mark_of(message_id) == AttentionMark.PATIENT_ASKED_FOR_PERSON


async def test_a_chat_id_from_another_session_transitions_nothing() -> None:
    session_id, chat_id = await _chat()
    other_session_id, _ = await _chat()
    message_id = await _patient_message(session_id, chat_id)
    requests = EscalationRequests()
    requests.record(_ASKED)

    await _apply(chat_id, other_session_id, message_id, requests)

    assert (await _state(chat_id, session_id)).escalated_at is None


# --- The tool -----------------------------------------------------------------------


def _tool_context(collector: EscalationRequests) -> ToolContext:
    return ToolContext(
        channel=MagicMock(),
        settings=MagicMock(spec=Settings),
        session_id="01SESSION0000000000000000",
        patient_id=None,
        local_now=datetime(2026, 9, 1, 9, 0),
        escalation=collector,
    )


def test_escalate_to_staff_takes_no_arguments_at_all() -> None:
    # The caller identity is the reason, so there is nothing for a model to supply -
    # and a closed, empty schema is the strongest form of "it cannot misstate anything".
    assert ESCALATE_TO_STAFF.input_schema == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }


def test_escalate_to_staff_is_registered_and_needs_no_patient_record() -> None:
    # FR-002: a visitor who has never booked anything can still ask for a person.
    registry = ToolRegistry(STAFF_TOOLS, _tool_context(EscalationRequests()))

    assert "escalate_to_staff" in registry.names
    assert ESCALATE_TO_STAFF.requires_patient is False
    assert ESCALATE_TO_STAFF.writes is False


async def test_the_handler_records_a_request_and_reports_ok() -> None:
    collector = EscalationRequests()
    registry = ToolRegistry(STAFF_TOOLS, _tool_context(collector))

    result = await registry.dispatch("escalate_to_staff", {})

    assert result["status"] == "ok"
    assert list(collector.recorded) == [_ASKED]


async def test_the_handler_writes_no_state_of_its_own() -> None:
    # It performs no I/O: the transition belongs to the end of the turn (FR-006), and a
    # handler that wrote it here would silence a conversation mid-reply.
    session_id, chat_id = await _chat()
    registry = ToolRegistry(STAFF_TOOLS, _tool_context(EscalationRequests()))

    await registry.dispatch("escalate_to_staff", {})

    state = await _state(chat_id, session_id)
    assert state.escalated_at is None
    assert state.attention_since is None


# --- Scope, lifetime, and persistence ------------------------------------------------


async def test_an_escalation_binds_exactly_one_conversation() -> None:
    # FR-011: a session's other chats keep working normally while one is silent.
    session_id, escalated_chat = await _chat()
    async with session_factory() as session:
        sibling = await chat_repository.create_chat(session, session_id)
    message_id = await _patient_message(session_id, escalated_chat)
    requests = EscalationRequests()
    requests.record(_ASKED)

    await _apply(escalated_chat, session_id, message_id, requests)

    sibling_state = await _state(sibling.id, session_id)
    assert sibling_state.escalated_at is None
    assert sibling_state.attention_since is None
    assert sibling_state.may_assistant_reply is True


async def test_a_conversation_can_be_escalated_again_after_one_ended() -> None:
    # FR-010: the second escalation is a fresh one with its own waiting time, not a
    # resumption of the first.
    session_id, chat_id = await _chat()
    first_message = await _patient_message(session_id, chat_id)
    first = EscalationRequests()
    first.record(_ASKED)
    await _apply(chat_id, session_id, first_message, first)
    first_waited_since = (await _state(chat_id, session_id)).attention_since

    async with session_factory() as session:
        await chat_repository.clear_escalation(session, chat_id, session_id)
        await chat_repository.clear_attention(session, chat_id, session_id)

    second_message = await _patient_message(session_id, chat_id)
    second = EscalationRequests()
    second.record(_ASKED)
    await _apply(chat_id, session_id, second_message, second)

    state = await _state(chat_id, session_id)
    assert state.escalation_reason == _ASKED
    assert state.attention_since is not None
    assert state.attention_since != first_waited_since


async def test_the_escalated_mark_is_a_property_of_the_stored_conversation() -> None:
    # FR-012/SC-006: it survives a reload, a second tab and a restarted process,
    # because nothing about it lives in an open connection. T079 covers the pause's
    # half of this; without both, persistence is tested only for the silence that
    # expires anyway.
    session_id, chat_id = await _chat()
    message_id = await _patient_message(session_id, chat_id)
    requests = EscalationRequests()
    requests.record(_ASKED)
    await _apply(chat_id, session_id, message_id, requests)

    # A restart is exactly this: every pool and every in-memory registry dropped, and
    # the conversation read back from the store as any other process would find it.
    await engine.dispose()

    state = await _state(chat_id, session_id)
    assert state.escalated_at is not None
    assert state.escalation_reason == _ASKED
    assert state.may_assistant_reply is False


async def test_a_restarted_backend_still_generates_nothing_in_that_chat() -> None:
    session_id, chat_id = await _chat()
    message_id = await _patient_message(session_id, chat_id)
    requests = EscalationRequests()
    requests.record(_ASKED)
    await _apply(chat_id, session_id, message_id, requests)

    anthropic_client = fake_anthropic_client(["never generated"])
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
                        "message": "hello again",
                        "local_now": LOCAL_NOW,
                    },
                )

    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert lines == [{"type": "silent"}]
    assert anthropic_client.messages.create.await_count == 0
    assert anthropic_client.messages.stream.call_count == 0


# --- The records --------------------------------------------------------------------


async def test_one_escalation_record_means_one_handoff() -> None:
    # SC-010: `escalation.raised` is counted against conversations actually silenced,
    # so a no-op that logged as a raise would over-count every one of them.
    silenced = 0
    with capture_logs() as logs:
        for _ in range(3):
            session_id, chat_id = await _chat()
            # Three calls against one conversation; only the first silences it.
            for reason in (_ASKED, _ASKED, _ASKED):
                message_id = await _patient_message(session_id, chat_id)
                requests = EscalationRequests()
                requests.record(reason)
                await _apply(chat_id, session_id, message_id, requests)
            silenced += 1

    raised = [entry for entry in logs if entry["event"] == "escalation.raised"]
    unchanged = [entry for entry in logs if entry["event"] == "escalation.unchanged"]
    assert len(raised) == silenced
    assert len(unchanged) == silenced * 2
    assert all(entry["silenced"] is True for entry in raised)
    assert all(entry["existing_reason"] == _ASKED for entry in unchanged)


async def test_a_failure_is_recorded_as_raised_but_not_silenced() -> None:
    session_id, chat_id = await _chat()
    message_id = await _patient_message(session_id, chat_id)
    requests = EscalationRequests()
    requests.record(_FAILED)

    with capture_logs() as logs:
        await _apply(chat_id, session_id, message_id, requests)

    raised = [entry for entry in logs if entry["event"] == "escalation.raised"]
    assert len(raised) == 1
    assert raised[0]["silenced"] is False
    assert raised[0]["reason"] == _FAILED
    assert raised[0]["message_id"] == message_id


async def test_a_second_failure_in_an_emphasized_conversation_transitions_nothing() -> (
    None
):
    # The conversation has been waiting for a person since the first failure, so the
    # second put it nowhere it was not already: a no-op, and a no-op logged as a raise
    # would over-count the handoffs the record exists to count.
    session_id, chat_id = await _chat()
    first = EscalationRequests()
    first.record(_FAILED)
    await _apply(
        chat_id, session_id, await _patient_message(session_id, chat_id), first
    )

    message_id = await _patient_message(session_id, chat_id)
    requests = EscalationRequests()
    requests.record(_FAILED)

    with capture_logs() as logs:
        await _apply(chat_id, session_id, message_id, requests)

    assert [entry["event"] for entry in logs] == ["escalation.unchanged"]
    assert logs[0]["requested_reason"] == _FAILED
    # There is no escalation to name: a failure emphasizes without silencing.
    assert logs[0]["existing_reason"] is None
    assert logs[0]["message_id"] == message_id
    # The mark is still the message's own, and the emphasis is still the first one's.
    assert await _mark_of(message_id) == AttentionMark.ASSISTANT_FAILED
    assert (await _state(chat_id, session_id)).emphasized is True


async def test_a_failure_is_raised_once_however_many_times_it_is_recorded() -> None:
    # Two tools failing in one turn is one conversation joining the queue, not two.
    session_id, chat_id = await _chat()
    message_id = await _patient_message(session_id, chat_id)
    requests = EscalationRequests()
    requests.record(_FAILED)
    requests.record(_FAILED)

    with capture_logs() as logs:
        await _apply(chat_id, session_id, message_id, requests)

    assert [entry["event"] for entry in logs] == [
        "escalation.raised",
        "escalation.unchanged",
    ]
    assert logs[0]["silenced"] is False


async def test_both_halves_of_a_mixed_turn_are_recorded() -> None:
    # The precedence decides the mark; the log keeps every call (research #6). Two
    # transitions happened here - the silence and the emphasis - so each is claimed by
    # one request, and both are raised.
    session_id, chat_id = await _chat()
    message_id = await _patient_message(session_id, chat_id)
    requests = EscalationRequests()
    requests.record(_ASKED)
    requests.record(_FAILED)

    with capture_logs() as logs:
        await _apply(chat_id, session_id, message_id, requests)

    raised = {
        entry["reason"]: entry["silenced"]
        for entry in logs
        if entry["event"] == "escalation.raised"
    }
    assert raised == {_ASKED: True, _FAILED: False}
    assert await _mark_of(message_id) == AttentionMark.PATIENT_ASKED_FOR_PERSON


async def test_two_non_silencing_halves_claim_one_emphasis_between_them() -> None:
    # A corpus gap and a failure in one turn transition exactly one thing - the
    # conversation joins the queue - so one request claims it and the other is recorded
    # as having changed nothing. Two raises here would over-count the handoffs the
    # record exists to count (FR-033).
    session_id, chat_id = await _chat()
    message_id = await _patient_message(session_id, chat_id)
    requests = EscalationRequests()
    requests.record(_CORPUS)
    requests.record(_FAILED)

    with capture_logs() as logs:
        await _apply(chat_id, session_id, message_id, requests)

    raised = [entry for entry in logs if entry["event"] == "escalation.raised"]
    unchanged = [entry for entry in logs if entry["event"] == "escalation.unchanged"]
    assert [(e["reason"], e["silenced"]) for e in raised] == [(_CORPUS, False)]
    assert [e["requested_reason"] for e in unchanged] == [_FAILED]
    # The stronger of the two is what the message carries, and neither silenced.
    assert await _mark_of(message_id) == AttentionMark.CORPUS_COULD_NOT_ANSWER
    state = await _state(chat_id, session_id)
    assert state.may_assistant_reply is True
    assert state.emphasized is True


async def test_a_failed_record_never_stops_the_transition() -> None:
    # FR-034: recording follows a transition and never gates one - a log entry that
    # could not be written cannot un-happen a handoff that already occurred.
    session_id, chat_id = await _chat()
    message_id = await _patient_message(session_id, chat_id)
    requests = EscalationRequests()
    requests.record(_ASKED)

    with patch(
        "chat.agent.escalation.get_logger",
        side_effect=RuntimeError("the log stream is gone"),
    ):
        await _apply(chat_id, session_id, message_id, requests)

    assert (await _state(chat_id, session_id)).escalated_at is not None


# --- End of turn, through the real endpoint ------------------------------------------


async def test_the_tool_call_escalates_the_conversation_it_was_called_in() -> None:
    # The model can only ever escalate the conversation it is in: the collector reaches
    # the handler as ambient context, never as an argument.
    session_id, chat_id = await _chat()
    reply = "A staff member has been notified and will reply in this conversation."

    with patch("chat.main.AsyncAnthropic") as mock_anthropic_cls:
        mock_anthropic_cls.return_value = fake_anthropic_client(
            intents=[IntentLabel.BOOKING],
            booking_tool_calls=[[("escalate_to_staff", {})]],
            booking_reply=reply,
        )
        with TestClient(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as http:
                http.cookies.set("visitdoc_session_id", session_id)
                response = await http.post(
                    "/chat",
                    json={
                        "chat_id": chat_id,
                        "message": "I want to talk to a person",
                        "local_now": LOCAL_NOW,
                    },
                )

    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert lines[-1]["type"] == "done"

    state = await _state(chat_id, session_id)
    assert state.escalation_reason == _ASKED
    assert state.may_assistant_reply is False


async def test_a_classified_call_for_a_person_escalates_the_conversation() -> None:
    # The other route to the same capability: the classifier already labelled the
    # message `call_staff`, so the router records it and no model is asked to agree.
    # The handoff is the whole turn - nothing is retrieved, and nothing else is said.
    session_id, chat_id = await _chat()

    with patch("chat.main.AsyncAnthropic") as mock_anthropic_cls:
        mock_anthropic_cls.return_value = fake_anthropic_client(
            ["never generated"], intents=[IntentLabel.CALL_STAFF]
        )
        with TestClient(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as http:
                http.cookies.set("visitdoc_session_id", session_id)
                response = await http.post(
                    "/chat",
                    json={
                        "chat_id": chat_id,
                        "message": "can I speak to someone about my bill?",
                        "local_now": LOCAL_NOW,
                    },
                )

    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    streamed = "".join(line["text"] for line in lines if line["type"] == "token")
    assert lines[-1]["answer_source"] == "hand_off"
    # FR-005: told in the same turn, in this conversation, with no time promised.
    assert streamed == HANDOFF_MESSAGE
    assert "minute" not in streamed and "hour" not in streamed

    state = await _state(chat_id, session_id)
    assert state.escalation_reason == _ASKED
    assert state.may_assistant_reply is False
    assert state.attention_since is not None

    # And the sentence is in the patient's own thread, not only on the wire.
    async with session_factory() as session:
        messages = await chat_repository.list_messages(session, chat_id)
    assert messages[-1].sender == MessageSender.ASSISTANT
    assert messages[-1].content == HANDOFF_MESSAGE
