"""Tests for the three booking RPCs, over a real gRPC channel to a real database.

A refusal the service evaluated must arrive as a typed `BookingFailure` on a successful
RPC; only caller defects and transport failures carry a status code.
"""

from collections.abc import AsyncIterator
from datetime import datetime, timedelta

import grpc
import pytest
import pytest_asyncio
from scheduler.grpc.interceptors import LoggingInterceptor
from scheduler.grpc.servicer import SchedulingServicer
from shared_models.scheduling import NotFoundEntity
from shared_proto.scheduling.v1 import scheduling_pb2 as pb
from shared_proto.scheduling.v1 import scheduling_pb2_grpc
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import new_id, seed_patient, seed_practitioner

_LOCAL_NOW = "2026-08-17T08:00:00"
_TUESDAY = "2026-08-18"
_TUESDAY_9AM = "2026-08-18T09:00:00"


@pytest_asyncio.fixture
async def stub() -> AsyncIterator[scheduling_pb2_grpc.SchedulingStub]:
    """Serve the real servicer on a loopback port and yield a stub against it.

    A real channel rather than a direct call, so the interceptor, the serialization,
    and the status codes are all exercised the way a caller sees them.
    """
    server = grpc.aio.server(interceptors=[LoggingInterceptor()])
    scheduling_pb2_grpc.add_SchedulingServicer_to_server(  # type: ignore[no-untyped-call]
        SchedulingServicer(), server
    )
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
        yield scheduling_pb2_grpc.SchedulingStub(channel)  # type: ignore[no-untyped-call]
    await server.stop(0)


async def test_list_practitioners_returns_the_sessions_own_with_their_schedule(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    session_id = new_id()
    await seed_practitioner(db_session, session_id, full_name="William Osler")
    await seed_practitioner(db_session, new_id(), full_name="Someone Else")

    response = await stub.ListPractitioners(
        pb.ListPractitionersRequest(session_id=session_id)
    )

    assert [p.full_name for p in response.practitioners] == ["William Osler"]
    listed = response.practitioners[0]
    assert listed.specialty == "General Practice"
    assert listed.appointment_duration_minutes == 60
    assert len(listed.schedule) == 5
    assert listed.schedule[0].start_time == "09:00"
    assert listed.schedule[0].end_time == "17:00"


async def test_list_practitioners_is_empty_for_a_session_with_none(
    stub: scheduling_pb2_grpc.SchedulingStub,
) -> None:
    response = await stub.ListPractitioners(
        pb.ListPractitionersRequest(session_id=new_id())
    )

    assert list(response.practitioners) == []


async def test_check_availability_returns_offset_free_local_starts(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)

    response = await stub.CheckAvailability(
        pb.CheckAvailabilityRequest(
            session_id=session_id,
            practitioner_id=practitioner.id,
            patient_id=patient.id,
            from_date=_TUESDAY,
            to_date=_TUESDAY,
            local_now=_LOCAL_NOW,
        )
    )

    assert response.appointment_duration_minutes == 60
    assert response.truncated is False
    assert response.available_starts[0] == _TUESDAY_9AM
    assert all("Z" not in s and "+" not in s for s in response.available_starts)


async def test_check_availability_excludes_the_requesting_patients_own_bookings(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    session_id = new_id()
    booked_with = await seed_practitioner(db_session, session_id, full_name="A")
    other = await seed_practitioner(db_session, session_id, full_name="B")
    patient = await seed_patient(db_session, session_id)

    booked = await stub.BookAppointment(
        pb.BookAppointmentRequest(
            session_id=session_id,
            patient_id=patient.id,
            practitioner_id=booked_with.id,
            starts_at=_TUESDAY_9AM,
            local_now=_LOCAL_NOW,
            idempotency_key=new_id(),
        )
    )
    assert booked.WhichOneof("result") == "appointment"

    response = await stub.CheckAvailability(
        pb.CheckAvailabilityRequest(
            session_id=session_id,
            practitioner_id=other.id,
            patient_id=patient.id,
            from_date=_TUESDAY,
            to_date=_TUESDAY,
            local_now=_LOCAL_NOW,
        )
    )

    # The hour is free for practitioner B, but not for this patient.
    assert _TUESDAY_9AM not in response.available_starts


async def test_check_availability_for_an_unknown_practitioner_is_not_found(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    # Not an empty result: that is reserved for a practitioner who exists and has
    # nothing free, and the caller offers to look further ahead when it sees one.
    session_id = new_id()
    patient = await seed_patient(db_session, session_id)

    with pytest.raises(grpc.aio.AioRpcError) as caught:
        await stub.CheckAvailability(
            pb.CheckAvailabilityRequest(
                session_id=session_id,
                practitioner_id=new_id(),
                patient_id=patient.id,
                from_date=_TUESDAY,
                to_date=_TUESDAY,
                local_now=_LOCAL_NOW,
            )
        )

    assert caught.value.code() is grpc.StatusCode.NOT_FOUND
    # Which id failed is part of the contract: one status for two ids would leave the
    # caller to guess, and its guess is a sentence the patient reads.
    assert caught.value.details() == NotFoundEntity.PRACTITIONER.value


async def test_check_availability_rejects_another_sessions_patient(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    # Without the session scope, this patient's appointments would still be subtracted
    # from the answer, so the times they hold could be read off the missing slots.
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    foreign_patient = await seed_patient(db_session, new_id())

    with pytest.raises(grpc.aio.AioRpcError) as caught:
        await stub.CheckAvailability(
            pb.CheckAvailabilityRequest(
                session_id=session_id,
                practitioner_id=practitioner.id,
                patient_id=foreign_patient.id,
                from_date=_TUESDAY,
                to_date=_TUESDAY,
                local_now=_LOCAL_NOW,
            )
        )

    assert caught.value.code() is grpc.StatusCode.NOT_FOUND
    assert caught.value.details() == NotFoundEntity.PATIENT.value


async def test_check_availability_rejects_a_reversed_window(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    # A reversed window walks zero days, which would otherwise produce the empty,
    # untruncated result that means "genuinely nothing bookable".
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)

    with pytest.raises(grpc.aio.AioRpcError) as caught:
        await stub.CheckAvailability(
            pb.CheckAvailabilityRequest(
                session_id=session_id,
                practitioner_id=practitioner.id,
                patient_id=patient.id,
                from_date="2026-08-25",
                to_date=_TUESDAY,
                local_now=_LOCAL_NOW,
            )
        )

    assert caught.value.code() is grpc.StatusCode.INVALID_ARGUMENT


async def test_book_appointment_returns_the_created_appointment(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id, full_name="Osler")
    patient = await seed_patient(db_session, session_id, full_name="Ada")

    response = await stub.BookAppointment(
        pb.BookAppointmentRequest(
            session_id=session_id,
            patient_id=patient.id,
            practitioner_id=practitioner.id,
            starts_at=_TUESDAY_9AM,
            local_now=_LOCAL_NOW,
            idempotency_key=new_id(),
        )
    )

    assert response.WhichOneof("result") == "appointment"
    assert response.appointment.patient_full_name == "Ada"
    assert response.appointment.practitioner_full_name == "Osler"
    assert response.appointment.starts_at == _TUESDAY_9AM
    assert response.appointment.ends_at == "2026-08-18T10:00:00"
    assert response.idempotent_replay is False


@pytest.mark.parametrize(
    ("starts_at", "local_now", "expected"),
    [
        ("2026-08-23T10:00:00", _LOCAL_NOW, pb.BOOKING_FAILURE_REASON_OUTSIDE_SCHEDULE),
        ("2026-08-18T09:30:00", _LOCAL_NOW, pb.BOOKING_FAILURE_REASON_OFF_GRID),
        (_TUESDAY_9AM, "2026-08-19T08:00:00", pb.BOOKING_FAILURE_REASON_IN_PAST),
        (_TUESDAY_9AM, "2026-01-01T08:00:00", pb.BOOKING_FAILURE_REASON_BEYOND_HORIZON),
    ],
)
async def test_a_domain_refusal_arrives_as_a_typed_failure_not_an_error_status(
    stub: scheduling_pb2_grpc.SchedulingStub,
    db_session: AsyncSession,
    starts_at: str,
    local_now: str,
    expected: int,
) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)

    response = await stub.BookAppointment(
        pb.BookAppointmentRequest(
            session_id=session_id,
            patient_id=patient.id,
            practitioner_id=practitioner.id,
            starts_at=starts_at,
            local_now=local_now,
            idempotency_key=new_id(),
        )
    )

    assert response.WhichOneof("result") == "failure"
    assert response.failure.reason == expected


async def test_a_repeated_booking_with_one_key_replays_the_original(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)
    request = pb.BookAppointmentRequest(
        session_id=session_id,
        patient_id=patient.id,
        practitioner_id=practitioner.id,
        starts_at=_TUESDAY_9AM,
        local_now=_LOCAL_NOW,
        idempotency_key="one-derived-key",
    )

    first = await stub.BookAppointment(request)
    second = await stub.BookAppointment(request)

    assert second.appointment.id == first.appointment.id
    assert first.idempotent_replay is False
    assert second.idempotent_replay is True


async def test_a_key_recorded_by_another_session_is_never_replayed(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    """Replaying it would hand back that session's appointment and both parties' names.

    The idempotency key is global and derived from the three fields compared here, so
    all three match when a caller supplies another session's ids - the replay runs
    ahead of the session-scoped lookups that would otherwise refuse the request.
    """
    owning_session = new_id()
    practitioner = await seed_practitioner(
        db_session, owning_session, full_name="Osler"
    )
    patient = await seed_patient(db_session, owning_session, full_name="Ada")
    booked = await stub.BookAppointment(
        pb.BookAppointmentRequest(
            session_id=owning_session,
            patient_id=patient.id,
            practitioner_id=practitioner.id,
            starts_at=_TUESDAY_9AM,
            local_now=_LOCAL_NOW,
            idempotency_key="one-derived-key",
        )
    )
    assert booked.WhichOneof("result") == "appointment"

    stolen = await stub.BookAppointment(
        pb.BookAppointmentRequest(
            session_id=new_id(),
            patient_id=patient.id,
            practitioner_id=practitioner.id,
            starts_at=_TUESDAY_9AM,
            local_now=_LOCAL_NOW,
            idempotency_key="one-derived-key",
        )
    )

    assert stolen.WhichOneof("result") == "failure"
    assert stolen.failure.reason == pb.BOOKING_FAILURE_REASON_PRACTITIONER_NOT_FOUND


async def test_a_key_mismatch_is_an_invalid_argument_not_a_failure(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)
    await stub.BookAppointment(
        pb.BookAppointmentRequest(
            session_id=session_id,
            patient_id=patient.id,
            practitioner_id=practitioner.id,
            starts_at=_TUESDAY_9AM,
            local_now=_LOCAL_NOW,
            idempotency_key="one-derived-key",
        )
    )

    with pytest.raises(grpc.aio.AioRpcError) as raised:
        await stub.BookAppointment(
            pb.BookAppointmentRequest(
                session_id=session_id,
                patient_id=patient.id,
                practitioner_id=practitioner.id,
                starts_at="2026-08-18T10:00:00",
                local_now=_LOCAL_NOW,
                idempotency_key="one-derived-key",
            )
        )

    assert raised.value.code() == grpc.StatusCode.INVALID_ARGUMENT


@pytest.mark.parametrize(
    "local_now", ["2026-08-17T08:00:00Z", "2026-08-17T08:00:00+02:00", "not a time"]
)
async def test_a_timezone_bearing_local_now_is_rejected_on_ingress(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession, local_now: str
) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)

    with pytest.raises(grpc.aio.AioRpcError) as raised:
        await stub.BookAppointment(
            pb.BookAppointmentRequest(
                session_id=session_id,
                patient_id=patient.id,
                practitioner_id=practitioner.id,
                starts_at=_TUESDAY_9AM,
                local_now=local_now,
                idempotency_key=new_id(),
            )
        )

    assert raised.value.code() == grpc.StatusCode.INVALID_ARGUMENT


async def test_every_offered_start_is_actually_bookable(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    """The guarantee the availability and booking paths share one validator buys."""
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id, duration_minutes=45)
    patient = await seed_patient(db_session, session_id)

    offered = await stub.CheckAvailability(
        pb.CheckAvailabilityRequest(
            session_id=session_id,
            practitioner_id=practitioner.id,
            patient_id=patient.id,
            from_date=_TUESDAY,
            to_date=_TUESDAY,
            local_now=_LOCAL_NOW,
        )
    )
    assert offered.available_starts

    # Booking the first offered slot must succeed; every later one is then refused
    # only because this patient is now busy, never because it was never bookable.
    response = await stub.BookAppointment(
        pb.BookAppointmentRequest(
            session_id=session_id,
            patient_id=patient.id,
            practitioner_id=practitioner.id,
            starts_at=offered.available_starts[0],
            local_now=_LOCAL_NOW,
            idempotency_key=new_id(),
        )
    )

    assert response.WhichOneof("result") == "appointment"


async def test_the_rpc_lifecycle_pair_is_logged(
    stub: scheduling_pb2_grpc.SchedulingStub,
) -> None:
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        await stub.ListPractitioners(pb.ListPractitionersRequest(session_id=new_id()))

    events = [entry["event"] for entry in logs]
    assert "rpc.received" in events
    assert "rpc.completed" in events
    received = next(e for e in logs if e["event"] == "rpc.received")
    assert received["method"] == "ListPractitioners"


async def test_the_callers_turn_id_reaches_the_servers_log_context(
    stub: scheduling_pb2_grpc.SchedulingStub,
) -> None:
    from structlog.contextvars import merge_contextvars
    from structlog.testing import capture_logs

    with capture_logs([merge_contextvars]) as logs:
        await stub.ListPractitioners(
            pb.ListPractitionersRequest(session_id=new_id()),
            metadata=(("x-turn-id", "01TURNFROMCHAT"),),
        )

    received = next(e for e in logs if e["event"] == "rpc.received")
    assert received["turn_id"] == "01TURNFROMCHAT"


async def test_availability_beyond_the_horizon_offers_nothing(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)
    far = (datetime(2026, 8, 18) + timedelta(days=200)).date()

    response = await stub.CheckAvailability(
        pb.CheckAvailabilityRequest(
            session_id=session_id,
            practitioner_id=practitioner.id,
            patient_id=patient.id,
            from_date=far.isoformat(),
            to_date=far.isoformat(),
            local_now=_LOCAL_NOW,
        )
    )

    assert list(response.available_starts) == []


# --- excluded_appointment_id: the offer path ---------------------------------


async def test_availability_offers_the_slot_the_excluded_appointment_holds(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    """An appointment must not block its own move.

    Without the exclusion, 09:00 is missing from the options offered for the 09:00
    appointment, and a patient could never move one onto a time overlapping the one it
    currently occupies - including keeping the time and changing only the practitioner.
    """
    from .conftest import make_appointment

    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id)
    mine = make_appointment(
        session_id,
        patient.id,
        practitioner.id,
        datetime(2026, 8, 18, 9, 0),
        datetime(2026, 8, 18, 10, 0),
    )
    db_session.add(mine)
    await db_session.commit()

    def request(**extra: str) -> pb.CheckAvailabilityRequest:
        return pb.CheckAvailabilityRequest(
            session_id=session_id,
            practitioner_id=practitioner.id,
            patient_id=patient.id,
            from_date=_TUESDAY,
            to_date=_TUESDAY,
            local_now=_LOCAL_NOW,
            **extra,
        )

    without = await stub.CheckAvailability(request())
    with_exclusion = await stub.CheckAvailability(
        request(excluded_appointment_id=mine.id)
    )

    assert _TUESDAY_9AM not in list(without.available_starts)
    assert _TUESDAY_9AM in list(with_exclusion.available_starts)


async def test_an_excluded_id_from_another_session_frees_nothing(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    # Scoped like every other id, so passing one from another session excludes nothing
    # rather than revealing that it exists.
    from .conftest import make_appointment

    theirs_session = new_id()
    theirs_practitioner = await seed_practitioner(db_session, theirs_session)
    theirs_patient = await seed_patient(db_session, theirs_session)
    theirs = make_appointment(
        theirs_session,
        theirs_patient.id,
        theirs_practitioner.id,
        datetime(2026, 8, 18, 9, 0),
        datetime(2026, 8, 18, 10, 0),
    )
    db_session.add(theirs)
    await db_session.commit()

    response = await stub.CheckAvailability(
        pb.CheckAvailabilityRequest(
            session_id=theirs_session,
            practitioner_id=theirs_practitioner.id,
            patient_id=theirs_patient.id,
            from_date=_TUESDAY,
            to_date=_TUESDAY,
            local_now=_LOCAL_NOW,
            excluded_appointment_id=new_id(),
        )
    )

    assert _TUESDAY_9AM not in list(response.available_starts)
    assert theirs.id is not None
