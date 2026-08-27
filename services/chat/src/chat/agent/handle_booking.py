"""`handle_booking`: a bounded tool-use loop over the scheduling capabilities.

A booking conversation is genuinely open-ended - the patient may name a specialty, a
person, a day, a vague time, or change their mind mid-sentence - and the number of
capability calls varies per turn. Encoding that as a state machine would mean
re-deriving intent from free text at every state, which is what the model is for. All
scheduling knowledge stays in the tool handlers; all dialogue policy stays in the
prompt.

The turn's `BookingOutcome` is derived from the tool results actually observed, never
from the reply text. That is what makes "never claim an appointment that does not
exist" checkable rather than hoped for.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam
from shared_models.localtime import parse_local_datetime

from chat.agent.history import bound_to_last_n_turns, to_claude_messages
from chat.agent.tools.registry import ToolArgumentError, ToolRegistry
from chat.core.config import get_settings
from chat.core.logging import get_logger
from chat.domain.models import Message
from chat.domain.schemas import ChatTokenEvent

_MAX_TOKENS = 1024
# Enough for the longest legitimate chain - list practitioners, check availability,
# book, and a retry or two after a refusal - with room to spare before the loop is
# doing something other than making progress.
_MAX_ITERATIONS = 6

_SYSTEM_PROMPT = """You are a clinic's booking assistant, talking to {patient_name}.
The patient's current local date and time is {local_now}. Resolve every relative
phrase ("tomorrow", "next Tuesday at 3") against that, and never against your own
sense of the date.

Rules you must follow:
- Only offer times that check_availability returned. Never invent or round one.
- Confirm BOTH the practitioner and the exact start time with the patient before
  calling book_appointment. An appointment cannot be cancelled or changed afterwards.
- Never state or imply that an appointment exists unless book_appointment returned
  status "booked" in this turn.
- When several practitioners could match what the patient asked for, list them and ask
  which they want. Never choose for them.
- Answer any question about who works here, or what they specialize in, from
  list_practitioners and never from memory. Name everyone it returns who fits, and say
  which of them are not currently taking appointments.
- When none matches, say so and name the specialties this clinic actually has - never
  a specialty it does not.
- Speak in plain local time ("Tuesday at 9am"). Never mention a timezone, an internal
  id, or the name of a tool.
- If a booking was refused, explain the reason you were given and offer alternatives."""

_READ_FAILED_EXPLANATION = "That step failed. Nothing was booked."

# A write that raised is not evidence that it did not land: the call may have succeeded
# and the failure come afterwards. Says the one true thing, and forbids the retry that
# would turn an uncertain appointment into two real ones.
_WRITE_FAILED_EXPLANATION = (
    "That step failed, so it is not known whether the appointment was created. Do not "
    "say it was booked, and do not book it again - tell the patient to check with the "
    "clinic before trying again."
)

# Arguments that could not be read are rejected before the handler calls anything, so
# unlike every other failure this one provably had no effect. It says so, and says what
# to fix: the model can correct the call and make it again within this same turn.
_INVALID_ARGUMENTS_EXPLANATION = (
    "That call was rejected before anything happened, so nothing was created: "
    "{detail}. Correct the arguments and call it again."
)

_LOOP_EXHAUSTED_REPLY = (
    "I wasn't able to finish that. Could you tell me again what you'd like to book?"
)

# Used only when the loop ran out while an appointment had in fact been created. The
# reply is written here rather than by the model because the model is exactly what ran
# out - but it may still state the appointment exists, because `_outcome_from` read that
# off the tool result rather than off any text.
_LOOP_EXHAUSTED_BOOKED_REPLY = (
    "You're booked in with {practitioner} on {when}. "
    "Was there anything else you needed?"
)
_LOOP_EXHAUSTED_BOOKED_REPLY_PLAIN = (
    "Your appointment is booked. Was there anything else you needed?"
)


class BookingOutcome(StrEnum):
    """What a booking turn actually achieved, as observed from tool results."""

    BOOKED = "booked"
    REFUSED = "refused"
    UNAVAILABLE = "unavailable"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    INFORMATIONAL = "informational"


@dataclass(frozen=True)
class BookingResult:
    """What `handle_booking` produces for the turn."""

    reply_text: str
    outcome: BookingOutcome
    appointment_id: str | None = None
    iterations: int = 0
    tool_calls: int = 0


def _outcome_from(results: list[dict[str, Any]]) -> tuple[BookingOutcome, str | None]:
    """Derive the turn's outcome from the tool results it actually observed.

    Returns: the outcome, and the booked appointment's id when there is one.

    A booking that succeeded outranks everything: a turn that was refused once and then
    succeeded is a success. Below that, an unreachable service outranks a refusal,
    because "nothing was booked and we could not tell you why" is the more important
    thing to say.

    A turn that wrote nothing is `awaiting_confirmation` only if it actually put times
    in front of the patient - otherwise it merely answered a question and is
    `informational`. The distinction reaches the composing step verbatim, so reporting
    "here is what you have booked" as awaiting confirmation would invite a reply
    implying a pending decision that does not exist.
    """
    booked = [r for r in results if r.get("status") == "booked"]
    if booked:
        appointment = booked[-1].get("appointment", {})
        return BookingOutcome.BOOKED, appointment.get("id")
    if any(r.get("status") in {"unavailable", "error"} for r in results):
        # A step that failed, whether the scheduler never answered or the handler
        # raised. Not `booked`, because nothing here observed an appointment - the
        # result's own explanation is what says whether one might nonetheless exist.
        return BookingOutcome.UNAVAILABLE, None
    if any(r.get("status") == "refused" for r in results):
        return BookingOutcome.REFUSED, None
    if any(r.get("available_starts") for r in results):
        return BookingOutcome.AWAITING_CONFIRMATION, None
    return BookingOutcome.INFORMATIONAL, None


def _exhausted_reply(results: list[dict[str, Any]], outcome: BookingOutcome) -> str:
    """Return what to tell the patient when the loop ran out of iterations.

    A turn can exhaust the loop *after* `book_appointment` succeeded, when the model
    keeps calling tools instead of writing its confirmation. Reporting the generic
    failure then would deny an appointment that exists and cannot be cancelled, and
    invite the patient to book a second one at a different time - which derives a
    different key and really would create it.
    """
    if outcome is not BookingOutcome.BOOKED:
        return _LOOP_EXHAUSTED_REPLY

    booked = [r for r in results if r.get("status") == "booked"]
    appointment = booked[-1].get("appointment", {}) if booked else {}
    practitioner = appointment.get("practitioner_full_name")
    starts_at = appointment.get("starts_at")
    if not practitioner or not starts_at:
        return _LOOP_EXHAUSTED_BOOKED_REPLY_PLAIN
    try:
        when = parse_local_datetime(str(starts_at)).strftime("%A %d %B at %H:%M")
    except ValueError:
        return _LOOP_EXHAUSTED_BOOKED_REPLY_PLAIN
    return _LOOP_EXHAUSTED_BOOKED_REPLY.format(practitioner=practitioner, when=when)


async def handle_booking(
    anthropic_client: AsyncAnthropic,
    registry: ToolRegistry,
    bursts: list[list[Message]],
    *,
    patient_name: str,
    local_now: str,
    stream: bool,
) -> AsyncIterator[ChatTokenEvent | BookingResult]:
    """Run one booking turn, emitting its reply when streaming and always a result.

    Args:
        bursts: The chat's full history; this bounds it to the last few turns itself.
        stream: True when this is the turn's only specialist, so its reply goes straight
            to the patient. False when another specialist also ran and a later step
            composes one reply from both results.

    Yields: in streaming mode, one `ChatTokenEvent` carrying the whole reply, then
        exactly one `BookingResult` as the final item.

    The reply is emitted whole rather than token by token, unlike the FAQ path. Only the
    loop's *last* model call produces the reply - every earlier one is a tool request -
    and which call is the last is not known until it comes back without tool blocks. So
    a turn that calls tools shows nothing until the loop ends.

    The conversation history is bounded to the last five turns, matching every other
    model call in the graph. The `tool_use`/`tool_result` blocks accumulated *within*
    this turn are not history and are never dropped - truncating them mid-loop would
    hide from the model what it has already booked or been refused.
    """
    logger = get_logger()
    settings = get_settings()
    system = _SYSTEM_PROMPT.format(patient_name=patient_name, local_now=local_now)
    messages: list[MessageParam] = list(
        to_claude_messages(bound_to_last_n_turns(bursts, n=settings.CONTEXT_TURNS))
    )
    tools = registry.to_anthropic_tools()
    observed: list[dict[str, Any]] = []
    tool_calls = 0

    for iteration in range(1, _MAX_ITERATIONS + 1):
        response = await anthropic_client.messages.create(
            model=settings.GENERATION_MODEL,
            max_tokens=_MAX_TOKENS,
            system=system,
            messages=messages,
            tools=tools,
        )
        tool_uses = [block for block in response.content if block.type == "tool_use"]
        reply_text = "".join(
            block.text for block in response.content if block.type == "text"
        )

        if not tool_uses:
            if stream:
                yield ChatTokenEvent(text=reply_text)
            outcome, appointment_id = _outcome_from(observed)
            yield BookingResult(
                reply_text=reply_text,
                outcome=outcome,
                appointment_id=appointment_id,
                iterations=iteration,
                tool_calls=tool_calls,
            )
            return

        messages.append({"role": "assistant", "content": response.content})
        tool_calls += len(tool_uses)
        # Run the message's tool calls together. The model emits several blocks when it
        # wants several answers at once ("is Dr. A or Dr. B free Friday?"), and each is
        # a gRPC round trip under its own deadline - awaiting them in turn multiplies
        # the turn's latency by the number of questions asked.
        outcomes = await asyncio.gather(
            *(
                _dispatch(
                    registry,
                    block.name,
                    dict(block.input) if isinstance(block.input, dict) else {},
                    iteration,
                )
                for block in tool_uses
            )
        )
        # Appended in block order rather than completion order: `observed` decides the
        # turn's outcome, and `tool_result` blocks are matched by id, so neither may
        # depend on which call happened to finish first.
        observed.extend(outcomes)
        results: list[dict[str, Any]] = [
            {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result),
            }
            for block, result in zip(tool_uses, outcomes, strict=True)
        ]
        messages.append({"role": "user", "content": results})  # type: ignore[typeddict-item]

    outcome, appointment_id = _outcome_from(observed)
    logger.warning(
        "booking.loop_exhausted", iterations=_MAX_ITERATIONS, outcome=outcome
    )
    reply_text = _exhausted_reply(observed, outcome)
    if stream:
        yield ChatTokenEvent(text=reply_text)
    yield BookingResult(
        reply_text=reply_text,
        outcome=outcome,
        appointment_id=appointment_id,
        iterations=_MAX_ITERATIONS,
        tool_calls=tool_calls,
    )


async def _dispatch(
    registry: ToolRegistry, name: str, arguments: dict[str, Any], iteration: int
) -> dict[str, Any]:
    """Run one tool call, reporting a handler failure to the model rather than raising.

    A handler that raises is a fact the model needs - it should apologize or try
    something else - not a reason to fail the whole node, which would lose a booking
    the same turn may already have made.

    What the failure is reported as depends on whether the tool writes. A read that
    raised created nothing, by construction. A write that raised may have raised
    *after* the appointment was created - rendering its response, say - so claiming
    nothing was booked would be a guess, and the guess that invites a second booking
    the patient cannot cancel.

    Arguments that could not be read are the one failure reported as having created
    nothing even for a write: they are rejected before the handler calls anything, so
    there is no attempt whose fate could be in doubt.
    """
    logger = get_logger()
    logger.info(
        "booking.tool_called", tool_name=name, iteration=iteration, arguments=arguments
    )
    started = time.monotonic()
    try:
        result = await registry.dispatch(name, arguments)
    except ToolArgumentError as exc:
        logger.warning(
            "booking.tool_arguments_invalid",
            tool_name=name,
            iteration=iteration,
            error_detail=str(exc),
        )
        return {
            "status": "error",
            "explanation": _INVALID_ARGUMENTS_EXPLANATION.format(detail=exc),
        }
    except Exception as exc:  # noqa: BLE001 - reported to the model, not raised
        logger.error(
            "booking.tool_failed",
            tool_name=name,
            iteration=iteration,
            error_type=type(exc).__name__,
            error_detail=str(exc),
        )
        return {
            "status": "error",
            "explanation": (
                _WRITE_FAILED_EXPLANATION
                if registry.writes(name)
                else _READ_FAILED_EXPLANATION
            ),
        }

    logger.info(
        "booking.tool_result",
        tool_name=name,
        iteration=iteration,
        status=result.get("status", "ok"),
        reason=result.get("reason"),
        duration_ms=round((time.monotonic() - started) * 1000, 2),
    )
    return result
