"""`answer_faq`: retrieve -> groundedness gate -> generate/stream.

Plain async function, no agent-framework dependency of its own - `agent/graph.py`'s
`answer_faq_node` wraps it as a LangGraph node, forwarding its yielded events via the
stream writer.

Two modes, one pipeline. Streaming mode is the FAQ path exactly as it has always been:
tokens go straight to the patient and the terminal event is this function's own. Collect
mode runs when another specialist also ran, and produces a result for the composing step
instead of emitting anything - retrieval, the groundedness gate, and how citations are
derived are identical either way.
"""

from collections.abc import AsyncIterator
from typing import cast

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam
from qdrant_client import AsyncQdrantClient
from voyageai.client_async import AsyncClient

from chat.agent.compose_answer import FaqResult
from chat.agent.history import (
    bound_to_last_n_turns,
    to_claude_messages,
    to_loggable_messages,
)
from chat.core.config import get_settings
from chat.core.logging import get_logger
from chat.domain.models import Message
from chat.domain.schemas import ChatDoneEvent, ChatTokenEvent, Citation
from chat.rag.groundedness import is_grounded
from chat.rag.retriever import TurnPipelineError, search_faq

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
    *,
    stream: bool = True,
) -> AsyncIterator[ChatTokenEvent | ChatDoneEvent | FaqResult]:
    """Retrieve context for the current turn, then stream a grounded answer or abstain.

    Args:
        bursts: The chat's full conversation history, partitioned into contiguous
            same-side runs, with the current (possibly burst-merged) patient message
            always the trailing burst - the query this turn retrieves for and answers.
            Bounded here to the last few turns before any model call.
        reply_to_message_ids: The patient message id(s) this turn is answering.
        stream: True to stream tokens and emit the terminal event; False to yield a
            single `FaqResult` for a later composing step instead.

    Yields: in streaming mode, `ChatTokenEvent`s then one `ChatDoneEvent`; in collect
        mode, exactly one `FaqResult`.

    Raises: TurnPipelineError wrapping any failure in embedding, retrieval,
        groundedness, or generation.
    """
    logger = get_logger()
    settings = get_settings()
    history = to_claude_messages(
        bound_to_last_n_turns(bursts, n=settings.CONTEXT_TURNS)
    )
    # `history`'s entries are always built with plain str content (agent/history.py).
    message = cast(str, history[-1]["content"])

    chunks = await search_faq(qdrant_client, voyage_client, message)
    logger.info(
        "faq.retrieved",
        chunk_count=len(chunks),
        top_score=chunks[0].score if chunks else None,
        entry_ids=[c.faq_entry_id for c in chunks],
    )

    try:
        grounded = is_grounded(chunks)
    except Exception as exc:
        raise TurnPipelineError("groundedness", exc) from exc
    logger.info("turn.groundedness_verdict", grounded=grounded)

    if not grounded:
        if stream:
            yield ChatDoneEvent(
                grounded=False, citations=[], message=_ABSTENTION_MESSAGE
            )
        yield FaqResult(answer_text=_ABSTENTION_MESSAGE, citations=[], grounded=False)
        return

    context = "\n\n".join(chunk.chunk_text for chunk in chunks)
    prompt = f"Context:\n{context}\n\nQuestion: {message}"
    current_turn: MessageParam = {"role": "user", "content": prompt}
    messages = [*history[:-1], current_turn]

    # The retrieved context reaches the model inside this turn's own message, so the
    # logged conversation is also the record of what was retrieved for it.
    logger.debug("faq.model_request", messages=to_loggable_messages(messages))

    answer_parts: list[str] = []
    try:
        async with anthropic_client.messages.stream(
            model=settings.GENERATION_MODEL,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=messages,
        ) as stream_response:
            async for event in stream_response:
                if event.type == "text":
                    answer_parts.append(event.text)
                    if stream:
                        yield ChatTokenEvent(text=event.text)
    except Exception as exc:
        raise TurnPipelineError("generation", exc) from exc

    citations = [
        Citation(
            entry_id=c.faq_entry_id, chunk_index=c.chunk_index, chunk_text=c.chunk_text
        )
        for c in chunks
    ]
    scores = [c.score for c in chunks]
    if stream:
        yield ChatDoneEvent(grounded=True, citations=citations)
        yield FaqResult(
            answer_text="".join(answer_parts),
            citations=citations,
            grounded=True,
            chunk_scores=scores,
        )
        return
    yield FaqResult(
        answer_text="".join(answer_parts),
        citations=citations,
        grounded=True,
        chunk_scores=scores,
    )
