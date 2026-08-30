"""Tests for the change tool handlers: four result shapes, and one total reason table.

The gRPC client is faked at the handler module's own boundary, so what is exercised is
the adapter - which arguments reach the client, and how each domain outcome becomes
something the model can read without inventing a cause.
"""

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from chat.agent.tools.registry import ToolContext, ToolRegistry
from chat.agent.tools.scheduling_tools import SCHEDULING_TOOLS
from chat.clients.scheduling import (
    AppointmentInfo,
    ChangeApplied,
    ChangeNoOp,
    ChangeRefusal,
    SchedulingUnavailableError,
)
from chat.core.config import Settings
from shared_models.scheduling import AppointmentStatus, ChangeFailureReason

_SESSION_ID = "01SESSION0000000000000000"
_PATIENT_ID = "01PATIENT0000000000000000"
_PRACTITIONER_ID = "01PRACTITIONER0000000000"
_APPOINTMENT_ID = "01APPOINTMENT000000000000"
_LOCAL_NOW = datetime(2026, 8, 17, 8, 0)
_STARTS_AT = datetime(2026, 8, 18, 9, 0)
_CLIENT = "chat.agent.tools.scheduling_tools.scheduling"

_CANCEL_ARGUMENTS = {
    "appointment_id": _APPOINTMENT_ID,
    "expected_starts_at": "2026-08-18T09:00:00",
    "expected_practitioner_id": _PRACTITIONER_ID,
}


def _settings() -> Settings:
    return Settings(
        DATABASE_URL="postgresql+asyncpg://u:p@localhost:5432/db",
        QDRANT_URL="http://localhost:6333",
        ANTHROPIC_API_KEY="unused",
        VOYAGE_API_KEY="unused",
    )


def _registry() -> ToolRegistry:
    return ToolRegistry(
        SCHEDULING_TOOLS,
        ToolContext(
            channel=MagicMock(),
            settings=_settings(),
            session_id=_SESSION_ID,
            patient_id=_PATIENT_ID,
            local_now=_LOCAL_NOW,
        ),
    )


def _appointment(
    status: AppointmentStatus = AppointmentStatus.CANCELLED,
    starts_at: datetime = _STARTS_AT,
) -> AppointmentInfo:
    return AppointmentInfo(
        id=_APPOINTMENT_ID,
        patient_id=_PATIENT_ID,
        patient_full_name="Ada",
        practitioner_id=_PRACTITIONER_ID,
        practitioner_full_name="William Osler",
        practitioner_specialty="General Practice",
        starts_at=starts_at,
        ends_at=datetime(2026, 8, 18, 10, 0),
        status=status,
    )


async def _cancel(outcome: Any, **argument_overrides: str) -> dict[str, Any]:
    arguments = dict(_CANCEL_ARGUMENTS)
    arguments.update(argument_overrides)
    side: dict[str, Any] = (
        {"side_effect": outcome}
        if isinstance(outcome, BaseException)
        else {"return_value": outcome}
    )
    with patch(_CLIENT + ".cancel_appointment", new=AsyncMock(**side)):
        return await _registry().dispatch("cancel_appointment", arguments)


# --- the four result shapes --------------------------------------------------


async def test_a_completed_cancellation_is_reported_as_changed() -> None:
    result = await _cancel(
        ChangeApplied(
            appointment=_appointment(),
            previous_starts_at=_STARTS_AT,
            previous_practitioner_full_name="William Osler",
        )
    )

    assert result["status"] == "changed"
    assert result["change"] == "cancelled"
    assert result["appointment"]["starts_at"] == "2026-08-18T09:00:00"
    assert result["appointment"]["status"] == "cancelled"


async def test_an_already_cancelled_appointment_is_unchanged_never_refused() -> None:
    # FR-017: the appointment is in the state that was asked for. Reporting it as a
    # failure would have the assistant tell a patient their cancellation failed while
    # the appointment is, in fact, cancelled.
    result = await _cancel(ChangeNoOp(appointment=_appointment()))

    assert result["status"] == "unchanged"
    assert result["explanation"]
    assert "reason" not in result


async def test_an_appointment_that_never_existed_is_refused_not_unchanged() -> None:
    # FR-018: distinguishable from already-cancelled, which is the whole point of
    # having both.
    result = await _cancel(
        ChangeRefusal(reason=ChangeFailureReason.APPOINTMENT_NOT_FOUND, detail="gone")
    )

    assert result["status"] == "refused"
    assert result["reason"] == "appointment_not_found"


async def test_a_lost_write_is_reported_as_unknown() -> None:
    result = await _cancel(
        SchedulingUnavailableError("timed out", outcome_unknown=True)
    )

    assert result["status"] == "unknown"


async def test_a_write_that_never_reached_the_server_is_unavailable() -> None:
    # Every attempt failed to reach the scheduler, so nothing was changed and that is
    # actually known - the one case where saying so is not a guess.
    result = await _cancel(SchedulingUnavailableError("refused", outcome_unknown=False))

    assert result["status"] == "unavailable"


async def test_unknown_is_a_distinct_status_from_unavailable() -> None:
    # research #13: 005 had both meanings but separated them only in prose, so every
    # consumer that reads `status` saw one value.
    unknown = await _cancel(
        SchedulingUnavailableError("timed out", outcome_unknown=True)
    )
    unavailable = await _cancel(
        SchedulingUnavailableError("refused", outcome_unknown=False)
    )

    assert unknown["status"] != unavailable["status"]


async def test_the_unknown_explanation_never_says_nothing_happened() -> None:
    # FR-023: the words "nothing was changed" are not available on this path.
    result = await _cancel(
        SchedulingUnavailableError("timed out", outcome_unknown=True)
    )

    explanation = result["explanation"].lower()
    assert "not known" in explanation or "could not confirm" in explanation
    assert "nothing was" not in explanation


# --- the guard and the ambient arguments -------------------------------------


async def test_the_guard_fields_are_passed_through_verbatim() -> None:
    with patch(
        _CLIENT + ".cancel_appointment",
        new=AsyncMock(return_value=ChangeNoOp(appointment=_appointment())),
    ) as called:
        await _registry().dispatch("cancel_appointment", dict(_CANCEL_ARGUMENTS))

    kwargs = called.call_args.kwargs
    assert kwargs["expected_starts_at"] == _STARTS_AT
    assert kwargs["expected_practitioner_id"] == _PRACTITIONER_ID
    assert kwargs["appointment_id"] == _APPOINTMENT_ID


async def test_the_ambient_session_patient_and_clock_are_bound_not_supplied() -> None:
    with patch(
        _CLIENT + ".cancel_appointment",
        new=AsyncMock(return_value=ChangeNoOp(appointment=_appointment())),
    ) as called:
        await _registry().dispatch("cancel_appointment", dict(_CANCEL_ARGUMENTS))

    kwargs = called.call_args.kwargs
    assert kwargs["session_id"] == _SESSION_ID
    assert kwargs["patient_id"] == _PATIENT_ID
    assert kwargs["local_now"] == _LOCAL_NOW


def test_the_cancel_schema_is_closed_and_names_only_its_three_arguments() -> None:
    tool = next(t for t in SCHEDULING_TOOLS if t.name == "cancel_appointment")

    assert tool.input_schema["additionalProperties"] is False
    assert set(tool.input_schema["properties"]) == {
        "appointment_id",
        "expected_starts_at",
        "expected_practitioner_id",
    }
    assert set(tool.input_schema["required"]) == {
        "appointment_id",
        "expected_starts_at",
        "expected_practitioner_id",
    }


def test_cancelling_is_declared_a_write() -> None:
    # What lets the loop tell "this failed and nothing happened" from "this failed and
    # its effect is unknown", without hardcoding a tool name.
    tool = next(t for t in SCHEDULING_TOOLS if t.name == "cancel_appointment")

    assert tool.writes is True
    assert tool.requires_patient is True


@pytest.mark.parametrize(
    "missing",
    ["appointment_id", "expected_starts_at", "expected_practitioner_id"],
)
async def test_a_missing_required_argument_is_rejected(missing: str) -> None:
    from chat.agent.tools.registry import ToolArgumentError

    arguments = {k: v for k, v in _CANCEL_ARGUMENTS.items() if k != missing}
    with (
        patch(_CLIENT + ".cancel_appointment", new=AsyncMock()),
        pytest.raises(ToolArgumentError),
    ):
        await _registry().dispatch("cancel_appointment", arguments)


async def test_an_unparseable_expected_start_is_rejected_before_anything_happens() -> (
    None
):
    from chat.agent.tools.registry import ToolArgumentError

    with (
        patch(_CLIENT + ".cancel_appointment", new=AsyncMock()) as called,
        pytest.raises(ToolArgumentError),
    ):
        await _registry().dispatch(
            "cancel_appointment",
            {**_CANCEL_ARGUMENTS, "expected_starts_at": "next Tuesday"},
        )

    called.assert_not_awaited()


# --- the explanation table ---------------------------------------------------


@pytest.mark.parametrize("reason", list(ChangeFailureReason))
async def test_every_change_reason_has_its_own_explanation(
    reason: ChangeFailureReason,
) -> None:
    # The set is closed and the scheduler picks exactly one per attempt, so this
    # mapping must be total - iterated rather than listed, so a thirteenth reason
    # fails here instead of raising a KeyError mid-turn.
    result = await _cancel(ChangeRefusal(reason=reason, detail=reason.value))

    assert result["status"] == "refused"
    assert result["reason"] == reason.value
    assert result["explanation"]


def test_the_explanation_table_covers_the_whole_enum() -> None:
    from chat.agent.tools.scheduling_tools import CHANGE_EXPLANATION_BY_REASON

    assert set(CHANGE_EXPLANATION_BY_REASON) == set(ChangeFailureReason)


async def test_no_result_ever_names_a_tool_or_an_internal_id_to_the_patient() -> None:
    # The ids the model holds are for calling tools with. The explanation is what
    # reaches the patient, and it must carry neither.
    for reason in ChangeFailureReason:
        result = await _cancel(ChangeRefusal(reason=reason, detail=reason.value))
        explanation = result["explanation"]
        assert _APPOINTMENT_ID not in explanation
        assert _PRACTITIONER_ID not in explanation
        assert "cancel_appointment" not in explanation


# --- reschedule_appointment --------------------------------------------------

_RESCHEDULE_ARGUMENTS = {
    "appointment_id": _APPOINTMENT_ID,
    "new_starts_at": "2026-08-18T10:00:00",
    "expected_starts_at": "2026-08-18T09:00:00",
    "expected_practitioner_id": _PRACTITIONER_ID,
}
_NEW_STARTS_AT = datetime(2026, 8, 18, 10, 0)


async def _move(outcome: Any, **argument_overrides: str) -> dict[str, Any]:
    arguments = dict(_RESCHEDULE_ARGUMENTS)
    arguments.update(argument_overrides)
    side: dict[str, Any] = (
        {"side_effect": outcome}
        if isinstance(outcome, BaseException)
        else {"return_value": outcome}
    )
    with patch(_CLIENT + ".reschedule_appointment", new=AsyncMock(**side)):
        return await _registry().dispatch("reschedule_appointment", arguments)


async def test_a_completed_move_is_reported_as_changed_rescheduled() -> None:
    result = await _move(
        ChangeApplied(
            appointment=_appointment(
                status=AppointmentStatus.STANDING, starts_at=_NEW_STARTS_AT
            ),
            previous_starts_at=_STARTS_AT,
            previous_practitioner_full_name="William Osler",
        )
    )

    assert result["status"] == "changed"
    assert result["change"] == "rescheduled"
    assert result["appointment"]["starts_at"] == "2026-08-18T10:00:00"
    assert result["previous_starts_at"] == "2026-08-18T09:00:00"


async def test_a_move_that_transitioned_nothing_is_unchanged() -> None:
    result = await _move(
        ChangeNoOp(appointment=_appointment(status=AppointmentStatus.STANDING))
    )

    assert result["status"] == "unchanged"
    assert "reason" not in result


async def test_a_refused_move_carries_its_reason_and_explanation() -> None:
    result = await _move(
        ChangeRefusal(reason=ChangeFailureReason.OFF_GRID, detail="off_grid")
    )

    assert result["status"] == "refused"
    assert result["reason"] == "off_grid"
    assert result["explanation"]


async def test_a_lost_move_is_reported_as_unknown() -> None:
    result = await _move(
        SchedulingUnavailableError("timed out", outcome_unknown=True)
    )

    assert result["status"] == "unknown"


@pytest.mark.parametrize(
    "reason",
    [
        ChangeFailureReason.PRACTITIONER_BUSY,
        ChangeFailureReason.PATIENT_BUSY,
        ChangeFailureReason.OUTSIDE_SCHEDULE,
        ChangeFailureReason.OFF_GRID,
        ChangeFailureReason.IN_PAST,
        ChangeFailureReason.BEYOND_HORIZON,
    ],
)
async def test_each_placement_reason_has_a_distinct_explanation(
    reason: ChangeFailureReason,
) -> None:
    # The six a move can be refused by that a cancellation cannot: each has to be
    # explainable on its own, because the model is required to offer alternatives for
    # exactly these and for no others.
    result = await _move(ChangeRefusal(reason=reason, detail=reason.value))

    assert result["reason"] == reason.value
    assert result["explanation"]


def test_the_placement_reasons_have_six_different_explanations() -> None:
    from chat.agent.tools.scheduling_tools import CHANGE_EXPLANATION_BY_REASON

    placement = {
        ChangeFailureReason.PRACTITIONER_BUSY,
        ChangeFailureReason.PATIENT_BUSY,
        ChangeFailureReason.OUTSIDE_SCHEDULE,
        ChangeFailureReason.OFF_GRID,
        ChangeFailureReason.IN_PAST,
        ChangeFailureReason.BEYOND_HORIZON,
    }
    explanations = {CHANGE_EXPLANATION_BY_REASON[r] for r in placement}

    assert len(explanations) == len(placement)


async def test_the_move_guard_and_destination_are_passed_through() -> None:
    with patch(
        _CLIENT + ".reschedule_appointment",
        new=AsyncMock(
            return_value=ChangeNoOp(
                appointment=_appointment(status=AppointmentStatus.STANDING)
            )
        ),
    ) as called:
        await _registry().dispatch(
            "reschedule_appointment", dict(_RESCHEDULE_ARGUMENTS)
        )

    kwargs = called.call_args.kwargs
    assert kwargs["new_starts_at"] == _NEW_STARTS_AT
    assert kwargs["expected_starts_at"] == _STARTS_AT
    assert kwargs["expected_practitioner_id"] == _PRACTITIONER_ID
    assert kwargs["session_id"] == _SESSION_ID
    assert kwargs["local_now"] == _LOCAL_NOW


def test_the_reschedule_schema_is_closed_and_names_only_its_own_arguments() -> None:
    tool = next(t for t in SCHEDULING_TOOLS if t.name == "reschedule_appointment")

    assert tool.input_schema["additionalProperties"] is False
    assert set(tool.input_schema["properties"]) == {
        "appointment_id",
        "new_starts_at",
        "new_practitioner_id",
        "expected_starts_at",
        "expected_practitioner_id",
    }
    # The practitioner is optional; everything else is required.
    assert set(tool.input_schema["required"]) == {
        "appointment_id",
        "new_starts_at",
        "expected_starts_at",
        "expected_practitioner_id",
    }
    assert tool.writes is True
    assert tool.requires_patient is True


async def test_an_unparseable_new_start_is_rejected_before_anything_happens() -> None:
    from chat.agent.tools.registry import ToolArgumentError

    with (
        patch(_CLIENT + ".reschedule_appointment", new=AsyncMock()) as called,
        pytest.raises(ToolArgumentError),
    ):
        await _registry().dispatch(
            "reschedule_appointment",
            {**_RESCHEDULE_ARGUMENTS, "new_starts_at": "next Tuesday"},
        )

    called.assert_not_awaited()
