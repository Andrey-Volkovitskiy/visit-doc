"""`classify_intent`: structured-output intent classification."""

from typing import Any

from anthropic import AsyncAnthropic

from chat.agent.history import to_claude_messages_separating_silence
from chat.clients.anthropic_failure import AnthropicFailure, classify_failure
from chat.core.config import get_settings
from chat.domain.models import Message
from chat.domain.schemas import MAX_SEGMENTS, IntentClassificationResult, IntentLabel

# Room for the largest legal response and then some. A response is no longer one short
# label list: it is up to `MAX_SEGMENTS` restated requests, each a sentence of the
# visitor's own length. Running into the cap truncates the JSON mid-string, which
# parses as an invalid classification and drops the turn back to the unsplit message -
# on exactly the multi-request traffic the split exists for.
_MAX_TOKENS = 1024
# Zero, not the API's default of 1: a message on a label boundary would otherwise be
# sampled onto either side of it from one run to the next, and every later stage - which
# specialist runs, what is searched for, whether a person is paged - follows the label.
# Zero makes the most likely reading the one returned; it does not make the call
# fully deterministic.
_TEMPERATURE = 0.0
_SYSTEM_PROMPT = (
    "Split the visitor's most recent message into the requests it contains, given the "
    "conversation so far, and label each one. Return one segment per request, in the "
    "order the requests appear in the message. The labels are: "
    "faq_question (any question about the clinic that is not one of the five booking "
    "requests below), "
    "booking (exactly five requests, which are everything the clinic's live "
    "appointment records can answer and nothing else: (i) which practitioners the "
    "clinic has, what a named one specializes in, or who has a given specialty - this "
    "one asks for no appointment at all and is still booking; (ii) when a practitioner "
    "has a free slot; (iii) making an appointment; (iv) rescheduling or cancelling "
    "one; (v) which appointments this patient already has), "
    "small_talk (the message asks for nothing the clinic could act on - a greeting, an "
    "acknowledgement, a thank-you, a farewell, a reaction, a note that they are "
    "thinking it over, a message that is unintelligible, or a question about something "
    "the clinic has nothing to do with, such as the weather. A message that asks "
    "anything about the clinic, an appointment, a practitioner or the patient's own "
    'care is NEVER small_talk, however short or polite it is: "is there a number I '
    'can call you on?" is faq_question, not a pleasantry. A bare "ok", "yes", "sure" '
    "or "
    '"perfect" that answers a question you just asked is never small_talk either - '
    "it belongs to whatever you asked about), "
    "urgent_condition (the message describes a condition needing immediate attention "
    "right now - severe, sudden, or dangerous, the kind of thing an emergency "
    "department exists for. Chest pain, difficulty breathing, heavy bleeding, a "
    "suspected overdose, fainting, a child who cannot be roused properly, stroke signs "
    "or sudden severe pain are this however calmly they are put, and remain this when "
    "the patient also asks for an appointment. Everyday hyperbole about an ordinary "
    "complaint is not this "
    '("my tooth is killing me", "this headache is brutal" - an ordinary complaint '
    "stated beside a request for an appointment is part of that request, not a "
    "question of its own), and neither is "
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
    "call_staff (the patient explicitly asks to speak to a human), not_authorized "
    "(a request the assistant is not authorized to serve, such as a sick note, a "
    "prescription, a records transfer or a billing correction). "
    "A message may carry more than one intent at once. "
    "Three boundaries decide most borderline messages. "
    "(a) faq_question or booking: the five booking requests are a closed list - the "
    "live records answer those and nothing else - while the FAQ corpus is open, "
    "gaining entries over time, so no list of its subjects would stay complete. So "
    "test the message against booking, not against FAQ: if what is asked is not one "
    "of the five, it is faq_question, whatever its subject. "
    "Run that test by asking where the answer is kept, and never by whether the "
    "message reaches for an appointment. Two of the five ask for no appointment at "
    "all - the roster, and what this patient already holds - so a message naming no "
    "time, no slot and no visit is still booking whenever only the records hold its "
    'answer: "which of your doctors handles skin conditions?" and "is the one I saw '
    'last time a specialist or a GP?" are both booking, because no clinic document '
    "names a practitioner or says what any of them treats. Wanting to be seen is not "
    "what makes a message booking; it is one of the five things that happen to be. "
    "A question about what a "
    "visit requires or costs is the usual case - whether a referral is needed, whether "
    "a specialist can be seen without one, what the patient must bring first, what a "
    'visit is priced at - and stays faq_question even when phrased "can I see a '
    'specialist if...", because it asks a policy rather than the records. '
    "Asking the rules governing one "
    "of the five is a policy question too: three of the five are acts on this "
    "patient's records, and the terms those acts are subject to are written in the "
    "clinic's documents rather than the records, so \"what is your cancellation "
    'policy?" and "how late may I reschedule?" are faq_question while "cancel my '
    'Friday appointment" is booking. One specialty can likewise fall on both sides: '
    '"is a dermatologist free on the 14th?" is booking, and "what does a '
    'dermatologist charge?" is faq_question. '
    "(b) faq_question or small_talk: parking, directions, transport, opening hours and "
    "prices at or near the clinic are the clinic's business and faq_question - "
    "including a garage, stop or shop its visitors use. "
    "(c) faq_question or not_authorized: asking whether something is possible or how a "
    "policy works - is a deposit required, how long a refund takes, does a copy of an "
    "invoice cost extra - is faq_question. not_authorized is asking the clinic to do "
    "something for the "
    "patient that the assistant may not do: issue a sick note, correct or reissue a "
    "bill, transfer records. "
    "If a message could be read either as asking for something or as asking for "
    "nothing, it is not small_talk: answering a real request with a pleasantry is the "
    "worse mistake, and the other paths know how to say they cannot help. "
    "The same words mean different things at different points in a conversation, so "
    'read the message against what was said before it. "OK" after directions to the '
    'clinic is small_talk; the same "OK" after you offered a specific '
    "appointment slot is the patient confirming that booking. "
    "How to split the message. Two limits hold whatever it contains: never return "
    f"more than {MAX_SEGMENTS} segments, and never leave out something the visitor "
    "asked for - "
    "every request must be inside one of the segments you return, even if that means "
    "one segment carrying two of them: "
    "(1) Segments are requests. A greeting, a thank-you or a reaction standing beside "
    "a real request is not a segment of its own, and is not folded into one either - "
    "leave it out. A message that asks for nothing at all is a single small_talk "
    "segment carrying the message. "
    "(2) Each segment must stand on its own, as a request someone could act on "
    "without reading the rest of the message or the conversation. Resolve pronouns "
    'and ellipsis: "is there a pharmacy nearby, and is it open late?" becomes "is '
    'there a pharmacy near the clinic?" and "is the pharmacy near the clinic open '
    'late?", never "is it open late?". A segment does not have '
    "to be a substring of the message. "
    "(2a) A follow-up clause that refines, contrasts with or asks the other side of "
    "the first request is a request of its own, and needs the same restating: "
    '"how long is a first consultation, and is a second one shorter?" becomes "how '
    'long is a first consultation?" and "how long is a second consultation?"; "is '
    'Kaiser accepted? and if it is not?" becomes "is Kaiser accepted?" and "what '
    'happens if my insurance is not accepted?". Dropping such a clause loses a '
    "question the visitor asked. "
    "(3) Restate, never add. Never introduce a constraint, specialty, date, "
    "practitioner or symptom that the message and the conversation do not carry: "
    'turning "how long will I wait?" into "how long will I wait for a cardiology '
    'appointment?" invents the thing that decides the answer. Resolving a reference '
    'must keep what is asked: "is there a fee for X-rays? and what about for a '
    'physiotherapist?" gives "is there a fee to see a physiotherapist?", never "do '
    'you have a physiotherapist?" - '
    "changing the question is adding one. "
    "(4) Split by answer. A message is split wherever its parts are independently "
    "answerable - different answers, not merely different sentences - even when they "
    'share a sentence and a topic: "what are your email address and fax number?" is '
    'two requests, "what is your email address?" and "what is your fax number?". '
    "Length, punctuation and repetition are not split points, and two ways of asking "
    'one thing are one segment: "is there a lift? can I get upstairs without the '
    'stairs?" is one request, not two. '
    "(4a) Keep the visitor's order. Segments come in the order their requests were "
    "asked, even when a later one is more specific or restates part of an earlier "
    'one: "is there a fee for X-rays? and what about for a physiotherapist?" gives '
    '"is there a fee for X-rays?" first and "is there a fee to see a '
    'physiotherapist?" second, never the reverse. '
    f"(5) {MAX_SEGMENTS} segments is the hard limit. A message carrying more requests "
    f"than that still returns {MAX_SEGMENTS}: combine the least separable of them into "
    "one segment, so that nothing the visitor asked for is missing from every segment, "
    "and set cap_bound to true. Leave cap_bound false whenever you did not have to "
    f"combine anything - {MAX_SEGMENTS} requests that fit are not a message that was "
    "cut short."
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
# `query` is not the model's to write: a segment arrives with its restatement in `text`,
# which also becomes its query, and orchestration decides afterwards what the
# specialist reads. Removed before `required` is derived, so it is not demanded either.
_RESPONSE_SCHEMA["$defs"]["RequestSegment"]["properties"].pop("query")
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

    Raises: ClassificationFailedError on any API error, timeout, a response that ran
        into `_MAX_TOKENS`, a response that fails to validate against the schema, or a
        validated response that still contains `CLASSIFICATION_FAILED` - never returns
        a result containing it. Its `failure` says which of those it was, and its
        message says which of the last three.

    Uses native JSON Outputs (`output_config.format`), not tool-use.
    """
    try:
        response = await anthropic_client.messages.create(
            model=get_settings().CLASSIFICATION_MODEL,
            max_tokens=_MAX_TOKENS,
            temperature=_TEMPERATURE,
            system=_SYSTEM_PROMPT,
            messages=to_claude_messages_separating_silence(bursts),
            output_config={
                "format": {"type": "json_schema", "schema": _RESPONSE_SCHEMA}
            },
        )
        if response.stop_reason == "max_tokens":
            # Named rather than left to the parse below, which would report it as
            # malformed JSON. Both fall back the same way, but they need opposite
            # fixes - raise the cap, or look at what the model returned - and the two
            # are indistinguishable once a truncated response is a `ValidationError`.
            raise ClassificationFailedError(
                "the classification ran out of output tokens", AnthropicFailure.ANSWERED
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
