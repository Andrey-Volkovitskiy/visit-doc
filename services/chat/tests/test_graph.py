"""Tests for `graph.py`'s LangGraph wrapper (research.md #1/#2/#3).

Pins down `classify_intent_node`'s full behavioral contract - multi-label passthrough
(FR-001), catch-all handling (FR-003), and the FR-007 failure-sentinel mapping - and
`answer_faq_node`'s byte-for-byte preservation of `answer_faq()`'s own behavior, before
`graph.py` is implemented (Constitution Principle VIII).
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
import structlog
from chat.agent import graph as graph_module
from chat.agent.tools.registry import ToolContext, ToolRegistry
from chat.agent.tools.scheduling_tools import SCHEDULING_TOOLS
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
    test_turn_api.py's own fixture of the same name - `graph.run_turn()` is called
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


_LOCAL_NOW = datetime(2026, 8, 14, 9, 0)


def _registry(patient_id: str | None = "01PATIENT0000000000000000") -> ToolRegistry:
    """Build a real registry over a channel no test ever dials.

    The mocked booking loop returns plain text unless a test asks for tool calls, so
    the channel stays untouched - but the registry itself is real, so the tool names
    and schemas the model would see are the production ones.

    The registry is also where the turn's patient lives, so a test exercising a chat
    with no patient record varies it here rather than in the graph's own state.
    """
    return ToolRegistry(
        SCHEDULING_TOOLS,
        ToolContext(
            channel=MagicMock(),
            settings=Settings(),
            session_id="01SESSION0000000000000000",
            patient_id=patient_id,
            local_now=_LOCAL_NOW,
        ),
    )


async def _run_turn(
    anthropic_client: MagicMock, message: str, *, patient_id: str | None = "01PATIENT"
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
            patient_name="Ada Lovelace",
            local_now=_LOCAL_NOW,
            registry=_registry(patient_id),
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
    # The routing decision is made and recorded before any specialist starts.
    assert event_names.index("intent.classified") < event_names.index("faq.retrieved")


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
                patient_name="Ada Lovelace",
                local_now=_LOCAL_NOW,
                registry=_registry(),
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


# --- routing and fan-out ------------------------------------------------------


def _node_result(logs: list[dict[str, object]], node: str) -> dict[str, object]:
    """Return the `result` payload of `node`'s completion line."""
    entry = next(
        e for e in logs if e["event"] == "node.completed" and e["node"] == node
    )
    return dict(entry["result"])  # type: ignore[arg-type]


def _started_nodes(logs: list[dict[str, object]]) -> list[str]:
    return [str(e["node"]) for e in logs if e["event"] == "node.started"]


def test_a_booking_only_intent_launches_the_booking_specialist_alone(
    seeded_entry: int,
) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(intents=[IntentLabel.BOOKING])
        events = asyncio.run(_run_turn(anthropic_client, "book me Tuesday at 9"))

    assert _started_nodes(logs) == [
        "classify_intent",
        "handle_booking",
        "compose_answer",
    ]
    routing = _node_result(logs, "classify_intent")
    assert routing["specialists"] == ["handle_booking"]
    assert routing["merge_required"] is False
    done_event = events[-1]
    assert isinstance(done_event, ChatDoneEvent)
    assert done_event.answer_source == "booking"
    # A booking reply was never retrieved against, so it is neither grounded nor
    # abstaining - and it carries no citations.
    assert done_event.grounded is None
    assert done_event.citations == []


def test_a_faq_only_intent_launches_the_faq_specialist_alone(
    seeded_entry: int,
) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(["Visiting hours are 8am to 5pm."])
        events = asyncio.run(_run_turn(anthropic_client, "when can I visit?"))

    assert _started_nodes(logs) == ["classify_intent", "answer_faq", "compose_answer"]
    assert _node_result(logs, "compose_answer")["merged"] is False
    done_event = events[-1]
    assert isinstance(done_event, ChatDoneEvent)
    assert done_event.answer_source == "faq"


def test_both_intents_fan_out_concurrently_and_merge(seeded_entry: int) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(
            ["Visiting hours are 8am to 5pm."],
            intents=[IntentLabel.FAQ_QUESTION, IntentLabel.BOOKING],
        )
        events = asyncio.run(
            _run_turn(anthropic_client, "when can I visit, and can I book Friday?")
        )

    assert set(_started_nodes(logs)) == {
        "classify_intent",
        "answer_faq",
        "handle_booking",
        "compose_answer",
    }
    routing = _node_result(logs, "classify_intent")
    assert routing["specialists"] == ["answer_faq", "handle_booking"]
    assert routing["merge_required"] is True
    # Both specialists collect rather than stream, so the composing step owns the reply.
    assert _node_result(logs, "answer_faq")["mode"] == "collected"
    assert _node_result(logs, "handle_booking")["mode"] == "collected"
    assert _node_result(logs, "compose_answer")["merged"] is True

    done_event = events[-1]
    assert isinstance(done_event, ChatDoneEvent)
    assert done_event.answer_source == "merged"


@pytest.mark.parametrize(
    "intents",
    [
        [IntentLabel.CALL_STAFF],
        [IntentLabel.UNKNOWN],
        [IntentLabel.CLASSIFICATION_FAILED],
    ],
)
def test_an_intent_with_no_specialist_falls_back_to_the_faq_path(
    seeded_entry: int, intents: list[IntentLabel]
) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(["Visiting hours."], intents=intents)
        asyncio.run(_run_turn(anthropic_client, "when can I visit?"))

    assert _node_result(logs, "classify_intent")["specialists"] == ["answer_faq"]


def test_turn_completed_is_emitted_exactly_once_on_the_merged_path(
    seeded_entry: int,
) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(
            ["Visiting hours are 8am to 5pm."],
            intents=[IntentLabel.FAQ_QUESTION, IntentLabel.BOOKING],
        )
        asyncio.run(_run_turn(anthropic_client, "when can I visit and book Friday?"))

    assert [e["event"] for e in logs].count("turn.completed") == 1


@pytest.mark.parametrize("intents", [[IntentLabel.FAQ_QUESTION], [IntentLabel.BOOKING]])
def test_turn_completed_is_emitted_exactly_once_on_a_single_specialist_path(
    seeded_entry: int, intents: list[IntentLabel]
) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(["Visiting hours."], intents=intents)
        asyncio.run(_run_turn(anthropic_client, "when can I visit?"))

    assert [e["event"] for e in logs].count("turn.completed") == 1


def test_every_node_emits_its_lifecycle_pair_with_its_own_name(
    seeded_entry: int,
) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(["Visiting hours."])
        asyncio.run(_run_turn(anthropic_client, "when can I visit?"))

    started = _started_nodes(logs)
    completed = [str(e["node"]) for e in logs if e["event"] == "node.completed"]
    assert started == completed
    assert "node.failed" not in [e["event"] for e in logs]


def test_faq_events_carry_their_own_node_name_under_the_fan_out(
    seeded_entry: int,
) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(
            ["Visiting hours."],
            intents=[IntentLabel.FAQ_QUESTION, IntentLabel.BOOKING],
        )
        asyncio.run(_run_turn(anthropic_client, "when can I visit and book Friday?"))

    retrieved = next(e for e in logs if e["event"] == "faq.retrieved")
    assert retrieved["node"] == "answer_faq"
