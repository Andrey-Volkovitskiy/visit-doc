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

import httpx
import pytest
import structlog
from anthropic import APITimeoutError, OverloadedError
from chat.agent import graph as graph_module
from chat.agent.escalation import HANDOFF_MESSAGE, EscalationRequests
from chat.agent.history import (
    OPENING_CLINIC_NOTE,
    split_into_bursts,
    to_claude_messages,
)
from chat.agent.tools.registry import ToolContext
from chat.agent.tools.scheduling_tools import SCHEDULING_TOOLS
from chat.core.config import Settings
from chat.db.session import session_factory
from chat.domain.models import (
    AttentionMark,
    EscalationReason,
    Message,
    MessageSender,
)
from chat.domain.schemas import ChatDoneEvent, ChatTokenEvent, FaqVerdict, IntentLabel
from chat.rag.indexing import publish_revision, remove_entry_chunks
from chat.repositories import chat_repository, faq_repository
from chat.repositories.qdrant_repository import create_client, ensure_collection
from structlog.testing import capture_logs
from ulid import ULID

from .conftest import (
    DEFAULT_BOOKING_REPLY,
    fake_anthropic_client,
    fake_classify_intent_client,
    fake_embed_texts,
    seeded_session_id,
    set_seeded_session,
)

_ENTRY_CONTENT = "Visiting hours are 8am to 5pm."


def _patient_message(content: str, id: str) -> Message:
    return Message(sender=MessageSender.PATIENT, content=content, id=id)


# The revisions the seeding fixture published, so `_run_turn` retrieves against the
# same corpus the fixture built rather than an empty one.
_seeded_revisions: list[str] = []
# What a turn with no seeding fixture runs as. Retrieval is scoped to a session as well
# as to revisions, so a turn needs one even when there is nothing for it to find.
_SESSION_WITH_NO_CORPUS = "01SESSIONWITHNOCORPUS00000"


def _retrieving_session() -> str:
    """The session a turn runs as: the seeded corpus's owner, or one owning nothing."""
    return seeded_session_id() if _seeded_revisions else _SESSION_WITH_NO_CORPUS


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

    revision = str(ULID())
    _seeded_revisions.append(revision)
    async with session_factory() as session:
        seeded_session = await chat_repository.create_session(session)
        entry = await faq_repository.create(
            session, seeded_session.id, _ENTRY_CONTENT, revision
        )
    # An entry belongs to exactly one session, so the client has to talk to that one.
    set_seeded_session(seeded_session.id)

    with patch("chat.rag.indexing.embed_texts", fake_embed_texts):
        await publish_revision(
            qdrant_client,
            MagicMock(),
            seeded_session.id,
            entry.id,
            revision,
            _ENTRY_CONTENT,
        )

    yield entry.id

    _seeded_revisions.clear()
    await remove_entry_chunks(qdrant_client, seeded_session.id, entry.id)
    async with session_factory() as session:
        await faq_repository.delete(session, seeded_session.id, entry.id)
    await qdrant_client.close()


_LOCAL_NOW = datetime(2026, 8, 14, 9, 0)


def _tool_context(
    patient_id: str | None = "01PATENT000000000000000000",
    channel: MagicMock | None = None,
) -> ToolContext:
    """Build the turn's ambient facts over a channel no test ever dials.

    The mocked booking loop returns plain text unless a test asks for tool calls, so
    the channel stays untouched - and the registry each node builds over this is the
    production one, so the tool names and schemas the model would see are real.

    This is also where the turn's patient lives, so a test exercising a chat with no
    patient record varies it here rather than in the graph's own state.

    Args:
        channel: the scheduling channel to hand the turn. Passed in by a test that
            asserts nothing dialled it - a mock the test made and did not give the turn
            is untouched whatever the turn does.
    """
    return ToolContext(
        channel=channel if channel is not None else MagicMock(),
        settings=Settings(),
        session_id="01SESS00000000000000000000",
        patient_id=patient_id,
        local_now=_LOCAL_NOW,
    )


async def _run_turn(
    anthropic_client: MagicMock,
    message: str,
    *,
    patient_id: str | None = "01PATIENT",
    live_revisions: list[str] | None = None,
    escalation: EscalationRequests | None = None,
    bursts: list[list[Message]] | None = None,
    channel: MagicMock | None = None,
) -> list[ChatTokenEvent | ChatDoneEvent]:
    qdrant_client = create_client(Settings())
    if bursts is None:
        bursts = [[_patient_message(message, id="turn-1")]]
    if live_revisions is None:
        live_revisions = list(_seeded_revisions)
    events = [
        event
        async for event in graph_module.run_turn(
            qdrant_client,
            MagicMock(),
            MagicMock(),  # reranking client; the boundary is faked in conftest
            anthropic_client,
            bursts,
            ["turn-1"],
            _retrieving_session(),
            live_revisions,
            escalation=escalation if escalation is not None else EscalationRequests(),
            patient_name="Ada Lovelace",
            local_now=_LOCAL_NOW,
            tool_context=_tool_context(patient_id, channel),
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
    assert done_event.faq_verdict is FaqVerdict.ANSWERED
    assert any(c.entry_id == seeded_entry for c in done_event.citations)


def test_abstention_matches_answer_faq_byte_for_byte(seeded_entry: int) -> None:
    with patch("chat.rag.retriever.embed_texts", fake_embed_texts):
        anthropic_client = fake_anthropic_client()
        events = asyncio.run(_run_turn(anthropic_client, "what is the weather today?"))

    assert len(events) == 1
    done_event = events[0]
    assert isinstance(done_event, ChatDoneEvent)
    assert done_event.faq_verdict is FaqVerdict.ABSTAINED_SIMILARITY_FLOOR
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
    assert event_names.index("intent.classified") < event_names.index(
        "faq.retrieval_completed"
    )


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
    assert done_event.faq_verdict is FaqVerdict.ANSWERED


def test_a_model_outage_during_classification_raises_the_dependency_alert(
    seeded_entry: int,
) -> None:
    # The classification call cannot be tagged `generation` - a failed classification
    # does not fail the turn, so no `TurnPipelineError` is raised and `turn.error` never
    # fires for it. Without an alert raised here, an unreachable model API is invisible
    # for the whole turn whenever the FAQ path abstains before generating, which is
    # exactly the turn a corpus that cannot answer produces: the classification call is
    # then the only model call the turn makes.
    #
    # A 529 specifically, because that is how an Anthropic outage most often arrives and
    # because the SDK gives it its own `OverloadedError` that does *not* subclass
    # `InternalServerError` - a check written as a tuple of exception classes misses it.
    outage = OverloadedError(
        "overloaded",
        response=httpx.Response(
            529, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        ),
        body=None,
    )
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(
            ["Visiting hours."], classify_error=outage
        )
        events = asyncio.run(_run_turn(anthropic_client, "when can I visit?"))

    failure_logged = next(
        e for e in logs if e["event"] == "intent.classification_failed"
    )
    assert failure_logged["dependency_unreachable"] is True
    alerts = [
        e
        for e in logs
        if e["event"] == "critical.dependency_unreachable"
        and e["dependency"] == "anthropic_api"
    ]
    assert len(alerts) == 1
    assert alerts[0]["log_level"] == "critical"
    # And the turn is untouched by it: still the FAQ fallback, still an answer.
    classified = next(e for e in logs if e["event"] == "intent.classified")
    assert classified["intents"] == [IntentLabel.CLASSIFICATION_FAILED]
    done_event = events[-1]
    assert isinstance(done_event, ChatDoneEvent)
    assert done_event.faq_verdict is FaqVerdict.ANSWERED


def test_a_classification_answer_that_would_not_parse_raises_no_alert(
    seeded_entry: int,
) -> None:
    # The other half of the rule, and the reason it is a rule rather than "alert on any
    # classification failure": a response that came back and would not validate is the
    # API reachable and answering. So is a refused key and an enforced quota. An alert
    # that fires for those is one an operator learns to ignore, which costs the alert
    # its only purpose.
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(
            ["Visiting hours."], classify_error=ValueError("not json")
        )
        events = asyncio.run(_run_turn(anthropic_client, "when can I visit?"))

    failure_logged = next(
        e for e in logs if e["event"] == "intent.classification_failed"
    )
    assert failure_logged["dependency_unreachable"] is False
    assert not [e for e in logs if e["event"] == "critical.dependency_unreachable"]
    # Still recorded, and still a fallback rather than a failed turn.
    assert failure_logged["log_level"] == "error"
    done_event = events[-1]
    assert isinstance(done_event, ChatDoneEvent)
    assert done_event.faq_verdict is FaqVerdict.ANSWERED


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
                MagicMock(),  # reranking client; the boundary is faked in conftest
                anthropic_client,
                bursts,
                ["turn-1"],
                seeded_session_id(),
                list(_seeded_revisions),
                escalation=EscalationRequests(),
                patient_name="Ada Lovelace",
                local_now=_LOCAL_NOW,
                tool_context=_tool_context(),
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
    assert done_event.faq_verdict is None
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


def test_each_node_completion_carries_the_text_that_node_returned(
    seeded_entry: int,
) -> None:
    """A merged turn is the case that needs this: `turn.completed` carries only the
    composed reply, so the two halves being merged appear in no other record."""
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(
            [_ENTRY_CONTENT],
            intents=[IntentLabel.FAQ_QUESTION, IntentLabel.BOOKING],
        )
        events = asyncio.run(
            _run_turn(anthropic_client, "when can I visit, and can I book Friday?")
        )

    assert _node_result(logs, "answer_faq")["answer_text"] == _ENTRY_CONTENT
    assert _node_result(logs, "handle_booking")["answer_text"] == DEFAULT_BOOKING_REPLY
    # The composing node's text is what it actually put on the wire, not a second copy
    # of it assembled elsewhere - which is the whole point of logging it here.
    composed = "".join(e.text for e in events if isinstance(e, ChatTokenEvent))
    assert composed != ""
    assert _node_result(logs, "compose_answer")["answer_text"] == composed


def test_a_single_specialist_turn_logs_its_text_on_the_node_that_produced_it(
    seeded_entry: int,
) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client([_ENTRY_CONTENT])
        asyncio.run(_run_turn(anthropic_client, "when can I visit?"))

    faq = _node_result(logs, "answer_faq")
    assert faq["answer_text"] == _ENTRY_CONTENT
    assert faq["answer_chars"] == len(_ENTRY_CONTENT)
    # The composing node passes a lone specialist's reply through untouched, so it
    # reports the same string rather than nothing at all.
    assert _node_result(logs, "compose_answer")["answer_text"] == _ENTRY_CONTENT


@pytest.mark.parametrize(
    "intents",
    [
        [IntentLabel.CLASSIFICATION_FAILED],
    ],
)
def test_an_intent_with_no_specialist_falls_back_to_the_faq_path(
    seeded_entry: int, intents: list[IntentLabel]
) -> None:
    """A failure is not evidence about what the message was, so the corpus is still
    the cheapest guess that can produce the right answer. `unknown` no longer belongs
    here: spec 009 gives it a route of its own (FR-022)."""
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


@pytest.mark.parametrize(
    "intents",
    [
        [IntentLabel.FAQ_QUESTION],
        [IntentLabel.BOOKING],
        [IntentLabel.FAQ_QUESTION, IntentLabel.BOOKING],
    ],
)
def test_turn_completed_is_the_last_line_after_every_node_has_closed(
    seeded_entry: int, intents: list[IntentLabel]
) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(["Visiting hours."], intents=intents)
        asyncio.run(_run_turn(anthropic_client, "when can I visit and book Friday?"))

    events = [e["event"] for e in logs]
    assert events.index("turn.completed") > _last_index(events, "node.completed")
    # A turn-level event, so it must not inherit the composing node's binding either.
    assert "node" not in next(e for e in logs if e["event"] == "turn.completed")


def _last_index(events: list[str], event: str) -> int:
    return len(events) - 1 - events[::-1].index(event)


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

    retrieved = next(e for e in logs if e["event"] == "faq.retrieval_completed")
    assert retrieved["node"] == "answer_faq"


# --- 007: `call_staff` is a decision, not a question for a model ------------------


def test_call_staff_takes_the_whole_turn(seeded_entry: int) -> None:
    # The only outcome is the handoff. Nothing is retrieved and nothing is generated,
    # even for a question this corpus could have answered - a visitor who has asked for
    # a person is going to get one, and the conversation falls silent from their next
    # message, so answering half of what they said and then going quiet is worse than
    # handing over cleanly.
    collector = EscalationRequests()
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(
            ["never generated"], intents=[IntentLabel.CALL_STAFF]
        )
        events = asyncio.run(
            _run_turn(anthropic_client, "when can I visit?", escalation=collector)
        )

    assert _started_nodes(logs) == ["classify_intent", "hand_off", "compose_answer"]
    assert list(collector.recorded) == [EscalationReason.PATIENT_ASKED_FOR_PERSON]

    token_events = [e for e in events if isinstance(e, ChatTokenEvent)]
    done_event = events[-1]
    assert isinstance(done_event, ChatDoneEvent)
    assert "".join(e.text for e in token_events) == HANDOFF_MESSAGE
    assert done_event.answer_source == "hand_off"
    # Never retrieved against, so neither grounded nor abstaining - and nothing to cite.
    assert done_event.faq_verdict is None
    assert done_event.citations == []


@pytest.mark.parametrize(
    "forbidden",
    ["faq.retrieval_completed", "faq.similarity_gate", "faq.verdict"],
)
def test_a_handed_off_turn_retrieves_nothing(seeded_entry: int, forbidden: str) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(intents=[IntentLabel.CALL_STAFF])
        asyncio.run(_run_turn(anthropic_client, "when can I visit?"))

    assert forbidden not in [entry["event"] for entry in logs]


def test_a_handed_off_turn_costs_one_classification_and_nothing_else(
    seeded_entry: int,
) -> None:
    # The label the classifier already returned is the whole decision, so no second
    # model is asked to agree with it and no generation is paid for.
    with patch("chat.rag.retriever.embed_texts", fake_embed_texts):
        anthropic_client = fake_anthropic_client(
            ["never generated"], intents=[IntentLabel.CALL_STAFF]
        )
        asyncio.run(_run_turn(anthropic_client, "can I speak to someone?"))

    assert anthropic_client.messages.create.await_count == 1
    assert anthropic_client.messages.stream.call_count == 0


def test_call_staff_suppresses_a_booking_on_the_same_message(
    seeded_entry: int,
) -> None:
    # "book me Friday and have someone call me" books nothing. The accepted cost of
    # the rule above, and the safer half of it: writing an appointment for a patient
    # who has just asked to stop talking to a machine is the harder thing to undo.
    with patch("chat.rag.retriever.embed_texts", fake_embed_texts):
        anthropic_client = fake_anthropic_client(
            intents=[IntentLabel.CALL_STAFF, IntentLabel.BOOKING]
        )
        events = asyncio.run(
            _run_turn(anthropic_client, "book me Friday, and have someone call me")
        )

    # No tool-bearing model call was made at all, so nothing was booked.
    assert not [
        call
        for call in anthropic_client.messages.create.await_args_list
        if call.kwargs.get("tools")
    ]
    done_event = events[-1]
    assert isinstance(done_event, ChatDoneEvent)
    assert done_event.answer_source == "hand_off"


def test_a_handed_off_turn_is_reported_as_its_own_outcome(
    seeded_entry: int,
) -> None:
    # Not "booking", which is what reading the outcome off an absent groundedness
    # verdict would have filed every handoff as.
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(intents=[IntentLabel.CALL_STAFF])
        asyncio.run(_run_turn(anthropic_client, "can I speak to someone?"))

    completed = next(e for e in logs if e["event"] == "turn.completed")
    assert completed["outcome"] == "handed_off"
    assert completed["answer_source"] == "hand_off"
    assert completed["answer_text"] == HANDOFF_MESSAGE
    # The node that wrote the sentence reports it too, like every other node that
    # returns text - a handoff is not the one path a log reader has to infer.
    assert _node_result(logs, "hand_off")["answer_text"] == HANDOFF_MESSAGE


@pytest.mark.parametrize(
    "intents", [[IntentLabel.FAQ_QUESTION], [IntentLabel.SMALL_TALK]]
)
def test_a_turn_nobody_asked_for_a_person_in_records_nothing(
    seeded_entry: int, intents: list[IntentLabel]
) -> None:
    collector = EscalationRequests()
    with patch("chat.rag.retriever.embed_texts", fake_embed_texts):
        anthropic_client = fake_anthropic_client(["Visiting hours."], intents=intents)
        asyncio.run(
            _run_turn(anthropic_client, "when can I visit?", escalation=collector)
        )

    assert list(collector.recorded) == []


# --- 007: each node reaches for its own tools, not the system's -------------------


def _tools_offered(client: MagicMock) -> set[str]:
    """Return the tool names the booking loop's model call actually carried."""
    offered = [
        call
        for call in client.messages.create.await_args_list
        if call.kwargs.get("tools")
    ]
    assert offered, "the booking loop made no tool-bearing model call"
    return {tool["name"] for tool in offered[0].kwargs["tools"]}


def test_the_booking_node_is_offered_the_tools_it_declares(seeded_entry: int) -> None:
    # Its own set, built at the node: the scheduling capabilities, plus the one for a
    # patient who asks for a person in the middle of booking with one.
    with patch("chat.rag.retriever.embed_texts", fake_embed_texts):
        anthropic_client = fake_anthropic_client(intents=[IntentLabel.BOOKING])
        asyncio.run(_run_turn(anthropic_client, "book me Tuesday at 9"))

    assert _tools_offered(anthropic_client) == {
        tool.name for tool in SCHEDULING_TOOLS
    } | {"escalate_to_staff"}


def test_the_faq_node_is_offered_no_tools_at_all(seeded_entry: int) -> None:
    # It makes no tool calls, so it is handed no capability to make one. A registry
    # shared by the whole graph would have offered it every tool in the system.
    with patch("chat.rag.retriever.embed_texts", fake_embed_texts):
        anthropic_client = fake_anthropic_client(["Visiting hours are 8am to 5pm."])
        asyncio.run(_run_turn(anthropic_client, "when can I visit?"))

    assert "tools" not in anthropic_client.messages.stream.call_args.kwargs
    assert not [
        call
        for call in anthropic_client.messages.create.await_args_list
        if call.kwargs.get("tools")
    ]


# --- 007: the clinic's opening words survive the specialist's own prompt ------------
#
# `to_claude_messages` folds a leading clinic-sided burst into the first `user` entry,
# and a specialist that builds a prompt of its own replaces the *trailing* entry - the
# same entry, whenever the whole window renders as one. Asserted here on the specialist
# rather than on the render, because it was the specialists that dropped it.


def test_the_clinics_opening_words_reach_the_faq_specialist(seeded_entry: int) -> None:
    bursts = split_into_bursts(
        [
            Message(
                sender=MessageSender.STAFF,
                content="Dr. Chen has a slot Friday at 3 - shall I book it?",
                id="s1",
            ),
            _patient_message("when can I visit?", id="turn-1"),
        ]
    )
    # One entry, so the entry the FAQ prompt replaces is the folded one.
    assert len(to_claude_messages(bursts)) == 1

    with patch("chat.rag.retriever.embed_texts", fake_embed_texts):
        anthropic_client = fake_anthropic_client(["Visiting hours are 8am to 5pm."])
        asyncio.run(_run_turn(anthropic_client, "when can I visit?", bursts=bursts))

    sent = anthropic_client.messages.stream.call_args.kwargs["messages"]
    content = str(sent[-1]["content"])
    assert "Dr. Chen has a slot Friday at 3 - shall I book it?" in content
    assert OPENING_CLINIC_NOTE in content
    # And the turn's own question is still the question it answers.
    assert content.endswith("Question: when can I visit?")


def test_a_classification_timeout_is_not_reported_as_an_outage(
    seeded_entry: int,
) -> None:
    # A deadline expiring is the caller's fact, not the callee's: it says the answer
    # did not arrive, not that the request went unserved (CLAUDE.md, "a timeout never
    # proves the server did nothing"). `APITimeoutError` subclasses `APIConnectionError`
    # and httpx maps a *pool* timeout - this service's own connection limit - onto it
    # too, so treating it as an outage pages an operator for this side's defect. The
    # scheduling client already suppresses its own `DEADLINE_EXCEEDED` alert this way.
    timeout = APITimeoutError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(
            ["Visiting hours."], classify_error=timeout
        )
        events = asyncio.run(_run_turn(anthropic_client, "when can I visit?"))

    failure_logged = next(
        e for e in logs if e["event"] == "intent.classification_failed"
    )
    # Recorded under its own name, so it is not silently filed as a parse failure.
    assert failure_logged["failure"] == "timed_out"
    assert failure_logged["dependency_unreachable"] is False
    assert [
        e
        for e in logs
        if e["event"] == "critical.dependency_unreachable"
        and e["dependency"] == "anthropic_api"
    ] == []
    # And the turn is untouched by it, exactly as on the outage path.
    done_event = events[-1]
    assert isinstance(done_event, ChatDoneEvent)
    assert done_event.faq_verdict is FaqVerdict.ANSWERED


# --- Phase 1f, US1: a message that asks for nothing ----------------------------------


async def test_small_talk_alone_runs_only_the_small_talk_node() -> None:
    client = fake_anthropic_client(
        ["You're welcome!"], intents=[IntentLabel.SMALL_TALK]
    )

    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        events = await _run_turn(client, "Thanks!")

    assert _started_nodes(logs) == ["classify_intent", "small_talk", "compose_answer"]
    assert (
        "".join(event.text for event in events if isinstance(event, ChatTokenEvent))
        == "You're welcome!"
    )


async def test_a_small_talk_turn_retrieves_nothing() -> None:
    client = fake_anthropic_client(["Hello!"], intents=[IntentLabel.SMALL_TALK])

    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        await _run_turn(client, "Hi")

    events = [entry["event"] for entry in logs]
    assert "faq.retrieval_completed" not in events
    assert "faq.similarity_gate" not in events
    assert "faq.verdict" not in events


async def test_a_small_talk_turn_calls_nobody() -> None:
    escalation = EscalationRequests()
    client = fake_anthropic_client(
        ["Sure, take your time."], intents=[IntentLabel.SMALL_TALK]
    )

    await _run_turn(client, "Let me think a bit", escalation=escalation)

    assert escalation.recorded == ()
    assert escalation.message_mark is None
    assert escalation.conversation_reason is None


async def test_a_small_talk_reply_carries_no_verdict_and_no_citations() -> None:
    client = fake_anthropic_client(
        ["You're welcome!"], intents=[IntentLabel.SMALL_TALK]
    )

    events = await _run_turn(client, "Thanks!")

    done = events[-1]
    assert isinstance(done, ChatDoneEvent)
    assert done.answer_source == "small_talk"
    assert done.faq_verdict is None
    assert done.citations == []


async def test_small_talk_is_answered_even_with_no_corpus_to_answer_from() -> None:
    # An empty corpus is irrelevant to a message that asked nothing of it.
    client = fake_anthropic_client(["Good morning!"], intents=[IntentLabel.SMALL_TALK])

    events = await _run_turn(client, "Good morning", live_revisions=[])

    assert (
        "".join(event.text for event in events if isinstance(event, ChatTokenEvent))
        == "Good morning!"
    )


# --- Phase 1f, US2: a pleasantry wrapped around a request ----------------------------


async def test_small_talk_beside_a_question_runs_the_faq_node_alone(
    seeded_entry: int,
) -> None:
    client = fake_anthropic_client(
        ["Visiting hours are 8am to 5pm."],
        intents=[IntentLabel.SMALL_TALK, IntentLabel.FAQ_QUESTION],
    )

    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        await _run_turn(client, "Hi, when can I visit?")

    assert _started_nodes(logs) == ["classify_intent", "answer_faq", "compose_answer"]
    assert _node_result(logs, "classify_intent")["specialists"] == ["answer_faq"]
    assert _node_result(logs, "classify_intent")["merge_required"] is False


async def test_small_talk_beside_a_booking_runs_the_booking_node_alone() -> None:
    client = fake_anthropic_client(
        ["never generated"],
        intents=[IntentLabel.SMALL_TALK, IntentLabel.BOOKING],
    )

    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        await _run_turn(client, "Thanks! Any slots on Monday?")

    assert _started_nodes(logs) == [
        "classify_intent",
        "handle_booking",
        "compose_answer",
    ]


async def test_small_talk_beside_both_specialists_is_still_dropped(
    seeded_entry: int,
) -> None:
    client = fake_anthropic_client(
        ["Visiting hours are 8am to 5pm."],
        intents=[
            IntentLabel.SMALL_TALK,
            IntentLabel.FAQ_QUESTION,
            IntentLabel.BOOKING,
        ],
    )

    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        await _run_turn(client, "Hi! When can I visit, and can I book Friday?")

    assert _node_result(logs, "classify_intent")["specialists"] == [
        "answer_faq",
        "handle_booking",
    ]
    # Merged because two specialists ran - never because a pleasantry was in the
    # message.
    assert _node_result(logs, "classify_intent")["merge_required"] is True


async def test_a_dropped_pleasantry_leaves_no_trace_in_the_reply(
    seeded_entry: int,
) -> None:
    client = fake_anthropic_client(
        ["Visiting hours are 8am to 5pm."],
        intents=[IntentLabel.SMALL_TALK, IntentLabel.FAQ_QUESTION],
    )

    with patch("chat.rag.retriever.embed_texts", fake_embed_texts):
        events = await _run_turn(client, "Hi, when can I visit?")

    streamed = "".join(e.text for e in events if isinstance(e, ChatTokenEvent))
    assert streamed == "Visiting hours are 8am to 5pm."
    done = events[-1]
    assert isinstance(done, ChatDoneEvent)
    # The servable path's own source, not `merged` and not `small_talk`.
    assert done.answer_source == "faq"


# --- Phase 1f, US5: the three situations that stop the conversation ------------------

_STOPPING = (
    (IntentLabel.URGENT_CONDITION, EscalationReason.URGENT_CONDITION),
    (IntentLabel.DISTRESS, EscalationReason.DISTRESS),
    (IntentLabel.BOOKING_FOR_ANOTHER, EscalationReason.BOOKING_FOR_ANOTHER_PERSON),
)


@pytest.mark.parametrize(("label", "reason"), _STOPPING)
async def test_a_stopping_label_takes_the_whole_turn(
    label: IntentLabel, reason: EscalationReason
) -> None:
    # Alongside a booking, which must not run: answering half a message and then
    # falling silent is worse than handing over cleanly (FR-046).
    client = fake_anthropic_client(
        ["never generated"], intents=[label, IntentLabel.BOOKING]
    )

    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        await _run_turn(client, "my chest hurts, can I see someone today?")

    assert _started_nodes(logs) == ["classify_intent", "hand_off", "compose_answer"]
    events = [entry["event"] for entry in logs]
    assert "faq.retrieval_completed" not in events
    assert "faq.verdict" not in events


@pytest.mark.parametrize(("label", "reason"), _STOPPING)
async def test_a_stopping_label_records_its_own_cause_and_its_own_sentence(
    label: IntentLabel, reason: EscalationReason
) -> None:
    from chat.agent.escalation import HANDOFF_TEXT

    escalation = EscalationRequests()
    client = fake_anthropic_client(["never generated"], intents=[label])

    events = await _run_turn(client, "anything", escalation=escalation)

    assert escalation.recorded == (reason,)
    streamed = "".join(e.text for e in events if isinstance(e, ChatTokenEvent))
    assert streamed == HANDOFF_TEXT[reason]
    assert events[-1].answer_source == "hand_off"


@pytest.mark.parametrize(("label", "reason"), _STOPPING)
async def test_a_stopping_turn_makes_no_generation_call(
    label: IntentLabel, reason: EscalationReason
) -> None:
    client = fake_anthropic_client(["never generated"], intents=[label])

    await _run_turn(client, "anything")

    assert client.messages.stream.call_count == 0


async def test_urgency_outranks_distress_and_a_request_for_a_person() -> None:
    escalation = EscalationRequests()
    client = fake_anthropic_client(
        ["never generated"],
        intents=[
            IntentLabel.DISTRESS,
            IntentLabel.CALL_STAFF,
            IntentLabel.URGENT_CONDITION,
        ],
    )

    await _run_turn(client, "I can't breathe and I'm terrified", escalation=escalation)

    assert escalation.message_mark is AttentionMark.URGENT_CONDITION
    assert escalation.conversation_reason is EscalationReason.URGENT_CONDITION


async def test_urgency_outranks_a_third_party_booking() -> None:
    escalation = EscalationRequests()
    client = fake_anthropic_client(
        ["never generated"],
        intents=[IntentLabel.BOOKING_FOR_ANOTHER, IntentLabel.URGENT_CONDITION],
    )

    await _run_turn(
        client, "I'm booking for my daughter, she can't breathe", escalation=escalation
    )

    assert escalation.conversation_reason is EscalationReason.URGENT_CONDITION


async def test_distress_outranks_a_request_for_a_person() -> None:
    escalation = EscalationRequests()
    client = fake_anthropic_client(
        ["never generated"],
        intents=[IntentLabel.CALL_STAFF, IntentLabel.DISTRESS],
    )

    await _run_turn(client, "please, I'm frightened", escalation=escalation)

    assert escalation.message_mark is AttentionMark.DISTRESS


async def test_a_third_party_booking_issues_no_scheduling_call() -> None:
    # The refusal precedes the service boundary: the scheduler never hears about it.
    # The channel is handed to the turn, not merely made beside it: a mock the turn was
    # never given stays untouched however the turn behaves, and asserts nothing.
    channel = MagicMock()
    client = fake_anthropic_client(
        ["never generated"], intents=[IntentLabel.BOOKING_FOR_ANOTHER]
    )

    with patch.object(graph_module, "ToolRegistry") as registry_cls:
        await _run_turn(client, "Can I book Monday for my mother?", channel=channel)

    assert registry_cls.call_count == 0
    assert channel.method_calls == []


async def test_the_urgent_reply_points_at_emergency_care_only() -> None:
    from chat.agent.escalation import HANDOFF_TEXT

    text = HANDOFF_TEXT[EscalationReason.URGENT_CONDITION].lower()
    assert "emergency" in text
    # It must not judge the condition it cannot see: no reassurance, no severity, no
    # advice on what to do medically beyond seeking emergency care.
    for forbidden in (
        "probably",
        "likely",
        "don't worry",
        "it sounds like",
        "you have",
    ):
        assert forbidden not in text


# --- Phase 1f, US3: both readings of one word are reachable --------------------------


async def test_an_acknowledgement_after_instructions_books_nothing() -> None:
    client = fake_anthropic_client(["See you soon!"], intents=[IntentLabel.SMALL_TALK])
    bursts = [
        [
            Message(
                sender=MessageSender.ASSISTANT,
                content="Please arrive 15 minutes early.",
                id="a1",
            )
        ],
        [_patient_message("ok", id="turn-1")],
    ]

    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        await _run_turn(client, "ok", bursts=bursts)

    assert _started_nodes(logs) == ["classify_intent", "small_talk", "compose_answer"]


async def test_the_same_word_after_a_slot_offer_reaches_the_booking_path() -> None:
    client = fake_anthropic_client(["never generated"], intents=[IntentLabel.BOOKING])
    bursts = [
        [
            Message(
                sender=MessageSender.ASSISTANT,
                content="9am Monday with Dr. Vesalius - shall I book it?",
                id="a1",
            )
        ],
        [_patient_message("ok", id="turn-1")],
    ]

    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        await _run_turn(client, "ok", bursts=bursts)

    assert _started_nodes(logs) == [
        "classify_intent",
        "handle_booking",
        "compose_answer",
    ]


# --- Phase 1f, US4: a request the assistant may not serve ---------------------------


async def test_an_unauthorized_request_retrieves_nothing_and_says_so() -> None:
    from chat.agent.escalation import HANDOFF_TEXT

    escalation = EscalationRequests()
    client = fake_anthropic_client(["never generated"], intents=[IntentLabel.UNKNOWN])

    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        events = await _run_turn(
            client,
            "I would like you to prepare a sick leave paper for my employer",
            escalation=escalation,
        )

    assert _started_nodes(logs) == ["classify_intent", "hand_off", "compose_answer"]
    assert "faq.retrieval_completed" not in [e["event"] for e in logs]
    streamed = "".join(e.text for e in events if isinstance(e, ChatTokenEvent))
    assert streamed == HANDOFF_TEXT[EscalationReason.NOT_AUTHORIZED]
    assert escalation.recorded == (EscalationReason.NOT_AUTHORIZED,)


async def test_an_unauthorized_request_does_not_silence_the_conversation() -> None:
    escalation = EscalationRequests()
    client = fake_anthropic_client(["never generated"], intents=[IntentLabel.UNKNOWN])

    await _run_turn(client, "please renew my prescription", escalation=escalation)

    # A person is needed for this request; the next one may be perfectly ordinary.
    assert escalation.conversation_reason is None
    assert escalation.message_mark is AttentionMark.NOT_AUTHORIZED


async def test_an_unauthorized_request_beside_a_question_answers_and_forwards(
    seeded_entry: int,
) -> None:
    escalation = EscalationRequests()
    client = fake_anthropic_client(
        ["Visiting hours are 8am to 5pm."],
        intents=[IntentLabel.UNKNOWN, IntentLabel.FAQ_QUESTION],
    )

    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        events = await _run_turn(
            client,
            "When can I visit, and can you write me a sick note?",
            escalation=escalation,
        )

    routing = _node_result(logs, "classify_intent")
    assert routing["specialists"] == ["answer_faq"]
    assert routing["notice_required"] is True
    # Merged, because the notice is owed alongside an answer - and no fixed sentence
    # was stapled on: the composer wrote one reply (FR-022c1).
    assert routing["merge_required"] is True
    assert escalation.recorded == (EscalationReason.NOT_AUTHORIZED,)
    streamed = "".join(e.text for e in events if isinstance(e, ChatTokenEvent))
    from chat.agent.escalation import HANDOFF_TEXT

    assert HANDOFF_TEXT[EscalationReason.NOT_AUTHORIZED] not in streamed


async def test_a_classification_failure_reaches_none_of_the_new_routes(
    seeded_entry: int,
) -> None:
    escalation = EscalationRequests()
    client = fake_anthropic_client(
        ["Visiting hours are 8am to 5pm."], classify_error=RuntimeError("down")
    )

    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        await _run_turn(client, "when can I visit?", escalation=escalation)

    assert _node_result(logs, "classify_intent")["specialists"] == ["answer_faq"]
    assert escalation.recorded == ()


# --- Phase 1f, US6: what the turn leaves on the record -------------------------------


async def test_the_routing_record_names_the_stopping_cause(seeded_entry: int) -> None:
    with patch("chat.rag.retriever.embed_texts", fake_embed_texts):
        ordinary = fake_anthropic_client(
            ["Visiting hours are 8am to 5pm."], intents=[IntentLabel.FAQ_QUESTION]
        )
        with capture_logs(
            processors=[structlog.contextvars.merge_contextvars]
        ) as ordinary_logs:
            await _run_turn(ordinary, "when can I visit?")

    stopping = fake_anthropic_client(
        ["never generated"], intents=[IntentLabel.URGENT_CONDITION]
    )
    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        await _run_turn(stopping, "I can't breathe")

    assert _node_result(ordinary_logs, "classify_intent")["stopping_cause"] is None
    assert _node_result(ordinary_logs, "classify_intent")["notice_required"] is False
    assert _node_result(logs, "classify_intent")["stopping_cause"] == "urgent_condition"


async def test_the_hand_off_record_names_which_constant_it_wrote() -> None:
    # Five causes end a turn the same way; without this they leave five
    # indistinguishable node records.
    for label, cause in _STOPPING:
        client = fake_anthropic_client(["never generated"], intents=[label])
        with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
            await _run_turn(client, "anything")
        assert _node_result(logs, "hand_off")["cause"] == cause.value


async def test_a_small_talk_turn_records_its_own_text_and_no_retrieval_fields() -> None:
    client = fake_anthropic_client(
        ["You're welcome!"], intents=[IntentLabel.SMALL_TALK]
    )

    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        await _run_turn(client, "Thanks!")

    node = _node_result(logs, "small_talk")
    assert node["answer_text"] == "You're welcome!"
    assert node["answer_chars"] == len("You're welcome!")
    assert "faq_verdict" not in node
    assert "citation_count" not in node

    completed = next(e for e in logs if e["event"] == "turn.completed")
    assert completed["answer_source"] == "small_talk"
    assert completed["outcome"] == "small_talk"


async def test_the_phase_two_join_is_computable_from_one_turns_lines() -> None:
    # "escalations raised by turns that contained no request" is this join: the labels
    # on `intent.classified`, against the presence of `escalation.raised`. Distress is
    # the deliberate exception, excluded by cause rather than by label.
    courteous = fake_anthropic_client(
        ["You're welcome!"], intents=[IntentLabel.SMALL_TALK]
    )
    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        await _run_turn(courteous, "Thanks!")

    classified = next(e for e in logs if e["event"] == "intent.classified")
    assert [str(i) for i in classified["intents"]] == ["small_talk"]
    assert _node_result(logs, "classify_intent")["stopping_cause"] is None

    distressed = fake_anthropic_client(
        ["never generated"], intents=[IntentLabel.DISTRESS]
    )
    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        await _run_turn(distressed, "I'm terrified")

    assert _node_result(logs, "classify_intent")["stopping_cause"] == "distress"


async def test_small_talk_beside_an_unauthorized_request_is_dropped() -> None:
    """FR-008 holds for `unknown` too: it is another intent, so the pleasantry goes.

    The combination no earlier test paired. Routed to the small-talk node with a notice
    owed, the turn replied twice - the node streams unconditionally, having no collect
    mode, and the composer then streamed a second reply the small-talk text never
    reached. One message, one reply.
    """
    from chat.agent.escalation import HANDOFF_TEXT

    escalation = EscalationRequests()
    client = fake_anthropic_client(
        ["never generated"],
        intents=[IntentLabel.SMALL_TALK, IntentLabel.UNKNOWN],
    )

    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        events = await _run_turn(
            client, "Thanks! Can you write me a sick note?", escalation=escalation
        )

    routing = _node_result(logs, "classify_intent")
    assert routing["specialists"] == ["hand_off"]
    assert routing["notice_required"] is False
    assert routing["merge_required"] is False
    assert _started_nodes(logs) == ["classify_intent", "hand_off", "compose_answer"]

    streamed = "".join(e.text for e in events if isinstance(e, ChatTokenEvent))
    assert streamed == HANDOFF_TEXT[EscalationReason.NOT_AUTHORIZED]
    assert escalation.recorded == (EscalationReason.NOT_AUTHORIZED,)
    # No generation at all: not the courteous reply, not a composed one.
    assert client.messages.stream.call_count == 0


async def test_exactly_one_terminal_event_reaches_the_patient_on_that_turn() -> None:
    # Two replies is the symptom a reader would actually see, so it is asserted
    # directly rather than only through the routing record.
    client = fake_anthropic_client(
        ["never generated"],
        intents=[IntentLabel.SMALL_TALK, IntentLabel.UNKNOWN],
    )

    events = await _run_turn(client, "Thanks! Please renew my prescription")

    assert len([e for e in events if isinstance(e, ChatDoneEvent)]) == 1


async def test_every_applicable_stopping_cause_reaches_the_record() -> None:
    """FR-047: precedence decides the mark; the log keeps every call.

    The existing precedence tests pass whether or not the discarded cause was ever
    recorded, because they assert `message_mark`, which resolves to the same value
    either way. This asserts the collector itself - which is what `apply_escalation`
    writes one log line per, and therefore the only place a second cause survives.
    """
    escalation = EscalationRequests()
    client = fake_anthropic_client(
        ["never generated"],
        intents=[IntentLabel.DISTRESS, IntentLabel.URGENT_CONDITION],
    )

    await _run_turn(client, "I can't breathe and I'm terrified", escalation=escalation)

    assert escalation.recorded == (
        EscalationReason.URGENT_CONDITION,
        EscalationReason.DISTRESS,
    )
    # Unchanged: one mark, one silence, chosen by precedence.
    assert escalation.message_mark is AttentionMark.URGENT_CONDITION
    assert escalation.conversation_reason is EscalationReason.URGENT_CONDITION


async def test_a_request_for_a_person_inside_an_emergency_is_not_lost() -> None:
    escalation = EscalationRequests()
    client = fake_anthropic_client(
        ["never generated"],
        intents=[
            IntentLabel.CALL_STAFF,
            IntentLabel.URGENT_CONDITION,
            IntentLabel.BOOKING_FOR_ANOTHER,
        ],
    )

    await _run_turn(
        client, "my father can't breathe, get me a person", escalation=escalation
    )

    assert escalation.recorded == (
        EscalationReason.URGENT_CONDITION,
        EscalationReason.PATIENT_ASKED_FOR_PERSON,
        EscalationReason.BOOKING_FOR_ANOTHER_PERSON,
    )
    assert escalation.message_mark is AttentionMark.URGENT_CONDITION


async def test_an_unauthorized_request_inside_a_stopping_turn_is_recorded_too() -> None:
    escalation = EscalationRequests()
    client = fake_anthropic_client(
        ["never generated"],
        intents=[IntentLabel.UNKNOWN, IntentLabel.DISTRESS],
    )

    await _run_turn(
        client, "I'm frightened, and please write me a sick note", escalation=escalation
    )

    assert escalation.recorded == (
        EscalationReason.DISTRESS,
        EscalationReason.NOT_AUTHORIZED,
    )
    # Distress still takes the turn, silences it, and writes its own sentence.
    assert escalation.conversation_reason is EscalationReason.DISTRESS
