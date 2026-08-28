"""Tests for `EnsureSessionProvisioned` and `DeletePatientForChat`, over a channel."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import grpc
import pytest
import pytest_asyncio
from scheduler.db.session import session_factory
from scheduler.domain.models import Appointment, Patient, Practitioner
from scheduler.domain.name_pools import PHYSICIAN_POOL, WRITER_POOL
from scheduler.grpc.interceptors import LoggingInterceptor
from scheduler.grpc.servicer import SchedulingServicer
from shared_models.scheduling import NotFoundEntity, Specialty
from shared_proto.scheduling.v1 import scheduling_pb2 as pb
from shared_proto.scheduling.v1 import scheduling_pb2_grpc
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import new_id, seed_patient, seed_practitioner

_LOCAL_NOW = "2026-08-17T08:00:00"
_TUESDAY_9AM = "2026-08-18T09:00:00"


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
) -> int:
    async with session_factory() as session:
        result = await session.execute(select(func.count()).select_from(model))
        return int(result.scalar_one())


async def test_a_first_visit_creates_a_patient_and_the_default_roster(
    stub: scheduling_pb2_grpc.SchedulingStub,
) -> None:
    session_id = new_id()
    chat_id = new_id()

    response = await stub.EnsureSessionProvisioned(
        pb.EnsureSessionProvisionedRequest(session_id=session_id, chat_id=chat_id)
    )

    assert response.patient_created is True
    assert response.practitioner_created is True
    assert response.patient.full_name == WRITER_POOL[0]
    assert response.patient.chat_id == chat_id
    assert [p.full_name for p in response.practitioners] == list(PHYSICIAN_POOL[:2])
    assert [p.specialty for p in response.practitioners] == [
        Specialty.GENERAL_PRACTICE,
        Specialty.DENTISTRY,
    ]
    # Immediately bookable: both came with their Monday-to-Saturday schedules.
    assert [len(p.schedule) for p in response.practitioners] == [6, 6]


async def test_a_second_call_for_one_chat_creates_nothing(
    stub: scheduling_pb2_grpc.SchedulingStub,
) -> None:
    session_id = new_id()
    chat_id = new_id()
    request = pb.EnsureSessionProvisionedRequest(session_id=session_id, chat_id=chat_id)

    first = await stub.EnsureSessionProvisioned(request)
    second = await stub.EnsureSessionProvisioned(request)

    assert second.patient.id == first.patient.id
    assert second.patient_created is False
    assert second.practitioner_created is False
    assert await _count(Patient) == 1


async def test_another_sessions_chat_is_not_found_rather_than_answered(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    """A chat is unique across the whole store, which is not the same as readable.

    Answering with the patient that holds it would hand a caller another session's
    patient id and name - and the chat service would then cache them as its own.
    """
    chat_id = new_id()
    theirs = await seed_patient(db_session, new_id(), full_name="Ada", chat_id=chat_id)

    with pytest.raises(grpc.aio.AioRpcError) as caught:
        await stub.EnsureSessionProvisioned(
            pb.EnsureSessionProvisionedRequest(session_id=new_id(), chat_id=chat_id)
        )

    assert caught.value.code() is grpc.StatusCode.NOT_FOUND
    assert caught.value.details() == NotFoundEntity.CHAT.value
    assert await _count(Patient) == 1
    assert theirs.full_name == "Ada"


async def test_a_second_chat_in_one_session_gets_its_own_patient_but_no_roster(
    stub: scheduling_pb2_grpc.SchedulingStub,
) -> None:
    session_id = new_id()

    first = await stub.EnsureSessionProvisioned(
        pb.EnsureSessionProvisionedRequest(session_id=session_id, chat_id=new_id())
    )
    second = await stub.EnsureSessionProvisioned(
        pb.EnsureSessionProvisionedRequest(session_id=session_id, chat_id=new_id())
    )

    assert second.patient.id != first.patient.id
    assert [second.patient.full_name] == [WRITER_POOL[1]]
    assert second.practitioner_created is False
    assert [p.id for p in second.practitioners] == [p.id for p in first.practitioners]


async def test_two_sessions_may_hold_the_same_patient_name(
    stub: scheduling_pb2_grpc.SchedulingStub,
) -> None:
    first = await stub.EnsureSessionProvisioned(
        pb.EnsureSessionProvisionedRequest(session_id=new_id(), chat_id=new_id())
    )
    second = await stub.EnsureSessionProvisioned(
        pb.EnsureSessionProvisionedRequest(session_id=new_id(), chat_id=new_id())
    )

    assert first.patient.full_name == second.patient.full_name
    assert first.patient.id != second.patient.id


async def test_concurrent_provisioning_of_one_chat_yields_one_patient(
    stub: scheduling_pb2_grpc.SchedulingStub,
) -> None:
    session_id = new_id()
    chat_id = new_id()
    request = pb.EnsureSessionProvisionedRequest(session_id=session_id, chat_id=chat_id)

    responses = await asyncio.gather(
        stub.EnsureSessionProvisioned(request),
        stub.EnsureSessionProvisioned(request),
    )

    assert len({r.patient.id for r in responses}) == 1
    assert await _count(Patient) == 1


async def test_concurrent_provisioning_of_two_chats_yields_two_distinct_names(
    stub: scheduling_pb2_grpc.SchedulingStub,
) -> None:
    """A name race is resolved by the UNIQUE constraint and a retry, not by locking.

    Patients and practitioners resolve it oppositely, and both are checked here: two
    chats need two patients, so the loser retries under the next name; one session
    needs one roster, so the loser abandons its own and re-reads the winner's.
    """
    session_id = new_id()

    responses = await asyncio.gather(
        stub.EnsureSessionProvisioned(
            pb.EnsureSessionProvisionedRequest(session_id=session_id, chat_id=new_id())
        ),
        stub.EnsureSessionProvisioned(
            pb.EnsureSessionProvisionedRequest(session_id=session_id, chat_id=new_id())
        ),
    )

    assert len({r.patient.full_name for r in responses}) == 2
    assert await _count(Patient) == 2
    assert await _count(Practitioner) == 2


# --- deletion ----------------------------------------------------------------


async def test_deleting_a_chats_patient_cascades_to_their_appointments(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    session_id = new_id()
    chat_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    patient = await seed_patient(db_session, session_id, chat_id=chat_id)
    await stub.BookAppointment(
        pb.BookAppointmentRequest(
            session_id=session_id,
            patient_id=patient.id,
            practitioner_id=practitioner.id,
            starts_at=_TUESDAY_9AM,
            local_now=_LOCAL_NOW,
            idempotency_key=new_id(),
        )
    )

    response = await stub.DeletePatientForChat(
        pb.DeletePatientForChatRequest(session_id=session_id, chat_id=chat_id)
    )

    assert response.patient_existed is True
    assert response.appointments_deleted == 1
    assert await _count(Patient) == 0
    assert await _count(Appointment) == 0


async def test_deleting_an_absent_patient_succeeds_and_reports_nothing_removed(
    stub: scheduling_pb2_grpc.SchedulingStub,
) -> None:
    response = await stub.DeletePatientForChat(
        pb.DeletePatientForChatRequest(session_id=new_id(), chat_id=new_id())
    )

    assert response.patient_existed is False
    assert response.appointments_deleted == 0


async def test_deletion_is_idempotent(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    session_id = new_id()
    chat_id = new_id()
    await seed_patient(db_session, session_id, chat_id=chat_id)
    request = pb.DeletePatientForChatRequest(session_id=session_id, chat_id=chat_id)

    first = await stub.DeletePatientForChat(request)
    second = await stub.DeletePatientForChat(request)

    assert first.patient_existed is True
    assert second.patient_existed is False


async def test_deletion_never_touches_another_sessions_patient(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    chat_id = new_id()
    await seed_patient(db_session, new_id(), chat_id=chat_id)

    response = await stub.DeletePatientForChat(
        pb.DeletePatientForChatRequest(session_id=new_id(), chat_id=chat_id)
    )

    assert response.patient_existed is False
    assert await _count(Patient) == 1


async def test_deletion_leaves_the_sessions_other_patients_untouched(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    session_id = new_id()
    doomed_chat = new_id()
    await seed_patient(db_session, session_id, full_name="Ada", chat_id=doomed_chat)
    await seed_patient(db_session, session_id, full_name="Bram")

    await stub.DeletePatientForChat(
        pb.DeletePatientForChatRequest(session_id=session_id, chat_id=doomed_chat)
    )

    assert await _count(Patient) == 1


async def test_a_deleted_patient_can_be_provisioned_afresh(
    stub: scheduling_pb2_grpc.SchedulingStub,
) -> None:
    """The recoverable half of the deletion ordering: a dangling id re-provisions."""
    session_id = new_id()
    chat_id = new_id()
    request = pb.EnsureSessionProvisionedRequest(session_id=session_id, chat_id=chat_id)
    first = await stub.EnsureSessionProvisioned(request)
    await stub.DeletePatientForChat(
        pb.DeletePatientForChatRequest(session_id=session_id, chat_id=chat_id)
    )

    second = await stub.EnsureSessionProvisioned(request)

    assert second.patient.id != first.patient.id
    assert second.patient_created is True


async def test_listing_upcoming_appointments_is_scoped_to_one_patient(
    stub: scheduling_pb2_grpc.SchedulingStub, db_session: AsyncSession
) -> None:
    session_id = new_id()
    practitioner = await seed_practitioner(db_session, session_id)
    mine = await seed_patient(db_session, session_id, full_name="Ada")
    theirs = await seed_patient(db_session, session_id, full_name="Bram")
    for patient, starts_at in ((mine, _TUESDAY_9AM), (theirs, "2026-08-18T11:00:00")):
        await stub.BookAppointment(
            pb.BookAppointmentRequest(
                session_id=session_id,
                patient_id=patient.id,
                practitioner_id=practitioner.id,
                starts_at=starts_at,
                local_now=_LOCAL_NOW,
                idempotency_key=new_id(),
            )
        )

    response = await stub.ListUpcomingAppointments(
        pb.ListUpcomingAppointmentsRequest(
            session_id=session_id, patient_id=mine.id, local_now=_LOCAL_NOW
        )
    )

    assert [a.starts_at for a in response.appointments] == [_TUESDAY_9AM]
    assert response.appointments[0].patient_full_name == "Ada"


def test_no_scheduler_module_reads_the_host_clock() -> None:
    """The caller's `local_now` decides every past, upcoming, and horizon question.

    A source-level guard rather than a behavioural one because the failure it catches
    is a *stray* clock read - one that would only show up on a day the host and the
    caller happen to disagree, which no ordinary test run would hit.
    """
    import scheduler

    root = Path(scheduler.__file__).parent
    offenders = [
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if "datetime.now(" in path.read_text() or "date.today(" in path.read_text()
    ]

    assert offenders == []
