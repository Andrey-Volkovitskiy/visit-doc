"""`POST /console/chats/{chat_id}/messages` - a person answering, in the same thread.

Replying *is* taking the conversation, so there is no separate resolve action: one post
ends the escalation, stops the conversation waiting, and clears every mark a person
speaking answers. The permanent marks are not among them, and that is the half of
FR-027c an implementation gets wrong by clearing the column.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Self
from unittest.mock import MagicMock, patch

import pytest
from chat.api import turn as turn_api
from chat.core.config import Settings
from chat.db.session import engine, session_factory
from chat.domain.models import AttentionMark, EscalationReason, MessageSender
from chat.domain.schemas import ChatDoneEvent, ChatTokenEvent, IntentLabel
from chat.main import app
from chat.rag.indexing import publish_revision
from chat.repositories import chat_repository, faq_repository
from chat.repositories.chat_repository import ConversationState
from chat.repositories.qdrant_repository import create_client, ensure_collection
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient, Response
from structlog.testing import capture_logs
from ulid import ULID

from .conftest import (
    LOCAL_NOW,
    FakeTextEvent,
    fake_anthropic_client,
    fake_embed_texts,
)

_CONTENT = "I've looked at your bill - the second charge was an error, and it's gone."


async def _chat(*, escalated: bool = False) -> tuple[str, str]:
    async with session_factory() as session:
        session_row = await chat_repository.create_session(session)
        chat = await chat_repository.create_chat(session, session_row.id)
        if escalated:
            await chat_repository.set_escalated(
                session,
                chat.id,
                session_row.id,
                EscalationReason.PATIENT_ASKED_FOR_PERSON,
            )
            await chat_repository.mark_attention(session, chat.id, session_row.id)
    await engine.dispose()
    return session_row.id, chat.id


async def _marked_message(
    session_id: str, chat_id: str, mark: AttentionMark | None
) -> str:
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
        if mark is not None:
            await chat_repository.set_attention_mark(
                session, chat_id, session_id, message_id, mark
            )
    await engine.dispose()
    return message_id


async def _state(chat_id: str, session_id: str) -> ConversationState:
    async with session_factory() as session:
        state = await chat_repository.get_conversation_state(
            session, chat_id, session_id
        )
    assert state is not None
    return state


async def _marks(chat_id: str) -> list[str | None]:
    async with session_factory() as session:
        messages = await chat_repository.list_messages(session, chat_id)
    return [m.attention_mark for m in messages]


async def _post(
    session_id: str | None, chat_id: str, content: str = _CONTENT
) -> Response:
    """Post as staff, through the real app, on this test's own event loop."""
    await engine.dispose()
    with patch("chat.main.AsyncAnthropic") as mock_anthropic_cls:
        mock_anthropic_cls.return_value = fake_anthropic_client()
        with TestClient(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as http:
                if session_id is not None:
                    http.cookies.set("visitdoc_session_id", session_id)
                return await http.post(
                    f"/console/chats/{chat_id}/messages", json={"content": content}
                )


async def test_a_staff_message_joins_the_patients_own_thread() -> None:
    # FR-020: one flat, ordered log - not a second thread the patient has to find.
    session_id, chat_id = await _chat()
    await _marked_message(session_id, chat_id, None)

    response = await _post(session_id, chat_id)

    assert response.status_code == 201
    body = response.json()
    assert body["sender"] == "staff"
    assert body["content"] == _CONTENT

    async with session_factory() as session:
        messages = await chat_repository.list_messages(session, chat_id)
    assert [m.sender for m in messages] == [
        MessageSender.PATIENT,
        MessageSender.STAFF,
    ]


async def test_no_staff_name_is_returned_anywhere() -> None:
    # FR-021/FR-022: `sender` already carries everything a label states, and this
    # system has no person behind the reply to name.
    session_id, chat_id = await _chat()

    body = (await _post(session_id, chat_id)).json()

    assert "staff_name" not in body
    assert not any("name" in key for key in body)


async def test_a_staff_message_ends_the_escalation() -> None:
    # FR-009a: replying is taking the conversation, so there is no separate resolve.
    session_id, chat_id = await _chat(escalated=True)

    await _post(session_id, chat_id)

    state = await _state(chat_id, session_id)
    assert state.escalated_at is None
    assert state.escalation_reason is None


async def test_a_staff_message_stops_the_conversation_waiting() -> None:
    session_id, chat_id = await _chat(escalated=True)

    await _post(session_id, chat_id)

    assert (await _state(chat_id, session_id)).attention_since is None


async def test_a_staff_message_clears_every_clearable_mark_at_once() -> None:
    session_id, chat_id = await _chat(escalated=True)
    await _marked_message(session_id, chat_id, AttentionMark.PATIENT_ASKED_FOR_PERSON)
    await _marked_message(session_id, chat_id, AttentionMark.UNANSWERED)
    await _marked_message(session_id, chat_id, AttentionMark.UNANSWERED)

    await _post(session_id, chat_id)

    assert await _marks(chat_id) == [None, None, None, None]


async def test_a_staff_message_leaves_the_permanent_marks_alone() -> None:
    # A person answering does not mean the corpus gained the entry it was missing, or
    # that the failure did not happen.
    session_id, chat_id = await _chat(escalated=True)
    await _marked_message(session_id, chat_id, AttentionMark.CORPUS_COULD_NOT_ANSWER)
    await _marked_message(session_id, chat_id, AttentionMark.ASSISTANT_FAILED)
    await _marked_message(session_id, chat_id, AttentionMark.UNANSWERED)

    await _post(session_id, chat_id)

    assert await _marks(chat_id) == [
        AttentionMark.CORPUS_COULD_NOT_ANSWER,
        AttentionMark.ASSISTANT_FAILED,
        None,
        None,
    ]


async def test_a_staff_message_is_accepted_in_an_unescalated_conversation() -> None:
    # FR-024: there is no conversation a staff member must escalate first in order to
    # speak in, and none they may not speak in twice.
    session_id, chat_id = await _chat()

    first = await _post(session_id, chat_id, "Just checking in on this one.")
    second = await _post(session_id, chat_id, "Anything else I can help with?")

    assert first.status_code == 201
    assert second.status_code == 201
    async with session_factory() as session:
        messages = await chat_repository.list_messages(session, chat_id)
    assert [m.sender for m in messages] == [MessageSender.STAFF, MessageSender.STAFF]


async def test_a_chat_from_another_session_is_reported_as_never_having_existed() -> (
    None
):
    # FR-032: identical to a chat id that was never issued, so a probing caller learns
    # nothing from which one they hit.
    _, chat_id = await _chat()
    other_session_id, _ = await _chat()

    theirs = await _post(other_session_id, chat_id)
    invented = await _post(other_session_id, str(ULID()))

    assert theirs.status_code == 404
    assert invented.status_code == 404
    assert theirs.json() == invented.json()


async def test_a_request_with_no_session_cookie_is_reported_the_same_way() -> None:
    _, chat_id = await _chat()

    response = await _post(None, chat_id)

    assert response.status_code == 404


@pytest.mark.parametrize("content", ["", "   ", "a" * 2001])
async def test_a_staff_message_faces_the_same_content_rules_as_a_patients(
    content: str,
) -> None:
    session_id, chat_id = await _chat()

    assert (await _post(session_id, chat_id, content)).status_code == 422


async def test_the_three_effects_of_one_post_are_recorded_together() -> None:
    # A reply that cleared four marks and ended an escalation is a different event from
    # one that cleared none (contracts/log-events.md).
    session_id, chat_id = await _chat(escalated=True)
    await _marked_message(session_id, chat_id, AttentionMark.UNANSWERED)
    await _marked_message(session_id, chat_id, AttentionMark.PATIENT_ASKED_FOR_PERSON)

    with capture_logs() as logs:
        await _post(session_id, chat_id)

    posted = [e for e in logs if e["event"] == "staff.message_posted"]
    ended = [e for e in logs if e["event"] == "escalation.ended"]
    assert len(posted) == 1
    assert posted[0]["chat_id"] == chat_id
    assert posted[0]["marks_cleared"] == 2
    assert posted[0]["ended_escalation"] is True
    assert len(ended) == 1
    assert ended[0]["ended_by"] == "staff_message"
    assert ended[0]["escalated_for"] == EscalationReason.PATIENT_ASKED_FOR_PERSON
    assert ended[0]["waited_seconds"] >= 0


async def test_a_post_into_an_open_conversation_ends_no_escalation() -> None:
    session_id, chat_id = await _chat()

    with capture_logs() as logs:
        await _post(session_id, chat_id)

    posted = [e for e in logs if e["event"] == "staff.message_posted"]
    assert posted[0]["ended_escalation"] is False
    assert posted[0]["marks_cleared"] == 0
    assert not [e for e in logs if e["event"] == "escalation.ended"]


async def test_a_staff_message_leaves_another_conversation_untouched() -> None:
    session_id, chat_id = await _chat(escalated=True)
    async with session_factory() as session:
        sibling = await chat_repository.create_chat(session, session_id)
        await chat_repository.set_escalated(
            session,
            sibling.id,
            session_id,
            EscalationReason.CORPUS_COULD_NOT_ANSWER,
        )
    await engine.dispose()

    await _post(session_id, chat_id)

    sibling_state = await _state(sibling.id, session_id)
    assert sibling_state.escalated_at is not None
    assert sibling_state.escalation_reason == EscalationReason.CORPUS_COULD_NOT_ANSWER


# --- 007 (FR-013a): a staff message discards whatever was being generated ----------
#
# A partial reply written alongside a staff member's own is worse than no reply at all:
# the patient reads two half-answers and cannot tell which the clinic stands behind.


class _PartialStream:
    """A generation that yields `before`, stalls until released, then yields `after`.

    Lets a test cancel a turn at a chosen point - before the first token, in the
    middle, or with only the last token left - without relying on wall-clock timing.
    """

    def __init__(
        self,
        before: list[str],
        after: list[str],
        gate: asyncio.Event,
        started: asyncio.Event,
    ) -> None:
        self._before = before
        self._after = after
        self._gate = gate
        self._started = started

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    def __aiter__(self) -> AsyncIterator[FakeTextEvent]:
        return self._generate()

    async def _generate(self) -> AsyncIterator[FakeTextEvent]:
        for token in self._before:
            yield FakeTextEvent(token)
        self._started.set()
        await self._gate.wait()
        for token in self._after:
            yield FakeTextEvent(token)


_TOKENS = ["Visiting ", "hours ", "are 8am to 5pm."]


async def _seed_corpus_session() -> tuple[str, str]:
    """Return a `(session_id, chat_id)` whose session can answer a grounded question."""
    settings = Settings()
    qdrant_client = create_client(settings)
    await ensure_collection(qdrant_client)
    revision = str(ULID())
    async with session_factory() as session:
        session_row = await chat_repository.create_session(session)
        entry = await faq_repository.create(
            session, session_row.id, "Visiting hours are 8am to 5pm.", revision
        )
        chat = await chat_repository.create_chat(session, session_row.id)
    with patch("chat.rag.indexing.embed_texts", fake_embed_texts):
        await publish_revision(
            qdrant_client,
            MagicMock(),
            session_row.id,
            entry.id,
            revision,
            "Visiting hours are 8am to 5pm.",
        )
    await qdrant_client.close()
    await engine.dispose()
    return session_row.id, chat.id


async def _interrupt_mid_generation(
    session_id: str,
    chat_id: str,
    *,
    tokens_before: int,
    interrupt: str,
) -> list[dict[str, object]]:
    """Start a turn, interrupt it at `tokens_before`, and return its NDJSON lines.

    Args:
        interrupt: `"staff_message"` to post as staff, `"switch"` to turn the assistant
            off. Both must discard the partial reply on the same terms.
    """
    gate = asyncio.Event()
    started = asyncio.Event()
    anthropic_client = fake_anthropic_client(_TOKENS)
    anthropic_client.messages.stream.return_value = _PartialStream(
        _TOKENS[:tokens_before], _TOKENS[tokens_before:], gate, started
    )

    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = anthropic_client
        with TestClient(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as http:
                http.cookies.set("visitdoc_session_id", session_id)
                turn = asyncio.create_task(
                    http.post(
                        "/chat",
                        json={
                            "chat_id": chat_id,
                            "message": "when can I visit?",
                            "local_now": LOCAL_NOW,
                        },
                    )
                )
                await asyncio.wait_for(started.wait(), timeout=5)
                if interrupt == "staff_message":
                    await http.post(
                        f"/console/chats/{chat_id}/messages",
                        json={"content": "I've got this one - 8am to 5pm."},
                    )
                else:
                    await http.post(
                        f"/console/chats/{chat_id}/assistant",
                        json={"enabled": False},
                    )
                gate.set()
                response = await asyncio.wait_for(turn, timeout=5)

    return [json.loads(line) for line in response.text.strip().splitlines()]


@pytest.mark.parametrize("tokens_before", [0, 1, len(_TOKENS) - 1])
async def test_a_staff_message_cancels_the_reply_being_generated(
    tokens_before: int,
) -> None:
    session_id, chat_id = await _seed_corpus_session()

    lines = await _interrupt_mid_generation(
        session_id, chat_id, tokens_before=tokens_before, interrupt="staff_message"
    )

    assert lines[-1] == {"type": "cancelled"}
    async with session_factory() as session:
        messages = await chat_repository.list_messages(session, chat_id)
    # Zero partial replies persisted: the patient, the staff member. Nothing else.
    assert [m.sender for m in messages] == [
        MessageSender.PATIENT,
        MessageSender.STAFF,
    ]


async def test_turning_the_assistant_off_mid_stream_cancels_it_too() -> None:
    # FR-017c: the switch discards a partial reply on FR-013a's exact terms, because
    # taking a conversation and replying in it are the same act as far as the half-
    # written answer is concerned.
    session_id, chat_id = await _seed_corpus_session()

    lines = await _interrupt_mid_generation(
        session_id, chat_id, tokens_before=1, interrupt="switch"
    )

    assert lines[-1] == {"type": "cancelled"}
    async with session_factory() as session:
        messages = await chat_repository.list_messages(session, chat_id)
    assert [m.sender for m in messages] == [MessageSender.PATIENT]


async def test_a_staff_post_racing_a_turn_leaves_no_orphaned_reply() -> None:
    # The turn registers its task while it still holds the chat's lock, so a staff post
    # can never land between the turn passing the silence gate and its generation
    # becoming cancellable. Whichever of the two takes the lock first, the outcome is
    # the same: either the turn was silenced before it started, or its reply was
    # cancelled - and no assistant message exists either way.
    session_id, chat_id = await _seed_corpus_session()
    gate = asyncio.Event()
    started = asyncio.Event()
    anthropic_client = fake_anthropic_client(_TOKENS)
    anthropic_client.messages.stream.return_value = _PartialStream(
        [], _TOKENS, gate, started
    )

    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = anthropic_client
        with TestClient(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as http:
                http.cookies.set("visitdoc_session_id", session_id)
                turn = asyncio.create_task(
                    http.post(
                        "/chat",
                        json={
                            "chat_id": chat_id,
                            "message": "when can I visit?",
                            "local_now": LOCAL_NOW,
                        },
                    )
                )
                post = asyncio.create_task(
                    http.post(
                        f"/console/chats/{chat_id}/messages",
                        json={"content": "I've got this one."},
                    )
                )
                posted = await asyncio.wait_for(post, timeout=5)
                gate.set()
                turn_response = await asyncio.wait_for(turn, timeout=5)

    assert posted.status_code == 201
    lines = [json.loads(line) for line in turn_response.text.strip().splitlines()]
    assert lines[-1]["type"] in {"cancelled", "silent"}
    async with session_factory() as session:
        messages = await chat_repository.list_messages(session, chat_id)
    assert all(m.sender != MessageSender.ASSISTANT for m in messages)


async def test_a_turn_that_completed_before_a_staff_post_transitions_nothing() -> None:
    # The window a cancellation cannot cover: by the time a turn writes what it decided,
    # it has already deregistered, so the post's `cancel_for_chat` finds nothing and its
    # clears run first. A transition applied behind them re-escalates a conversation a
    # person is already handling - and an escalation has no deadline, so the patient's
    # next message would be stored unanswered against the staff member replying to them.
    session_id, chat_id = await _chat()
    reached_the_writes = asyncio.Event()
    staff_has_posted = asyncio.Event()
    persist_outcome = turn_api._persist_outcome

    async def stalled(*args: object, **kwargs: object) -> None:
        """Hold the turn exactly where the race is: graph completed, nothing written."""
        reached_the_writes.set()
        await staff_has_posted.wait()
        await persist_outcome(*args, **kwargs)

    with (
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
        patch("chat.api.turn._persist_outcome", stalled),
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(
            intents=[IntentLabel.CALL_STAFF]
        )
        with TestClient(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as http:
                http.cookies.set("visitdoc_session_id", session_id)
                turn = asyncio.create_task(
                    http.post(
                        "/chat",
                        json={
                            "chat_id": chat_id,
                            "message": "can I speak to someone please?",
                            "local_now": LOCAL_NOW,
                        },
                    )
                )
                await asyncio.wait_for(reached_the_writes.wait(), timeout=5)
                posted = await http.post(
                    f"/console/chats/{chat_id}/messages", json={"content": _CONTENT}
                )
                staff_has_posted.set()
                await asyncio.wait_for(turn, timeout=5)

    assert posted.status_code == 201
    state = await _state(chat_id, session_id)
    assert state.escalated_at is None
    assert state.attention_since is None
    assert state.emphasized is False
    # Two messages, not three: the handoff sentence is a reply, and one written behind
    # the staff member's own is the reply FR-013a discards.
    assert await _marks(chat_id) == [None, None]


# --- a release that frees nothing ---------------------------------------------------
#
# Everything one post writes commits inside the locked section, so by the time the lock
# is released the reply is durable. A release reporting it held nothing is worth
# investigating - the lock may be stranded on a connection nothing here can reach, and
# the chat it keys would then never be lockable again - but it is not a reason to tell a
# staff member their reply was not sent: they would send it again, and the patient would
# read the same answer twice.


async def test_a_failed_lock_release_does_not_unsend_a_committed_reply() -> None:
    session_id, chat_id = await _chat(escalated=True)
    not_held = chat_repository.ChatLockNotHeldError("held nothing")

    with patch.object(chat_repository, "unlock_chat", side_effect=not_held):
        response = await _post(session_id, chat_id)

    assert response.status_code == 201
    assert response.json()["content"] == _CONTENT
    async with session_factory() as session:
        messages = await chat_repository.list_messages(session, chat_id)
    assert [m.sender for m in messages] == [MessageSender.STAFF]
    assert (await _state(chat_id, session_id)).escalated_at is None


async def test_a_failed_lock_release_is_recorded_twice_over() -> None:
    # The release that freed nothing, and that the caller was told the post succeeded
    # anyway - neither is inferable from the other, and both are worth investigating.
    # Neither is `critical`: `pg_advisory_unlock` answers false both when another live
    # connection still holds the lock and when the connection that took it has been
    # replaced, which released everything that backend held. Claiming a stranded lock
    # at the loudest level available would be asserting the worse of the two.
    session_id, chat_id = await _chat()
    not_held = chat_repository.ChatLockNotHeldError("held nothing")

    with (
        capture_logs() as logs,
        patch.object(chat_repository, "unlock_chat", side_effect=not_held),
    ):
        await _post(session_id, chat_id)

    events = {entry["event"]: entry for entry in logs}
    assert events["chat.lock_not_held"]["log_level"] == "error"
    assert events["chat.lock_not_held"]["chat_id"] == chat_id
    assert events["chat.lock_release_failed"]["log_level"] == "error"
    assert events["chat.lock_release_failed"]["chat_id"] == chat_id
    assert "held nothing" in events["chat.lock_release_failed"]["error_detail"]
    assert events["staff.message_posted"]["chat_id"] == chat_id


# --- a reply that finished generating and still lost -------------------------------
#
# The cancellation tests above interrupt a turn mid-sentence. The narrower window is the
# one after it: the graph has produced its `done` event, and the staff post lands before
# the turn has written anything. FR-013a is about the reply, not about how far it got -
# one nobody stored must not reach the patient either, or it sits in their window with
# the staff message under it and is gone from the thread on the next reload.


class _StalledGraph:
    """A `run_turn` stand-in that finishes generating, then waits to be released.

    Holds the turn exactly in the window under test: every event produced, nothing
    written yet.
    """

    def __init__(self, generated: asyncio.Event, release: asyncio.Event) -> None:
        self._generated = generated
        self._release = release

    async def __call__(
        self, *args: object, **kwargs: object
    ) -> AsyncIterator[ChatTokenEvent | ChatDoneEvent]:
        yield ChatTokenEvent(text="Visiting hours are 8am to 5pm.")
        yield ChatDoneEvent(grounded=True, citations=[])
        self._generated.set()
        await self._release.wait()


async def _turn_racing_a_staff_post(session_id: str, chat_id: str) -> Response:
    """Run a turn that finishes generating, post as staff, then let the turn go on."""
    generated = asyncio.Event()
    release = asyncio.Event()

    with (
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
        patch("chat.api.turn.run_turn", _StalledGraph(generated, release)),
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client()
        with TestClient(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as http:
                http.cookies.set("visitdoc_session_id", session_id)
                turn = asyncio.create_task(
                    http.post(
                        "/chat",
                        json={
                            "chat_id": chat_id,
                            "message": "when can I visit?",
                            "local_now": LOCAL_NOW,
                        },
                    )
                )
                await asyncio.wait_for(generated.wait(), timeout=5)
                posted = await http.post(
                    f"/console/chats/{chat_id}/messages",
                    json={"content": "I've got this one - 8am to 5pm."},
                )
                release.set()
                response = await asyncio.wait_for(turn, timeout=5)

    assert posted.status_code == 201
    return response


async def test_a_finished_reply_a_staff_post_beat_is_never_streamed() -> None:
    session_id, chat_id = await _chat()

    response = await _turn_racing_a_staff_post(session_id, chat_id)

    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert not any(line["type"] == "done" for line in lines)
    assert lines[-1] == {"type": "cancelled"}


async def test_a_finished_reply_a_staff_post_beat_is_never_stored() -> None:
    # The other half of the same guarantee, so a `done` suppressed while the reply was
    # written anyway would not read as a pass.
    session_id, chat_id = await _chat()

    await _turn_racing_a_staff_post(session_id, chat_id)

    async with session_factory() as session:
        messages = await chat_repository.list_messages(session, chat_id)
    assert [m.sender for m in messages] == [MessageSender.PATIENT, MessageSender.STAFF]


# --- the gap between deregistering and the writes -----------------------------------
#
# A turn deregisters itself *before* taking the chat's lock, deliberately: a staff post
# takes that lock first and only then asks for a cancellation, so a turn still
# registered while queued on the lock would be a cancellation waiting on the very lock
# its canceller holds. That leaves a window - deregistered, nothing written yet - where
# `cancel_for_chat` finds nothing to cancel and the gesture's own writes commit first.
# FR-013a is about the reply, not about whether anything managed to cancel it: one
# written behind a staff member's own is exactly the reply it says must not be left
# standing beside theirs.


class _FinishedGraph:
    """A `run_turn` stand-in that produces one complete reply and returns."""

    async def __call__(
        self, *args: object, **kwargs: object
    ) -> AsyncIterator[ChatTokenEvent | ChatDoneEvent]:
        yield ChatTokenEvent(text="Visiting hours are 8am to 5pm.")
        yield ChatDoneEvent(grounded=True, citations=[])


async def _turn_racing_a_gesture(
    session_id: str,
    chat_id: str,
    gesture: Callable[[AsyncClient], Awaitable[Response]],
) -> tuple[Response, Response]:
    """Hold a turn in the write gap, land `gesture` in it, then let the turn go on.

    Returns: the turn's streamed response, and the gesture's own.

    Only the *first* turn is held, so a gesture that is itself a second turn runs to
    completion inside the window rather than stalling in it too.
    """
    reached_the_writes = asyncio.Event()
    gesture_landed = asyncio.Event()
    persist_outcome = turn_api._persist_outcome
    calls = 0

    async def stalled(*args: object, **kwargs: object) -> object:
        """Hold the turn exactly where the race is: deregistered, nothing written."""
        nonlocal calls
        calls += 1
        if calls == 1:
            reached_the_writes.set()
            await gesture_landed.wait()
        return await persist_outcome(*args, **kwargs)

    with (
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
        patch("chat.api.turn.run_turn", _FinishedGraph()),
        patch("chat.api.turn._persist_outcome", stalled),
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client()
        with TestClient(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as http:
                http.cookies.set("visitdoc_session_id", session_id)
                turn = asyncio.create_task(
                    http.post(
                        "/chat",
                        json={
                            "chat_id": chat_id,
                            "message": "when can I visit?",
                            "local_now": LOCAL_NOW,
                        },
                    )
                )
                await asyncio.wait_for(reached_the_writes.wait(), timeout=5)
                gesture_response = await asyncio.wait_for(gesture(http), timeout=5)
                gesture_landed.set()
                turn_response = await asyncio.wait_for(turn, timeout=5)

    return turn_response, gesture_response


def _staff_post(chat_id: str) -> Callable[[AsyncClient], Awaitable[Response]]:
    """Return a gesture that posts as staff into `chat_id`."""
    return lambda http: http.post(
        f"/console/chats/{chat_id}/messages", json={"content": _CONTENT}
    )


async def test_a_staff_post_in_the_write_gap_leaves_no_assistant_reply() -> None:
    session_id, chat_id = await _chat()

    _, posted = await _turn_racing_a_gesture(session_id, chat_id, _staff_post(chat_id))

    assert posted.status_code == 201
    async with session_factory() as session:
        messages = await chat_repository.list_messages(session, chat_id)
    assert [m.sender for m in messages] == [MessageSender.PATIENT, MessageSender.STAFF]


async def test_a_staff_post_in_the_write_gap_ends_the_turn_without_an_answer() -> None:
    # The other half of the same guarantee: a reply nobody stored must not be left on
    # the patient's screen either, or the staff message sits under an answer that is
    # gone from the thread on the next reload.
    session_id, chat_id = await _chat()

    response, _ = await _turn_racing_a_gesture(
        session_id, chat_id, _staff_post(chat_id)
    )

    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert not any(line["type"] == "done" for line in lines)
    assert lines[-1] == {"type": "cancelled"}


async def test_the_switch_going_off_in_the_write_gap_discards_the_reply() -> None:
    # FR-017c: on exactly FR-013a's terms, by the same mechanism and not a second one.
    session_id, chat_id = await _chat()

    response, switched = await _turn_racing_a_gesture(
        session_id,
        chat_id,
        lambda http: http.post(
            f"/console/chats/{chat_id}/assistant", json={"enabled": False}
        ),
    )

    assert switched.status_code == 200
    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert lines[-1] == {"type": "cancelled"}
    async with session_factory() as session:
        messages = await chat_repository.list_messages(session, chat_id)
    assert [m.sender for m in messages] == [MessageSender.PATIENT]


async def test_a_superseding_patient_message_in_the_write_gap_keeps_the_reply() -> None:
    # The deliberate opposite. FR-013a discards a reply because a *person* is now
    # leading the conversation and two answers to one question contradict each other.
    # A patient's own next message is neither: nothing has contradicted the answer they
    # already asked for, and this turn won the supersede race outright - it deregistered
    # before the newer one registered, which is what `clear_if_current` reports.
    session_id, chat_id = await _chat()

    response, superseding = await _turn_racing_a_gesture(
        session_id,
        chat_id,
        lambda http: http.post(
            "/chat",
            json={
                "chat_id": chat_id,
                "message": "and on weekends?",
                "local_now": LOCAL_NOW,
            },
        ),
    )

    for streamed in (response, superseding):
        lines = [json.loads(line) for line in streamed.text.strip().splitlines()]
        assert lines[-1]["type"] == "done"
    async with session_factory() as session:
        messages = await chat_repository.list_messages(session, chat_id)
    assert sum(m.sender == MessageSender.ASSISTANT for m in messages) == 2
