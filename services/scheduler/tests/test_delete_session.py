"""`DeleteSession` — everything one session owns here, in one transaction.

Two properties carry this rpc. It is **idempotent**, which is load-bearing rather than
incidental: a caller told its deletion was incomplete has to be able to re-run it, and
"already gone" and "was never here" are the same end state to anybody acting on the
answer. And its cascades are **status-blind**, so a cancelled appointment goes with the
rest - it is still that session's row.
"""

from collections.abc import AsyncIterator
from datetime import datetime

import grpc
import pytest
import pytest_asyncio
from scheduler.db.session import session_factory
from scheduler.domain.models import Appointment, Patient, Practitioner
from scheduler.grpc.interceptors import LoggingInterceptor
from scheduler.grpc.servicer import SchedulingServicer
from shared_models.scheduling import AppointmentStatus
from shared_proto.scheduling.v1 import scheduling_pb2 as pb
from shared_proto.scheduling.v1 import scheduling_pb2_grpc
from sqlalchemy import func, select
from structlog.testing import capture_logs

from .conftest import make_appointment, new_id, seed_patient, seed_practitioner

_TUESDAY_9AM = datetime(2026, 8, 18, 9, 0)
_TUESDAY_10AM = datetime(2026, 8, 18, 10, 0)
_TUESDAY_11AM = datetime(2026, 8, 18, 11, 0)


@pytest_asyncio.fixture
async def stub() -> AsyncIterator[scheduling_pb2_grpc.SchedulingStub]:
    server = grpc.aio.server(interceptors=[LoggingInterceptor()])
    scheduling_pb2_grpc.add_SchedulingServicer_to_server(  # type: ignore[no-untyped-call]
        SchedulingServicer(), server
    )
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
        yield scheduling_pb2_grpc.SchedulingStub(channel)  # type: ignore[no-untyped-call]
    await server.stop(0)


async def _count(
    model: type[Patient] | type[Practitioner] | type[Appointment],
    session_id: str | None = None,
) -> int:
    async with session_factory() as session:
        statement = select(func.count()).select_from(model)
        if session_id is not None:
            statement = statement.where(model.session_id == session_id)
        result = await session.execute(statement)
        return int(result.scalar_one())


async def _seed_session(session_id: str, *, cancelled: bool = False) -> tuple[str, str]:
    """Seed one session with a practitioner, a patient and one appointment.

    Returns: the practitioner's id and the patient's id.
    """
    async with session_factory() as session:
        practitioner = await seed_practitioner(session, session_id)
        patient = await seed_patient(session, session_id)
        session.add(
            make_appointment(
                session_id,
                patient.id,
                practitioner.id,
                _TUESDAY_9AM,
                _TUESDAY_10AM,
                status=(
                    AppointmentStatus.CANCELLED
                    if cancelled
                    else AppointmentStatus.STANDING
                ),
            )
        )
        await session.commit()
    return practitioner.id, patient.id


async def test_a_deletion_removes_everything_the_session_owns(
    stub: scheduling_pb2_grpc.SchedulingStub,
) -> None:
    session_id = new_id()
    await _seed_session(session_id)

    response = await stub.DeleteSession(pb.DeleteSessionRequest(session_id=session_id))

    assert response.patients_deleted == 1
    assert response.practitioners_deleted == 1
    assert response.appointments_deleted == 1
    assert await _count(Patient, session_id) == 0
    assert await _count(Practitioner, session_id) == 0
    assert await _count(Appointment, session_id) == 0


async def test_a_cancelled_appointment_goes_with_the_rest(
    stub: scheduling_pb2_grpc.SchedulingStub,
) -> None:
    # The cascades are deliberately status-blind, and this is where that matters: a
    # cancelled appointment is still that session's row, and "everything that session
    # owns" includes it.
    session_id = new_id()
    await _seed_session(session_id, cancelled=True)

    response = await stub.DeleteSession(pb.DeleteSessionRequest(session_id=session_id))

    assert response.appointments_deleted == 1
    assert await _count(Appointment, session_id) == 0


async def test_every_appointment_is_counted_however_many_there_are(
    stub: scheduling_pb2_grpc.SchedulingStub,
) -> None:
    session_id = new_id()
    practitioner_id, patient_id = await _seed_session(session_id)
    async with session_factory() as session:
        session.add(
            make_appointment(
                session_id,
                patient_id,
                practitioner_id,
                _TUESDAY_10AM,
                _TUESDAY_11AM,
                status=AppointmentStatus.CANCELLED,
            )
        )
        await session.commit()

    response = await stub.DeleteSession(pb.DeleteSessionRequest(session_id=session_id))

    assert response.appointments_deleted == 2


async def test_deleting_an_absent_session_succeeds_with_zero_counts(
    stub: scheduling_pb2_grpc.SchedulingStub,
) -> None:
    # Not NOT_FOUND: "already gone" and "was never here" are the same end state, and a
    # caller re-running an incomplete deletion does not act differently on them.
    response = await stub.DeleteSession(pb.DeleteSessionRequest(session_id=new_id()))

    assert response.patients_deleted == 0
    assert response.practitioners_deleted == 0
    assert response.appointments_deleted == 0


async def test_re_running_a_deletion_is_safe_and_converges(
    stub: scheduling_pb2_grpc.SchedulingStub,
) -> None:
    # This is what makes FR-051's "re-run the incomplete ones" a real instruction
    # rather than a hope.
    session_id = new_id()
    await _seed_session(session_id)

    first = await stub.DeleteSession(pb.DeleteSessionRequest(session_id=session_id))
    second = await stub.DeleteSession(pb.DeleteSessionRequest(session_id=session_id))

    assert first.patients_deleted == 1
    assert second.patients_deleted == 0
    assert second.practitioners_deleted == 0
    assert second.appointments_deleted == 0


async def test_another_sessions_rows_are_untouched(
    stub: scheduling_pb2_grpc.SchedulingStub,
) -> None:
    mine = new_id()
    theirs = new_id()
    await _seed_session(mine)
    await _seed_session(theirs)

    await stub.DeleteSession(pb.DeleteSessionRequest(session_id=mine))

    assert await _count(Patient, theirs) == 1
    assert await _count(Practitioner, theirs) == 1
    assert await _count(Appointment, theirs) == 1


async def test_the_counts_are_recorded_where_they_are_known_atomically(
    stub: scheduling_pb2_grpc.SchedulingStub,
) -> None:
    # Emitted here rather than by the caller, for the same reason `appointment.
    # rescheduled` is: this side alone sees the counts in one transaction.
    session_id = new_id()
    await _seed_session(session_id)

    with capture_logs() as logs:
        await stub.DeleteSession(pb.DeleteSessionRequest(session_id=session_id))

    purged = next(e for e in logs if e["event"] == "session.purged")
    assert purged["session_id"] == session_id
    assert purged["patients_deleted"] == 1
    assert purged["practitioners_deleted"] == 1
    assert purged["appointments_deleted"] == 1


async def test_a_request_with_no_session_id_is_rejected(
    stub: scheduling_pb2_grpc.SchedulingStub,
) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as caught:
        await stub.DeleteSession(pb.DeleteSessionRequest(session_id=""))

    assert caught.value.code() is grpc.StatusCode.INVALID_ARGUMENT
