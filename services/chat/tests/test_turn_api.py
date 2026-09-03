import asyncio
import json
from collections.abc import AsyncIterator
from enum import StrEnum
from unittest.mock import MagicMock, patch

import pytest
import structlog
from chat.agent import generation_registry
from chat.agent.answer_faq import _ABSTENTION_MESSAGE
from chat.agent.escalation import (
    HANDOFF_MESSAGE,
    EscalationRequests,
)
from chat.agent.tools.staff_tools import ESCALATE_TO_STAFF
from chat.api import turn as turn_api
from chat.core.config import Settings
from chat.db.session import engine, session_factory
from chat.domain.models import (
    AttentionMark,
    Chat,
    EscalationReason,
    MessageSender,
)
from chat.domain.schemas import (
    ChatDoneEvent,
    ChatSilentEvent,
    ChatTokenEvent,
    IntentLabel,
)
from chat.main import app
from chat.rag.indexing import publish_revision, remove_entry_chunks
from chat.repositories import chat_repository, faq_repository
from chat.repositories.chat_repository import ConversationState
from chat.repositories.qdrant_repository import create_client, ensure_collection
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs
from ulid import ULID

from .conftest import (
    LOCAL_NOW,
    FakeAnthropicStream,
    adopt_seeded_session,
    async_chat_id_for,
    async_turn,
    chat_id_for,
    fake_anthropic_client,
    fake_anthropic_client_gated,
    fake_anthropic_client_sequence,
    fake_embed_texts,
    seeded_session_id,
    set_seeded_session,
    turn,
)

_ENTRY_CONTENT = "Visiting hours are 8am to 5pm."


@pytest.fixture
async def seeded_entry() -> AsyncIterator[int]:
    """Seed one `FaqEntry` directly (bypassing the not-yet-built `/faq` API, per US1's
    Independent Test), indexed into Qdrant with fake embeddings, cleaned up afterward.
    """
    settings = Settings()
    qdrant_client = create_client(settings)
    await ensure_collection(qdrant_client)

    revision = str(ULID())
    async with session_factory() as session:
        seeded_session = await chat_repository.create_session(session)
        entry = await faq_repository.create(
            session, seeded_session.id, _ENTRY_CONTENT, revision
        )
    # An entry belongs to exactly one session, so the client has to talk to that one.
    set_seeded_session(seeded_session.id)

    with patch("chat.rag.indexing.embed_texts", fake_embed_texts):
        # voyage_client is irrelevant here: embed_texts is faked and ignores it.
        await publish_revision(
            qdrant_client,
            MagicMock(),
            seeded_session.id,
            entry.id,
            revision,
            _ENTRY_CONTENT,
        )

    # This fixture's own DB writes above bind `chat.db.session.engine`'s pool to
    # pytest-asyncio's session loop. Since `POST /chat` now touches the same engine
    # too (chat_repository), and a sync test's `TestClient(app)` block runs request
    # handling on its own separate loop (docs/testing-strategy.md), leaving the pool
    # bound here would collide with that. Disposing it at both handoff points -
    # before yielding to the test body, and again before this teardown's own DB
    # touches below - lets it rebind fresh to whichever loop next uses it.
    await engine.dispose()
    yield entry.id

    await engine.dispose()
    await remove_entry_chunks(qdrant_client, entry.id)
    async with session_factory() as session:
        await faq_repository.delete(session, seeded_session.id, entry.id)
    await qdrant_client.close()


def test_grounded_answer_streams_tokens_and_citations(seeded_entry: int) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(
            ["Visiting ", "hours are 8am to 5pm."]
        )
        with TestClient(app) as client:
            response = turn(client, "when can I visit?")

    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    token_lines = [line for line in lines if line["type"] == "token"]
    done_line = lines[-1]

    assert "".join(t["text"] for t in token_lines) == "Visiting hours are 8am to 5pm."
    assert done_line["type"] == "done"
    assert done_line["grounded"] is True
    assert any(c["entry_id"] == seeded_entry for c in done_line["citations"])


def test_abstention_on_unrelated_question(seeded_entry: int) -> None:
    question = "what is the weather today?"
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client()
        with TestClient(app) as client:
            response = turn(client, question)

    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.strip().splitlines()]

    assert len(lines) == 1
    assert lines[0]["type"] == "done"
    assert lines[0]["grounded"] is False
    assert lines[0]["citations"] == []


@pytest.mark.parametrize("message", ["", "a" * 2001])
def test_message_validation_rejects_empty_and_oversized(message: str) -> None:
    with TestClient(app) as client:
        response = turn(client, message)

    assert response.status_code == 422


def test_grounded_turn_logs_full_trace_under_one_turn_id(seeded_entry: int) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(
            ["Visiting ", "hours are 8am to 5pm."]
        )
        with (
            capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
            TestClient(app) as client,
        ):
            turn(client, "when can I visit?")

    events = {entry["event"]: entry for entry in logs}
    # Chat creation is not part of a turn and carries no turn id, so the "one turn
    # id" property is asserted over the lines that belong to a turn at all.
    turn_ids = {entry["turn_id"] for entry in logs if "turn_id" in entry}
    event_names = [entry["event"] for entry in logs]

    assert len(turn_ids) == 1  # every line of the turn shares one turn id
    assert events["turn.message_received"]["message"] == "when can I visit?"
    assert "turn.message_embedded" in events
    chunks = events["turn.retrieval_completed"]["retrieved_chunks"]
    assert any(c["entry_id"] == seeded_entry for c in chunks)
    scores = [c["score"] for c in chunks]
    assert scores == sorted(scores, reverse=True)
    assert events["turn.groundedness_verdict"]["grounded"] is True
    done = events["turn.completed"]
    assert done["outcome"] == "grounded"
    assert done["answer_text"] == "Visiting hours are 8am to 5pm."
    # The whole turn, not one node: no shorter than the slowest node within it.
    node_durations = [e["duration_ms"] for e in logs if e["event"] == "node.completed"]
    assert done["duration_ms"] >= max(node_durations)
    assert any(c["entry_id"] == seeded_entry for c in done["citations"])
    assert all("score" in c for c in done["citations"])
    # intent.classified sits between turn.message_received and turn.completed
    # (contracts/log-events.md §3, research.md #1/#8).
    assert "intent.classified" in events
    assert (
        event_names.index("turn.message_received")
        < event_names.index("intent.classified")
        < event_names.index("turn.completed")
    )


def test_abstained_turn_logs_full_trace_under_one_turn_id(seeded_entry: int) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client()
        with (
            capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
            TestClient(app) as client,
        ):
            turn(client, "what is the weather today?")

    events = {entry["event"]: entry for entry in logs}
    # Chat creation is not part of a turn and carries no turn id, so the "one turn
    # id" property is asserted over the lines that belong to a turn at all.
    turn_ids = {entry["turn_id"] for entry in logs if "turn_id" in entry}
    event_names = [entry["event"] for entry in logs]

    assert len(turn_ids) == 1
    assert "turn.message_received" in events
    assert "turn.message_embedded" in events
    assert "turn.retrieval_completed" in events
    assert events["turn.groundedness_verdict"]["grounded"] is False
    done = events["turn.completed"]
    assert done["outcome"] == "abstained"
    assert "abstention_message" in done
    assert "intent.classified" in events
    assert (
        event_names.index("turn.message_received")
        < event_names.index("intent.classified")
        < event_names.index("turn.completed")
    )


def test_a_message_reaching_no_specialist_still_gets_the_faq_path() -> None:
    """`unknown` has no specialist of its own, so it falls back to the FAQ path.

    The message carries no "visit"/"hours" keyword, so `fake_embed_texts` routes it to
    abstain - and the reply is exactly the abstention, never a fabricated booking or
    hand-off confirmation.
    """
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(
            intents=[IntentLabel.UNKNOWN]
        )
        with (
            capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
            TestClient(app) as client,
        ):
            response = turn(client, "what's the weather like today?")

    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert len(lines) == 1
    assert lines[0]["type"] == "done"
    assert lines[0]["grounded"] is False
    assert lines[0]["answer_source"] == "faq"
    assert lines[0]["message"] == _ABSTENTION_MESSAGE

    classified = next(e for e in logs if e["event"] == "intent.classified")
    assert [i.value for i in classified["intents"]] == ["unknown"]


def test_a_message_asking_for_a_person_gets_the_handoff_and_nothing_else() -> None:
    # `call_staff` is not a fall-through: it takes the whole turn. The patient is told a
    # staff member has it, and nothing is retrieved, generated or booked on the way.
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        anthropic_client = fake_anthropic_client(
            ["never generated"], intents=[IntentLabel.CALL_STAFF]
        )
        mock_anthropic_cls.return_value = anthropic_client
        with (
            capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
            TestClient(app) as client,
        ):
            response = turn(client, "I need to talk to someone about a billing problem")

    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    streamed = "".join(line["text"] for line in lines if line["type"] == "token")
    assert streamed == HANDOFF_MESSAGE
    assert lines[-1]["answer_source"] == "hand_off"
    assert lines[-1]["message"] != _ABSTENTION_MESSAGE

    events = [entry["event"] for entry in logs]
    assert "turn.retrieval_completed" not in events
    assert "turn.groundedness_verdict" not in events
    assert anthropic_client.messages.stream.call_count == 0


def test_classification_failure_does_not_block_the_faq_reply(seeded_entry: int) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(
            ["Visiting ", "hours are 8am to 5pm."],
            classify_error=RuntimeError("boom"),
        )
        with (
            capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
            TestClient(app) as client,
        ):
            response = turn(client, "when can I visit?")

    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    done_line = lines[-1]
    assert done_line["type"] == "done"
    assert done_line["grounded"] is True
    assert any(c["entry_id"] == seeded_entry for c in done_line["citations"])

    classified = next(e for e in logs if e["event"] == "intent.classified")
    assert classified["intents"] == [IntentLabel.CLASSIFICATION_FAILED]


async def test_cancelled_turn_gets_no_intent_classified_survivor_reflects_both() -> (
    None
):
    """research.md #2's concrete regression test (quickstart Scenario 3): a message
    whose turn is cancelled by a rapid follow-up gets no `intent.classified` line at
    all, while the surviving message's own line is produced from a classify_intent()
    call whose context already includes the cancelled message's content (FR-005/
    FR-006) - verified against the mock's actual call args, not just its hardcoded
    return value (docs/testing-strategy.md).
    """
    # capture_logs isn't task-safe (docs/testing-strategy.md), so a plain collector
    # processor is spliced into the real chain instead, ahead of the renderer.
    collected: list[dict[str, object]] = []

    def _collector(
        _logger: object, _method_name: str, event_dict: dict[str, object]
    ) -> dict[str, object]:
        collected.append(dict(event_dict))
        return event_dict

    async with session_factory() as db_session:
        session_row = await chat_repository.create_session(db_session)

    gate = asyncio.Event()
    started = asyncio.Event()
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        anthropic_client = fake_anthropic_client(
            ["Tuesday hours are 8am to 5pm."],
            intents=[IntentLabel.FAQ_QUESTION, IntentLabel.BOOKING],
            classify_gate=gate,
            classify_started=started,
        )
        mock_anthropic_cls.return_value = anthropic_client

        transport = ASGITransport(app=app)
        with TestClient(app):
            processors = structlog.get_config()["processors"]
            processors.insert(-1, _collector)
            try:
                async with AsyncClient(transport=transport, base_url="http://t") as ac:
                    ac.cookies.set("visitdoc_session_id", session_row.id)
                    first_task = asyncio.create_task(
                        async_turn(ac, "What are your working hours")
                    )
                    await asyncio.wait_for(started.wait(), timeout=5)
                    started.clear()

                    second_message = "actually, can I just book a slot Tuesday?"
                    second_task = asyncio.create_task(async_turn(ac, second_message))
                    # message 2 reaching its own gated classify call proves
                    # register_and_cancel_previous has already cancelled message 1's
                    # still-suspended task by this point (it's serialized ahead of
                    # this call in api/turn.py) - only then is it safe to release the
                    # gate for message 2's own (sole surviving) call.
                    await asyncio.wait_for(started.wait(), timeout=5)
                    gate.set()

                    first_response, second_response = await asyncio.wait_for(
                        asyncio.gather(first_task, second_task), timeout=5
                    )
            finally:
                processors.remove(_collector)

    first_lines = [
        json.loads(line) for line in first_response.text.strip().splitlines()
    ]
    second_lines = [
        json.loads(line) for line in second_response.text.strip().splitlines()
    ]
    assert first_lines[-1] == {"type": "cancelled"}
    assert second_lines[-1]["type"] == "done"

    classified_events = [e for e in collected if e["event"] == "intent.classified"]
    assert len(classified_events) == 1
    assert classified_events[0]["intents"] == [
        IntentLabel.FAQ_QUESTION,
        IntentLabel.BOOKING,
    ]

    # The one classify_intent() call that actually completed (the survivor's) was
    # given context spanning both messages, not just its own - the concrete proof
    # the cancelled message's content still reached the surviving call (FR-006).
    last_call_messages = anthropic_client.messages.create.call_args_list[-1].kwargs[
        "messages"
    ]
    combined_text = " ".join(m["content"] for m in last_call_messages)
    assert "working hours" in combined_text
    assert "book a slot Tuesday" in combined_text


async def test_turn_message_received_fires_for_every_message_even_a_cancelled_one() -> (
    None
):
    """research.md #8's regression case: `turn.message_received` is emitted for every
    incoming patient message - including one whose turn is later cancelled - and
    always before that turn's `intent.classified`/`turn.cancelled` line.
    """
    # capture_logs isn't task-safe (docs/testing-strategy.md), so a plain collector
    # processor is spliced into the real chain instead, ahead of the renderer.
    collected: list[dict[str, object]] = []

    def _collector(
        _logger: object, _method_name: str, event_dict: dict[str, object]
    ) -> dict[str, object]:
        collected.append(dict(event_dict))
        return event_dict

    async with session_factory() as db_session:
        session_row = await chat_repository.create_session(db_session)

    gate = asyncio.Event()
    started = asyncio.Event()
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(
            ["Visiting hours are 8am to 5pm."],
            classify_gate=gate,
            classify_started=started,
        )

        transport = ASGITransport(app=app)
        with TestClient(app):
            processors = structlog.get_config()["processors"]
            processors.insert(-1, _collector)
            try:
                async with AsyncClient(transport=transport, base_url="http://t") as ac:
                    ac.cookies.set("visitdoc_session_id", session_row.id)
                    first_task = asyncio.create_task(
                        async_turn(ac, "when can I visit?")
                    )
                    await asyncio.wait_for(started.wait(), timeout=5)
                    started.clear()

                    second_task = asyncio.create_task(
                        async_turn(ac, "actually, what about Tuesday?")
                    )
                    await asyncio.wait_for(started.wait(), timeout=5)
                    gate.set()

                    await asyncio.wait_for(
                        asyncio.gather(first_task, second_task), timeout=5
                    )
            finally:
                processors.remove(_collector)

    received_events = [e for e in collected if e["event"] == "turn.message_received"]
    assert len(received_events) == 2

    by_turn: dict[object, list[str]] = {}
    for entry in collected:
        by_turn.setdefault(entry.get("turn_id"), []).append(str(entry["event"]))
    # turn.cancelled's own (ambient) turn_id is the *superseding* turn's, not the
    # cancelled one - the cancelled turn's id is its `cancelled_turn_id` field.
    turn_cancelled_entry = next(e for e in collected if e["event"] == "turn.cancelled")
    cancelled_turn = turn_cancelled_entry["cancelled_turn_id"]
    assert "turn.message_received" in by_turn[cancelled_turn]
    assert "intent.classified" not in by_turn[cancelled_turn]
    # `collected`'s order is chronological (each entry appended as it's logged) -
    # the cancelled turn's own turn.message_received precedes the turn.cancelled
    # line that reports it, wherever that line's ambient turn_id points.
    received_index = next(
        i
        for i, e in enumerate(collected)
        if e["event"] == "turn.message_received" and e["turn_id"] == cancelled_turn
    )
    cancelled_index = collected.index(turn_cancelled_entry)
    assert received_index < cancelled_index


def test_classified_intents_are_reviewable_from_logs_without_rerunning(
    seeded_entry: int,
) -> None:
    """spec.md User Story 3 / SC-002 (quickstart Scenario 4): after sending several
    messages with different intents, a maintainer can look up each one's classified
    intent from the captured logs alone, by `turn_id`, without re-running the
    conversation.
    """
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client_sequence(
            [
                [IntentLabel.FAQ_QUESTION],
                [IntentLabel.BOOKING],
                [IntentLabel.CALL_STAFF],
            ],
            ["Visiting hours are 8am to 5pm."],
        )
        with (
            capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
            TestClient(app) as client,
        ):
            r1 = turn(client, "when can I visit?")
            r2 = turn(client, "I'd like to book an appointment")
            r3 = turn(client, "I need to speak to someone about billing")

    patient_turn_ids = [
        entry["turn_id"]
        for entry in logs
        if entry["event"] == "message.persisted" and entry["sender"] == "patient"
    ]
    assert len(patient_turn_ids) == 3
    assert r1.status_code == r2.status_code == r3.status_code == 200

    classified_by_turn = {
        entry["turn_id"]: entry["intents"]
        for entry in logs
        if entry["event"] == "intent.classified"
    }
    assert len(classified_by_turn) == 3
    expected = [
        [IntentLabel.FAQ_QUESTION],
        [IntentLabel.BOOKING],
        [IntentLabel.CALL_STAFF],
    ]
    for turn_id, want in zip(patient_turn_ids, expected, strict=True):
        assert classified_by_turn[turn_id] == want


async def _post_two_chat_requests(asgi_app: FastAPI) -> None:
    """Run two turns concurrently, each in its own chat.

    One chat per turn deliberately: two concurrent turns in the *same* chat race to
    supersede each other, and the loser is cancelled mid-pipeline, so whether both
    turns reach `turn.completed` would depend on scheduling order. Supersession has
    its own tests; this one is about turn ids staying separate across concurrent
    tasks.
    """
    transport = ASGITransport(app=asgi_app)
    async with (
        AsyncClient(transport=transport, base_url="http://t") as first,
        AsyncClient(transport=transport, base_url="http://t") as second,
    ):
        # Both chats are created up front, so the gather below launches two turns and
        # nothing else: chat creation is slower than a turn whose model and embeddings
        # are faked, so leaving it inside `async_turn` would let the first turn finish
        # while the second client was still creating its chat - no overlap at all.
        await async_chat_id_for(first)
        await async_chat_id_for(second)
        await asyncio.gather(
            async_turn(first, "when can I visit?"),
            async_turn(second, "when can I visit?"),
        )


def test_generation_failure_logs_turn_error_with_step(seeded_entry: int) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(
            stream_error=RuntimeError("boom")
        )
        with (
            capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
            TestClient(app, raise_server_exceptions=False) as client,
        ):
            turn(client, "when can I visit?")

    events = {entry["event"]: entry for entry in logs}
    # Chat creation is not part of a turn and carries no turn id, so the "one turn
    # id" property is asserted over the lines that belong to a turn at all.
    turn_ids = {entry["turn_id"] for entry in logs if "turn_id" in entry}

    assert len(turn_ids) == 1
    assert events["turn.error"]["pipeline_step"] == "generation"
    assert "boom" in events["turn.error"]["error_detail"]


async def test_generation_failure_clears_in_flight_registry_entry(
    seeded_entry: int,
) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(
            stream_error=RuntimeError("boom")
        )
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        with TestClient(app):
            async with AsyncClient(transport=transport, base_url="http://t") as ac:
                await async_turn(ac, "when can I visit?")
                chat_id = await async_chat_id_for(ac)

    assert chat_id not in generation_registry._in_flight


async def test_concurrent_messages_on_existing_chat_both_reach_history(
    seeded_entry: int,
) -> None:
    """Regression: two concurrent messages on the same existing chat must not race -
    the second's history read must never miss the first's not-yet-committed message
    (research.md #5/#6) - the advisory lock means a second request can't read history
    until the first's message insert has fully committed.
    """
    async with session_factory() as db_session:
        # The seeded corpus belongs to one session, so this turn has to run in it -
        # a session of its own would retrieve nothing and abstain.
        await chat_repository.create_chat(db_session, seeded_session_id())

    real_list_messages = chat_repository.list_messages
    started = asyncio.Event()
    gate = asyncio.Event()
    call_count = 0

    async def gated_list_messages(session: object, chat_id: str) -> object:
        nonlocal call_count
        call_count += 1
        result = await real_list_messages(session, chat_id)  # type: ignore[arg-type]
        if call_count == 1:
            started.set()
            await gate.wait()
        return result

    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
        patch(
            "chat.repositories.chat_repository.list_messages",
            side_effect=gated_list_messages,
        ),
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(
            ["Visiting hours are 8am to 5pm."]
        )
        transport = ASGITransport(app=app)
        with TestClient(app):
            async with AsyncClient(transport=transport, base_url="http://t") as ac:
                ac.cookies.set("visitdoc_session_id", seeded_session_id())
                # Doesn't itself carry any FAQ-matching signal - abstains, never
                # reaches Claude (see fake_embed_texts).
                first_task = asyncio.create_task(
                    async_turn(ac, "I need help with something else first")
                )
                await asyncio.wait_for(started.wait(), timeout=5)

                # Grounds against `seeded_entry`'s FAQ content, so this is the only
                # message expected to reach Claude.
                second_task = asyncio.create_task(async_turn(ac, "when can I visit?"))
                await asyncio.sleep(0.2)
                gate.set()

                await asyncio.wait_for(
                    asyncio.gather(first_task, second_task), timeout=5
                )

    calls = mock_anthropic_cls.return_value.messages.stream.call_args_list
    assert len(calls) == 1
    messages_sent = calls[0].kwargs["messages"]
    assert any(
        "I need help with something else first" in m["content"] for m in messages_sent
    )


def test_concurrent_turns_keep_distinct_turn_ids(seeded_entry: int) -> None:
    # capture_logs isn't task-safe (docs/testing-strategy.md), so a plain collector
    # processor is spliced into the real chain instead, ahead of the renderer.
    collected: list[dict[str, object]] = []

    def _collector(
        _logger: object, _method_name: str, event_dict: dict[str, object]
    ) -> dict[str, object]:
        collected.append(dict(event_dict))
        return event_dict

    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(["ok"])
        with TestClient(app):
            processors = structlog.get_config()["processors"]
            processors.insert(-1, _collector)
            try:
                asyncio.run(_post_two_chat_requests(app))
            finally:
                processors.remove(_collector)

    by_turn: dict[object, list[dict[str, object]]] = {}
    for entry in collected:
        # Chat creation happens outside any turn and carries no turn id.
        if "turn_id" not in entry:
            continue
        by_turn.setdefault(entry["turn_id"], []).append(entry)

    assert len(by_turn) == 2
    for turn_id, entries in by_turn.items():
        assert turn_id is not None
        event_names = {entry["event"] for entry in entries}
        assert "turn.message_received" in event_names
        assert "turn.completed" in event_names
        assert all(entry["turn_id"] == turn_id for entry in entries)


def test_anthropic_and_voyage_clients_are_reused_across_chat_requests(
    seeded_entry: int,
) -> None:
    """finding #6: `AsyncAnthropic`/Voyage `AsyncClient` must be constructed once at
    app startup (main.py's lifespan) and reused, not rebuilt on every `/chat` request.
    Two requests in the same app lifespan must still see exactly one constructor call
    each.
    """
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
        patch("chat.main.AsyncClient") as mock_voyage_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(["ok"])
        with TestClient(app) as client:
            first_response = turn(client, "when can I visit?")
            second_response = turn(client, "when can I visit?")

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    mock_anthropic_cls.assert_called_once()
    mock_voyage_cls.assert_called_once()


def test_session_cookie_issued_on_first_chat_and_reused_by_every_turn(
    seeded_entry: int,
) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(["ok"])
        with TestClient(app) as client:
            created = client.post("/chats")
            assert "visitdoc_session_id" in created.cookies
            session_id = created.cookies["visitdoc_session_id"]

            first = turn(client, "when can I visit?")
            second = turn(client, "when can I visit?")

    # A turn never mints or reissues the cookie - only chat creation does.
    assert "set-cookie" not in first.headers
    assert "set-cookie" not in second.headers
    assert client.cookies["visitdoc_session_id"] == session_id


async def _end_escalation(chat_id: str) -> None:
    """Let the assistant speak again in `chat_id`, as a person taking it would.

    An abstention hands the conversation to staff and silences the assistant in it, so
    a second turn in the same chat only happens once somebody has ended that. Done
    directly here because these two tests are about how history carries forward, not
    about how an escalation ends.
    """
    session_id = await _session_of(chat_id)
    async with session_factory() as db_session:
        await chat_repository.clear_escalation(db_session, chat_id, session_id)


async def test_followup_reply_uses_earlier_message_as_history(
    seeded_entry: int,
) -> None:
    await engine.dispose()
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        anthropic_client = fake_anthropic_client(["Tuesday hours are 8am to 5pm."])
        mock_anthropic_cls.return_value = anthropic_client
        with TestClient(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as http:
                adopt_seeded_session(http)
                chat_id = await async_chat_id_for(http)
                # Doesn't itself carry any FAQ-matching signal - abstains, but is still
                # persisted and available as context for the next turn (FR-003).
                await async_turn(http, "I'm going to come on Tuesday")
                await _end_escalation(chat_id)
                await async_turn(http, "what are your working hours that day?")

    calls = anthropic_client.messages.stream.call_args_list
    assert len(calls) == 1  # message 1 abstained - never reached Claude
    messages_sent = calls[0].kwargs["messages"]
    assert any(
        m["role"] == "user" and "Tuesday" in m["content"] for m in messages_sent[:-1]
    )


async def test_followup_still_abstains_when_neither_message_is_grounded(
    seeded_entry: int,
) -> None:
    await engine.dispose()
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client()
        with TestClient(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as http:
                adopt_seeded_session(http)
                chat_id = await async_chat_id_for(http)
                await async_turn(http, "hello")
                await _end_escalation(chat_id)
                response = await async_turn(http, "how are you")

    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert lines[-1]["type"] == "done"
    assert lines[-1]["grounded"] is False


async def test_burst_cancels_earlier_generation_and_yields_one_reply(
    seeded_entry: int,
) -> None:
    # `httpx.ASGITransport` only returns a response once the ASGI app call fully
    # completes - it gives no incremental access to a StreamingResponse's body while
    # it's still in flight (verified against its source: `handle_async_request`
    # awaits `self.app(...)` to completion before constructing any `Response`). So
    # this pre-creates a real `Session` row and sends its id as the cookie on both
    # requests from the start, rather than trying to read it off an in-flight
    # response - message 1 and message 2 still genuinely overlap server-side, as two
    # independently scheduled `asyncio.Task`s.
    # The seeded corpus belongs to one session, so this turn runs in that one -
    # a fresh session's corpus is empty and every message would abstain.
    burst_session_id = seeded_session_id()

    gate = asyncio.Event()
    started = asyncio.Event()
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client_gated(
            ["Tuesday hours are 8am to 5pm."], gate, started=started
        )
        transport = ASGITransport(app=app)

        with (
            capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
            TestClient(app),
        ):
            async with AsyncClient(transport=transport, base_url="http://t") as ac:
                ac.cookies.set("visitdoc_session_id", burst_session_id)
                first_task = asyncio.create_task(
                    async_turn(ac, "What are your working hours")
                )
                await asyncio.wait_for(started.wait(), timeout=5)
                started.clear()

                second_task = asyncio.create_task(
                    async_turn(ac, "on Tuesdays specifically?")
                )
                # message 2 has now also reached its gated call
                await asyncio.wait_for(started.wait(), timeout=5)
                gate.set()

                first_response, second_response = await asyncio.wait_for(
                    asyncio.gather(first_task, second_task), timeout=5
                )
                chat_id = await async_chat_id_for(ac)

    first_lines = [
        json.loads(line) for line in first_response.text.strip().splitlines()
    ]
    second_lines = [
        json.loads(line) for line in second_response.text.strip().splitlines()
    ]

    assert first_lines[-1] == {"type": "cancelled"}
    assert second_lines[-1]["type"] == "done"
    assert second_lines[-1]["grounded"] is True

    async with session_factory() as db_session:
        messages = await chat_repository.list_messages(db_session, chat_id)

    assert [m.sender for m in messages] == ["patient", "patient", "assistant"]
    assert messages[0].content == "What are your working hours"
    assert messages[1].content == "on Tuesdays specifically?"
    # The reply answers both merged patient messages, not just the one that
    # triggered this generation - see reply_to_message_ids' docstring.
    assert messages[2].reply_to_message_ids == [messages[0].id, messages[1].id]

    # The surviving turn's turn.message_received (turn_id reuses the second patient
    # message's id, research.md #4) must log the merged burst text it actually
    # answers against, not just the second fragment that arrived last - contracts/
    # log-events.md's "a reader can therefore always tell what unified/merged message
    # a turn is processing" contract.
    received_events = [
        entry
        for entry in logs
        if entry["event"] == "turn.message_received"
        and entry["turn_id"] == messages[1].id
    ]
    assert len(received_events) == 1
    assert (
        received_events[0]["message"]
        == "What are your working hours\n\non Tuesdays specifically?"
    )


async def test_pipeline_failure_keeps_patient_message_as_context_for_next_turn(
    seeded_entry: int,
) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        anthropic_client = fake_anthropic_client(stream_error=RuntimeError("boom"))
        mock_anthropic_cls.return_value = anthropic_client

        # The first call's pipeline failure is expected to propagate as a genuine
        # error (matching spec 001's existing behavior) - don't let the transport
        # re-raise it here, so the response can still be inspected below.
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        with TestClient(app):
            async with AsyncClient(transport=transport, base_url="http://t") as ac:
                # Must itself ground (contains "hours") so it reaches the mocked,
                # raising Claude call - unlike a purely informational message, which
                # would abstain before ever calling Claude.
                await async_turn(ac, "What are your working hours on Tuesday")

                # Reconfigure the same mocked client to succeed for the next call.
                anthropic_client.messages.stream.side_effect = None
                anthropic_client.messages.stream.return_value = FakeAnthropicStream(
                    ["Tuesday hours are 8am to 5pm."]
                )
                # `ac`'s own cookie jar already carries the session cookie from the
                # first response.
                await async_turn(ac, "what are your working hours that day?")
                chat_id = await async_chat_id_for(ac)

    calls = anthropic_client.messages.stream.call_args_list
    assert len(calls) == 2  # first raised inside the call itself, still "called"
    second_call_messages = calls[1].kwargs["messages"]
    # Message 1 never got a reply, so it's part of the same unanswered trailing run
    # as message 2 and gets merged into the final turn (research.md #5), not kept as
    # a separate prior entry - either way, its content still reached Claude (FR-012).
    assert any("Tuesday" in m["content"] for m in second_call_messages)

    async with session_factory() as session:
        messages = await chat_repository.list_messages(session, chat_id)

    assert [m.sender for m in messages] == ["patient", "patient", "assistant"]


def test_get_chat_history_is_empty_for_a_chat_with_no_messages() -> None:
    with TestClient(app) as client:
        chat_id = client.post("/chats").json()["id"]
        response = client.get(f"/chats/{chat_id}/messages")
    assert response.status_code == 200
    assert response.json() == {"messages": []}


def test_get_chat_history_returns_messages_in_chronological_order(
    seeded_entry: int,
) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(
            ["Visiting hours are 8am to 5pm."]
        )
        with TestClient(app) as client:
            turn(client, "when can I visit?")
            history_response = client.get(f"/chats/{chat_id_for(client)}/messages")

    assert history_response.status_code == 200
    messages = history_response.json()["messages"]
    assert [m["sender"] for m in messages] == ["patient", "assistant"]
    assert messages[0]["content"] == "when can I visit?"
    assert messages[1]["content"] == "Visiting hours are 8am to 5pm."
    assert messages[1]["grounded"] is True
    assert len(messages[1]["citations"]) > 0
    assert "created_at" in messages[0]


def test_get_chat_history_preserves_abstention(seeded_entry: int) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client()
        with TestClient(app) as client:
            turn(client, "what is the weather today?")
            history_response = client.get(f"/chats/{chat_id_for(client)}/messages")

    messages = history_response.json()["messages"]
    assert messages[1]["grounded"] is False
    assert messages[1]["citations"] == []
    assert messages[1]["content"] == _ABSTENTION_MESSAGE


def test_get_chat_history_persists_across_simulated_reload(seeded_entry: int) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(["ok"])
        with TestClient(app) as client:
            turn(client, "when can I visit?")
            # Simulated reload: a later GET on the same cookie jar sees the same data.
            first_load = client.get(f"/chats/{chat_id_for(client)}/messages").json()
            second_load = client.get(f"/chats/{chat_id_for(client)}/messages").json()

    assert first_load == second_load
    assert len(first_load["messages"]) == 2


async def test_get_chat_history_shows_burst_without_forced_alternation() -> None:
    async with session_factory() as db_session:
        session_row = await chat_repository.create_session(db_session)
        chat = await chat_repository.create_chat(db_session, session_row.id)
        await chat_repository.create_message(
            db_session,
            id=str(ULID()),
            chat_id=chat.id,
            session_id=session_row.id,
            sender=MessageSender.PATIENT,
            content="When can I see",
        )
        await chat_repository.create_message(
            db_session,
            id=str(ULID()),
            chat_id=chat.id,
            session_id=session_row.id,
            sender=MessageSender.PATIENT,
            content="Dr. Josh?",
        )
        await chat_repository.create_message(
            db_session,
            id=str(ULID()),
            chat_id=chat.id,
            session_id=session_row.id,
            sender=MessageSender.ASSISTANT,
            content="Dr. Josh is available Tuesdays.",
            grounded=True,
            citations=[],
        )

    transport = ASGITransport(app=app)
    with TestClient(app):
        async with AsyncClient(transport=transport, base_url="http://t") as ac:
            ac.cookies.set("visitdoc_session_id", session_row.id)
            response = await ac.get(f"/chats/{chat.id}/messages")

    messages = response.json()["messages"]
    assert [m["sender"] for m in messages] == ["patient", "patient", "assistant"]
    assert messages[0]["content"] == "When can I see"
    assert messages[1]["content"] == "Dr. Josh?"
    assert messages[2]["content"] == "Dr. Josh is available Tuesdays."


def test_a_second_chat_in_one_session_carries_no_memory_of_the_first(
    seeded_entry: int,
) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        anthropic_client = fake_anthropic_client(["ok"])
        mock_anthropic_cls.return_value = anthropic_client
        with TestClient(app) as client:
            adopt_seeded_session(client)
            first_chat = client.post("/chats").json()["id"]
            client.post(
                "/chat",
                json={
                    "chat_id": first_chat,
                    "message": "What are your hours on Tuesday",
                    "local_now": LOCAL_NOW,
                },
            )
            second_chat = client.post("/chats").json()["id"]
            client.post(
                "/chat",
                json={
                    "chat_id": second_chat,
                    "message": "what are your working hours",
                    "local_now": LOCAL_NOW,
                },
            )

    calls = anthropic_client.messages.stream.call_args_list
    assert len(calls) == 2  # both messages ground independently
    second_call_messages = calls[1].kwargs["messages"]
    assert not any("Tuesday" in m["content"] for m in second_call_messages)


def test_a_booking_only_turn_persists_its_reply_and_reports_no_grounding(
    seeded_entry: int,
) -> None:
    """The wire shape and the persisted row for a booking-only turn.

    `run_turn` is covered on its own, but nothing else exercises `_event_stream`'s two
    rewritten lines for this path - the reply that gets persisted, and the `grounded`
    it is stored with. A regression there is invisible until the chat is reloaded and
    the assistant's bubble comes back blank.
    """
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(
            intents=[IntentLabel.BOOKING],
            booking_reply="What day suits you?",
        )
        with TestClient(app) as client:
            response = turn(client, "I'd like an appointment")
            chat_id = chat_id_for(client)
            history = client.get(f"/chats/{chat_id}/messages").json()["messages"]

    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    streamed = "".join(line["text"] for line in lines if line["type"] == "token")
    done_line = lines[-1]
    assert done_line["type"] == "done"
    assert done_line["answer_source"] == "booking"
    # Never retrieved against, so neither grounded nor abstaining.
    assert done_line["grounded"] is None

    assistant = [m for m in history if m["sender"] == "assistant"]
    assert len(assistant) == 1
    # Asserted against what the turn actually streamed rather than the canned reply:
    # what is under test is that the persisted row is the reply the patient saw.
    assert assistant[0]["content"] == streamed
    assert assistant[0]["content"] != ""
    assert assistant[0]["grounded"] is None


def test_a_mixed_intent_turn_persists_the_merged_reply(seeded_entry: int) -> None:
    """A merged turn's reply is composed, not either specialist's own text.

    What is checkable without asserting against canned text is that the persisted
    content is exactly what the turn streamed, and that it is stored with the FAQ
    half's grounding rather than the booking half's absent one.
    """
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(
            ["Visiting ", "hours are 8am to 5pm."],
            intents=[IntentLabel.FAQ_QUESTION, IntentLabel.BOOKING],
        )
        with TestClient(app) as client:
            response = turn(client, "when can I visit, and can I book Friday?")
            chat_id = chat_id_for(client)
            history = client.get(f"/chats/{chat_id}/messages").json()["messages"]

    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    streamed = "".join(line["text"] for line in lines if line["type"] == "token")
    done_line = lines[-1]
    assert done_line["type"] == "done"
    assert done_line["answer_source"] == "merged"

    assistant = [m for m in history if m["sender"] == "assistant"]
    assert len(assistant) == 1
    # The persisted reply is the one the patient saw, not a second rendering of it.
    assert assistant[0]["content"] == streamed
    assert assistant[0]["content"] != ""


# --- 007 (FR-042j): an unreachable corpus is never reported as an empty one --------


def test_an_empty_corpus_abstains_rather_than_failing() -> None:
    # The ordinary starting state of every session. Nothing is wrong, so nothing is
    # reported as wrong - the turn completes and says it has no confident answer.
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(["unused"])
        with TestClient(app) as client:
            response = turn(client, "when can I visit?")

    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert lines[-1]["type"] == "done"
    assert lines[-1]["grounded"] is False


def test_an_unreadable_corpus_fails_the_turn_and_never_abstains() -> None:
    # The failure this exists to prevent: telling the patient the corpus has no answer
    # for them, which is a claim nothing verified. A read that failed is a dependency
    # failure and must be reported as one - so zero `done` lines, and no abstention.
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
        patch(
            "chat.repositories.faq_repository.live_revisions",
            side_effect=RuntimeError("connection refused"),
        ),
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(["unused"])
        with TestClient(app, raise_server_exceptions=False) as client:
            response = turn(client, "when can I visit?")

    assert response.status_code == 503
    # The point of the requirement: no abstention, because nothing checked the corpus.
    assert "done" not in response.text
    assert "grounded" not in response.text


# --- 007 (US1): abstention hands the conversation to a person ----------------------


async def _session_of(chat_id: str) -> str:
    async with session_factory() as db_session:
        chat = await db_session.get(Chat, chat_id)
        assert chat is not None
        return chat.session_id


async def _conversation_state(chat_id: str) -> ConversationState:
    session_id = await _session_of(chat_id)
    async with session_factory() as db_session:
        state = await chat_repository.get_conversation_state(
            db_session, chat_id, session_id
        )
    assert state is not None
    return state


async def _marks_in(chat_id: str) -> list[str | None]:
    async with session_factory() as db_session:
        messages = await chat_repository.list_messages(db_session, chat_id)
    return [m.attention_mark for m in messages]


async def test_an_abstention_hands_the_conversation_to_staff(seeded_entry: int) -> None:
    # FR-003b: the signal that produces the abstention is the signal that escalates,
    # so the two can never disagree - and the patient is not left at a dead end.
    await engine.dispose()
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        anthropic_client = fake_anthropic_client(["never generated"])
        mock_anthropic_cls.return_value = anthropic_client
        with TestClient(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as http:
                adopt_seeded_session(http)
                chat_id = await async_chat_id_for(http)
                response = await async_turn(http, "what is the weather today?")

    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert lines[-1]["type"] == "done"
    assert lines[-1]["grounded"] is False
    # No speculative answer alongside the abstention: the turn produced no tokens at
    # all, so there is nothing for a patient to mistake for an answer (FR-003b).
    assert not [line for line in lines if line["type"] == "token"]

    state = await _conversation_state(chat_id)
    assert state.escalation_reason == EscalationReason.CORPUS_COULD_NOT_ANSWER
    assert state.may_assistant_reply is False
    assert state.attention_since is not None
    assert AttentionMark.CORPUS_COULD_NOT_ANSWER in await _marks_in(chat_id)
    # Recorded before any generation call, and here that is provable rather than
    # ordered: an abstaining turn makes no generation call at all.
    assert anthropic_client.messages.stream.call_count == 0


async def test_an_empty_corpus_abstention_escalates_with_no_exemption() -> None:
    # FR-003c/SC-001a. The most tempting exemption in the feature - "there was nothing
    # to find, so nobody need be called" - and the spec rules it out in terms: a
    # visitor whose question the clinic has no answer for is exactly who needs a person.
    await engine.dispose()
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(["never generated"])
        with TestClient(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as http:
                chat_id = await async_chat_id_for(http)
                response = await async_turn(http, "when can I visit?")

    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert lines[-1]["grounded"] is False
    state = await _conversation_state(chat_id)
    assert state.escalation_reason == EscalationReason.CORPUS_COULD_NOT_ANSWER


async def test_a_turn_that_escalates_delivers_its_whole_reply_first(
    seeded_entry: int,
) -> None:
    # FR-006: the turn runs to completion and the state takes effect at the end of it.
    # A mixed-intent message whose FAQ half abstains and whose booking half answers
    # delivers both halves, and escalates afterwards (spec Edge Cases).
    await engine.dispose()
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(
            ["Here is ", "what I can tell you."],
            intents=[IntentLabel.FAQ_QUESTION, IntentLabel.BOOKING],
        )
        with TestClient(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as http:
                adopt_seeded_session(http)
                chat_id = await async_chat_id_for(http)
                response = await async_turn(
                    http, "what is the weather, and can I book Friday?"
                )
                history = (await http.get(f"/chats/{chat_id}/messages")).json()[
                    "messages"
                ]

    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    streamed = "".join(line["text"] for line in lines if line["type"] == "token")
    assert lines[-1]["type"] == "done"
    assert lines[-1]["answer_source"] == "merged"
    assert streamed != ""
    # The reply was delivered in full and stored, not withheld because the turn was
    # about to hand the conversation over.
    assistant = [m for m in history if m["sender"] == "assistant"]
    assert len(assistant) == 1
    assert assistant[0]["content"] == streamed

    state = await _conversation_state(chat_id)
    assert state.escalation_reason == EscalationReason.CORPUS_COULD_NOT_ANSWER


async def test_the_handoff_turn_asks_the_patient_for_no_confirmation() -> None:
    # FR-004: unlike a change to an appointment, an escalation alters no record the
    # patient holds and is reversible - so one turn both answers and hands over, with
    # no "are you sure?" round trip in between.
    await engine.dispose()
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
                chat_id = await async_chat_id_for(http)
                response = await async_turn(http, "can I speak to someone please")
                history = (await http.get(f"/chats/{chat_id}/messages")).json()[
                    "messages"
                ]

    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert lines[-1]["type"] == "done"
    # The reply reached the patient in this same turn: silence begins with the *next*
    # message, not with the one that asked for a person (spec Edge Cases).
    assert [m["sender"] for m in history] == ["patient", "assistant"]
    assert (await _conversation_state(chat_id)).may_assistant_reply is False


def test_the_tool_tells_the_model_to_promise_no_response_time() -> None:
    # FR-005's "names no timeframe" is a property of the instruction, not of one
    # sampled reply: asserting it against a scripted response would only prove the
    # script. What is checkable is that the model is told, in the one place it reads.
    description = ESCALATE_TO_STAFF.description.lower()
    assert "reply" in description
    assert "this same conversation" in description or "this conversation" in description
    assert "do not promise a response time" in description


# --- 007 (FR-019a/b): the assistant never answers into a silence after the fact ----


async def _silenced_message(session_id: str, chat_id: str, content: str) -> str:
    """Insert a patient message that arrived while the assistant could not reply."""
    message_id = str(ULID())
    async with session_factory() as db_session:
        await chat_repository.create_message(
            db_session,
            id=message_id,
            chat_id=chat_id,
            session_id=session_id,
            sender=MessageSender.PATIENT,
            content=content,
        )
        await chat_repository.set_attention_mark(
            db_session, chat_id, session_id, message_id, AttentionMark.UNANSWERED
        )
    await engine.dispose()
    return message_id


async def test_a_turn_after_a_silence_answers_only_what_came_after_it(
    seeded_entry: int,
) -> None:
    # FR-019a/FR-019b and SC-009b, end to end. The burst-merging rule of 003 would pull
    # the silenced message into this turn - it is an unanswered patient message
    # immediately preceding this one - and answering it would reply to something a
    # staff member was meant to deal with.
    await engine.dispose()
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        anthropic_client = fake_anthropic_client(["Visiting hours are 8am to 5pm."])
        mock_anthropic_cls.return_value = anthropic_client
        with (
            capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
            TestClient(app),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as http:
                adopt_seeded_session(http)
                chat_id = await async_chat_id_for(http)
                silenced = await _silenced_message(
                    seeded_session_id(),
                    chat_id,
                    "is anyone there? my appointment is wrong",
                )
                await async_turn(http, "when can I visit?")
                history = (await http.get(f"/chats/{chat_id}/messages")).json()[
                    "messages"
                ]

    received = next(e for e in logs if e["event"] == "turn.message_received")
    answered = [m for m in history if m["sender"] == "assistant"]

    # The turn answers one message, and it is not the silenced one.
    assert len(received["message_ids_unified"]) == 1
    assert silenced not in received["message_ids_unified"]
    assert len(answered) == 1

    # And what the FAQ path was asked is that one message alone - while the silenced
    # message is still in front of the model, which is the other half of FR-019a: it
    # remains part of the conversation read for context, and is never the question.
    sent = anthropic_client.messages.stream.call_args.kwargs["messages"]
    prompt = str(sent[-1]["content"])
    assert "when can I visit?" in prompt
    assert "my appointment is wrong" in prompt
    assert "my appointment is wrong" not in prompt.split("Question:")[-1]
    assert "do not answer them" in prompt


async def test_the_silenced_message_stays_in_the_thread(seeded_entry: int) -> None:
    # It is kept, not dropped: it is part of the conversation, and a staff member
    # reading the thread has to see what the patient said while nobody answered.
    await engine.dispose()
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(["Visiting hours."])
        with TestClient(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as http:
                adopt_seeded_session(http)
                chat_id = await async_chat_id_for(http)
                silenced = await _silenced_message(
                    seeded_session_id(), chat_id, "is anyone there?"
                )
                await async_turn(http, "when can I visit?")
                history = (await http.get(f"/chats/{chat_id}/messages")).json()[
                    "messages"
                ]

    kept = next(m for m in history if m["id"] == silenced)
    assert kept["content"] == "is anyone there?"
    # Still marked: only a staff message clears it, and none was posted.
    assert kept["attention_mark"] == "unanswered"


# --- a release that frees nothing ---------------------------------------------------
#
# A turn holds the chat's lock twice, and both sections commit before they let it go:
# the patient's message is inserted under the first, the reply and the transition under
# the second. A release reporting it held nothing is a serious fault - the chat it keys
# can never be locked again - but it is not a reason to fail a turn whose writes are
# already durable. Failing the first would report a stored message as a failed send and
# invite the patient to type it again; failing the second would break the stream after
# the reply had already been generated and stored.


def _failing_release(*, on_call: int) -> object:
    """Return a `release_chat_lock` that really releases, then reports it did not.

    Args:
        on_call: Which of the turn's two releases fails - 1 is the patient message's
            section, 2 is `_persist_outcome`'s.

    The real release still runs, so the advisory lock does not strand and the rest of
    the turn can take it; only the answer the caller gets is under test.
    """
    real_release = chat_repository.release_chat_lock
    calls = 0

    async def release(session: object, chat_id: str) -> None:
        nonlocal calls
        calls += 1
        await real_release(session, chat_id)  # type: ignore[arg-type]
        if calls == on_call:
            raise chat_repository.ChatLockNotHeldError("held nothing")

    return release


@pytest.mark.parametrize("on_call", [1, 2])
def test_a_failed_lock_release_does_not_fail_a_committed_turn(
    seeded_entry: int, on_call: int
) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
        patch.object(
            chat_repository, "release_chat_lock", _failing_release(on_call=on_call)
        ),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(
            ["Visiting hours are 8am to 5pm."]
        )
        with TestClient(app) as client:
            chat_id = chat_id_for(client)
            response = turn(client, "when can I visit?")

    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert lines[-1]["type"] == "done"
    assert any(c["entry_id"] == seeded_entry for c in lines[-1]["citations"])

    failed = [e for e in logs if e["event"] == "chat.lock_release_failed"]
    assert len(failed) == 1
    assert failed[0]["log_level"] == "error"
    assert failed[0]["chat_id"] == chat_id
    assert "held nothing" in failed[0]["error_detail"]


@pytest.mark.parametrize("on_call", [1, 2])
def test_a_failed_lock_release_leaves_the_turn_s_writes_in_the_thread(
    seeded_entry: int, on_call: int
) -> None:
    # The half that matters to the patient: the message they sent and the answer they
    # were shown are both in the thread afterwards, so neither is retyped and neither
    # reappears from a reload as something they never saw.
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
        patch.object(
            chat_repository, "release_chat_lock", _failing_release(on_call=on_call)
        ),
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(
            ["Visiting hours are 8am to 5pm."]
        )
        with TestClient(app) as client:
            chat_id = chat_id_for(client)
            turn(client, "when can I visit?")
            history = client.get(f"/chats/{chat_id}/messages").json()["messages"]

    assert [m["sender"] for m in history] == ["patient", "assistant"]
    assert history[0]["content"] == "when can I visit?"
    assert history[1]["citations"]


# --- how a turn ends ----------------------------------------------------------------
#
# One guarantee, three ways of breaking it. A turn's reply reaches the patient only once
# the row holding it has committed, and every turn ends in exactly one terminal line -
# `done`, `cancelled` or `silent` - or in a broken stream. A stream that simply stops is
# none of those: the client saw no ending, so the turn stays in progress on the
# patient's screen for as long as they stay in the conversation.


_STUB_REPLY = "Visiting hours are 8am to 5pm."


class _StubGraph:
    """A `run_turn` stand-in yielding exactly the events it was built with.

    Records `reason` into the turn's own collector first when given - the same object
    the specialists fill, so the escalation writes that follow are the real ones.
    """

    def __init__(self, *events: object, reason: EscalationReason | None = None) -> None:
        self._events = events
        self._reason = reason

    async def __call__(self, *args: object, **kwargs: object) -> AsyncIterator[object]:
        escalation = kwargs.get("escalation")
        if self._reason is not None and isinstance(escalation, EscalationRequests):
            escalation.record(self._reason)
        for event in self._events:
            yield event


async def _write_that_fails(*args: object, **kwargs: object) -> None:
    """Stand in for a write the store could not complete."""
    raise RuntimeError("connection reset by peer")


def test_an_escalation_write_that_fails_does_not_cost_the_patient_the_reply() -> None:
    # The reply commits first and the escalation writes follow it under the same lock,
    # so a failure in the second must not swallow the first. A broken stream here puts
    # the question back in the composer under an error banner - inviting the patient to
    # ask again something that is already answered in the thread.
    with (
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
        patch(
            "chat.api.turn.run_turn",
            _StubGraph(
                ChatTokenEvent(text=_STUB_REPLY),
                ChatDoneEvent(grounded=True, citations=[]),
                reason=EscalationReason.CORPUS_COULD_NOT_ANSWER,
            ),
        ),
        patch.object(chat_repository, "set_attention_mark", _write_that_fails),
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client()
        with TestClient(app) as client:
            chat_id = chat_id_for(client)
            response = turn(client, "when can I visit?")
            history = client.get(f"/chats/{chat_id}/messages").json()["messages"]

    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert lines[-1]["type"] == "done"
    # The other half: the reply the patient was shown is the one the thread holds.
    assert [m["sender"] for m in history] == ["patient", "assistant"]


def test_a_store_failure_in_the_writes_is_not_recorded_as_a_broken_pipeline() -> None:
    # The same failure, read from the log rather than from the stream. The writes run
    # under the pipeline's own catch-all, so a store that dropped the connection during
    # them was recorded as `pipeline_step="unknown"` - indistinguishable from a graph
    # node blowing up, and pointing an operator at the pipeline instead of the store.
    # It is also the only record there is: the reply has already been streamed, so the
    # turn returns normally and nothing else in the run says a write failed.
    with (
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
        patch(
            "chat.api.turn.run_turn",
            _StubGraph(
                ChatTokenEvent(text=_STUB_REPLY),
                ChatDoneEvent(grounded=True, citations=[]),
                reason=EscalationReason.CORPUS_COULD_NOT_ANSWER,
            ),
        ),
        patch.object(chat_repository, "set_attention_mark", _write_that_fails),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client()
        with TestClient(app) as client:
            turn(client, "when can I visit?")

    errors = [e for e in logs if e["event"] == "turn.error"]
    assert len(errors) == 1
    assert errors[0]["pipeline_step"] == "persistence"
    assert "connection reset by peer" in errors[0]["error_detail"]
    # And not escalated to an outage: a write the store refused is not the store being
    # unreachable, and only the steps the turn's answer depends on claim that.
    assert not any(e["event"] == "critical.dependency_unreachable" for e in logs)


def test_a_turn_that_settles_no_reply_still_tells_the_patient_it_ended() -> None:
    # A pipeline that completes without a `done` settles no reply, and used to queue no
    # terminal event either: the stream ended cleanly, no error fired, and the
    # in-progress bubble stayed on screen until the patient switched chats.
    with (
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
        patch("chat.api.turn.run_turn", _StubGraph(ChatTokenEvent(text=_STUB_REPLY))),
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client()
        with TestClient(app) as client:
            chat_id = chat_id_for(client)
            response = turn(client, "when can I visit?")
            history = client.get(f"/chats/{chat_id}/messages").json()["messages"]

    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert lines[-1] == {"type": "cancelled"}
    # `cancelled` and not `done`, because it is the true one: nothing was stored.
    assert [m["sender"] for m in history] == ["patient"]


def test_an_event_shape_the_turn_cannot_name_is_dropped_rather_than_fatal() -> None:
    # `run_turn` casts what the graph yields rather than checking it, so a third shape
    # arrives as an assertion nobody made good. Unguarded, it took the whole turn down
    # with an AttributeError - discarding a reply that had generated perfectly well.
    with (
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
        patch(
            "chat.api.turn.run_turn",
            _StubGraph(
                ChatTokenEvent(text=_STUB_REPLY),
                ChatSilentEvent(),
                ChatDoneEvent(grounded=True, citations=[]),
            ),
        ),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client()
        with TestClient(app) as client:
            chat_id = chat_id_for(client)
            response = turn(client, "when can I visit?")
            history = client.get(f"/chats/{chat_id}/messages").json()["messages"]

    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert lines[-1]["type"] == "done"
    assert [m["sender"] for m in history] == ["patient", "assistant"]
    # Dropped, not forwarded: a line the client cannot name is read by its parser as a
    # completed turn, which would end the turn on an empty reply.
    assert not any(line["type"] == "silent" for line in lines)
    unknown = [entry for entry in logs if entry["event"] == "turn.unknown_event"]
    assert len(unknown) == 1
    assert unknown[0]["log_level"] == "error"
    assert unknown[0]["event_type"] == "ChatSilentEvent"


def test_a_turn_that_stored_its_reply_never_re_asks_whether_it_was_taken_over() -> None:
    # The reply's own insert evaluates the takeover guard in its `WHERE`, and the lock
    # it runs under is what holds that answer still - so a second read of the same fact
    # in the same locked section can only agree with the first. Asking twice is not
    # merely wasted: it is two answers where the turn's two writes must act on one.
    reads: list[str] = []
    real_get_takeover_since = chat_repository.get_takeover_since

    async def counting_get_takeover_since(
        session: AsyncSession, chat_id: str, session_id: str, message_id: str
    ) -> chat_repository.TakeoverRead:
        reads.append(message_id)
        return await real_get_takeover_since(session, chat_id, session_id, message_id)

    with (
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
        patch(
            "chat.api.turn.run_turn",
            _StubGraph(
                ChatTokenEvent(text=_STUB_REPLY),
                ChatDoneEvent(grounded=False, citations=[]),
                reason=EscalationReason.CORPUS_COULD_NOT_ANSWER,
            ),
        ),
        patch.object(
            chat_repository, "get_takeover_since", counting_get_takeover_since
        ),
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client()
        with TestClient(app) as client:
            chat_id = chat_id_for(client)
            turn(client, "when can I visit?")
            history = client.get(f"/chats/{chat_id}/messages").json()["messages"]

    assert reads == []
    # And not because the escalation was skipped: it applied, off the insert's answer.
    assert history[0]["attention_mark"] == AttentionMark.CORPUS_COULD_NOT_ANSWER


# --- a store one version ahead of the turn that translates it ------------------------
#
# `ReplyWrite` is documented as a set that grows, and the turn's translation of it into
# `ReplyOutcome` sits between the reply's insert committing and the patient being shown
# it. A lookup there that a later member - or a mapping that had lost one - could miss
# turns a reply already in the thread into an error banner over the question it answers.


class _FutureReplyWrite(StrEnum):
    """A `ReplyWrite` answer from a store one version ahead of this build."""

    DECLINED_FOR_A_NEW_REASON = "declined_for_a_new_reason"


def test_a_reply_write_this_build_cannot_name_still_ends_the_turn() -> None:
    # The unmapped member raised `KeyError` inside the locked write, which the turn
    # reported as a broken pipeline: the patient got an error where the truthful ending
    # was `cancelled`. Whatever a later member names, it is not a stored reply, and
    # every non-`STORED` outcome ends a turn the same way.
    async def declining_write(*args: object, **kwargs: object) -> object:
        return _FutureReplyWrite.DECLINED_FOR_A_NEW_REASON

    asked: list[str] = []
    real_get_takeover_since = chat_repository.get_takeover_since

    async def counting_get_takeover_since(
        *args: object, **kwargs: object
    ) -> chat_repository.TakeoverRead:
        asked.append("asked")
        return await real_get_takeover_since(*args, **kwargs)  # type: ignore[arg-type]

    with (
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
        patch(
            "chat.api.turn.run_turn",
            _StubGraph(
                ChatTokenEvent(text=_STUB_REPLY),
                ChatDoneEvent(grounded=True, citations=[]),
            ),
        ),
        patch.object(
            chat_repository, "create_assistant_reply_unless_taken_over", declining_write
        ),
        patch.object(
            chat_repository, "get_takeover_since", counting_get_takeover_since
        ),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client()
        with TestClient(app) as client:
            chat_id = chat_id_for(client)
            response = turn(client, "when can I visit?")
            history = client.get(f"/chats/{chat_id}/messages").json()["messages"]

    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    # Exactly one ending, and the true one - nothing was stored to show.
    assert [line["type"] for line in lines if line["type"] != "token"] == ["cancelled"]
    assert [m["sender"] for m in history] == ["patient"]
    # And the drift is not silent: an answer this build has no outcome for means the
    # translation has fallen behind the store it translates.
    unknown = [entry for entry in logs if entry["event"] == "turn.unknown_reply_write"]
    assert len(unknown) == 1
    assert unknown[0]["log_level"] == "error"
    # An answer this build cannot read says the reply was not stored and nothing else,
    # so whether a person took the conversation is asked rather than assumed - assuming
    # it re-silences a patient against the staff member already handling them.
    assert asked == ["asked"]


def test_a_stored_reply_reaches_the_patient_when_its_outcome_cannot_be_mapped() -> None:
    # The delivery used to hang off the mapping rather than off the write's own answer,
    # so a build whose two enums had drifted committed the reply, raised before
    # `on_stored` could run, and handed the patient an error over a reply that is in
    # their thread - the exact failure the on-commit delivery exists to prevent. An
    # emptied mapping is that drift at its worst; the reply must still arrive.
    with (
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
        patch(
            "chat.api.turn.run_turn",
            _StubGraph(
                ChatTokenEvent(text=_STUB_REPLY),
                ChatDoneEvent(grounded=True, citations=[]),
            ),
        ),
        patch.object(turn_api, "_OUTCOME_BY_REPLY_WRITE", {}),
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client()
        with TestClient(app) as client:
            chat_id = chat_id_for(client)
            response = turn(client, "when can I visit?")
            history = client.get(f"/chats/{chat_id}/messages").json()["messages"]

    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    # One ending, `done`, and the reply the patient was shown is the one stored.
    assert [line["type"] for line in lines if line["type"] != "token"] == ["done"]
    assert [m["sender"] for m in history] == ["patient", "assistant"]
