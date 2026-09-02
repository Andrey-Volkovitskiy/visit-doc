"""The turn graph: a router, two specialists that may run concurrently, and a merge.

```
                       ┌──> answer_faq ─────┐
START ──> classify_intent ──> hand_off ──────├──> compose_answer ──> END
                       └──> handle_booking ─┘
```

`classify_intent` is a real router: it selects the specialist(s) the classified intents
imply, and LangGraph runs the selected ones concurrently. A message like "what should I
bring, and can I book Friday?" is ordinary phrasing, and routing it to one specialist
would answer half of it.

A single-specialist turn must not pay for the merge: the sole specialist emits its own
reply and its own terminal event, and `compose_answer` detects one result and emits
nothing but the turn's completion. Only a genuinely mixed turn makes the extra
generation call. The FAQ path streams its reply token by token; the booking path emits
its reply in one event once its tool-use loop finishes (see `handle_booking`).

The two specialists write *disjoint* state keys, so concurrent branches need no channel
reducer - that error only fires when two branches write the same key.
"""

from collections.abc import AsyncIterator
from datetime import datetime
from functools import lru_cache
from typing import TypedDict, cast

from anthropic import AsyncAnthropic
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from qdrant_client import AsyncQdrantClient
from voyageai.client_async import AsyncClient as VoyageAsyncClient

from chat.agent.answer_faq import answer_faq
from chat.agent.classify_intent import classify_intent
from chat.agent.compose_answer import (
    FaqResult,
    TurnCompletion,
    compose_answer,
    record_single_specialist_completion,
)
from chat.agent.escalation import HANDOFF_MESSAGE, EscalationRequests
from chat.agent.handle_booking import BookingResult, handle_booking
from chat.agent.history import bound_to_last_n_turns
from chat.agent.node_logging import node_span
from chat.agent.tools.registry import ToolContext, ToolRegistry
from chat.agent.tools.scheduling_tools import SCHEDULING_TOOLS
from chat.agent.tools.staff_tools import STAFF_TOOLS
from chat.core.config import get_settings
from chat.core.logging import get_logger
from chat.domain.models import EscalationReason, Message
from chat.domain.schemas import (
    AnswerSource,
    ChatDoneEvent,
    ChatTokenEvent,
    IntentLabel,
)

_ANSWER_FAQ = "answer_faq"
_HANDLE_BOOKING = "handle_booking"
_HAND_OFF = "hand_off"
_COMPOSE_ANSWER = "compose_answer"

# Which specialist each classified intent implies. Anything not listed - unknown, a
# failed classification - falls back to the FAQ path, which is today's default.
# `call_staff` is absent because it is not a specialist's job at all: see
# `_select_specialists`.
_SPECIALIST_BY_INTENT = {
    IntentLabel.FAQ_QUESTION: _ANSWER_FAQ,
    IntentLabel.BOOKING: _HANDLE_BOOKING,
}

# What the booking specialist may call - declared here, beside the node it belongs to,
# rather than assembled into one registry the whole graph shares. A node's capabilities
# are part of what that node *is*: a shared bag means a node added later silently
# inherits every tool in the system, and a model sees capabilities its own step was
# never meant to have.
#
# `escalate_to_staff` is in it because a patient can ask for a person in the middle of
# booking one ("forget it, just have someone call me"), and this is the only node with a
# tool loop to act on that. The FAQ node builds no registry at all: it makes no tool
# calls, so it is given none.
_BOOKING_TOOLS = [*SCHEDULING_TOOLS, *STAFF_TOOLS]


class _GraphState(TypedDict):
    """Everything the router and both specialists need for one turn.

    `bursts` stays the *whole* history: each node applies its own context bound, so a
    future node with a different requirement is not silently starved by a decision
    another node made.

    `faq_result` and `booking_result` are deliberately separate keys - the two branches
    can run at once, and concurrent writes to one key are what LangGraph rejects.

    `escalation` is a shared mutable object rather than a written key for the same
    reason: both specialists may record a call to staff into it, and only a channel
    reducer would let two branches write one key. Appending to it is not a state write,
    and what it resolves to does not depend on the order they appended.

    `tool_context` is the turn's ambient facts, not a registry: each node that uses
    tools builds its own over these, from its own declared set.
    """

    bursts: list[list[Message]]
    reply_to_message_ids: list[str]
    live_revisions: list[str]
    escalation: EscalationRequests
    patient_name: str
    local_now: datetime
    tool_context: ToolContext | None
    specialists: list[str]
    merge_required: bool
    faq_result: FaqResult | None
    booking_result: BookingResult | None
    handed_off: bool


def clear_graph_cache() -> None:
    """Drop every compiled graph, releasing the clients each closes over.

    Called when an app shuts down: the cache is keyed on the client objects, so without
    this the lifespan's `aclose` calls free nothing and a second lifecycle in the same
    process adds another full set.
    """
    _build_graph.cache_clear()


def _select_specialists(intents: list[IntentLabel]) -> list[str]:
    """Return the node(s) `intents` implies, in a stable order.

    `call_staff` takes the whole turn and suppresses every other label on it. A visitor
    who has asked for a person is going to get one, and the conversation falls silent
    from their next message - so answering half of what they said and then going quiet
    is worse than handing over cleanly, and booking something for a patient who has just
    asked to stop talking to a machine is worse still.

    This selects *no* specialist rather than interrupting one, so the turn still runs
    to completion: nothing is cut off mid-flight, because nothing was started.

    Never empty: a message that matches nothing still gets the FAQ path rather than no
    answer at all.
    """
    if IntentLabel.CALL_STAFF in intents:
        return [_HAND_OFF]
    selected = {
        _SPECIALIST_BY_INTENT[intent]
        for intent in intents
        if intent in _SPECIALIST_BY_INTENT
    }
    if not selected:
        return [_ANSWER_FAQ]
    return [name for name in (_ANSWER_FAQ, _HANDLE_BOOKING) if name in selected]


@lru_cache
def _build_graph(
    qdrant_client: AsyncQdrantClient,
    voyage_client: VoyageAsyncClient,
    anthropic_client: AsyncAnthropic,
) -> "CompiledStateGraph[_GraphState, None, _GraphState, _GraphState]":
    """Build and compile the graph, closing over its three shared clients.

    Memoized on the three clients: each is constructed once at app startup and passed
    in unchanged on every turn, so the graph's structure never varies call to call and
    recompiling it per turn would be pure waste. Safe to invoke concurrently across
    overlapping requests - a compiled graph carries no per-invocation state.

    The entry keys on the clients themselves, so it keeps them - and their compiled
    graph - reachable for as long as it lives. `clear_graph_cache()` is therefore part
    of shutting an app down, or a process running more than one lifecycle accumulates a
    set of closed clients per lifecycle.
    """

    async def classify_intent_node(state: _GraphState) -> dict[str, object]:
        """Classify the current message and route the turn to its specialist(s).

        Always routes somewhere: a failed or invalid classification call is caught here
        and recorded as `CLASSIFICATION_FAILED`, which falls back to the FAQ path rather
        than failing the request.
        """
        logger = get_logger()
        async with node_span("classify_intent") as span:
            try:
                bounded_bursts = bound_to_last_n_turns(
                    state["bursts"], n=get_settings().CONTEXT_TURNS
                )
                result = await classify_intent(anthropic_client, bounded_bursts)
                intents = result.intents
            except Exception as exc:  # noqa: BLE001 - a classification failure must
                # never fail the request; it's recorded as CLASSIFICATION_FAILED
                # instead, after logging the cause for visibility.
                logger.error("intent.classification_failed", error_detail=str(exc))
                intents = [IntentLabel.CLASSIFICATION_FAILED]
            logger.info("intent.classified", intents=intents)
            if IntentLabel.CALL_STAFF in intents:
                # The label *is* the decision, so nothing is asked to make it again:
                # no model call, and no dependence on whether the corpus happens to
                # ground the sentence the patient used to ask for a human. Recorded
                # like every other call to staff, and applied once the turn completes.
                state["escalation"].record(EscalationReason.PATIENT_ASKED_FOR_PERSON)

            specialists = _select_specialists(intents)
            merge_required = len(specialists) > 1
            span.set(
                intents=[str(i) for i in intents],
                specialists=specialists,
                merge_required=merge_required,
            )
        return {"specialists": specialists, "merge_required": merge_required}

    async def answer_faq_node(state: _GraphState) -> dict[str, object]:
        """Run the FAQ pipeline, streaming or collecting depending on the route.

        Raises: TurnPipelineError propagated from `answer_faq()`.
        """
        writer = get_stream_writer()
        streaming = not state["merge_required"]
        result: FaqResult | None = None
        async with node_span(_ANSWER_FAQ) as span:
            async for event in answer_faq(
                qdrant_client,
                voyage_client,
                anthropic_client,
                state["bursts"],
                state["reply_to_message_ids"],
                state["live_revisions"],
                escalation=state["escalation"],
                stream=streaming,
            ):
                # In streaming mode the events go to the patient and the trailing
                # result is kept for the completion line; in collect mode the result is
                # all there is.
                if isinstance(event, FaqResult):
                    result = event
                else:
                    writer(event)
            span.set(
                grounded=result.grounded if result else None,
                abstained=result is not None and not result.grounded,
                citation_count=len(result.citations) if result else 0,
                answer_chars=len(result.answer_text) if result else 0,
                mode="streamed" if streaming else "collected",
            )
        return {"faq_result": result}

    async def handle_booking_node(state: _GraphState) -> dict[str, object]:
        """Run the booking loop, streaming or collecting depending on the route.

        Raises: RuntimeError if the turn was routed to booking without a tool context.
        """
        writer = get_stream_writer()
        streaming = not state["merge_required"]
        context = state["tool_context"]
        result: BookingResult | None = None
        async with node_span(_HANDLE_BOOKING) as span:
            if context is None:
                raise RuntimeError("booking requires a tool context")
            registry = ToolRegistry(_BOOKING_TOOLS, context)
            async for event in handle_booking(
                anthropic_client,
                registry,
                state["bursts"],
                patient_name=state["patient_name"],
                local_now=state["local_now"].isoformat(),
                stream=streaming,
                escalation=state["escalation"],
            ):
                if isinstance(event, BookingResult):
                    result = event
                else:
                    writer(event)
            if streaming:
                # The sole specialist ends its own turn, exactly as the FAQ path does.
                # A booking reply was never retrieved against, so it carries no
                # groundedness verdict and no citations.
                writer(
                    ChatDoneEvent(
                        grounded=None,
                        citations=[],
                        answer_source=AnswerSource.BOOKING,
                    )
                )
            span.set(
                outcome=str(result.outcome) if result else None,
                appointment_id=result.appointment_id if result else None,
                iterations=result.iterations if result else 0,
                tool_calls=result.tool_calls if result else 0,
                mode="streamed" if streaming else "collected",
            )
        return {"booking_result": result}

    async def hand_off_node(state: _GraphState) -> dict[str, object]:
        """Tell the visitor a person has been fetched, and do nothing else.

        No retrieval, no embedding, no generation, no tool call - the classification
        that produced the label is the only model call this turn makes. The sentence is
        fixed because there is nothing here for a model to decide, and the router has
        already recorded the call to staff that `turn.py` applies once this completes.
        """
        writer = get_stream_writer()
        async with node_span(_HAND_OFF) as span:
            writer(ChatTokenEvent(text=HANDOFF_MESSAGE))
            writer(
                ChatDoneEvent(
                    grounded=None,
                    citations=[],
                    answer_source=AnswerSource.HAND_OFF,
                )
            )
            span.set(answer_chars=len(HANDOFF_MESSAGE))
        return {"handed_off": True}

    async def compose_answer_node(state: _GraphState) -> None:
        """Emit the turn's reply and completion, merging only when both halves ran."""
        writer = get_stream_writer()
        faq_result = state.get("faq_result")
        booking_result = state.get("booking_result")
        booking_outcome = booking_result.outcome if booking_result is not None else None
        completion = TurnCompletion()
        async with node_span(_COMPOSE_ANSWER) as span:
            if not state["merge_required"]:
                answer_text, citations, grounded, source = _single_specialist_reply(
                    faq_result, booking_result, handed_off=state["handed_off"]
                )
                record_single_specialist_completion(
                    completion,
                    answer_source=source,
                    grounded=grounded,
                    booking_outcome=booking_outcome,
                    answer_text=answer_text,
                    citations=citations,
                    reply_to_message_ids=state["reply_to_message_ids"],
                )
                span.set(
                    answer_source=str(source),
                    merged=False,
                    grounded=grounded,
                    booking_outcome=booking_outcome,
                    citation_count=len(citations),
                )
            else:
                async for event in compose_answer(
                    anthropic_client,
                    faq_result=faq_result,
                    booking_reply=(
                        booking_result.reply_text
                        if booking_result is not None
                        else None
                    ),
                    booking_outcome=booking_outcome,
                    reply_to_message_ids=state["reply_to_message_ids"],
                    completion=completion,
                ):
                    writer(event)
                span.set(
                    answer_source=str(AnswerSource.MERGED),
                    merged=True,
                    grounded=faq_result.grounded if faq_result else None,
                    booking_outcome=booking_outcome,
                    citation_count=len(faq_result.citations) if faq_result else 0,
                )
        # Emitted outside the span so the turn's terminal line follows the last
        # `node.completed` rather than sitting inside it: `turn.completed` describes
        # the whole turn, and a reader should not meet it before the node that
        # produced it has been reported closed.
        completion.emit()

    builder = StateGraph(_GraphState)
    builder.add_node("classify_intent", classify_intent_node)
    builder.add_node(_ANSWER_FAQ, answer_faq_node)
    builder.add_node(_HANDLE_BOOKING, handle_booking_node)
    builder.add_node(_HAND_OFF, hand_off_node)
    builder.add_node(_COMPOSE_ANSWER, compose_answer_node)
    builder.add_edge(START, "classify_intent")
    builder.add_conditional_edges(
        "classify_intent",
        lambda state: state["specialists"],
        [_ANSWER_FAQ, _HANDLE_BOOKING, _HAND_OFF],
    )
    builder.add_edge(_ANSWER_FAQ, _COMPOSE_ANSWER)
    builder.add_edge(_HANDLE_BOOKING, _COMPOSE_ANSWER)
    builder.add_edge(_HAND_OFF, _COMPOSE_ANSWER)
    builder.add_edge(_COMPOSE_ANSWER, END)
    return builder.compile()


def _single_specialist_reply(
    faq_result: FaqResult | None,
    booking_result: BookingResult | None,
    *,
    handed_off: bool = False,
) -> tuple[str, list[dict[str, object]], bool | None, AnswerSource]:
    """Describe the reply a single node already streamed.

    Returns: its text, its citations as logged, its groundedness verdict (None for a
        booking reply or a handoff, neither of which was retrieved against), and which
        node produced it.
    """
    if handed_off:
        return HANDOFF_MESSAGE, [], None, AnswerSource.HAND_OFF
    if booking_result is not None:
        return booking_result.reply_text, [], None, AnswerSource.BOOKING
    if faq_result is not None:
        return (
            faq_result.answer_text,
            faq_result.scored_citations(),
            faq_result.grounded,
            AnswerSource.FAQ,
        )
    return "", [], None, AnswerSource.FAQ


async def run_turn(
    qdrant_client: AsyncQdrantClient,
    voyage_client: VoyageAsyncClient,
    anthropic_client: AsyncAnthropic,
    bursts: list[list[Message]],
    reply_to_message_ids: list[str],
    live_revisions: list[str],
    *,
    escalation: EscalationRequests,
    patient_name: str,
    local_now: datetime,
    tool_context: ToolContext | None,
) -> AsyncIterator[ChatTokenEvent | ChatDoneEvent]:
    """Run this turn's graph: classify, fan out to the specialist(s), then compose.

    Args:
        bursts: The chat's full conversation history (oldest first), split into
            contiguous same-side runs, with the trailing burst always patient-sided.
        reply_to_message_ids: The patient message id(s) the trailing burst represents.
        live_revisions: Every revision this session publishes, read before the graph is
            entered so an empty list cannot be confused with a failed read.
        escalation: The turn's collector of calls to staff, filled by whichever
            specialist decides a person is needed and applied by the caller once this
            has completed.
        tool_context: The turn's ambient facts, or None when scheduling is not wired
            up. It already carries the turn's session, patient and clock, so none of
            those are threaded through the graph state as a second copy that could
            drift. Each node builds its own registry over it, from its own tool set.

    Raises: TurnPipelineError propagated from `answer_faq_node` - a classification
        failure never raises here, only logs.
    """
    graph = _build_graph(qdrant_client, voyage_client, anthropic_client)
    state: _GraphState = {
        "bursts": bursts,
        "reply_to_message_ids": reply_to_message_ids,
        "live_revisions": live_revisions,
        "escalation": escalation,
        "patient_name": patient_name,
        "local_now": local_now,
        "tool_context": tool_context,
        "specialists": [],
        "merge_required": False,
        "faq_result": None,
        "booking_result": None,
        "handed_off": False,
    }
    async for event in graph.astream(state, stream_mode="custom"):
        # `astream`'s own return type is untyped (`dict[str, Any] | Any`) - every value
        # it yields here is one `writer(event)` call from a node, always a
        # `ChatTokenEvent`/`ChatDoneEvent`.
        yield cast("ChatTokenEvent | ChatDoneEvent", event)
