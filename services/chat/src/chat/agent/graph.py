"""The turn graph: a router, three specialists, a hand-off, and a merge.

```
                          ┌──> answer_faq ─────┐
                          ├──> handle_booking ─┤
START ──> classify_intent ┤                    ├──> compose_answer ──> END
                          ├──> small_talk ─────┤
                          └──> hand_off ───────┘
```

`classify_intent` is a real router: it selects the specialist(s) the classified intents
imply, and LangGraph runs the selected ones concurrently. A message like "what should I
bring, and can I book Friday?" is ordinary phrasing, and routing it to one specialist
would answer half of it.

Only `answer_faq` and `handle_booking` ever run together. `small_talk` answers a message
that asked for nothing and is dropped whenever any other intent applies, so it always
runs alone; `hand_off` writes one constant for the cause that stopped the turn and
suppresses every other label, so it does too (spec 009 FR-008, FR-046).

A single-specialist turn must not pay for the merge: the sole specialist emits its own
reply and its own terminal event, and `compose_answer` detects one result and emits
nothing but the turn's completion. Only a genuinely mixed turn makes the extra
generation call. The FAQ path streams its reply token by token; the booking path emits
its reply in one event once its tool-use loop finishes (see `handle_booking`).

Every specialist writes a *disjoint* state key, so concurrent branches need no channel
reducer - that error only fires when two branches write the same key. Only two of them
can be concurrent, but the rule is kept for all of them: a node that writes its own key
cannot become the exception later.
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
from chat.agent.classify_intent import ClassificationFailedError, classify_intent
from chat.agent.compose_answer import (
    FaqResult,
    TurnCompletion,
    compose_answer,
    record_single_specialist_completion,
)
from chat.agent.escalation import HANDOFF_TEXT, EscalationRequests
from chat.agent.handle_booking import BookingResult, handle_booking
from chat.agent.history import bound_to_last_n_turns
from chat.agent.node_logging import node_span
from chat.agent.small_talk import SmallTalkResult, answer_small_talk
from chat.agent.tools.registry import ToolContext, ToolRegistry
from chat.agent.tools.scheduling_tools import SCHEDULING_TOOLS
from chat.agent.tools.staff_tools import STAFF_TOOLS
from chat.clients.anthropic_failure import AnthropicFailure
from chat.core.config import get_settings
from chat.core.logging import get_logger
from chat.domain.models import EscalationReason, Message
from chat.domain.schemas import (
    AnswerSource,
    ChatDoneEvent,
    ChatTokenEvent,
    FaqVerdict,
    IntentLabel,
)

_ANSWER_FAQ = "answer_faq"
_HANDLE_BOOKING = "handle_booking"
_SMALL_TALK = "small_talk"
_HAND_OFF = "hand_off"
_COMPOSE_ANSWER = "compose_answer"

# Which specialist each classified intent implies. Anything not listed - unknown, a
# failed classification - falls back to the FAQ path, which is today's default.
# `call_staff` is absent because it is not a specialist's job at all: see
# `_select_specialists`.
_SPECIALIST_BY_INTENT = {
    IntentLabel.FAQ_QUESTION: _ANSWER_FAQ,
    IntentLabel.BOOKING: _HANDLE_BOOKING,
    IntentLabel.SMALL_TALK: _SMALL_TALK,
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

    `handoff_reason` is the cause the hand-off node writes its constant for, rather than
    a bool: four causes now end a turn that way, and "it happened" cannot say which
    sentence the patient is owed.

    `notice_required` says the turn also carried a request the assistant may not serve,
    alongside one it can. The notice is then the composer's to render, not a constant to
    staple on - which is why this is a flag read by the merge rather than a fifth
    hand-off.
    """

    bursts: list[list[Message]]
    reply_to_message_ids: list[str]
    session_id: str
    live_revisions: list[str]
    escalation: EscalationRequests
    patient_name: str
    local_now: datetime
    tool_context: ToolContext | None
    specialists: list[str]
    merge_required: bool
    faq_result: FaqResult | None
    booking_result: BookingResult | None
    small_talk_result: SmallTalkResult | None
    handoff_reason: EscalationReason | None
    notice_required: bool


def clear_graph_cache() -> None:
    """Drop every compiled graph, releasing the clients each closes over.

    Called when an app shuts down: the cache is keyed on the client objects, so without
    this the lifespan's `aclose` calls free nothing and a second lifecycle in the same
    process adds another full set.
    """
    _build_graph.cache_clear()


# Which cause a label hands the turn over for. Ordered by `_PRECEDENCE`'s logic at the
# routing layer too: the first match takes the whole turn, so a message carrying both an
# urgent condition and a booking is never partly answered before it falls silent.
_HANDOFF_REASON_BY_INTENT: tuple[tuple[IntentLabel, EscalationReason], ...] = (
    (IntentLabel.URGENT_CONDITION, EscalationReason.URGENT_CONDITION),
    (IntentLabel.DISTRESS, EscalationReason.DISTRESS),
    (IntentLabel.CALL_STAFF, EscalationReason.PATIENT_ASKED_FOR_PERSON),
    (
        IntentLabel.BOOKING_FOR_ANOTHER,
        EscalationReason.BOOKING_FOR_ANOTHER_PERSON,
    ),
    # Last: a request the assistant may not serve hands over only when it is the whole
    # message. Beside something servable it becomes a notice instead (FR-022d).
    (IntentLabel.UNKNOWN, EscalationReason.NOT_AUTHORIZED),
)


def _handoff_reasons(intents: list[IntentLabel]) -> list[EscalationReason]:
    """Return every cause these labels call a person for, strongest first.

    All of them, not just the winner: precedence decides the one mark the message
    carries and whether the conversation falls silent, and the collector keeps the rest
    so `apply_escalation` still writes a line per call (FR-047). A message that is both
    an emergency and a plea for a human was both, and a record naming only
    `urgent_condition` has thrown away something a person reviewing it would want.
    """
    return [reason for intent, reason in _HANDOFF_REASON_BY_INTENT if intent in intents]


def _first(reasons: list[EscalationReason]) -> EscalationReason | None:
    """Return the strongest cause, or None when the turn hands over for nothing."""
    return reasons[0] if reasons else None


def _handoff_reason(intents: list[IntentLabel]) -> EscalationReason | None:
    """Return the cause this turn hands over for, or None if it answers normally.

    The strongest of `_handoff_reasons()`, which is what routing needs: one node, one
    sentence. The rest are recorded, never routed to.
    """
    return _first(_handoff_reasons(intents))


def _select_specialists(intents: list[IntentLabel]) -> list[str]:
    """Return the node(s) `intents` implies, in a stable order.

    A cause that hands the turn to a person takes the whole turn and suppresses every
    other label on it - an urgent condition, evident distress, a request for a human, a
    booking for someone else, or (when nothing servable accompanies it) a request the
    assistant is not authorized to serve. A visitor who is going to get a person gets
    one, and for the first four the conversation falls silent from their next message -
    so answering half of what they said and then going quiet is worse than handing over
    cleanly, and booking something for a patient the turn is about to stop talking to is
    worse still (spec 009 FR-046).

    The exception is a not-authorized request *beside* something servable: that one owes
    a notice rather than a hand-off, so the servable half still runs and the merge step
    says the rest went to staff (FR-022d).

    This selects *no* specialist rather than interrupting one, so the turn still runs
    to completion: nothing is cut off mid-flight, because nothing was started.

    Never empty: a message that matches nothing still gets the FAQ path rather than no
    answer at all.
    """
    stopping = _handoff_reason(intents)
    if stopping is not None and stopping is not EscalationReason.NOT_AUTHORIZED:
        return [_HAND_OFF]
    selected = {
        _SPECIALIST_BY_INTENT[intent]
        for intent in intents
        if intent in _SPECIALIST_BY_INTENT
    }
    if stopping is EscalationReason.NOT_AUTHORIZED:
        # A request the assistant may not serve is another intent like any other, so
        # the pleasantry is dropped here too (FR-008). Without this the turn routed to
        # the small-talk node *and* owed a notice, and since that node has no collect
        # mode it streamed a reply the composer then streamed a second one over.
        selected.discard(_SMALL_TALK)
        if not selected:
            # Nothing servable in the message: the notice is the whole reply, so it is
            # a hand-off like the other stopping causes - no retrieval, no generation.
            return [_HAND_OFF]
    if not selected:
        return [_ANSWER_FAQ]
    if selected == {_SMALL_TALK}:
        return [_SMALL_TALK]
    # Small talk alongside anything else is dropped, never merged: the specialist
    # answering the real request absorbs the pleasantry, and stitching "You're welcome!"
    # onto a booking confirmation buys nothing (spec 009 FR-008).
    selected.discard(_SMALL_TALK)
    return [name for name in (_ANSWER_FAQ, _HANDLE_BOOKING) if name in selected]


def _record_classification_failure(exc: Exception) -> None:
    """Record a failed classification, raising the outage alert only if it was one.

    This entry is the whole record such a failure leaves. The turn does not fail on it
    - it falls back to the FAQ path - so no `TurnPipelineError` is raised and nothing
    downstream reports it; and against a corpus that cannot answer the FAQ path
    abstains before generating, so the failed call is the only model call the turn
    made. Without the critical event here, a model API that is down is invisible for
    exactly that turn.

    Only a failure that names the API as unreachable raises it. A response that came
    back and would not parse is the API answering, and an alert that fires for that is
    one an operator learns to ignore. A timeout is neither: it says the answer did not
    arrive, not that the request went unserved, so it is recorded under its own
    `failure` and raises nothing - the same suppression the scheduling client already
    applies to a `DEADLINE_EXCEEDED`.
    """
    failure = (
        exc.failure
        if isinstance(exc, ClassificationFailedError)
        else AnthropicFailure.ANSWERED
    )
    unreachable = failure is AnthropicFailure.UNREACHABLE
    logger = get_logger()
    logger.error(
        "intent.classification_failed",
        error_detail=str(exc),
        failure=failure.value,
        dependency_unreachable=unreachable,
    )
    if unreachable:
        logger.critical(
            "critical.dependency_unreachable",
            dependency="anthropic_api",
            error_detail=str(exc),
        )


@lru_cache
def _build_graph(
    qdrant_client: AsyncQdrantClient,
    voyage_client: VoyageAsyncClient,
    rerank_client: VoyageAsyncClient,
    anthropic_client: AsyncAnthropic,
) -> "CompiledStateGraph[_GraphState, None, _GraphState, _GraphState]":
    """Build and compile the graph, closing over its four shared clients.

    Memoized on the four clients: each is constructed once at app startup and passed
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
                _record_classification_failure(exc)
                intents = [IntentLabel.CLASSIFICATION_FAILED]
            logger.info("intent.classified", intents=intents)
            # The label *is* the decision, so nothing is asked to make it again: no
            # model call, and no dependence on whether the corpus happens to ground the
            # sentence the patient used. Recorded like every other call to staff, and
            # applied once the turn completes.
            specialists = _select_specialists(intents)
            # `unknown` alongside something servable is a notice owed, not a hand-off:
            # the servable half still runs, and the composer says the rest went to
            # staff (FR-022d). Alone, it is the whole turn.
            notice_required = IntentLabel.UNKNOWN in intents and specialists != [
                _HAND_OFF
            ]
            reasons = _handoff_reasons(intents)
            for reason in reasons:
                state["escalation"].record(reason)
            # The turn hands over for the strongest of them - unless the only cause is
            # a request the assistant may not serve *and* something servable ran, in
            # which case the notice is the composer's to render (FR-022d).
            handoff_reason = None if notice_required else _first(reasons)

            merge_required = len(specialists) > 1 or notice_required
            span.set(
                intents=[str(i) for i in intents],
                specialists=specialists,
                merge_required=merge_required,
                stopping_cause=(
                    handoff_reason.value if handoff_reason is not None else None
                ),
                notice_required=notice_required,
            )
        return {
            "specialists": specialists,
            "merge_required": merge_required,
            "handoff_reason": handoff_reason,
            "notice_required": notice_required,
        }

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
                rerank_client,
                anthropic_client,
                state["bursts"],
                state["reply_to_message_ids"],
                state["session_id"],
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
                faq_verdict=result.verdict.value if result else None,
                abstained=result is not None and not result.verdict.answered,
                citation_count=len(result.citations) if result else 0,
                answer_chars=len(result.answer_text) if result else 0,
                # The half's own text, not the turn's. On a merged turn `turn.completed`
                # carries only what the composing model wrote, so without this the two
                # specialists' actual words - the ones being merged - appear in no
                # record at all, and a bad merge cannot be told from a bad half.
                answer_text=result.answer_text if result else None,
                mode="streamed" if streaming else "collected",
            )
        return {"faq_result": result}

    async def handle_booking_node(state: _GraphState) -> dict[str, object]:
        """Run the booking loop, streaming or collecting depending on the route.

        Raises: RuntimeError if the turn was routed to booking without a tool context,
            or TurnPipelineError propagated from `handle_booking()`.
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
                # FAQ verdict and no citations.
                writer(
                    ChatDoneEvent(
                        faq_verdict=None,
                        citations=[],
                        answer_source=AnswerSource.BOOKING,
                    )
                )
            span.set(
                outcome=str(result.outcome) if result else None,
                appointment_id=result.appointment_id if result else None,
                iterations=result.iterations if result else 0,
                tool_calls=result.tool_calls if result else 0,
                answer_chars=len(result.reply_text) if result else 0,
                answer_text=result.reply_text if result else None,
                mode="streamed" if streaming else "collected",
            )
        return {"booking_result": result}

    async def small_talk_node(state: _GraphState) -> dict[str, object]:
        """Answer a message that asked for nothing, and call nobody.

        Always streaming: small talk is dropped whenever another intent applies, so
        this node only ever runs alone and is never merged.

        Raises: TurnPipelineError propagated from `answer_small_talk()`.
        """
        writer = get_stream_writer()
        result: SmallTalkResult | None = None
        async with node_span(_SMALL_TALK) as span:
            async for event in answer_small_talk(anthropic_client, state["bursts"]):
                if isinstance(event, SmallTalkResult):
                    result = event
                else:
                    writer(event)
            writer(
                ChatDoneEvent(
                    faq_verdict=None,
                    citations=[],
                    answer_source=AnswerSource.SMALL_TALK,
                )
            )
            span.set(
                answer_chars=len(result.reply_text) if result else 0,
                answer_text=result.reply_text if result else None,
            )
        return {"small_talk_result": result}

    async def hand_off_node(state: _GraphState) -> dict[str, object]:
        """Tell the visitor a person has this, and do nothing else.

        No retrieval, no embedding, no generation, no tool call - the classification
        that produced the label is the only model call this turn makes. The sentence is
        fixed because there is nothing here for a model to decide, and the router has
        already recorded the call to staff that `turn.py` applies once this completes.

        One node for every cause that ends a turn this way, keyed by the cause the
        router put in the state: the four texts differ, and nothing else about the turn
        does (spec 009 FR-043).

        Raises: RuntimeError if the turn reached this node with no cause, which would
            mean the router selected it without deciding why - a bug, not a state a
            patient should be answered from.
        """
        writer = get_stream_writer()
        reason = state["handoff_reason"]
        if reason is None:
            raise RuntimeError("hand-off requires the cause it is handing off for")
        text = HANDOFF_TEXT[reason]
        async with node_span(_HAND_OFF) as span:
            writer(ChatTokenEvent(text=text))
            writer(
                ChatDoneEvent(
                    faq_verdict=None,
                    citations=[],
                    answer_source=AnswerSource.HAND_OFF,
                )
            )
            span.set(cause=reason.value, answer_chars=len(text), answer_text=text)
        return {}

    async def compose_answer_node(state: _GraphState) -> None:
        """Emit the turn's reply and completion, merging only when both halves ran.

        Raises: TurnPipelineError propagated from `compose_answer()` on a merged turn.
        """
        writer = get_stream_writer()
        faq_result = state.get("faq_result")
        booking_result = state.get("booking_result")
        booking_outcome = booking_result.outcome if booking_result is not None else None
        completion = TurnCompletion()
        async with node_span(_COMPOSE_ANSWER) as span:
            if not state["merge_required"]:
                answer_text, citations, verdict, source = _single_specialist_reply(
                    faq_result,
                    booking_result,
                    state.get("small_talk_result"),
                    handoff_reason=state["handoff_reason"],
                )
                record_single_specialist_completion(
                    completion,
                    answer_source=source,
                    verdict=verdict,
                    booking_outcome=booking_outcome,
                    answer_text=answer_text,
                    citations=citations,
                    reply_to_message_ids=state["reply_to_message_ids"],
                )
                span.set(
                    answer_source=str(source),
                    merged=False,
                    faq_verdict=verdict.value if verdict is not None else None,
                    booking_outcome=booking_outcome,
                    citation_count=len(citations),
                    answer_chars=len(answer_text),
                    # The same text the specialist node above already logged: this node
                    # passed it through unchanged, and that is what the field says.
                    answer_text=answer_text,
                )
            else:
                # Accumulated from the tokens on their way to the patient rather than
                # read back off `completion`: what this node returned is what it wrote
                # to the stream, and the two are then the same string by construction.
                composed_parts: list[str] = []
                async for event in compose_answer(
                    anthropic_client,
                    notice_required=state["notice_required"],
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
                    if isinstance(event, ChatTokenEvent):
                        composed_parts.append(event.text)
                    writer(event)
                composed_text = "".join(composed_parts)
                span.set(
                    answer_source=str(AnswerSource.MERGED),
                    merged=True,
                    faq_verdict=faq_result.verdict.value if faq_result else None,
                    booking_outcome=booking_outcome,
                    citation_count=len(faq_result.citations) if faq_result else 0,
                    answer_chars=len(composed_text),
                    answer_text=composed_text,
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
    builder.add_node(_SMALL_TALK, small_talk_node)
    builder.add_node(_HAND_OFF, hand_off_node)
    builder.add_node(_COMPOSE_ANSWER, compose_answer_node)
    builder.add_edge(START, "classify_intent")
    builder.add_conditional_edges(
        "classify_intent",
        lambda state: state["specialists"],
        [_ANSWER_FAQ, _HANDLE_BOOKING, _SMALL_TALK, _HAND_OFF],
    )
    builder.add_edge(_ANSWER_FAQ, _COMPOSE_ANSWER)
    builder.add_edge(_HANDLE_BOOKING, _COMPOSE_ANSWER)
    builder.add_edge(_SMALL_TALK, _COMPOSE_ANSWER)
    builder.add_edge(_HAND_OFF, _COMPOSE_ANSWER)
    builder.add_edge(_COMPOSE_ANSWER, END)
    return builder.compile()


def _single_specialist_reply(
    faq_result: FaqResult | None,
    booking_result: BookingResult | None,
    small_talk_result: SmallTalkResult | None = None,
    *,
    handoff_reason: EscalationReason | None = None,
) -> tuple[str, list[dict[str, object]], FaqVerdict | None, AnswerSource]:
    """Describe the reply a single node already streamed.

    Returns: its text, its citations as logged, its FAQ verdict (None for a booking
        reply or a handoff, neither of which was retrieved against), and which node
        produced it.
    """
    if handoff_reason is not None:
        return HANDOFF_TEXT[handoff_reason], [], None, AnswerSource.HAND_OFF
    if small_talk_result is not None:
        return small_talk_result.reply_text, [], None, AnswerSource.SMALL_TALK
    if booking_result is not None:
        return booking_result.reply_text, [], None, AnswerSource.BOOKING
    if faq_result is not None:
        return (
            faq_result.answer_text,
            faq_result.scored_citations(),
            faq_result.verdict,
            AnswerSource.FAQ,
        )
    return "", [], None, AnswerSource.FAQ


async def run_turn(
    qdrant_client: AsyncQdrantClient,
    voyage_client: VoyageAsyncClient,
    rerank_client: VoyageAsyncClient,
    anthropic_client: AsyncAnthropic,
    bursts: list[list[Message]],
    reply_to_message_ids: list[str],
    session_id: str,
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
        session_id: The session this turn belongs to, and the corpus retrieval is scoped
            to. Held in the state rather than read off `tool_context`, which is None
            whenever scheduling is not wired up - what retrieval may reach must not
            depend on whether this deployment has a scheduler.
        live_revisions: Every revision that session publishes, read before the graph is
            entered so an empty list cannot be confused with a failed read.
        escalation: The turn's collector of calls to staff, filled by whichever
            specialist decides a person is needed and applied by the caller once this
            has completed.
        tool_context: The turn's ambient facts, or None when scheduling is not wired
            up. Each node builds its own registry over it, from its own tool set.

    Raises: TurnPipelineError propagated from whichever node made the failing call -
        `answer_faq_node`, `handle_booking_node` or `compose_answer_node` - and
        RuntimeError from `handle_booking_node` without a tool context. A
        classification failure never raises here, only logs.
    """
    graph = _build_graph(qdrant_client, voyage_client, rerank_client, anthropic_client)
    state: _GraphState = {
        "bursts": bursts,
        "reply_to_message_ids": reply_to_message_ids,
        "session_id": session_id,
        "live_revisions": live_revisions,
        "escalation": escalation,
        "patient_name": patient_name,
        "local_now": local_now,
        "tool_context": tool_context,
        "specialists": [],
        "merge_required": False,
        "faq_result": None,
        "booking_result": None,
        "small_talk_result": None,
        "handoff_reason": None,
        "notice_required": False,
    }
    async for event in graph.astream(state, stream_mode="custom"):
        # `astream`'s own return type is untyped (`dict[str, Any] | Any`) - every value
        # it yields here is one `writer(event)` call from a node, always a
        # `ChatTokenEvent`/`ChatDoneEvent`.
        yield cast("ChatTokenEvent | ChatDoneEvent", event)
