import asyncio
import json
from collections.abc import AsyncIterator
from unittest.mock import MagicMock, patch

import pytest
import structlog
from chat.agent import generation_registry
from chat.core.config import Settings
from chat.db.session import engine, session_factory
from chat.domain.models import Chat, MessageSender
from chat.domain.schemas import IntentLabel
from chat.main import app
from chat.rag.indexing import deindex_faq_entry, index_faq_entry
from chat.repositories import chat_repository, faq_repository
from chat.repositories.qdrant_repository import create_client, ensure_collection
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from structlog.testing import capture_logs
from ulid import ULID

from .conftest import (
    FakeAnthropicStream,
    fake_anthropic_client,
    fake_anthropic_client_gated,
    fake_anthropic_client_sequence,
    fake_embed_texts,
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

    async with session_factory() as session:
        entry = await faq_repository.create(session, _ENTRY_CONTENT)

    with patch("chat.rag.indexing.embed_texts", fake_embed_texts):
        # voyage_client is irrelevant here: embed_texts is faked and ignores it.
        await index_faq_entry(qdrant_client, MagicMock(), entry.id, _ENTRY_CONTENT)

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
    await deindex_faq_entry(qdrant_client, entry.id)
    async with session_factory() as session:
        await faq_repository.delete(session, entry.id)
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
            response = client.post("/chat", json={"message": "when can I visit?"})

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
            response = client.post("/chat", json={"message": question})

    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.strip().splitlines()]

    assert len(lines) == 1
    assert lines[0]["type"] == "done"
    assert lines[0]["grounded"] is False
    assert lines[0]["citations"] == []


@pytest.mark.parametrize("message", ["", "a" * 2001])
def test_message_validation_rejects_empty_and_oversized(message: str) -> None:
    with TestClient(app) as client:
        response = client.post("/chat", json={"message": message})

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
            client.post("/chat", json={"message": "when can I visit?"})

    events = {entry["event"]: entry for entry in logs}
    turn_ids = {entry["turn_id"] for entry in logs}
    event_names = [entry["event"] for entry in logs]

    assert turn_ids == {logs[0]["turn_id"]}  # every entry shares one turn_id
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
            client.post("/chat", json={"message": "what is the weather today?"})

    events = {entry["event"]: entry for entry in logs}
    turn_ids = {entry["turn_id"] for entry in logs}
    event_names = [entry["event"] for entry in logs]

    assert turn_ids == {logs[0]["turn_id"]}
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


@pytest.mark.parametrize(
    ("message", "mocked_intents", "expected_intents"),
    [
        (
            "I'd like to book an appointment for next Tuesday",
            [IntentLabel.BOOKING],
            ["booking"],
        ),
        (
            "I need to talk to someone about a billing problem",
            [IntentLabel.CALL_STAFF],
            ["call_staff"],
        ),
        (
            "I need to book something and also ask a policy question",
            [IntentLabel.FAQ_QUESTION, IntentLabel.BOOKING],
            ["faq_question", "booking"],
        ),
        (
            "what's the weather like today?",
            [IntentLabel.UNKNOWN],
            ["unknown"],
        ),
    ],
)
def test_non_faq_messages_get_a_coherent_faq_path_reply_and_correct_intents(
    message: str,
    mocked_intents: list[IntentLabel],
    expected_intents: list[str],
) -> None:
    """FR-001/FR-003/FR-004: every case here has no "visit"/"hours" keyword, so
    `fake_embed_texts` routes it to abstain (docs/testing-strategy.md) - the reply is
    always exactly `_ABSTENTION_MESSAGE`, never a fabricated booking/hand-off
    confirmation, regardless of the mocked classified intent(s) (spec.md Acceptance
    Scenarios US2.1-US2.3, quickstart Scenario 2).
    """
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(intents=mocked_intents)
        with (
            capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
            TestClient(app) as client,
        ):
            response = client.post("/chat", json={"message": message})

    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert len(lines) == 1
    assert lines[0]["type"] == "done"
    assert lines[0]["grounded"] is False
    assert lines[0]["message"] == "I don't have a confident answer to that."

    classified = next(e for e in logs if e["event"] == "intent.classified")
    assert [i.value for i in classified["intents"]] == expected_intents


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
            response = client.post("/chat", json={"message": "when can I visit?"})

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
                        ac.post(
                            "/chat", json={"message": "What are your working hours"}
                        )
                    )
                    await asyncio.wait_for(started.wait(), timeout=5)
                    started.clear()

                    second_message = "actually, can I just book a slot Tuesday?"
                    second_task = asyncio.create_task(
                        ac.post("/chat", json={"message": second_message})
                    )
                    # message 2 reaching its own gated classify call proves
                    # register_and_cancel_previous has already cancelled message 1's
                    # still-suspended task by this point (it's serialized ahead of
                    # this call in api/chat.py) - only then is it safe to release the
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
                        ac.post("/chat", json={"message": "when can I visit?"})
                    )
                    await asyncio.wait_for(started.wait(), timeout=5)
                    started.clear()

                    second_task = asyncio.create_task(
                        ac.post(
                            "/chat", json={"message": "actually, what about Tuesday?"}
                        )
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
            r1 = client.post("/chat", json={"message": "when can I visit?"})
            r2 = client.post(
                "/chat", json={"message": "I'd like to book an appointment"}
            )
            r3 = client.post(
                "/chat", json={"message": "I need to speak to someone about billing"}
            )

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
    transport = ASGITransport(app=asgi_app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        await asyncio.gather(
            ac.post("/chat", json={"message": "when can I visit?"}),
            ac.post("/chat", json={"message": "when can I visit?"}),
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
            client.post("/chat", json={"message": "when can I visit?"})

    events = {entry["event"]: entry for entry in logs}
    turn_ids = {entry["turn_id"] for entry in logs}

    assert turn_ids == {logs[0]["turn_id"]}
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
                response = await ac.post("/chat", json={"message": "when can I visit?"})
                session_id = response.cookies["visitdoc_session_id"]

    async with session_factory() as session:
        session_row = await chat_repository.get_session(session, session_id)
        assert session_row is not None
        chat = await chat_repository.get_or_create_chat_for_session(
            session, session_row.id
        )

    assert chat.id not in generation_registry._in_flight


async def test_concurrent_first_messages_create_only_one_chat() -> None:
    """Regression: two concurrent first messages for a brand-new session must not
    race into two separate `Chat` rows - `_event_stream`'s advisory lock
    (`chat_repository.lock_session`) serializes the chat-creation critical section
    per session_id, so the second request can't read "no chat yet" until the first's
    chat-creation has fully committed.
    """
    async with session_factory() as db_session:
        session_row = await chat_repository.create_session(db_session)

    real_get_chat_for_session = chat_repository.get_chat_for_session
    started = asyncio.Event()
    gate = asyncio.Event()
    call_count = 0

    async def gated_get_chat_for_session(session: object, session_id: str) -> object:
        nonlocal call_count
        call_count += 1
        result = await real_get_chat_for_session(session, session_id)  # type: ignore[arg-type]
        if call_count == 1:
            started.set()
            await gate.wait()
        return result

    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
        patch(
            "chat.repositories.chat_repository.get_chat_for_session",
            side_effect=gated_get_chat_for_session,
        ),
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(["ok"])
        transport = ASGITransport(app=app)
        with TestClient(app):
            async with AsyncClient(transport=transport, base_url="http://t") as ac:
                ac.cookies.set("visitdoc_session_id", session_row.id)
                first_task = asyncio.create_task(
                    ac.post("/chat", json={"message": "first message"})
                )
                await asyncio.wait_for(started.wait(), timeout=5)

                second_task = asyncio.create_task(
                    ac.post("/chat", json={"message": "second message"})
                )
                # Give the second request a real chance to attempt (and, with the
                # lock, block on) its own critical section before releasing the
                # first - proves genuine concurrency, not lucky ordering.
                await asyncio.sleep(0.2)
                gate.set()

                await asyncio.wait_for(
                    asyncio.gather(first_task, second_task), timeout=5
                )

    async with session_factory() as db_session:
        result = await db_session.execute(
            select(Chat).where(Chat.session_id == session_row.id)
        )
        chats = result.scalars().all()

    assert len(chats) == 1


async def test_concurrent_messages_on_existing_chat_both_reach_history(
    seeded_entry: int,
) -> None:
    """Regression: two concurrent messages on the same existing chat must not race -
    the second's history read must never miss the first's not-yet-committed message
    (research.md #5/#6) - the advisory lock means a second request can't read history
    until the first's message insert has fully committed.
    """
    async with session_factory() as db_session:
        session_row = await chat_repository.create_session(db_session)
        await chat_repository.get_or_create_chat_for_session(db_session, session_row.id)

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
                ac.cookies.set("visitdoc_session_id", session_row.id)
                # Doesn't itself carry any FAQ-matching signal - abstains, never
                # reaches Claude (see fake_embed_texts).
                first_task = asyncio.create_task(
                    ac.post(
                        "/chat",
                        json={"message": "I need help with something else first"},
                    )
                )
                await asyncio.wait_for(started.wait(), timeout=5)

                # Grounds against `seeded_entry`'s FAQ content, so this is the only
                # message expected to reach Claude.
                second_task = asyncio.create_task(
                    ac.post("/chat", json={"message": "when can I visit?"})
                )
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
        by_turn.setdefault(entry.get("turn_id"), []).append(entry)

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
        message = {"message": "when can I visit?"}
        with TestClient(app) as client:
            first_response = client.post("/chat", json=message)
            second_response = client.post("/chat", json=message)

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    mock_anthropic_cls.assert_called_once()
    mock_voyage_cls.assert_called_once()


def test_session_cookie_issued_on_first_message_and_reused_thereafter(
    seeded_entry: int,
) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(["ok"])
        with TestClient(app) as client:
            first = client.post("/chat", json={"message": "when can I visit?"})
            assert "visitdoc_session_id" in first.cookies
            session_id = first.cookies["visitdoc_session_id"]

            second = client.post("/chat", json={"message": "when can I visit?"})

    assert "set-cookie" not in second.headers
    assert second.cookies.get("visitdoc_session_id", session_id) == session_id


def test_followup_reply_uses_earlier_message_as_history(seeded_entry: int) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        anthropic_client = fake_anthropic_client(["Tuesday hours are 8am to 5pm."])
        mock_anthropic_cls.return_value = anthropic_client
        with TestClient(app) as client:
            # Doesn't itself carry any FAQ-matching signal - abstains, but is still
            # persisted and available as context for the next turn (FR-003).
            client.post("/chat", json={"message": "I'm going to come on Tuesday"})
            client.post(
                "/chat", json={"message": "what are your working hours that day?"}
            )

    calls = anthropic_client.messages.stream.call_args_list
    assert len(calls) == 1  # message 1 abstained - never reached Claude
    messages_sent = calls[0].kwargs["messages"]
    assert any(
        m["role"] == "user" and "Tuesday" in m["content"] for m in messages_sent[:-1]
    )


def test_followup_still_abstains_when_neither_message_is_grounded(
    seeded_entry: int,
) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client()
        with TestClient(app) as client:
            client.post("/chat", json={"message": "hello"})
            response = client.post("/chat", json={"message": "how are you"})

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
    async with session_factory() as db_session:
        session_row = await chat_repository.create_session(db_session)

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
                ac.cookies.set("visitdoc_session_id", session_row.id)
                first_task = asyncio.create_task(
                    ac.post("/chat", json={"message": "What are your working hours"})
                )
                await asyncio.wait_for(started.wait(), timeout=5)
                started.clear()

                second_task = asyncio.create_task(
                    ac.post("/chat", json={"message": "on Tuesdays specifically?"})
                )
                # message 2 has now also reached its gated call
                await asyncio.wait_for(started.wait(), timeout=5)
                gate.set()

                first_response, second_response = await asyncio.wait_for(
                    asyncio.gather(first_task, second_task), timeout=5
                )

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
        chat = await chat_repository.get_or_create_chat_for_session(
            db_session, session_row.id
        )
        messages = await chat_repository.list_messages(db_session, chat.id)

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
                failed_response = await ac.post(
                    "/chat", json={"message": "What are your working hours on Tuesday"}
                )
                session_id = failed_response.cookies["visitdoc_session_id"]

                # Reconfigure the same mocked client to succeed for the next call.
                anthropic_client.messages.stream.side_effect = None
                anthropic_client.messages.stream.return_value = FakeAnthropicStream(
                    ["Tuesday hours are 8am to 5pm."]
                )
                # `ac`'s own cookie jar already carries the session cookie from the
                # first response.
                await ac.post(
                    "/chat",
                    json={"message": "what are your working hours that day?"},
                )

    calls = anthropic_client.messages.stream.call_args_list
    assert len(calls) == 2  # first raised inside the call itself, still "called"
    second_call_messages = calls[1].kwargs["messages"]
    # Message 1 never got a reply, so it's part of the same unanswered trailing run
    # as message 2 and gets merged into the final turn (research.md #5), not kept as
    # a separate prior entry - either way, its content still reached Claude (FR-012).
    assert any("Tuesday" in m["content"] for m in second_call_messages)

    async with session_factory() as session:
        session_row = await chat_repository.get_session(session, session_id)
        assert session_row is not None
        chat = await chat_repository.get_or_create_chat_for_session(
            session, session_row.id
        )
        messages = await chat_repository.list_messages(session, chat.id)

    assert [m.sender for m in messages] == ["patient", "patient", "assistant"]


def test_get_chat_history_empty_without_cookie() -> None:
    with TestClient(app) as client:
        response = client.get("/chat")
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
            client.post("/chat", json={"message": "when can I visit?"})
            history_response = client.get("/chat")

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
            client.post("/chat", json={"message": "what is the weather today?"})
            history_response = client.get("/chat")

    messages = history_response.json()["messages"]
    assert messages[1]["grounded"] is False
    assert messages[1]["citations"] == []
    assert messages[1]["content"] == "I don't have a confident answer to that."


def test_get_chat_history_persists_across_simulated_reload(seeded_entry: int) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(["ok"])
        with TestClient(app) as client:
            client.post("/chat", json={"message": "when can I visit?"})
            # Simulated reload: a later GET on the same cookie jar sees the same data.
            first_load = client.get("/chat").json()
            second_load = client.get("/chat").json()

    assert first_load == second_load
    assert len(first_load["messages"]) == 2


async def test_get_chat_history_shows_burst_without_forced_alternation() -> None:
    async with session_factory() as db_session:
        session_row = await chat_repository.create_session(db_session)
        chat = await chat_repository.get_or_create_chat_for_session(
            db_session, session_row.id
        )
        await chat_repository.create_message(
            db_session,
            id=str(ULID()),
            chat_id=chat.id,
            sender=MessageSender.PATIENT,
            content="When can I see",
        )
        await chat_repository.create_message(
            db_session,
            id=str(ULID()),
            chat_id=chat.id,
            sender=MessageSender.PATIENT,
            content="Dr. Josh?",
        )
        await chat_repository.create_message(
            db_session,
            id=str(ULID()),
            chat_id=chat.id,
            sender=MessageSender.ASSISTANT,
            content="Dr. Josh is available Tuesdays.",
            grounded=True,
            citations=[],
        )

    transport = ASGITransport(app=app)
    with TestClient(app):
        async with AsyncClient(transport=transport, base_url="http://t") as ac:
            ac.cookies.set("visitdoc_session_id", session_row.id)
            response = await ac.get("/chat")

    messages = response.json()["messages"]
    assert [m["sender"] for m in messages] == ["patient", "patient", "assistant"]
    assert messages[0]["content"] == "When can I see"
    assert messages[1]["content"] == "Dr. Josh?"
    assert messages[2]["content"] == "Dr. Josh is available Tuesdays."


def test_delete_chat_hard_deletes_messages_and_leaves_session_cookie_untouched(
    seeded_entry: int,
) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client(["ok"])
        with TestClient(app) as client:
            post_response = client.post("/chat", json={"message": "when can I visit?"})
            session_id = post_response.cookies["visitdoc_session_id"]

            delete_response = client.delete("/chat")
            assert delete_response.status_code == 204
            assert "set-cookie" not in delete_response.headers
            assert client.cookies["visitdoc_session_id"] == session_id

            history_response = client.get("/chat")

    assert history_response.json() == {"messages": []}


def test_delete_chat_is_noop_when_no_current_chat() -> None:
    with TestClient(app) as client:
        first = client.delete("/chat")
        second = client.delete("/chat")
    assert first.status_code == 204
    assert second.status_code == 204


def test_delete_chat_then_post_starts_fresh_chat_with_no_memory(
    seeded_entry: int,
) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
    ):
        anthropic_client = fake_anthropic_client(["ok"])
        mock_anthropic_cls.return_value = anthropic_client
        with TestClient(app) as client:
            client.post("/chat", json={"message": "What are your hours on Tuesday"})
            delete_response = client.delete("/chat")
            assert delete_response.status_code == 204

            client.post("/chat", json={"message": "what are your working hours"})

    calls = anthropic_client.messages.stream.call_args_list
    assert len(calls) == 2  # both messages ground independently
    second_call_messages = calls[1].kwargs["messages"]
    assert not any("Tuesday" in m["content"] for m in second_call_messages)
