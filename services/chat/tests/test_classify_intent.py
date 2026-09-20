"""Tests for `classify_intent()` (research.md #3)."""

import pytest
from chat.agent.classify_intent import ClassificationFailedError, classify_intent
from chat.agent.tools.scheduling_tools import SCHEDULING_TOOLS
from chat.domain.models import Message, MessageSender
from chat.domain.schemas import IntentLabel, RequestSegment
from pydantic import ValidationError

from .conftest import fake_classify_intent_client

_CONTEXT: list[list[Message]] = [
    [Message(sender=MessageSender.PATIENT, content="when can I visit?", id="turn-1")]
]


async def test_classify_intent_returns_single_label() -> None:
    client = fake_classify_intent_client([IntentLabel.FAQ_QUESTION])

    result = await classify_intent(client, _CONTEXT)

    assert result.intents == [IntentLabel.FAQ_QUESTION]


async def test_classify_intent_returns_multiple_labels() -> None:
    labels = [IntentLabel.FAQ_QUESTION, IntentLabel.BOOKING]
    client = fake_classify_intent_client(labels)

    result = await classify_intent(client, _CONTEXT)

    assert result.intents == [IntentLabel.FAQ_QUESTION, IntentLabel.BOOKING]


async def test_classify_intent_returns_catch_all_label() -> None:
    client = fake_classify_intent_client([IntentLabel.NOT_AUTHORIZED])

    result = await classify_intent(client, _CONTEXT)

    assert result.intents == [IntentLabel.NOT_AUTHORIZED]


async def test_classify_intent_raises_on_api_error() -> None:
    client = fake_classify_intent_client(call_error=RuntimeError("boom"))

    with pytest.raises(ClassificationFailedError):
        await classify_intent(client, _CONTEXT)


async def test_classify_intent_raises_on_unparseable_response() -> None:
    client = fake_classify_intent_client(None)

    with pytest.raises(ClassificationFailedError):
        await classify_intent(client, _CONTEXT)


async def test_classify_intent_names_a_response_that_ran_out_of_room() -> None:
    # A truncated response and a malformed one both fall back, but need opposite fixes
    # - raise the cap, or look at what the model wrote - so the message says which.
    # The body here is valid JSON precisely so that only `stop_reason` can tell them
    # apart: a cap check that never ran would let this call succeed.
    client = fake_classify_intent_client(
        [IntentLabel.FAQ_QUESTION], stop_reason="max_tokens"
    )

    with pytest.raises(ClassificationFailedError) as raised:
        await classify_intent(client, _CONTEXT)

    assert "output tokens" in str(raised.value)


async def test_classify_intent_raises_when_model_returns_classification_failed() -> (
    None
):
    client = fake_classify_intent_client([IntentLabel.CLASSIFICATION_FAILED])

    with pytest.raises(ClassificationFailedError):
        await classify_intent(client, _CONTEXT)


# --- Phase 1f: the widened label set and the prompt that defines it -------------------


def _prompt() -> str:
    from chat.agent.classify_intent import _SYSTEM_PROMPT

    return _SYSTEM_PROMPT.lower()


def test_small_talk_is_a_label_the_model_may_return() -> None:
    from chat.agent.classify_intent import _RESPONSE_SCHEMA

    assert IntentLabel.SMALL_TALK
    assert "small_talk" in _RESPONSE_SCHEMA["$defs"]["IntentLabel"]["enum"]


def test_the_failure_sentinel_stays_unreachable_from_the_model() -> None:
    from chat.agent.classify_intent import _RESPONSE_SCHEMA

    assert (
        "classification_failed" not in _RESPONSE_SCHEMA["$defs"]["IntentLabel"]["enum"]
    )


def test_every_label_but_the_sentinel_is_offered_to_the_model() -> None:
    from chat.agent.classify_intent import _RESPONSE_SCHEMA

    assert set(_RESPONSE_SCHEMA["$defs"]["IntentLabel"]["enum"]) == {
        label.value
        for label in IntentLabel
        if label is not IntentLabel.CLASSIFICATION_FAILED
    }


def test_the_prompt_defines_small_talk_as_asking_for_nothing() -> None:
    # The prompt is the contract for a label whose whole meaning is a judgement, so it
    # is asserted rather than paraphrased.
    prompt = _prompt()
    assert "small_talk" in prompt
    assert "asks for nothing" in prompt
    assert "unintelligible" in prompt


def test_the_prompt_makes_a_request_win_a_tie_against_small_talk() -> None:
    # Answering a real request with "You're welcome!" is the costlier error, and the
    # other paths already know how to abstain (FR-007).
    prompt = _prompt()
    assert "not small_talk" in prompt or "not small talk" in prompt


def test_the_three_stopping_labels_are_offered_to_the_model() -> None:
    from chat.agent.classify_intent import _RESPONSE_SCHEMA

    enum = _RESPONSE_SCHEMA["$defs"]["IntentLabel"]["enum"]
    assert {"urgent_condition", "distress", "booking_for_another"} <= set(enum)


def test_the_prompt_states_each_stopping_rule_and_its_exclusion() -> None:
    prompt = _prompt()
    # Urgency: current, not historical.
    assert "urgent_condition" in prompt
    assert "immediate" in prompt
    # Distress, which often asks for nothing at all.
    assert "distress" in prompt
    # Third party: explicit only, which is what keeps a mention from stopping a chat.
    assert "booking_for_another" in prompt
    assert "explicit" in prompt


# --- Phase 1f, US3: the same word, read against the conversation ---------------------


async def test_the_call_carries_the_exchange_around_the_message() -> None:
    # "OK" is small talk after arrival instructions and a confirmation after a slot
    # offer. The difference is entirely in what came before, so it has to be in the
    # call.
    client = fake_classify_intent_client([IntentLabel.SMALL_TALK])
    context = [
        [
            Message(
                sender=MessageSender.ASSISTANT,
                content="Please arrive 15 minutes early.",
                id="a1",
            )
        ],
        [Message(sender=MessageSender.PATIENT, content="ok", id="p1")],
    ]

    await classify_intent(client, context)

    messages = client.messages.create.call_args.kwargs["messages"]
    # However the window is rendered, what the clinic said last has to be in it: a
    # leading assistant burst is folded into the same entry, and folding it away
    # entirely would leave "ok" to be classified with nothing to read it against.
    rendered = " ".join(str(entry["content"]) for entry in messages)
    assert "arrive 15 minutes early" in rendered
    assert rendered.endswith("ok")


def test_the_prompt_says_the_same_words_can_mean_different_things() -> None:
    prompt = _prompt()
    assert "conversation so far" in prompt
    # The confirmation case named explicitly: it is the one where getting it wrong
    # loses an appointment rather than producing an awkward reply.
    assert "confirm" in prompt


def test_the_prompt_narrows_calling_staff_to_asking_for_a_human() -> None:
    # Before 1f this label collected "an urgent or staff-handled issue, e.g. a billing
    # problem" - which now belongs to `not_authorized` (no silence) or
    # `urgent_condition`
    # (silence). The label and the cause it sets now say the same thing.
    prompt = _prompt()
    assert "explicitly asks to speak to a human" in prompt
    # still named, but as an example of `not_authorized`
    assert "billing" in prompt
    billing_at = prompt.index("billing")
    not_authorized_at = prompt.index("not_authorized (")
    assert not_authorized_at < billing_at


def test_the_prompt_defines_not_authorized_as_a_request_that_may_not_be_served() -> (
    None
):
    prompt = _prompt()
    assert "not authorized to serve" in prompt


# --- Phase 1f: the two rules the first live run added --------------------------------
#
# Every other rule in this prompt is pinned above. These two came from a *measured*
# defect (`evaluation/procedure.md`, 2026-09-08: "Perfect. What time should I arrive?"
# was answered with a pleasantry), so leaving them unpinned would let a reword regress
# exactly what the live run was run to catch.


def test_the_prompt_refuses_to_call_a_clinic_question_small_talk() -> None:
    """FR-003a: asking anything about the clinic is never a pleasantry."""
    prompt = " ".join(_prompt().split())
    # "however short or polite" rather than "never small_talk": the latter also appears
    # in the bare-affirmative rule below, so asserting it here would pass on that
    # sentence alone and pin nothing. Verified by mutation.
    assert "never small_talk, however short or polite" in prompt
    for named in ("the clinic", "an appointment", "a practitioner"):
        assert named in prompt
    # The worked example, which is the part a model actually generalizes from.
    assert "is there a number i can call you on?" in prompt


def test_the_prompt_binds_a_bare_affirmative_to_the_question_it_answers() -> None:
    """FR-003a: "ok" after the assistant's own question is not a pleasantry."""
    prompt = " ".join(_prompt().split())
    assert "belongs to whatever you asked about" in prompt
    for affirmative in ('"ok"', '"yes"', '"sure"'):
        assert affirmative in prompt


def test_the_prompt_keeps_an_off_topic_question_out_of_the_queue() -> None:
    """FR-003b: it requests something, but nothing a staff member could act on."""
    prompt = " ".join(_prompt().split())
    assert "the clinic has nothing to do with" in prompt
    assert "weather" in prompt


def test_the_prompt_excludes_hyperbole_and_the_past_tense_from_urgency() -> None:
    """FR-040 as the live run narrowed it: "my tooth is killing me" is not urgent."""
    prompt = " ".join(_prompt().split())
    assert "hyperbole" in prompt
    assert "past tense" in prompt
    # And the recall that tightening cost once, restored by naming the red flags.
    for red_flag in ("chest pain", "difficulty breathing", "heavy bleeding"):
        assert red_flag in prompt


# --- The silent window: context to read against, never the message being classified ---


async def test_a_silent_window_is_separated_from_the_message_being_classified() -> None:
    """The labels this call returns drive the hand-off, the mark and the silence.

    `to_claude_messages` rejoins the two consecutive patient-sided bursts that
    `exclude_silent_window` leaves behind, so without the separation the classifier
    reads a message a staff member was meant to answer as part of the one it is
    labelling - and a turn saying "thanks anyway" hands over, and re-silences the
    conversation, for a request the patient made before a person took it.
    """
    from chat.agent.history import ANSWERING_HEADING, SILENT_WINDOW_NOTE

    client = fake_classify_intent_client([IntentLabel.SMALL_TALK])
    held_back = [
        Message(
            sender=MessageSender.PATIENT,
            content="I need you to write me a sick note",
            id="p0",
        )
    ]
    answering = [
        Message(sender=MessageSender.PATIENT, content="thanks anyway!", id="p1")
    ]

    await classify_intent(client, [held_back, answering])

    entry = client.messages.create.call_args.kwargs["messages"][-1]["content"]
    assert SILENT_WINDOW_NOTE in entry
    assert "write me a sick note" in entry
    assert entry.endswith(f"{ANSWERING_HEADING}\nthanks anyway!")


async def test_an_ordinary_turn_is_classified_with_no_silent_window_note() -> None:
    # The note is a seam a model has to read, absent when there is nothing to separate.
    from chat.agent.history import SILENT_WINDOW_NOTE

    client = fake_classify_intent_client([IntentLabel.FAQ_QUESTION])

    await classify_intent(client, _CONTEXT)

    entries = client.messages.create.call_args.kwargs["messages"]
    assert SILENT_WINDOW_NOTE not in " ".join(str(e["content"]) for e in entries)


# --- Phase 1g: the classifier returns requests, not labels ---------------------------


async def test_classify_intent_returns_one_segment_per_request() -> None:
    client = fake_classify_intent_client(
        segments=[
            (IntentLabel.FAQ_QUESTION, "what is your address?"),
            (IntentLabel.BOOKING, "what dentist slots are free tomorrow?"),
        ]
    )

    result = await classify_intent(client, _CONTEXT)

    assert [(s.intent, s.text) for s in result.segments] == [
        (IntentLabel.FAQ_QUESTION, "what is your address?"),
        (IntentLabel.BOOKING, "what dentist slots are free tomorrow?"),
    ]


async def test_the_label_list_is_derived_from_the_segments_in_order() -> None:
    # The router's existing selection reads `intents`, so it survives unchanged
    # (FR-001) - and cannot drift from the segments, because it *is* them.
    client = fake_classify_intent_client(
        segments=[
            (IntentLabel.BOOKING, "cancel friday"),
            (IntentLabel.FAQ_QUESTION, "do i need a referral?"),
        ]
    )

    result = await classify_intent(client, _CONTEXT)

    assert result.intents == [IntentLabel.BOOKING, IntentLabel.FAQ_QUESTION]


async def test_a_single_request_is_a_single_segment() -> None:
    client = fake_classify_intent_client([IntentLabel.FAQ_QUESTION])

    result = await classify_intent(client, _CONTEXT)

    assert len(result.segments) == 1
    assert result.intents == [IntentLabel.FAQ_QUESTION]


async def test_three_segments_are_accepted() -> None:
    client = fake_classify_intent_client(
        segments=[
            (IntentLabel.FAQ_QUESTION, "what are your hours?"),
            (IntentLabel.FAQ_QUESTION, "is parking free?"),
            (IntentLabel.BOOKING, "book me friday"),
        ]
    )

    result = await classify_intent(client, _CONTEXT)

    assert len(result.segments) == 3


async def test_more_segments_than_the_cap_is_an_invalid_result() -> None:
    # Trimming would silently drop a request the patient made; the segmenter combining
    # them is a decision made with the message in view (FR-006a, FR-008).
    client = fake_classify_intent_client(
        raw_text=(
            '{"segments": ['
            '{"intent": "faq_question", "text": "a"},'
            '{"intent": "faq_question", "text": "b"},'
            '{"intent": "faq_question", "text": "c"},'
            '{"intent": "faq_question", "text": "d"}]}'
        )
    )

    with pytest.raises(ClassificationFailedError):
        await classify_intent(client, _CONTEXT)


async def test_an_empty_segment_list_is_an_invalid_result() -> None:
    client = fake_classify_intent_client(raw_text='{"segments": []}')

    with pytest.raises(ClassificationFailedError):
        await classify_intent(client, _CONTEXT)


async def test_a_blank_segment_text_is_an_invalid_result() -> None:
    # A blank question is not a request, and would be retrieved for as one.
    client = fake_classify_intent_client(
        raw_text='{"segments": [{"intent": "faq_question", "text": "   "}]}'
    )

    with pytest.raises(ClassificationFailedError):
        await classify_intent(client, _CONTEXT)


async def test_cap_bound_defaults_to_false_when_the_model_omits_it() -> None:
    # A model that says nothing is saying nothing was combined (FR-007).
    client = fake_classify_intent_client(
        raw_text='{"segments": [{"intent": "faq_question", "text": "hours?"}]}'
    )

    result = await classify_intent(client, _CONTEXT)

    assert result.cap_bound is False


async def test_cap_bound_is_carried_through_when_the_model_sets_it() -> None:
    client = fake_classify_intent_client(
        segments=[(IntentLabel.FAQ_QUESTION, "hours?")], cap_bound=True
    )

    result = await classify_intent(client, _CONTEXT)

    assert result.cap_bound is True


def test_the_request_schema_states_no_array_bounds() -> None:
    # The API rejects `minItems`/`maxItems` on an array in a JSON Outputs schema, so
    # the cap cannot be made unrepresentable in the response. It is stated in the
    # prompt and enforced on arrival instead - an over-long list is rejected, never
    # trimmed (FR-006, FR-008). Asserted rather than left implicit: Pydantic emits
    # both keys from the field's own bounds, so a regression here is a 400 on every
    # classification call, on every turn.
    from chat.agent.classify_intent import _RESPONSE_SCHEMA

    segments = _RESPONSE_SCHEMA["properties"]["segments"]
    assert "minItems" not in segments
    assert "maxItems" not in segments


def test_the_request_schema_forbids_extra_keys_on_a_segment() -> None:
    # The API requires `additionalProperties: false` on every object-typed schema, and
    # a segment is now one of them.
    from chat.agent.classify_intent import _RESPONSE_SCHEMA

    assert _RESPONSE_SCHEMA["$defs"]["RequestSegment"]["additionalProperties"] is False


def test_the_prompt_says_the_segments_are_the_requests() -> None:
    # Rule 1: a part that routes nowhere must not consume one of the three slots
    # (FR-005b, FR-005c).
    prompt = _prompt()
    assert "segments are requests" in prompt
    assert "asks for nothing at all is a single small_talk segment" in prompt


def test_the_prompt_requires_each_segment_to_stand_on_its_own() -> None:
    # Rule 2: "is it free?" retrieves nothing on its own (FR-004, FR-004a).
    prompt = _prompt()
    assert "stand on its own" in prompt
    assert "is the pharmacy near the clinic open late?" in prompt


def test_the_prompt_forbids_inventing_what_the_message_did_not_carry() -> None:
    # Rule 3: a restatement that adds a constraint changes what is retrieved (FR-004b).
    prompt = _prompt()
    assert "restate, never add" in prompt


def test_the_prompt_reads_the_message_against_the_conversation() -> None:
    # Rule 6, unchanged from Phase 1f and restated for segmentation (FR-006 of 009).
    prompt = _prompt()
    assert "against what was said before it" in prompt


def test_the_prompt_splits_by_answer() -> None:
    # Two parts with two answers are two requests even inside one sentence: each gets
    # its own retrieval, and the golden set scores a labelled question split this way
    # as answered whole.
    prompt = _prompt()
    assert "split by answer" in prompt
    assert "independently answerable" in prompt
    assert '"what are your email address and fax number?" is two requests' in prompt


def test_the_prompt_combines_above_the_cap_rather_than_dropping() -> None:
    # Nothing the visitor asked for may be missing from every segment (FR-006a), and
    # the turn records that it happened (FR-007).
    prompt = _prompt()
    assert "combine" in prompt
    assert "cap_bound" in prompt


def test_the_prompt_treats_a_follow_up_clause_as_its_own_request() -> None:
    # Rule 2a: a clause that refines or contrasts with the first request is a request,
    # not a decoration - two messages of exactly this shape came back as one segment
    # with the clause gone before it was added (FR-004, FR-004a).
    prompt = _prompt()
    assert "follow-up clause" in prompt
    assert "never leave out something the visitor asked for" in prompt


def test_the_request_schema_does_not_ask_the_model_for_a_query() -> None:
    # The model restates each request once, in `text`; what a specialist reads instead
    # is orchestration's decision, not a second field the model could disagree with.
    from chat.agent.classify_intent import _RESPONSE_SCHEMA

    segment = _RESPONSE_SCHEMA["$defs"]["RequestSegment"]
    assert "query" not in segment["properties"]
    assert segment["required"] == ["intent", "text"]


def test_a_segment_built_without_a_query_searches_for_its_text() -> None:
    segment = RequestSegment.model_validate(
        {"intent": "faq_question", "text": "is parking free?"}
    )

    assert segment.query == "is parking free?"


def test_a_segment_refuses_a_blank_query() -> None:
    with pytest.raises(ValidationError):
        RequestSegment(intent=IntentLabel.FAQ_QUESTION, text="is it free?", query="  ")


async def test_the_classification_is_sampled_at_temperature_zero() -> None:
    # A borderline message sampled at the default temperature lands on either side of
    # a label boundary from run to run, and every later stage follows the label.
    client = fake_classify_intent_client([IntentLabel.FAQ_QUESTION])

    await classify_intent(client, _CONTEXT)

    assert client.messages.create.call_args.kwargs["temperature"] == 0.0


# Each booking tool, and the words the prompt's closed list describes it with. Compared
# for equality against `SCHEDULING_TOOLS`, not inclusion: a tool added to the booking
# node without a phrase here is a capability the prompt's "exactly five requests" no
# longer covers, and nothing else catches the closed list quietly becoming untrue.
_BOOKING_CAPABILITY_IN_PROMPT = {
    "list_practitioners": "which practitioners the clinic has",
    "check_availability": "when a practitioner has a free slot",
    "book_appointment": "making an appointment",
    "reschedule_appointment": "rescheduling or cancelling one",
    "cancel_appointment": "rescheduling or cancelling one",
    "list_my_appointments": "which appointments this patient already has",
}


def test_the_prompt_describes_every_booking_tool_and_no_others() -> None:
    assert {tool.name for tool in SCHEDULING_TOOLS} == set(
        _BOOKING_CAPABILITY_IN_PROMPT
    )

    prompt = " ".join(_prompt().split())
    for phrase in set(_BOOKING_CAPABILITY_IN_PROMPT.values()):
        assert phrase in prompt


def test_the_prompt_counts_the_closed_booking_list_correctly() -> None:
    # The six tools group into five requests because one phrase covers rescheduling and
    # cancelling. If a seventh tool ever adds a sixth phrase, "five" is wrong.
    assert len(set(_BOOKING_CAPABILITY_IN_PROMPT.values())) == 5
    assert "exactly five requests" in " ".join(_prompt().split())


def test_the_prompt_tests_against_booking_rather_than_against_faq() -> None:
    """The FAQ corpus gains entries, so only the booking side can be enumerated."""
    prompt = " ".join(_prompt().split())
    assert "the five booking requests are a closed list" in prompt
    assert "test the message against booking, not against faq" in prompt
    assert "not one of the five, it is faq_question" in prompt


def test_the_prompt_puts_a_question_about_what_a_visit_requires_on_the_faq_side() -> (
    None
):
    prompt = " ".join(_prompt().split())
    assert "a question about what a visit requires or costs" in prompt
    assert '"is a dermatologist free on the 14th?" is booking' in prompt
    assert '"what does a dermatologist charge?" is faq_question' in prompt


def test_the_prompt_keeps_the_rules_governing_a_booking_act_on_the_faq_side() -> None:
    """G018 asks the cancellation policy, which the closed list must not capture.

    "Cancel one" is item (iv), so a rule read only by keyword would route a question
    about the terms of cancelling to the records, which hold no terms to answer it.
    """
    prompt = " ".join(_prompt().split())
    assert "the five are acts on this patient's records, not the terms" in prompt
    assert '"what is your cancellation policy?"' in prompt
    assert 'faq_question while "cancel my friday appointment" is booking' in prompt


def test_the_prompt_puts_logistics_near_the_clinic_on_the_faq_side() -> None:
    prompt = _prompt()
    assert "faq_question or small_talk: parking, directions" in prompt
    assert "including a garage, stop or shop its visitors use" in prompt


def test_the_prompt_separates_asking_about_a_policy_from_asking_for_an_action() -> None:
    prompt = _prompt()
    assert (
        "faq_question or not_authorized: asking whether something is possible" in prompt
    )
    assert (
        "not_authorized is asking the clinic to do something for the patient" in prompt
    )


def test_the_prompt_forbids_a_restatement_that_changes_the_question() -> None:
    prompt = _prompt()
    assert "resolving a reference must keep what is asked" in prompt
    assert 'never "do you have a physiotherapist?"' in prompt


def test_the_prompt_keeps_the_segments_in_the_order_the_visitor_asked() -> None:
    # Positions are what every per-request record joins on, and what the golden set
    # scores a request against: a reversed pair files each answer under the other's
    # question.
    prompt = _prompt()
    assert "keep the visitor's order" in prompt
    assert (
        'gives "is there a fee for x-rays?" first and "is there a fee to see a '
        'physiotherapist?" second, never the reverse' in prompt
    )
