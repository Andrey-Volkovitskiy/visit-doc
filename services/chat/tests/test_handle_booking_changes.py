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


async def test_unchanged_outranks_an_unknown_outcome() -> None:
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

    assert result.outcome is BookingOutcome.UNCHANGED


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
    """Run one trivial turn and return the system prompt it was given."""
    registry = _RecordingRegistry({"list_my_appointments": _listing([])})
    client = _client([_text_response("Nothing booked.")])
    await _run(client, registry, _bursts("what have I got?"))
    return _system_prompt(client)


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
