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
from anthropic import OverloadedError
from chat.agent import graph as graph_module
from chat.agent.escalation import HANDOFF_MESSAGE, EscalationRequests
from chat.agent.tools.registry import ToolContext
from chat.agent.tools.scheduling_tools import SCHEDULING_TOOLS
from chat.core.config import Settings
from chat.db.session import session_factory
from chat.domain.models import (
    EscalationReason,
    Message,
    MessageSender,
)
from chat.domain.schemas import ChatDoneEvent, ChatTokenEvent, IntentLabel
from chat.rag.indexing import publish_revision, remove_entry_chunks
from chat.repositories import chat_repository, faq_repository
from chat.repositories.qdrant_repository import create_client, ensure_collection
from structlog.testing import capture_logs
from ulid import ULID

from .conftest import (
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


def _tool_context(patient_id: str | None = "01PATENT000000000000000000") -> ToolContext:
    """Build the turn's ambient facts over a channel no test ever dials.

    The mocked booking loop returns plain text unless a test asks for tool calls, so
    the channel stays untouched - and the registry each node builds over this is the
    production one, so the tool names and schemas the model would see are real.

    This is also where the turn's patient lives, so a test exercising a chat with no
    patient record varies it here rather than in the graph's own state.
    """
    return ToolContext(
        channel=MagicMock(),
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
) -> list[ChatTokenEvent | ChatDoneEvent]:
    qdrant_client = create_client(Settings())
    bursts = [[_patient_message(message, id="turn-1")]]
    if live_revisions is None:
        live_revisions = list(_seeded_revisions)
    events = [
        event
        async for event in graph_module.run_turn(
            qdrant_client,
            MagicMock(),
            anthropic_client,
            bursts,
            ["turn-1"],
            _retrieving_session(),
            live_revisions,
            escalation=escalation if escalation is not None else EscalationRequests(),
            patient_name="Ada Lovelace",
            local_now=_LOCAL_NOW,
            tool_context=_tool_context(patient_id),
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
    assert done_event.grounded is True


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

    retrieved = next(e for e in logs if e["event"] == "faq.retrieved")
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
    assert done_event.grounded is None
    assert done_event.citations == []


@pytest.mark.parametrize(
    "forbidden",
    ["turn.retrieval_completed", "faq.retrieved", "turn.groundedness_verdict"],
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


@pytest.mark.parametrize("intents", [[IntentLabel.FAQ_QUESTION], [IntentLabel.UNKNOWN]])
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
