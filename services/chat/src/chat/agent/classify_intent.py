"""`classify_intent`: structured-output intent classification."""

from typing import Any

from anthropic import AsyncAnthropic

from chat.agent.history import to_claude_messages
from chat.clients.anthropic_failure import AnthropicFailure, classify_failure
from chat.core.config import get_settings
from chat.domain.models import Message
from chat.domain.schemas import IntentClassificationResult, IntentLabel

_MAX_TOKENS = 256
_SYSTEM_PROMPT = (
    "Classify the visitor's most recent message into every intent that applies, given "
    "the conversation so far: faq_question (a clinic policy/FAQ question), booking "
    "(anything only the clinic's live appointment records can answer - booking, "
    "rescheduling, cancelling, or listing appointments, and equally asking which "
    "practitioners this clinic has, what a named practitioner specializes in, or when "
    "one of them next has a free appointment), "
    "small_talk (the message asks for nothing the clinic could act on - a greeting, an "
    "acknowledgement, a thank-you, a farewell, a reaction, a note that they are "
    "thinking it over, a message that is unintelligible, or a question about something "
    "the clinic has nothing to do with, such as the weather. A message that asks "
    "anything about the clinic, an appointment, a practitioner or the patient's own "
    'care is NEVER small_talk, however short or polite it is: "what time should I '
    'arrive?" is faq_question, not a pleasantry. A bare "ok", "yes", "sure" or '
    '"perfect" that answers a question you just asked is never small_talk either - '
    "it belongs to whatever you asked about), "
    "urgent_condition (the message describes a condition needing immediate attention "
    "right now - severe, sudden, or dangerous, the kind of thing an emergency "
    "department exists for. Chest pain, difficulty breathing, heavy bleeding, a "
    "suspected overdose, fainting, a child who cannot be roused properly, stroke signs "
    "or sudden severe pain are this however calmly they are put, and remain this when "
    "the patient also asks for an appointment. Everyday hyperbole about an ordinary "
    "complaint is not this "
    '("my tooth is killing me", "this headache is brutal"), and neither is '
    "anything described in the past tense, as having happened last week or last "
    "month, or as "
    "since resolved - those are ordinary requests, however they are worded), "
    "distress (the message expresses real fear, panic or acute upset about their "
    "health, their care, or something happening to them - whether or not it asks for "
    "anything. A brief exclamation or a mild reaction on its own is not this: "
    '"oh no", "ugh", "yikes" are small_talk, and so is dismay about something '
    "ordinary like a wait or a full calendar), "
    "booking_for_another (the message explicitly says the appointment is for someone "
    "other than the person in this chat. The other person must be the one the "
    'appointment is FOR: "book me in with whoever my daughter saw" books for the '
    "patient and is booking. Merely mentioning another person is not this, and neither "
    "is an unclear case - both of those are booking), "
    "call_staff (the patient explicitly asks to speak to a human), unknown "
    "(a request the assistant is not authorized to serve, such as a sick note, a "
    "prescription, a records transfer or a billing correction). "
    "A message may carry more than one intent at once. "
    "If a message could be read either as asking for something or as asking for "
    "nothing, it is not small_talk: answering a real request with a pleasantry is the "
    "worse mistake, and the other paths know how to say they cannot help. "
    "The same words mean different things at different points in a conversation, so "
    'read the message against what was said before it. "OK" after arrival '
    'instructions is small_talk; the same "OK" after you offered a specific '
    "appointment slot is the patient confirming that booking."
)

# The classifier's own request schema, built from `IntentClassificationResult`'s
# schema but with `CLASSIFICATION_FAILED` excluded from the `intents` enum - that
# value is assigned only by orchestration code on a failed/invalid call (FR-007), so
# it must be structurally unreachable from the model's own response (research.md #3).
# `additionalProperties: false` is required by the API for any `object`-typed JSON
# Outputs schema - Pydantic's own `model_json_schema()` doesn't set it by default.
_RESPONSE_SCHEMA: dict[str, Any] = IntentClassificationResult.model_json_schema()
_RESPONSE_SCHEMA["$defs"]["IntentLabel"]["enum"] = [
    label.value
    for label in IntentLabel
    if label is not IntentLabel.CLASSIFICATION_FAILED
]
_RESPONSE_SCHEMA["additionalProperties"] = False


class ClassificationFailedError(Exception):
    """Raised when a `classify_intent()` call fails or returns an invalid result.

    `failure` separates the things that raise this. They are one failure to this
    function's caller, which falls back either way, and different events to an
    operator - only `UNREACHABLE` is an outage. A response that came back and would
    not parse is `ANSWERED`: what failed is the response, not reaching it.

    `failure` is a required positional argument, deliberately. It is not in `args` -
    `str(exc)` has to stay the message, since that is what the caller logs as
    `error_detail` - so anything that rebuilds this exception from `args` alone loses
    it. With a default that loss is silent and downgrades an outage to a parse error;
    without one, `exc.__class__(*exc.args)` raises `TypeError` where it is written,
    the same way `TurnPipelineError` does.
    """

    def __init__(self, message: str, failure: AnthropicFailure) -> None:
        """Record why classification failed, and what it proves about the API."""
        super().__init__(message)
        self.failure = failure

    @property
    def dependency_unreachable(self) -> bool:
        """Whether this failure proves the API never served the request."""
        return self.failure is AnthropicFailure.UNREACHABLE


async def classify_intent(
    anthropic_client: AsyncAnthropic, bursts: list[list[Message]]
) -> IntentClassificationResult:
    """Classify the trailing patient message in `bursts`.

    Args:
        bursts: A `bound_to_last_n_turns()`-bounded window over the turn's
            conversation history - never the full chat history.

    Raises: ClassificationFailedError on any API error, timeout, a response that
        fails to validate against the schema, or a validated response that still
        contains `CLASSIFICATION_FAILED` - never returns a result containing it. Its
        `failure` says which of those it was.

    Uses native JSON Outputs (`output_config.format`), not tool-use.
    """
    try:
        response = await anthropic_client.messages.create(
            model=get_settings().CLASSIFICATION_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=to_claude_messages(bursts),
            output_config={
                "format": {"type": "json_schema", "schema": _RESPONSE_SCHEMA}
            },
        )
        block = response.content[0]
        if block.type != "text":
            raise ClassificationFailedError(
                f"unexpected content block: {block.type}", AnthropicFailure.ANSWERED
            )
        result = IntentClassificationResult.model_validate_json(block.text)
        if IntentLabel.CLASSIFICATION_FAILED in result.intents:
            # Defense in depth: `_RESPONSE_SCHEMA`'s enum exclusion (above) is what's
            # actually supposed to make this unreachable, but that's enforced at a
            # private, Pydantic-internals-dependent key path - re-check the parsed
            # result itself so a schema-level regression fails loudly here instead of
            # silently violating this function's own contract (FR-007).
            raise ClassificationFailedError(
                "model returned CLASSIFICATION_FAILED despite schema exclusion",
                AnthropicFailure.ANSWERED,
            )
        return result
    except ClassificationFailedError:
        raise
    except Exception as exc:
        raise ClassificationFailedError(str(exc), classify_failure(exc)) from exc
