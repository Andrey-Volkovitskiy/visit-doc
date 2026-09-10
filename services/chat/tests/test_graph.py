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
from chat.agent.answer_faq import _ABSTENTION_MESSAGE
from chat.agent.compose_answer import FaqResult, FaqSegmentAnswer
from chat.agent.escalation import HANDOFF_MESSAGE, EscalationRequests
from chat.agent.history import (
    OPENING_CLINIC_NOTE,
    split_into_bursts,
    to_claude_messages,
)
from chat.agent.tools.registry import ToolContext
from chat.agent.tools.scheduling_tools import SCHEDULING_TOOLS
from chat.core.config import Settings
from chat.core.errors import TurnPipelineError
from chat.db.session import session_factory
from chat.domain.models import (
    AttentionMark,
    EscalationReason,
    Message,
    MessageSender,
)
from chat.domain.schemas import (
    AnswerSource,
    ChatDoneEvent,
    ChatTokenEvent,
    Citation,
    FaqVerdict,
    IntentLabel,
    RequestSegment,
)
from chat.rag.indexing import publish_revision, remove_entry_chunks
from chat.rag.pipeline import ScoredChunk
from chat.repositories import chat_repository, faq_repository
from chat.repositories.qdrant_repository import create_client, ensure_collection
from structlog.testing import capture_logs
from ulid import ULID

from .conftest import (
    COMPOSE_SYSTEM_PROMPT,
    DEFAULT_BOOKING_REPLY,
    fake_anthropic_client,
    fake_classify_intent_client,
    fake_embed_texts,
    recording_embed_texts,
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


def _verdicts(done: ChatDoneEvent) -> list[FaqVerdict]:
    """Return each request's verdict, in position order, off a terminal event.

    There is no turn-level verdict on the event to read: a turn may answer one request
    and abstain on another, so the verdict lives on the request.
    """
    assert done.request_outcomes is not None
    return [outcome.verdict for outcome in done.request_outcomes]


def _cited(done: ChatDoneEvent) -> list[Citation]:
    """Return every chunk the turn's requests cited, in position order."""
    assert done.request_outcomes is not None
    return [c for outcome in done.request_outcomes for c in outcome.citations]


def test_grounded_answer_matches_answer_faq_byte_for_byte(seeded_entry: int) -> None:
    with patch("chat.rag.retriever.embed_texts", fake_embed_texts):
        anthropic_client = fake_anthropic_client(["Visiting ", "hours are 8am to 5pm."])
        events = asyncio.run(_run_turn(anthropic_client, "when can I visit?"))

    token_events = [e for e in events if isinstance(e, ChatTokenEvent)]
    done_event = events[-1]
    assert isinstance(done_event, ChatDoneEvent)
    assert "".join(e.text for e in token_events) == "Visiting hours are 8am to 5pm."
    assert _verdicts(done_event) == [FaqVerdict.ANSWERED]
    assert any(c.entry_id == seeded_entry for c in _cited(done_event))


def test_abstention_matches_answer_faq_byte_for_byte(seeded_entry: int) -> None:
    with patch("chat.rag.retriever.embed_texts", fake_embed_texts):
        anthropic_client = fake_anthropic_client()
        events = asyncio.run(_run_turn(anthropic_client, "what is the weather today?"))

    assert len(events) == 1
    done_event = events[0]
    assert isinstance(done_event, ChatDoneEvent)
    assert _verdicts(done_event) == [FaqVerdict.ABSTAINED_SIMILARITY_FLOOR]
    assert _cited(done_event) == []


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
    assert _verdicts(done_event) == [FaqVerdict.ANSWERED]


def test_a_failed_classification_logs_the_whole_event_it_always_logged(
    seeded_entry: int,
) -> None:
    # The whole `intent.classified` payload on the fallback path, not just its
    # `intents`: one segment at position 0 carrying the message verbatim, and
    # `cap_bound` false because the fallback segmented nothing and so combined nothing.
    # The fallback builds an `IntentClassificationResult` of its own, and this is what
    # says that building one changed none of what the turn reports.
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(
            ["Visiting hours."], classify_error=RuntimeError("boom")
        )
        asyncio.run(_run_turn(anthropic_client, "when can I visit?"))

    classified = next(e for e in logs if e["event"] == "intent.classified")
    assert classified["intents"] == [IntentLabel.CLASSIFICATION_FAILED]
    assert classified["segments"] == [
        {
            "position": 0,
            "intent": IntentLabel.CLASSIFICATION_FAILED.value,
            "text": "when can I visit?",
        }
    ]
    assert classified["cap_bound"] is False


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
    assert _verdicts(done_event) == [FaqVerdict.ANSWERED]


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
    assert _verdicts(done_event) == [FaqVerdict.ANSWERED]


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
    assert routing["specialists_collect"] is False
    done_event = events[-1]
    assert isinstance(done_event, ChatDoneEvent)
    assert done_event.answer_source == "booking"
    # A booking reply was never retrieved against, so it has no request outcome at all
    # - null, not an empty list, which would read as a half that ran.
    assert done_event.request_outcomes is None


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
    assert routing["specialists_collect"] is True
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
    # Never retrieved against, so it carries no request outcome at all.
    assert done_event.request_outcomes is None


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
    assert completed["outcome"] == "handed_off"
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
    assert _verdicts(done_event) == [FaqVerdict.ANSWERED]


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
    assert done.request_outcomes is None


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
    assert _node_result(logs, "classify_intent")["specialists_collect"] is False


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
    assert _node_result(logs, "classify_intent")["specialists_collect"] is True


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
    assert routing["specialists_collect"] is True
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
    assert "request_outcomes" not in node
    assert "citation_count" not in node

    completed = next(e for e in logs if e["event"] == "turn.completed")
    # One field, not two: `outcome` names the turn's shape, so an `answer_source`
    # beside it would be the same fact twice in one event.
    assert completed["outcome"] == "small_talk"
    assert "answer_source" not in completed


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
    assert routing["specialists_collect"] is False
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


# --- what a merged turn merged, and a reply that ran out of room ---------------------


async def test_the_compose_record_says_whether_a_notice_was_merged_in(
    seeded_entry: int,
) -> None:
    client = fake_anthropic_client(
        ["Hours are 8-5, and the sick note has gone to staff."],
        intents=[IntentLabel.UNKNOWN, IntentLabel.FAQ_QUESTION],
    )

    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        await _run_turn(client, "When can I visit, and write me a sick note?")

    composed = _node_result(logs, "compose_answer")
    assert composed["merged"] is True
    # One specialist ran, so `merged` alone would count this with the two-specialist
    # turns. This is what tells them apart on the same line.
    assert composed["notice_included"] is True


async def test_a_two_specialist_merge_records_no_notice(seeded_entry: int) -> None:
    client = fake_anthropic_client(
        ["Hours are 8-5, and you're booked."],
        intents=[IntentLabel.FAQ_QUESTION, IntentLabel.BOOKING],
    )

    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        await _run_turn(client, "When can I visit, and can I book Friday?")

    composed = _node_result(logs, "compose_answer")
    assert composed["merged"] is True
    assert composed["notice_included"] is False


async def test_a_truncated_pleasantry_is_visible_in_the_turns_record() -> None:
    client = fake_anthropic_client(
        ["Of course, take your"],
        intents=[IntentLabel.SMALL_TALK],
        stop_reason="max_tokens",
    )

    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        await _run_turn(client, "Let me think a bit")

    assert _node_result(logs, "small_talk")["truncated"] is True


async def test_an_ordinary_pleasantry_records_no_truncation() -> None:
    client = fake_anthropic_client(
        ["You're welcome!"], intents=[IntentLabel.SMALL_TALK]
    )

    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        await _run_turn(client, "Thanks!")

    assert _node_result(logs, "small_talk")["truncated"] is False


# --- Phase 1g: a failed classification is still one segment --------------------------


def test_a_failed_classification_synthesizes_one_segment_of_the_whole_message(
    seeded_entry: int,
) -> None:
    # The fallback must stay exactly what it is today: the whole message down the FAQ
    # path. Making it a one-segment turn keeps every consumer downstream on one shape
    # (FR-008, and spec 009's FR-009 preserved).
    queries: list[str] = []
    with (
        patch("chat.rag.retriever.embed_texts", recording_embed_texts(queries)),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(
            ["Visiting hours."], classify_error=RuntimeError("boom")
        )
        events = asyncio.run(_run_turn(anthropic_client, "when can I visit?"))

    classified = next(e for e in logs if e["event"] == "intent.classified")
    assert classified["intents"] == [IntentLabel.CLASSIFICATION_FAILED]
    # One retrieval, for the whole message - byte for byte what it retrieves today.
    assert queries == ["when can I visit?"]
    done_event = events[-1]
    assert isinstance(done_event, ChatDoneEvent)
    assert _verdicts(done_event) == [FaqVerdict.ANSWERED]


def test_a_failed_classification_calls_nobody_and_answers_no_pleasantry(
    seeded_entry: int,
) -> None:
    # A failure is not evidence about what the message was: no small-talk reply, and
    # not the not-authorized route either (FR-008).
    escalation = EscalationRequests()
    with patch("chat.rag.retriever.embed_texts", fake_embed_texts):
        anthropic_client = fake_anthropic_client(
            ["Visiting hours."], classify_error=RuntimeError("boom")
        )
        events = asyncio.run(
            _run_turn(anthropic_client, "when can I visit?", escalation=escalation)
        )

    done_event = events[-1]
    assert isinstance(done_event, ChatDoneEvent)
    assert done_event.answer_source is AnswerSource.FAQ
    assert EscalationReason.NOT_AUTHORIZED not in escalation.recorded


def test_an_invalid_segmentation_falls_back_to_the_faq_path(
    seeded_entry: int,
) -> None:
    # An over-long segment list is an invalid result, and takes the existing fallback.
    queries: list[str] = []
    with (
        patch("chat.rag.retriever.embed_texts", recording_embed_texts(queries)),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(["Visiting hours."])
        fake_classify_intent_client(
            raw_text=(
                '{"segments": ['
                '{"intent": "faq_question", "text": "a"},'
                '{"intent": "faq_question", "text": "b"},'
                '{"intent": "faq_question", "text": "c"},'
                '{"intent": "faq_question", "text": "d"}]}'
            ),
            client=anthropic_client,
        )
        asyncio.run(_run_turn(anthropic_client, "when can I visit?"))

    classified = next(e for e in logs if e["event"] == "intent.classified")
    assert classified["intents"] == [IntentLabel.CLASSIFICATION_FAILED]
    assert queries == ["when can I visit?"]


def test_a_blank_message_still_falls_back_rather_than_failing_the_turn(
    seeded_entry: int,
) -> None:
    # `ChatRequest` rejects a whitespace-only message, but this calls the graph
    # directly, as anything reading a turn out of stored history does - and there a
    # segment refuses to carry blank text. The handler exists so that a classification
    # failure never fails the request - it must not be the thing that raises.
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(
            ["Visiting hours."], classify_error=RuntimeError("boom")
        )
        events = asyncio.run(_run_turn(anthropic_client, "   "))

    classified = next(e for e in logs if e["event"] == "intent.classified")
    assert classified["intents"] == [IntentLabel.CLASSIFICATION_FAILED]
    assert classified["segments"][0]["text"].strip()
    assert isinstance(events[-1], ChatDoneEvent)


# --- Phase 1g: a turn carrying several requests --------------------------------------


def _composing_calls(client: MagicMock) -> int:
    """How many times the composing step ran on this turn."""
    return sum(
        1
        for call in client.messages.stream.call_args_list
        if call.kwargs.get("system") == COMPOSE_SYSTEM_PROMPT
    )


def test_two_answerable_requests_are_answered_in_one_merged_reply(
    seeded_entry: int,
) -> None:
    queries: list[str] = []
    with patch("chat.rag.retriever.embed_texts", recording_embed_texts(queries)):
        anthropic_client = fake_anthropic_client(
            ["Visiting hours are 8am to 5pm."],
            segments=[
                (IntentLabel.FAQ_QUESTION, "when can I visit?"),
                (IntentLabel.FAQ_QUESTION, "what are the visiting hours on Sunday?"),
            ],
        )
        events = asyncio.run(
            _run_turn(
                anthropic_client,
                "when can I visit, and what are the hours on Sunday?",
            )
        )

    assert queries == ["when can I visit?", "what are the visiting hours on Sunday?"]
    assert _composing_calls(anthropic_client) == 1
    done_event = events[-1]
    assert isinstance(done_event, ChatDoneEvent)
    assert done_event.answer_source is AnswerSource.MERGED
    assert _verdicts(done_event) == [FaqVerdict.ANSWERED, FaqVerdict.ANSWERED]


def test_one_unanswerable_request_no_longer_abstains_for_the_answered_one(
    seeded_entry: int,
) -> None:
    # Phase 1h's headline change, at the graph level: where this turn used to collapse
    # to the constant message, it now merges the answered request's answer with a named
    # gap for the one the corpus could not answer.
    escalation = EscalationRequests()
    with patch("chat.rag.retriever.embed_texts", fake_embed_texts):
        anthropic_client = fake_anthropic_client(
            ["Visiting hours are 8am to 5pm."],
            segments=[
                (IntentLabel.FAQ_QUESTION, "when can I visit?"),
                (IntentLabel.FAQ_QUESTION, "what is your refund policy?"),
            ],
        )
        events = asyncio.run(
            _run_turn(
                anthropic_client,
                "when can I visit, and what is your refund policy?",
                escalation=escalation,
            )
        )

    assert _composing_calls(anthropic_client) == 1
    done_event = events[-1]
    assert isinstance(done_event, ChatDoneEvent)
    assert done_event.answer_source is AnswerSource.MERGED
    assert [v.answered for v in _verdicts(done_event)] == [True, False]
    # The gap still calls a person, exactly once, and still does not silence the
    # conversation - what changed is only that an answer goes out beside it.
    assert escalation.recorded == (EscalationReason.CORPUS_COULD_NOT_ANSWER,)


def test_a_collapsed_answered_half_cites_what_it_retrieved() -> None:
    """The collapse path reads its citations off the half that survived.

    No route produces this state today: the only turn that collapses to one part is
    one whose FAQ half abstained, and an abstention cites nothing - so the composer's
    citation arm has never run. It becomes reachable the moment a half contributes
    fewer parts than the route counted (Phase 1h serving the answerable half, a third
    specialist, a booking half that returns no result), and it would then first run in
    front of a patient. The half is stubbed to that shape here instead: two questions
    routed - so the specialists collect and the composer owes the terminal event - and
    one answered part coming back.
    """
    chunk = ScoredChunk(
        faq_entry_id=7,
        chunk_index=0,
        chunk_text=_ENTRY_CONTENT,
        similarity_score=0.9,
        rerank_score=0.8,
    )
    answered = FaqResult.from_segments(
        [
            FaqSegmentAnswer(
                position=0,
                question="when can I visit?",
                answer_text=_ENTRY_CONTENT,
                verdict=FaqVerdict.ANSWERED,
                citations=[
                    Citation(entry_id=7, chunk_index=0, chunk_text=_ENTRY_CONTENT)
                ],
                scored_chunks=[chunk],
            )
        ],
        abstention_message="unused: this half answered",
    )

    async def _answer_faq(
        *_args: object, **_kwargs: object
    ) -> AsyncIterator[FaqResult]:
        yield answered

    anthropic_client = fake_anthropic_client(
        ["never generated"],
        segments=[
            (IntentLabel.FAQ_QUESTION, "when can I visit?"),
            (IntentLabel.FAQ_QUESTION, "what is your refund policy?"),
        ],
    )
    with (
        patch("chat.agent.graph.answer_faq", _answer_faq),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        events = asyncio.run(
            _run_turn(
                anthropic_client, "when can I visit, and what is your refund policy?"
            )
        )

    compose = _node_result(logs, "compose_answer")
    assert compose["collapsed_to_one_part"] is True
    assert _composing_calls(anthropic_client) == 0
    done_event = events[-1]
    assert isinstance(done_event, ChatDoneEvent)
    assert done_event.answer_source is AnswerSource.FAQ
    assert _verdicts(done_event) == [FaqVerdict.ANSWERED]
    assert done_event.message == _ENTRY_CONTENT
    # The patient is told what the answer rested on: the collapse path reads the
    # surviving half's own outcomes rather than emitting an empty list beside a reply
    # that did cite something.
    assert _cited(done_event) == [
        Citation(entry_id=7, chunk_index=0, chunk_text=_ENTRY_CONTENT)
    ]
    # And the turn's record names the same chunk under the same request, with the
    # scores the wire type does not carry - the two are one selection in two shapes,
    # not two answers.
    completed = next(e for e in logs if e["event"] == "turn.completed")
    assert completed["request_outcomes"] == [
        {
            "position": 0,
            "verdict": "answered",
            "citations": [
                {
                    "entry_id": 7,
                    "chunk_index": 0,
                    "chunk_text": _ENTRY_CONTENT,
                    "similarity_score": 0.9,
                    "rerank_score": 0.8,
                }
            ],
        }
    ]


def test_one_requests_retrieval_failure_fails_the_whole_turn(
    seeded_entry: int,
) -> None:
    async def _embed(
        client: object, texts: list[str], input_type: str = "document"
    ) -> list[list[float]]:
        if "refund" in texts[0]:
            raise RuntimeError("voyage is down")
        return await fake_embed_texts(client, texts, input_type)

    with patch("chat.rag.retriever.embed_texts", _embed):
        anthropic_client = fake_anthropic_client(
            ["Visiting hours are 8am to 5pm."],
            segments=[
                (IntentLabel.FAQ_QUESTION, "when can I visit?"),
                (IntentLabel.FAQ_QUESTION, "what is your refund policy?"),
            ],
        )
        with pytest.raises(TurnPipelineError):
            asyncio.run(
                _run_turn(anthropic_client, "when can I visit, and refund policy?")
            )


def test_a_mixed_message_retrieves_only_for_its_question(seeded_entry: int) -> None:
    # The FAQ half never sees the scheduling clause, so it cannot abstain on one and
    # cannot page a person for it.
    queries: list[str] = []
    escalation = EscalationRequests()
    with patch("chat.rag.retriever.embed_texts", recording_embed_texts(queries)):
        anthropic_client = fake_anthropic_client(
            ["Visiting hours are 8am to 5pm."],
            segments=[
                (IntentLabel.FAQ_QUESTION, "when can I visit?"),
                (IntentLabel.BOOKING, "can I book Friday?"),
            ],
        )
        events = asyncio.run(
            _run_turn(
                anthropic_client,
                "when can I visit, and can I book Friday?",
                escalation=escalation,
            )
        )

    assert queries == ["when can I visit?"]
    assert EscalationReason.CORPUS_COULD_NOT_ANSWER not in escalation.recorded
    done_event = events[-1]
    assert isinstance(done_event, ChatDoneEvent)
    assert done_event.answer_source is AnswerSource.MERGED


def test_the_booking_half_is_asked_only_about_the_scheduling_clause(
    seeded_entry: int,
) -> None:
    with patch("chat.rag.retriever.embed_texts", fake_embed_texts):
        anthropic_client = fake_anthropic_client(
            ["Visiting hours are 8am to 5pm."],
            segments=[
                (IntentLabel.FAQ_QUESTION, "when can I visit?"),
                (IntentLabel.BOOKING, "can I book Friday?"),
            ],
        )
        asyncio.run(
            _run_turn(anthropic_client, "when can I visit, and can I book Friday?")
        )

    booking_prompts = [
        str(call.kwargs["messages"])
        for call in anthropic_client.messages.create.call_args_list
        if call.kwargs.get("tools") is not None
    ]
    assert booking_prompts
    for prompt in booking_prompts:
        assert "can I book Friday?" in prompt
        assert "when can I visit?" not in prompt


# --- Phase 1g: one request still costs one path --------------------------------------


def test_a_single_request_turn_streams_and_makes_no_composing_call(
    seeded_entry: int,
) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(
            ["Visiting hours are 8am to 5pm."],
            segments=[(IntentLabel.FAQ_QUESTION, "when can I visit?")],
        )
        events = asyncio.run(_run_turn(anthropic_client, "when can I visit?"))

    assert _node_result(logs, "classify_intent")["specialists_collect"] is False
    assert _node_result(logs, "answer_faq")["mode"] == "streamed"
    assert _node_result(logs, "compose_answer")["merged"] is False
    assert _composing_calls(anthropic_client) == 0
    done_event = events[-1]
    assert isinstance(done_event, ChatDoneEvent)
    assert done_event.answer_source is AnswerSource.FAQ
    assert any(isinstance(e, ChatTokenEvent) for e in events)


def test_a_three_request_turn_still_classifies_once(seeded_entry: int) -> None:
    # Segmentation rides on the classification the turn already makes: no second call
    # and no second round trip, however many requests the message carried.
    with patch("chat.rag.retriever.embed_texts", fake_embed_texts):
        anthropic_client = fake_anthropic_client(
            ["Visiting hours are 8am to 5pm."],
            segments=[
                (IntentLabel.FAQ_QUESTION, "when can I visit?"),
                (IntentLabel.FAQ_QUESTION, "what are the visiting hours?"),
                (IntentLabel.FAQ_QUESTION, "are visiting hours different on Sunday?"),
            ],
        )
        asyncio.run(
            _run_turn(anthropic_client, "when can I visit, hours, Sunday hours?")
        )

    classifications = [
        call
        for call in anthropic_client.messages.create.call_args_list
        if call.kwargs.get("tools") is None
    ]
    assert len(classifications) == 1


# --- Phase 1g: the earlier phases' rules, read per request ---------------------------


@pytest.mark.parametrize(
    ("label", "reason"),
    [
        (IntentLabel.URGENT_CONDITION, EscalationReason.URGENT_CONDITION),
        (IntentLabel.DISTRESS, EscalationReason.DISTRESS),
        (IntentLabel.CALL_STAFF, EscalationReason.PATIENT_ASKED_FOR_PERSON),
        (
            IntentLabel.BOOKING_FOR_ANOTHER,
            EscalationReason.BOOKING_FOR_ANOTHER_PERSON,
        ),
    ],
)
def test_an_overriding_request_takes_the_whole_turn(
    seeded_entry: int, label: IntentLabel, reason: EscalationReason
) -> None:
    # Answering half a message and then falling silent is worse than handing over
    # cleanly, so every other request on the turn is suppressed.
    queries: list[str] = []
    escalation = EscalationRequests()
    with (
        patch("chat.rag.retriever.embed_texts", recording_embed_texts(queries)),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(
            ["Visiting hours are 8am to 5pm."],
            segments=[
                (IntentLabel.FAQ_QUESTION, "when can I visit?"),
                (label, "my chest hurts"),
            ],
        )
        events = asyncio.run(
            _run_turn(
                anthropic_client,
                "when can I visit? my chest hurts",
                escalation=escalation,
            )
        )

    assert _started_nodes(logs) == ["classify_intent", "hand_off", "compose_answer"]
    assert queries == []
    assert escalation.recorded == (reason,)
    done_event = events[-1]
    assert isinstance(done_event, ChatDoneEvent)
    assert done_event.answer_source is AnswerSource.HAND_OFF
    assert done_event.request_outcomes is None


def test_a_pleasantry_beside_a_request_routes_nowhere(seeded_entry: int) -> None:
    # Belt and braces: the segmenter is told not to emit one, and the router drops one
    # that appears anyway - the two fail independently.
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(
            ["Visiting hours are 8am to 5pm."],
            segments=[
                (IntentLabel.SMALL_TALK, "hi!"),
                (IntentLabel.FAQ_QUESTION, "when can I visit?"),
            ],
        )
        events = asyncio.run(_run_turn(anthropic_client, "hi! when can I visit?"))

    assert _started_nodes(logs) == ["classify_intent", "answer_faq", "compose_answer"]
    done_event = events[-1]
    assert isinstance(done_event, ChatDoneEvent)
    assert done_event.answer_source is AnswerSource.FAQ


def test_an_unauthorized_request_beside_a_question_is_a_notice(
    seeded_entry: int,
) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(
            ["Visiting hours are 8am to 5pm."],
            segments=[
                (IntentLabel.FAQ_QUESTION, "when can I visit?"),
                (IntentLabel.UNKNOWN, "write me a sick note"),
            ],
        )
        events = asyncio.run(
            _run_turn(anthropic_client, "when can I visit? also a sick note please")
        )

    assert _node_result(logs, "classify_intent")["notice_required"] is True
    assert _started_nodes(logs) == ["classify_intent", "answer_faq", "compose_answer"]
    done_event = events[-1]
    assert isinstance(done_event, ChatDoneEvent)
    assert done_event.answer_source is AnswerSource.MERGED


def test_an_unauthorized_request_alone_hands_the_turn_over(seeded_entry: int) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(
            ["unused"],
            segments=[(IntentLabel.UNKNOWN, "write me a sick note")],
        )
        events = asyncio.run(_run_turn(anthropic_client, "write me a sick note"))

    assert _started_nodes(logs) == ["classify_intent", "hand_off", "compose_answer"]
    done_event = events[-1]
    assert isinstance(done_event, ChatDoneEvent)
    assert done_event.answer_source is AnswerSource.HAND_OFF


def test_two_unanswerable_questions_raise_one_escalation(seeded_entry: int) -> None:
    escalation = EscalationRequests()
    with patch("chat.rag.retriever.embed_texts", fake_embed_texts):
        anthropic_client = fake_anthropic_client(
            ["unused"],
            segments=[
                (IntentLabel.FAQ_QUESTION, "what is your refund policy?"),
                (IntentLabel.FAQ_QUESTION, "do you validate parking?"),
            ],
        )
        asyncio.run(
            _run_turn(anthropic_client, "refund policy? parking validation?"),
        )
        events = asyncio.run(
            _run_turn(
                anthropic_client,
                "refund policy? parking validation?",
                escalation=escalation,
            )
        )

    assert escalation.recorded == (EscalationReason.CORPUS_COULD_NOT_ANSWER,)
    assert isinstance(events[-1], ChatDoneEvent)


# --- Phase 1g: what the turn records about its requests ------------------------------


def test_the_classification_event_carries_the_segmentation_it_chose(
    seeded_entry: int,
) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(
            ["Visiting hours are 8am to 5pm."],
            segments=[
                (IntentLabel.FAQ_QUESTION, "when can I visit?"),
                (IntentLabel.BOOKING, "can I book Friday?"),
            ],
        )
        asyncio.run(
            _run_turn(anthropic_client, "when can I visit, and can I book Friday?")
        )

    classified = next(e for e in logs if e["event"] == "intent.classified")
    assert classified["segments"] == [
        {"position": 0, "intent": "faq_question", "text": "when can I visit?"},
        {"position": 1, "intent": "booking", "text": "can I book Friday?"},
    ]
    assert classified["cap_bound"] is False


def test_a_capped_segmentation_says_so_rather_than_leaving_it_to_the_count(
    seeded_entry: int,
) -> None:
    # Three segments is not evidence the cap bound - a message with exactly three
    # requests fits.
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(["Visiting hours are 8am to 5pm."])
        fake_classify_intent_client(
            segments=[(IntentLabel.FAQ_QUESTION, "when can I visit?")],
            cap_bound=True,
            client=anthropic_client,
        )
        asyncio.run(_run_turn(anthropic_client, "when can I visit?"))

    classified = next(e for e in logs if e["event"] == "intent.classified")
    assert classified["cap_bound"] is True


def test_the_turn_records_each_requests_own_outcome_beside_the_summary(
    seeded_entry: int,
) -> None:
    # The summary verdict is lossy where two requests stopped at different gates; the
    # record is not.
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(
            ["Visiting hours are 8am to 5pm."],
            segments=[
                (IntentLabel.FAQ_QUESTION, "when can I visit?"),
                (IntentLabel.FAQ_QUESTION, "what is your refund policy?"),
            ],
        )
        asyncio.run(_run_turn(anthropic_client, "when can I visit, and refund policy?"))

    completed = next(e for e in logs if e["event"] == "turn.completed")
    assert completed["segment_count"] == 2
    assert [
        {"position": o["position"], "verdict": o["verdict"]}
        for o in completed["request_outcomes"]
    ] == [
        {"position": 0, "verdict": "answered"},
        {"position": 1, "verdict": "abstained_similarity_floor"},
    ]
    assert _node_result(logs, "answer_faq")["segment_count"] == 2


def test_a_single_request_turn_records_one_segment(seeded_entry: int) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(["Visiting hours are 8am to 5pm."])
        asyncio.run(_run_turn(anthropic_client, "when can I visit?"))

    completed = next(e for e in logs if e["event"] == "turn.completed")
    assert completed["segment_count"] == 1
    assert [
        {"position": o["position"], "verdict": o["verdict"]}
        for o in completed["request_outcomes"]
    ] == [{"position": 0, "verdict": "answered"}]


def test_each_requests_own_words_are_recorded_on_a_merged_turn(
    seeded_entry: int,
) -> None:
    # On a merged turn `turn.completed` carries only what the composing model wrote, so
    # without this the answers being merged appear in no record at all and a bad merge
    # cannot be told from a bad half.
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(
            ["Visiting hours are 8am to 5pm."],
            segments=[
                (IntentLabel.FAQ_QUESTION, "when can I visit?"),
                (IntentLabel.FAQ_QUESTION, "what are the visiting hours on Sunday?"),
            ],
        )
        asyncio.run(
            _run_turn(anthropic_client, "when can I visit, and what about Sunday?")
        )

    answers = _node_result(logs, "answer_faq")["segment_answers"]
    assert [a["position"] for a in answers] == [0, 1]
    assert [a["question"] for a in answers] == [
        "when can I visit?",
        "what are the visiting hours on Sunday?",
    ]
    assert all(a["verdict"] == "answered" for a in answers)
    assert all(a["answer_text"] for a in answers)


def test_a_single_request_turn_records_its_one_answer_the_same_way(
    seeded_entry: int,
) -> None:
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(["Visiting hours are 8am to 5pm."])
        asyncio.run(_run_turn(anthropic_client, "when can I visit?"))

    result = _node_result(logs, "answer_faq")
    assert [a["position"] for a in result["segment_answers"]] == [0]
    # The half's own single text keeps its own field, unchanged.
    assert result["answer_text"] == "Visiting hours are 8am to 5pm."


# --- Phase 1h, US1: a turn serves what it can and names what it cannot ---------------
#
# Driven through the seeded corpus, which answers a question about visiting hours and
# nothing else - so "one answerable and one unanswerable request" is a property of the
# corpus these turns run against rather than of a stub the assertions then read back.


def _generation_calls(client: MagicMock) -> int:
    """How many answer-generating calls this turn made, composing excluded."""
    return sum(
        1
        for call in client.messages.stream.call_args_list
        if call.kwargs.get("system") != COMPOSE_SYSTEM_PROMPT
    )


def _mixed_faq_turn(
    *questions: str, tokens: list[str] | None = None
) -> tuple[MagicMock, list[ChatTokenEvent | ChatDoneEvent]]:
    """Run one turn carrying `questions` as separate FAQ requests.

    Returns: the mocked client the turn ran against, and the events it produced.
    """
    with patch("chat.rag.retriever.embed_texts", fake_embed_texts):
        anthropic_client = fake_anthropic_client(
            tokens if tokens is not None else [_ENTRY_CONTENT],
            segments=[(IntentLabel.FAQ_QUESTION, q) for q in questions],
        )
        events = asyncio.run(_run_turn(anthropic_client, ", and ".join(questions)))
    return anthropic_client, events


def test_a_partly_answerable_turn_is_composed_from_its_answer_and_its_gap(
    seeded_entry: int,
) -> None:
    client, events = _mixed_faq_turn(
        "when can I visit?", "what is your refund policy?", tokens=["merged reply"]
    )

    assert _composing_calls(client) == 1
    done_event = events[-1]
    assert isinstance(done_event, ChatDoneEvent)
    assert [v.answered for v in _verdicts(done_event)] == [True, False]
    # The answered request's own text reached the composer, rather than being replaced
    # by the abstention the way the whole half used to be.
    prompt = str(client.messages.stream.call_args.kwargs["messages"])
    assert "when can I visit?" in prompt
    assert "NO CONFIDENT ANSWER" in prompt


def test_a_partly_answerable_turn_counts_the_answer_and_the_gap_as_two_parts(
    seeded_entry: int,
) -> None:
    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        _mixed_faq_turn(
            "when can I visit?", "what is your refund policy?", tokens=["merged reply"]
        )

    compose = _node_result(logs, "compose_answer")
    # Merged, so the composing branch ran - the collapse to a single part is what the
    # turn no longer does when one of its requests was answerable.
    assert compose["merged"] is True
    assert "collapsed_to_one_part" not in compose


def test_a_turn_whose_every_request_abstained_keeps_the_constant_reply(
    seeded_entry: int,
) -> None:
    client, events = _mixed_faq_turn(
        "what is your refund policy?", "who won the game last night?"
    )

    assert _composing_calls(client) == 0
    done_event = events[-1]
    assert isinstance(done_event, ChatDoneEvent)
    assert [v.answered for v in _verdicts(done_event)] == [False, False]
    # Byte for byte: a stopping reply makes no claim about the clinic, which is the
    # only thing retrieval could have grounded, so no model paraphrases it.
    assert done_event.message == _ABSTENTION_MESSAGE


def test_a_single_answered_request_still_streams_and_composes_nothing(
    seeded_entry: int,
) -> None:
    client, events = _mixed_faq_turn("when can I visit?")

    assert _composing_calls(client) == 0
    assert [e for e in events if isinstance(e, ChatTokenEvent)]
    done_event = events[-1]
    assert isinstance(done_event, ChatDoneEvent)
    assert done_event.answer_source is AnswerSource.FAQ
    assert _verdicts(done_event) == [FaqVerdict.ANSWERED]


@pytest.mark.parametrize("answerable", [0, 1, 2])
def test_a_turn_generates_one_answer_per_answerable_request(
    seeded_entry: int, answerable: int
) -> None:
    # Counted, never timed: an abstaining request costs no generation call, so *m*
    # answered requests cost exactly *m*, whatever the turn does with them afterwards.
    questions = ["when can I visit?", "what are the visiting hours on Sunday?"][
        :answerable
    ] + ["what is your refund policy?", "who won the game last night?"][
        : 2 - answerable
    ]
    client, _ = _mixed_faq_turn(*questions, tokens=["merged reply"])

    assert _generation_calls(client) == answerable
    assert _composing_calls(client) <= 1


@pytest.mark.parametrize(
    ("faq_requests", "abstained"),
    [(k, m) for k in range(1, 4) for m in range(k + 1)],
)
def test_a_turn_never_produces_more_parts_than_its_route_expected(
    faq_requests: int, abstained: int
) -> None:
    """The routing-time bound holds for every (k requests, m abstained).

    `_expected_parts` decides before retrieval runs whether the specialists stream or
    collect, so a turn that turned out to have *more* parts than expected would stream
    a reply the composer then wrote over.
    """
    segments = [
        RequestSegment(intent=IntentLabel.FAQ_QUESTION, text=f"q{i}?")
        for i in range(faq_requests)
    ]
    answers = [
        FaqSegmentAnswer(
            position=i,
            question=f"q{i}?",
            answer_text="" if i < abstained else "an answer",
            verdict=(
                FaqVerdict.ABSTAINED_RERANK_FLOOR
                if i < abstained
                else FaqVerdict.ANSWERED
            ),
            citations=(
                []
                if i < abstained
                else [Citation(entry_id=i, chunk_index=0, chunk_text="chunk")]
            ),
        )
        for i in range(faq_requests)
    ]
    state = {
        "faq_result": FaqResult.from_segments(answers, abstention_message="constant"),
        "booking_result": None,
        "small_talk_result": None,
        "handoff_reason": None,
        "notice_required": False,
        "segments": segments,
    }

    expected = graph_module._expected_parts(
        ["answer_faq"], segments, notice_required=False
    )
    assert graph_module._actual_parts(state) <= expected


# --- Phase 1h, US5: everything that already worked still works -----------------------


@pytest.mark.parametrize(
    ("intents", "expected_source"),
    [
        ([IntentLabel.BOOKING], AnswerSource.BOOKING),
        ([IntentLabel.SMALL_TALK], AnswerSource.SMALL_TALK),
        ([IntentLabel.CALL_STAFF], AnswerSource.HAND_OFF),
    ],
)
def test_a_turn_with_no_faq_half_reports_no_request_outcome_at_all(
    seeded_entry: int, intents: list[IntentLabel], expected_source: AnswerSource
) -> None:
    # Null, never `[]`: a half that ran answered or abstained on at least one request,
    # so an empty list would describe a state no path can produce.
    with patch("chat.rag.retriever.embed_texts", fake_embed_texts):
        anthropic_client = fake_anthropic_client(["reply"], intents=intents)
        events = asyncio.run(_run_turn(anthropic_client, "a message"))

    done_event = events[-1]
    assert isinstance(done_event, ChatDoneEvent)
    assert done_event.answer_source is expected_source
    assert done_event.request_outcomes is None


def test_an_embedding_failure_still_fails_the_whole_turn(seeded_entry: int) -> None:
    # Unchanged by partial serving: a dependency that did not answer is not a corpus
    # gap, so it is never recorded as an abstention and nothing partial is delivered -
    # not even the sibling request that had already been answered.
    async def _failing_embed(*_args: object, **_kwargs: object) -> list[list[float]]:
        raise RuntimeError("voyage down")

    with (
        patch("chat.rag.retriever.embed_texts", _failing_embed),
        pytest.raises(TurnPipelineError) as raised,
    ):
        anthropic_client = fake_anthropic_client(
            [_ENTRY_CONTENT],
            segments=[
                (IntentLabel.FAQ_QUESTION, "when can I visit?"),
                (IntentLabel.FAQ_QUESTION, "what is your refund policy?"),
            ],
        )
        asyncio.run(
            _run_turn(anthropic_client, "when can I visit, and what about refunds?")
        )

    assert raised.value.pipeline_step == "embedding"


def test_every_per_request_retrieval_event_still_carries_its_fields(
    seeded_entry: int,
) -> None:
    # 1e's six events are unchanged, including 1g's `segment` binding: this phase
    # changes what is done with a request's outcome, not how it is produced or logged.
    with (
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        anthropic_client = fake_anthropic_client(
            [_ENTRY_CONTENT],
            segments=[
                (IntentLabel.FAQ_QUESTION, "when can I visit?"),
                (IntentLabel.FAQ_QUESTION, "what is your refund policy?"),
            ],
        )
        asyncio.run(
            _run_turn(anthropic_client, "when can I visit, and what about refunds?")
        )

    retrievals = [e for e in logs if e["event"] == "faq.retrieval_completed"]
    verdicts = [e for e in logs if e["event"] == "faq.verdict"]
    assert sorted(e["segment"] for e in retrievals) == [0, 1]
    assert sorted(e["segment"] for e in verdicts) == [0, 1]
    assert all("candidates" in e for e in retrievals)
    assert all("blocked_gate" in e for e in verdicts)
    # The request's text is carried once, on the classification event, and joined by
    # position - not repeated onto every per-request line.
    assert all("question" not in e for e in verdicts)
