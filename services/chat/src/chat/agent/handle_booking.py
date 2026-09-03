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

from chat.agent.escalation import EscalationRequests
from chat.agent.history import (
    bound_to_last_n_turns,
    render_silent_window,
    silent_window,
    to_claude_messages,
    to_loggable_messages,
    trailing_question,
)
from chat.agent.tools.registry import ToolArgumentError, ToolRegistry
from chat.core.config import get_settings
from chat.core.logging import get_logger
from chat.domain.models import EscalationReason, Message
from chat.domain.schemas import ChatTokenEvent
from chat.rag.retriever import TurnPipelineError

_MAX_TOKENS = 1024
# The capability the node reads for itself, before the model gets a turn.
_LIST_PRACTITIONERS = "list_practitioners"
# Enough for the longest legitimate chain - list practitioners, check availability,
# book, and a retry or two after a refusal - with room to spare before the loop is
# doing something other than making progress.
_MAX_ITERATIONS = 6

_SYSTEM_PROMPT = """You are a clinic's booking assistant, talking to {patient_name}.
Your goal is to answer the last message in the conversation, previous messages are
context.

The patient's current local date and time is {local_now}. Resolve every relative
phrase ("tomorrow", "next Tuesday at 3") against that, and never against your own
sense of the date.

{practitioners}

Rules you must follow:
- Only offer times that check_availability returned. Never invent or round one.
- Confirm BOTH the practitioner and the exact start time with the patient before
  calling book_appointment.
  Don't ask for confirmation if the patient already confirmed this appointment before.
- Never state or imply that an appointment exists unless book_appointment returned
  status "booked" in this turn.
- When several practitioners could match what the patient asked for, list them and ask
  which they want. Never choose for them.
- Speak in plain local time ("Tuesday at 9am"). Never mention a timezone, an internal
  id, or the name of a tool.
- If a booking was refused, explain the reason you were given and offer alternatives.

Changing and cancelling an existing appointment:
- An appointment_id is not something you can work out, shorten, or describe. Every one
  comes from a list_my_appointments result in THIS turn - earlier turns' tool results
  are not in the conversation you can see, only what you and the patient said. So when
  the patient confirms a change, call list_my_appointments first to get the id, even
  though you already know which appointment they mean and they have already said yes.
  Never send a placeholder or a made-up id: it is refused, and the refusal reads as the
  patient having no such appointment.
- Reading the list back gives you the id and nothing else. expected_starts_at and
  expected_practitioner_id are still what you told the patient - if the listed start
  differs from the one you read out, send the one you read out and let the refusal tell
  you it changed.
- NEVER call cancel_appointment without an explicit confirmation from the patient given
  in the CURRENT turn. A yes from an earlier turn does not carry over.
- A confirmation binds only for the turn it was asked in. If anything intervening has
  happened since, answer what the patient actually said and then re-state the
  confirmation in full before accepting a "yes".
- A reply that neither confirms nor declines is NOT a decline. Answer it, keep the
  offer, and ask again. Never make the patient restate the appointment, the
  practitioner or the time they have already given you.
- Before every change, state: the start date-time, the practitioner's full name, and
  their specialty. For a move, state both the current and the proposed start.
- When offering times for a move, call check_availability with
  excluded_appointment_id set to the appointment being moved. Without it the time it
  currently holds is missing from its own options. Offer only times that call
  returned - never one you invented, rounded, or worked out yourself.
- What to do about each refusal reason:
    * practitioner_busy, patient_busy, outside_schedule, off_grid, in_past,
      beyond_horizon - call check_availability and offer other times.
    * stale_confirmation - describe the appointment as it now stands and ask again.
      Never re-issue the change, and never treat the earlier yes as covering the new
      state.
    * appointment_not_found, already_cancelled, already_started - say plainly what is
      so and invent no alternative times. These three admit none.
- For a practitioner swap, name both practitioners, with both specialties, and frame
  it as the same appointment changing hands - not as one appointment ending and
  another beginning.
- State the new length or end time whenever the change differs from the old one; say
  nothing about the length when it does not. A 15-minute appointment becoming an hour
  is not something to discover on arrival.
- When nobody matches the specialty the patient asked for, say so and name the
  specialties that do exist - never one that does not - and leave the appointment
  exactly as it is.
- When the request could mean more than one appointment, list the candidates and ask
  which they mean. Never choose for them, and never act on more than one appointment
  per confirmation.
- expected_starts_at and expected_practitioner_id must be exactly the values you stated
  to the patient when you asked them to confirm - never values you have just re-read.
- Never state or imply that an appointment was moved or cancelled unless a tool
  returned status "changed" or "unchanged" in this turn. A result of "unchanged" means
  the appointment was already in that state: report it as done, never as a failure and
  never as a second change.
- On status "unknown", say the outcome is not known. Do not claim it happened, do not
  claim it did not, and do not retry.
- Never mention an appointment id, a practitioner id, or the name of a tool to the
  patient. You use ids to call tools; the patient never sees one.
- Cancellation is final. Do not offer to "restore" a cancelled appointment - offer to
  book again, and only after check_availability shows the time is still free.
- When the patient asks what they have CANCELLED, call list_my_appointments with
  status_filter "cancelled" AND time_filter "both". A cancellation is not something
  they are still waiting for, so the question spans both sides of now - and the
  defaults would otherwise return only cancellations still in the future, which is
  almost none of them. Widening the status axis does not widen the time axis for you.
- When list_my_appointments returns past_truncated true, say that THAT PART of the list
  - the past appointments - is not complete. Never say the whole answer is partial, and
  never say it about the future appointments, which are always complete.
- If the patient has no appointments at all, say so plainly. Do not present an empty
  list."""

# The roster is read once per turn and put in front of the model, because the failure
# it prevents is the model inventing a `practitioner_id` from a name it read in the
# conversation: the call is refused, and the turn spends two more model round trips
# recovering from a guess it was never given the means to avoid.
_ROSTER_KNOWN = """The clinic's practitioners, read at the start of this turn:
{roster}

Answer any question about who works here, or what they specialize in, from that list
and never from memory. Name everyone in it who fits, and say which of them are not
currently taking appointments. When a tool asks for a practitioner_id, pass one exactly
as it appears above - never build one out of a name. When nobody there matches what the
patient asked for, say so and name the specialties that are listed - never one that is
not."""

_ROSTER_EMPTY = "(nobody - this clinic currently has no practitioners at all)"

# Says what is not known rather than leaving the model to assume, because the tool call
# it is told to make is against the same service that just failed. Naming a
# practitioner from earlier in the conversation is exactly what it would otherwise do,
# and that name would be unverifiable at the moment it is said.
_ROSTER_UNKNOWN = """The clinic's practitioner list could not be read at the start of
this turn. You do not know who works here.

Call list_practitioners before you name anyone or book anything, and answer any
question about who works here from what it returns. If that call fails too, tell the
patient the clinic's schedule cannot be reached right now and that nothing was booked -
do not name a practitioner or a specialty from memory or from earlier in this
conversation, and do not offer any appointment time."""

_READ_FAILED_EXPLANATION = "That step failed. Nothing was booked."

# A write that raised is not evidence that it did not land: the call may have succeeded
# and the failure come afterwards. Says the one true thing, and forbids the retry that
# would turn an uncertain appointment into two real ones.
_WRITE_FAILED_EXPLANATION = (
    "That step failed, so it is not known whether the appointment was created. Do not "
    "say it was booked, and do not book it again - tell the patient to check with the "
    "clinic before trying again."
)

# The same for a change. Booking's sentence names the wrong act and, worse, omits the
# claim this path actually forbids - that the change did, or did not, take effect.
_CHANGE_FAILED_EXPLANATION = (
    "That step failed, so it is not known whether the change went through. Do not say "
    "the appointment was changed, do not say it was not, and do not try it again - "
    "tell the patient to check with the clinic."
)

# Arguments that could not be read are rejected before the handler calls anything, so
# unlike every other failure this one provably had no effect. It says so, and says what
# to fix: the model can correct the call and make it again within this same turn.
_INVALID_ARGUMENTS_EXPLANATION = (
    "That call was rejected before anything happened, so nothing was created: "
    "{detail}. Correct the arguments and call it again."
)

# Which operation each change tool performs, for the change record. Read from the tool
# name rather than the result, because the record has to be emitted for a result that
# says only that the outcome is unknown.
_OPERATION_BY_TOOL = {
    "cancel_appointment": "cancel",
    "reschedule_appointment": "reschedule",
}

# The two result statuses that mean the assistant could not answer, as distinct from
# `refused`, which is an answer. `error` is absent: it is what a *read* that raised is
# reported as, and that path records its own call above - reading it here too would
# record the same failure twice.
_FAILED_STATUSES = frozenset({"unavailable", "unknown"})

# Labels the half of a rewritten entry that is actually the request, for the turns that
# follow a silence. Absent from every other turn, whose entries are untouched.
_ANSWERING_HEADING = "The message you are answering:"

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

# The same problem for a change, and worse: a cancellation cannot be undone and its slot
# may already be gone, so the generic reply would deny a cancellation that happened and
# invite the patient to rebook a time that is no longer theirs.
_LOOP_EXHAUSTED_CANCELLED_REPLY = (
    "Your appointment with {practitioner} on {when} is cancelled. "
    "Was there anything else you needed?"
)
_LOOP_EXHAUSTED_CANCELLED_REPLY_PLAIN = (
    "That appointment is cancelled. Was there anything else you needed?"
)
_LOOP_EXHAUSTED_RESCHEDULED_REPLY = (
    "Your appointment is now with {practitioner} on {when}. "
    "Was there anything else you needed?"
)
_LOOP_EXHAUSTED_RESCHEDULED_REPLY_PLAIN = (
    "Your appointment has been moved. Was there anything else you needed?"
)
_LOOP_EXHAUSTED_UNCHANGED_REPLY = (
    "That appointment was already as you asked, so nothing needed to change. "
    "Was there anything else you needed?"
)
# Says neither that the change happened nor that it did not - the only two sentences
# this path is forbidden.
_LOOP_EXHAUSTED_UNKNOWN_REPLY = (
    "I couldn't confirm whether that went through. Please check with the clinic "
    "before trying again."
)


class BookingOutcome(StrEnum):
    """What a booking turn actually achieved, as observed from tool results.

    The members are values with meanings, so a change that completed is not a booking
    that completed: reusing `BOOKED` for a reschedule would make one value stand for two
    situations in exactly the place - the composing step's truth constraint - where that
    confusion becomes a false statement to a patient.

    `OUTCOME_UNKNOWN` is the one that is neither success nor failure: the request was
    sent and no answer arrived, so what happened is genuinely not known.
    """

    BOOKED = "booked"
    RESCHEDULED = "rescheduled"
    CANCELLED = "cancelled"
    UNCHANGED = "unchanged"
    OUTCOME_UNKNOWN = "outcome_unknown"
    REFUSED = "refused"
    UNAVAILABLE = "unavailable"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    INFORMATIONAL = "informational"


# Which completed-change outcome a tool result stands for, keyed by the `change` field
# the handler set. A completed change says which change it was rather than leaving the
# node to infer it from which tool was called.
_OUTCOME_BY_CHANGE = {
    "cancelled": BookingOutcome.CANCELLED,
    "rescheduled": BookingOutcome.RESCHEDULED,
}


@dataclass(frozen=True)
class BookingResult:
    """What `handle_booking` produces for the turn."""

    reply_text: str
    outcome: BookingOutcome
    appointment_id: str | None = None
    iterations: int = 0
    tool_calls: int = 0


def _elapsed_ms(started: float) -> float:
    """Return milliseconds elapsed since `started`, to two decimal places."""
    return round((time.monotonic() - started) * 1000, 2)


async def _read_roster(registry: ToolRegistry) -> list[Any] | None:
    """Read the clinic's practitioners for this turn's prompt.

    Returns: the practitioners as the capability reported them, or None when they
        could not be read at all.

    Never an empty list for that second case: a clinic with nobody on it and a clinic
    whose roster is unknown need opposite replies, and the reply to the second one must
    not name anyone.

    A failure here is not the turn's failure - the loop still runs, told that it does
    not know who works here - so every way this can fail is caught.
    """
    logger = get_logger()
    try:
        result = await registry.dispatch(_LIST_PRACTITIONERS, {})
    except Exception as exc:  # noqa: BLE001 - degrades the prompt, never the turn
        logger.warning(
            "booking.roster_unread",
            error_type=type(exc).__name__,
            error_detail=str(exc),
        )
        return None

    practitioners = result.get("practitioners")
    if practitioners is None:
        # The capability answered, and its answer was that it could not tell us -
        # `status` carries which failure that was.
        logger.warning("booking.roster_unread", status=result.get("status"))
        return None

    logger.info("booking.roster_read", practitioner_count=len(practitioners))
    return list(practitioners)


def _practitioners_section(roster: list[Any] | None) -> str:
    """Render the prompt's practitioner section from what the roster read returned."""
    if roster is None:
        return _ROSTER_UNKNOWN
    lines = "\n".join(_practitioner_line(entry) for entry in roster)
    return _ROSTER_KNOWN.format(roster=lines or _ROSTER_EMPTY)


def _practitioner_line(entry: Any) -> str:
    """Render one practitioner as a line of the prompt's roster."""
    if not isinstance(entry, dict):
        return "- (unreadable entry)"
    bookable = "" if entry.get("bookable") else ", not currently taking appointments"
    return (
        f"- id {entry.get('id')}: {entry.get('full_name')}"
        f", {entry.get('specialty')}"
        f", {entry.get('appointment_duration_minutes')}-minute appointments{bookable}"
    )


def _outcome_from(results: list[dict[str, Any]]) -> tuple[BookingOutcome, str | None]:
    """Derive the turn's outcome from the tool results it actually observed.

    Returns: the outcome, and the appointment's id when one was booked or changed.

    A completed booking or change outranks everything: a turn that was refused once and
    then succeeded is a success. Below that, `unchanged` outranks an unknown outcome
    because a request that provably transitioned nothing is more informative than one
    whose fate is unclear; an unknown outcome outranks an unreachable service, because
    "we cannot tell you whether your appointment changed" is the more important thing to
    say than "nothing happened" - and saying the latter when the former is true is
    exactly the claim a lost write forbids. An unreachable service still outranks a
    refusal.

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

    changed = [r for r in results if r.get("status") == "changed"]
    if changed:
        last = changed[-1]
        appointment = last.get("appointment", {})
        # `.get()` on a value the handler set, not indexed: an unrecognized `change`
        # must not raise mid-turn over an appointment that really was altered.
        outcome = _OUTCOME_BY_CHANGE.get(str(last.get("change")))
        if outcome is None:
            get_logger().error("booking.unknown_change_kind", change=last.get("change"))
            outcome = BookingOutcome.OUTCOME_UNKNOWN
        return outcome, appointment.get("id")

    # An unknown outcome outranks a no-op, though both rank below a completed change.
    # `results` accumulates across every tool call the turn made - the model can ask for
    # several at once - so one turn can hold a provable no-op *and* a lost write. Only
    # `unchanged` asserts positively that nothing was written, and a turn holding a lost
    # write cannot make that claim: the weaker, safer label has to win.
    if any(r.get("status") == "unknown" for r in results):
        return BookingOutcome.OUTCOME_UNKNOWN, None
    if any(r.get("status") == "unchanged" for r in results):
        unchanged = [r for r in results if r.get("status") == "unchanged"][-1]
        return BookingOutcome.UNCHANGED, unchanged.get("appointment", {}).get("id")
    if any(r.get("status") in {"unavailable", "error"} for r in results):
        # A step that failed, whether the scheduler never answered or the handler
        # raised. Not a completed change, because nothing here observed one - the
        # result's own explanation is what says whether one might nonetheless exist.
        return BookingOutcome.UNAVAILABLE, None
    if any(r.get("status") == "refused" for r in results):
        return BookingOutcome.REFUSED, None
    if any(r.get("available_starts") for r in results):
        return BookingOutcome.AWAITING_CONFIRMATION, None
    return BookingOutcome.INFORMATIONAL, None


def _exhausted_reply(results: list[dict[str, Any]], outcome: BookingOutcome) -> str:
    """Return what to tell the patient when the loop ran out of iterations.

    A turn can exhaust the loop *after* a write succeeded, when the model keeps calling
    tools instead of writing its confirmation. Reporting the generic failure then would
    deny something that really happened - an appointment that exists and cannot be
    cancelled, or a cancellation that cannot be undone - and invite the patient to act
    on a state that is no longer true.

    So every outcome that observed a completed write gets its own reply, and the generic
    one is left to the outcomes where nothing was written. The unknown outcome gets a
    third kind again: it may claim neither that the change happened nor that it did not.
    """
    if outcome is BookingOutcome.BOOKED:
        return _completed_exhausted_reply(
            results,
            "booked",
            _LOOP_EXHAUSTED_BOOKED_REPLY,
            _LOOP_EXHAUSTED_BOOKED_REPLY_PLAIN,
        )
    if outcome is BookingOutcome.CANCELLED:
        return _completed_exhausted_reply(
            results,
            "changed",
            _LOOP_EXHAUSTED_CANCELLED_REPLY,
            _LOOP_EXHAUSTED_CANCELLED_REPLY_PLAIN,
        )
    if outcome is BookingOutcome.RESCHEDULED:
        return _completed_exhausted_reply(
            results,
            "changed",
            _LOOP_EXHAUSTED_RESCHEDULED_REPLY,
            _LOOP_EXHAUSTED_RESCHEDULED_REPLY_PLAIN,
        )
    if outcome is BookingOutcome.UNCHANGED:
        return _LOOP_EXHAUSTED_UNCHANGED_REPLY
    if outcome is BookingOutcome.OUTCOME_UNKNOWN:
        return _LOOP_EXHAUSTED_UNKNOWN_REPLY
    return _LOOP_EXHAUSTED_REPLY


def _completed_exhausted_reply(
    results: list[dict[str, Any]], status: str, template: str, plain: str
) -> str:
    """Render the reply for a write that completed before the loop ran out.

    Written here rather than by the model because the model is exactly what ran out -
    but it may state what happened, because `_outcome_from` read that off the tool
    result rather than off any text. Falls back to `plain` whenever the appointment
    cannot be described, so a missing field degrades the wording rather than the claim.
    """
    observed = [r for r in results if r.get("status") == status]
    appointment = observed[-1].get("appointment", {}) if observed else {}
    practitioner = appointment.get("practitioner_full_name")
    starts_at = appointment.get("starts_at")
    if not practitioner or not starts_at:
        return plain
    try:
        when = parse_local_datetime(str(starts_at)).strftime("%A %d %B at %H:%M")
    except ValueError:
        return plain
    return template.format(practitioner=practitioner, when=when)


async def handle_booking(
    anthropic_client: AsyncAnthropic,
    registry: ToolRegistry,
    bursts: list[list[Message]],
    *,
    patient_name: str,
    local_now: str,
    stream: bool,
    escalation: EscalationRequests,
) -> AsyncIterator[ChatTokenEvent | BookingResult]:
    """Run one booking turn, emitting its reply when streaming and always a result.

    Args:
        bursts: The chat's full history; this bounds it to the last few turns itself.
        stream: True when this is the turn's only specialist, so its reply goes straight
            to the patient. False when another specialist also ran and a later step
            composes one reply from both results.
        escalation: This turn's collector of calls to staff. A tool call that *failed* -
            as distinct from one that was refused - records one into it.

    Yields: in streaming mode, one `ChatTokenEvent` carrying the whole reply, then
        exactly one `BookingResult` as the final item.

    Raises: TurnPipelineError wrapping any failure of the loop's model call. A tool
        that fails is not one of those - it is answered to the model and the loop
        continues.

    The reply is emitted whole rather than token by token, unlike the FAQ path. Only the
    loop's *last* model call produces the reply - every earlier one is a tool request -
    and which call is the last is not known until it comes back without tool blocks. So
    a turn that calls tools shows nothing until the loop ends.

    The conversation history is bounded to the last five turns, matching every other
    model call in the graph. The `tool_use`/`tool_result` blocks accumulated *within*
    this turn are not history and are never dropped - truncating them mid-loop would
    hide from the model what it has already booked or been refused.

    The clinic's roster is read before the first model call and put in the prompt, so
    the model has the ids it would otherwise guess at. A roster that cannot be read
    leaves the turn running without it, told that it does not know who works here.
    """
    logger = get_logger()
    settings = get_settings()
    system = _SYSTEM_PROMPT.format(
        patient_name=patient_name,
        local_now=local_now,
        practitioners=_practitioners_section(await _read_roster(registry)),
    )
    bounded = bound_to_last_n_turns(bursts, n=settings.CONTEXT_TURNS)
    messages: list[MessageParam] = list(to_claude_messages(bounded))
    silenced = render_silent_window(silent_window(bounded))
    if silenced:
        # This loop has no question field of its own - the prompt above tells the model
        # to answer the last message in the conversation - and after a silent window
        # that last entry is the window and the new message rejoined into one, with
        # nothing to tell them apart by. Restated here so it can: acting on the window
        # would cancel or book something a person had already taken over.
        messages[-1] = {
            "role": "user",
            "content": (
                f"{silenced}\n\n{_ANSWERING_HEADING}\n{trailing_question(bounded)}"
            ),
        }
    tools = registry.to_anthropic_tools()
    observed: list[dict[str, Any]] = []
    tool_calls = 0

    for iteration in range(1, _MAX_ITERATIONS + 1):
        # What the model is about to answer from, in full: the bounded history plus
        # every tool exchange this turn has accumulated so far. A booking loop that
        # goes wrong usually went wrong on what it could see, not on what it was told.
        logger.debug(
            "booking.model_request",
            iteration=iteration,
            messages=to_loggable_messages(messages),
        )
        started = time.monotonic()
        try:
            response = await anthropic_client.messages.create(
                model=settings.GENERATION_MODEL,
                max_tokens=_MAX_TOKENS,
                system=system,
                messages=messages,
                tools=tools,
            )
        except Exception as exc:
            # Only the model call is inside this - the tool dispatch below reports its
            # own failures to the model and never raises. Widening it to the rest of
            # the iteration would file a scheduler outage under `generation` and send
            # an operator to the model API for it.
            raise TurnPipelineError("generation", exc) from exc
        model_duration_ms = _elapsed_ms(started)
        tool_uses = [block for block in response.content if block.type == "tool_use"]
        reply_text = "".join(
            block.text for block in response.content if block.type == "text"
        )
        # Every iteration waits on this call, and none of the surrounding events time
        # it - without this, the seconds between one `booking.tool_result` and the next
        # `booking.tool_called` belong to nothing in the log.
        logger.info(
            "booking.model_call",
            iteration=iteration,
            duration_ms=model_duration_ms,
            tool_names=[block.name for block in tool_uses],
            text=reply_text,
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
                    escalation,
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
    registry: ToolRegistry,
    name: str,
    arguments: dict[str, Any],
    iteration: int,
    escalation: EscalationRequests,
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

    A *failure* here calls a person; a *refusal* does not. A refusal is an answer - the
    slot is taken, the appointment has already started - and an alternative is offered
    with it. A failure is the absence of an answer, and arguments that could not be read
    are not one either: the model gets another attempt inside the same turn, so
    escalating would call a person for a typo it then corrected.
    """
    logger = get_logger()
    logger.info(
        "booking.tool_called", tool_name=name, iteration=iteration, arguments=arguments
    )
    started = time.monotonic()
    try:
        result = await registry.dispatch(name, arguments)
    except ToolArgumentError as exc:
        # Deliberately records nothing: see the docstring.
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
        # A handler that raised, or a name the registry does not hold: either way the
        # assistant could not do what it was asked.
        escalation.record(EscalationReason.ASSISTANT_FAILED)
        logger.error(
            "booking.tool_failed",
            tool_name=name,
            iteration=iteration,
            error_type=type(exc).__name__,
            error_detail=str(exc),
        )
        if registry.writes(name):
            # The handler may have raised *after* the write landed - rendering its
            # response, say - so what happened is genuinely unknown. `error` folds into
            # `UNAVAILABLE`, which the composing step is told means nothing was
            # created, moved or cancelled: the status has to carry the same meaning its
            # explanation already does.
            failed: dict[str, Any] = {
                "status": "unknown",
                "explanation": (
                    _CHANGE_FAILED_EXPLANATION
                    if name in _OPERATION_BY_TOOL
                    else _WRITE_FAILED_EXPLANATION
                ),
            }
            # Recorded here as well as on the answering path: this is an unknown write
            # outcome like any other, and it took the one route that skipped the event.
            _record_unknown_outcome(name, arguments, failed)
            return failed
        return {"status": "error", "explanation": _READ_FAILED_EXPLANATION}

    logger.info(
        "booking.tool_result",
        tool_name=name,
        iteration=iteration,
        status=result.get("status", "ok"),
        reason=result.get("reason"),
        duration_ms=_elapsed_ms(started),
    )
    if result.get("status") in _FAILED_STATUSES:
        escalation.record(EscalationReason.ASSISTANT_FAILED)
    _record_unknown_outcome(name, arguments, result)
    return result


def _record_unknown_outcome(
    name: str, arguments: dict[str, Any], result: dict[str, Any]
) -> None:
    """Record that a change was sent and its outcome never came back.

    Emitted here rather than by the scheduler, which by definition never learns that
    its answer was lost. The transport failure itself is already described by
    `scheduling.unavailable`; this records the domain consequence - that the outcome of
    a write is genuinely unknown - which is an operator's problem even though the turn
    answered the patient correctly.

    `attempts` is the call budget that was spent before giving up.
    """
    if result.get("status") != "unknown":
        return
    operation = _OPERATION_BY_TOOL.get(name)
    if operation is None:
        return
    get_logger().error(
        "change.outcome_unknown",
        operation=operation,
        appointment_id=arguments.get("appointment_id"),
        attempts=get_settings().SCHEDULING_MAX_ATTEMPTS,
    )
