"""Tests for `RenamePatient`, over a channel."""

from collections.abc import AsyncIterator

import grpc
import pytest
import pytest_asyncio
from scheduler.db.session import session_factory
from scheduler.domain.models import NAME_LENGTH
from scheduler.grpc.interceptors import LoggingInterceptor
from scheduler.grpc.servicer import SchedulingServicer
from scheduler.repositories import patient_repository
from shared_proto.scheduling.v1 import scheduling_pb2 as pb
from shared_proto.scheduling.v1 import scheduling_pb2_grpc

from .conftest import new_id, seed_patient


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


async def _stored_name(patient_id: str, session_id: str) -> str | None:
    async with session_factory() as session:
        patient = await patient_repository.get(session, patient_id, session_id)
        return None if patient is None else patient.full_name


async def test_a_rename_persists_and_is_echoed_back(
    stub: scheduling_pb2_grpc.SchedulingStub,
) -> None:
    session_id = new_id()
    async with session_factory() as session:
        patient = await seed_patient(session, session_id, full_name="Ada")

    response = await stub.RenamePatient(
        pb.RenamePatientRequest(
            session_id=session_id, patient_id=patient.id, full_name="Grace"
        )
    )

    assert response.WhichOneof("result") == "patient"
    assert response.patient.full_name == "Grace"
    assert await _stored_name(patient.id, session_id) == "Grace"


async def test_renaming_to_the_current_name_succeeds_and_changes_nothing(
    stub: scheduling_pb2_grpc.SchedulingStub,
) -> None:
    # Idempotence is what makes a timed-out rename safe for the caller to send again.
    session_id = new_id()
    async with session_factory() as session:
        patient = await seed_patient(session, session_id, full_name="Ada")

    request = pb.RenamePatientRequest(
        session_id=session_id, patient_id=patient.id, full_name="Grace"
    )
    await stub.RenamePatient(request)
    response = await stub.RenamePatient(request)

    assert response.WhichOneof("result") == "patient"
    assert response.patient.full_name == "Grace"
    assert await _stored_name(patient.id, session_id) == "Grace"


async def test_a_name_held_by_another_patient_in_the_session_is_refused(
    stub: scheduling_pb2_grpc.SchedulingStub,
) -> None:
    session_id = new_id()
    async with session_factory() as session:
        await seed_patient(session, session_id, full_name="Ada")
        second = await seed_patient(session, session_id, full_name="Bram")

    response = await stub.RenamePatient(
        pb.RenamePatientRequest(
            session_id=session_id, patient_id=second.id, full_name="Ada"
        )
    )

    assert response.WhichOneof("result") == "failure"
    assert response.failure.reason == pb.RENAME_FAILURE_REASON_NAME_TAKEN
    assert await _stored_name(second.id, session_id) == "Bram"


async def test_a_name_held_in_another_session_is_accepted(
    stub: scheduling_pb2_grpc.SchedulingStub,
) -> None:
    # Names are unique per session, not globally: two sessions never see each other.
    session_id = new_id()
    other_session_id = new_id()
    async with session_factory() as session:
        await seed_patient(session, other_session_id, full_name="Ada")
        mine = await seed_patient(session, session_id, full_name="Bram")

    response = await stub.RenamePatient(
        pb.RenamePatientRequest(
            session_id=session_id, patient_id=mine.id, full_name="Ada"
        )
    )

    assert response.WhichOneof("result") == "patient"
    assert await _stored_name(mine.id, session_id) == "Ada"


async def test_another_sessions_patient_is_reported_as_not_found(
    stub: scheduling_pb2_grpc.SchedulingStub,
) -> None:
    owner_session_id = new_id()
    async with session_factory() as session:
        theirs = await seed_patient(session, owner_session_id, full_name="Ada")

    response = await stub.RenamePatient(
        pb.RenamePatientRequest(
            session_id=new_id(), patient_id=theirs.id, full_name="Grace"
        )
    )

    assert response.WhichOneof("result") == "failure"
    assert response.failure.reason == pb.RENAME_FAILURE_REASON_PATIENT_NOT_FOUND
    assert await _stored_name(theirs.id, owner_session_id) == "Ada"


async def test_an_unknown_patient_is_reported_as_not_found(
    stub: scheduling_pb2_grpc.SchedulingStub,
) -> None:
    response = await stub.RenamePatient(
        pb.RenamePatientRequest(
            session_id=new_id(), patient_id=new_id(), full_name="Grace"
        )
    )

    assert response.WhichOneof("result") == "failure"
    assert response.failure.reason == pb.RENAME_FAILURE_REASON_PATIENT_NOT_FOUND


@pytest.mark.parametrize("full_name", ["", "x" * (NAME_LENGTH + 1)])
async def test_a_name_the_column_cannot_hold_is_a_caller_defect(
    stub: scheduling_pb2_grpc.SchedulingStub, full_name: str
) -> None:
    # A malformed request, not a refusal the patient can act on - so a status code
    # rather than a typed failure.
    session_id = new_id()
    async with session_factory() as session:
        patient = await seed_patient(session, session_id, full_name="Ada")

    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await stub.RenamePatient(
            pb.RenamePatientRequest(
                session_id=session_id, patient_id=patient.id, full_name=full_name
            )
        )

    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert await _stored_name(patient.id, session_id) == "Ada"
