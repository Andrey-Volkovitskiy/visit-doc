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
from chat.core.logging import get_logger
from chat.domain.schemas import (
    AnswerSource,
    ChatDoneEvent,
    ChatTokenEvent,
    Citation,
)
from chat.rag.retriever import TurnPipelineError

_MAX_TOKENS = 1024
_SYSTEM_PROMPT = """You are a clinic assistant writing ONE reply to a patient whose
message had two parts: a question, and something about an appointment. Two specialists
have already handled them, and their outputs are below.

Combine them into a single, natural reply. You must:
- Preserve every factual claim exactly as given. Do not add, soften, or strengthen one.
- If the question half says there is no confident answer, say plainly that you do not
  have a confident answer to that part, and that a staff member has been notified and
  will reply in this conversation. Never fill the gap from your own knowledge, and
  never promise when they will reply.
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
- Do not mention that two specialists, tools, or internal steps were involved.
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


@dataclass(frozen=True)
class FaqResult:
    """What `answer_faq` produces for the turn.

    `chunk_scores` holds each citation's retrieval score, positionally. The scores are
    part of the turn's observable record but not of the reply, so they ride here rather
    than on `Citation`, which is a wire type the client reads.
    """

    answer_text: str
    citations: list[Citation]
    grounded: bool
    chunk_scores: list[float] = field(default_factory=list)

    def scored_citations(self) -> list[dict[str, object]]:
        """Return each citation with the score it was retrieved at, for the log line."""
        return [
            {**citation.model_dump(), "score": score}
            for citation, score in zip(self.citations, self.chunk_scores, strict=True)
        ]


async def compose_answer(
    anthropic_client: AsyncAnthropic,
    *,
    faq_result: FaqResult | None,
    booking_reply: str | None,
    booking_outcome: BookingOutcome | None,
    reply_to_message_ids: list[str],
    completion: TurnCompletion,
) -> AsyncIterator[ChatTokenEvent | ChatDoneEvent]:
    """Compose and stream the merged reply, recording the turn's completion fields.

    Args:
        faq_result: The FAQ specialist's collected output, or None if it did not run.
        booking_reply: The booking specialist's own reply text, or None if it did not
            run.
        booking_outcome: The machine-derived outcome of the booking half, carried into
            the prompt so the composing model is constrained by what actually happened
            rather than by how the booking half phrased it.
        completion: Filled in with the turn's `turn.completed` fields, for the caller
            to emit once the composing node's span has closed.

    Yields: the composed reply's tokens, then exactly one `ChatDoneEvent`.

    Raises: TurnPipelineError wrapping any failure of the composing call.

    Citations are carried through from the chunks the FAQ half actually retrieved and
    are never re-reported by the composing model, so a merged answer cites exactly what
    a single-specialist answer would have.
    """
    prompt = _build_prompt(faq_result, booking_reply, booking_outcome)

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
    except Exception as exc:
        raise TurnPipelineError("generation", exc) from exc

    answer_text = "".join(answer_parts)
    citations = faq_result.citations if faq_result is not None else []
    grounded = faq_result.grounded if faq_result is not None else None
    fields: dict[str, object] = {
        "outcome": "merged",
        "answer_source": AnswerSource.MERGED,
        "answer_text": answer_text,
        "grounded": grounded,
        "booking_outcome": booking_outcome,
        "message_ids_unified": reply_to_message_ids,
        "citations": (faq_result.scored_citations() if faq_result is not None else []),
    }
    if grounded is False:
        # Carried on a merged turn too, so a log query or eval harness counting
        # abstentions does not silently miss exactly the mixed-intent traffic this
        # node exists to serve.
        fields["abstention_message"] = answer_text
    completion.set(**fields)
    yield ChatDoneEvent(
        grounded=grounded,
        citations=citations,
        answer_source=AnswerSource.MERGED,
    )


def _single_specialist_outcome(
    answer_source: AnswerSource, grounded: bool | None
) -> str:
    """Describe a single-specialist turn's outcome for its `turn.completed` line.

    `answer_source` decides before `grounded` does: a handoff and a booking reply both
    carry no groundedness verdict, and reading one off the other would file every
    handed-off turn in the log as a booking.
    """
    if answer_source is AnswerSource.HAND_OFF:
        return "handed_off"
    if grounded is None:
        return "booking"
    return "grounded" if grounded else "abstained"


def record_single_specialist_completion(
    completion: TurnCompletion,
    *,
    answer_source: AnswerSource,
    grounded: bool | None,
    booking_outcome: BookingOutcome | None,
    answer_text: str,
    citations: list[dict[str, object]],
    reply_to_message_ids: list[str],
) -> None:
    """Record `turn.completed` for a turn whose sole specialist streamed its own reply.

    The no-op composing path still owns the event: this is the one node that runs on
    every path, so keeping it here is what makes "exactly once per turn" true rather
    than a property each specialist has to remember.
    """
    fields: dict[str, object] = {
        "outcome": _single_specialist_outcome(answer_source, grounded),
        "answer_source": answer_source,
        "answer_text": answer_text,
        "grounded": grounded,
        "booking_outcome": booking_outcome,
        "message_ids_unified": reply_to_message_ids,
        "citations": citations,
    }
    if grounded is False:
        # The abstained turn's own long-standing field, kept so a log reader (and the
        # eval harness) can still pick out what the patient was actually told.
        fields["abstention_message"] = answer_text
    completion.set(**fields)


def _build_prompt(
    faq_result: FaqResult | None,
    booking_reply: str | None,
    booking_outcome: BookingOutcome | None,
) -> str:
    """Build the composing call's single user message from both halves' outputs."""
    parts: list[str] = []
    if faq_result is not None:
        if faq_result.grounded:
            parts.append(f"Answer to the question part:\n{faq_result.answer_text}")
        else:
            parts.append(
                "Answer to the question part:\n"
                "There is no confident answer to this part. Say so plainly, and say "
                "that a staff member has been notified and will reply here."
            )
    if booking_reply is not None:
        parts.append(f"Appointment part (outcome: {booking_outcome}):\n{booking_reply}")
    return "\n\n".join(parts)
