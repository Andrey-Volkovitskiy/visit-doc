"""`answer_faq`: retrieve -> similarity gate -> rerank -> rerank gate -> generate.

Plain async function, no agent-framework dependency of its own - `agent/graph.py`'s
`answer_faq_node` wraps it as a LangGraph node, forwarding its yielded events via the
stream writer.

One run per request. A turn's message may carry several questions, and each is
retrieved for, gated and answered on its own evidence - concurrently, never pooled:
a shortlist shared by two questions lets the stronger one's chunks crowd the other's
out under the same cap, which is the defect splitting a message exists to remove.

Two modes, one pipeline. Streaming mode sends tokens straight to the patient and the
terminal event is this function's own; it runs only for a turn whose whole reply is
this one request's answer. Collect mode runs when anything else contributes a part -
another specialist, or another request here - and produces a result for the composing
step instead of emitting anything. Retrieval, both gates, and how citations are derived
are identical either way.

The gates are pure and live in `rag/pipeline.py`; the reranking call and its deadline
live in `rag/reranking.py`. What is left here is the order they run in, what the prompt
is built from, and what the turn records.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam
from qdrant_client import AsyncQdrantClient
from structlog.contextvars import bound_contextvars
from voyageai.client_async import AsyncClient

from chat.agent.compose_answer import FaqResult, FaqSegmentAnswer
from chat.agent.escalation import EscalationRequests
from chat.agent.history import (
    bound_to_last_n_turns,
    render_opening_clinic,
    render_silent_window,
    replace_trailing_entry,
    silent_window,
    to_claude_messages,
    to_loggable_messages,
)
from chat.core.config import get_settings
from chat.core.errors import TurnPipelineError
from chat.core.logging import get_logger
from chat.domain.models import EscalationReason, Message
from chat.domain.schemas import (
    ChatDoneEvent,
    ChatTokenEvent,
    Citation,
    FaqVerdict,
    RequestSegment,
)
from chat.rag.pipeline import (
    PipelineOutcome,
    ScoredChunk,
    apply_rerank_gate,
    apply_similarity_gate,
    decide,
)
from chat.rag.reranking import rerank_chunks
from chat.rag.retriever import search_faq

# One question's answer, not the turn's: a turn carrying several questions spends this
# per question, and the merge step's own budget is built from this number.
_MAX_TOKENS = 1024
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


@dataclass(frozen=True)
class _TurnContext:
    """Everything every request of one turn shares: its clients and its history.

    Rendered once and read by each request's run, so a fan-out cannot end up with two
    renderings of one conversation.
    """

    qdrant_client: AsyncQdrantClient
    voyage_client: AsyncClient
    rerank_client: AsyncClient
    anthropic_client: AsyncAnthropic
    session_id: str
    live_revisions: list[str]
    history: list[MessageParam]
    silenced: str
    opening_clinic: str


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
    segments: list[RequestSegment],
    escalation: EscalationRequests,
    stream: bool = True,
) -> AsyncIterator[ChatTokenEvent | ChatDoneEvent | FaqResult]:
    """Answer each of this turn's questions from its own retrieval, or abstain.

    Args:
        rerank_client: The reranking client, separate from `voyage_client` so its
            deadline bounds the whole call however the embedding client is configured
            to retry.
        bursts: The chat's full conversation history, partitioned into contiguous
            same-side runs. Bounded here to the last few turns before any model call,
            and used as context only - what is retrieved for and answered is
            `segments`, not the trailing message.
        reply_to_message_ids: The patient message id(s) this turn is answering.
        session_id: The session this turn belongs to, and the only corpus retrieval may
            reach - carried to the search as a term of its own rather than left to the
            revisions to imply.
        live_revisions: Every revision that session currently publishes - the whole of
            what retrieval may search. Read before this function is entered, so an empty
            list provably means an empty corpus rather than a read that failed.
        segments: The questions this half is answering, in the order they appeared in
            the message. Each is retrieved for, gated and answered on its own; nothing
            is pooled across them.
        escalation: This turn's collector of calls to staff. An abstention records one
            into it - once per turn, however many questions could not be answered -
            and nothing here writes the transition, which belongs to the end of the
            turn.
        stream: True to stream tokens and emit the terminal event; False to yield a
            single `FaqResult` for a later composing step instead.

    Yields: in streaming mode, `ChatTokenEvent`s then one `ChatDoneEvent`, then the
        result; in collect mode, exactly one `FaqResult`.

    Raises: TurnPipelineError wrapping any failure in embedding, retrieval or
        generation, whichever question it happened on - the turn fails whole, and no
        other question's answer is delivered on its own. A reranking failure is
        deliberately not among them: it degrades that question's answer rather than
        failing the turn. RuntimeError if the half is entered with no question, or
        asked to stream more than one.
    """
    settings = get_settings()
    bounded = bound_to_last_n_turns(bursts, n=settings.CONTEXT_TURNS)
    if not segments:
        raise RuntimeError("the FAQ half was entered with no question to answer")
    if stream and len(segments) > 1:
        # Streaming is decided before retrieval runs, from how many parts the reply
        # could have; more than one part is always collected and composed.
        raise RuntimeError("a streamed turn answers exactly one question")

    # Both taken from the bursts, not from `history`'s last entry: a turn following a
    # silent window has two consecutive patient-sided bursts, which that render rejoins
    # into one. Reading them off it would carry a message a staff member was meant to
    # deal with into the prompt; and since the prompt below replaces that entry, the
    # window has to be carried into the prompt explicitly or it would be dropped from
    # the conversation the model reads at all. The clinic's own opening words are
    # carried for the same reason - see `replace_trailing_entry`.
    context = _TurnContext(
        qdrant_client=qdrant_client,
        voyage_client=voyage_client,
        rerank_client=rerank_client,
        anthropic_client=anthropic_client,
        session_id=session_id,
        live_revisions=live_revisions,
        history=to_claude_messages(bounded),
        silenced=render_silent_window(silent_window(bounded)),
        opening_clinic=render_opening_clinic(bounded),
    )

    answers: list[FaqSegmentAnswer] = []
    if len(segments) == 1:
        async for event in _answer_one(0, segments[0], context, stream=stream):
            if isinstance(event, FaqSegmentAnswer):
                answers.append(event)
            elif stream:
                yield event
    else:
        answers = await _answer_all(segments, context)

    result = FaqResult.from_segments(answers, abstention_message=_ABSTENTION_MESSAGE)
    if not result.verdict.answered:
        # One call to staff for the turn, however many of its questions the corpus
        # could not answer. Recorded on the same signal that produced the abstention,
        # and before any of it reaches the patient. A visitor whose question the clinic
        # has no answer for is exactly who needs a person, so there is no exemption for
        # an empty corpus, and no distinction between the gates: all of them mean the
        # corpus could not answer.
        escalation.record(EscalationReason.CORPUS_COULD_NOT_ANSWER)
    if stream:
        yield ChatDoneEvent(
            faq_verdict=result.verdict,
            citations=result.citations,
            message=None if result.verdict.answered else result.answer_text,
        )
    yield result


async def _answer_all(
    segments: list[RequestSegment], context: _TurnContext
) -> list[FaqSegmentAnswer]:
    """Run every question of one turn concurrently, in message order.

    Raises: whatever ended the run first - the first question's failure, or this
        turn's own cancellation. Either way the turn fails whole rather than serving
        what survived.

    Not a bare `asyncio.gather`: that propagates the first exception and leaves the
    other questions *running*, so a turn that has already failed would keep spending
    generation calls on answers nobody will read, and each one's own failure would
    surface later as an unretrieved task exception attributed to nothing. The siblings
    are cancelled here, and waited for, before the failure leaves this function.
    """
    # Four invariants hold over the block below:
    #
    # 1. Whatever ended the run first is what leaves. A question's failure is the
    #    turn's only account of itself - which question, and at which step - so a
    #    cancellation landing while the siblings are being drained does not displace
    #    it. It would read as a supersede and name nothing.
    # 2. No question outlives this call. Every sibling is cancelled before anything
    #    leaves, and, unless the wait is itself interrupted, awaited to completion.
    # 3. A cancellation with nothing else to report leaves as itself. When no question
    #    has failed it is the exception in flight and is re-raised unchanged, so a
    #    superseded turn still reads as cancelled rather than as broken.
    # 4. The drain cannot outlive a cancellation, because it is not shielded: a
    #    sibling that refuses to unwind holds this call only until the next cancel,
    #    which ends the wait at once. That is also why it carries no deadline - a
    #    deadline could only abandon the siblings, which is invariant 2, to guard
    #    against a sibling that would have to catch `CancelledError` to hang, and
    #    nothing on this path does.
    tasks = [
        asyncio.ensure_future(
            _collect(_answer_one(position, segment, context, stream=False))
        )
        for position, segment in enumerate(segments)
    ]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            task.cancel()
        with suppress(asyncio.CancelledError):
            await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def _collect(
    events: AsyncIterator[ChatTokenEvent | FaqSegmentAnswer],
) -> FaqSegmentAnswer:
    """Run one question's generator to completion and return its answer.

    Its tokens are dropped: a turn answering several questions composes one reply from
    them afterwards, so no question streams its own words to the patient.

    Raises: RuntimeError if the generator ended without producing an answer.
    """
    answer: FaqSegmentAnswer | None = None
    async for event in events:
        if isinstance(event, FaqSegmentAnswer):
            answer = event
    if answer is None:
        raise RuntimeError("a question's run ended without an answer")
    return answer


async def _answer_one(
    position: int,
    segment: RequestSegment,
    context: _TurnContext,
    *,
    stream: bool,
) -> AsyncIterator[ChatTokenEvent | FaqSegmentAnswer]:
    """Retrieve for one question, then answer it from its own survivors or abstain.

    Yields: this question's tokens while it generates, if `stream`, then exactly one
        `FaqSegmentAnswer`.

    Raises: TurnPipelineError wrapping any failure in embedding, retrieval or
        generation.

    An answer that ran into `_MAX_TOKENS` is delivered as it stands and recorded as
    `faq.truncated`: it rests on evidence that cleared both gates, so abstaining over
    it would be a lie about the corpus, and a clipped answer otherwise reads as a short
    complete one.
    """
    with bound_contextvars(segment=position):
        async for event in _answer_one_bound(position, segment, context, stream=stream):
            yield event


async def _answer_one_bound(
    position: int,
    segment: RequestSegment,
    context: _TurnContext,
    *,
    stream: bool,
) -> AsyncIterator[ChatTokenEvent | FaqSegmentAnswer]:
    """`_answer_one`'s body, with this request's position already bound to the log.

    Split out only so the binding wraps the whole run rather than one statement of it.
    A fan-out runs each request in its own task, and `structlog`'s context is copied per
    task, so the positions cannot cross. A turn with one request has no task of its own:
    there the binding is set in the calling node's context for as long as this generator
    is suspended, which is harmless where every event of the turn belongs to request 0.
    """
    logger = get_logger()
    outcome = await _run_pipeline(
        context.qdrant_client,
        context.voyage_client,
        context.rerank_client,
        segment.text,
        context.session_id,
        context.live_revisions,
    )
    if not outcome.verdict.answered:
        yield FaqSegmentAnswer(
            position=position,
            question=segment.text,
            answer_text="",
            verdict=outcome.verdict,
            citations=[],
        )
        return

    survivors = outcome.survivors
    retrieved = "\n\n".join(chunk.chunk_text for chunk in survivors)
    # The same three parts it has always had, and the same two when nothing was
    # silenced. What the question *is* did change: it is this request as the classifier
    # restated it, not the message verbatim - the same text this run retrieved for, so
    # the prompt and the shortlist can never be about two different questions. Another
    # request's chunks and another request's words are not in this prompt at all.
    prompt = "\n\n".join(
        part
        for part in (
            f"Context:\n{retrieved}",
            context.silenced,
            f"Question: {segment.text}",
        )
        if part
    )
    # Not `[*history[:-1], current_turn]`: when the whole window renders as one entry,
    # the entry this prompt replaces is the one carrying the clinic's opening words.
    messages = replace_trailing_entry(
        context.history, prompt, opening_clinic=context.opening_clinic
    )

    # The retrieved context reaches the model inside this turn's own message, so the
    # logged conversation is also the record of what was retrieved for it.
    logger.debug("faq.model_request", messages=to_loggable_messages(messages))

    answer_parts: list[str] = []
    try:
        async with context.anthropic_client.messages.stream(
            model=get_settings().GENERATION_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=messages,
        ) as stream_response:
            async for event in stream_response:
                if event.type == "text":
                    answer_parts.append(event.text)
                    if stream:
                        yield ChatTokenEvent(text=event.text)
            # Read after the loop, the same way `answer_small_talk` reads it: the SDK
            # accumulates the final message, and this is the documented way to ask why
            # it stopped. Inside the `try` because a failure to obtain it is a failure
            # of the same call.
            final = await stream_response.get_final_message()
    except Exception as exc:
        raise TurnPipelineError("generation", exc) from exc

    if final.stop_reason == "max_tokens":
        # Logged, not raised, and staff are not called: in streaming mode the tokens
        # have already reached the patient, and an abstention would be a lie about
        # evidence that did clear both gates. What is owed is a record - an answer cut
        # off at the cap otherwise looks exactly like a short complete one, and this is
        # the event that says which it was, per request.
        logger.warning(
            "faq.truncated",
            max_tokens=_MAX_TOKENS,
            answer_chars=sum(len(part) for part in answer_parts),
        )

    yield FaqSegmentAnswer(
        position=position,
        question=segment.text,
        answer_text="".join(answer_parts),
        verdict=outcome.verdict,
        citations=[
            Citation(
                entry_id=chunk.faq_entry_id,
                chunk_index=chunk.chunk_index,
                chunk_text=chunk.chunk_text,
            )
            for chunk in survivors
        ],
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
