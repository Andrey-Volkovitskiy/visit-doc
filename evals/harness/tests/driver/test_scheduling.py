"""Planting, reading and releasing a case's appointments over the scheduler's contract.

The stub is fake: each rpc replays scripted responses and records the requests it saw,
so what the harness sends and how it reads each answer is checked with no scheduler.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any

import grpc
import pytest
from golden_harness.cases import AppointmentRef
from golden_harness.driver.scheduling import (
    CleanupFailedError,
    SchedulingCallError,
    Unplantable,
    plant,
    read_post_state,
    release,
    resolve_roster,
)
from golden_harness.record import AppointmentState, UnplantableSituation
from shared_proto.scheduling.v1 import scheduling_pb2 as pb

_SESSION = "01K5SESSION000000000000000"
_PATIENT = "01K5PATIENT000000000000000"
_OSLER_ID = "01K5OSLER00000000000000000"
_VESALIUS_ID = "01K5VESALIUS00000000000000"
_CLOCK = datetime(2026, 3, 2, 8, 0, 0)


class _RpcError(grpc.aio.AioRpcError):
    """An `AioRpcError` carrying just the status a test wants to provoke."""

    def __init__(self, code: grpc.StatusCode = grpc.StatusCode.UNAVAILABLE) -> None:
        super().__init__(code, grpc.aio.Metadata(), grpc.aio.Metadata(), "boom", None)


class _Method:
    """One rpc: replays `outcomes` in order, or computes one, recording each call."""

    def __init__(self, outcomes: list[Any] | Callable[[Any], Any]) -> None:
        self._outcomes = outcomes
        self.requests: list[Any] = []
        self.kwargs: list[dict[str, Any]] = []

    async def __call__(self, request: Any, **kwargs: Any) -> Any:
        self.requests.append(request)
        self.kwargs.append(kwargs)
        if callable(self._outcomes):
            outcome = self._outcomes(request)
        else:
            outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _Stub:
    """Stands in for `SchedulingStub`, with only the four rpcs the harness calls."""

    def __init__(
        self,
        *,
        practitioners: list[Any] | None = None,
        book: list[Any] | Callable[[Any], Any] | None = None,
        listing: list[Any] | None = None,
        cancel: list[Any] | Callable[[Any], Any] | None = None,
    ) -> None:
        self.ListPractitioners = _Method(
            practitioners if practitioners is not None else [_roster()]
        )
        self.BookAppointment = _Method(book if book is not None else _booked)
        self.ListAppointments = _Method(listing if listing is not None else [])
        self.CancelAppointment = _Method(cancel if cancel is not None else _cancelled)


def _roster(*names: tuple[str, str]) -> pb.ListPractitionersResponse:
    listed = names or (("William Osler", _OSLER_ID), ("Andreas Vesalius", _VESALIUS_ID))
    return pb.ListPractitionersResponse(
        practitioners=[
            pb.Practitioner(id=practitioner_id, full_name=name)
            for name, practitioner_id in listed
        ]
    )


def _wire(
    appointment_id: str,
    starts_at: str,
    *,
    practitioner: tuple[str, str] = ("William Osler", _OSLER_ID),
    status: int = pb.APPOINTMENT_STATUS_STANDING,
) -> pb.Appointment:
    name, practitioner_id = practitioner
    return pb.Appointment(
        id=appointment_id,
        patient_id=_PATIENT,
        practitioner_id=practitioner_id,
        practitioner_full_name=name,
        starts_at=starts_at,
        ends_at=starts_at,
        status=status,  # type: ignore[arg-type]
    )


def _booked(request: pb.BookAppointmentRequest) -> pb.BookAppointmentResponse:
    names = {_OSLER_ID: "William Osler", _VESALIUS_ID: "Andreas Vesalius"}
    return pb.BookAppointmentResponse(
        appointment=_wire(
            f"APPT-{request.starts_at}",
            request.starts_at,
            practitioner=(names[request.practitioner_id], request.practitioner_id),
        )
    )


def _cancelled(request: pb.CancelAppointmentRequest) -> pb.ChangeAppointmentResponse:
    return pb.ChangeAppointmentResponse(
        appointment=_wire(
            request.appointment_id,
            request.expected_starts_at,
            status=pb.APPOINTMENT_STATUS_CANCELLED,
        )
    )


def _ref(practitioner: str, day: str, time: str) -> AppointmentRef:
    return AppointmentRef(practitioner=practitioner, day=day, time=time)


def _state(starts_at: str, status: str = "standing") -> AppointmentState:
    return AppointmentState.model_validate(
        {
            "practitioner_full_name": "William Osler",
            "starts_at": starts_at,
            "status": status,
        }
    )


# --- the roster ---------------------------------------------------------------------


async def test_the_roster_maps_each_full_name_to_its_practitioner_id() -> None:
    stub = _Stub()

    roster = await resolve_roster(stub, _SESSION)

    assert roster == {"William Osler": _OSLER_ID, "Andreas Vesalius": _VESALIUS_ID}
    (request,) = stub.ListPractitioners.requests
    assert request.session_id == _SESSION


async def test_a_roster_that_cannot_be_read_raises() -> None:
    stub = _Stub(practitioners=[_RpcError()])

    with pytest.raises(SchedulingCallError):
        await resolve_roster(stub, _SESSION)


# --- planting -----------------------------------------------------------------------


async def test_planting_books_each_given_at_the_clock_date_plus_its_day() -> None:
    stub = _Stub()
    given = [
        _ref("William Osler", "+1d", "10:00"),
        _ref("Andreas Vesalius", "+5d", "13:00"),
    ]

    planted = await plant(
        stub, session_id=_SESSION, patient_id=_PATIENT, given=given, clock=_CLOCK
    )

    assert planted == [
        _state("2026-03-03T10:00:00"),
        AppointmentState.model_validate(
            {
                "practitioner_full_name": "Andreas Vesalius",
                "starts_at": "2026-03-07T13:00:00",
                "status": "standing",
            }
        ),
    ]
    first, second = stub.BookAppointment.requests
    assert (first.session_id, first.patient_id) == (_SESSION, _PATIENT)
    assert (first.practitioner_id, first.starts_at) == (
        _OSLER_ID,
        "2026-03-03T10:00:00",
    )
    assert (second.practitioner_id, second.starts_at) == (
        _VESALIUS_ID,
        "2026-03-07T13:00:00",
    )
    assert {r.local_now for r in (first, second)} == {"2026-03-02T08:00:00"}
    assert first.idempotency_key != ""
    assert first.idempotency_key != second.idempotency_key


async def test_a_negative_day_counts_back_from_the_clock_date() -> None:
    stub = _Stub()

    await plant(
        stub,
        session_id=_SESSION,
        patient_id=_PATIENT,
        given=[_ref("William Osler", "-1d", "10:00")],
        clock=_CLOCK,
    )

    assert stub.BookAppointment.requests[0].starts_at == "2026-03-01T10:00:00"


async def test_a_name_not_on_the_roster_is_unplantable_and_books_nothing() -> None:
    stub = _Stub(practitioners=[_roster(("William Osler", _OSLER_ID))])
    given = [
        _ref("William Osler", "+1d", "10:00"),
        _ref("Andreas Vesalius", "+1d", "11:00"),
    ]

    planted = await plant(
        stub, session_id=_SESSION, patient_id=_PATIENT, given=given, clock=_CLOCK
    )

    assert isinstance(planted, Unplantable)
    assert "Andreas Vesalius" in planted.detail
    assert stub.BookAppointment.requests == []


async def test_a_roster_that_cannot_be_read_is_unplantable() -> None:
    stub = _Stub(practitioners=[_RpcError()])

    planted = await plant(
        stub,
        session_id=_SESSION,
        patient_id=_PATIENT,
        given=[_ref("William Osler", "+1d", "10:00")],
        clock=_CLOCK,
    )

    assert isinstance(planted, Unplantable)
    assert stub.BookAppointment.requests == []


async def test_a_typed_booking_refusal_is_unplantable_and_names_the_reason() -> None:
    refused = pb.BookAppointmentResponse(
        failure=pb.BookingFailure(
            reason=pb.BOOKING_FAILURE_REASON_PRACTITIONER_BUSY, detail="taken"
        )
    )
    stub = _Stub(book=[refused])

    planted = await plant(
        stub,
        session_id=_SESSION,
        patient_id=_PATIENT,
        given=[_ref("William Osler", "+1d", "10:00")],
        clock=_CLOCK,
    )

    assert isinstance(planted, Unplantable)
    assert "PRACTITIONER_BUSY" in planted.detail
    assert "William Osler" in planted.detail


async def test_a_booking_rpc_error_is_unplantable() -> None:
    stub = _Stub(book=[_RpcError(grpc.StatusCode.DEADLINE_EXCEEDED)])

    planted = await plant(
        stub,
        session_id=_SESSION,
        patient_id=_PATIENT,
        given=[_ref("William Osler", "+1d", "10:00")],
        clock=_CLOCK,
    )

    assert isinstance(planted, Unplantable)
    assert "DEADLINE_EXCEEDED" in planted.detail


async def test_each_way_planting_stops_is_named_by_its_situation() -> None:
    given = [_ref("William Osler", "+1d", "10:00")]
    unreadable = pb.BookAppointmentResponse(
        appointment=_wire("APPT-1", "2026-03-03T10:00:00", status=0)
    )
    stubs = {
        UnplantableSituation.NOT_ON_ROSTER: _Stub(practitioners=[_roster(("X", "1"))]),
        UnplantableSituation.ROSTER_UNREADABLE: _Stub(practitioners=[_RpcError()]),
        UnplantableSituation.BOOKING_UNANSWERED: _Stub(book=[_RpcError()]),
        UnplantableSituation.BOOKING_UNREADABLE: _Stub(book=[unreadable]),
    }

    for situation, stub in stubs.items():
        planted = await plant(
            stub, session_id=_SESSION, patient_id=_PATIENT, given=given, clock=_CLOCK
        )

        assert isinstance(planted, Unplantable), situation
        assert planted.situation is situation
        assert (planted.failure_reason, planted.scheduler_message) == (None, None)


async def test_a_booking_refusal_carries_the_schedulers_reason_and_message() -> None:
    refused = pb.BookAppointmentResponse(
        failure=pb.BookingFailure(
            reason=pb.BOOKING_FAILURE_REASON_PRACTITIONER_BUSY, detail="taken"
        )
    )
    stub = _Stub(book=[refused])

    planted = await plant(
        stub,
        session_id=_SESSION,
        patient_id=_PATIENT,
        given=[_ref("William Osler", "+1d", "10:00")],
        clock=_CLOCK,
    )

    assert isinstance(planted, Unplantable)
    assert planted.situation is UnplantableSituation.BOOKING_REFUSED
    assert planted.failure_reason == "BOOKING_FAILURE_REASON_PRACTITIONER_BUSY"
    assert planted.scheduler_message == "taken"


async def test_a_booking_refusal_with_no_message_carries_none() -> None:
    refused = pb.BookAppointmentResponse(
        failure=pb.BookingFailure(reason=pb.BOOKING_FAILURE_REASON_IN_PAST)
    )
    stub = _Stub(book=[refused])

    planted = await plant(
        stub,
        session_id=_SESSION,
        patient_id=_PATIENT,
        given=[_ref("William Osler", "+1d", "10:00")],
        clock=_CLOCK,
    )

    assert isinstance(planted, Unplantable)
    assert planted.failure_reason == "BOOKING_FAILURE_REASON_IN_PAST"
    assert planted.scheduler_message is None


async def test_every_call_carries_a_deadline() -> None:
    stub = _Stub(listing=[pb.ListAppointmentsResponse()])

    await plant(
        stub,
        session_id=_SESSION,
        patient_id=_PATIENT,
        given=[_ref("William Osler", "+1d", "10:00")],
        clock=_CLOCK,
    )
    await read_post_state(stub, session_id=_SESSION, patient_id=_PATIENT, clock=_CLOCK)

    for method in (stub.ListPractitioners, stub.BookAppointment, stub.ListAppointments):
        assert all(kwargs.get("timeout", 0) > 0 for kwargs in method.kwargs)


# --- the post-state -----------------------------------------------------------------


async def test_the_post_state_asks_for_every_time_and_every_status() -> None:
    listing = pb.ListAppointmentsResponse(
        future=[
            _wire("A1", "2026-03-03T10:00:00", status=pb.APPOINTMENT_STATUS_CANCELLED),
            _wire(
                "A2",
                "2026-03-09T09:00:00",
                practitioner=("Andreas Vesalius", _VESALIUS_ID),
            ),
        ],
        past=[_wire("A0", "2026-02-27T10:00:00")],
    )
    stub = _Stub(listing=[listing])

    after = await read_post_state(
        stub, session_id=_SESSION, patient_id=_PATIENT, clock=_CLOCK
    )

    (request,) = stub.ListAppointments.requests
    assert (request.session_id, request.patient_id) == (_SESSION, _PATIENT)
    assert request.time_filter == pb.TIME_FILTER_BOTH
    assert request.status_filter == pb.STATUS_FILTER_BOTH
    assert request.local_now == "2026-03-02T08:00:00"
    assert after == [
        _state("2026-03-03T10:00:00", "cancelled"),
        AppointmentState.model_validate(
            {
                "practitioner_full_name": "Andreas Vesalius",
                "starts_at": "2026-03-09T09:00:00",
                "status": "standing",
            }
        ),
        _state("2026-02-27T10:00:00"),
    ]


async def test_a_post_state_that_cannot_be_read_raises() -> None:
    stub = _Stub(listing=[_RpcError()])

    with pytest.raises(SchedulingCallError):
        await read_post_state(
            stub, session_id=_SESSION, patient_id=_PATIENT, clock=_CLOCK
        )


async def test_an_appointment_with_an_unknown_status_is_not_read_as_standing() -> None:
    listing = pb.ListAppointmentsResponse(
        future=[
            _wire("A1", "2026-03-03T10:00:00", status=pb.APPOINTMENT_STATUS_UNSPECIFIED)
        ]
    )
    stub = _Stub(listing=[listing])

    with pytest.raises(SchedulingCallError, match="A1"):
        await read_post_state(
            stub, session_id=_SESSION, patient_id=_PATIENT, clock=_CLOCK
        )


async def test_a_post_state_whose_past_leg_was_cut_short_raises() -> None:
    stub = _Stub(listing=[pb.ListAppointmentsResponse(past_truncated=True)])

    with pytest.raises(SchedulingCallError, match="truncated"):
        await read_post_state(
            stub, session_id=_SESSION, patient_id=_PATIENT, clock=_CLOCK
        )


# --- releasing ----------------------------------------------------------------------


def _standing_listing(*appointments: pb.Appointment) -> pb.ListAppointmentsResponse:
    return pb.ListAppointmentsResponse(future=list(appointments))


async def test_release_cancels_every_standing_appointment_from_its_own_listing() -> (
    None
):
    stub = _Stub(
        listing=[
            _standing_listing(
                _wire("A1", "2026-03-03T10:00:00"),
                _wire(
                    "A2",
                    "2026-03-04T11:00:00",
                    practitioner=("Andreas Vesalius", _VESALIUS_ID),
                ),
            )
        ]
    )

    cancelled = await release(
        stub, session_id=_SESSION, patient_id=_PATIENT, clock=_CLOCK
    )

    (listing,) = stub.ListAppointments.requests
    assert listing.time_filter == pb.TIME_FILTER_BOTH
    assert listing.status_filter == pb.STATUS_FILTER_STANDING
    assert listing.local_now == "2026-03-02T08:00:00"
    first, second = stub.CancelAppointment.requests
    assert (first.session_id, first.patient_id, first.appointment_id) == (
        _SESSION,
        _PATIENT,
        "A1",
    )
    assert (first.expected_starts_at, first.expected_practitioner_id) == (
        "2026-03-03T10:00:00",
        _OSLER_ID,
    )
    assert (second.appointment_id, second.expected_practitioner_id) == (
        "A2",
        _VESALIUS_ID,
    )
    assert {r.local_now for r in (first, second)} == {"2026-03-02T08:00:00"}
    assert cancelled == [
        _state("2026-03-03T10:00:00", "cancelled"),
        _state("2026-03-04T11:00:00", "cancelled"),
    ]


async def test_release_with_nothing_standing_cancels_nothing() -> None:
    stub = _Stub(listing=[pb.ListAppointmentsResponse()])

    assert (
        await release(stub, session_id=_SESSION, patient_id=_PATIENT, clock=_CLOCK)
        == []
    )
    assert stub.CancelAppointment.requests == []


async def test_a_no_change_is_already_cancelled_and_is_not_reported_as_cancelled() -> (
    None
):
    already = pb.ChangeAppointmentResponse(
        no_change=pb.NoChange(
            appointment=_wire(
                "A1", "2026-03-03T10:00:00", status=pb.APPOINTMENT_STATUS_CANCELLED
            )
        )
    )
    stub = _Stub(
        listing=[
            _standing_listing(
                _wire("A1", "2026-03-03T10:00:00"), _wire("A2", "2026-03-04T10:00:00")
            )
        ],
        cancel=lambda request: (
            already if request.appointment_id == "A1" else _cancelled(request)
        ),
    )

    cancelled = await release(
        stub, session_id=_SESSION, patient_id=_PATIENT, clock=_CLOCK
    )

    assert cancelled == [_state("2026-03-04T10:00:00", "cancelled")]


async def test_a_refused_cancellation_raises_naming_the_appointment_and_what_went() -> (
    None
):
    refused = pb.ChangeAppointmentResponse(
        failure=pb.ChangeFailure(
            reason=pb.CHANGE_FAILURE_REASON_STALE_CONFIRMATION, detail="moved"
        )
    )
    stub = _Stub(
        listing=[
            _standing_listing(
                _wire("A1", "2026-03-03T10:00:00"), _wire("A2", "2026-03-04T10:00:00")
            )
        ],
        cancel=lambda request: (
            _cancelled(request) if request.appointment_id == "A1" else refused
        ),
    )

    with pytest.raises(CleanupFailedError, match="A2") as raised:
        await release(stub, session_id=_SESSION, patient_id=_PATIENT, clock=_CLOCK)

    assert "STALE_CONFIRMATION" in str(raised.value)
    assert raised.value.cancelled == [_state("2026-03-03T10:00:00", "cancelled")]


async def test_a_cancellation_rpc_error_raises_naming_the_appointment() -> None:
    stub = _Stub(
        listing=[_standing_listing(_wire("A1", "2026-03-03T10:00:00"))],
        cancel=[_RpcError(grpc.StatusCode.DEADLINE_EXCEEDED)],
    )

    with pytest.raises(CleanupFailedError, match="A1") as raised:
        await release(stub, session_id=_SESSION, patient_id=_PATIENT, clock=_CLOCK)

    assert raised.value.cancelled == []


async def test_a_cancellation_answered_with_no_result_raises() -> None:
    stub = _Stub(
        listing=[_standing_listing(_wire("A1", "2026-03-03T10:00:00"))],
        cancel=[pb.ChangeAppointmentResponse()],
    )

    with pytest.raises(CleanupFailedError, match="A1"):
        await release(stub, session_id=_SESSION, patient_id=_PATIENT, clock=_CLOCK)


async def test_a_standing_listing_that_cannot_be_read_raises_cleanup_failed() -> None:
    stub = _Stub(listing=[_RpcError()])

    with pytest.raises(CleanupFailedError):
        await release(stub, session_id=_SESSION, patient_id=_PATIENT, clock=_CLOCK)

    assert stub.CancelAppointment.requests == []
