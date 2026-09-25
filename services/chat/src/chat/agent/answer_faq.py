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
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam
from qdrant_client import AsyncQdrantClient
from structlog.contextvars import bound_contextvars
from voyageai.client_async import AsyncClient

from chat.agent.compose_answer import (
    FaqResult,
    FaqSegmentAnswer,
    deduplicate_chunks,
)
from chat.agent.escalation import EscalationRequests
from chat.agent.history import to_loggable_messages
from chat.core.config import get_settings
from chat.core.errors import TurnPipelineError
from chat.core.logging import get_logger
from chat.domain.models import EscalationReason
from chat.domain.schemas import (
    ChatDoneEvent,
    ChatTokenEvent,
    Citation,
    FaqVerdict,
    RequestSegment,
)
from chat.observability import generation, record, step
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
# The model's way of reporting that evidence which cleared both gates does not answer
# the question. A signal to this function, never a line for a patient to read: it is
# withheld from the stream and turned into an abstention, which is what makes the
# record, the citations and the call to staff agree with what the patient was told.
# Prose was tried first and cannot work - "I don't have that information" is
# indistinguishable from an answer to everything downstream, so the turn recorded the
# request as answered, cited a chunk that had not answered it, and called nobody.
#
# Asking for a token rather than a sentence moves how readily the model declines, so
# the instruction carries a clause about what not to do instead. Without it, "can I
# pay half by HSA card and half in cash?" - which the payment entry answers for each
# method and not for the two together - went from declining 5 times in 5 to declining
# once, the other four listing the methods or answering "yes, you can split your
# payment between them". The clause returns it to 5 in 5 and leaves the cases that
# should be answered where they were.
_NO_ANSWER = "NO_ANSWER"

# The model echoes whatever the prompt calls its material: told to use "the provided
# context", it opened about one answer in forty with "Based on the provided context",
# which tells a patient about the pipeline instead of the clinic. So nothing it is shown
# uses that word, and it is told to speak as the clinic rather than cite a source.
# Speaking as the clinic has a cost the last sentence pays: told only that, the model
# answered "do you do blood tests on Saturdays?" with a flat "we do not" from an hours
# entry that never mentions blood tests. The rule forbids stating what the entries do
# not say, not combining what they do: "never infer an answer" also stopped it
# assembling "what should I do before my visit?" from the arrival and what-to-bring
# entries, which is exactly what it should do.
#
# That rule is read at the level of the words present, which is what the last sentence
# is for. "You can book a visit with any of our practitioners without a referral"
# answers "do I need a referral to see a dentist?", but the chunk never types the word
# "dentist", so the model called it unanswered: G-o-02's second request abstained in
# prose on the chunk that answered it, 10 samples out of 10, while the same chunk
# answered the "specialist" wording every time. Deleting the rule instead was measured
# and is worse - the invented "we do not offer blood tests on Saturdays" returns at 3
# samples in 5, and an MRI acquires a $160 price the corpus never states.
_SYSTEM_PROMPT = (
    "You are a clinic assistant. Answer the visitor's question using ONLY the clinic "
    "information given with it. Do not use outside knowledge. Be concise. Speak as the "
    "clinic, stating the facts directly: never mention where they came from, and never "
    "refer to context, provided information, documents, excerpts or text you were "
    "given. Say nothing it does not say - not even a no. What it states of every "
    "member of a group it states of each one, so a question about one of them is "
    f"answered, not missing. When it does not answer the question, reply with exactly "
    f"{_NO_ANSWER} and nothing else. Listing what it does say, or agreeing because "
    f"the parts are there separately, is worse than replying {_NO_ANSWER}."
)
# The heading the retrieved chunks sit under. It names what they are to the patient, so
# the model repeating it would still read naturally.
_RETRIEVED_HEADING = "Clinic information:"
# Introduces the classifier's restatement, shown beside the patient's own question only
# when the two differ. The model is shown no conversation (`_TurnContext`), so "and a
# dentist?" names nothing without it - but the restatement is a search wording and can
# drop a condition: "...for the same visit, splitting the cost between them?" came back
# as "Do you accept UnitedHealthcare and Blue Cross Blue Shield?", and answering that
# answered a question the corpus cannot. So it is labelled as what the question refers
# to, and the patient's question comes last, as the thing answered. Measured on the 11
# requests of a full run whose two wordings differed, 3 samples each: every answerable
# one answered, the dentist's price resolved, and the split-cost gap declined 3 in 3.
_RESTATED_HEADING = (
    "What the question refers to, restated to stand on its own - it may leave out part "
    "of what was asked:"
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
    """Everything every request of one turn shares: its clients and its corpus.

    No conversation: the answerer is shown none of it. What a request refers to comes
    from its standalone restatement (`RequestSegment.query`), which the classifier
    wrote with the whole conversation in view and which the prompt shows beside the
    patient's own question (`_RESTATED_HEADING`). Shown the conversation, the answerer
    let it decide the answer: with an earlier question in view that had gone
    unanswered, it declined questions the entry in front of it answered - 5 samples in
    5 on replay of a live turn, and still 5 in 5 with the clinic's replies taken out
    and only the patient's earlier messages kept. With no conversation it answered 3
    in 3. Golden case `G-r-01` holds that turn.
    """

    qdrant_client: AsyncQdrantClient
    voyage_client: AsyncClient
    rerank_client: AsyncClient
    anthropic_client: AsyncAnthropic
    session_id: str
    live_revisions: list[str]


async def answer_faq(
    qdrant_client: AsyncQdrantClient,
    voyage_client: AsyncClient,
    rerank_client: AsyncClient,
    anthropic_client: AsyncAnthropic,
    reply_to_message_ids: list[str],
    session_id: str,
    live_revisions: list[str],
    *,
    segments: list[RequestSegment],
    positions: list[int] | None = None,
    escalation: EscalationRequests,
    stream: bool = True,
) -> AsyncIterator[ChatTokenEvent | ChatDoneEvent | FaqResult]:
    """Answer each of this turn's questions from its own retrieval, or abstain.

    Args:
        rerank_client: The reranking client, separate from `voyage_client` so its
            deadline bounds the whole call however the embedding client is configured
            to retry.
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
        positions: Each question's 0-based place in the patient's *whole* message, in
            the same order as `segments`. It is what every per-request record is joined
            on - the retrieval events' `segment`, `intent.classified`'s entry, and the
            stored `RequestOutcome` - so a message whose first request went to another
            specialist starts this half at a position above zero. Omitted only by a
            caller whose questions are the whole message, where it defaults to their
            own order.
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
        failing the turn. RuntimeError if the half is entered with no question, asked
        to stream more than one, or handed a position per question that does not match
        the questions.
    """
    if not segments:
        raise RuntimeError("the FAQ half was entered with no question to answer")
    if positions is None:
        positions = list(range(len(segments)))
    elif len(positions) != len(segments):
        raise RuntimeError("every question carries its position in the message")
    if stream and len(segments) > 1:
        # Streaming is decided before retrieval runs, from how many parts the reply
        # could have; more than one part is always collected and composed.
        raise RuntimeError("a streamed turn answers exactly one question")

    context = _TurnContext(
        qdrant_client=qdrant_client,
        voyage_client=voyage_client,
        rerank_client=rerank_client,
        anthropic_client=anthropic_client,
        session_id=session_id,
        live_revisions=live_revisions,
    )

    requests = list(zip(positions, segments, strict=True))
    answers: list[FaqSegmentAnswer] = []
    if len(requests) == 1:
        position, segment = requests[0]
        async for event in _answer_one(position, segment, context, stream=stream):
            if isinstance(event, FaqSegmentAnswer):
                answers.append(event)
            elif stream:
                yield event
    else:
        answers = await _answer_all(requests, context)

    result = FaqResult.from_segments(answers, abstention_message=_ABSTENTION_MESSAGE)
    if result.any_abstained:
        # One call to staff for the turn, however many of its questions the corpus
        # could not answer. Recorded on the request that could not be answered, and
        # before any of it reaches the patient. A visitor whose question the clinic
        # has no answer for is exactly who needs a person, so there is no exemption for
        # an empty corpus, and no distinction between the gates: all of them mean the
        # corpus could not answer. Nothing is escalated for a request that *was*
        # answered, including one answered without reranking.
        escalation.record(EscalationReason.CORPUS_COULD_NOT_ANSWER)
    if stream:
        yield ChatDoneEvent(
            request_outcomes=result.request_outcomes,
            # Read off the same predicate that decides `answer_text` is the constant:
            # a half that answered anything streamed those tokens already, so there is
            # no reply to send instead of them. `any_abstained` is the other predicate
            # and would pair the "some" question with a value only the "all" case has.
            message=result.answer_text if not result.any_answered else None,
        )
    yield result


async def _answer_all(
    requests: list[tuple[int, RequestSegment]], context: _TurnContext
) -> list[FaqSegmentAnswer]:
    """Run every question of one turn concurrently, in message order.

    Args:
        requests: each question paired with its position in the patient's message.

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
        for position, segment in requests
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
    with (
        bound_contextvars(segment=position),
        step(
            f"request[{position}]",
            input={"query": segment.query, "text": segment.text},
        ) as observed,
    ):
        async for event in _answer_one_bound(position, segment, context, stream=stream):
            if isinstance(event, FaqSegmentAnswer):
                observed.set_output(event.request_outcome().model_dump(mode="json"))
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
        segment.query,
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

    # Deduplicated here, once, where this request's evidence is assembled - not in
    # each of the readers downstream. The same list becomes the prompt's context, the
    # request's `citations` and its `scored_chunks`, so a chunk a single shortlist
    # listed twice is one chunk in all three (FR-003, research #11). A chunk that
    # supported a *different* request is untouched: nothing here sees another request.
    survivors = deduplicate_chunks(outcome.survivors)
    retrieved = "\n\n".join(chunk.chunk_text for chunk in survivors)
    # One message, and the only one: the clinic information, the restatement when it
    # differs from the patient's wording (`_RESTATED_HEADING`), then the question as
    # the patient asked it. Where the two are the same - every request of a message
    # that carried several - the prompt is the two parts it has always had. Another
    # request's chunks and another request's words are not in this prompt at all.
    parts = [f"{_RETRIEVED_HEADING}\n{retrieved}"]
    if segment.query != segment.text:
        parts.append(f"{_RESTATED_HEADING} {segment.query}")
    parts.append(f"Question: {segment.text}")
    prompt = "\n\n".join(parts)
    messages: list[MessageParam] = [{"role": "user", "content": prompt}]

    # The retrieved context reaches the model inside this turn's own message, so the
    # logged conversation is also the record of what was retrieved for it.
    logger.debug("faq.model_request", messages=to_loggable_messages(messages))

    answer_parts: list[str] = []
    # Nothing reaches the patient until the reply can no longer be the sentinel, which
    # is a signal to this function and not a sentence to read. The hold is bounded by
    # the sentinel's own length: the moment the text diverges from it everything held
    # is flushed in one event and the rest streams token by token, so an answer is
    # delayed by a few characters and never by the whole generation.
    withheld = True
    model = get_settings().GENERATION_MODEL
    try:
        with generation(
            "answer_faq.model",
            model=model,
            input={"system": _SYSTEM_PROMPT, "messages": messages},
            model_parameters={"max_tokens": _MAX_TOKENS},
        ) as observed:
            async with context.anthropic_client.messages.stream(
                model=model,
                max_tokens=_MAX_TOKENS,
                system=_SYSTEM_PROMPT,
                messages=messages,
            ) as stream_response:
                async for event in stream_response:
                    if event.type != "text":
                        continue
                    observed.mark_token()
                    answer_parts.append(event.text)
                    if not stream:
                        continue
                    if not withheld:
                        yield ChatTokenEvent(text=event.text)
                    elif not _may_still_be_sentinel("".join(answer_parts)):
                        withheld = False
                        yield ChatTokenEvent(text="".join(answer_parts))
                # Read after the loop, the same way `answer_small_talk` reads it: the
                # SDK accumulates the final message, and this is the documented way to
                # ask why it stopped. Inside the `try` because a failure to obtain it is
                # a failure of the same call.
                final = await stream_response.get_final_message()
            observed.record_completion(
                "".join(answer_parts), final.usage, final.stop_reason
            )
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

    answer_text = "".join(answer_parts)
    if _NO_ANSWER in answer_text:
        # The gates passed the evidence and the generation step reports it does not
        # answer this request. Recorded as the abstention it is, so the outcome carries
        # no answer, cites nothing, and reaches the same call to staff as any other
        # gap - rather than an answered verdict whose text declines in prose.
        #
        # Anywhere in the reply, not only at its start: the model sometimes writes a
        # prose decline and appends the sentinel to it. Reading only the start would
        # take that for an answer and send the patient a reply ending in NO_ANSWER.
        # `malformed` is what separates the two, because only the bare sentinel is
        # withheld in full - a trailing one has already streamed by the time it
        # arrives, so the patient saw a decline in the model's own words while the
        # record says abstained. That is the safe side of the disagreement and still
        # worth an operator seeing.
        logger.info(
            "faq.no_answer",
            gates_verdict=outcome.verdict.value,
            survivor_count=len(survivors),
            malformed=answer_text.strip() != _NO_ANSWER,
        )
        yield FaqSegmentAnswer(
            position=position,
            question=segment.text,
            answer_text="",
            verdict=FaqVerdict.ABSTAINED_GENERATION,
            citations=[],
        )
        return

    if stream and withheld and answer_text:
        # Shorter than the sentinel and never ruled out inside the loop, so it is still
        # held here. Without this the patient would be sent nothing while the turn
        # reported an answer.
        yield ChatTokenEvent(text=answer_text)

    if not answer_text.strip():
        # A request whose verdict says it was answered has to carry the text it
        # produced - its outcome pairs the two, and "answered, with nothing" is the one
        # thing that pair may not say. Raised here rather than left to the outcome's own
        # construction, which happens after the half has already streamed or been
        # merged: the same failure would then reach the patient behind their own reply.
        raise TurnPipelineError(
            "generation", ValueError("the model returned no answer text")
        )

    yield FaqSegmentAnswer(
        position=position,
        question=segment.text,
        answer_text=answer_text,
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
    # No step is recorded for it either, for the same reason.
    if not corpus_empty:
        with step("faq.similarity_gate") as gated:
            # On the gate's step, not the search's: each candidate's `considered` is
            # this gate's decision, so the event cannot exist until the gate has run.
            gated.set_metadata(
                retrieval_completed=_log_retrieval(pool, kept=similarity.kept)
            )
            record(
                gated,
                "faq.similarity_gate",
                {
                    "floor": settings.SIMILARITY_FLOOR,
                    "cap": settings.SIMILARITY_CAP,
                    # `pool_returned`, not `pool_size`: the gate saw what the search
                    # actually returned, and `pool_size` already names the configured
                    # ceiling on the retrieval event beside this one.
                    "pool_returned": len(pool),
                    "kept": _identify(similarity.kept),
                    "dropped_by_floor": _identify(similarity.dropped_by_floor),
                    "dropped_by_cap": _identify(similarity.dropped_by_cap),
                },
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
            with step("faq.rerank_gate") as gated:
                gate = apply_rerank_gate(
                    scored, floor=settings.RERANK_FLOOR, cap=settings.RERANK_CAP
                )
                record(
                    gated,
                    "faq.rerank_gate",
                    {
                        "floor": settings.RERANK_FLOOR,
                        "cap": settings.RERANK_CAP,
                        "kept": _identify(gate.kept, rerank=True),
                        "dropped_by_floor": _identify(
                            gate.dropped_by_floor, rerank=True
                        ),
                        "dropped_by_cap": _identify(gate.dropped_by_cap, rerank=True),
                    },
                )
            reranked = gate.kept

    with step("faq.verdict") as decided:
        outcome = decide(pool, similarity.kept, reranked, corpus_empty=corpus_empty)
        record(
            decided,
            "faq.verdict",
            {
                "verdict": outcome.verdict.value,
                "survivor_count": len(outcome.survivors),
                "blocked_gate": _gate_of(outcome.verdict),
                # One best score per floor, because the two floors are tuned separately
                # and a single number could not say which bar was too high. Each is the
                # best its whole stage saw - the pool before the similarity floor, every
                # scored candidate before the rerank floor - which is what separates
                # "nothing was close" from "something was close and the floor was too
                # high".
                "best_similarity_score": max(
                    (c.similarity_score for c in pool), default=None
                ),
                # None means no chunk carries a rerank score at all: the reranker did
                # not run, or it failed. Never 0.0, which is the cross-encoder judging a
                # chunk irrelevant - a judgement it did make.
                "best_rerank_score": max(
                    (
                        c.rerank_score
                        for c in scored or ()
                        if c.rerank_score is not None
                    ),
                    default=None,
                ),
            },
        )
    return outcome


# How much of a chunk the log carries when the chunk was observed but not considered.
# Enough to recognise it, not enough to multiply a turn's log by the size of the pool.
_PREVIEW_CHARS = 200


def _log_retrieval(
    pool: list[ScoredChunk], *, kept: list[ScoredChunk]
) -> dict[str, Any]:
    """Record the whole observation pool, marking what the gate actually kept.

    Returns: the payload `faq.retrieval_completed` was logged with.

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
    payload: dict[str, Any] = {
        "pool_size": get_settings().RETRIEVAL_POOL_SIZE,
        "pool_returned": len(pool),
        "candidates": candidates,
    }
    get_logger().info("faq.retrieval_completed", **payload)
    return payload


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


def _may_still_be_sentinel(text: str) -> bool:
    """Whether `text` could still grow into the sentinel, or already carries it.

    Leading whitespace is ignored, so a reply the model opens with a newline is held
    on the same terms as one that does not. Text that already begins with the sentinel
    keeps being held: a sentinel with prose after it is a malformed decline, and
    sending the patient the prose half of one would be sending them the half the
    record then contradicts.
    """
    seen = text.lstrip()
    return _NO_ANSWER.startswith(seen) or seen.startswith(_NO_ANSWER)


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
