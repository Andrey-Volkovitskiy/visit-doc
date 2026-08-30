"""Tests for the four scheduling tool handlers.

The gRPC client is faked at its own module boundary, so what is exercised here is the
adapter: which arguments reach the client, and how each domain result becomes something
the model can read.
"""

from datetime import date, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from chat.agent.tools import scheduling_tools
from chat.agent.tools.registry import ToolArgumentError, ToolContext, ToolRegistry
from chat.agent.tools.scheduling_tools import (
    SCHEDULING_TOOLS,
    derive_idempotency_key,
)
from chat.clients.scheduling import (
    AppointmentInfo,
    AppointmentListing,
    AvailabilityResult,
    BookingRefusal,
    BookingSuccess,
    PractitionerInfo,
    SchedulingNotFoundError,
    SchedulingRequestError,
    SchedulingUnavailableError,
    WorkingRangeInfo,
)
from chat.core.config import Settings
from shared_models.scheduling import (
    AppointmentStatus,
    BookingFailureReason,
    NotFoundEntity,
    StatusFilter,
    TimeFilter,
    Weekday,
)
from structlog.testing import capture_logs

_SESSION_ID = "01SESSION0000000000000000"
_PATIENT_ID = "01PATIENT0000000000000000"
_PRACTITIONER_ID = "01PRACTITIONER0000000000"
_LOCAL_NOW = datetime(2026, 8, 17, 8, 0)
_STARTS_AT = datetime(2026, 8, 18, 9, 0)
# The handlers call the gRPC client through this module, so patching it here fakes
# the wire without faking the adapter under test.
_CLIENT = "chat.agent.tools.scheduling_tools.scheduling"


def _settings() -> Settings:
    return Settings(
        DATABASE_URL="postgresql+asyncpg://u:p@localhost:5432/db",
        QDRANT_URL="http://localhost:6333",
        ANTHROPIC_API_KEY="unused",
        VOYAGE_API_KEY="unused",
    )


def _context(patient_id: str | None = _PATIENT_ID) -> ToolContext:
    return ToolContext(
        channel=MagicMock(),
        settings=_settings(),
        session_id=_SESSION_ID,
        patient_id=patient_id,
        local_now=_LOCAL_NOW,
    )


def _registry(context: ToolContext | None = None) -> ToolRegistry:
    return ToolRegistry(SCHEDULING_TOOLS, context or _context())


def _appointment() -> AppointmentInfo:
    return AppointmentInfo(
        id="01APPOINTMENT000000000000",
        patient_id=_PATIENT_ID,
        patient_full_name="Ada",
        practitioner_id=_PRACTITIONER_ID,
        practitioner_full_name="William Osler",
        practitioner_specialty="General Practice",
        starts_at=_STARTS_AT,
        ends_at=datetime(2026, 8, 18, 10, 0),
        status=AppointmentStatus.STANDING,
    )


# --- ambient arguments -------------------------------------------------------


@pytest.mark.parametrize("tool", SCHEDULING_TOOLS, ids=lambda t: t.name)
def test_no_tool_accepts_an_ambient_argument(tool: Any) -> None:
    """A model must not be able to supply a session, a patient, or a clock."""
    properties = set(tool.input_schema.get("properties", {}))
    assert properties.isdisjoint({"session_id", "patient_id", "local_now"})


@pytest.mark.parametrize("tool", SCHEDULING_TOOLS, ids=lambda t: t.name)
def test_every_tool_schema_is_closed(tool: Any) -> None:
    assert tool.input_schema["additionalProperties"] is False


def test_the_registry_renders_exactly_the_registered_tools() -> None:
    rendered = _registry().to_anthropic_tools()

    assert [t["name"] for t in rendered] == [t.name for t in SCHEDULING_TOOLS]
    assert all({"name", "description", "input_schema"} == set(t) for t in rendered)


async def test_a_handler_receives_the_ambient_session_and_clock() -> None:
    with patch(
        _CLIENT + ".list_practitioners", new=AsyncMock(return_value=())
    ) as called:
        await _registry().dispatch("list_practitioners", {})

    assert called.call_args.kwargs["session_id"] == _SESSION_ID


async def test_check_availability_passes_the_ambient_patient_and_clock() -> None:
    result = AvailabilityResult(
        available_starts=(), appointment_duration_minutes=60, truncated=False
    )
    with patch(
        _CLIENT + ".check_availability", new=AsyncMock(return_value=result)
    ) as called:
        await _registry().dispatch(
            "check_availability",
            {
                "practitioner_id": _PRACTITIONER_ID,
                "from_date": "2026-08-17",
                "to_date": "2026-08-21",
            },
        )

    kwargs = called.call_args.kwargs
    assert kwargs["patient_id"] == _PATIENT_ID
    assert kwargs["local_now"] == _LOCAL_NOW
    assert kwargs["from_date"] == date(2026, 8, 17)


# --- the derived idempotency key ---------------------------------------------


def test_the_same_booking_always_derives_the_same_key() -> None:
    first = derive_idempotency_key(_PATIENT_ID, _PRACTITIONER_ID, _STARTS_AT)
    second = derive_idempotency_key(_PATIENT_ID, _PRACTITIONER_ID, _STARTS_AT)

    assert first == second


@pytest.mark.parametrize(
    ("patient", "practitioner", "starts_at"),
    [
        ("01OTHERPATIENT0000000000", _PRACTITIONER_ID, _STARTS_AT),
        (_PATIENT_ID, "01OTHERPRACTITIONER00000", _STARTS_AT),
        (_PATIENT_ID, _PRACTITIONER_ID, datetime(2026, 8, 18, 10, 0)),
    ],
)
def test_a_different_booking_never_derives_the_same_key(
    patient: str, practitioner: str, starts_at: datetime
) -> None:
    baseline = derive_idempotency_key(_PATIENT_ID, _PRACTITIONER_ID, _STARTS_AT)

    assert derive_idempotency_key(patient, practitioner, starts_at) != baseline


async def test_the_booking_call_carries_the_derived_key() -> None:
    success = BookingSuccess(appointment=_appointment(), idempotent_replay=False)
    with patch(
        _CLIENT + ".book_appointment", new=AsyncMock(return_value=success)
    ) as called:
        await _registry().dispatch(
            "book_appointment",
            {"practitioner_id": _PRACTITIONER_ID, "starts_at": "2026-08-18T09:00:00"},
        )

    assert called.call_args.kwargs["idempotency_key"] == derive_idempotency_key(
        _PATIENT_ID, _PRACTITIONER_ID, _STARTS_AT
    )


# --- results -----------------------------------------------------------------


async def test_a_replayed_booking_is_reported_exactly_like_a_new_one() -> None:
    replayed = BookingSuccess(appointment=_appointment(), idempotent_replay=True)
    created = BookingSuccess(appointment=_appointment(), idempotent_replay=False)

    results = []
    for outcome in (created, replayed):
        with patch(_CLIENT + ".book_appointment", new=AsyncMock(return_value=outcome)):
            results.append(
                await _registry().dispatch(
                    "book_appointment",
                    {
                        "practitioner_id": _PRACTITIONER_ID,
                        "starts_at": "2026-08-18T09:00:00",
                    },
                )
            )

    assert results[0] == results[1]
    assert results[0]["status"] == "booked"


@pytest.mark.parametrize("reason", list(BookingFailureReason))
async def test_every_refusal_reason_has_its_own_explanation(
    reason: BookingFailureReason,
) -> None:
    refusal = BookingRefusal(reason=reason, detail="detail for logs")
    with patch(_CLIENT + ".book_appointment", new=AsyncMock(return_value=refusal)):
        result = await _registry().dispatch(
            "book_appointment",
            {"practitioner_id": _PRACTITIONER_ID, "starts_at": "2026-08-18T09:00:00"},
        )

    assert result["status"] == "refused"
    assert result["reason"] == reason.value
    assert result["explanation"]
    # The wire's `detail` is for logs; the patient-facing sentence is this side's.
    assert result["explanation"] != "detail for logs"


def test_the_explanations_are_all_distinct() -> None:
    explanations = list(scheduling_tools._EXPLANATION_BY_REASON.values())

    assert len(set(explanations)) == len(explanations)


async def test_an_exhausted_retry_budget_yields_unavailable() -> None:
    with patch.object(
        scheduling_tools.scheduling,
        "book_appointment",
        new=AsyncMock(
            side_effect=SchedulingUnavailableError("down", outcome_unknown=False)
        ),
    ):
        result = await _registry().dispatch(
            "book_appointment",
            {"practitioner_id": _PRACTITIONER_ID, "starts_at": "2026-08-18T09:00:00"},
        )

    assert result["status"] == "unavailable"
    assert "Nothing was booked" in result["explanation"]


async def test_a_write_of_unknown_outcome_never_claims_nothing_was_booked() -> None:
    """The scheduler may have committed the appointment after we stopped waiting.

    Telling the patient nothing happened is the one guess that leads them to book a
    second appointment they cannot cancel. The status carries that distinction itself,
    rather than leaving it to a reader of the explanation: `unavailable` states that
    nothing happened, and this path cannot state that.
    """
    with patch.object(
        scheduling_tools.scheduling,
        "book_appointment",
        new=AsyncMock(
            side_effect=SchedulingUnavailableError("deadline", outcome_unknown=True)
        ),
    ):
        result = await _registry().dispatch(
            "book_appointment",
            {"practitioner_id": _PRACTITIONER_ID, "starts_at": "2026-08-18T09:00:00"},
        )

    assert result["status"] == "unknown"
    assert "nothing was booked" not in result["explanation"].lower()
    assert "Nothing was booked" not in result["explanation"]
    assert "not known whether" in result["explanation"]


async def test_a_key_mismatch_is_reported_as_unavailable_not_as_a_conflict() -> None:
    """A broken key derivation is this service's defect, not the patient's problem.

    Reporting it as a conflict would invite the model to offer the patient a different
    time for a booking that never failed on availability at all.
    """
    with (
        patch.object(
            scheduling_tools.scheduling,
            "book_appointment",
            new=AsyncMock(side_effect=SchedulingRequestError("key mismatch")),
        ),
        capture_logs() as logs,
    ):
        result = await _registry().dispatch(
            "book_appointment",
            {"practitioner_id": _PRACTITIONER_ID, "starts_at": "2026-08-18T09:00:00"},
        )

    assert result["status"] == "unavailable"
    assert "reason" not in result
    defect = next(e for e in logs if e["event"] == "booking.key_derivation_rejected")
    assert defect["log_level"] == "error"


@pytest.mark.parametrize(
    "tool_name", ["check_availability", "book_appointment", "list_my_appointments"]
)
async def test_a_chat_with_no_patient_yet_reports_unavailable(tool_name: str) -> None:
    arguments = {
        "check_availability": {
            "practitioner_id": _PRACTITIONER_ID,
            "from_date": "2026-08-17",
            "to_date": "2026-08-21",
        },
        "book_appointment": {
            "practitioner_id": _PRACTITIONER_ID,
            "starts_at": "2026-08-18T09:00:00",
        },
        "list_my_appointments": {},
    }[tool_name]

    result = await _registry(_context(patient_id=None)).dispatch(tool_name, arguments)

    assert result["status"] == "unavailable"


async def test_a_practitioner_with_no_bookable_time_is_marked_as_such() -> None:
    practitioners = (
        PractitionerInfo(
            id=_PRACTITIONER_ID,
            full_name="William Osler",
            specialty="General Practice",
            appointment_duration_minutes=60,
            schedule=(),
        ),
        PractitionerInfo(
            id="01OTHER",
            full_name="Ada Ada",
            specialty="Dentistry",
            appointment_duration_minutes=30,
            schedule=(WorkingRangeInfo(Weekday.TUESDAY, "09:00", "17:00"),),
        ),
    )
    with patch(
        _CLIENT + ".list_practitioners", new=AsyncMock(return_value=practitioners)
    ):
        result = await _registry().dispatch("list_practitioners", {})

    assert [p["bookable"] for p in result["practitioners"]] == [False, True]
    assert result["practitioners"][1]["specialty"] == "Dentistry"


async def test_availability_reports_truncation_so_the_model_can_look_further() -> None:
    result = AvailabilityResult(
        available_starts=(_STARTS_AT,),
        appointment_duration_minutes=60,
        truncated=True,
    )
    with patch(_CLIENT + ".check_availability", new=AsyncMock(return_value=result)):
        tool_result = await _registry().dispatch(
            "check_availability",
            {
                "practitioner_id": _PRACTITIONER_ID,
                "from_date": "2026-08-17",
                "to_date": "2026-12-21",
            },
        )

    assert tool_result["truncated"] is True
    assert tool_result["available_starts"] == ["2026-08-18T09:00:00"]


async def _availability_not_found(entity: NotFoundEntity | None) -> dict[str, Any]:
    """Ask for availability against a scheduler that resolved nothing."""
    failure = SchedulingNotFoundError("nope", entity=entity)
    with patch(_CLIENT + ".check_availability", new=AsyncMock(side_effect=failure)):
        return await _registry().dispatch(
            "check_availability",
            {
                "practitioner_id": _PRACTITIONER_ID,
                "from_date": "2026-08-17",
                "to_date": "2026-08-21",
            },
        )


async def test_an_unknown_practitioner_is_reported_as_the_practitioner() -> None:
    result = await _availability_not_found(NotFoundEntity.PRACTITIONER)

    assert result["reason"] == BookingFailureReason.PRACTITIONER_NOT_FOUND.value
    assert "practitioner" in result["explanation"]


async def test_an_unresolved_patient_is_never_blamed_on_the_practitioner() -> None:
    """The same status arrives for either id, so the entity has to decide the answer.

    Blaming the practitioner tells the patient that a real, listed one does not work
    here - and offers to try someone else, which fails identically.
    """
    result = await _availability_not_found(NotFoundEntity.PATIENT)

    assert result["reason"] == BookingFailureReason.PATIENT_NOT_FOUND.value
    assert "practitioner" not in result["explanation"]


async def test_an_unnamed_missing_entity_blames_neither() -> None:
    result = await _availability_not_found(None)

    assert result["status"] == "unavailable"
    assert "practitioner" not in result["explanation"]


async def _listed(listing: AppointmentListing) -> dict[str, Any]:
    with patch(
        _CLIENT + ".list_appointments", new=AsyncMock(return_value=listing)
    ):
        return await _registry().dispatch("list_my_appointments", {})


def _empty() -> AppointmentListing:
    return AppointmentListing(future=(), past=(), past_truncated=False)


async def test_no_appointments_is_two_empty_legs_not_an_error() -> None:
    result = await _listed(_empty())

    assert result == {"future": [], "past": [], "past_truncated": False}
    assert "status" not in result


async def test_a_listed_appointment_carries_its_id_and_status() -> None:
    # The id is what a change has to name; the status is what FR-015 requires wherever
    # a cancelled appointment appears. Neither is ever said to the patient.
    result = await _listed(
        AppointmentListing(future=(_appointment(),), past=(), past_truncated=False)
    )

    listed = result["future"][0]
    assert set(listed) == {
        "id",
        "practitioner_full_name",
        "specialty",
        "starts_at",
        "ends_at",
        "status",
    }
    assert listed["id"] == "01APPOINTMENT000000000000"
    assert listed["status"] == "standing"


# --- list_my_appointments ----------------------------------------------------


async def test_the_two_legs_are_never_merged_into_one_list() -> None:
    past = AppointmentInfo(
        id="01PAST",
        patient_id=_PATIENT_ID,
        patient_full_name="Ada",
        practitioner_id=_PRACTITIONER_ID,
        practitioner_full_name="William Osler",
        practitioner_specialty="General Practice",
        starts_at=datetime(2026, 8, 1, 9, 0),
        ends_at=datetime(2026, 8, 1, 10, 0),
        status=AppointmentStatus.CANCELLED,
    )

    result = await _listed(
        AppointmentListing(
            future=(_appointment(),), past=(past,), past_truncated=False
        )
    )

    assert [a["id"] for a in result["future"]] == ["01APPOINTMENT000000000000"]
    assert [a["id"] for a in result["past"]] == ["01PAST"]
    assert result["past"][0]["status"] == "cancelled"
    assert "appointments" not in result


async def test_a_truncated_past_leg_is_surfaced() -> None:
    result = await _listed(
        AppointmentListing(future=(), past=(_appointment(),), past_truncated=True)
    )

    assert result["past_truncated"] is True


async def test_appointments_are_reported_in_the_order_received() -> None:
    later = AppointmentInfo(
        id="01SECOND",
        patient_id=_PATIENT_ID,
        patient_full_name="Ada",
        practitioner_id=_PRACTITIONER_ID,
        practitioner_full_name="Ada Ada",
        practitioner_specialty="Dentistry",
        starts_at=datetime(2026, 8, 19, 9, 0),
        ends_at=datetime(2026, 8, 19, 9, 30),
        status=AppointmentStatus.STANDING,
    )

    result = await _listed(
        AppointmentListing(
            future=(_appointment(), later), past=(), past_truncated=False
        )
    )

    assert [a["starts_at"] for a in result["future"]] == [
        "2026-08-18T09:00:00",
        "2026-08-19T09:00:00",
    ]
    assert result["future"][1]["specialty"] == "Dentistry"


async def test_both_axes_are_optional_and_default_to_the_narrowest_corner() -> None:
    with patch(
        _CLIENT + ".list_appointments", new=AsyncMock(return_value=_empty())
    ) as called:
        await _registry().dispatch("list_my_appointments", {})

    kwargs = called.call_args.kwargs
    assert kwargs["time_filter"] is TimeFilter.FUTURE
    assert kwargs["status_filter"] is StatusFilter.STANDING


async def test_each_axis_is_passed_through_when_the_model_widens_it() -> None:
    with patch(
        _CLIENT + ".list_appointments", new=AsyncMock(return_value=_empty())
    ) as called:
        await _registry().dispatch(
            "list_my_appointments",
            {"time_filter": "both", "status_filter": "cancelled"},
        )

    kwargs = called.call_args.kwargs
    assert kwargs["time_filter"] is TimeFilter.BOTH
    assert kwargs["status_filter"] is StatusFilter.CANCELLED


async def test_an_axis_value_outside_the_enum_is_rejected() -> None:
    with (
        patch(_CLIENT + ".list_appointments", new=AsyncMock()) as called,
        pytest.raises(ToolArgumentError),
    ):
        await _registry().dispatch("list_my_appointments", {"time_filter": "someday"})

    called.assert_not_awaited()


async def test_listing_appointments_passes_the_ambient_patient_and_clock() -> None:
    with patch(
        _CLIENT + ".list_appointments", new=AsyncMock(return_value=_empty())
    ) as called:
        await _registry().dispatch("list_my_appointments", {})

    kwargs = called.call_args.kwargs
    assert kwargs["patient_id"] == _PATIENT_ID
    assert kwargs["local_now"] == _LOCAL_NOW
    assert kwargs["session_id"] == _SESSION_ID


async def test_an_unresolved_patient_is_not_reported_as_having_no_appointments() -> (
    None
):
    """An empty result would read as "you have nothing booked", which is not known here.

    The scheduler answered that the patient does not resolve; their appointments were
    never looked at.
    """
    failure = SchedulingNotFoundError("nope", entity=NotFoundEntity.PATIENT)
    with patch(_CLIENT + ".list_appointments", new=AsyncMock(side_effect=failure)):
        result = await _registry().dispatch("list_my_appointments", {})

    assert result["status"] == "unavailable"
    assert "future" not in result
    assert "past" not in result


async def test_listing_appointments_when_the_scheduler_is_down_is_unavailable() -> None:
    with patch(
        _CLIENT + ".list_appointments",
        new=AsyncMock(side_effect=SchedulingUnavailableError("down")),
    ):
        result = await _registry().dispatch("list_my_appointments", {})

    assert result["status"] == "unavailable"


# --- argument validation -----------------------------------------------------


@pytest.mark.parametrize(
    ("tool_name", "arguments", "missing"),
    [
        (
            "check_availability",
            {"from_date": "2026-08-18", "to_date": "2026-08-19"},
            "practitioner_id",
        ),
        (
            "check_availability",
            {"practitioner_id": _PRACTITIONER_ID, "to_date": "2026-08-19"},
            "from_date",
        ),
        (
            "check_availability",
            {"practitioner_id": _PRACTITIONER_ID, "from_date": "2026-08-18"},
            "to_date",
        ),
        ("book_appointment", {"starts_at": "2026-08-18T09:00:00"}, "practitioner_id"),
        ("book_appointment", {"practitioner_id": _PRACTITIONER_ID}, "starts_at"),
    ],
)
async def test_a_missing_argument_is_rejected_before_the_scheduler_is_called(
    tool_name: str, arguments: dict[str, Any], missing: str
) -> None:
    with (
        patch(_CLIENT, autospec=True) as client,
        pytest.raises(ToolArgumentError, match=missing),
    ):
        await _registry().dispatch(tool_name, arguments)

    assert client.mock_calls == []


@pytest.mark.parametrize(
    ("tool_name", "arguments", "bad"),
    [
        (
            "check_availability",
            {
                "practitioner_id": _PRACTITIONER_ID,
                "from_date": "next Tuesday",
                "to_date": "2026-08-19",
            },
            "from_date",
        ),
        (
            "book_appointment",
            {"practitioner_id": _PRACTITIONER_ID, "starts_at": "2026-08-18"},
            "starts_at",
        ),
    ],
)
async def test_an_unparsable_argument_is_rejected_before_the_scheduler_is_called(
    tool_name: str, arguments: dict[str, Any], bad: str
) -> None:
    with (
        patch(_CLIENT, autospec=True) as client,
        pytest.raises(ToolArgumentError, match=bad),
    ):
        await _registry().dispatch(tool_name, arguments)

    assert client.mock_calls == []
