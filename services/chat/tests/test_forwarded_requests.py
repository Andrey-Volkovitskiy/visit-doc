"""What is open with staff, what the patient is told about it, and in what register.

Pure functions over stored messages and recorded causes: no store, no model. The turn
reads the open requests off its thread, the router decides the notice and the tone from
them, and each of those is one answer given in one place.
"""

import pytest
from chat.agent.escalation import (
    CALM_TONE,
    FOLLOW_UP_MESSAGE,
    HANDOFF_TEXT,
    NOTICE_SEPARATOR,
    EscalationRequests,
    ForwardedRequest,
    calls_for_calm,
    notice_for,
    open_requests,
    with_calm_tone,
    with_notice,
)
from chat.domain.models import (
    CLEARABLE_MARKS,
    OPEN_REQUEST_MARKS,
    AttentionMark,
    EscalationReason,
    Message,
    MessageSender,
)
from ulid import ULID


def _message(
    content: str,
    mark: AttentionMark | str | None = None,
    sender: MessageSender = MessageSender.PATIENT,
) -> Message:
    return Message(
        id=str(ULID()),
        chat_id="c",
        sender=sender.value,
        content=content,
        attention_mark=None if mark is None else str(mark),
    )


def test_open_requests_are_the_patient_messages_carrying_an_open_mark() -> None:
    thread = [
        _message("I need a sick note", AttentionMark.NOT_AUTHORIZED),
        _message("I've passed this to staff", sender=MessageSender.ASSISTANT),
        _message("what are your hours?"),
        _message("I want a person", AttentionMark.PATIENT_ASKED_FOR_PERSON),
    ]

    assert open_requests(thread) == [
        ForwardedRequest("I need a sick note", AttentionMark.NOT_AUTHORIZED),
        ForwardedRequest("I want a person", AttentionMark.PATIENT_ASKED_FOR_PERSON),
    ]


@pytest.mark.parametrize(
    "mark",
    [
        AttentionMark.CORPUS_COULD_NOT_ANSWER,
        AttentionMark.ASSISTANT_FAILED,
        AttentionMark.UNANSWERED,
    ],
)
def test_a_gap_a_failure_and_a_paused_message_are_not_open_requests(
    mark: AttentionMark,
) -> None:
    # A rephrased question is tried against the corpus again, a failed turn is
    # retried, and a message held by a pause was never forwarded at all.
    assert open_requests([_message("anything", mark)]) == []


def test_a_mark_this_build_does_not_name_is_not_an_open_request() -> None:
    assert open_requests([_message("anything", "a_mark_from_a_later_build")]) == []


def test_open_marks_are_exactly_the_clearable_ones_but_the_pause() -> None:
    # A staff post is what closes an open request, so every open mark must be one it
    # clears - an open mark nothing clears would be a request open forever.
    assert OPEN_REQUEST_MARKS <= CLEARABLE_MARKS
    assert CLEARABLE_MARKS - OPEN_REQUEST_MARKS == {AttentionMark.UNANSWERED}


def test_every_cause_the_classifier_raises_has_a_notice() -> None:
    told = set(HANDOFF_TEXT)
    untold = {
        EscalationReason.CORPUS_COULD_NOT_ANSWER,
        EscalationReason.ASSISTANT_FAILED,
    }
    assert told == set(EscalationReason) - untold


def test_the_notice_is_the_strongest_cause_that_has_one() -> None:
    reasons = [
        EscalationReason.CORPUS_COULD_NOT_ANSWER,
        EscalationReason.FORWARDED_FOLLOW_UP,
        EscalationReason.DISTRESS,
    ]
    assert notice_for(reasons) is EscalationReason.DISTRESS


def test_no_notice_for_causes_the_patient_is_told_about_elsewhere() -> None:
    reasons = [
        EscalationReason.CORPUS_COULD_NOT_ANSWER,
        EscalationReason.ASSISTANT_FAILED,
    ]
    assert notice_for(reasons) is None
    assert notice_for([]) is None


def test_a_notice_follows_the_reply_after_one_separator() -> None:
    reply = with_notice("We're open until six.", EscalationReason.FORWARDED_FOLLOW_UP)
    assert reply == f"We're open until six.{NOTICE_SEPARATOR}{FOLLOW_UP_MESSAGE}"


def test_a_notice_with_no_reply_before_it_stands_alone() -> None:
    assert with_notice("", EscalationReason.FORWARDED_FOLLOW_UP) == FOLLOW_UP_MESSAGE


def test_no_notice_leaves_the_reply_untouched() -> None:
    assert with_notice("Hello.", None) == "Hello."


@pytest.mark.parametrize(
    "mark", [AttentionMark.URGENT_CONDITION, AttentionMark.DISTRESS]
)
def test_an_open_urgent_or_distressed_request_calls_for_calm(
    mark: AttentionMark,
) -> None:
    assert calls_for_calm([ForwardedRequest("help", mark)], [])


@pytest.mark.parametrize(
    "reason", [EscalationReason.URGENT_CONDITION, EscalationReason.DISTRESS]
)
def test_raising_urgent_or_distress_this_turn_calls_for_calm(
    reason: EscalationReason,
) -> None:
    assert calls_for_calm([], [reason])


def test_other_open_requests_do_not_call_for_calm() -> None:
    forwarded = [
        ForwardedRequest("a sick note", AttentionMark.NOT_AUTHORIZED),
        ForwardedRequest("a person", AttentionMark.PATIENT_ASKED_FOR_PERSON),
    ]
    assert not calls_for_calm(forwarded, [EscalationReason.FORWARDED_FOLLOW_UP])


def test_the_calm_tone_is_added_after_the_prompt_only_when_called_for() -> None:
    assert with_calm_tone("PROMPT", calm=False) == "PROMPT"
    calm = with_calm_tone("PROMPT", calm=True)
    assert calm.startswith("PROMPT")
    assert calm.endswith(CALM_TONE)


def test_a_follow_up_marks_its_message_below_every_new_cause() -> None:
    # A follow-up beside a new request for a person is marked for the person: the new
    # cause is the stronger claim, and the follow-up survives in the log.
    requests = EscalationRequests()
    requests.record(EscalationReason.FORWARDED_FOLLOW_UP)
    requests.record(EscalationReason.NOT_AUTHORIZED)
    assert requests.message_mark is AttentionMark.NOT_AUTHORIZED

    requests = EscalationRequests()
    requests.record(EscalationReason.CORPUS_COULD_NOT_ANSWER)
    requests.record(EscalationReason.FORWARDED_FOLLOW_UP)
    assert requests.message_mark is AttentionMark.FORWARDED_FOLLOW_UP
