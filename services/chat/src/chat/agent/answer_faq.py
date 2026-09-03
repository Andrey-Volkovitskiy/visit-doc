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

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam
from qdrant_client import AsyncQdrantClient
from voyageai.client_async import AsyncClient

from chat.agent.compose_answer import FaqResult
from chat.agent.escalation import EscalationRequests
from chat.agent.history import (
    bound_to_last_n_turns,
    render_silent_window,
    silent_window,
    to_claude_messages,
    to_loggable_messages,
    trailing_question,
)
from chat.core.config import get_settings
from chat.core.logging import get_logger
from chat.domain.models import EscalationReason, Message
from chat.domain.schemas import ChatDoneEvent, ChatTokenEvent, Citation
from chat.rag.groundedness import is_grounded
from chat.rag.retriever import TurnPipelineError, search_faq

_SYSTEM_PROMPT = (
    "You are a clinic assistant. Answer the visitor's question using ONLY the provided "
    "context. Do not use outside knowledge. Be concise."
)
# The abstention and the handoff are one outcome, so they are one sentence: an
# abstention that then attempted a speculative answer, or that left the patient at a
# dead end, is the failure this wording exists to prevent. It names no timeframe,
# because nothing in the system commits to one.
_ABSTENTION_MESSAGE = (
    "I don't have a confident answer to that from the clinic's documents, so I've "
    "handed the question to a staff member, who will reply in this conversation."
)


async def answer_faq(
    qdrant_client: AsyncQdrantClient,
    voyage_client: AsyncClient,
    anthropic_client: AsyncAnthropic,
    bursts: list[list[Message]],
    reply_to_message_ids: list[str],
    session_id: str,
    live_revisions: list[str],
    *,
    escalation: EscalationRequests,
    stream: bool = True,
) -> AsyncIterator[ChatTokenEvent | ChatDoneEvent | FaqResult]:
    """Retrieve context for the current turn, then stream a grounded answer or abstain.

    Args:
        bursts: The chat's full conversation history, partitioned into contiguous
            same-side runs, with the current (possibly burst-merged) patient message
            always the trailing burst - the query this turn retrieves for and answers.
            Bounded here to the last few turns before any model call.
        reply_to_message_ids: The patient message id(s) this turn is answering.
        session_id: The session this turn belongs to, and the only corpus retrieval may
            reach - carried to the search as a term of its own rather than left to the
            revisions to imply.
        live_revisions: Every revision that session currently publishes - the whole of
            what retrieval may search. Read before this function is entered, so an empty
            list provably means an empty corpus rather than a read that failed.
        escalation: This turn's collector of calls to staff. An abstention records one
            into it; nothing here writes the transition, which belongs to the end of the
            turn.
        stream: True to stream tokens and emit the terminal event; False to yield a
            single `FaqResult` for a later composing step instead.

    Yields: in streaming mode, `ChatTokenEvent`s then one `ChatDoneEvent`; in collect
        mode, exactly one `FaqResult`.

    Raises: TurnPipelineError wrapping any failure in embedding, retrieval,
        groundedness, or generation.
    """
    logger = get_logger()
    settings = get_settings()
    bounded = bound_to_last_n_turns(bursts, n=settings.CONTEXT_TURNS)
    history = to_claude_messages(bounded)
    # Both taken from the bursts, not from `history`'s last entry: a turn following a
    # silent window has two consecutive patient-sided bursts, which that render rejoins
    # into one. Reading the question off it would retrieve for - and answer - a message
    # a staff member was meant to deal with; and since the prompt below replaces that
    # entry, the window has to be carried into the prompt explicitly or it would be
    # dropped from the conversation the model reads at all.
    message = trailing_question(bounded)
    silenced = render_silent_window(silent_window(bounded))

    chunks = await search_faq(
        qdrant_client, voyage_client, message, session_id, live_revisions
    )
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
        # Recorded on the same signal that produced the abstention, at the same moment,
        # so the two can never disagree - and before any generation call, which on this
        # branch means before there is one at all. A visitor whose question the clinic
        # has no answer for is exactly who needs a person, so there is no exemption for
        # an empty corpus.
        escalation.record(EscalationReason.CORPUS_COULD_NOT_ANSWER)
        if stream:
            yield ChatDoneEvent(
                grounded=False, citations=[], message=_ABSTENTION_MESSAGE
            )
        yield FaqResult(answer_text=_ABSTENTION_MESSAGE, citations=[], grounded=False)
        return

    context = "\n\n".join(chunk.chunk_text for chunk in chunks)
    # Identical to what it has always been when nothing was silenced, so an ordinary
    # turn's prompt does not change at all.
    prompt = "\n\n".join(
        part
        for part in (f"Context:\n{context}", silenced, f"Question: {message}")
        if part
    )
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
