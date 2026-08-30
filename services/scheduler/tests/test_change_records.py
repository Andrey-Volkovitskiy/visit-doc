"""The change record contract, pinned field-for-field.

Every completed change has to be recoverable from the logs alone, every non-completing
outcome has to be recorded as the thing it is, and the count of change records has to
equal the count of appointments actually altered. Those are three different properties,
and each fails in a different direction - a missing field, a record for something that
did not happen, or two records for one transition - so each is asserted separately here
rather than left to the per-story tests that happen to touch the same events.
"""

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any

import grpc
import pytest
import pytest_asyncio
from scheduler.grpc.interceptors import LoggingInterceptor
from scheduler.grpc.servicer import SchedulingServicer
from scheduler.repositories import appointment_repository
from shared_models.scheduling import AppointmentStatus, ChangeFailureReason
from shared_proto.metadata import TURN_ID_METADATA_KEY
from shared_proto.scheduling.v1 import scheduling_pb2 as pb
from shared_proto.scheduling.v1 import scheduling_pb2_grpc
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs

from .conftest import make_appointment, new_id, seed_patient, seed_practitioner
from .test_servicer_changes import _cancel_request, _reschedule_request
from .test_servicer_changes import _seed as _seed_over_the_wire

_TUESDAY_9AM = datetime(2026, 8, 18, 9, 0)
_TUESDAY_10AM = datetime(2026, 8, 18, 10, 0)
_LOCAL_NOW = datetime(2026, 8, 17, 8, 0)
_HORIZON_DAYS = 90

# Exactly the fields `contracts/log-events.md` declares for each event - no more and no
# fewer. Written out here rather than derived from the emitting code, so a field quietly
# added or dropped fails against the contract instead of agreeing with itself.
_EXPECTED_FIELDS = {
    "appointment.rescheduled": {
        "appointment_id",
        "old_starts_at",
        "new_starts_at",
        "old_practitioner_id",
        "new_practitioner_id",
    },
    "appointment.cancelled": {"appointment_id", "old_starts_at", "practitioner_id"},
    "appointment.unchanged": {"appointment_id", "operation", "starts_at"},
    "change.refused": {"appointment_id", "operation", "reason"},
    "change.key_released": {"appointment_id", "idempotency_key"},
}

# structlog's capture adds these to every entry; they are not part of the payload.
_CAPTURE_KEYS = {"event", "log_level"}


@pytest_asyncio.fixture
async def stub() -> AsyncIterator[scheduling_pb2_grpc.SchedulingStub]:
    """Serve the real servicer, so the interceptor's turn-id binding is exercised."""
    server = grpc.aio.server(interceptors=[LoggingInterceptor()])
    scheduling_pb2_grpc.add_SchedulingServicer_to_server(  # type: ignore[no-untyped-call]
        SchedulingServicer(), server
    )
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
        yield scheduling_pb2_grpc.SchedulingStub(channel)  # type: ignore[no-untyped-call]
    await server.stop(0)


def _payload(log: dict[str, Any]) -> set[str]:
    """Return the fields one captured entry actually carries."""
    return set(log) - _CAPTURE_KEYS


def _find(logs: list[dict[str, Any]], event: str) -> dict[str, Any]:
    return next(log for log in logs if log["event"] == event)


class _Booked:
    def __init__(
        self,
        session_id: str,
        patient_id: str,
        practitioner_id: str,
        appointment_id: str,
        idempotency_key: str,
    ) -> None:
        self.session_id = session_id
        self.patient_id = patient_id
        self.practitioner_id = practitioner_id
        self.appointment_id = appointment_id
        self.idempotency_key = idempotency_key


async def _seed(
    session: AsyncSession, *, status: AppointmentStatus = AppointmentStatus.STANDING
) -> _Booked:
    session_id = new_id()
    practitioner = await seed_practitioner(session, session_id)
    patient = await seed_patient(session, session_id)
    appointment = make_appointment(
        session_id,
        patient.id,
        practitioner.id,
        _TUESDAY_9AM,
        _TUESDAY_10AM,
        status=status,
    )
    session.add(appointment)
    await session.commit()
    return _Booked(
        session_id,
        patient.id,
        practitioner.id,
        appointment.id,
        appointment.idempotency_key,
    )


async def _cancel(session: AsyncSession, booked: _Booked, **overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "session_id": booked.session_id,
        "patient_id": booked.patient_id,
        "appointment_id": booked.appointment_id,
        "expected_starts_at": _TUESDAY_9AM,
        "expected_practitioner_id": booked.practitioner_id,
        "local_now": _LOCAL_NOW,
    }
    kwargs.update(overrides)
    return await appointment_repository.cancel(session, **kwargs)


async def _reschedule(session: AsyncSession, booked: _Booked, **overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "session_id": booked.session_id,
        "patient_id": booked.patient_id,
        "appointment_id": booked.appointment_id,
        "new_starts_at": _TUESDAY_10AM,
        "new_practitioner_id": None,
        "expected_starts_at": _TUESDAY_9AM,
        "expected_practitioner_id": booked.practitioner_id,
        "local_now": _LOCAL_NOW,
        "horizon_days": _HORIZON_DAYS,
    }
    kwargs.update(overrides)
    return await appointment_repository.reschedule(session, **kwargs)


# --- each event, field for field ---------------------------------------------


async def test_the_rescheduled_event_carries_exactly_its_declared_fields(
    db_session: AsyncSession,
) -> None:
    booked = await _seed(db_session)

    with capture_logs() as logs:
        await _reschedule(db_session, booked)

    assert (
        _payload(_find(logs, "appointment.rescheduled"))
        == (_EXPECTED_FIELDS["appointment.rescheduled"])
    )


async def test_the_cancelled_event_carries_exactly_its_declared_fields(
    db_session: AsyncSession,
) -> None:
    booked = await _seed(db_session)

    with capture_logs() as logs:
        await _cancel(db_session, booked)

    assert (
        _payload(_find(logs, "appointment.cancelled"))
        == (_EXPECTED_FIELDS["appointment.cancelled"])
    )


async def test_the_cancelled_event_carries_no_new_start_key_at_all(
    db_session: AsyncSession,
) -> None:
    # Not an empty one, not a placeholder - absent. That is what makes a cancellation
    # distinguishable from a move at a glance, and what a log query filtering on the
    # presence of `new_starts_at` depends on.
    booked = await _seed(db_session)

    with capture_logs() as logs:
        await _cancel(db_session, booked)

    event = _find(logs, "appointment.cancelled")
    assert "new_starts_at" not in event
    assert "new_practitioner_id" not in event


async def test_the_unchanged_event_carries_exactly_its_declared_fields(
    db_session: AsyncSession,
) -> None:
    booked = await _seed(db_session, status=AppointmentStatus.CANCELLED)

    with capture_logs() as logs:
        await _cancel(db_session, booked)

    assert (
        _payload(_find(logs, "appointment.unchanged"))
        == (_EXPECTED_FIELDS["appointment.unchanged"])
    )


async def test_the_refused_event_carries_exactly_its_declared_fields(
    db_session: AsyncSession,
) -> None:
    booked = await _seed(db_session)

    with capture_logs() as logs:
        await _cancel(db_session, booked, expected_starts_at=_TUESDAY_10AM)

    assert _payload(_find(logs, "change.refused")) == _EXPECTED_FIELDS["change.refused"]


async def test_the_key_released_event_carries_exactly_its_declared_fields(
    db_session: AsyncSession,
) -> None:
    booked = await _seed(db_session)

    with capture_logs() as logs:
        await _cancel(db_session, booked)

    assert (
        _payload(_find(logs, "change.key_released"))
        == (_EXPECTED_FIELDS["change.key_released"])
    )


async def test_a_refusal_names_exactly_one_already_resolved_reason(
    db_session: AsyncSession,
) -> None:
    booked = await _seed(db_session)

    with capture_logs() as logs:
        await _cancel(
            db_session,
            booked,
            expected_starts_at=_TUESDAY_10AM,
            local_now=_TUESDAY_9AM + timedelta(minutes=30),
        )

    refused = [log for log in logs if log["event"] == "change.refused"]
    assert len(refused) == 1
    # One reason, already resolved by the precedence - not a list, and not the several
    # rules the request happened to break.
    assert refused[0]["reason"] is ChangeFailureReason.ALREADY_STARTED


@pytest.mark.parametrize("level_event", list(_EXPECTED_FIELDS))
async def test_every_change_record_is_info_level(
    db_session: AsyncSession, level_event: str
) -> None:
    # An evaluated refusal is a normal outcome, not an error - exactly as a booking
    # refusal is. Only the chat side's unknown outcome is an operator's problem.
    booked = await _seed(db_session)
    with capture_logs() as first:
        await _reschedule(db_session, booked)
    with capture_logs() as second:
        await _cancel(
            db_session, booked, expected_starts_at=_TUESDAY_10AM, local_now=_LOCAL_NOW
        )
    with capture_logs() as third:
        await _cancel(db_session, booked, expected_starts_at=_TUESDAY_10AM)

    logs = first + second + third
    matching = [log for log in logs if log["event"] == level_event]
    if matching:
        assert all(log["log_level"] == "info" for log in matching), level_event


# --- mutual exclusion, and never both ----------------------------------------


async def test_unchanged_and_rescheduled_are_mutually_exclusive_for_one_request(
    db_session: AsyncSession,
) -> None:
    booked = await _seed(db_session)

    with capture_logs() as moved:
        await _reschedule(db_session, booked)
    with capture_logs() as unchanged:
        await _reschedule(db_session, booked)

    moved_events = [log["event"] for log in moved]
    unchanged_events = [log["event"] for log in unchanged]
    assert "appointment.rescheduled" in moved_events
    assert "appointment.unchanged" not in moved_events
    assert "appointment.unchanged" in unchanged_events
    assert "appointment.rescheduled" not in unchanged_events


async def test_a_cancellation_never_emits_the_rescheduled_event(
    db_session: AsyncSession,
) -> None:
    booked = await _seed(db_session)

    with capture_logs() as logs:
        await _cancel(db_session, booked)

    assert "appointment.rescheduled" not in [log["event"] for log in logs]


async def test_a_move_never_emits_the_key_released_event(
    db_session: AsyncSession,
) -> None:
    # The key is released by a cancellation and nothing else: a moved appointment still
    # stands, so it still holds its key.
    booked = await _seed(db_session)

    with capture_logs() as logs:
        await _reschedule(db_session, booked)

    assert "change.key_released" not in [log["event"] for log in logs]


# --- what does NOT get a record ----------------------------------------------


async def test_a_refusal_produces_no_completed_change_record(
    db_session: AsyncSession,
) -> None:
    booked = await _seed(db_session)

    with capture_logs() as logs:
        await _cancel(db_session, booked, expected_starts_at=_TUESDAY_10AM)

    events = [log["event"] for log in logs]
    assert "appointment.cancelled" not in events
    assert "appointment.rescheduled" not in events
    assert "appointment.unchanged" not in events
    assert "change.refused" in events


async def test_a_request_that_never_reached_a_change_produces_no_record(
    db_session: AsyncSession,
) -> None:
    # An appointment that does not resolve was never altered, so nothing about it is a
    # change - the refusal is the whole record.
    booked = await _seed(db_session)

    with capture_logs() as logs:
        await _cancel(db_session, booked, appointment_id=new_id())

    events = [log["event"] for log in logs]
    assert events.count("change.refused") == 1
    assert not any(e.startswith("appointment.") for e in events)


# --- turn correlation ---------------------------------------------------------


async def test_every_record_carries_the_turn_id_from_the_request_metadata(
    stub: Any, db_session: AsyncSession
) -> None:
    """A change joins to the conversation that caused it on one key.

    The id is re-bound from `x-turn-id` by the interceptor, so a handler emits it
    without passing it along - which is what makes a record added later carry it too.
    """
    booked = await _seed_over_the_wire(db_session)

    # `capture_logs` replaces the whole processor chain, so the contextvars merge has
    # to be asked for by name - without it a bound id is simply absent from the capture
    # and the assertion would be testing the harness rather than the binding.
    with capture_logs([merge_contextvars]) as logs:
        await stub.CancelAppointment(
            _cancel_request(booked),
            metadata=((TURN_ID_METADATA_KEY, "01TURNFROMTHECHATSERVICE"),),
        )

    for event in ("appointment.cancelled", "change.key_released"):
        assert _find(logs, event)["turn_id"] == "01TURNFROMTHECHATSERVICE"


async def test_a_moves_record_carries_the_turn_id_too(
    stub: Any, db_session: AsyncSession
) -> None:
    booked = await _seed_over_the_wire(db_session)

    with capture_logs([merge_contextvars]) as logs:
        await stub.RescheduleAppointment(
            _reschedule_request(booked),
            metadata=((TURN_ID_METADATA_KEY, "01ANOTHERTURN"),),
        )

    assert _find(logs, "appointment.rescheduled")["turn_id"] == "01ANOTHERTURN"


async def test_a_refusals_record_carries_the_turn_id_too(
    stub: Any, db_session: AsyncSession
) -> None:
    booked = await _seed_over_the_wire(db_session)

    with capture_logs([merge_contextvars]) as logs:
        await stub.CancelAppointment(
            _cancel_request(booked, appointment_id=new_id()),
            metadata=((TURN_ID_METADATA_KEY, "01REFUSEDTURN"),),
        )

    assert _find(logs, "change.refused")["turn_id"] == "01REFUSEDTURN"


# --- privacy ------------------------------------------------------------------


async def test_no_change_record_carries_a_name_a_message_or_a_reply(
    db_session: AsyncSession,
) -> None:
    """Ids, times and reasons only.

    A change record is joinable to the conversation by `turn_id` for anyone entitled to
    read both; it does not restate the conversation. Asserted over the values, not the
    field names, because the leak this prevents is a name arriving inside a field whose
    name sounds harmless.
    """
    session_id = new_id()
    practitioner = await seed_practitioner(
        db_session, session_id, full_name="William Osler"
    )
    patient = await seed_patient(db_session, session_id, full_name="Ada Lovelace")
    appointment = make_appointment(
        session_id, patient.id, practitioner.id, _TUESDAY_9AM, _TUESDAY_10AM
    )
    db_session.add(appointment)
    await db_session.commit()
    booked = _Booked(
        session_id,
        patient.id,
        practitioner.id,
        appointment.id,
        appointment.idempotency_key,
    )

    with capture_logs() as moved:
        await _reschedule(db_session, booked)
    with capture_logs() as cancelled:
        await _cancel(db_session, booked, expected_starts_at=_TUESDAY_10AM)

    records = [
        log
        for log in moved + cancelled
        if log["event"] in _EXPECTED_FIELDS or log["event"] == "change.refused"
    ]
    assert records
    for record in records:
        rendered = " ".join(str(v) for v in record.values())
        assert "William Osler" not in rendered
        assert "Ada Lovelace" not in rendered


async def test_no_change_record_carries_a_free_text_field(
    db_session: AsyncSession,
) -> None:
    # Every declared field is an id, a time, a reason, or an operation name. A record
    # with somewhere to put prose is a record that eventually has prose in it.
    booked = await _seed(db_session)

    with capture_logs() as logs:
        await _reschedule(db_session, booked)
        await _cancel(db_session, booked, expected_starts_at=_TUESDAY_10AM)

    for record in [log for log in logs if log["event"] in _EXPECTED_FIELDS]:
        for key in _payload(record):
            assert key not in {"detail", "message", "reply", "text", "content"}


async def test_the_proto_change_failure_detail_never_reaches_a_record(
    db_session: AsyncSession,
) -> None:
    # The wire's `detail` is for logs on the *caller's* side; the record here carries
    # the resolved reason, which is the value anything downstream should branch on.
    booked = await _seed(db_session)

    with capture_logs() as logs:
        await _cancel(db_session, booked, expected_starts_at=_TUESDAY_10AM)

    refused = _find(logs, "change.refused")
    assert "detail" not in refused
    assert refused["reason"] in set(ChangeFailureReason)
    assert pb.ChangeFailure is not None
