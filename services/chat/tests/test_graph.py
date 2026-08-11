"""Tests for `graph.py`'s LangGraph wrapper (research.md #1/#2/#3).

Pins down `classify_intent_node`'s full behavioral contract - multi-label passthrough
(FR-001), catch-all handling (FR-003), and the FR-007 failure-sentinel mapping - and
`answer_faq_node`'s byte-for-byte preservation of `answer_faq()`'s own behavior, before
`graph.py` is implemented (Constitution Principle VIII).
"""

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import MagicMock, patch

import pytest
import structlog
from chat.agent import graph as graph_module
from chat.core.config import Settings
from chat.db.session import session_factory
from chat.domain.models import Message, MessageSender
from chat.domain.schemas import ChatDoneEvent, ChatTokenEvent, IntentLabel
from chat.rag.indexing import deindex_faq_entry, index_faq_entry
from chat.repositories import faq_repository
from chat.repositories.qdrant_repository import create_client, ensure_collection
from structlog.testing import capture_logs

from .conftest import (
    fake_anthropic_client,
    fake_classify_intent_client,
    fake_embed_texts,
)

_ENTRY_CONTENT = "Visiting hours are 8am to 5pm."


def _patient_message(content: str, id: str) -> Message:
    return Message(sender=MessageSender.PATIENT, content=content, id=id)


@pytest.fixture
async def seeded_entry() -> AsyncIterator[int]:
    """Seed one `FaqEntry`, indexed into Qdrant with fake embeddings (mirrors
    test_chat_api.py's own fixture of the same name - `graph.run_turn()` is called
    directly here, not through `TestClient`, so no separate-event-loop handoff dance
    is needed).
    """
    settings = Settings()
    qdrant_client = create_client(settings)
    await ensure_collection(qdrant_client)

    async with session_factory() as session:
        entry = await faq_repository.create(session, _ENTRY_CONTENT)

    with patch("chat.rag.indexing.embed_texts", fake_embed_texts):
        await index_faq_entry(qdrant_client, MagicMock(), entry.id, _ENTRY_CONTENT)

    yield entry.id

    await deindex_faq_entry(qdrant_client, entry.id)
    async with session_factory() as session:
        await faq_repository.delete(session, entry.id)
    await qdrant_client.close()


async def _run_turn(
    anthropic_client: MagicMock, message: str
) -> list[ChatTokenEvent | ChatDoneEvent]:
    qdrant_client = create_client(Settings())
    bursts = [[_patient_message(message, id="turn-1")]]
    events = [
        event
        async for event in graph_module.run_turn(
            qdrant_client,
            MagicMock(),
            anthropic_client,
            bursts,
            ["turn-1"],
        )
    ]
    await qdrant_client.close()
    return events


def test_grounded_answer_matches_answer_faq_byte_for_byte(seeded_entry: int) -> None:
    with patch("chat.rag.retriever.embed_texts", fake_embed_texts):
        anthropic_client = fake_anthropic_client(["Visiting ", "hours are 8am to 5pm."])
        events = asyncio.run(_run_turn(anthropic_client, "when can I visit?"))

    token_events = [e for e in events if isinstance(e, ChatTokenEvent)]
    done_event = events[-1]
    assert isinstance(done_event, ChatDoneEvent)
    assert "".join(e.text for e in token_events) == "Visiting hours are 8am to 5pm."
    assert done_event.grounded is True
    assert any(c.entry_id == seeded_entry for c in done_event.citations)


def test_abstention_matches_answer_faq_byte_for_byte(seeded_entry: int) -> None:
    with patch("chat.rag.retriever.embed_texts", fake_embed_texts):
        anthropic_client = fake_anthropic_client()
        events = asyncio.run(_run_turn(anthropic_client, "what is the weather today?"))

    assert len(events) == 1
    done_event = events[0]
    assert isinstance(done_event, ChatDoneEvent)
    assert done_event.grounded is False
    assert done_event.citations == []


def test_intent_classified_is_logged_before_any_answer_faq_event(
    seeded_entry: int,
) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(["Visiting hours."])
        asyncio.run(_run_turn(anthropic_client, "when can I visit?"))

    event_names = [entry["event"] for entry in logs]
    assert "intent.classified" in event_names
    assert event_names.index("intent.classified") == 0


def test_multi_label_result_is_passed_through_unchanged(seeded_entry: int) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(
            ["Visiting hours."],
            intents=[IntentLabel.FAQ_QUESTION, IntentLabel.BOOKING],
        )
        asyncio.run(
            _run_turn(anthropic_client, "when can I visit, and can I book Friday?")
        )

    classified = next(e for e in logs if e["event"] == "intent.classified")
    assert classified["intents"] == [IntentLabel.FAQ_QUESTION, IntentLabel.BOOKING]


def test_catch_all_result_is_logged_as_a_normal_classification(
    seeded_entry: int,
) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(
            ["Visiting hours."], intents=[IntentLabel.UNKNOWN]
        )
        asyncio.run(_run_turn(anthropic_client, "what is the weather today?"))

    classified = next(e for e in logs if e["event"] == "intent.classified")
    assert classified["intents"] == [IntentLabel.UNKNOWN]


def test_classification_failure_is_recorded_and_does_not_block_the_faq_reply(
    seeded_entry: int,
) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(
            ["Visiting hours."], classify_error=RuntimeError("boom")
        )
        events = asyncio.run(_run_turn(anthropic_client, "when can I visit?"))

    classified = next(e for e in logs if e["event"] == "intent.classified")
    assert classified["intents"] == [IntentLabel.CLASSIFICATION_FAILED]
    failure_logged = next(
        e for e in logs if e["event"] == "intent.classification_failed"
    )
    assert failure_logged["log_level"] == "error"
    assert failure_logged["error_detail"] == "boom"
    done_event = events[-1]
    assert isinstance(done_event, ChatDoneEvent)
    assert done_event.grounded is True


async def test_cancelling_mid_classification_suppresses_the_log_and_the_faq_reply(
    seeded_entry: int,
) -> None:
    gate = asyncio.Event()
    qdrant_client = create_client(Settings())

    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_classify_intent_client(
            [IntentLabel.FAQ_QUESTION], gate=gate
        )

        bursts = [[_patient_message("when can I visit?", id="turn-1")]]

        async def _collect() -> None:
            async for _ in graph_module.run_turn(
                qdrant_client,
                MagicMock(),
                anthropic_client,
                bursts,
                ["turn-1"],
            ):
                pass

        task = asyncio.create_task(_collect())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    await qdrant_client.close()
    event_names = [entry["event"] for entry in logs]
    assert "intent.classified" not in event_names
