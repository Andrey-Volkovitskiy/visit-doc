"""The change half of the booking turn: outcome precedence, and the confirmation rules.

The model is mocked, so what is exercised is the loop and the prompt it is given -
which tool calls actually get dispatched, and what the turn's machine-derived outcome
is. A model's behaviour is only testable against a script, so the confirmation rules are
asserted by scripting a model that tries to break them and checking that the *loop*
records what really happened, plus by pinning that the prompt states the rule at all.
"""

import re
from typing import Any

from chat.agent.handle_booking import BookingOutcome
from structlog.testing import capture_logs

from .test_handle_booking import (
    _bursts,
    _client,
    _model_dispatched,
    _RecordingRegistry,
    _run,
    _system_prompt,
    _text_response,
    _tool_use_response,
)

_APPOINTMENT_ID = "01APPOINTMENT000000000000"
_PRACTITIONER_ID = "01PRACTITIONER0000000000"
_STARTS_AT = "2026-08-18T09:00:00"

_CANCEL_ARGUMENTS = {
    "appointment_id": _APPOINTMENT_ID,
    "expected_starts_at": _STARTS_AT,
    "expected_practitioner_id": _PRACTITIONER_ID,
}


def _cancelled_result() -> dict[str, Any]:
    return {
        "status": "changed",
        "change": "cancelled",
        "appointment": {
            "id": _APPOINTMENT_ID,
            "practitioner_full_name": "William Osler",
            "specialty": "General Practice",
            "starts_at": _STARTS_AT,
            "ends_at": "2026-08-18T10:00:00",
            "status": "cancelled",
        },
        "previous_starts_at": _STARTS_AT,
        "previous_practitioner_full_name": "William Osler",
    }


def _listing(
    future: list[dict[str, Any]], past_truncated: bool = False
) -> dict[str, Any]:
    return {"future": future, "past": [], "past_truncated": past_truncated}


def _entry(
    appointment_id: str = _APPOINTMENT_ID,
    starts_at: str = _STARTS_AT,
    status: str = "standing",
) -> dict[str, Any]:
    return {
        "id": appointment_id,
        "practitioner_full_name": "William Osler",
        "specialty": "General Practice",
        "starts_at": starts_at,
        "ends_at": "2026-08-18T10:00:00",
        "status": status,
    }


# --- the turn's outcome ------------------------------------------------------


async def test_a_completed_cancellation_makes_the_turn_cancelled() -> None:
    registry = _RecordingRegistry({"cancel_appointment": _cancelled_result()})
    client = _client(
        [
            _tool_use_response([("cancel_appointment", _CANCEL_ARGUMENTS)]),
            _text_response("That's cancelled."),
        ]
    )

    _, result = await _run(
        client, registry, _bursts("cancel my appointment", "sure?", "yes")
    )

    assert result.outcome is BookingOutcome.CANCELLED
    assert _model_dispatched(registry) == ["cancel_appointment"]


async def test_a_no_op_makes_the_turn_unchanged() -> None:
    registry = _RecordingRegistry(
        {
            "cancel_appointment": {
                "status": "unchanged",
                "appointment": _entry(status="cancelled"),
                "explanation": "Already cancelled.",
            }
        }
    )
    client = _client(
        [
            _tool_use_response([("cancel_appointment", _CANCEL_ARGUMENTS)]),
            _text_response("That was already cancelled."),
        ]
    )

    _, result = await _run(client, registry, _bursts("cancel it", "sure?", "yes"))

    assert result.outcome is BookingOutcome.UNCHANGED


async def test_an_unknown_outcome_makes_the_turn_outcome_unknown() -> None:
    registry = _RecordingRegistry(
        {
            "cancel_appointment": {
                "status": "unknown",
                "explanation": "It is not known whether that went through.",
            }
        }
    )
    client = _client(
        [
            _tool_use_response([("cancel_appointment", _CANCEL_ARGUMENTS)]),
            _text_response("I could not confirm that."),
        ]
    )

    _, result = await _run(client, registry, _bursts("cancel it", "sure?", "yes"))

    assert result.outcome is BookingOutcome.OUTCOME_UNKNOWN


async def test_a_completed_change_outranks_a_refusal_in_the_same_turn() -> None:
    # A turn refused once and then successful is a success.
    registry = _RecordingRegistry(
        {
            "cancel_appointment": {
                "status": "refused",
                "reason": "stale_confirmation",
                "explanation": "That has changed.",
            }
        }
    )
    client = _client(
        [
            _tool_use_response([("cancel_appointment", _CANCEL_ARGUMENTS)]),
            _tool_use_response([("cancel_appointment", _CANCEL_ARGUMENTS)]),
            _text_response("Cancelled."),
        ]
    )

    _, first = await _run(client, registry, _bursts("cancel it", "sure?", "yes"))
    assert first.outcome is BookingOutcome.REFUSED

    registry.set_result("cancel_appointment", _cancelled_result())
    client = _client(
        [
            _tool_use_response(
                [
                    ("cancel_appointment", _CANCEL_ARGUMENTS),
                    ("cancel_appointment", _CANCEL_ARGUMENTS),
                ]
            ),
            _text_response("Cancelled."),
        ]
    )
    _, second = await _run(client, registry, _bursts("cancel it", "sure?", "yes"))

    assert second.outcome is BookingOutcome.CANCELLED


async def test_an_unknown_outcome_outranks_unchanged() -> None:
    registry = _RecordingRegistry(
        {
            "cancel_appointment": {
                "status": "unchanged",
                "appointment": _entry(status="cancelled"),
                "explanation": "Already cancelled.",
            },
            "list_my_appointments": {
                "status": "unknown",
                "explanation": "Not known.",
            },
        }
    )
    client = _client(
        [
            _tool_use_response(
                [
                    ("cancel_appointment", _CANCEL_ARGUMENTS),
                    ("list_my_appointments", {}),
                ]
            ),
            _text_response("Done."),
        ]
    )

    _, result = await _run(client, registry, _bursts("cancel it", "sure?", "yes"))

    # Both are claims about writes, and `unchanged` is the stronger one: it asserts
    # positively that nothing was written. A turn holding a lost write cannot make that
    # claim, so the weaker, safer label wins.
    assert result.outcome is BookingOutcome.OUTCOME_UNKNOWN


async def test_an_unknown_outcome_outranks_an_unavailable_step() -> None:
    # "We cannot tell you whether your appointment was cancelled" is the more important
    # thing to say than "nothing happened" - and saying the latter when the former is
    # true is exactly what the contract forbids.
    registry = _RecordingRegistry(
        {
            "cancel_appointment": {
                "status": "unknown",
                "explanation": "Not known.",
            },
            "list_my_appointments": {
                "status": "unavailable",
                "explanation": "Down.",
            },
        }
    )
    client = _client(
        [
            _tool_use_response(
                [
                    ("cancel_appointment", _CANCEL_ARGUMENTS),
                    ("list_my_appointments", {}),
                ]
            ),
            _text_response("Hmm."),
        ]
    )

    _, result = await _run(client, registry, _bursts("cancel it", "sure?", "yes"))

    assert result.outcome is BookingOutcome.OUTCOME_UNKNOWN


async def test_an_unavailable_step_still_outranks_a_refusal() -> None:
    registry = _RecordingRegistry(
        {
            "cancel_appointment": {
                "status": "refused",
                "reason": "already_started",
                "explanation": "Already started.",
            },
            "list_my_appointments": {
                "status": "unavailable",
                "explanation": "Down.",
            },
        }
    )
    client = _client(
        [
            _tool_use_response(
                [
                    ("cancel_appointment", _CANCEL_ARGUMENTS),
                    ("list_my_appointments", {}),
                ]
            ),
            _text_response("Hmm."),
        ]
    )

    _, result = await _run(client, registry, _bursts("cancel it", "sure?", "yes"))

    assert result.outcome is BookingOutcome.UNAVAILABLE


async def test_a_turn_that_only_listed_appointments_is_informational() -> None:
    registry = _RecordingRegistry({"list_my_appointments": _listing([_entry()])})
    client = _client(
        [
            _tool_use_response([("list_my_appointments", {})]),
            _text_response("You have one appointment."),
        ]
    )

    _, result = await _run(client, registry, _bursts("what have I got booked?"))

    assert result.outcome is BookingOutcome.INFORMATIONAL


async def test_an_unknown_outcome_is_logged_at_error_level_with_its_context() -> None:
    registry = _RecordingRegistry(
        {"cancel_appointment": {"status": "unknown", "explanation": "Not known."}}
    )
    client = _client(
        [
            _tool_use_response([("cancel_appointment", _CANCEL_ARGUMENTS)]),
            _text_response("I could not confirm that."),
        ]
    )

    with capture_logs() as logs:
        await _run(client, registry, _bursts("cancel it", "sure?", "yes"))

    event = next(log for log in logs if log["event"] == "change.outcome_unknown")
    assert event["log_level"] == "error"
    assert event["operation"] == "cancel"
    assert event["appointment_id"] == _APPOINTMENT_ID
    assert event["attempts"] >= 1


# --- the confirmation rules, as stated in the prompt -------------------------


async def _captured_prompt() -> str:
    """Run one trivial turn and return the system prompt it was given.

    Whitespace-collapsed, because the prompt is wrapped prose: an assertion about what
    a rule *says* must not depend on where its line happens to break.
    """
    registry = _RecordingRegistry({"list_my_appointments": _listing([])})
    client = _client([_text_response("Nothing booked.")])
    await _run(client, registry, _bursts("what have I got?"))
    return re.sub(r"\s+", " ", _system_prompt(client))


async def test_the_prompt_forbids_changing_without_a_confirmation_this_turn() -> None:
    prompt = await _captured_prompt()

    assert "cancel_appointment" in prompt
    assert re.search(r"confirm", prompt, re.IGNORECASE)
    assert re.search(r"this turn|current turn", prompt, re.IGNORECASE)


async def test_the_prompt_requires_the_appointment_to_be_read_back_in_full() -> None:
    prompt = await _captured_prompt()

    assert re.search(r"start date-time|start time", prompt, re.IGNORECASE)
    assert re.search(r"full name", prompt, re.IGNORECASE)
    assert re.search(r"specialty", prompt, re.IGNORECASE)


async def test_the_prompt_says_a_confirmation_binds_only_for_its_own_turn() -> None:
    prompt = await _captured_prompt()

    assert re.search(r"re-?state", prompt, re.IGNORECASE)
    assert re.search(r"intervening|another turn|since", prompt, re.IGNORECASE)


async def test_the_prompt_says_a_non_answer_is_not_a_decline() -> None:
    prompt = await _captured_prompt()

    assert re.search(r"neither confirms nor declines", prompt, re.IGNORECASE)
    assert re.search(r"not a decline|is NOT a decline", prompt, re.IGNORECASE)


async def test_the_prompt_forbids_choosing_between_several_appointments() -> None:
    prompt = await _captured_prompt()

    assert re.search(r"never choose", prompt, re.IGNORECASE)
    assert re.search(r"more than one appointment", prompt, re.IGNORECASE)


async def test_the_prompt_forbids_saying_an_id_or_a_tool_name() -> None:
    prompt = await _captured_prompt()

    assert re.search(
        r"never mention an appointment id|appointment id", prompt, re.IGNORECASE
    )
    assert re.search(r"tool", prompt, re.IGNORECASE)


async def test_the_prompt_states_the_truncation_rule_about_that_part_only() -> None:
    prompt = await _captured_prompt()

    assert "past_truncated" in prompt
    assert re.search(r"that part", prompt, re.IGNORECASE)


async def test_the_prompt_forbids_claiming_a_change_without_a_result_this_turn() -> (
    None
):
    prompt = await _captured_prompt()

    assert re.search(r'"changed"', prompt)
    assert re.search(r'"unchanged"', prompt)


async def test_the_prompt_says_what_to_do_on_an_unknown_outcome() -> None:
    prompt = await _captured_prompt()

    assert re.search(r'"unknown"', prompt)
    assert re.search(r"do not retry|do not try it again", prompt, re.IGNORECASE)


async def test_the_prompt_resolves_relative_times_against_the_client_clock() -> None:
    # FR-035: plain local time, no timezone, resolved against the supplied `local_now`
    # and never a server clock.
    prompt = await _captured_prompt()

    assert "2026-08-17T08:00:00" in prompt
    assert re.search(r"never against your own", prompt, re.IGNORECASE)
    assert re.search(
        r"never mention a timezone|never .*timezone", prompt, re.IGNORECASE
    )


# --- what the loop actually does ---------------------------------------------


async def test_no_change_is_dispatched_on_a_turn_the_model_only_listed_in() -> None:
    # The rule is the prompt's, but the observable guarantee is the loop's: a turn that
    # dispatched no change tool cannot have changed anything, whatever the reply says.
    registry = _RecordingRegistry({"list_my_appointments": _listing([_entry()])})
    client = _client(
        [
            _tool_use_response([("list_my_appointments", {})]),
            _text_response("Would you like me to cancel that?"),
        ]
    )

    _, result = await _run(client, registry, _bursts("I want to cancel something"))

    assert "cancel_appointment" not in _model_dispatched(registry)
    assert result.outcome is not BookingOutcome.CANCELLED


async def test_one_confirmation_never_acts_on_more_than_one_appointment() -> None:
    # Two appointments listed, one cancel dispatched: the loop records exactly the
    # appointment the change actually named.
    registry = _RecordingRegistry(
        {
            "list_my_appointments": _listing(
                [_entry(), _entry("01OTHER", "2026-08-19T09:00:00")]
            ),
            "cancel_appointment": _cancelled_result(),
        }
    )
    client = _client(
        [
            _tool_use_response([("list_my_appointments", {})]),
            _tool_use_response([("cancel_appointment", _CANCEL_ARGUMENTS)]),
            _text_response("Cancelled."),
        ]
    )

    await _run(
        client, registry, _bursts("cancel my appointment", "which?", "the first")
    )

    cancels = [
        arguments
        for name, arguments in registry.dispatched
        if name == "cancel_appointment"
    ]
    assert len(cancels) == 1
    assert cancels[0]["appointment_id"] == _APPOINTMENT_ID


async def test_the_guard_the_model_supplied_reaches_the_tool_unchanged() -> None:
    registry = _RecordingRegistry({"cancel_appointment": _cancelled_result()})
    client = _client(
        [
            _tool_use_response([("cancel_appointment", _CANCEL_ARGUMENTS)]),
            _text_response("Cancelled."),
        ]
    )

    await _run(client, registry, _bursts("cancel it", "sure?", "yes"))

    _, arguments = registry.dispatched[-1]
    assert arguments["expected_starts_at"] == _STARTS_AT
    assert arguments["expected_practitioner_id"] == _PRACTITIONER_ID


async def test_no_upcoming_appointments_is_reported_without_an_empty_list() -> None:
    registry = _RecordingRegistry({"list_my_appointments": _listing([])})
    client = _client(
        [
            _tool_use_response([("list_my_appointments", {})]),
            _text_response("You have nothing booked at the moment."),
        ]
    )

    _, result = await _run(client, registry, _bursts("what have I got booked?"))

    assert result.outcome is BookingOutcome.INFORMATIONAL
    assert "cancel_appointment" not in _model_dispatched(registry)


# --- the degraded dependency -------------------------------------------------


async def test_a_cancellation_turn_answers_within_budget_when_scheduling_is_down() -> (
    None
):
    """SC-015: the patient gets a reply, and it claims nothing either way.

    The scheduler being unreachable is a real operating state, not an exceptional one -
    005's 2s deadline and 2 attempts bound how long it can cost, and the reply that
    comes out must neither say the appointment was cancelled nor say nothing happened.
    """
    import time

    registry = _RecordingRegistry(
        {
            "cancel_appointment": {
                "status": "unknown",
                "explanation": (
                    "The scheduling service stopped responding, so it is not known "
                    "whether that went through."
                ),
            }
        }
    )
    client = _client(
        [
            _tool_use_response([("cancel_appointment", _CANCEL_ARGUMENTS)]),
            _text_response("I could not confirm whether that was cancelled."),
        ]
    )

    started = time.monotonic()
    events, result = await _run(client, registry, _bursts("cancel it", "sure?", "yes"))
    elapsed = time.monotonic() - started

    assert elapsed < 5.0
    assert result.outcome is BookingOutcome.OUTCOME_UNKNOWN
    assert events, "the patient must get a reply, not silence"

    # The result the model was handed says the outcome is unknown, and says neither of
    # the two things it must not say.
    observed = next(
        result
        for name, result in _observed_results(registry)
        if name == "cancel_appointment"
    )
    explanation = observed["explanation"].lower()
    assert "not known" in explanation
    assert "was cancelled" not in explanation
    assert "nothing" not in explanation


def _observed_results(
    registry: _RecordingRegistry,
) -> list[tuple[str, dict[str, Any]]]:
    """Pair each dispatched tool name with the result the registry answered it with."""
    return [(name, registry._results[name]) for name, _ in registry.dispatched]


async def test_a_read_that_fails_is_reported_as_unavailable_not_unknown() -> None:
    # A read wrote nothing by construction, so "nothing happened" is a fact there -
    # the one place it is not a guess.
    registry = _RecordingRegistry(
        {
            "list_my_appointments": {
                "status": "unavailable",
                "explanation": "Booking is temporarily unavailable.",
            }
        }
    )
    client = _client(
        [
            _tool_use_response([("list_my_appointments", {})]),
            _text_response("I could not look that up."),
        ]
    )

    _, result = await _run(client, registry, _bursts("what have I got booked?"))

    assert result.outcome is BookingOutcome.UNAVAILABLE


async def test_a_failed_change_turn_still_leaves_the_faq_half_untouched() -> None:
    # The two specialists are independent: a scheduling outage must not make grounded
    # FAQ answering stop working in the same session.
    from chat.agent.compose_answer import FaqResult
    from chat.domain.schemas import Citation

    faq = FaqResult(
        answer_text="Visiting hours are 8-5.",
        citations=[
            Citation(entry_id=1, chunk_index=0, chunk_text="Visiting hours are 8-5.")
        ],
        grounded=True,
        chunk_scores=[0.9],
    )

    assert faq.grounded is True
    assert faq.citations[0].chunk_text == "Visiting hours are 8-5."


# --- moving an appointment ---------------------------------------------------

_NEW_STARTS_AT = "2026-08-18T10:00:00"

_RESCHEDULE_ARGUMENTS = {
    "appointment_id": _APPOINTMENT_ID,
    "new_starts_at": _NEW_STARTS_AT,
    "expected_starts_at": _STARTS_AT,
    "expected_practitioner_id": _PRACTITIONER_ID,
}


def _moved_result() -> dict[str, Any]:
    return {
        "status": "changed",
        "change": "rescheduled",
        "appointment": _entry(starts_at=_NEW_STARTS_AT),
        "previous_starts_at": _STARTS_AT,
        "previous_practitioner_full_name": "William Osler",
    }


async def test_a_completed_move_makes_the_turn_rescheduled() -> None:
    registry = _RecordingRegistry({"reschedule_appointment": _moved_result()})
    client = _client(
        [
            _tool_use_response([("reschedule_appointment", _RESCHEDULE_ARGUMENTS)]),
            _text_response("Moved."),
        ]
    )

    _, result = await _run(client, registry, _bursts("move it to 10", "sure?", "yes"))

    assert result.outcome is BookingOutcome.RESCHEDULED


async def test_a_stale_refusal_never_re_issues_the_change() -> None:
    """FR-022, SC-018: the earlier yes does not cover the new state.

    The loop's guarantee is the observable one - a refused move followed by a reply is
    exactly one dispatch, not a silent retry under a confirmation that has expired.
    """
    registry = _RecordingRegistry(
        {
            "reschedule_appointment": {
                "status": "refused",
                "reason": "stale_confirmation",
                "explanation": (
                    "That appointment has changed since it was read out. Describe it "
                    "as it now stands and ask again - do not repeat the change."
                ),
            },
            "list_my_appointments": _listing([_entry(starts_at="2026-08-18T14:00:00")]),
        }
    )
    client = _client(
        [
            _tool_use_response([("reschedule_appointment", _RESCHEDULE_ARGUMENTS)]),
            _tool_use_response([("list_my_appointments", {})]),
            _text_response("That has moved since I read it out - it is now at 2pm."),
        ]
    )

    _, result = await _run(client, registry, _bursts("move it", "sure?", "yes"))

    moves = [
        name for name, _ in registry.dispatched if name == "reschedule_appointment"
    ]
    assert len(moves) == 1
    assert result.outcome is BookingOutcome.REFUSED


async def test_the_prompt_requires_both_the_current_and_the_proposed_start() -> None:
    prompt = await _captured_prompt()

    assert re.search(r"both the current and the proposed start", prompt, re.IGNORECASE)


async def test_the_prompt_says_what_to_do_per_refusal_group() -> None:
    prompt = await _captured_prompt()

    # The six placement reasons: offer alternatives.
    assert re.search(r"check_availability", prompt)
    for reason in ("practitioner_busy", "patient_busy", "off_grid"):
        assert reason in prompt
    # The three that admit none: no alternatives at all.
    for reason in ("already_started", "already_cancelled", "appointment_not_found"):
        assert reason in prompt
    assert re.search(r"no alternative|invent no alternative", prompt, re.IGNORECASE)


async def test_the_prompt_says_a_stale_confirmation_is_re_asked_never_re_issued() -> (
    None
):
    prompt = await _captured_prompt()

    assert "stale_confirmation" in prompt
    assert re.search(r"never re-?issue|do not re-?issue", prompt, re.IGNORECASE)
    assert re.search(r"ask again", prompt, re.IGNORECASE)


async def test_the_prompt_requires_the_exclusion_when_offering_times_for_a_move() -> (
    None
):
    # Without it the appointment's current slot is missing from its own options, and
    # the patient cannot move it onto a time overlapping the one it holds.
    prompt = await _captured_prompt()

    assert "excluded_appointment_id" in prompt


async def test_every_start_offered_in_a_move_turn_came_from_check_availability() -> (
    None
):
    # FR-033: the loop cannot police the reply text, but it can pin that the only
    # starts the model was ever shown are the ones the tool returned.
    offered = ["2026-08-18T10:00:00", "2026-08-18T11:00:00"]
    registry = _RecordingRegistry(
        {
            "check_availability": {
                "available_starts": offered,
                "appointment_duration_minutes": 60,
                "truncated": False,
            },
            "reschedule_appointment": _moved_result(),
        }
    )
    client = _client(
        [
            _tool_use_response(
                [
                    (
                        "check_availability",
                        {
                            "practitioner_id": _PRACTITIONER_ID,
                            "from_date": "2026-08-18",
                            "to_date": "2026-08-18",
                            "excluded_appointment_id": _APPOINTMENT_ID,
                        },
                    )
                ]
            ),
            _tool_use_response([("reschedule_appointment", _RESCHEDULE_ARGUMENTS)]),
            _text_response("Moved to 10am."),
        ]
    )

    await _run(client, registry, _bursts("move it", "10am please", "yes"))

    # The start the move actually used is one of the offered ones, not an invented,
    # rounded or inferred time.
    move = next(
        arguments
        for name, arguments in registry.dispatched
        if name == "reschedule_appointment"
    )
    assert move["new_starts_at"] in offered


async def test_a_move_turn_passes_the_exclusion_when_checking_availability() -> None:
    registry = _RecordingRegistry(
        {
            "check_availability": {
                "available_starts": [_NEW_STARTS_AT],
                "appointment_duration_minutes": 60,
                "truncated": False,
            }
        }
    )
    client = _client(
        [
            _tool_use_response(
                [
                    (
                        "check_availability",
                        {
                            "practitioner_id": _PRACTITIONER_ID,
                            "from_date": "2026-08-18",
                            "to_date": "2026-08-18",
                            "excluded_appointment_id": _APPOINTMENT_ID,
                        },
                    )
                ]
            ),
            _text_response("You could move it to 10am."),
        ]
    )

    await _run(client, registry, _bursts("when could I move it to?"))

    _, arguments = registry.dispatched[-1]
    assert arguments["excluded_appointment_id"] == _APPOINTMENT_ID


# --- swapping the practitioner -----------------------------------------------


async def test_the_prompt_requires_both_practitioners_with_both_specialties() -> None:
    prompt = await _captured_prompt()

    assert re.search(
        r"both practitioners.*both specialties|both practitioners, with both",
        prompt,
        re.IGNORECASE,
    )
    assert re.search(r"same appointment", prompt, re.IGNORECASE)


async def test_the_prompt_requires_stating_a_changed_length_and_only_then() -> None:
    # FR-025: say it whenever the change alters it, and say nothing about length when
    # it does not - an unchanged length mentioned every time is noise the patient
    # learns to skip past.
    prompt = await _captured_prompt()

    assert re.search(r"length|how long", prompt, re.IGNORECASE)
    assert re.search(r"differs|changes it", prompt, re.IGNORECASE)
    assert re.search(r"say nothing about (the )?length", prompt, re.IGNORECASE)


async def test_the_prompt_says_an_unmatched_specialty_leaves_it_alone() -> None:
    prompt = await _captured_prompt()

    assert re.search(r"name the specialties that (are|do) exist", prompt, re.IGNORECASE)
    assert re.search(r"leave the appointment", prompt, re.IGNORECASE)


async def test_a_swap_turn_reports_the_previous_practitioner_to_the_model() -> None:
    # The observable guarantee: the model is handed both names, so naming both is
    # something it can do from the tool result rather than from memory.
    swapped = {
        "status": "changed",
        "change": "rescheduled",
        "appointment": {
            **_entry(starts_at=_NEW_STARTS_AT),
            "practitioner_full_name": "Elizabeth Blackwell",
            "specialty": "Dentistry",
        },
        "previous_starts_at": _STARTS_AT,
        "previous_practitioner_full_name": "William Osler",
    }
    registry = _RecordingRegistry({"reschedule_appointment": swapped})
    client = _client(
        [
            _tool_use_response(
                [
                    (
                        "reschedule_appointment",
                        {**_RESCHEDULE_ARGUMENTS, "new_practitioner_id": "01OTHER"},
                    )
                ]
            ),
            _text_response("Moved to Elizabeth Blackwell."),
        ]
    )

    _, result = await _run(client, registry, _bursts("see someone else", "ok?", "yes"))

    assert result.outcome is BookingOutcome.RESCHEDULED
    observed = registry._results["reschedule_appointment"]
    assert observed["previous_practitioner_full_name"] == "William Osler"
    assert observed["appointment"]["practitioner_full_name"] == "Elizabeth Blackwell"


# --- what does NOT get a change record ---------------------------------------


async def test_a_declined_confirmation_produces_no_change_and_no_record() -> None:
    # Nothing was sent, so there is nothing to record. The observable guarantee is that
    # no change tool was dispatched at all.
    registry = _RecordingRegistry({"list_my_appointments": _listing([_entry()])})
    client = _client(
        [
            _tool_use_response([("list_my_appointments", {})]),
            _text_response("All right, I have left it as it is."),
        ]
    )

    with capture_logs() as logs:
        _, result = await _run(
            client, registry, _bursts("cancel it", "shall I?", "no, leave it")
        )

    assert "cancel_appointment" not in _model_dispatched(registry)
    assert result.outcome is BookingOutcome.INFORMATIONAL
    assert "change.outcome_unknown" not in [log["event"] for log in logs]


async def test_a_turn_still_awaiting_confirmation_produces_no_record() -> None:
    registry = _RecordingRegistry(
        {
            "check_availability": {
                "available_starts": ["2026-08-18T10:00:00"],
                "appointment_duration_minutes": 60,
                "truncated": False,
            }
        }
    )
    client = _client(
        [
            _tool_use_response(
                [
                    (
                        "check_availability",
                        {
                            "practitioner_id": _PRACTITIONER_ID,
                            "from_date": "2026-08-18",
                            "to_date": "2026-08-18",
                            "excluded_appointment_id": _APPOINTMENT_ID,
                        },
                    )
                ]
            ),
            _text_response("I could move it to 10am - shall I?"),
        ]
    )

    with capture_logs() as logs:
        _, result = await _run(client, registry, _bursts("move it later"))

    assert result.outcome is BookingOutcome.AWAITING_CONFIRMATION
    assert "reschedule_appointment" not in _model_dispatched(registry)
    assert "change.outcome_unknown" not in [log["event"] for log in logs]


async def test_a_refusal_produces_no_unknown_outcome_record() -> None:
    # A refusal is recorded scheduler-side as `change.refused`. The chat side's own
    # record exists for one thing only, and a refusal is not it: the outcome is known.
    registry = _RecordingRegistry(
        {
            "cancel_appointment": {
                "status": "refused",
                "reason": "already_started",
                "explanation": "That appointment has already started.",
            }
        }
    )
    client = _client(
        [
            _tool_use_response([("cancel_appointment", _CANCEL_ARGUMENTS)]),
            _text_response("That has already started."),
        ]
    )

    with capture_logs() as logs:
        _, result = await _run(client, registry, _bursts("cancel it", "sure?", "yes"))

    assert result.outcome is BookingOutcome.REFUSED
    assert "change.outcome_unknown" not in [log["event"] for log in logs]


async def test_a_completed_change_produces_no_unknown_outcome_record() -> None:
    registry = _RecordingRegistry({"cancel_appointment": _cancelled_result()})
    client = _client(
        [
            _tool_use_response([("cancel_appointment", _CANCEL_ARGUMENTS)]),
            _text_response("Cancelled."),
        ]
    )

    with capture_logs() as logs:
        await _run(client, registry, _bursts("cancel it", "sure?", "yes"))

    assert "change.outcome_unknown" not in [log["event"] for log in logs]


async def test_only_an_unknown_outcome_produces_the_unknown_record() -> None:
    registry = _RecordingRegistry(
        {"cancel_appointment": {"status": "unknown", "explanation": "Not known."}}
    )
    client = _client(
        [
            _tool_use_response([("cancel_appointment", _CANCEL_ARGUMENTS)]),
            _text_response("I could not confirm that."),
        ]
    )

    with capture_logs() as logs:
        await _run(client, registry, _bursts("cancel it", "sure?", "yes"))

    unknown = [log for log in logs if log["event"] == "change.outcome_unknown"]
    assert len(unknown) == 1
    assert set(unknown[0]) - {"event", "log_level"} == {
        "operation",
        "appointment_id",
        "attempts",
    }


async def test_a_failing_read_never_produces_a_change_record() -> None:
    # `list_my_appointments` is not a change, so an unknown result from it - which it
    # cannot produce anyway - must not be recorded as one.
    registry = _RecordingRegistry(
        {"list_my_appointments": {"status": "unavailable", "explanation": "Down."}}
    )
    client = _client(
        [
            _tool_use_response([("list_my_appointments", {})]),
            _text_response("I could not look that up."),
        ]
    )

    with capture_logs() as logs:
        await _run(client, registry, _bursts("what have I got?"))

    assert "change.outcome_unknown" not in [log["event"] for log in logs]


# --- asking about cancelled appointments -------------------------------------


async def test_the_prompt_widens_the_time_axis_for_cancelled_appointments() -> None:
    """FR-015: cancelled ones come from either side of now.

    A cancellation is not something the patient is still waiting for, so "what have I
    cancelled?" is not a question about the future. Both filters default to the
    narrowest corner - which is right, since an unset filter must never widen - so the
    assistant has to widen the time axis itself when the request is about cancellations.
    """
    prompt = await _captured_prompt()

    assert re.search(
        r"cancelled.*time_filter.*both|time_filter.*both.*cancelled",
        prompt,
        re.IGNORECASE,
    )


async def test_asking_what_was_cancelled_reaches_both_sides_of_now() -> None:
    # The loop's own guarantee: the arguments the model produced reach the tool
    # unchanged, so a turn that asks for cancellations across both directions really
    # does request both legs.
    registry = _RecordingRegistry(
        {
            "list_my_appointments": {
                "future": [],
                "past": [_entry(starts_at="2026-08-01T09:00:00", status="cancelled")],
                "past_truncated": False,
            }
        }
    )
    client = _client(
        [
            _tool_use_response(
                [
                    (
                        "list_my_appointments",
                        {"time_filter": "both", "status_filter": "cancelled"},
                    )
                ]
            ),
            _text_response("You cancelled one appointment last month."),
        ]
    )

    await _run(client, registry, _bursts("what have I cancelled?"))

    _, arguments = registry.dispatched[-1]
    assert arguments["status_filter"] == "cancelled"
    assert arguments["time_filter"] == "both"


async def test_a_past_cancelled_appointment_survives_to_the_model() -> None:
    # SC-012: cancelled ones are returned when asked for, including those whose start
    # time has already passed. Nothing in the loop may drop the past leg.
    registry = _RecordingRegistry(
        {
            "list_my_appointments": {
                "future": [],
                "past": [_entry(starts_at="2026-08-01T09:00:00", status="cancelled")],
                "past_truncated": False,
            }
        }
    )
    client = _client(
        [
            _tool_use_response(
                [
                    (
                        "list_my_appointments",
                        {"time_filter": "both", "status_filter": "cancelled"},
                    )
                ]
            ),
            _text_response("You cancelled the 1st of August."),
        ]
    )

    _, result = await _run(client, registry, _bursts("what have I cancelled?"))

    observed = registry._results["list_my_appointments"]
    assert [a["status"] for a in observed["past"]] == ["cancelled"]
    assert result.outcome is BookingOutcome.INFORMATIONAL


def test_the_listing_tool_tells_the_model_when_to_widen_the_time_axis() -> None:
    from chat.agent.tools.scheduling_tools import SCHEDULING_TOOLS

    tool = next(t for t in SCHEDULING_TOOLS if t.name == "list_my_appointments")

    assert re.search(r"cancelled", tool.description, re.IGNORECASE)
    assert "both" in tool.description


# --- the loop running out after a change already landed -----------------------


def _exhausting_client(first: Any) -> Any:
    """A model that makes one real call and then stalls until the loop runs out."""
    return _client(
        [first] + [_tool_use_response([("list_my_appointments", {})]) for _ in range(5)]
    )


async def test_exhausting_the_loop_after_a_cancellation_never_denies_it() -> None:
    """The generic exhaustion reply denies a cancellation that really happened.

    Worse than for a booking: a cancellation is irreversible and the freed slot may
    already be gone, so "I wasn't able to finish that - what would you like to book?"
    invites the patient to rebook a time that is no longer theirs, having been told
    their cancellation failed when it did not.
    """
    from chat.agent.handle_booking import _LOOP_EXHAUSTED_REPLY

    registry = _RecordingRegistry(
        {
            "cancel_appointment": _cancelled_result(),
            "list_my_appointments": _listing([]),
        }
    )
    client = _exhausting_client(
        _tool_use_response([("cancel_appointment", _CANCEL_ARGUMENTS)])
    )

    _, result = await _run(client, registry, _bursts("cancel it", "sure?", "yes"))

    assert result.outcome is BookingOutcome.CANCELLED
    assert result.reply_text != _LOOP_EXHAUSTED_REPLY
    assert "cancelled" in result.reply_text.lower()
    assert "book" not in result.reply_text.lower()


async def test_exhausting_the_loop_after_a_move_never_denies_it() -> None:
    from chat.agent.handle_booking import _LOOP_EXHAUSTED_REPLY

    registry = _RecordingRegistry(
        {
            "reschedule_appointment": _moved_result(),
            "list_my_appointments": _listing([]),
        }
    )
    client = _exhausting_client(
        _tool_use_response([("reschedule_appointment", _RESCHEDULE_ARGUMENTS)])
    )

    _, result = await _run(client, registry, _bursts("move it", "sure?", "yes"))

    assert result.outcome is BookingOutcome.RESCHEDULED
    assert result.reply_text != _LOOP_EXHAUSTED_REPLY
    assert "William Osler" in result.reply_text


async def test_exhausting_the_loop_after_a_no_op_reports_it_as_done() -> None:
    from chat.agent.handle_booking import _LOOP_EXHAUSTED_REPLY

    registry = _RecordingRegistry(
        {
            "cancel_appointment": {
                "status": "unchanged",
                "appointment": _entry(status="cancelled"),
                "explanation": "Already cancelled.",
            },
            "list_my_appointments": _listing([]),
        }
    )
    client = _exhausting_client(
        _tool_use_response([("cancel_appointment", _CANCEL_ARGUMENTS)])
    )

    _, result = await _run(client, registry, _bursts("cancel it", "sure?", "yes"))

    assert result.outcome is BookingOutcome.UNCHANGED
    assert result.reply_text != _LOOP_EXHAUSTED_REPLY


async def test_exhausting_the_loop_after_an_unknown_outcome_claims_neither_way() -> (
    None
):
    # The reply the contract is strictest about: it may not say the change happened,
    # and it may not say nothing happened.
    from chat.agent.handle_booking import _LOOP_EXHAUSTED_REPLY

    registry = _RecordingRegistry(
        {
            "cancel_appointment": {"status": "unknown", "explanation": "Not known."},
            "list_my_appointments": _listing([]),
        }
    )
    client = _exhausting_client(
        _tool_use_response([("cancel_appointment", _CANCEL_ARGUMENTS)])
    )

    _, result = await _run(client, registry, _bursts("cancel it", "sure?", "yes"))

    assert result.outcome is BookingOutcome.OUTCOME_UNKNOWN
    assert result.reply_text != _LOOP_EXHAUSTED_REPLY
    reply = result.reply_text.lower()
    assert "couldn't confirm" in reply or "could not confirm" in reply
    assert "is cancelled" not in reply
    assert "nothing" not in reply


async def test_exhausting_the_loop_with_nothing_done_still_reports_the_failure() -> (
    None
):
    # The generic reply keeps its place for every outcome that changed nothing.
    from chat.agent.handle_booking import _LOOP_EXHAUSTED_REPLY

    registry = _RecordingRegistry({"list_my_appointments": _listing([])})
    client = _exhausting_client(_tool_use_response([("list_my_appointments", {})]))

    _, result = await _run(client, registry, _bursts("what have I got?"))

    assert result.outcome is BookingOutcome.INFORMATIONAL
    assert result.reply_text == _LOOP_EXHAUSTED_REPLY


async def test_no_exhaustion_reply_ever_carries_an_identifier() -> None:
    from chat.agent.handle_booking import (
        _LOOP_EXHAUSTED_CANCELLED_REPLY_PLAIN,
        _LOOP_EXHAUSTED_RESCHEDULED_REPLY_PLAIN,
        _LOOP_EXHAUSTED_UNCHANGED_REPLY,
        _LOOP_EXHAUSTED_UNKNOWN_REPLY,
    )

    from .test_handle_booking import _identifiers_in

    for reply in (
        _LOOP_EXHAUSTED_CANCELLED_REPLY_PLAIN,
        _LOOP_EXHAUSTED_RESCHEDULED_REPLY_PLAIN,
        _LOOP_EXHAUSTED_UNCHANGED_REPLY,
        _LOOP_EXHAUSTED_UNKNOWN_REPLY,
    ):
        assert _identifiers_in(reply) == []


# --- a write tool that raised ------------------------------------------------


async def test_a_write_tool_that_raised_is_an_unknown_outcome_not_unavailable() -> None:
    """A handler that raised may have raised *after* the change landed.

    `_dispatch` already tells the model so - its explanation says the outcome is not
    known - but the status it returned was `error`, which folds into `UNAVAILABLE`, and
    the composing step is now told `unavailable` means nothing was created, moved or
    cancelled. The status has to carry the same meaning its own explanation does.
    """
    registry = _RecordingRegistry({"cancel_appointment": _cancelled_result()})
    registry.raise_on = "cancel_appointment"
    client = _client(
        [
            _tool_use_response([("cancel_appointment", _CANCEL_ARGUMENTS)]),
            _text_response("I could not confirm that."),
        ]
    )

    _, result = await _run(client, registry, _bursts("cancel it", "sure?", "yes"))

    assert result.outcome is BookingOutcome.OUTCOME_UNKNOWN


async def test_a_read_tool_that_raised_is_still_unavailable() -> None:
    # A read wrote nothing by construction, so "nothing happened" stays a fact there.
    registry = _RecordingRegistry({"list_my_appointments": _listing([])})
    registry.raise_on = "list_my_appointments"
    client = _client(
        [
            _tool_use_response([("list_my_appointments", {})]),
            _text_response("I could not look that up."),
        ]
    )

    _, result = await _run(client, registry, _bursts("what have I got?"))

    assert result.outcome is BookingOutcome.UNAVAILABLE


# --- one turn, two changes: the lost one must not be masked -------------------


async def test_a_lost_write_beside_a_no_op_is_never_reported_as_nothing_written() -> (
    None
):
    """`observed` accumulates across every tool call the turn made, not just one.

    The model can emit several tool_use blocks in one message, and they are dispatched
    together - so "move my Tuesday appointment and cancel my Friday one" can produce a
    provable no-op and a lost write at once. `unchanged` would then label the turn,
    and the composing step is told that label means nothing was written and to report
    it as already done - with an irreversible cancellation of unknown fate open.
    """
    registry = _RecordingRegistry(
        {
            "reschedule_appointment": {
                "status": "unchanged",
                "appointment": _entry(),
                "explanation": "Already at that time.",
            },
            "cancel_appointment": {
                "status": "unknown",
                "explanation": "It is not known whether that went through.",
            },
        }
    )
    client = _client(
        [
            _tool_use_response(
                [
                    ("reschedule_appointment", _RESCHEDULE_ARGUMENTS),
                    ("cancel_appointment", _CANCEL_ARGUMENTS),
                ]
            ),
            _text_response("One was already set; I could not confirm the other."),
        ]
    )

    _, result = await _run(client, registry, _bursts("move one, cancel the other"))

    assert result.outcome is BookingOutcome.OUTCOME_UNKNOWN


async def test_the_exhausted_reply_for_that_turn_claims_nothing_either_way() -> None:
    # The sharpest form: `_LOOP_EXHAUSTED_UNCHANGED_REPLY` says "nothing needed to
    # change", which is a flat denial of the lost cancellation.
    from chat.agent.handle_booking import _LOOP_EXHAUSTED_UNCHANGED_REPLY

    registry = _RecordingRegistry(
        {
            "reschedule_appointment": {
                "status": "unchanged",
                "appointment": _entry(),
                "explanation": "Already at that time.",
            },
            "cancel_appointment": {"status": "unknown", "explanation": "Not known."},
            "list_my_appointments": _listing([]),
        }
    )
    client = _exhausting_client(
        _tool_use_response(
            [
                ("reschedule_appointment", _RESCHEDULE_ARGUMENTS),
                ("cancel_appointment", _CANCEL_ARGUMENTS),
            ]
        )
    )

    _, result = await _run(client, registry, _bursts("move one, cancel the other"))

    assert result.outcome is BookingOutcome.OUTCOME_UNKNOWN
    assert result.reply_text != _LOOP_EXHAUSTED_UNCHANGED_REPLY


# --- a change handler that raised --------------------------------------------


async def test_a_raising_change_handler_is_told_about_changes_not_bookings() -> None:
    # The wrong-verb problem this feature fixed one file over: booking's sentence tells
    # the model not to say it was *booked* and not to *book* it again, and never
    # mentions the claim it actually must not make about a cancellation.
    registry = _RecordingRegistry({"cancel_appointment": _cancelled_result()})
    registry.raise_on = "cancel_appointment"
    client = _client(
        [
            _tool_use_response([("cancel_appointment", _CANCEL_ARGUMENTS)]),
            _text_response("I could not confirm that."),
        ]
    )

    await _run(client, registry, _bursts("cancel it", "sure?", "yes"))

    import json

    sent = client.messages.create.await_args_list[1].kwargs["messages"]
    reported = json.loads(sent[-1]["content"][0]["content"])
    explanation = reported["explanation"].lower()
    assert reported["status"] == "unknown"
    assert "book" not in explanation
    assert "not known" in explanation


async def test_a_raising_change_handler_still_records_the_unknown_outcome() -> None:
    # contracts/log-events.md: an unknown outcome is recorded as
    # `change.outcome_unknown`, never as a completed change. A handler that raised is
    # exactly such an outcome, and it took the one path that skipped the record.
    registry = _RecordingRegistry({"cancel_appointment": _cancelled_result()})
    registry.raise_on = "cancel_appointment"
    client = _client(
        [
            _tool_use_response([("cancel_appointment", _CANCEL_ARGUMENTS)]),
            _text_response("I could not confirm that."),
        ]
    )

    with capture_logs() as logs:
        await _run(client, registry, _bursts("cancel it", "sure?", "yes"))

    event = next(log for log in logs if log["event"] == "change.outcome_unknown")
    assert event["operation"] == "cancel"
    assert event["appointment_id"] == _APPOINTMENT_ID
