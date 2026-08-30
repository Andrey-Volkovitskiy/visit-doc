"""Tests for `clients/scheduling.py`: the deadline, the retry budget, and the split
between an evaluated refusal and an unreachable service.

The stub is faked at this module's own boundary, so the retry rules and the failure
taxonomy are exercised without a running scheduler.
"""

from collections.abc import Iterator
from datetime import date, datetime
from typing import Any, cast
from unittest.mock import patch

import grpc
import pytest
import structlog
from chat.clients import scheduling
from chat.core.config import Settings
from shared_models.localtime import format_local_datetime
from shared_models.scheduling import BookingFailureReason, RenameFailureReason
from shared_proto.scheduling.v1 import scheduling_pb2 as pb
from structlog.testing import capture_logs

# Captured at import, before any fixture runs, so these are the real functions.
# `conftest.py`'s autouse boundary fake replaces them on this very module - which is
# right for tests that go through the API, and wrong here, where the client itself is
# what is under test. The fake stub below is the boundary these tests replace instead.
_REAL_CLIENT_FUNCTIONS = {
    name: getattr(scheduling, name)
    for name in (
        "ensure_session_provisioned",
        "delete_patient_for_chat",
        "rename_patient",
    )
}


@pytest.fixture(autouse=True)
def _client_functions_are_not_faked() -> Iterator[None]:
    """Restore the real client functions for every test in this module."""
    with patch.multiple(scheduling, **_REAL_CLIENT_FUNCTIONS):
        yield


_SESSION_ID = "01SESSION0000000000000000"
_PATIENT_ID = "01PATIENT0000000000000000"
_PRACTITIONER_ID = "01PRACTITIONER0000000000"
_LOCAL_NOW = datetime(2026, 8, 14, 9, 0)
# Every test patches `_stub`, so the channel argument is never dereferenced - this
# stands in for it and makes that explicit at each call site.
_CHANNEL = cast(grpc.aio.Channel, None)


def _settings(**overrides: Any) -> Settings:
    return Settings(
        DATABASE_URL="postgresql+asyncpg://u:p@localhost:5432/db",
        QDRANT_URL="http://localhost:6333",
        ANTHROPIC_API_KEY="unused",
        VOYAGE_API_KEY="unused",
        **overrides,
    )


class _RpcError(grpc.aio.AioRpcError):
    """An `AioRpcError` carrying just the status a test wants to provoke."""

    def __init__(self, code: grpc.StatusCode, details: str = "boom") -> None:
        super().__init__(code, grpc.aio.Metadata(), grpc.aio.Metadata(), details, None)


class _FakeMethod:
    """One stub method: replays `outcomes` in order, recording every call it saw."""

    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, request: Any, **kwargs: Any) -> Any:
        self.calls.append({"request": request, **kwargs})
        outcome = self._outcomes.pop(0) if self._outcomes else self._outcomes
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _FakeStub:
    """Stands in for `SchedulingStub`, exposing only the method under test."""

    def __init__(self, method_name: str, method: _FakeMethod) -> None:
        setattr(self, method_name, method)


def _patched(method_name: str, outcomes: list[Any]) -> tuple[Any, _FakeMethod]:
    """Return a patch context for `_stub` and the fake method it will hand back."""
    method = _FakeMethod(outcomes)
    return patch.object(
        scheduling, "_stub", return_value=_FakeStub(method_name, method)
    ), method


def _book_request_kwargs() -> dict[str, Any]:
    return {
        "session_id": _SESSION_ID,
        "patient_id": _PATIENT_ID,
        "practitioner_id": _PRACTITIONER_ID,
        "starts_at": datetime(2026, 8, 18, 9, 0),
        "local_now": _LOCAL_NOW,
        "idempotency_key": "derived-key",
    }


def _booked_response() -> pb.BookAppointmentResponse:
    return pb.BookAppointmentResponse(
        appointment=pb.Appointment(
            id="01APPOINTMENT000000000000",
            patient_id=_PATIENT_ID,
            patient_full_name="Ada",
            practitioner_id=_PRACTITIONER_ID,
            practitioner_full_name="William Osler",
            practitioner_specialty="General Practice",
            starts_at="2026-08-18T09:00:00",
            ends_at="2026-08-18T10:00:00",
            status=pb.APPOINTMENT_STATUS_STANDING,
        ),
        idempotent_replay=False,
    )


async def test_every_call_carries_the_configured_deadline() -> None:
    ctx, method = _patched("ListPractitioners", [pb.ListPractitionersResponse()])
    with ctx:
        await scheduling.list_practitioners(
            _CHANNEL, _settings(SCHEDULING_TIMEOUT_SECONDS=2.0), session_id=_SESSION_ID
        )

    assert method.calls[0]["timeout"] == 2.0


async def test_every_call_carries_the_bound_turn_id_as_metadata() -> None:
    ctx, method = _patched("ListPractitioners", [pb.ListPractitionersResponse()])
    structlog.contextvars.clear_contextvars()
    with structlog.contextvars.bound_contextvars(turn_id="01TURN"), ctx:
        await scheduling.list_practitioners(
            _CHANNEL, _settings(), session_id=_SESSION_ID
        )

    assert ("x-turn-id", "01TURN") in method.calls[0]["metadata"]


async def test_a_call_with_no_bound_turn_id_sends_no_metadata() -> None:
    ctx, method = _patched("ListPractitioners", [pb.ListPractitionersResponse()])
    structlog.contextvars.clear_contextvars()
    with ctx:
        await scheduling.list_practitioners(
            _CHANNEL, _settings(), session_id=_SESSION_ID
        )

    assert method.calls[0]["metadata"] == ()


@pytest.mark.parametrize(
    "status", [grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED]
)
async def test_a_retryable_status_is_retried_once_then_gives_up(
    status: grpc.StatusCode,
) -> None:
    ctx, method = _patched("ListPractitioners", [_RpcError(status), _RpcError(status)])
    with ctx, pytest.raises(scheduling.SchedulingUnavailableError):
        await scheduling.list_practitioners(
            _CHANNEL, _settings(SCHEDULING_MAX_ATTEMPTS=2), session_id=_SESSION_ID
        )

    assert len(method.calls) == 2


@pytest.mark.parametrize(
    "status", [grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED]
)
async def test_a_retry_that_succeeds_returns_normally(status: grpc.StatusCode) -> None:
    ctx, method = _patched(
        "ListPractitioners",
        [
            _RpcError(status),
            pb.ListPractitionersResponse(
                practitioners=[
                    pb.Practitioner(
                        id=_PRACTITIONER_ID,
                        full_name="William Osler",
                        specialty="General Practice",
                        appointment_duration_minutes=60,
                    )
                ]
            ),
        ],
    )
    with ctx:
        result = await scheduling.list_practitioners(
            _CHANNEL, _settings(), session_id=_SESSION_ID
        )

    assert len(method.calls) == 2
    assert [p.full_name for p in result] == ["William Osler"]


@pytest.mark.parametrize(
    "status",
    [
        grpc.StatusCode.INTERNAL,
        grpc.StatusCode.PERMISSION_DENIED,
    ],
)
async def test_a_non_retryable_status_is_not_retried(status: grpc.StatusCode) -> None:
    ctx, method = _patched("ListPractitioners", [_RpcError(status)])
    with ctx, pytest.raises(scheduling.SchedulingUnavailableError):
        await scheduling.list_practitioners(
            _CHANNEL, _settings(), session_id=_SESSION_ID
        )

    assert len(method.calls) == 1


async def test_a_server_side_status_reports_the_outcome_as_unknown() -> None:
    ctx, _ = _patched("BookAppointment", [_RpcError(grpc.StatusCode.INTERNAL)])
    with ctx, pytest.raises(scheduling.SchedulingUnavailableError) as caught:
        await scheduling.book_appointment(
            _CHANNEL, _settings(), **_book_request_kwargs()
        )

    assert caught.value.outcome_unknown is True


async def test_an_unreachable_server_reports_the_outcome_as_known() -> None:
    ctx, _ = _patched(
        "BookAppointment",
        [
            _RpcError(grpc.StatusCode.UNAVAILABLE),
            _RpcError(grpc.StatusCode.UNAVAILABLE),
        ],
    )
    with ctx, pytest.raises(scheduling.SchedulingUnavailableError) as caught:
        await scheduling.book_appointment(
            _CHANNEL, _settings(), **_book_request_kwargs()
        )

    assert caught.value.outcome_unknown is False


async def test_an_exhausted_deadline_reports_the_outcome_as_unknown() -> None:
    ctx, _ = _patched(
        "BookAppointment",
        [
            _RpcError(grpc.StatusCode.DEADLINE_EXCEEDED),
            _RpcError(grpc.StatusCode.DEADLINE_EXCEEDED),
        ],
    )
    with ctx, pytest.raises(scheduling.SchedulingUnavailableError) as caught:
        await scheduling.book_appointment(
            _CHANNEL, _settings(), **_book_request_kwargs()
        )

    # The deadline is the caller's, not the server's: it may have committed the
    # appointment after this side stopped waiting for the answer.
    assert caught.value.outcome_unknown is True


async def test_not_found_raises_its_own_error_and_is_not_retried() -> None:
    ctx, method = _patched(
        "CheckAvailability", [_RpcError(grpc.StatusCode.NOT_FOUND, "practitioner")]
    )
    with ctx, pytest.raises(scheduling.SchedulingNotFoundError):
        await scheduling.check_availability(
            _CHANNEL,
            _settings(),
            session_id=_SESSION_ID,
            practitioner_id=_PRACTITIONER_ID,
            patient_id=_PATIENT_ID,
            from_date=date(2026, 8, 17),
            to_date=date(2026, 8, 21),
            local_now=_LOCAL_NOW,
        )

    assert len(method.calls) == 1


async def test_invalid_argument_raises_a_request_error_and_is_not_retried() -> None:
    ctx, method = _patched(
        "BookAppointment", [_RpcError(grpc.StatusCode.INVALID_ARGUMENT, "key mismatch")]
    )
    with ctx, pytest.raises(scheduling.SchedulingRequestError):
        await scheduling.book_appointment(
            _CHANNEL, _settings(), **_book_request_kwargs()
        )

    assert len(method.calls) == 1


async def test_exhausting_the_budget_logs_unavailable_and_the_critical_dependency() -> (
    None
):
    ctx, _ = _patched(
        "ListPractitioners",
        [_RpcError(grpc.StatusCode.UNAVAILABLE)] * 2,
    )
    with (
        capture_logs() as logs,
        ctx,
        pytest.raises(scheduling.SchedulingUnavailableError),
    ):
        await scheduling.list_practitioners(
            _CHANNEL, _settings(), session_id=_SESSION_ID
        )

    events = [entry["event"] for entry in logs]
    assert events.count("scheduling.call") == 2
    assert "scheduling.unavailable" in events
    critical = next(e for e in logs if e["event"] == "critical.dependency_unreachable")
    assert critical["dependency"] == "scheduler"


async def test_a_successful_call_logs_one_scheduling_call_line() -> None:
    ctx, _ = _patched("ListPractitioners", [pb.ListPractitionersResponse()])
    with capture_logs() as logs, ctx:
        await scheduling.list_practitioners(
            _CHANNEL, _settings(), session_id=_SESSION_ID
        )

    calls = [entry for entry in logs if entry["event"] == "scheduling.call"]
    assert len(calls) == 1
    assert calls[0]["status"] == "OK"


@pytest.mark.parametrize(
    ("proto_reason", "expected"),
    [
        (pb.BOOKING_FAILURE_REASON_PRACTITIONER_BUSY, "practitioner_busy"),
        (pb.BOOKING_FAILURE_REASON_PATIENT_BUSY, "patient_busy"),
        (pb.BOOKING_FAILURE_REASON_OUTSIDE_SCHEDULE, "outside_schedule"),
        (pb.BOOKING_FAILURE_REASON_OFF_GRID, "off_grid"),
        (pb.BOOKING_FAILURE_REASON_IN_PAST, "in_past"),
        (pb.BOOKING_FAILURE_REASON_BEYOND_HORIZON, "beyond_horizon"),
        (pb.BOOKING_FAILURE_REASON_PRACTITIONER_NOT_FOUND, "practitioner_not_found"),
        (pb.BOOKING_FAILURE_REASON_PATIENT_NOT_FOUND, "patient_not_found"),
    ],
)
async def test_every_failure_reason_maps_to_its_domain_value(
    proto_reason: int, expected: str
) -> None:
    ctx, _ = _patched(
        "BookAppointment",
        [
            pb.BookAppointmentResponse(
                failure=pb.BookingFailure(reason=proto_reason, detail="because")
            )
        ],
    )
    with ctx:
        result = await scheduling.book_appointment(
            _CHANNEL, _settings(), **_book_request_kwargs()
        )

    assert isinstance(result, scheduling.BookingRefusal)
    assert result.reason == BookingFailureReason(expected)


async def test_a_refusal_is_a_result_not_an_exception() -> None:
    ctx, _ = _patched(
        "BookAppointment",
        [
            pb.BookAppointmentResponse(
                failure=pb.BookingFailure(
                    reason=pb.BOOKING_FAILURE_REASON_PRACTITIONER_BUSY, detail="taken"
                )
            )
        ],
    )
    with ctx:
        result = await scheduling.book_appointment(
            _CHANNEL, _settings(), **_book_request_kwargs()
        )

    assert isinstance(result, scheduling.BookingRefusal)


async def test_a_booking_success_carries_parsed_naive_local_times() -> None:
    ctx, _ = _patched("BookAppointment", [_booked_response()])
    with ctx:
        result = await scheduling.book_appointment(
            _CHANNEL, _settings(), **_book_request_kwargs()
        )

    assert isinstance(result, scheduling.BookingSuccess)
    assert result.appointment.starts_at == datetime(2026, 8, 18, 9, 0)
    assert result.appointment.starts_at.tzinfo is None
    assert result.appointment.ends_at == datetime(2026, 8, 18, 10, 0)


async def test_the_booking_request_carries_offset_free_local_times() -> None:
    ctx, method = _patched("BookAppointment", [_booked_response()])
    with ctx:
        await scheduling.book_appointment(
            _CHANNEL, _settings(), **_book_request_kwargs()
        )

    request = method.calls[0]["request"]
    assert request.starts_at == "2026-08-18T09:00:00"
    assert request.local_now == "2026-08-14T09:00:00"
    assert request.idempotency_key == "derived-key"


async def test_check_availability_always_sends_the_patient_id() -> None:
    ctx, method = _patched(
        "CheckAvailability",
        [pb.CheckAvailabilityResponse(appointment_duration_minutes=60)],
    )
    with ctx:
        await scheduling.check_availability(
            _CHANNEL,
            _settings(),
            session_id=_SESSION_ID,
            practitioner_id=_PRACTITIONER_ID,
            patient_id=_PATIENT_ID,
            from_date=date(2026, 8, 17),
            to_date=date(2026, 8, 21),
            local_now=_LOCAL_NOW,
        )

    request = method.calls[0]["request"]
    assert request.patient_id == _PATIENT_ID
    assert request.from_date == "2026-08-17"
    assert request.to_date == "2026-08-21"


async def test_a_practitioner_with_no_schedule_is_not_bookable() -> None:
    ctx, _ = _patched(
        "ListPractitioners",
        [
            pb.ListPractitionersResponse(
                practitioners=[
                    pb.Practitioner(
                        id=_PRACTITIONER_ID,
                        full_name="William Osler",
                        specialty="General Practice",
                        appointment_duration_minutes=60,
                    )
                ]
            )
        ],
    )
    with ctx:
        practitioners = await scheduling.list_practitioners(
            _CHANNEL, _settings(), session_id=_SESSION_ID
        )

    assert practitioners[0].bookable is False


async def test_a_practitioner_whose_ranges_are_too_short_is_not_bookable() -> None:
    ctx, _ = _patched(
        "ListPractitioners",
        [
            pb.ListPractitionersResponse(
                practitioners=[
                    pb.Practitioner(
                        id=_PRACTITIONER_ID,
                        full_name="William Osler",
                        specialty="General Practice",
                        appointment_duration_minutes=60,
                        schedule=[
                            pb.WorkingRange(
                                weekday=1, start_time="09:00", end_time="09:30"
                            )
                        ],
                    )
                ]
            )
        ],
    )
    with ctx:
        practitioners = await scheduling.list_practitioners(
            _CHANNEL, _settings(), session_id=_SESSION_ID
        )

    assert practitioners[0].bookable is False


async def test_a_practitioner_with_a_range_fitting_one_slot_is_bookable() -> None:
    ctx, _ = _patched(
        "ListPractitioners",
        [
            pb.ListPractitionersResponse(
                practitioners=[
                    pb.Practitioner(
                        id=_PRACTITIONER_ID,
                        full_name="William Osler",
                        specialty="General Practice",
                        appointment_duration_minutes=60,
                        schedule=[
                            pb.WorkingRange(
                                weekday=1, start_time="09:00", end_time="17:00"
                            )
                        ],
                    )
                ]
            )
        ],
    )
    with ctx:
        practitioners = await scheduling.list_practitioners(
            _CHANNEL, _settings(), session_id=_SESSION_ID
        )

    assert practitioners[0].bookable is True


# --- renaming a patient --------------------------------------------------------


def _rename_kwargs(full_name: str = "Grace") -> dict[str, Any]:
    return {
        "session_id": _SESSION_ID,
        "patient_id": _PATIENT_ID,
        "full_name": full_name,
    }


async def test_a_rename_returns_the_patient_the_scheduler_stored() -> None:
    ctx, method = _patched(
        "RenamePatient",
        [
            pb.RenamePatientResponse(
                patient=pb.Patient(
                    id=_PATIENT_ID,
                    chat_id="01CHAT00000000000000000000",
                    full_name="Grace B.",
                )
            )
        ],
    )
    with ctx:
        result = await scheduling.rename_patient(
            _CHANNEL, _settings(), **_rename_kwargs()
        )

    assert isinstance(result, scheduling.PatientInfo)
    # What comes back, not what was asked for - the scheduler owns the value.
    assert result.full_name == "Grace B."
    assert method.calls[0]["request"].full_name == "Grace"


@pytest.mark.parametrize(
    ("proto_reason", "expected"),
    [
        (pb.RENAME_FAILURE_REASON_NAME_TAKEN, "name_taken"),
        (pb.RENAME_FAILURE_REASON_PATIENT_NOT_FOUND, "patient_not_found"),
    ],
)
async def test_each_refusal_reason_arrives_as_its_domain_member(
    proto_reason: int, expected: str
) -> None:
    ctx, _ = _patched(
        "RenamePatient",
        [
            pb.RenamePatientResponse(
                failure=pb.RenameFailure(reason=proto_reason, detail="d")
            )
        ],
    )
    with ctx:
        result = await scheduling.rename_patient(
            _CHANNEL, _settings(), **_rename_kwargs()
        )

    assert isinstance(result, scheduling.RenameRefusal)
    assert result.reason == RenameFailureReason(expected)


async def test_an_unnamed_refusal_reason_is_reported_as_a_defect() -> None:
    # A scheduler deployed ahead of this service, or an unset field. The refusal is
    # trustworthy but cannot be explained, so it is never guessed at.
    ctx, _ = _patched(
        "RenamePatient",
        [pb.RenamePatientResponse(failure=pb.RenameFailure(reason=99, detail="d"))],
    )
    with ctx, pytest.raises(scheduling.SchedulingRequestError):
        await scheduling.rename_patient(_CHANNEL, _settings(), **_rename_kwargs())


async def test_a_rename_that_times_out_reports_an_unknown_outcome() -> None:
    # Our deadline expiring says nothing about whether the server applied the rename.
    ctx, _ = _patched(
        "RenamePatient",
        [
            _RpcError(grpc.StatusCode.DEADLINE_EXCEEDED),
            _RpcError(grpc.StatusCode.DEADLINE_EXCEEDED),
        ],
    )
    with ctx, pytest.raises(scheduling.SchedulingUnavailableError) as exc_info:
        await scheduling.rename_patient(
            _CHANNEL,
            _settings(SCHEDULING_MAX_ATTEMPTS=2, SCHEDULING_RETRY_BACKOFF_SECONDS=0.0),
            **_rename_kwargs(),
        )

    assert exc_info.value.outcome_unknown is True


async def test_a_rename_that_never_reached_the_server_reports_a_known_outcome() -> None:
    ctx, _ = _patched(
        "RenamePatient",
        [
            _RpcError(grpc.StatusCode.UNAVAILABLE),
            _RpcError(grpc.StatusCode.UNAVAILABLE),
        ],
    )
    with ctx, pytest.raises(scheduling.SchedulingUnavailableError) as exc_info:
        await scheduling.rename_patient(
            _CHANNEL,
            _settings(SCHEDULING_MAX_ATTEMPTS=2, SCHEDULING_RETRY_BACKOFF_SECONDS=0.0),
            **_rename_kwargs(),
        )

    assert exc_info.value.outcome_unknown is False


async def test_a_booked_appointment_carries_its_status_off_the_wire() -> None:
    from shared_models.scheduling import AppointmentStatus

    response = _booked_response()
    response.appointment.status = pb.APPOINTMENT_STATUS_STANDING
    ctx, _ = _patched("BookAppointment", [response])
    with ctx:
        result = await scheduling.book_appointment(
            _CHANNEL, _settings(), **_book_request_kwargs()
        )

    assert isinstance(result, scheduling.BookingSuccess)
    assert result.appointment.status is AppointmentStatus.STANDING


async def test_a_cancelled_appointment_reads_back_as_cancelled() -> None:
    from shared_models.scheduling import AppointmentStatus

    response = _booked_response()
    response.appointment.status = pb.APPOINTMENT_STATUS_CANCELLED
    ctx, _ = _patched("BookAppointment", [response])
    with ctx:
        result = await scheduling.book_appointment(
            _CHANNEL, _settings(), **_book_request_kwargs()
        )

    assert isinstance(result, scheduling.BookingSuccess)
    assert result.appointment.status is AppointmentStatus.CANCELLED


async def test_an_unspecified_status_on_the_wire_is_not_read_as_standing() -> None:
    # A scheduler that forgot to set the field, or one deployed behind this build.
    # Defaulting to standing would present a cancelled appointment as a live one.
    response = _booked_response()
    response.appointment.ClearField("status")
    ctx, _ = _patched("BookAppointment", [response])
    with ctx, pytest.raises(scheduling.SchedulingRequestError):
        await scheduling.book_appointment(
            _CHANNEL, _settings(), **_book_request_kwargs()
        )


# --- changes -----------------------------------------------------------------


def _change_kwargs() -> dict[str, Any]:
    return {
        "session_id": _SESSION_ID,
        "patient_id": _PATIENT_ID,
        "appointment_id": "01APPOINTMENT000000000000",
        "expected_starts_at": datetime(2026, 8, 18, 9, 0),
        "expected_practitioner_id": _PRACTITIONER_ID,
        "local_now": _LOCAL_NOW,
    }


def _wire_appointment(
    starts_at: str = "2026-08-18T09:00:00",
    status: int = pb.APPOINTMENT_STATUS_STANDING,
) -> pb.Appointment:
    return pb.Appointment(
        id="01APPOINTMENT000000000000",
        patient_id=_PATIENT_ID,
        patient_full_name="Ada",
        practitioner_id=_PRACTITIONER_ID,
        practitioner_full_name="William Osler",
        practitioner_specialty="General Practice",
        starts_at=starts_at,
        ends_at="2026-08-18T10:00:00",
        status=status,
    )


async def test_a_completed_cancellation_maps_onto_change_applied() -> None:
    response = pb.ChangeAppointmentResponse(
        appointment=_wire_appointment(status=pb.APPOINTMENT_STATUS_CANCELLED)
    )
    ctx, _ = _patched("CancelAppointment", [response])
    with ctx:
        result = await scheduling.cancel_appointment(
            _CHANNEL, _settings(), **_change_kwargs()
        )

    assert isinstance(result, scheduling.ChangeApplied)
    assert result.appointment.status.value == "cancelled"


async def test_a_no_change_cancellation_maps_onto_change_no_op() -> None:
    # Never a refusal: the appointment is in the state that was asked for.
    response = pb.ChangeAppointmentResponse(
        no_change=pb.NoChange(
            appointment=_wire_appointment(status=pb.APPOINTMENT_STATUS_CANCELLED)
        )
    )
    ctx, _ = _patched("CancelAppointment", [response])
    with ctx:
        result = await scheduling.cancel_appointment(
            _CHANNEL, _settings(), **_change_kwargs()
        )

    assert isinstance(result, scheduling.ChangeNoOp)
    assert result.appointment.id == "01APPOINTMENT000000000000"


async def test_a_refused_cancellation_maps_onto_change_refusal() -> None:
    from shared_models.scheduling import ChangeFailureReason

    response = pb.ChangeAppointmentResponse(
        failure=pb.ChangeFailure(
            reason=pb.CHANGE_FAILURE_REASON_STALE_CONFIRMATION, detail="stale"
        )
    )
    ctx, _ = _patched("CancelAppointment", [response])
    with ctx:
        result = await scheduling.cancel_appointment(
            _CHANNEL, _settings(), **_change_kwargs()
        )

    assert isinstance(result, scheduling.ChangeRefusal)
    assert result.reason is ChangeFailureReason.STALE_CONFIRMATION


async def test_a_change_reason_this_build_cannot_name_is_a_request_error() -> None:
    response = pb.ChangeAppointmentResponse(
        failure=pb.ChangeFailure(reason=pb.CHANGE_FAILURE_REASON_UNSPECIFIED)
    )
    ctx, _ = _patched("CancelAppointment", [response])
    with ctx, pytest.raises(scheduling.SchedulingRequestError):
        await scheduling.cancel_appointment(
            _CHANNEL, _settings(), **_change_kwargs()
        )


async def test_the_cancel_request_carries_the_guard_fields_verbatim() -> None:
    # The guard is what the assistant stated to the patient. Re-deriving it here would
    # make it match the appointment's current state by definition, disabling it.
    ctx, method = _patched(
        "CancelAppointment",
        [pb.ChangeAppointmentResponse(appointment=_wire_appointment())],
    )
    with ctx:
        await scheduling.cancel_appointment(
            _CHANNEL, _settings(), **_change_kwargs()
        )

    request = method.calls[0]["request"]
    assert request.expected_starts_at == "2026-08-18T09:00:00"
    assert request.expected_practitioner_id == _PRACTITIONER_ID
    assert request.appointment_id == "01APPOINTMENT000000000000"
    assert request.session_id == _SESSION_ID
    assert request.local_now == format_local_datetime(_LOCAL_NOW)


async def test_a_cancellation_follows_the_same_two_attempt_budget() -> None:
    ctx, method = _patched(
        "CancelAppointment",
        [
            _RpcError(grpc.StatusCode.UNAVAILABLE),
            _RpcError(grpc.StatusCode.UNAVAILABLE),
        ],
    )
    with ctx, pytest.raises(scheduling.SchedulingUnavailableError):
        await scheduling.cancel_appointment(
            _CHANNEL,
            _settings(SCHEDULING_MAX_ATTEMPTS=2, SCHEDULING_RETRY_BACKOFF_SECONDS=0.0),
            **_change_kwargs(),
        )

    assert len(method.calls) == 2


async def test_a_cancellation_whose_deadline_expired_reports_an_unknown_outcome() -> (
    None
):
    # A deadline is ours, not the server's: it may well have cancelled the appointment
    # after we stopped waiting.
    ctx, _ = _patched(
        "CancelAppointment",
        [
            _RpcError(grpc.StatusCode.DEADLINE_EXCEEDED),
            _RpcError(grpc.StatusCode.DEADLINE_EXCEEDED),
        ],
    )
    with ctx, pytest.raises(scheduling.SchedulingUnavailableError) as caught:
        await scheduling.cancel_appointment(
            _CHANNEL,
            _settings(SCHEDULING_MAX_ATTEMPTS=2, SCHEDULING_RETRY_BACKOFF_SECONDS=0.0),
            **_change_kwargs(),
        )

    assert caught.value.outcome_unknown is True


# --- listing -----------------------------------------------------------------


def _list_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "session_id": _SESSION_ID,
        "patient_id": _PATIENT_ID,
        "local_now": _LOCAL_NOW,
    }
    kwargs.update(overrides)
    return kwargs


async def test_a_listing_returns_both_legs_and_the_truncation_flag() -> None:
    response = pb.ListAppointmentsResponse(
        future=[_wire_appointment()],
        past=[
            _wire_appointment(
                starts_at="2026-08-01T09:00:00",
                status=pb.APPOINTMENT_STATUS_CANCELLED,
            )
        ],
        past_truncated=True,
    )
    ctx, _ = _patched("ListAppointments", [response])
    with ctx:
        result = await scheduling.list_appointments(
            _CHANNEL, _settings(), **_list_kwargs()
        )

    assert isinstance(result, scheduling.AppointmentListing)
    assert [a.starts_at for a in result.future] == [datetime(2026, 8, 18, 9, 0)]
    assert [a.status.value for a in result.past] == ["cancelled"]
    assert result.past_truncated is True


async def test_a_listing_defaults_to_the_narrowest_corner_on_the_wire() -> None:
    from shared_models.scheduling import StatusFilter, TimeFilter

    ctx, method = _patched("ListAppointments", [pb.ListAppointmentsResponse()])
    with ctx:
        await scheduling.list_appointments(_CHANNEL, _settings(), **_list_kwargs())

    request = method.calls[0]["request"]
    assert request.time_filter == pb.TIME_FILTER_FUTURE
    assert request.status_filter == pb.STATUS_FILTER_STANDING
    assert TimeFilter.FUTURE.value == "future"
    assert StatusFilter.STANDING.value == "standing"


async def test_a_listing_carries_the_filters_it_was_given() -> None:
    from shared_models.scheduling import StatusFilter, TimeFilter

    ctx, method = _patched("ListAppointments", [pb.ListAppointmentsResponse()])
    with ctx:
        await scheduling.list_appointments(
            _CHANNEL,
            _settings(),
            **_list_kwargs(
                time_filter=TimeFilter.BOTH, status_filter=StatusFilter.CANCELLED
            ),
        )

    request = method.calls[0]["request"]
    assert request.time_filter == pb.TIME_FILTER_BOTH
    assert request.status_filter == pb.STATUS_FILTER_CANCELLED


async def test_an_unresolvable_patient_raises_rather_than_returning_empty_legs() -> (
    None
):
    ctx, _ = _patched(
        "ListAppointments", [_RpcError(grpc.StatusCode.NOT_FOUND, "patient_not_found")]
    )
    with ctx, pytest.raises(scheduling.SchedulingNotFoundError):
        await scheduling.list_appointments(_CHANNEL, _settings(), **_list_kwargs())


def _reschedule_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "session_id": _SESSION_ID,
        "patient_id": _PATIENT_ID,
        "appointment_id": "01APPOINTMENT000000000000",
        "new_starts_at": datetime(2026, 8, 18, 10, 0),
        "new_practitioner_id": None,
        "expected_starts_at": datetime(2026, 8, 18, 9, 0),
        "expected_practitioner_id": _PRACTITIONER_ID,
        "local_now": _LOCAL_NOW,
    }
    kwargs.update(overrides)
    return kwargs


async def test_a_completed_move_maps_onto_change_applied_with_its_previous_state() -> (
    None
):
    response = pb.ChangeAppointmentResponse(
        appointment=_wire_appointment(starts_at="2026-08-18T10:00:00"),
        previous_starts_at="2026-08-18T09:00:00",
        previous_practitioner_id=_PRACTITIONER_ID,
    )
    ctx, _ = _patched("RescheduleAppointment", [response])
    with ctx:
        result = await scheduling.reschedule_appointment(
            _CHANNEL, _settings(), **_reschedule_kwargs()
        )

    assert isinstance(result, scheduling.ChangeApplied)
    assert result.appointment.starts_at == datetime(2026, 8, 18, 10, 0)
    assert result.previous_starts_at == datetime(2026, 8, 18, 9, 0)


async def test_a_move_that_transitioned_nothing_maps_onto_change_no_op() -> None:
    response = pb.ChangeAppointmentResponse(
        no_change=pb.NoChange(appointment=_wire_appointment())
    )
    ctx, _ = _patched("RescheduleAppointment", [response])
    with ctx:
        result = await scheduling.reschedule_appointment(
            _CHANNEL, _settings(), **_reschedule_kwargs()
        )

    assert isinstance(result, scheduling.ChangeNoOp)


async def test_a_refused_move_maps_onto_change_refusal_with_its_reason() -> None:
    from shared_models.scheduling import ChangeFailureReason

    response = pb.ChangeAppointmentResponse(
        failure=pb.ChangeFailure(
            reason=pb.CHANGE_FAILURE_REASON_OFF_GRID, detail="off_grid"
        )
    )
    ctx, _ = _patched("RescheduleAppointment", [response])
    with ctx:
        result = await scheduling.reschedule_appointment(
            _CHANNEL, _settings(), **_reschedule_kwargs()
        )

    assert isinstance(result, scheduling.ChangeRefusal)
    assert result.reason is ChangeFailureReason.OFF_GRID


async def test_the_reschedule_request_carries_the_guard_and_the_destination() -> None:
    ctx, method = _patched(
        "RescheduleAppointment",
        [pb.ChangeAppointmentResponse(appointment=_wire_appointment())],
    )
    with ctx:
        await scheduling.reschedule_appointment(
            _CHANNEL, _settings(), **_reschedule_kwargs()
        )

    request = method.calls[0]["request"]
    assert request.new_starts_at == "2026-08-18T10:00:00"
    assert request.expected_starts_at == "2026-08-18T09:00:00"
    assert request.expected_practitioner_id == _PRACTITIONER_ID
    assert request.local_now == format_local_datetime(_LOCAL_NOW)


async def test_keeping_the_practitioner_sends_an_empty_new_practitioner_id() -> None:
    # Empty is the contract's "keep the one it has" - not a missing required field.
    ctx, method = _patched(
        "RescheduleAppointment",
        [pb.ChangeAppointmentResponse(appointment=_wire_appointment())],
    )
    with ctx:
        await scheduling.reschedule_appointment(
            _CHANNEL, _settings(), **_reschedule_kwargs(new_practitioner_id=None)
        )

    assert method.calls[0]["request"].new_practitioner_id == ""


async def test_a_move_follows_the_same_two_attempt_budget() -> None:
    ctx, method = _patched(
        "RescheduleAppointment",
        [
            _RpcError(grpc.StatusCode.UNAVAILABLE),
            _RpcError(grpc.StatusCode.UNAVAILABLE),
        ],
    )
    with ctx, pytest.raises(scheduling.SchedulingUnavailableError):
        await scheduling.reschedule_appointment(
            _CHANNEL,
            _settings(SCHEDULING_MAX_ATTEMPTS=2, SCHEDULING_RETRY_BACKOFF_SECONDS=0.0),
            **_reschedule_kwargs(),
        )

    assert len(method.calls) == 2


async def test_a_move_whose_deadline_expired_reports_an_unknown_outcome() -> None:
    ctx, _ = _patched(
        "RescheduleAppointment",
        [
            _RpcError(grpc.StatusCode.DEADLINE_EXCEEDED),
            _RpcError(grpc.StatusCode.DEADLINE_EXCEEDED),
        ],
    )
    with ctx, pytest.raises(scheduling.SchedulingUnavailableError) as caught:
        await scheduling.reschedule_appointment(
            _CHANNEL,
            _settings(SCHEDULING_MAX_ATTEMPTS=2, SCHEDULING_RETRY_BACKOFF_SECONDS=0.0),
            **_reschedule_kwargs(),
        )

    assert caught.value.outcome_unknown is True


async def test_check_availability_carries_the_excluded_appointment_id() -> None:
    ctx, method = _patched(
        "CheckAvailability", [pb.CheckAvailabilityResponse(available_starts=[])]
    )
    with ctx:
        await scheduling.check_availability(
            _CHANNEL,
            _settings(),
            session_id=_SESSION_ID,
            practitioner_id=_PRACTITIONER_ID,
            patient_id=_PATIENT_ID,
            from_date=date(2026, 8, 18),
            to_date=date(2026, 8, 18),
            local_now=_LOCAL_NOW,
            excluded_appointment_id="01APPOINTMENT000000000000",
        )

    assert (
        method.calls[0]["request"].excluded_appointment_id
        == "01APPOINTMENT000000000000"
    )


async def test_check_availability_omits_the_exclusion_when_none_is_given() -> None:
    ctx, method = _patched(
        "CheckAvailability", [pb.CheckAvailabilityResponse(available_starts=[])]
    )
    with ctx:
        await scheduling.check_availability(
            _CHANNEL,
            _settings(),
            session_id=_SESSION_ID,
            practitioner_id=_PRACTITIONER_ID,
            patient_id=_PATIENT_ID,
            from_date=date(2026, 8, 18),
            to_date=date(2026, 8, 18),
            local_now=_LOCAL_NOW,
        )

    assert method.calls[0]["request"].excluded_appointment_id == ""
