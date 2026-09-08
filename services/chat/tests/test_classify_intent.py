"""Tests for `classify_intent()` (research.md #3)."""

import pytest
from chat.agent.classify_intent import ClassificationFailedError, classify_intent
from chat.domain.models import Message, MessageSender
from chat.domain.schemas import IntentLabel

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
    client = fake_classify_intent_client([IntentLabel.UNKNOWN])

    result = await classify_intent(client, _CONTEXT)

    assert result.intents == [IntentLabel.UNKNOWN]


async def test_classify_intent_raises_on_api_error() -> None:
    client = fake_classify_intent_client(call_error=RuntimeError("boom"))

    with pytest.raises(ClassificationFailedError):
        await classify_intent(client, _CONTEXT)


async def test_classify_intent_raises_on_unparseable_response() -> None:
    client = fake_classify_intent_client(None)

    with pytest.raises(ClassificationFailedError):
        await classify_intent(client, _CONTEXT)


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
    # problem" - which now belongs to `unknown` (no silence) or `urgent_condition`
    # (silence). The label and the cause it sets now say the same thing.
    prompt = _prompt()
    assert "explicitly asks to speak to a human" in prompt
    assert "billing" in prompt  # still named, but as an example of `unknown`
    billing_at = prompt.index("billing")
    unknown_at = prompt.index("unknown (")
    assert unknown_at < billing_at


def test_the_prompt_defines_unknown_as_a_request_that_may_not_be_served() -> None:
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
    assert "what time should i arrive?" in prompt


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
