"""`compose_answer`: the join node that owns the turn's user-visible reply.

It runs on every path, merging or not, which is what makes it the one place a turn can
be reported complete exactly once - after the reply actually exists.

When only one specialist ran it does nothing at all: that specialist streamed its own
tokens and emitted its own terminal event, so the FAQ path keeps its existing latency
and behavior byte for byte. Only a mixed-intent turn pays for a composing call.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from anthropic import AsyncAnthropic

from chat.agent.handle_booking import BookingOutcome
from chat.core.config import get_settings
from chat.core.correlation import turn_elapsed_ms
from chat.core.errors import TurnPipelineError
from chat.core.logging import get_logger
from chat.domain.schemas import (
    MAX_SEGMENTS,
    AnswerSource,
    ChatDoneEvent,
    ChatTokenEvent,
    Citation,
    FaqVerdict,
    RequestOutcome,
    RequestSegment,
)
from chat.rag.pipeline import ScoredChunk

# What one half's part of the merge was itself written under - the FAQ path's and the
# booking loop's own cap, which are the same number. Restated rather than imported:
# `answer_faq` imports this module, so the dependency cannot run the other way.
_PART_MAX_TOKENS = 1024
# Derived, not chosen: this call has to hold every part at once - up to `MAX_SEGMENTS`
# question answers plus a booking reply - so a budget smaller than their sum is one the
# worst legal merge runs past. A run that still hits it is logged, because the merge is
# the one step that can cut the reply off with nothing downstream to notice.
_MAX_TOKENS = (MAX_SEGMENTS + 1) * _PART_MAX_TOKENS
_SYSTEM_PROMPT = """You are a clinic assistant writing ONE reply to a patient whose
message had more than one part. Every part that was in it is labelled below, with what
was done about it - a question answered from the clinic's knowledge base, something
about an appointment, or a request this assistant is not authorized to handle. Only the
parts labelled below were in the message: never write about one that is not there.

Combine them into a single, natural reply. You must:
- Preserve every factual claim exactly as given. Do not add, soften, or strengthen one.
- If the question half says there is no confident answer, say plainly that you do not
  have that information in the clinic's knowledge base, that the question has been
  forwarded to staff who will follow up, and that you can still help with anything
  else in the meantime. Never fill the gap from your own knowledge, and never promise
  when they will reply.
- Never soften a gap. A question labelled as having no confident answer has none. Do
  not promise to look into it, do not suggest when staff will reply, do not imply the
  answer is somewhere else in the reply, and do not turn it into a partial answer.
- Never extend an answer to cover a gap. A claim given for one question answers that
  question only. Two questions about the same subject are still two questions, and an
  answer about one of them says nothing about the other.
- Name each gap in your own words, so the patient can tell which of their requests went
  unanswered - never by quoting the restatement they were given here, which is a
  machine's wording and not what the patient wrote.
- If the appointment half did not result in a booking, never write anything that
  suggests one exists.
- The appointment half is labelled with the outcome that actually happened. Say only
  what that outcome records, whatever the appointment half's own wording suggests:
    * "booked" - an appointment was created.
    * "rescheduled" - an existing appointment moved. It was not newly booked.
    * "cancelled" - an existing appointment was cancelled.
    * "unchanged" - the appointment was already in the state asked for. Nothing was
      written; report it as already done, never as a new change.
    * "outcome_unknown" - the request was sent and no answer came back, so whether it
      took effect is not known. Say that it is not known. Never say it happened, and
      never say it did not happen or that nothing happened.
    * "refused", "unavailable", "awaiting_confirmation", "informational" - nothing was
      created, moved, or cancelled.
- If the input says a request was NOT AUTHORIZED, the reply must also say three
  things about it: that you are not authorized to handle that request, that it has
  been forwarded to the clinic's staff who will follow up, and that they can ask you
  about anything else in the meantime. Never claim that request was served, and never
  promise when staff will respond.
- Do not mention specialists, tools, or internal steps.
Be concise."""


class TurnCompletion:
    """A slot holding the fields `turn.completed` should carry, until it is emitted.

    The fields are only known inside `compose_answer`'s node span, but the event
    describes the *turn*, not that node - so the path that produced the reply records
    them here and the caller emits them once the span has closed. That keeps the
    turn's terminal line after the last `node.completed` instead of nested inside it,
    and keeps it free of the span's `node` binding.
    """

    def __init__(self) -> None:
        """Start the slot empty - no path has recorded a completion yet."""
        self._fields: dict[str, Any] | None = None

    def set(self, **fields: Any) -> None:
        """Record the fields `turn.completed` should carry."""
        self._fields = fields

    def emit(self) -> None:
        """Emit `turn.completed`, timed from when the turn's id was bound.

        Raises: RuntimeError if no path recorded a completion first - every path
            through the turn owes exactly one.

        `duration_ms` covers the whole turn the patient waited through - the history
        read and message insert included, not just the graph - and is left off
        entirely when the emission happens outside a bound turn.
        """
        if self._fields is None:
            raise RuntimeError("turn.completed was emitted with no fields recorded")
        duration_ms = turn_elapsed_ms()
        if duration_ms is None:
            get_logger().info("turn.completed", **self._fields)
            return
        get_logger().info("turn.completed", duration_ms=duration_ms, **self._fields)


def deduplicate_chunks(chunks: list[ScoredChunk]) -> list[ScoredChunk]:
    """Return `chunks` with later repeats of a chunk dropped, first appearance kept.

    Identity is `(entry_id, chunk_index)` - the pair every gate and every log event
    already identifies a chunk by - so two chunks of one entry stay two chunks, while
    one chunk that answered two of the turn's requests is carried, and cited, once.
    """
    seen: set[tuple[int, int]] = set()
    unique: list[ScoredChunk] = []
    for chunk in chunks:
        key = (chunk.faq_entry_id, chunk.chunk_index)
        if key not in seen:
            seen.add(key)
            unique.append(chunk)
    return unique


@dataclass(frozen=True)
class FaqSegmentAnswer:
    """What one of the turn's requests produced.

    `question` is the request as the classifier restated it - what this run retrieved
    for and answered - so the record can say which evidence belonged to which question
    without joining anything.

    `answer_text` is empty for a request that abstained: nothing was generated for it.
    """

    position: int
    question: str
    answer_text: str
    verdict: FaqVerdict
    citations: list[Citation]
    scored_chunks: list[ScoredChunk] = field(default_factory=list)

    def request_outcome(self) -> RequestOutcome:
        """Project this request onto the record the turn stores and streams.

        `scored_chunks` are deliberately dropped: they are the log's, and `Citation` -
        the wire type the console renders - carries no number at all. Stripping them
        here rather than at every call site is what keeps the scores from reaching the
        wire by accident.
        """
        answered = self.verdict.answered
        return RequestOutcome(
            position=self.position,
            question=self.question,
            answer=self.answer_text if answered else None,
            verdict=self.verdict,
            citations=self.citations if answered else [],
        )


@dataclass(frozen=True)
class FaqResult:
    """What `answer_faq` produces for the turn, over all of its requests.

    `segment_answers` is what everything else is derived from: there is no turn-level
    verdict and no turn-level citation list, because a turn may answer one request and
    abstain on another and neither value could describe both (FR-002, FR-003).

    `scored_chunks` are the same chunks the outcomes cite, carrying the two scores that
    selected them. They are part of the turn's observable record but not of the reply,
    so they ride here rather than on `Citation`.

    `answer_text` is the half's own single text, and is None exactly when the half has
    more than one reply part: several answers have no one text, and joining them would
    put a reply nobody wrote into the record as the turn's answer.
    """

    answer_text: str | None
    scored_chunks: list[ScoredChunk] = field(default_factory=list)
    segment_answers: list["FaqSegmentAnswer"] = field(default_factory=list)

    @classmethod
    def from_segments(
        cls, answers: list["FaqSegmentAnswer"], *, abstention_message: str
    ) -> "FaqResult":
        """Assemble the turn's FAQ half from what each of its requests produced.

        Args:
            abstention_message: what the patient is told when every request abstained -
                the whole of the reply in that case, since nothing was generated.

        Raises: ValueError if `answers` is empty, or if their positions are not unique
            and ascending. Order is the message's order, and it is what the reply, the
            console and the derived unserved list all read - so it is structural here
            rather than a promise each caller has to keep.
        """
        if not answers:
            raise ValueError("a turn's FAQ half always answered at least one request")
        positions = [answer.position for answer in answers]
        if positions != sorted(set(positions)):
            raise ValueError(
                "a turn's requests carry unique positions, in ascending order"
            )
        if not any(answer.verdict.answered for answer in answers):
            # Nothing was answered, so the half's whole reply is the constant - the one
            # abstention wording a patient ever sees unaccompanied, and the one reply
            # this design deliberately keeps model-free.
            return cls(answer_text=abstention_message, segment_answers=answers)
        return cls(
            # The half's own single text, and only when it contributes exactly one
            # part. Reaching here means at least one request was answered, so a lone
            # request is an answered one; two requests are two parts - an answer and a
            # gap, or two answers - which have no one text between them.
            answer_text=answers[0].answer_text if len(answers) == 1 else None,
            # Deduplicated within each request rather than across the turn: two chunks
            # of one entry are still two chunks, one chunk a single request's shortlist
            # listed twice is still one, and a chunk that supported two *different*
            # requests is cited under each - which is two provenances, not a duplicate.
            scored_chunks=[
                chunk
                for answer in answers
                for chunk in deduplicate_chunks(answer.scored_chunks)
            ],
            segment_answers=answers,
        )

    @property
    def request_outcomes(self) -> list[RequestOutcome]:
        """Return one outcome per request, in ascending position order."""
        return [answer.request_outcome() for answer in self.segment_answers]

    @property
    def any_answered(self) -> bool:
        """Return True if at least one of this turn's requests was answered.

        Not the negation of `any_abstained`: a turn may do both, which is the whole of
        what this phase serves.
        """
        return any(answer.verdict.answered for answer in self.segment_answers)

    @property
    def any_abstained(self) -> bool:
        """Return True if at least one of this turn's requests could not be answered.

        A predicate, not a summary: it says that a gap exists, and never which gate
        produced it or what the turn as a whole did. Which gate stopped which request
        is each outcome's own to report, and there is no value here that answers it.
        """
        return any(not answer.verdict.answered for answer in self.segment_answers)

    @property
    def part_count(self) -> int:
        """How many parts of the turn's reply this half contributes.

        One per request it answered, plus exactly one for the gap if any request
        abstained - one gap part however many requests fell into it. A turn whose every
        request abstained therefore contributes exactly one part, which is the constant
        message and reaches the patient through the collapse rather than a merge.

        Never more than the number of requests, which is what keeps the routing-time
        bound on `_actual_parts` true.
        """
        answered = sum(1 for a in self.segment_answers if a.verdict.answered)
        return answered + (1 if self.any_abstained else 0)

    def logged_request_outcomes(self) -> list[dict[str, object]]:
        """Return what `turn.completed` says about each request, in position order.

        Each entry carries the request's position, its verdict, and the chunks its
        answer stood on with both scores - `rerank_score` absent on a request answered
        without reranking, not zero and not a copy of the similarity score, because no
        rerank score was ever obtained for it.

        The request's *text* is deliberately not repeated here: it is on
        `intent.classified`, joined by position. Its *answer* is not here either - that
        is on the stored message, which is where a merged reply can be checked against
        the parts it was built from.
        """
        return [
            {
                "position": answer.position,
                "verdict": answer.verdict.value,
                "citations": [
                    {
                        "entry_id": chunk.faq_entry_id,
                        "chunk_index": chunk.chunk_index,
                        "chunk_text": chunk.chunk_text,
                        "similarity_score": chunk.similarity_score,
                        "rerank_score": chunk.rerank_score,
                    }
                    for chunk in deduplicate_chunks(answer.scored_chunks)
                ],
            }
            for answer in self.segment_answers
        ]


async def compose_answer(
    anthropic_client: AsyncAnthropic,
    *,
    segments: list[RequestSegment],
    faq_result: FaqResult | None,
    booking_reply: str | None,
    booking_outcome: BookingOutcome | None,
    reply_to_message_ids: list[str],
    completion: TurnCompletion,
    notice_required: bool = False,
) -> AsyncIterator[ChatTokenEvent | ChatDoneEvent]:
    """Compose and stream the merged reply, recording the turn's completion fields.

    Args:
        segments: The requests this turn carried, for the completion record.
        faq_result: The FAQ specialist's collected output, or None if it did not run.
        booking_reply: The booking specialist's own reply text, or None if it did not
            run.
        booking_outcome: The machine-derived outcome of the booking half, carried into
            the prompt so the composing model is constrained by what actually happened
            rather than by how the booking half phrased it.
        completion: Filled in with the turn's `turn.completed` fields, for the caller
            to emit once the composing node's span has closed.
        notice_required: True when the message also carried a request the assistant is
            not authorized to serve. The notice is rendered *by this call* rather than
            appended verbatim: a merged turn is already paying for the composing call,
            and a reply stitched from a paraphrase and a fixed sentence reads as two
            voices (spec 009 FR-022c1). What is required of the composer is the three
            things the notice must convey, not its wording.

    Yields: the composed reply's tokens, then exactly one `ChatDoneEvent`.

    Raises: TurnPipelineError wrapping any failure of the composing call.

    Citations are carried through from the chunks the FAQ half actually retrieved and
    are never re-reported by the composing model, so a merged answer cites exactly what
    a single-specialist answer would have.

    A reply that ran into `_MAX_TOKENS` is streamed as it stands and recorded as
    `compose.truncated`: the tokens have already reached the patient, and the halves
    this call merged cannot report a cut that happened here.
    """
    prompt = _build_prompt(
        faq_result, booking_reply, booking_outcome, notice_required=notice_required
    )

    answer_parts: list[str] = []
    try:
        async with anthropic_client.messages.stream(
            model=get_settings().GENERATION_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            async for event in stream:
                if event.type == "text":
                    answer_parts.append(event.text)
                    yield ChatTokenEvent(text=event.text)
            # Read after the loop, the same way `answer_small_talk` reads it: the SDK
            # accumulates the final message, and this is the documented way to ask why
            # it stopped. Inside the `try` because a failure to obtain it is a failure
            # of the same call.
            final = await stream.get_final_message()
    except Exception as exc:
        raise TurnPipelineError("generation", exc) from exc

    answer_text = "".join(answer_parts)
    if final.stop_reason == "max_tokens":
        # Logged, not raised: the tokens have already reached the patient, so there is
        # nothing left to fail cleanly. What is owed is a record - a merged reply that
        # ends mid-sentence otherwise looks exactly like a short complete one, and this
        # is the only step of the turn whose own halves cannot report it.
        get_logger().warning(
            "compose.truncated",
            max_tokens=_MAX_TOKENS,
            answer_chars=len(answer_text),
        )
    fields: dict[str, object] = {
        **_segment_fields(segments, faq_result),
        "outcome": "merged",
        "answer_text": answer_text,
        "booking_outcome": booking_outcome,
        # What was merged, beside the fact that something was. `merged` alone covers
        # both a two-specialist turn and one specialist plus a notice, so a reader
        # counting mixed-intent turns needs this to tell them apart - and needs it on
        # this line, not joined from the router's (contracts/log-events.md).
        "notice_included": notice_required,
        "message_ids_unified": reply_to_message_ids,
    }
    if faq_result is not None and not faq_result.any_answered:
        # Only when the reply really is the abstention: a turn that served one request
        # and named a gap for another was answered, and filing it here would count it
        # among the turns that told the patient nothing.
        fields["abstention_message"] = answer_text
    completion.set(**fields)
    yield ChatDoneEvent(
        request_outcomes=(
            faq_result.request_outcomes if faq_result is not None else None
        ),
        answer_source=AnswerSource.MERGED,
    )


def _single_specialist_outcome(answer_source: AnswerSource) -> str:
    """Name the shape of a single-specialist turn, for its `turn.completed` line.

    What the turn did, never what a gate decided: which gate stopped which request is
    each request's own outcome to report, and a turn may now have two different ones.
    """
    if answer_source is AnswerSource.HAND_OFF:
        return "handed_off"
    if answer_source is AnswerSource.SMALL_TALK:
        return "small_talk"
    if answer_source is AnswerSource.BOOKING:
        return "booking"
    return "faq"


def _segment_fields(
    segments: list[RequestSegment], faq_result: "FaqResult | None"
) -> dict[str, object]:
    """Return what `turn.completed` says about the turn's requests.

    `request_outcomes` is where two requests that stopped at different gates are both
    recoverable, together with the evidence each answer stood on. Absent for a turn
    whose FAQ half did not run - a booking reply was never retrieved against, so it has
    nothing to report per request.
    """
    fields: dict[str, object] = {"segment_count": len(segments)}
    if faq_result is not None:
        fields["request_outcomes"] = faq_result.logged_request_outcomes()
    return fields


def record_single_specialist_completion(
    completion: TurnCompletion,
    *,
    answer_source: AnswerSource,
    booking_outcome: BookingOutcome | None,
    answer_text: str,
    reply_to_message_ids: list[str],
    segments: list[RequestSegment],
    faq_result: "FaqResult | None" = None,
) -> None:
    """Record `turn.completed` for a turn whose reply came from one part.

    The no-op composing path still owns the event: this is the one node that runs on
    every path, so keeping it here is what makes "exactly once per turn" true rather
    than a property each specialist has to remember.
    """
    fields: dict[str, object] = {
        **_segment_fields(segments, faq_result),
        "outcome": _single_specialist_outcome(answer_source),
        "answer_text": answer_text,
        "booking_outcome": booking_outcome,
        "message_ids_unified": reply_to_message_ids,
    }
    if faq_result is not None and not faq_result.any_answered:
        # The abstained turn's own long-standing field, kept so a log reader (and the
        # eval harness) can still pick out what the patient was actually told - and set
        # only when every request abstained, which is when the reply is that message.
        fields["abstention_message"] = answer_text
    completion.set(**fields)


def _gap_block(faq_result: FaqResult) -> str:
    """Build the one block naming every request that could not be answered.

    One block however many requests fell into it: a reply repeating the same refusal
    per question reads as several failures rather than one gap.

    The "in your own words" rule is stated here as well as in the system prompt,
    because the input slice and the instruction fail independently - a block that
    arrives without its instruction is one the composer will quote back verbatim, and
    the restatement is a machine's wording, not the patient's.
    """
    questions = "\n".join(
        f'- "{answer.question}"'
        for answer in faq_result.segment_answers
        if not answer.verdict.answered
    )
    return (
        "NO CONFIDENT ANSWER for these questions:\n"
        f"{questions}\n"
        "Say plainly that the clinic's knowledge base does not have this "
        "information, that these questions have been forwarded to staff who will "
        "follow up, and that you can still help with anything else in the meantime. "
        "Refer to what each unanswered question was about in your own words - do NOT "
        "quote the wording above back to the patient, which is a machine restatement "
        "and not what they wrote."
    )


def _build_prompt(
    faq_result: FaqResult | None,
    booking_reply: str | None,
    booking_outcome: BookingOutcome | None,
    *,
    notice_required: bool = False,
) -> str:
    """Build the composing call's single user message from the halves' outputs."""
    parts: list[str] = []
    if faq_result is not None:
        # One block per *answered* request, each naming the question it answers: an
        # answer attached to the wrong question is a wrong answer, not a formatting
        # slip. A request that abstained gets no block here - a label with nothing
        # under it would be worse than none, since the prompt above requires every
        # labelled claim to be preserved exactly, and an empty body is an invitation to
        # write one. It is named in the gap block below instead.
        parts.extend(
            f'Answer to the question "{answer.question}":\n{answer.answer_text}'
            for answer in faq_result.segment_answers
            if answer.verdict.answered
        )
        if faq_result.any_abstained:
            parts.append(_gap_block(faq_result))
    if booking_reply is not None:
        parts.append(f"Appointment part (outcome: {booking_outcome}):\n{booking_reply}")
    if notice_required:
        parts.append(
            "NOT AUTHORIZED: the message also asked for something this assistant is "
            "not authorized to do. It has been forwarded to the clinic's staff. Say so "
            "in your reply, alongside the rest."
        )
    return "\n\n".join(parts)
