"""Tests for the booking specialist's tool-use loop.

The model is mocked, so what is exercised here is the loop: which tool calls it
actually dispatches, what it does with the results, and how the turn's outcome is
derived. Assertions are on unmocked artifacts - the dispatched calls, the derived
outcome, the messages that reached the model - never on canned reply text.
"""

import json
import re
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from chat.agent.handle_booking import (
    _LOOP_EXHAUSTED_REPLY,
    BookingOutcome,
    BookingResult,
    handle_booking,
)
from chat.agent.tools.registry import Tool, ToolContext, ToolRegistry
from chat.agent.tools.scheduling_tools import (
    _EXPLANATION_BY_REASON,
    _UNAVAILABLE_EXPLANATION,
    SCHEDULING_TOOLS,
    derive_idempotency_key,
)
from chat.core.config import Settings
from chat.domain.models import Message, MessageSender
from chat.domain.schemas import ChatTokenEvent
from structlog.testing import capture_logs

_LOCAL_NOW = "2026-08-17T08:00:00"
_ROSTER_READ = "list_practitioners"
_PRACTITIONER_ID = "01PRACT0000000000000000000"
_STARTS_AT = "2026-08-18T09:00:00"


def _message(content: str, id: str, sender: MessageSender) -> Message:
    return Message(sender=sender, content=content, id=id)


def _bursts(*contents: str) -> list[list[Message]]:
    """Build alternating patient/assistant bursts ending with a patient message."""
    bursts: list[list[Message]] = []
    for index, content in enumerate(contents):
        sender = MessageSender.PATIENT if index % 2 == 0 else MessageSender.ASSISTANT
        bursts.append([_message(content, f"m{index}", sender)])
    return bursts


class _RecordingRegistry(ToolRegistry):
    """A registry whose tools return canned results and record every dispatch."""

    def __init__(self, results: dict[str, Any]) -> None:
        self.dispatched: list[tuple[str, dict[str, Any]]] = []
        # Set by a test whose point is a handler failing rather than answering.
        self.raise_on: str | None = None
        # `handle_booking` reads the roster through the registry before the model's
        # first turn, so every turn needs a result for it - a test names one itself
        # only when the roster is what that test is about.
        self._results = {_ROSTER_READ: {"practitioners": []}, **results}
        tools = [
            Tool(
                name=tool.name,
                description=tool.description,
                input_schema=tool.input_schema,
                handler=self._handler,
                # Carried through: it decides what a raising handler is reported as.
                writes=tool.writes,
            )
            for tool in SCHEDULING_TOOLS
        ]
        super().__init__(
            tools,
            ToolContext(
                channel=MagicMock(),
                settings=MagicMock(spec=Settings),
                session_id="01SESSION",
                patient_id="01PATIENT",
                local_now=datetime(2026, 8, 17, 8, 0),
            ),
        )

    async def dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.dispatched.append((name, arguments))
        return await super().dispatch(name, arguments)

    def set_result(self, name: str, result: dict[str, Any]) -> None:
        """Replace one canned result partway through a turn."""
        self._results[name] = result

    async def _handler(
        self, _context: ToolContext, _arguments: dict[str, Any]
    ) -> dict[str, Any]:
        name = self.dispatched[-1][0]
        if name == self.raise_on:
            raise RuntimeError("handler blew up")
        return self._results[name]


def _model_dispatched(registry: _RecordingRegistry) -> list[str]:
    """Return the tools the *model* asked for, in order.

    `handle_booking` reads the roster through this same registry before the model's
    first turn, so the first dispatch of every turn is the node's own, not the
    model's - asserted here rather than left to each caller to skip past.
    """
    assert registry.dispatched[0][0] == _ROSTER_READ
    return [name for name, _ in registry.dispatched[1:]]


def _system_prompt(client: MagicMock) -> str:
    """Return the system prompt the turn's first model call was given."""
    return str(client.messages.create.await_args_list[0].kwargs["system"])


def _tool_use_response(calls: list[tuple[str, dict[str, Any]]]) -> MagicMock:
    blocks = []
    for index, (name, arguments) in enumerate(calls):
        block = MagicMock()
        block.type = "tool_use"
        block.id = f"toolu_{index}"
        block.name = name
        block.input = arguments
        blocks.append(block)
    response = MagicMock()
    response.content = blocks
    return response


def _text_response(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.content = [block]
    return response


def _client(responses: list[MagicMock]) -> MagicMock:
    client = MagicMock()
    client.messages.create = AsyncMock(side_effect=list(responses))
    return client


async def _run(
    client: MagicMock,
    registry: ToolRegistry,
    bursts: list[list[Message]],
    *,
    stream: bool = True,
) -> tuple[list[ChatTokenEvent], BookingResult]:
    events: list[ChatTokenEvent] = []
    result: BookingResult | None = None
    async for item in handle_booking(
        client,
        registry,
        bursts,
        patient_name="Ada Lovelace",
        local_now=_LOCAL_NOW,
        stream=stream,
    ):
        if isinstance(item, BookingResult):
            result = item
        else:
            events.append(item)
    assert result is not None
    return events, result


# --- the loop ----------------------------------------------------------------


async def test_the_loop_chains_list_then_availability_then_book_in_one_turn() -> None:
    registry = _RecordingRegistry(
        {
            "list_practitioners": {"practitioners": []},
            "check_availability": {"available_starts": [_STARTS_AT]},
            "book_appointment": {
                "status": "booked",
                "appointment": {"id": "01APPOINTMENT", "starts_at": _STARTS_AT},
            },
        }
    )
    client = _client(
        [
            _tool_use_response([("list_practitioners", {})]),
            _tool_use_response(
                [
                    (
                        "check_availability",
                        {
                            "practitioner_id": _PRACTITIONER_ID,
                            "from_date": "2026-08-18",
                            "to_date": "2026-08-18",
                        },
                    )
                ]
            ),
            _tool_use_response(
                [
                    (
                        "book_appointment",
                        {
                            "practitioner_id": _PRACTITIONER_ID,
                            "starts_at": _STARTS_AT,
                        },
                    )
                ]
            ),
            _text_response("You're booked for Tuesday at 9."),
        ]
    )

    _, result = await _run(client, registry, _bursts("book me with anyone Tuesday"))

    assert _model_dispatched(registry) == [
        "list_practitioners",
        "check_availability",
        "book_appointment",
    ]
    assert result.outcome is BookingOutcome.BOOKED
    assert result.appointment_id == "01APPOINTMENT"
    assert result.iterations == 4
    assert result.tool_calls == 3


async def test_a_turn_with_no_tool_calls_is_informational() -> None:
    registry = _RecordingRegistry({})
    client = _client([_text_response("What day suits you?")])

    _, result = await _run(client, registry, _bursts("I'd like an appointment"))

    assert _model_dispatched(registry) == []
    assert result.outcome is BookingOutcome.INFORMATIONAL


async def test_a_turn_that_offered_times_awaits_the_patients_choice() -> None:
    registry = _RecordingRegistry(
        {"check_availability": {"available_starts": [_STARTS_AT]}}
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
                        },
                    )
                ]
            ),
            _text_response("Tuesday at 9 is free - shall I book it?"),
        ]
    )

    _, result = await _run(client, registry, _bursts("anything Tuesday?"))

    assert result.outcome is BookingOutcome.AWAITING_CONFIRMATION
    assert result.appointment_id is None


# --- the outcome is derived, never read off the reply ------------------------


async def test_a_reply_claiming_a_booking_does_not_make_the_outcome_booked() -> None:
    """The reply is the model's; the outcome is the loop's, from what tools returned."""
    registry = _RecordingRegistry(
        {
            "book_appointment": {
                "status": "refused",
                "reason": "practitioner_busy",
                "explanation": "That time was taken.",
            }
        }
    )
    client = _client(
        [
            _tool_use_response(
                [
                    (
                        "book_appointment",
                        {"practitioner_id": _PRACTITIONER_ID, "starts_at": _STARTS_AT},
                    )
                ]
            ),
            _text_response("Great - you're all booked for Tuesday at 9!"),
        ]
    )

    _, result = await _run(client, registry, _bursts("book Tuesday at 9"))

    assert result.outcome is BookingOutcome.REFUSED
    assert result.appointment_id is None


async def test_an_unavailable_result_outranks_a_refusal() -> None:
    registry = _RecordingRegistry(
        {
            "check_availability": {"status": "unavailable", "explanation": "down"},
            "book_appointment": {"status": "refused", "reason": "off_grid"},
        }
    )
    client = _client(
        [
            _tool_use_response(
                [
                    (
                        "book_appointment",
                        {"practitioner_id": _PRACTITIONER_ID, "starts_at": _STARTS_AT},
                    ),
                    (
                        "check_availability",
                        {
                            "practitioner_id": _PRACTITIONER_ID,
                            "from_date": "2026-08-18",
                            "to_date": "2026-08-18",
                        },
                    ),
                ]
            ),
            _text_response("Something went wrong."),
        ]
    )

    _, result = await _run(client, registry, _bursts("book Tuesday at 9"))

    assert result.outcome is BookingOutcome.UNAVAILABLE


async def test_a_booking_that_succeeded_after_a_refusal_is_a_success() -> None:
    results = {
        "book_appointment": {
            "status": "refused",
            "reason": "practitioner_busy",
            "explanation": "taken",
        }
    }
    registry = _RecordingRegistry(results)
    client = _client(
        [
            _tool_use_response(
                [
                    (
                        "book_appointment",
                        {"practitioner_id": _PRACTITIONER_ID, "starts_at": _STARTS_AT},
                    )
                ]
            ),
            _tool_use_response(
                [
                    (
                        "book_appointment",
                        {
                            "practitioner_id": _PRACTITIONER_ID,
                            "starts_at": "2026-08-18T10:00:00",
                        },
                    )
                ]
            ),
            _text_response("Booked for 10 instead."),
        ]
    )

    # Flip the canned result after the first booking attempt, so the second succeeds.
    original = registry.dispatch

    async def dispatch(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await original(name, arguments)
        if name == "book_appointment":
            registry.set_result(
                "book_appointment",
                {"status": "booked", "appointment": {"id": "01APPOINTMENT"}},
            )
        return result

    registry.dispatch = dispatch  # type: ignore[method-assign]

    _, result = await _run(client, registry, _bursts("book Tuesday at 9"))

    assert result.outcome is BookingOutcome.BOOKED
    assert result.appointment_id == "01APPOINTMENT"


# --- bounds ------------------------------------------------------------------


async def test_the_loop_stops_at_its_iteration_bound() -> None:
    registry = _RecordingRegistry({"list_practitioners": {"practitioners": []}})
    # The model never stops asking for tools, so only the bound ends the turn.
    client = _client([_tool_use_response([("list_practitioners", {})])] * 20)

    with capture_logs() as logs:
        _, result = await _run(client, registry, _bursts("book me something"))

    exhausted = next(e for e in logs if e["event"] == "booking.loop_exhausted")
    assert exhausted["iterations"] == 6
    assert exhausted["log_level"] == "warning"
    assert result.iterations == 6
    assert len(_model_dispatched(registry)) == 6
    assert result.reply_text


async def test_the_conversation_context_is_bounded_to_the_last_five_turns() -> None:
    registry = _RecordingRegistry({})
    client = _client([_text_response("ok")])
    # Eight complete turns plus the current unanswered message.
    bursts = _bursts(*[f"m{i}" for i in range(16)], "book me something")

    await _run(client, registry, bursts)

    sent = client.messages.create.call_args.kwargs["messages"]
    # Five complete turns is ten bursts, plus the trailing patient message.
    assert len(sent) == 11
    assert sent[-1]["content"] == "book me something"
    assert not any("m0" == entry["content"] for entry in sent)


async def test_within_turn_tool_results_are_never_truncated_away() -> None:
    registry = _RecordingRegistry({"list_practitioners": {"practitioners": []}})
    client = _client(
        [
            _tool_use_response([("list_practitioners", {})]),
            _tool_use_response([("list_practitioners", {})]),
            _text_response("ok"),
        ]
    )
    bursts = _bursts(*[f"m{i}" for i in range(16)], "book me something")

    await _run(client, registry, bursts)

    final_messages = client.messages.create.call_args.kwargs["messages"]
    tool_results = [
        block
        for entry in final_messages
        if isinstance(entry["content"], list)
        for block in entry["content"]
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]
    assert len(tool_results) == 2


# --- what reaches the model --------------------------------------------------


async def test_the_clients_local_now_reaches_the_system_prompt_verbatim() -> None:
    registry = _RecordingRegistry({})
    client = _client([_text_response("ok")])

    await _run(client, registry, _bursts("book me next Tuesday"))

    system = client.messages.create.call_args.kwargs["system"]
    assert _LOCAL_NOW in system
    assert "Ada Lovelace" in system


async def test_the_prompt_forbids_booking_without_confirmation() -> None:
    """FR-027 is a prompt rule reinforced by the tool's own description.

    Asserted structurally because a mocked model cannot demonstrate judgement - what is
    checkable is that the instruction is actually sent, on every turn.
    """
    registry = _RecordingRegistry({})
    client = _client([_text_response("ok")])

    await _run(client, registry, _bursts("book me something"))

    system = client.messages.create.call_args.kwargs["system"]
    assert "Confirm BOTH the practitioner and the exact start time" in system

    book_tool = next(t for t in SCHEDULING_TOOLS if t.name == "book_appointment")
    assert "explicitly confirmed" in book_tool.description


async def test_the_prompt_forbids_leaking_ids_and_timezones() -> None:
    registry = _RecordingRegistry({})
    client = _client([_text_response("ok")])

    await _run(client, registry, _bursts("book me something"))

    system = client.messages.create.call_args.kwargs["system"]
    assert "Never mention a timezone, an internal" in system


async def test_the_prompt_carries_both_disambiguation_rules() -> None:
    """FR-052/FR-053: list and ask when several match; name only real specialties."""
    registry = _RecordingRegistry({})
    client = _client([_text_response("ok")])

    await _run(client, registry, _bursts("I'd like to see a dentist"))

    system = _system_prompt(client)
    assert "list them and ask" in system
    assert "name the specialties that are listed - never one that is\nnot" in system


async def test_the_model_sees_every_registered_tool() -> None:
    registry = _RecordingRegistry({})
    client = _client([_text_response("ok")])

    await _run(client, registry, _bursts("book me something"))

    tools = client.messages.create.call_args.kwargs["tools"]
    assert [t["name"] for t in tools] == [t.name for t in SCHEDULING_TOOLS]


# --- failures ----------------------------------------------------------------


async def test_a_handler_that_raises_is_reported_to_the_model_not_raised() -> None:
    registry = _RecordingRegistry({})
    registry.raise_on = "list_practitioners"

    client = _client(
        [
            _tool_use_response([("list_practitioners", {})]),
            _text_response("Sorry, something went wrong."),
        ]
    )

    with capture_logs() as logs:
        _, result = await _run(client, registry, _bursts("who's available?"))

    failed = next(e for e in logs if e["event"] == "booking.tool_failed")
    assert failed["tool_name"] == "list_practitioners"
    assert failed["log_level"] == "error"
    # Nothing was created, which is the same thing the patient needs to hear as an
    # exhausted retry budget - and is never mistaken for a pending choice.
    assert result.outcome is BookingOutcome.UNAVAILABLE


async def test_a_raising_write_is_not_reported_as_having_created_nothing() -> None:
    """A booking handler can raise *after* the appointment was created.

    Rendering the scheduler's own response is one way; whatever the cause, the call
    landing and the failure coming afterwards is indistinguishable from here - so the
    model must not be told nothing was booked and invited to try again.
    """
    registry = _RecordingRegistry({})
    registry.raise_on = "book_appointment"
    client = _client(
        [
            _tool_use_response(
                [
                    (
                        "book_appointment",
                        {
                            "practitioner_id": _PRACTITIONER_ID,
                            "starts_at": _STARTS_AT,
                        },
                    )
                ]
            ),
            _text_response("Sorry, something went wrong."),
        ]
    )

    _, result = await _run(client, registry, _bursts("book me in Friday at 9"))

    sent = client.messages.create.await_args_list[1].kwargs["messages"]
    reported = json.loads(sent[-1]["content"][0]["content"])
    assert "Nothing was booked" not in reported["explanation"]
    assert "not known whether" in reported["explanation"]
    # The outcome carries the same meaning the explanation does. `UNAVAILABLE` would
    # not: the composing step is told that one means nothing was created, moved or
    # cancelled, which is precisely what this path cannot claim.
    assert result.outcome is BookingOutcome.OUTCOME_UNKNOWN


async def test_every_tool_call_leaves_a_called_and_a_result_line() -> None:
    registry = _RecordingRegistry({"list_practitioners": {"practitioners": []}})
    client = _client(
        [_tool_use_response([("list_practitioners", {})]), _text_response("ok")]
    )

    with capture_logs() as logs:
        await _run(client, registry, _bursts("who's available?"))

    events = [e["event"] for e in logs]
    assert events.count("booking.tool_called") == 1
    assert events.count("booking.tool_result") == 1


# --- streaming vs collecting -------------------------------------------------


async def test_streaming_mode_yields_the_reply_as_a_token_event() -> None:
    registry = _RecordingRegistry({})
    client = _client([_text_response("What day suits you?")])

    events, result = await _run(client, registry, _bursts("book me"), stream=True)

    assert [e.text for e in events] == ["What day suits you?"]
    assert result.reply_text == "What day suits you?"


async def test_collect_mode_emits_nothing_and_only_returns_the_result() -> None:
    registry = _RecordingRegistry({})
    client = _client([_text_response("What day suits you?")])

    events, result = await _run(client, registry, _bursts("book me"), stream=False)

    assert events == []
    assert result.reply_text == "What day suits you?"


@pytest.mark.parametrize("stream", [True, False])
async def test_the_outcome_is_the_same_in_either_mode(stream: bool) -> None:
    registry = _RecordingRegistry(
        {
            "book_appointment": {
                "status": "booked",
                "appointment": {"id": "01APPOINTMENT"},
            }
        }
    )
    client = _client(
        [
            _tool_use_response(
                [
                    (
                        "book_appointment",
                        {"practitioner_id": _PRACTITIONER_ID, "starts_at": _STARTS_AT},
                    )
                ]
            ),
            _text_response("Booked."),
        ]
    )

    _, result = await _run(client, registry, _bursts("book it"), stream=stream)

    assert result.outcome is BookingOutcome.BOOKED


# --- the two read-only questions ---------------------------------------------


async def test_asking_who_is_available_lists_practitioners_and_writes_nothing() -> None:
    registry = _RecordingRegistry(
        {
            "list_practitioners": {
                "practitioners": [
                    {"id": _PRACTITIONER_ID, "full_name": "Osler", "bookable": True}
                ]
            }
        }
    )
    client = _client(
        [
            _tool_use_response([("list_practitioners", {})]),
            _text_response("We have one general practitioner available."),
        ]
    )

    _, result = await _run(client, registry, _bursts("who can I see?"))

    assert _model_dispatched(registry) == ["list_practitioners"]
    assert result.outcome is BookingOutcome.INFORMATIONAL
    assert result.appointment_id is None


async def test_asking_what_i_have_booked_lists_appointments_and_writes_nothing() -> (
    None
):
    registry = _RecordingRegistry(
        {
            "list_my_appointments": {
                "appointments": [
                    {
                        "practitioner_full_name": "Osler",
                        "specialty": "General Practice",
                        "starts_at": _STARTS_AT,
                        "ends_at": "2026-08-18T10:00:00",
                    }
                ]
            }
        }
    )
    client = _client(
        [
            _tool_use_response([("list_my_appointments", {})]),
            _text_response("You have one appointment, Tuesday at 9."),
        ]
    )

    _, result = await _run(client, registry, _bursts("what have I got booked?"))

    assert _model_dispatched(registry) == ["list_my_appointments"]
    assert result.outcome is BookingOutcome.INFORMATIONAL


async def test_a_read_only_turn_is_informational_not_awaiting_confirmation() -> None:
    """Nothing was offered, so there is nothing for the patient to confirm.

    The distinction matters to the merge step, which is told the booking half's outcome
    verbatim - reporting a "what have I booked?" answer as awaiting_confirmation would
    invite a reply implying a pending decision that does not exist.
    """
    registry = _RecordingRegistry({"list_my_appointments": {"appointments": []}})
    client = _client(
        [
            _tool_use_response([("list_my_appointments", {})]),
            _text_response("You have nothing upcoming."),
        ]
    )

    _, result = await _run(client, registry, _bursts("anything booked?"))

    assert result.outcome is BookingOutcome.INFORMATIONAL


async def test_offering_times_awaits_confirmation_rather_than_being_informational() -> (
    None
):
    registry = _RecordingRegistry(
        {"check_availability": {"available_starts": [_STARTS_AT]}}
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
                        },
                    )
                ]
            ),
            _text_response("Tuesday at 9 is free - shall I book it?"),
        ]
    )

    _, result = await _run(client, registry, _bursts("anything Tuesday?"))

    assert result.outcome is BookingOutcome.AWAITING_CONFIRMATION


async def test_a_read_only_turn_never_reaches_the_booking_tool() -> None:
    registry = _RecordingRegistry(
        {
            "list_practitioners": {"practitioners": []},
            "list_my_appointments": {"appointments": []},
        }
    )
    client = _client(
        [
            _tool_use_response(
                [("list_practitioners", {}), ("list_my_appointments", {})]
            ),
            _text_response("Here is who we have, and what you have booked."),
        ]
    )

    _, result = await _run(
        client, registry, _bursts("who's there, and what have I got?")
    )

    dispatched = set(_model_dispatched(registry))
    assert dispatched == {"list_practitioners", "list_my_appointments"}
    assert "book_appointment" not in dispatched
    assert result.outcome is BookingOutcome.INFORMATIONAL


async def test_both_read_only_tools_receive_the_turns_own_ambient_patient() -> None:
    """The model never supplies the patient - so it cannot ask about someone else's."""
    registry = _RecordingRegistry(
        {"list_my_appointments": {"future": [], "past": [], "past_truncated": False}}
    )
    client = _client(
        [
            _tool_use_response(
                [("list_my_appointments", {"patient_id": "01SOMEONEELSE"})]
            ),
            _text_response("Nothing upcoming."),
        ]
    )

    await _run(client, registry, _bursts("what have I got booked?"))

    # The model's smuggled argument reaches the handler as a plain argument and is
    # simply unused: the handler reads the patient from its bound context instead. The
    # schema is closed and names only the two listing axes, so `patient_id` is not
    # something the tool accepts at all.
    tool_schema = next(
        t for t in SCHEDULING_TOOLS if t.name == "list_my_appointments"
    ).input_schema
    assert set(tool_schema["properties"]) == {"time_filter", "status_filter"}
    assert tool_schema["additionalProperties"] is False


# --- identifiers never reach the patient -------------------------------------

# A ULID as this codebase mints them, and a derived idempotency key: the two identifier
# shapes that pass through the booking loop on their way to and from the tools.
_ULID_PATTERN = re.compile(r"\b[0-9A-HJKMNP-TV-Z]{26}\b")
_UUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
)


def _identifiers_in(text: str) -> list[str]:
    """Return every identifier-shaped token in `text`."""
    return _ULID_PATTERN.findall(text) + _UUID_PATTERN.findall(text)


async def test_exhausting_the_loop_after_a_booking_never_denies_the_appointment() -> (
    None
):
    """The loop can run out *after* book_appointment succeeded.

    Reporting the generic failure then denies an appointment that exists and cannot be
    cancelled, and invites the patient to book a second one at a different time - which
    derives a different key and really would create it.
    """
    registry = _RecordingRegistry(
        {
            "book_appointment": {
                "status": "booked",
                "appointment": {
                    "id": "01APPT00000000000000000000",
                    "practitioner_full_name": "William Osler",
                    "starts_at": _STARTS_AT,
                    "ends_at": "2026-08-18T10:00:00",
                },
            },
            "list_my_appointments": {"appointments": []},
        }
    )
    booking_call = _tool_use_response(
        [
            (
                "book_appointment",
                {"practitioner_id": _PRACTITIONER_ID, "starts_at": _STARTS_AT},
            )
        ]
    )
    # The model keeps calling tools instead of writing its confirmation, so the loop
    # runs out with the booking already made.
    client = _client(
        [booking_call]
        + [_tool_use_response([("list_my_appointments", {})]) for _ in range(5)]
    )

    events, result = await _run(client, registry, _bursts("book me"), stream=True)

    assert result.outcome is BookingOutcome.BOOKED
    assert result.appointment_id == "01APPT00000000000000000000"
    assert result.reply_text != _LOOP_EXHAUSTED_REPLY
    assert "William Osler" in result.reply_text
    assert _identifiers_in(result.reply_text) == []
    assert "".join(e.text for e in events) == result.reply_text


async def test_exhausting_the_loop_without_a_booking_still_reports_the_failure() -> (
    None
):
    registry = _RecordingRegistry({"list_my_appointments": {"appointments": []}})
    client = _client(
        [_tool_use_response([("list_my_appointments", {})]) for _ in range(6)]
    )

    _, result = await _run(client, registry, _bursts("book me"), stream=True)

    assert result.outcome is not BookingOutcome.BOOKED
    assert result.reply_text == _LOOP_EXHAUSTED_REPLY


def test_the_loops_own_failure_reply_carries_no_identifier() -> None:
    """The reply the loop writes itself, when the model never produced one.

    Only the loop-authored replies are assertable here: the model's own text is canned
    by the mock, so asserting it holds no identifier would only prove the mock said what
    the test told it to. What is checkable - and checked - is that the loop never
    interpolates an id into a reply of its own, and that the prompt forbids the model
    from doing so (`test_the_prompt_forbids_leaking_ids_and_timezones`).
    """
    assert _identifiers_in(_LOOP_EXHAUSTED_REPLY) == []


def test_no_handler_authored_explanation_carries_an_identifier() -> None:
    explanations = [
        _UNAVAILABLE_EXPLANATION,
        *_EXPLANATION_BY_REASON.values(),
    ]

    assert [e for e in explanations if _identifiers_in(e)] == []


async def test_the_identifiers_the_loop_handles_are_genuinely_identifier_shaped() -> (
    None
):
    """Guards the two tests above from passing vacuously.

    If the patterns matched nothing real, the assertions would hold no matter what the
    loop did - so this pins that a tool result really does carry ids of that shape.
    """
    appointment_id = "01KZXYCGKQSSMYC38S9BXBWSBP"
    registry = _RecordingRegistry(
        {
            "book_appointment": {
                "status": "booked",
                "appointment": {"id": appointment_id, "starts_at": _STARTS_AT},
            }
        }
    )
    client = _client(
        [
            _tool_use_response(
                [
                    (
                        "book_appointment",
                        {"practitioner_id": _PRACTITIONER_ID, "starts_at": _STARTS_AT},
                    )
                ]
            ),
            _text_response("Booked."),
        ]
    )

    _, result = await _run(client, registry, _bursts("book it"))

    # The id reached the loop and was carried on the result, where the reply is not.
    assert result.appointment_id == appointment_id
    assert _identifiers_in(appointment_id) == [appointment_id]
    assert _identifiers_in(derive_idempotency_key("01P", "01D", datetime(2026, 8, 18)))


async def test_a_write_rejected_for_bad_arguments_says_nothing_was_created() -> None:
    """The one failed write the model may be told created nothing, and may retry.

    A missing argument is caught while reading it, before the scheduler is called at
    all - so unlike a handler that raised mid-write, there is no attempt whose fate is
    unknown, and no reason to send the patient to the clinic over it.
    """
    registry = ToolRegistry(
        SCHEDULING_TOOLS,
        ToolContext(
            channel=MagicMock(),
            settings=MagicMock(spec=Settings),
            session_id="01SESSION",
            patient_id="01PATIENT",
            local_now=datetime(2026, 8, 17, 8, 0),
        ),
    )
    client = _client(
        [
            # `starts_at` omitted, which its schema marks required.
            _tool_use_response(
                [("book_appointment", {"practitioner_id": _PRACTITIONER_ID})]
            ),
            _text_response("Which time would you like?"),
        ]
    )

    with capture_logs() as logs:
        _, result = await _run(client, registry, _bursts("book me in with Osler"))

    sent = client.messages.create.await_args_list[1].kwargs["messages"]
    reported = json.loads(sent[-1]["content"][0]["content"])
    assert "nothing was created" in reported["explanation"]
    # Names the argument, so the model can correct the call rather than repeat it.
    assert "starts_at" in reported["explanation"]
    assert "not known whether" not in reported["explanation"]

    assert [e["event"] for e in logs if e["event"].startswith("booking.tool_")] == [
        "booking.tool_called",
        "booking.tool_arguments_invalid",
    ]
    assert result.outcome is BookingOutcome.UNAVAILABLE


# --- the roster the prompt carries -------------------------------------------


async def test_the_roster_is_read_once_and_put_in_the_system_prompt() -> None:
    registry = _RecordingRegistry(
        {
            _ROSTER_READ: {
                "practitioners": [
                    {
                        "id": _PRACTITIONER_ID,
                        "full_name": "William Osler",
                        "specialty": "General Practice",
                        "appointment_duration_minutes": 60,
                        "bookable": True,
                    }
                ]
            }
        }
    )
    client = _client([_text_response("Which day suits you?")])

    await _run(client, registry, _bursts("I'd like to see someone"))

    system = _system_prompt(client)
    assert _PRACTITIONER_ID in system
    assert "William Osler" in system
    assert "General Practice" in system
    assert _model_dispatched(registry) == []


async def test_a_practitioner_not_taking_appointments_is_marked_as_such() -> None:
    registry = _RecordingRegistry(
        {
            _ROSTER_READ: {
                "practitioners": [
                    {
                        "id": _PRACTITIONER_ID,
                        "full_name": "William Osler",
                        "specialty": "General Practice",
                        "appointment_duration_minutes": 60,
                        "bookable": False,
                    }
                ]
            }
        }
    )
    client = _client([_text_response("Nobody is taking appointments right now.")])

    await _run(client, registry, _bursts("I'd like to see someone"))

    assert "not currently taking appointments" in _system_prompt(client)


async def test_an_unreadable_roster_says_it_is_not_known_who_works_here() -> None:
    # What the read-only tools return when the scheduler cannot be reached at all.
    registry = _RecordingRegistry(
        {
            _ROSTER_READ: {
                "status": "unavailable",
                "explanation": _UNAVAILABLE_EXPLANATION,
            }
        }
    )
    client = _client([_text_response("I can't reach the clinic's schedule.")])

    with capture_logs() as logs:
        _, result = await _run(client, registry, _bursts("book me with Dr. Osler"))

    system = _system_prompt(client)
    assert "could not be read" in system
    # The name the patient just used must not come back as an established fact.
    assert "Osler" not in system
    unread = next(e for e in logs if e["event"] == "booking.roster_unread")
    assert unread["log_level"] == "warning"
    assert unread["status"] == "unavailable"
    assert result.outcome is BookingOutcome.INFORMATIONAL


async def test_a_roster_read_that_raises_does_not_fail_the_turn() -> None:
    registry = _RecordingRegistry({})
    registry.raise_on = _ROSTER_READ
    client = _client([_text_response("Let me look that up.")])

    with capture_logs() as logs:
        _, result = await _run(client, registry, _bursts("who works here?"))

    assert "could not be read" in _system_prompt(client)
    unread = next(e for e in logs if e["event"] == "booking.roster_unread")
    assert unread["error_type"] == "RuntimeError"
    assert result.reply_text


async def test_an_empty_roster_is_not_reported_as_an_unreadable_one() -> None:
    # A clinic with nobody on it and a clinic whose roster is unknown need opposite
    # replies, so they must not render the same way.
    registry = _RecordingRegistry({_ROSTER_READ: {"practitioners": []}})
    client = _client([_text_response("We have no practitioners at the moment.")])

    await _run(client, registry, _bursts("who works here?"))

    system = _system_prompt(client)
    assert "no practitioners" in system
    assert "could not be read" not in system
