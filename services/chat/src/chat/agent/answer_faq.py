"""`answer_faq`: retrieve -> similarity gate -> rerank -> rerank gate -> generate.

Plain async function, no agent-framework dependency of its own - `agent/graph.py`'s
`answer_faq_node` wraps it as a LangGraph node, forwarding its yielded events via the
stream writer.

Two modes, one pipeline. Streaming mode sends tokens straight to the patient and the
terminal event is this function's own. Collect mode runs when another specialist also
ran, and produces a result for the composing step instead of emitting anything -
retrieval, both gates, and how citations are derived are identical either way.

The gates are pure and live in `rag/pipeline.py`; the reranking call and its deadline
live in `rag/reranking.py`. What is left here is the order they run in, what the prompt
is built from, and what the turn records.
"""

from collections.abc import AsyncIterator
from enum import StrEnum

from anthropic import AsyncAnthropic
from qdrant_client import AsyncQdrantClient
from voyageai.client_async import AsyncClient

from chat.agent.compose_answer import FaqResult
from chat.agent.escalation import EscalationRequests
from chat.agent.history import (
    bound_to_last_n_turns,
    render_opening_clinic,
    render_silent_window,
    replace_trailing_entry,
    silent_window,
    to_claude_messages,
    to_loggable_messages,
    trailing_question,
)
from chat.core.config import get_settings
from chat.core.errors import TurnPipelineError
from chat.core.logging import get_logger
from chat.domain.models import EscalationReason, Message
from chat.domain.schemas import ChatDoneEvent, ChatTokenEvent, Citation, FaqVerdict
from chat.rag.pipeline import (
    PipelineOutcome,
    ScoredChunk,
    apply_rerank_gate,
    apply_similarity_gate,
    decide,
)
from chat.rag.reranking import rerank_chunks
from chat.rag.retriever import search_faq

_SYSTEM_PROMPT = (
    "You are a clinic assistant. Answer the visitor's question using ONLY the provided "
    "context. Do not use outside knowledge. Be concise."
)
# The abstention and the handoff are one outcome, so they are one sentence: an
# abstention that then attempted a speculative answer, or that left the patient at a
# dead end, is the failure this wording exists to prevent. The closing invitation is
# not politeness: a corpus gap raises attention without silencing the conversation, so
# the patient really may go on asking while staff follow up, and the sentence has to
# say so or the silence it implies is a lie about the state.
#
# One message for all four abstentions. Which gate stopped the turn is a fact about
# the clinic's corpus, not about the patient's question, and telling them apart here
# would be describing the system's internals to someone who asked about a visit.
_ABSTENTION_MESSAGE = (
    "I don't have that information in the clinic's knowledge base, so I've forwarded "
    "your question to our staff to ensure you get an accurate answer. They'll follow "
    "up with you shortly. Feel free to ask if you need help with anything else in the "
    "meantime!"
)


async def answer_faq(
    qdrant_client: AsyncQdrantClient,
    voyage_client: AsyncClient,
    rerank_client: AsyncClient,
    anthropic_client: AsyncAnthropic,
    bursts: list[list[Message]],
    reply_to_message_ids: list[str],
    session_id: str,
    live_revisions: list[str],
    *,
    escalation: EscalationRequests,
    stream: bool = True,
) -> AsyncIterator[ChatTokenEvent | ChatDoneEvent | FaqResult]:
    """Retrieve context for the current turn, then answer from it or abstain.

    Args:
        rerank_client: The reranking client, separate from `voyage_client` so its
            deadline bounds the whole call however the embedding client is configured
            to retry.
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

    Raises: TurnPipelineError wrapping any failure in embedding, retrieval or
        generation. A reranking failure is deliberately not among them: it degrades the
        answer rather than failing the turn.
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
    # dropped from the conversation the model reads at all. The clinic's own opening
    # words are carried for the same reason - see `replace_trailing_entry`.
    message = trailing_question(bounded)
    silenced = render_silent_window(silent_window(bounded))
    opening_clinic = render_opening_clinic(bounded)

    outcome = await _run_pipeline(
        qdrant_client,
        voyage_client,
        rerank_client,
        message,
        session_id,
        live_revisions,
    )

    if not outcome.verdict.answered:
        # Recorded on the same signal that produced the abstention, at the same moment,
        # so the two can never disagree - and before any generation call, which on this
        # branch means before there is one at all. A visitor whose question the clinic
        # has no answer for is exactly who needs a person, so there is no exemption for
        # an empty corpus, and no distinction between the three gates: all three mean
        # the corpus could not answer.
        escalation.record(EscalationReason.CORPUS_COULD_NOT_ANSWER)
        if stream:
            yield ChatDoneEvent(
                faq_verdict=outcome.verdict, citations=[], message=_ABSTENTION_MESSAGE
            )
        yield FaqResult(
            answer_text=_ABSTENTION_MESSAGE, citations=[], verdict=outcome.verdict
        )
        return

    survivors = outcome.survivors
    context = "\n\n".join(chunk.chunk_text for chunk in survivors)
    # Identical to what it has always been when nothing was silenced, so an ordinary
    # turn's prompt does not change at all.
    prompt = "\n\n".join(
        part
        for part in (f"Context:\n{context}", silenced, f"Question: {message}")
        if part
    )
    # Not `[*history[:-1], current_turn]`: when the whole window renders as one entry,
    # the entry this prompt replaces is the one carrying the clinic's opening words.
    messages = replace_trailing_entry(history, prompt, opening_clinic=opening_clinic)

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
        for c in survivors
    ]
    if stream:
        yield ChatDoneEvent(faq_verdict=outcome.verdict, citations=citations)
    yield FaqResult(
        answer_text="".join(answer_parts),
        citations=citations,
        verdict=outcome.verdict,
        scored_chunks=survivors,
    )


async def _run_pipeline(
    qdrant_client: AsyncQdrantClient,
    voyage_client: AsyncClient,
    rerank_client: AsyncClient,
    message: str,
    session_id: str,
    live_revisions: list[str],
) -> PipelineOutcome:
    """Retrieve, gate, rerank, gate again, and return the turn's outcome.

    Each stage logs what it saw and what it decided, so a threshold can be argued about
    afterwards from the log alone - including the candidates a gate rejected, which is
    the half a floor is actually tuned against.

    Raises: TurnPipelineError from retrieval or embedding. Never from reranking.
    """
    logger = get_logger()
    settings = get_settings()

    pool = await search_faq(
        qdrant_client, voyage_client, message, session_id, live_revisions
    )
    corpus_empty = not live_revisions

    similarity = apply_similarity_gate(
        pool, floor=settings.SIMILARITY_FLOOR, cap=settings.SIMILARITY_CAP
    )
    # Neither event is raised when the corpus is empty: no search was issued and no gate
    # decided anything, so "retrieval completed" and "the gate ran" would both be false.
    # `turn.retrieval_skipped_empty_corpus` is what records that case, and conflating it
    # with a search that found nothing is what the empty-corpus verdict exists to undo.
    if not corpus_empty:
        _log_retrieval(pool, kept=similarity.kept)
        logger.info(
            "faq.similarity_gate",
            floor=settings.SIMILARITY_FLOOR,
            cap=settings.SIMILARITY_CAP,
            # `pool_returned`, not `pool_size`: the gate saw what the search actually
            # returned, and `pool_size` already names the configured ceiling on the
            # retrieval event beside this one.
            pool_returned=len(pool),
            kept=_identify(similarity.kept),
            dropped_by_floor=_identify(similarity.dropped_by_floor),
            dropped_by_cap=_identify(similarity.dropped_by_cap),
        )

    reranked: list[ScoredChunk] | None = None
    # Every candidate the reranker scored, not just the survivors: the verdict's
    # `best_rerank_score` reports what the whole stage saw, which is the number a floor
    # that dropped everything has to be read against.
    scored: list[ScoredChunk] | None = None
    if similarity.kept:
        scored = await rerank_chunks(
            rerank_client,
            message,
            similarity.kept,
            model=settings.RERANK_MODEL,
            # Every candidate, not the cap that produced them: a `top_k` below the
            # shortlist's length leaves a survivor unscored, which the port refuses as
            # an unusable response - so the turn would silently answer unreranked for
            # as long as the two numbers disagreed.
            top_k=len(similarity.kept),
            timeout_seconds=settings.RERANK_TIMEOUT_SECONDS,
        )
        if scored is not None:
            gate = apply_rerank_gate(
                scored, floor=settings.RERANK_FLOOR, cap=settings.RERANK_CAP
            )
            logger.info(
                "faq.rerank_gate",
                floor=settings.RERANK_FLOOR,
                cap=settings.RERANK_CAP,
                kept=_identify(gate.kept, rerank=True),
                dropped_by_floor=_identify(gate.dropped_by_floor, rerank=True),
                dropped_by_cap=_identify(gate.dropped_by_cap, rerank=True),
            )
            reranked = gate.kept

    outcome = decide(pool, similarity.kept, reranked, corpus_empty=corpus_empty)
    logger.info(
        "faq.verdict",
        verdict=outcome.verdict.value,
        survivor_count=len(outcome.survivors),
        blocked_gate=_gate_of(outcome.verdict),
        # One best score per floor, because the two floors are tuned separately and a
        # single number could not say which bar was too high. Each is the best its whole
        # stage saw - the pool before the similarity floor, every scored candidate
        # before the rerank floor - which is what separates "nothing was close" from
        # "something was close and the floor was too high".
        best_similarity_score=max((c.similarity_score for c in pool), default=None),
        # None means no chunk carries a rerank score at all: the reranker did not run,
        # or it failed. Never 0.0, which is the cross-encoder judging a chunk
        # irrelevant - a judgement it did make.
        best_rerank_score=max(
            (c.rerank_score for c in scored or () if c.rerank_score is not None),
            default=None,
        ),
    )
    return outcome


# How much of a chunk the log carries when the chunk was observed but not considered.
# Enough to recognise it, not enough to multiply a turn's log by the size of the pool.
_PREVIEW_CHARS = 200


def _log_retrieval(pool: list[ScoredChunk], *, kept: list[ScoredChunk]) -> None:
    """Record the whole observation pool, marking what the gate actually kept.

    Args:
        kept: the similarity gate's survivors. `considered` is read from this rather
            than from a candidate's position, because the two only agree while every
            candidate inside the cap also clears the floor. When fewer do, a positional
            flag would report chunks as considered that the floor threw out, and print
            their full text as though it had reached the prompt.
    """
    survivors = {(c.faq_entry_id, c.chunk_index) for c in kept}
    candidates = []
    for chunk in pool:
        considered = (chunk.faq_entry_id, chunk.chunk_index) in survivors
        text = chunk.chunk_text if considered else chunk.chunk_text[:_PREVIEW_CHARS]
        candidates.append(
            {
                "entry_id": chunk.faq_entry_id,
                "chunk_index": chunk.chunk_index,
                "similarity_score": chunk.similarity_score,
                "considered": considered,
                "chunk_text": text,
                "text_truncated": not considered
                and len(chunk.chunk_text) > _PREVIEW_CHARS,
            }
        )
    get_logger().info(
        "faq.retrieval_completed",
        pool_size=get_settings().RETRIEVAL_POOL_SIZE,
        pool_returned=len(pool),
        candidates=candidates,
    )


def _identify(
    chunks: list[ScoredChunk], *, rerank: bool = False
) -> list[dict[str, object]]:
    """Describe `chunks` for a gate's log line: identity and the score that decided."""
    key = "rerank_score" if rerank else "similarity_score"
    return [
        {
            "entry_id": c.faq_entry_id,
            "chunk_index": c.chunk_index,
            key: c.rerank_score if rerank else c.similarity_score,
        }
        for c in chunks
    ]


class AbstentionGate(StrEnum):
    """Where a turn stopped: `faq.verdict`'s `blocked_gate`, one per abstention."""

    EMPTY_CORPUS = "empty_corpus"
    EMPTY_POOL = "empty_pool"
    SIMILARITY_FLOOR = "similarity_floor"
    RERANK_FLOOR = "rerank_floor"


def _gate_of(verdict: FaqVerdict) -> AbstentionGate | None:
    """Name the gate an abstention stopped at, or None when the turn answered."""
    return {
        FaqVerdict.ABSTAINED_EMPTY_CORPUS: AbstentionGate.EMPTY_CORPUS,
        FaqVerdict.ABSTAINED_EMPTY_POOL: AbstentionGate.EMPTY_POOL,
        FaqVerdict.ABSTAINED_SIMILARITY_FLOOR: AbstentionGate.SIMILARITY_FLOOR,
        FaqVerdict.ABSTAINED_RERANK_FLOOR: AbstentionGate.RERANK_FLOOR,
    }.get(verdict)
