"""Tests for the `/chats` resource: listing, creating, deleting, and history."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from chat.agent.generation_registry import register_and_cancel_previous
from chat.api.session_cookie import COOKIE_NAME
from chat.clients.scheduling import (
    PatientInfo,
    RenameRefusal,
    SchedulingNotFoundError,
    SchedulingRequestError,
    SchedulingUnavailableError,
)
from chat.db.session import session_factory
from chat.domain.models import Chat, MessageSender
from chat.main import app
from chat.repositories import chat_repository
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from shared_models.scheduling import RenameFailureReason
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs
from ulid import ULID

from .conftest import LOCAL_NOW, fake_anthropic_client, fake_embed_texts

_PROVISION = "chat.api.provisioning.scheduling.ensure_session_provisioned"


@asynccontextmanager
async def _api(session_id: str | None = None) -> AsyncIterator[AsyncClient]:
    """Yield an HTTP client against the app, with every paid or remote call faked out.

    `TestClient(app)` is entered only to run the lifespan (which builds the shared
    clients); the requests themselves go through `AsyncClient` so an async test's own
    database work and the app's share one event loop.

    Scheduling defaults to unreachable, which is the truth for this tier: no scheduler
    runs alongside chat's unit tests, and the app's gRPC channel is bound to the
    lifespan's loop rather than the test's. A test that wants provisioning to succeed
    patches `_PROVISION` itself.
    """
    with (
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
        patch("chat.rag.retriever.embed_texts", fake_embed_texts),
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client()
        with TestClient(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://t") as client:
                if session_id is not None:
                    client.cookies.set(COOKIE_NAME, session_id)
                yield client


async def _seed_session_with_chats(count: int) -> tuple[str, list[str]]:
    """Create a session and `count` chats in it, ids returned in creation order."""
    async with session_factory() as session:
        created = await chat_repository.create_session(session)
        chat_ids = [
            (await chat_repository.create_chat(session, created.id)).id
            for _ in range(count)
        ]
    return created.id, chat_ids


async def _add_message(chat_id: str, content: str) -> None:
    async with session_factory() as session:
        await chat_repository.create_message(
            session,
            id=str(ULID()),
            chat_id=chat_id,
            sender=MessageSender.PATIENT,
            content=content,
        )


async def test_list_chats_is_empty_without_a_cookie() -> None:
    async with _api() as client:
        response = await client.get("/chats")

    assert response.status_code == 200
    assert response.json() == {"chats": [], "session_exists": False}


async def test_list_chats_is_empty_for_an_unrecognized_cookie() -> None:
    async with _api(session_id=str(ULID())) as client:
        response = await client.get("/chats")

    assert response.status_code == 200
    assert response.json() == {"chats": [], "session_exists": False}


async def test_post_chats_creates_a_chat_and_mints_the_cookie_on_first_visit() -> None:
    async with _api() as client:
        response = await client.post("/chats")

    assert response.status_code == 201
    body = response.json()
    assert len(body["id"]) == 26
    assert body["patient_name"] is None
    assert body["last_message_at"] is None
    assert COOKIE_NAME in response.cookies


async def test_post_chats_reuses_an_existing_session_without_reissuing_the_cookie() -> (
    None
):
    async with _api() as client:
        first = await client.post("/chats")
        second = await client.post("/chats")

    assert first.json()["id"] != second.json()["id"]
    assert COOKIE_NAME not in second.cookies


async def test_post_chats_then_list_returns_both_chats() -> None:
    async with _api() as client:
        first = (await client.post("/chats")).json()["id"]
        second = (await client.post("/chats")).json()["id"]
        listed = (await client.get("/chats")).json()["chats"]

    assert {chat["id"] for chat in listed} == {first, second}


async def test_list_chats_puts_the_chat_with_the_newest_message_first() -> None:
    session_id, chat_ids = await _seed_session_with_chats(3)
    # Only the middle chat gets a message; the third was created most recently, so a
    # coalesced "newest activity" sort would wrongly put it first.
    await _add_message(chat_ids[1], "hello")

    async with _api(session_id) as client:
        listed = (await client.get("/chats")).json()["chats"]

    assert listed[0]["id"] == chat_ids[1]
    assert listed[0]["last_message_at"] is not None
    assert [c["id"] for c in listed[1:]] == [chat_ids[2], chat_ids[0]]
    assert all(c["last_message_at"] is None for c in listed[1:])


async def test_list_chats_never_shows_another_sessions_chats() -> None:
    await _seed_session_with_chats(1)
    stranger_session, theirs = await _seed_session_with_chats(2)

    async with _api(stranger_session) as client:
        listed = (await client.get("/chats")).json()["chats"]

    assert {chat["id"] for chat in listed} == set(theirs)


async def test_get_messages_returns_the_chats_own_history() -> None:
    session_id, chat_ids = await _seed_session_with_chats(2)
    await _add_message(chat_ids[0], "in the first chat")
    await _add_message(chat_ids[1], "in the second chat")

    async with _api(session_id) as client:
        body = (await client.get(f"/chats/{chat_ids[0]}/messages")).json()

    assert [m["content"] for m in body["messages"]] == ["in the first chat"]


async def test_get_messages_404s_for_another_sessions_chat() -> None:
    _, other_chats = await _seed_session_with_chats(1)
    stranger_session, _ = await _seed_session_with_chats(1)

    async with _api(stranger_session) as client:
        response = await client.get(f"/chats/{other_chats[0]}/messages")

    assert response.status_code == 404


async def test_get_messages_404s_without_a_cookie() -> None:
    async with _api() as client:
        response = await client.get(f"/chats/{ULID()}/messages")

    assert response.status_code == 404


# --- provisioning and the degraded path ---------------------------------------


def _provisioned(full_name: str = "Ada Lovelace", *, created: bool = True) -> Mock:
    """A successful `EnsureSessionProvisioned` result."""
    return Mock(
        patient=Mock(id="01PATENT000000000000000000", full_name=full_name),
        practitioners=(),
        patient_created=created,
        practitioner_created=created,
    )


async def test_a_created_chat_reports_its_new_patients_name() -> None:
    with patch(_PROVISION, new=AsyncMock(return_value=_provisioned())):
        async with _api() as client:
            body = (await client.post("/chats")).json()

    assert body["patient_name"] == "Ada Lovelace"


async def test_the_chat_list_shows_the_cached_patient_name_after_a_reload() -> None:
    with patch(_PROVISION, new=AsyncMock(return_value=_provisioned())):
        async with _api() as client:
            await client.post("/chats")
            # A second client on the same cookie is the reload: nothing is remembered
            # in memory, so the name has to come back off the chat row.
            listed = (await client.get("/chats")).json()["chats"]

    assert [c["patient_name"] for c in listed] == ["Ada Lovelace"]


async def test_chat_creation_succeeds_unnamed_when_the_scheduler_is_unreachable() -> (
    None
):
    with patch(
        _PROVISION, new=AsyncMock(side_effect=SchedulingUnavailableError("down"))
    ):
        async with _api() as client:
            response = await client.post("/chats")
            listed = (await client.get("/chats")).json()["chats"]

    assert response.status_code == 201
    assert response.json()["patient_name"] is None
    assert listed[0]["patient_name"] is None


async def test_chat_creation_survives_any_scheduling_failure_not_just_an_outage() -> (
    None
):
    """The guarantee is about chat creation, so it cannot hold for one failure only.

    A rejected request or a chat the scheduler will not resolve would otherwise escape
    provisioning and surface as a 500 on the route that promised never to fail on the
    scheduler.
    """
    for failure in (
        SchedulingRequestError("malformed"),
        SchedulingNotFoundError("chat_not_found"),
    ):
        with patch(_PROVISION, new=AsyncMock(side_effect=failure)):
            async with _api() as client:
                response = await client.post("/chats")

        assert response.status_code == 201, failure
        assert response.json()["patient_name"] is None


async def test_a_degraded_creation_is_logged_as_such() -> None:
    with (
        patch(
            _PROVISION, new=AsyncMock(side_effect=SchedulingUnavailableError("down"))
        ),
        capture_logs(processors=[merge_contextvars]) as logs,
    ):
        async with _api() as client:
            await client.post("/chats")

    created = next(e for e in logs if e["event"] == "chat.created")
    assert created["provisioning_ok"] is False
    assert created["patient_id"] is None


async def test_a_successful_creation_logs_the_provisioned_patient() -> None:
    with (
        patch(_PROVISION, new=AsyncMock(return_value=_provisioned())),
        capture_logs(processors=[merge_contextvars]) as logs,
    ):
        async with _api() as client:
            await client.post("/chats")

    created = next(e for e in logs if e["event"] == "chat.created")
    provisioned = next(e for e in logs if e["event"] == "patient.provisioned")
    assert created["provisioning_ok"] is True
    assert provisioned["created"] is True
    assert provisioned["patient_id"] == "01PATENT000000000000000000"


async def test_a_chat_created_while_degraded_acquires_its_patient_on_a_later_turn() -> (
    None
):
    """The lazy retry: the chat stays usable, and picks up a patient when it can."""
    provision = AsyncMock(side_effect=SchedulingUnavailableError("down"))
    with patch(_PROVISION, new=provision):
        async with _api() as client:
            chat_id = (await client.post("/chats")).json()["id"]

    async with session_factory() as session:
        before = await session.get(Chat, chat_id)
        assert before is not None
        assert before.patient_id is None
        session_id = before.session_id

    provision.side_effect = None
    provision.return_value = _provisioned(created=False)
    with patch(_PROVISION, new=provision):
        async with _api(session_id) as client:
            await client.post(
                "/chat",
                json={
                    "chat_id": chat_id,
                    "message": "when can I visit?",
                    "local_now": LOCAL_NOW,
                },
            )

    async with session_factory() as session:
        after = await session.get(Chat, chat_id)
    assert after is not None
    assert after.patient_id == "01PATENT000000000000000000"
    assert after.patient_name == "Ada Lovelace"


async def test_a_chat_that_already_has_a_patient_is_not_re_provisioned() -> None:
    # A turn asks the scheduler for nothing once the chat has a patient. That keeps the
    # rename route the only writer of the cached name, and keeps a turn's latency clear
    # of the scheduling budget.
    provision = AsyncMock(return_value=_provisioned())
    with patch(_PROVISION, new=provision):
        async with _api() as client:
            chat_id = (await client.post("/chats")).json()["id"]
            calls_after_creation = provision.await_count
            await client.post(
                "/chat",
                json={
                    "chat_id": chat_id,
                    "message": "when can I visit?",
                    "local_now": LOCAL_NOW,
                },
            )

    assert provision.await_count == calls_after_creation


async def test_a_provisioned_chat_keeps_its_name_when_the_scheduler_goes_away() -> None:
    # Losing the scheduler must not blank a name this service already has - and since
    # the turn asks it nothing, there is no failure that could.
    session_id, chat_id = await _seed_chat_with_patient()
    provision = AsyncMock(side_effect=SchedulingUnavailableError("down"))

    with patch(_PROVISION, new=provision):
        async with _api(session_id) as client:
            await client.post(
                "/chat",
                json={
                    "chat_id": chat_id,
                    "message": "when can I visit?",
                    "local_now": LOCAL_NOW,
                },
            )
            listed = (await client.get("/chats")).json()["chats"]

    assert [c["patient_name"] for c in listed] == ["Ada Lovelace"]
    provision.assert_not_awaited()


# --- deletion -----------------------------------------------------------------

_DELETE = "chat.api.chats.scheduling.delete_patient_for_chat"


def _deleted(*, existed: bool = True, appointments: int = 2) -> Mock:
    return Mock(patient_existed=existed, appointments_deleted=appointments)


async def test_deleting_a_chat_removes_it_and_its_messages() -> None:
    session_id, chat_ids = await _seed_session_with_chats(2)
    await _add_message(chat_ids[0], "hello")

    with patch(_DELETE, new=AsyncMock(return_value=_deleted())):
        async with _api(session_id) as client:
            response = await client.delete(f"/chats/{chat_ids[0]}")
            remaining = (await client.get("/chats")).json()["chats"]

    assert response.status_code == 204
    assert [c["id"] for c in remaining] == [chat_ids[1]]
    async with session_factory() as session:
        assert await chat_repository.list_messages(session, chat_ids[0]) == []


async def test_deleting_a_chat_leaves_the_sessions_other_chats_untouched() -> None:
    session_id, chat_ids = await _seed_session_with_chats(3)
    await _add_message(chat_ids[1], "kept")

    with patch(_DELETE, new=AsyncMock(return_value=_deleted())):
        async with _api(session_id) as client:
            await client.delete(f"/chats/{chat_ids[0]}")

    async with session_factory() as session:
        surviving = await chat_repository.list_messages(session, chat_ids[1])
    assert [m.content for m in surviving] == ["kept"]


async def test_deletion_asks_the_scheduler_to_remove_the_patient_first() -> None:
    session_id, chat_ids = await _seed_session_with_chats(1)
    delete_call = AsyncMock(return_value=_deleted())

    with patch(_DELETE, new=delete_call):
        async with _api(session_id) as client:
            await client.delete(f"/chats/{chat_ids[0]}")

    assert delete_call.await_args.kwargs["chat_id"] == chat_ids[0]
    assert delete_call.await_args.kwargs["session_id"] == session_id


async def test_a_completed_deletion_is_logged_with_what_it_removed() -> None:
    session_id, chat_ids = await _seed_session_with_chats(1)

    with (
        patch(_DELETE, new=AsyncMock(return_value=_deleted(appointments=3))),
        capture_logs(processors=[merge_contextvars]) as logs,
    ):
        async with _api(session_id) as client:
            await client.delete(f"/chats/{chat_ids[0]}")

    deleted = next(e for e in logs if e["event"] == "chat.deleted")
    assert deleted["appointments_deleted"] == 3
    assert deleted["patient_existed"] is True
    assert deleted["turn_cancelled"] is False


async def test_deleting_another_sessions_chat_is_not_found() -> None:
    _, theirs = await _seed_session_with_chats(1)
    stranger_session, _ = await _seed_session_with_chats(1)

    with patch(_DELETE, new=AsyncMock(return_value=_deleted())) as delete_call:
        async with _api(stranger_session) as client:
            response = await client.delete(f"/chats/{theirs[0]}")

    assert response.status_code == 404
    delete_call.assert_not_awaited()
    async with session_factory() as session:
        assert await chat_repository.get_chat(session, theirs[0], theirs[0]) is None


async def test_deleting_without_a_cookie_is_not_found() -> None:
    async with _api() as client:
        response = await client.delete(f"/chats/{ULID()}")

    assert response.status_code == 404


async def test_an_unreachable_scheduler_deletes_nothing_and_reports_it() -> None:
    """The never-block guarantee covers creation and answering, never deletion.

    Deleting locally anyway would strand a patient and their appointments with no chat
    left to reach them, which is exactly what the scheduler-first ordering prevents.
    """
    session_id, chat_ids = await _seed_session_with_chats(1)
    unreachable = SchedulingUnavailableError("down", outcome_unknown=False)

    with patch(_DELETE, new=AsyncMock(side_effect=unreachable)):
        async with _api(session_id) as client:
            response = await client.delete(f"/chats/{chat_ids[0]}")

    assert response.status_code == 503
    assert "nothing was deleted" in response.json()["detail"]
    async with session_factory() as session:
        assert await chat_repository.get_chat(session, chat_ids[0], session_id) is not (
            None
        )


async def test_a_deletion_of_unknown_outcome_is_not_reported_as_a_failure() -> None:
    """A deadline is ours, not the scheduler's.

    It expiring does not prove the patient survived, and saying "nothing was deleted"
    would leave this chat bound to a patient that may already be gone - one it can
    never re-provision, since provisioning only ever creates a patient for a chat that
    has none. The caller is told to retry instead; the delete is idempotent.
    """
    session_id, chat_ids = await _seed_session_with_chats(1)
    timed_out = SchedulingUnavailableError("deadline", outcome_unknown=True)

    with patch(_DELETE, new=AsyncMock(side_effect=timed_out)):
        async with _api(session_id) as client:
            response = await client.delete(f"/chats/{chat_ids[0]}")

    assert response.status_code == 504
    detail = response.json()["detail"]
    assert "may not have been applied" in detail
    assert "nothing" not in detail
    async with session_factory() as session:
        assert await chat_repository.get_chat(session, chat_ids[0], session_id) is not (
            None
        )


async def test_a_refused_deletion_leaves_an_in_flight_turn_running() -> None:
    """ "Nothing was deleted" has to include the reply that was being written.

    A turn's answer is persisted only when it completes, so cancelling one and then
    refusing the delete would destroy it for good - leaving the patient a question with
    no answer, a chat that still exists, and nothing to retry.
    """
    session_id, chat_ids = await _seed_session_with_chats(1)

    unreachable = SchedulingUnavailableError("down", outcome_unknown=False)
    with (
        patch(_DELETE, new=AsyncMock(side_effect=unreachable)),
        patch(
            "chat.api.chats.cancel_for_chat", new=AsyncMock(return_value=True)
        ) as cancel_call,
    ):
        async with _api(session_id) as client:
            response = await client.delete(f"/chats/{chat_ids[0]}")

    assert response.status_code == 503
    cancel_call.assert_not_awaited()


async def test_a_session_surviving_with_zero_chats_is_a_valid_state() -> None:
    session_id, chat_ids = await _seed_session_with_chats(1)

    with patch(_DELETE, new=AsyncMock(return_value=_deleted())):
        async with _api(session_id) as client:
            await client.delete(f"/chats/{chat_ids[0]}")
            listed = (await client.get("/chats")).json()

    assert listed == {"chats": [], "session_exists": True}
    async with session_factory() as session:
        assert await chat_repository.get_session(session, session_id) is not None


async def test_deleting_a_chat_mid_turn_cancels_it_and_records_no_reply() -> None:
    """The in-flight reply belongs to a chat that is about to stop existing."""
    session_id, chat_ids = await _seed_session_with_chats(1)
    chat_id = chat_ids[0]

    async def _never_finishes() -> None:
        await asyncio.Event().wait()

    task: asyncio.Task[None] = asyncio.create_task(_never_finishes())
    await register_and_cancel_previous(chat_id, "01TURN", task)

    with (
        patch(_DELETE, new=AsyncMock(return_value=_deleted(appointments=0))),
        capture_logs(processors=[merge_contextvars]) as logs,
    ):
        async with _api(session_id) as client:
            response = await client.delete(f"/chats/{chat_id}")

    assert response.status_code == 204
    assert task.cancelled()
    deleted = next(e for e in logs if e["event"] == "chat.deleted")
    assert deleted["turn_cancelled"] is True
    async with session_factory() as session:
        assert await chat_repository.list_messages(session, chat_id) == []


# --- first arrival vs an emptied session --------------------------------------


async def test_a_request_with_no_cookie_reports_no_session() -> None:
    """The only thing that tells a first arrival from a session the user emptied.

    The client cannot make the distinction itself - the session cookie is `HttpOnly`,
    so the SPA never sees it.
    """
    async with _api() as client:
        body = (await client.get("/chats")).json()

    assert body["session_exists"] is False
    assert body["chats"] == []


async def test_a_cookie_naming_an_unknown_session_reports_no_session() -> None:
    """An unrecognized cookie is a first arrival too - there is nothing to return to."""
    async with _api(session_id=str(ULID())) as client:
        body = (await client.get("/chats")).json()

    assert body["session_exists"] is False


async def test_a_recognized_session_reports_a_session_even_with_no_chats() -> None:
    """FR-040's state: the session survives its last chat being deleted."""
    session_id, chat_ids = await _seed_session_with_chats(1)

    with patch(_DELETE, new=AsyncMock(return_value=_deleted())):
        async with _api(session_id) as client:
            await client.delete(f"/chats/{chat_ids[0]}")
            body = (await client.get("/chats")).json()

    assert body["session_exists"] is True
    assert body["chats"] == []


async def test_a_session_holding_chats_reports_a_session() -> None:
    session_id, _ = await _seed_session_with_chats(2)

    async with _api(session_id) as client:
        body = (await client.get("/chats")).json()

    assert body["session_exists"] is True
    assert len(body["chats"]) == 2


async def test_creating_a_chat_makes_the_next_list_report_a_session() -> None:
    async with _api() as client:
        before = (await client.get("/chats")).json()
        await client.post("/chats")
        after = (await client.get("/chats")).json()

    assert before["session_exists"] is False
    assert after["session_exists"] is True


# --- renaming this chat's patient ----------------------------------------------

_RENAME = "chat.api.chats.scheduling.rename_patient"


async def _seed_chat_with_patient(name: str = "Ada Lovelace") -> tuple[str, str]:
    """Create a session holding one already-provisioned chat.

    Returns: the session id and the chat id.
    """
    async with session_factory() as session:
        created = await chat_repository.create_session(session)
        chat = await chat_repository.create_chat(session, created.id)
        await chat_repository.set_patient(
            session, chat.id, "01PATENT000000000000000000", name
        )
    return created.id, chat.id


def _renamed(full_name: str) -> PatientInfo:
    """A successful `RenamePatient` result."""
    return PatientInfo(
        id="01PATENT000000000000000000",
        chat_id="01CHAT00000000000000000000",
        full_name=full_name,
    )


async def test_a_rename_answers_with_the_new_name() -> None:
    session_id, chat_id = await _seed_chat_with_patient()

    with patch(_RENAME, new=AsyncMock(return_value=_renamed("Grace Hopper"))):
        async with _api(session_id) as client:
            response = await client.patch(
                f"/chats/{chat_id}/patient", json={"full_name": "Grace Hopper"}
            )

    assert response.status_code == 200
    assert response.json() == {"chat_id": chat_id, "patient_name": "Grace Hopper"}


async def test_a_renamed_chat_keeps_its_new_name_in_the_list() -> None:
    session_id, chat_id = await _seed_chat_with_patient()

    with patch(_RENAME, new=AsyncMock(return_value=_renamed("Grace Hopper"))):
        async with _api(session_id) as client:
            await client.patch(
                f"/chats/{chat_id}/patient", json={"full_name": "Grace Hopper"}
            )
            listed = (await client.get("/chats")).json()["chats"]

    assert [c["patient_name"] for c in listed] == ["Grace Hopper"]


async def test_the_cached_name_is_the_schedulers_answer_not_the_request() -> None:
    # The scheduler owns the value: what it echoes back is what gets displayed, even
    # when that differs from what was asked for.
    session_id, chat_id = await _seed_chat_with_patient()

    with patch(_RENAME, new=AsyncMock(return_value=_renamed("Grace B. Hopper"))):
        async with _api(session_id) as client:
            body = (
                await client.patch(
                    f"/chats/{chat_id}/patient", json={"full_name": "Grace Hopper"}
                )
            ).json()
            listed = (await client.get("/chats")).json()["chats"]

    assert body["patient_name"] == "Grace B. Hopper"
    assert [c["patient_name"] for c in listed] == ["Grace B. Hopper"]


async def test_a_taken_name_is_a_conflict_and_changes_nothing() -> None:
    session_id, chat_id = await _seed_chat_with_patient()
    refusal = RenameRefusal(reason=RenameFailureReason.NAME_TAKEN, detail="name_taken")

    with patch(_RENAME, new=AsyncMock(return_value=refusal)):
        async with _api(session_id) as client:
            response = await client.patch(
                f"/chats/{chat_id}/patient", json={"full_name": "Grace Hopper"}
            )
            listed = (await client.get("/chats")).json()["chats"]

    assert response.status_code == 409
    assert [c["patient_name"] for c in listed] == ["Ada Lovelace"]


async def test_a_patient_the_scheduler_no_longer_has_is_not_found() -> None:
    session_id, chat_id = await _seed_chat_with_patient()
    refusal = RenameRefusal(
        reason=RenameFailureReason.PATIENT_NOT_FOUND, detail="patient_not_found"
    )

    with patch(_RENAME, new=AsyncMock(return_value=refusal)):
        async with _api(session_id) as client:
            response = await client.patch(
                f"/chats/{chat_id}/patient", json={"full_name": "Grace Hopper"}
            )

    assert response.status_code == 404


async def test_a_chat_without_a_patient_yet_cannot_be_renamed() -> None:
    async with session_factory() as session:
        created = await chat_repository.create_session(session)
        chat = await chat_repository.create_chat(session, created.id)

    async with _api(created.id) as client:
        response = await client.patch(
            f"/chats/{chat.id}/patient", json={"full_name": "Grace Hopper"}
        )

    assert response.status_code == 409


async def test_another_sessions_chat_cannot_be_renamed() -> None:
    _, chat_id = await _seed_chat_with_patient()
    async with session_factory() as session:
        intruder = await chat_repository.create_session(session)

    async with _api(intruder.id) as client:
        response = await client.patch(
            f"/chats/{chat_id}/patient", json={"full_name": "Grace Hopper"}
        )

    assert response.status_code == 404


async def test_an_unreachable_scheduler_reports_that_nothing_was_renamed() -> None:
    session_id, chat_id = await _seed_chat_with_patient()
    unreachable = SchedulingUnavailableError("down", outcome_unknown=False)

    with patch(_RENAME, new=AsyncMock(side_effect=unreachable)):
        async with _api(session_id) as client:
            response = await client.patch(
                f"/chats/{chat_id}/patient", json={"full_name": "Grace Hopper"}
            )
            listed = (await client.get("/chats")).json()["chats"]

    assert response.status_code == 503
    assert "nothing was renamed" in response.json()["detail"]
    assert [c["patient_name"] for c in listed] == ["Ada Lovelace"]


async def test_an_unknown_outcome_is_reported_as_unknown_not_as_a_failure() -> None:
    # The deadline is ours, not the server's: it expiring does not prove the rename
    # was not applied, so the caller is told to retry rather than told it did not
    # happen.
    session_id, chat_id = await _seed_chat_with_patient()
    timed_out = SchedulingUnavailableError("deadline", outcome_unknown=True)

    with patch(_RENAME, new=AsyncMock(side_effect=timed_out)):
        async with _api(session_id) as client:
            response = await client.patch(
                f"/chats/{chat_id}/patient", json={"full_name": "Grace Hopper"}
            )

    assert response.status_code == 504
    detail = response.json()["detail"]
    assert "may not have been applied" in detail
    assert "nothing" not in detail


async def test_an_empty_name_is_rejected_before_the_scheduler_is_called() -> None:
    session_id, chat_id = await _seed_chat_with_patient()
    rename = AsyncMock(return_value=_renamed("unused"))

    with patch(_RENAME, new=rename):
        async with _api(session_id) as client:
            response = await client.patch(
                f"/chats/{chat_id}/patient", json={"full_name": ""}
            )

    assert response.status_code == 422
    rename.assert_not_awaited()


# --- 007: creating a session costs nothing in the retrieval path -------------------


def test_creating_a_session_touches_neither_the_embedder_nor_the_retrieval_store() -> (
    None
):
    # A new session's corpus is empty because nothing seeded it, not because a seeding
    # step failed - so provisioning must not gain a corpus step at all. Asserted as an
    # absence, which is the only way to state "no step was added".
    with (
        patch("chat.rag.indexing.embed_texts") as embed,
        patch("chat.repositories.qdrant_repository.upsert_chunks") as upsert,
        TestClient(app) as client,
    ):
        response = client.post("/chats")

    assert response.status_code == 201
    embed.assert_not_called()
    upsert.assert_not_called()


def test_a_chat_is_created_even_when_the_retrieval_store_refuses_every_call() -> None:
    # Nothing about session creation reaches Qdrant, so a store that answers nothing
    # cannot stop a visitor getting a chat. If this ever fails, provisioning acquired a
    # dependency it is not supposed to have.
    with TestClient(app) as client:
        client.app.state.qdrant_client = MagicMock(  # type: ignore[attr-defined]
            side_effect=RuntimeError("qdrant down")
        )
        response = client.post("/chats")

    assert response.status_code == 201
    assert response.json()["id"]
