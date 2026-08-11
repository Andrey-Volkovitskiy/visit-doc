"""`answer_faq`: retrieve -> groundedness gate -> generate/stream (research.md #9).

Plain async function, no agent-framework dependency of its own — `agent/graph.py`'s
`answer_faq_node` wraps it as a LangGraph node, forwarding its yielded events via the
stream writer (research.md #1).
"""

from collections.abc import AsyncIterator
from typing import cast

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam
from qdrant_client import AsyncQdrantClient
from voyageai.client_async import AsyncClient

from chat.agent.history import to_claude_messages
from chat.core.logging import get_logger
from chat.domain.models import Message
from chat.domain.schemas import ChatDoneEvent, ChatTokenEvent, Citation
from chat.rag.groundedness import is_grounded
from chat.rag.retriever import TurnPipelineError, search_faq

_MODEL = "claude-sonnet-5"
_SYSTEM_PROMPT = (
    "You are a clinic assistant. Answer the visitor's question using ONLY the provided "
    "context. Do not use outside knowledge. Be concise."
)
_ABSTENTION_MESSAGE = "I don't have a confident answer to that."


async def answer_faq(
    qdrant_client: AsyncQdrantClient,
    voyage_client: AsyncClient,
    anthropic_client: AsyncAnthropic,
    bursts: list[list[Message]],
    reply_to_message_ids: list[str],
) -> AsyncIterator[ChatTokenEvent | ChatDoneEvent]:
    """Retrieve context for the current turn, then stream a grounded answer or abstain.

    `bursts` is the turn's conversation history, already partitioned into contiguous
    same-side runs by `history.py::split_into_bursts` (research.md #5/#9), with the
    current (possibly burst-merged) patient message always the trailing burst - the
    query this turn retrieves for and ultimately answers (research.md #6). Translated
    to Claude's `messages` format internally, via `history.py::to_claude_messages()` -
    that's this function's own implementation detail (it's the one that talks to
    `anthropic_client`), not something callers should need to know or do themselves.
    For a chat with no prior messages, `bursts` has exactly one burst: the current
    message. `reply_to_message_ids` is `history.py::derive_reply_to_message_ids()`'s
    output over that same trailing burst - the patient message id(s) this turn is
    actually answering, logged as `message_ids_unified` on `turn.completed` (so a log
    reader can tell whether a merged burst (len > 1) or a single message this turn
    answers, from the turn's outcome log alone) - `message.persisted` never carries
    it, for either the patient or the assistant message. `turn.message_received` (the
    same ids, up front) is logged by the caller in `api/chat.py`, before this function
    (and `classify_intent_node`) ever run.

    Raises: TurnPipelineError wrapping any failure in embedding, retrieval,
        groundedness, or generation (FR-005).
    """
    logger = get_logger()
    history = to_claude_messages(bursts)
    # `history`'s entries are always built with plain str content (agent/history.py).
    message = cast(str, history[-1]["content"])

    chunks = await search_faq(qdrant_client, voyage_client, message)

    try:
        grounded = is_grounded(chunks)
    except Exception as exc:
        raise TurnPipelineError("groundedness", exc) from exc
    logger.info("turn.groundedness_verdict", grounded=grounded)

    if not grounded:
        logger.info(
            "turn.completed",
            outcome="abstained",
            abstention_message=_ABSTENTION_MESSAGE,
            message_ids_unified=reply_to_message_ids,
        )
        yield ChatDoneEvent(grounded=False, citations=[], message=_ABSTENTION_MESSAGE)
        return

    context = "\n\n".join(chunk.chunk_text for chunk in chunks)
    prompt = f"Context:\n{context}\n\nQuestion: {message}"
    current_turn: MessageParam = {"role": "user", "content": prompt}
    messages = [*history[:-1], current_turn]

    answer_parts: list[str] = []
    try:
        async with anthropic_client.messages.stream(
            model=_MODEL,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=messages,
        ) as stream:
            async for event in stream:
                if event.type == "text":
                    answer_parts.append(event.text)
                    yield ChatTokenEvent(text=event.text)
    except Exception as exc:
        raise TurnPipelineError("generation", exc) from exc

    citations = [
        Citation(
            entry_id=c.faq_entry_id, chunk_index=c.chunk_index, chunk_text=c.chunk_text
        )
        for c in chunks
    ]
    logger.info(
        "turn.completed",
        outcome="grounded",
        answer_text="".join(answer_parts),
        message_ids_unified=reply_to_message_ids,
        citations=[
            {
                "entry_id": c.faq_entry_id,
                "chunk_index": c.chunk_index,
                "chunk_text": c.chunk_text,
                "score": c.score,
            }
            for c in chunks
        ],
    )
    yield ChatDoneEvent(grounded=True, citations=citations)
