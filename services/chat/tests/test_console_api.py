"""`GET /console/conversations` - the one polled read model, serving both panes.

It is a *listing*, not a queue: every conversation in the session appears, emphasized or
not, because a staff member browsing them is an ordinary thing to do. What emphasis
does is decide the order and the total.

The two derived fields are the ones worth watching. `assistant_may_reply` and
`pause_seconds_remaining` are computed from the stored columns in the same statement
that reads them, so the switch a staff member sees and the gate a turn obeys cannot
disagree.
"""

from datetime import datetime
from typing import Any
from unittest.mock import patch

from chat.db.session import engine, session_factory
from chat.domain.models import AttentionMark, EscalationReason, MessageSender
from chat.main import app
from chat.repositories import chat_repository
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient, Response
from ulid import ULID

from .conftest import fake_anthropic_client


async def _session() -> str:
    async with session_factory() as session:
        session_row = await chat_repository.create_session(session)
    await engine.dispose()
    return session_row.id


async def _chat(
    session_id: str,
    *,
    escalated: EscalationReason | None = None,
    waiting: bool = False,
    paused_seconds: int | None = None,
    patient_name: str | None = None,
) -> str:
    async with session_factory() as session:
        chat = await chat_repository.create_chat(session, session_id)
        if patient_name is not None:
            await chat_repository.set_patient_name(
                session, chat.id, session_id, patient_name
            )
        if escalated is not None:
            await chat_repository.set_escalated(session, chat.id, session_id, escalated)
        if waiting or escalated is not None:
            await chat_repository.mark_attention(session, chat.id, session_id)
        if paused_seconds is not None:
            await chat_repository.set_paused_until(
                session, chat.id, session_id, paused_seconds
            )
    await engine.dispose()
    return chat.id


async def _message(
    session_id: str,
    chat_id: str,
    *,
    mark: AttentionMark | None = None,
    sender: MessageSender = MessageSender.PATIENT,
) -> str:
    message_id = str(ULID())
    async with session_factory() as session:
        await chat_repository.create_message(
            session,
            id=message_id,
            chat_id=chat_id,
            session_id=session_id,
            sender=sender,
            content="is anyone there?",
        )
        if mark is not None:
            await chat_repository.set_attention_mark(
                session, chat_id, session_id, message_id, mark
            )
    await engine.dispose()
    return message_id


async def _get(session_id: str | None) -> Response:
    """Read the console listing through the real app, on this test's own loop."""
    await engine.dispose()
    with patch("chat.main.AsyncAnthropic") as mock_anthropic_cls:
        mock_anthropic_cls.return_value = fake_anthropic_client()
        with TestClient(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as http:
                if session_id is not None:
                    http.cookies.set("visitdoc_session_id", session_id)
                return await http.get("/console/conversations")


def _by_id(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["chat_id"]: row for row in body["conversations"]}


# --- what the listing contains ------------------------------------------------------


async def test_every_conversation_is_listed_emphasized_or_not() -> None:
    # FR-027: a listing, not a queue. A staff member reads a conversation nobody
    # flagged as often as one somebody did.
    session_id = await _session()
    quiet = await _chat(session_id)
    escalated = await _chat(
        session_id, escalated=EscalationReason.PATIENT_ASKED_FOR_PERSON
    )

    body = (await _get(session_id)).json()

    assert set(_by_id(body)) == {quiet, escalated}


async def test_an_escalated_conversation_is_emphasized_and_says_why() -> None:
    session_id = await _session()
    chat_id = await _chat(
        session_id, escalated=EscalationReason.CORPUS_COULD_NOT_ANSWER
    )

    row = _by_id((await _get(session_id)).json())[chat_id]

    assert row["emphasized"] is True
    assert row["escalated"] is True
    assert row["escalation_reason"] == EscalationReason.CORPUS_COULD_NOT_ANSWER
    assert row["attention_since"] is not None


async def test_a_conversation_waiting_without_being_escalated_is_emphasized() -> None:
    # The other half of the pair: emphasis is "a person is needed here", and being
    # silenced is not a precondition for it.
    session_id = await _session()
    chat_id = await _chat(session_id, waiting=True)

    row = _by_id((await _get(session_id)).json())[chat_id]

    assert row["emphasized"] is True
    assert row["escalated"] is False
    assert row["escalation_reason"] is None
    assert row["assistant_may_reply"] is True


async def test_an_untouched_conversation_is_neither() -> None:
    session_id = await _session()
    chat_id = await _chat(session_id)

    row = _by_id((await _get(session_id)).json())[chat_id]

    assert row["emphasized"] is False
    assert row["escalated"] is False
    assert row["attention_since"] is None
    assert row["assistant_may_reply"] is True
    assert row["pause_seconds_remaining"] is None


# --- the two derived fields ---------------------------------------------------------


async def test_the_switch_position_is_derived_from_both_silences() -> None:
    # FR-017a: computed from the two states that actually decide, so it cannot disagree
    # with either of them.
    session_id = await _session()
    escalated = await _chat(
        session_id, escalated=EscalationReason.PATIENT_ASKED_FOR_PERSON
    )
    paused = await _chat(session_id, paused_seconds=120)
    open_chat = await _chat(session_id)

    rows = _by_id((await _get(session_id)).json())

    assert rows[escalated]["assistant_may_reply"] is False
    assert rows[paused]["assistant_may_reply"] is False
    assert rows[open_chat]["assistant_may_reply"] is True


async def test_a_running_pause_reports_its_remaining_seconds() -> None:
    session_id = await _session()
    chat_id = await _chat(session_id, paused_seconds=120)

    row = _by_id((await _get(session_id)).json())[chat_id]

    assert 0 < row["pause_seconds_remaining"] <= 120


async def test_an_escalation_shows_no_deadline_because_it_has_none() -> None:
    # FR-017b: nothing about time passing ends an escalation, so there is no countdown
    # to render - and rendering a zero would say there was one that had run out.
    session_id = await _session()
    chat_id = await _chat(
        session_id, escalated=EscalationReason.PATIENT_ASKED_FOR_PERSON
    )

    row = _by_id((await _get(session_id)).json())[chat_id]

    assert row["assistant_may_reply"] is False
    assert row["pause_seconds_remaining"] is None


async def test_an_elapsed_pause_reports_neither_a_deadline_nor_a_silence() -> None:
    session_id = await _session()
    chat_id = await _chat(session_id, paused_seconds=-5)

    row = _by_id((await _get(session_id)).json())[chat_id]

    assert row["assistant_may_reply"] is True
    assert row["pause_seconds_remaining"] is None


# --- the total ----------------------------------------------------------------------


async def test_a_conversation_counts_once_however_many_marks_it_holds() -> None:
    # Edge Cases: the total counts conversations needing a person, not marks - four
    # unanswered messages in one thread are one person's problem, not four.
    session_id = await _session()
    crowded = await _chat(
        session_id, escalated=EscalationReason.PATIENT_ASKED_FOR_PERSON
    )
    for _ in range(4):
        await _message(session_id, crowded, mark=AttentionMark.UNANSWERED)
    await _chat(session_id, waiting=True)
    await _chat(session_id)

    body = (await _get(session_id)).json()

    assert body["attention_total"] == 2


async def test_a_session_with_nothing_in_it_is_not_an_error() -> None:
    session_id = await _session()

    response = await _get(session_id)

    assert response.status_code == 200
    assert response.json() == {"attention_total": 0, "conversations": []}


async def test_a_request_with_no_session_cookie_gets_the_same_empty_shape() -> None:
    # Exactly as `GET /chats` already answers a first arrival: nothing to show is not
    # something to report as wrong.
    response = await _get(None)

    assert response.status_code == 200
    assert response.json() == {"attention_total": 0, "conversations": []}


# --- the ordering -------------------------------------------------------------------


def _order(body: dict[str, Any]) -> list[str]:
    return [row["chat_id"] for row in body["conversations"]]


async def test_emphasized_conversations_sort_above_the_rest() -> None:
    session_id = await _session()
    quiet_but_recent = await _chat(session_id)
    await _message(session_id, quiet_but_recent)
    needs_a_person = await _chat(
        session_id, escalated=EscalationReason.PATIENT_ASKED_FOR_PERSON
    )

    order = _order((await _get(session_id)).json())

    assert order == [needs_a_person, quiet_but_recent]


async def test_the_one_waiting_longest_comes_first() -> None:
    # Ascending `attention_since`: the queue is ordered by how long each has waited,
    # which is the only ranking this phase applies.
    session_id = await _session()
    first = await _chat(session_id, waiting=True)
    second = await _chat(session_id, waiting=True)
    third = await _chat(session_id, waiting=True)

    order = _order((await _get(session_id)).json())

    assert order == [first, second, third]


async def test_a_later_call_does_not_send_a_conversation_to_the_back() -> None:
    # It has been waiting since the first thing that needed a person, so re-stamping
    # would reward the conversation that kept being ignored with a worse position.
    session_id = await _session()
    oldest = await _chat(session_id, waiting=True)
    newer = await _chat(session_id, waiting=True)

    async with session_factory() as session:
        # A second call against the one already waiting.
        await chat_repository.mark_attention(session, oldest, session_id)
        await chat_repository.set_escalated(
            session, oldest, session_id, EscalationReason.PATIENT_ASKED_FOR_PERSON
        )
    await engine.dispose()

    assert _order((await _get(session_id)).json()) == [oldest, newer]


async def test_unemphasized_conversations_keep_the_existing_activity_order() -> None:
    session_id = await _session()
    stale = await _chat(session_id)
    await _message(session_id, stale)
    active = await _chat(session_id)
    await _message(session_id, active)

    assert _order((await _get(session_id)).json()) == [active, stale]


# --- session scope ------------------------------------------------------------------


async def test_the_listing_never_contains_another_sessions_conversation() -> None:
    mine = await _session()
    theirs = await _session()
    my_chat = await _chat(mine)
    their_chat = await _chat(
        theirs, escalated=EscalationReason.PATIENT_ASKED_FOR_PERSON
    )

    body = (await _get(mine)).json()

    assert set(_by_id(body)) == {my_chat}
    assert their_chat not in _by_id(body)
    assert body["attention_total"] == 0


async def test_another_sessions_chat_id_resolves_to_nothing_on_every_route() -> None:
    # FR-032: a well-formed id from another session is reported exactly as one that
    # never existed, on the write route as well as the read one.
    mine = await _session()
    theirs = await _session()
    their_chat = await _chat(theirs)

    await engine.dispose()
    with patch("chat.main.AsyncAnthropic") as mock_anthropic_cls:
        mock_anthropic_cls.return_value = fake_anthropic_client()
        with TestClient(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as http:
                http.cookies.set("visitdoc_session_id", mine)
                posted = await http.post(
                    f"/console/chats/{their_chat}/messages",
                    json={"content": "Can I help?"},
                )
                listed = await http.get("/console/conversations")

    assert posted.status_code == 404
    assert their_chat not in _by_id(listed.json())


async def test_the_listing_never_contains_a_session_id() -> None:
    # No response anywhere on this surface carries the credential the browser is not
    # allowed to read.
    session_id = await _session()
    await _chat(session_id)

    response = await _get(session_id)

    assert session_id not in response.text


# --- what the row renders -----------------------------------------------------------


async def test_a_row_carries_the_name_and_the_newest_message_time() -> None:
    session_id = await _session()
    chat_id = await _chat(session_id, patient_name="Ada Lovelace")
    await _message(session_id, chat_id)

    row = _by_id((await _get(session_id)).json())[chat_id]

    assert row["patient_name"] == "Ada Lovelace"
    assert datetime.fromisoformat(row["last_message_at"]) is not None


async def test_a_conversation_with_no_messages_reports_no_message_time() -> None:
    session_id = await _session()
    chat_id = await _chat(session_id)

    row = _by_id((await _get(session_id)).json())[chat_id]

    assert row["last_message_at"] is None
