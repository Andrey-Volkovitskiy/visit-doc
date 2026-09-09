"""`classify_intent`: structured-output intent classification."""

from typing import Any

from anthropic import AsyncAnthropic

from chat.agent.history import to_claude_messages_separating_silence
from chat.clients.anthropic_failure import AnthropicFailure, classify_failure
from chat.core.config import get_settings
from chat.domain.models import Message
from chat.domain.schemas import IntentClassificationResult, IntentLabel

_MAX_TOKENS = 256
_SYSTEM_PROMPT = (
    "Split the visitor's most recent message into the requests it contains, given the "
    "conversation so far, and label each one. Return one segment per request, in the "
    "order the requests appear in the message. The labels are: "
    "faq_question (a clinic policy/FAQ question), booking "
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
    "appointment slot is the patient confirming that booking. "
    "How to split the message. Two limits hold whatever it contains: never return "
    "more than 3 segments, and never leave out something the visitor asked for - "
    "every request must be inside one of the segments you return, even if that means "
    "one segment carrying two of them: "
    "(1) Segments are requests. A greeting, a thank-you or a reaction standing beside "
    "a real request is not a segment of its own, and is not folded into one either - "
    "leave it out. A message that asks for nothing at all is a single small_talk "
    "segment carrying the message. "
    "(2) Each segment must stand on its own, as a request someone could act on "
    "without reading the rest of the message or the conversation. Resolve pronouns "
    'and ellipsis: "do you have parking, and is it free?" becomes "do you have '
    'parking?" and "is parking free?", never "is it free?". A segment does not have '
    "to be a substring of the message. "
    "(2a) A follow-up clause that refines, contrasts with or asks the other side of "
    "the first request is a request of its own, and needs the same restating: "
    '"what should I bring, and is that different for a returning patient?" becomes '
    '"what should I bring?" and "what should a returning patient bring?"; "do you '
    'take Medicare? what if you do not?" becomes "do you take Medicare?" and "what '
    'happens if my insurance is not accepted?". Dropping such a clause loses a '
    "question the visitor asked. "
    "(3) Restate, never add. Never introduce a constraint, specialty, date, "
    "practitioner or symptom that the message and the conversation do not carry: "
    'turning "what should I bring?" into "what should I bring to a first cardiology '
    'visit?" invents the thing that decides the answer. '
    "(4) Split conservatively. One request is one segment, and a message is split only "
    "where its parts are independently answerable - different answers, not merely "
    "different sentences. Length, punctuation and repetition are not split points, and "
    'two ways of asking one thing are one segment: "what time do you open? when can I '
    'come in the morning?" is one request, not two. '
    "(5) Three segments is the hard limit. A message carrying four or more requests "
    "still returns three: combine the least separable of them into one segment, so "
    "that nothing the visitor asked for is missing from every segment, and set "
    "cap_bound to true. Leave cap_bound false whenever you did not have to combine "
    "anything - three requests that fit are not a message that was cut short."
)

# The classifier's own request schema, built from `IntentClassificationResult`'s
# schema but with `CLASSIFICATION_FAILED` excluded from the `intents` enum - that
# value is assigned only by orchestration code on a failed/invalid call, so it must be
# structurally unreachable from the model's own response (research.md #3).
#
# `additionalProperties: false` and a `required` naming every property are what the API
# asks of an `object`-typed JSON Outputs schema, and neither is what
# `model_json_schema()` produces: it omits the first entirely and leaves a field with a
# default out of the second. Both are applied to the segment object as well as to the
# result - a nested object is an object.
#
# `cap_bound` is therefore required of the *model* while the Python model keeps a
# default: the schema is what the response must contain, the default is what parsing
# falls back to.
_RESPONSE_SCHEMA: dict[str, Any] = IntentClassificationResult.model_json_schema()
_RESPONSE_SCHEMA["$defs"]["IntentLabel"]["enum"] = [
    label.value
    for label in IntentLabel
    if label is not IntentLabel.CLASSIFICATION_FAILED
]
for _schema in (_RESPONSE_SCHEMA, _RESPONSE_SCHEMA["$defs"]["RequestSegment"]):
    _schema["additionalProperties"] = False
    _schema["required"] = list(_schema["properties"])
# The API rejects `minItems`/`maxItems` on an array in a JSON Outputs schema ("For
# 'array' type, property 'maxItems' is not supported"), which `model_json_schema()`
# emits from the field's own bounds. So the cap cannot be made unrepresentable in the
# response: it is stated in the prompt, and enforced on arrival by the model's own
# `max_length`, which rejects an over-long list rather than trimming it - nothing the
# visitor asked for is ever dropped, and the turn falls back exactly as it does for any
# other invalid classification.
for _bound in ("minItems", "maxItems"):
    _RESPONSE_SCHEMA["properties"]["segments"].pop(_bound, None)


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
            conversation history - never the full chat history. Messages held back
            from this turn are separated from it here, exactly as every specialist
            separates them: the labels this call returns decide which cause the turn
            hands over for, which sentence the patient is owed, which mark the message
            carries and whether the conversation falls silent, so a message still
            waiting for a person must not be able to drive any of the four.

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
            messages=to_claude_messages_separating_silence(bursts),
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
