"""LangGraph wrapper: `classify_intent_node -> answer_faq_node -> END` (research.md #1).

Sequential, not parallel: `classify_intent_node` completing before `answer_faq_node`
starts is deliberate, giving a future routing decision (ROADMAP Phase 1d) a graph edge
to attach to (research.md #1). Both nodes run inside the one `asyncio.Task`
`api/chat.py`'s `generation_registry` already tracks and cancels per chat - cancelling
that task cancels whichever node is currently running (research.md #2).
"""

from collections.abc import AsyncIterator
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
from chat.agent.history import bound_to_last_n_turns
from chat.core.logging import get_logger
from chat.domain.models import Message
from chat.domain.schemas import ChatDoneEvent, ChatTokenEvent, IntentLabel


class _GraphState(TypedDict):
    """Everything either node needs - read-only from each node's own perspective.

    Neither node writes a meaningful state update: `classify_intent_node`'s only
    output is its own `intent.classified` log line (FR-004 - classification never
    changes what `answer_faq_node` does this phase); `answer_faq_node` forwards its
    events via the stream writer rather than a state update (research.md #1).
    """

    bursts: list[list[Message]]
    reply_to_message_ids: list[str]


@lru_cache
def _build_graph(
    qdrant_client: AsyncQdrantClient,
    voyage_client: VoyageAsyncClient,
    anthropic_client: AsyncAnthropic,
) -> "CompiledStateGraph[_GraphState, None, _GraphState, _GraphState]":
    """Build and compile the graph, closing over its three shared clients.

    Memoized via `lru_cache`: `qdrant_client`/`voyage_client`/`anthropic_client` are
    each constructed once at app startup (main.py's lifespan) and passed in unchanged
    on every turn, so the graph's structure never actually varies call to call -
    recompiling it from scratch on every turn would be pure waste. The first call for
    a given client triple compiles and caches the `CompiledStateGraph`; every later
    call with that same triple reuses it. Safe to invoke concurrently across
    overlapping requests - a compiled LangGraph graph carries no per-invocation
    mutable state, that's the point of separating `.compile()` from `.astream()`.
    """

    async def classify_intent_node(state: _GraphState) -> None:
        """Classify the current message and log the result (FR-001/FR-003/FR-007).

        Bounds `bursts` to the last 5 turns itself (FR-006) - that's a business rule
        about how much history to consider, this node's own call to make - and hands
        `classify_intent()` the bounded bursts directly; formatting them for Claude is
        `classify_intent()`'s own concern, not this node's (Core design principles'
        Dependency Inversion rule). Always continues to `answer_faq_node` regardless
        of outcome - a failed or invalid classification call, or a failure while
        building its own bounded context, is caught here and recorded as
        `CLASSIFICATION_FAILED`, never allowed to fail the request (FR-007). The
        underlying exception is logged as `intent.classification_failed` (error level)
        right before that, so a developer can still see *why* it failed, even though
        the request itself never surfaces it.
        """
        logger = get_logger()
        try:
            bounded_bursts = bound_to_last_n_turns(state["bursts"], n=5)
            result = await classify_intent(anthropic_client, bounded_bursts)
            intents = result.intents
        except Exception as exc:  # noqa: BLE001 - a classification failure must
            # never fail the request (FR-007); it's recorded as CLASSIFICATION_FAILED
            # instead, after logging the cause for visibility.
            logger.error("intent.classification_failed", error_detail=str(exc))
            intents = [IntentLabel.CLASSIFICATION_FAILED]
        logger.info("intent.classified", intents=intents)

    async def answer_faq_node(state: _GraphState) -> None:
        """Wrap `answer_faq()`, forwarding its events via the stream writer.

        Raises: TurnPipelineError propagated from `answer_faq()` (FR-005).
        """
        writer = get_stream_writer()
        async for event in answer_faq(
            qdrant_client,
            voyage_client,
            anthropic_client,
            state["bursts"],
            state["reply_to_message_ids"],
        ):
            writer(event)

    builder = StateGraph(_GraphState)
    builder.add_node("classify_intent", classify_intent_node)
    builder.add_node("answer_faq", answer_faq_node)
    builder.add_edge(START, "classify_intent")
    builder.add_edge("classify_intent", "answer_faq")
    builder.add_edge("answer_faq", END)
    return builder.compile()


async def run_turn(
    qdrant_client: AsyncQdrantClient,
    voyage_client: VoyageAsyncClient,
    anthropic_client: AsyncAnthropic,
    bursts: list[list[Message]],
    reply_to_message_ids: list[str],
) -> AsyncIterator[ChatTokenEvent | ChatDoneEvent]:
    """Run this turn's graph: classify the intent, then answer via the FAQ path.

    `bursts` is the chat's full conversation history (oldest first), already split
    into contiguous same-side runs by `history.py::split_into_bursts` - its trailing
    burst is always patient-sided (`api/chat.py`'s single production call site
    guarantees the current, not-yet-answered patient message is folded into it).
    `classify_intent_node` bounds `bursts` to the last 5 turns itself (FR-006);
    `answer_faq_node` passes the full, unbounded `bursts` through unchanged. Neither
    node formats `bursts` for Claude itself - each callee does that internally, as its
    own implementation detail. `reply_to_message_ids` is
    `history.py::derive_reply_to_message_ids`'s output over that same trailing burst -
    forwarded to `answer_faq_node` unchanged (research.md #1).

    Raises: TurnPipelineError propagated from `answer_faq_node` (FR-005) - a
        classification failure never raises here (FR-007), only logs.
    """
    graph = _build_graph(qdrant_client, voyage_client, anthropic_client)
    state: _GraphState = {
        "bursts": bursts,
        "reply_to_message_ids": reply_to_message_ids,
    }
    async for event in graph.astream(state, stream_mode="custom"):
        # `astream`'s own return type is untyped (`dict[str, Any] | Any`) - every
        # value it yields here is actually one `writer(event)` call from
        # `answer_faq_node`, always a `ChatTokenEvent`/`ChatDoneEvent` (research.md #1).
        yield cast("ChatTokenEvent | ChatDoneEvent", event)
