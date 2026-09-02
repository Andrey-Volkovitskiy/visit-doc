"""Deleting one session across both stores, and what a partial outcome is called.

The order is chosen so its only failure mode is benign: the scheduler first, then this
service's session row - which takes its chats, messages, marks and FAQ entries by
cascade - then that session's chunks. A crash between the steps leaves a session that a
re-run clears, rather than rows with nothing left to name them.

A partial outcome is never reported as success, and a chunk that outlived its row is
never reported as a partial outcome: those chunks are already unreachable, so telling an
admin to re-run would send them back for something that already achieved every
observable effect.
"""

import json
from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import AsyncMock, patch

import grpc
import pytest
import pytest_asyncio
from chat.core.config import Settings as ChatSettings
from chat.db.session import engine, session_factory
from chat.domain.models import AttentionMark, Chat, FaqEntry, MessageSender, Session
from chat.main import app
from chat.repositories import chat_repository
from chat.repositories.qdrant_repository import (
    COLLECTION_NAME,
    ChunkPayload,
    create_client,
    ensure_collection,
)
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient, Response
from scheduler.db.session import session_factory as scheduler_session_factory
from scheduler.domain.models import Patient, Practitioner
from sqlalchemy import func, select
from structlog.testing import capture_logs
from ulid import ULID

from .conftest import fake_anthropic_client, fake_embed_texts, new_id

_SECRET = "an-admin-secret"
_CONTENT = "Visiting hours are 8am to 5pm."


def _settings(secret: str = _SECRET) -> ChatSettings:
    return ChatSettings().model_copy(update={"ADMIN_SECRET": secret})


@pytest.fixture(autouse=True)
def _no_provisioning() -> Iterator[None]:
    """Keep chat's own provisioning call off the app's lifespan-bound channel.

    The deletion rpc under test is redirected onto this tier's live servicer below;
    provisioning is not what these tests are about, and leaving it on the app's own
    channel would fail on the loop rather than on anything they assert.
    """
    from chat.clients.scheduling import SchedulingUnavailableError

    with patch(
        "chat.api.provisioning.scheduling.ensure_session_provisioned",
        new=AsyncMock(
            side_effect=SchedulingUnavailableError(
                "not provisioned in this test", outcome_unknown=False
            )
        ),
    ):
        yield


@pytest_asyncio.fixture(autouse=True)
async def _qdrant_collection() -> AsyncIterator[None]:
    qdrant_client = create_client(ChatSettings())
    try:
        await ensure_collection(qdrant_client)
    finally:
        await qdrant_client.close()
    yield
    await engine.dispose()


async def _admin(
    scheduling_channel: grpc.aio.Channel,
    path: str,
    *,
    secret: str | None = _SECRET,
    configured: str = _SECRET,
    patches: list[Any] | None = None,
) -> Response:
    """Send one admin request against a live scheduling servicer."""
    await engine.dispose()
    with (
        patch("chat.main.AsyncAnthropic") as mock_anthropic_cls,
        patch("chat.api.admin.get_settings", return_value=_settings(configured)),
    ):
        mock_anthropic_cls.return_value = fake_anthropic_client()
        for extra in patches or []:
            extra.start()
        try:
            with TestClient(app):
                # The app's own channel points at a scheduler that is not running here;
                # this tier has a real servicer on a real socket, and the deletion is
                # supposed to cross that boundary for real.
                app.state.scheduling_channel = scheduling_channel
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://t"
                ) as http:
                    headers = {} if secret is None else {"X-Admin-Secret": secret}
                    return await http.delete(path, headers=headers)
        finally:
            for extra in reversed(patches or []):
                extra.stop()


async def _seed_chat_side(session_id: str) -> tuple[str, int]:
    """Give `session_id` a chat, a marked message, and one FAQ entry with chunks.

    Returns: the chat's id and the FAQ entry's id.
    """
    revision = str(ULID())
    async with session_factory() as session:
        session.add(Session(id=session_id))
        await session.commit()
        chat = await chat_repository.create_chat(session, session_id)
        message_id = str(ULID())
        await chat_repository.create_message(
            session,
            id=message_id,
            chat_id=chat.id,
            sender=MessageSender.PATIENT,
            content="is anyone there?",
        )
        await chat_repository.set_attention_mark(
            session, chat.id, session_id, message_id, AttentionMark.UNANSWERED
        )
        await chat_repository.mark_attention(session, chat.id, session_id)
        from chat.repositories import faq_repository

        entry = await faq_repository.create(session, session_id, _CONTENT, revision)

    qdrant_client = create_client(ChatSettings())
    try:
        from chat.rag.indexing import publish_revision

        with patch("chat.rag.indexing.embed_texts", fake_embed_texts):
            await publish_revision(
                qdrant_client, None, session_id, entry.id, revision, _CONTENT
            )
    finally:
        await qdrant_client.close()
    await engine.dispose()
    return chat.id, entry.id


async def _seed_scheduler_side(session_id: str) -> None:
    async with scheduler_session_factory() as session:
        practitioner = Practitioner(
            id=new_id(),
            session_id=session_id,
            full_name=f"Dr {session_id[:6]}",
            specialty="general_practice",
            appointment_duration_minutes=60,
        )
        patient = Patient(
            id=new_id(),
            session_id=session_id,
            chat_id=new_id(),
            full_name="Ada Lovelace",
        )
        session.add_all([practitioner, patient])
        await session.commit()


async def _chat_counts(session_id: str) -> tuple[int, int, int]:
    """Return how many sessions, chats and FAQ entries `session_id` still has."""
    async with session_factory() as session:
        sessions = await session.execute(
            select(func.count()).select_from(Session).where(Session.id == session_id)
        )
        chats = await session.execute(
            select(func.count()).select_from(Chat).where(Chat.session_id == session_id)
        )
        entries = await session.execute(
            select(func.count())
            .select_from(FaqEntry)
            .where(FaqEntry.session_id == session_id)
        )
    return (
        int(sessions.scalar_one()),
        int(chats.scalar_one()),
        int(entries.scalar_one()),
    )


async def _scheduler_counts(session_id: str) -> tuple[int, int]:
    async with scheduler_session_factory() as session:
        practitioners = await session.execute(
            select(func.count())
            .select_from(Practitioner)
            .where(Practitioner.session_id == session_id)
        )
        patients = await session.execute(
            select(func.count())
            .select_from(Patient)
            .where(Patient.session_id == session_id)
        )
    return int(practitioners.scalar_one()), int(patients.scalar_one())


async def _chunk_sessions() -> set[str]:
    qdrant_client = create_client(ChatSettings())
    try:
        records, _ = await qdrant_client.scroll(
            collection_name=COLLECTION_NAME, limit=1000, with_payload=True
        )
    finally:
        await qdrant_client.close()
    return {
        ChunkPayload.model_validate(r.payload).session_id for r in records if r.payload
    }


def _results(response: Response) -> list[dict[str, Any]]:
    return list(response.json()["results"])


# --- one session, both stores ---------------------------------------------------------


async def test_a_deletion_leaves_nothing_of_that_session_in_either_store(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    await _seed_chat_side(session_id)
    await _seed_scheduler_side(session_id)

    response = await _admin(scheduling_channel, f"/admin/sessions/{session_id}")

    assert response.status_code == 200
    assert _results(response)[0]["status"] == "deleted"
    assert await _chat_counts(session_id) == (0, 0, 0)
    assert await _scheduler_counts(session_id) == (0, 0)
    assert session_id not in await _chunk_sessions()


async def test_no_other_session_is_touched(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    mine = new_id()
    theirs = new_id()
    await _seed_chat_side(mine)
    await _seed_chat_side(theirs)
    await _seed_scheduler_side(mine)
    await _seed_scheduler_side(theirs)

    await _admin(scheduling_channel, f"/admin/sessions/{mine}")

    assert await _chat_counts(theirs) == (1, 1, 1)
    assert await _scheduler_counts(theirs) == (1, 1)
    assert theirs in await _chunk_sessions()


async def test_deleting_an_absent_session_succeeds_with_nothing_removed(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    response = await _admin(scheduling_channel, f"/admin/sessions/{new_id()}")

    result = _results(response)[0]
    assert result["status"] == "deleted"
    assert result["patients_deleted"] == 0
    assert result["practitioners_deleted"] == 0


# --- a partial outcome, and what it is called ----------------------------------------


async def test_an_unreachable_scheduler_is_reported_incomplete_never_deleted(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    from chat.clients.scheduling import SchedulingUnavailableError

    session_id = new_id()
    await _seed_chat_side(session_id)
    await _seed_scheduler_side(session_id)

    response = await _admin(
        scheduling_channel,
        f"/admin/sessions/{session_id}",
        patches=[
            patch(
                "chat.api.admin.scheduling.delete_session",
                new=AsyncMock(
                    side_effect=SchedulingUnavailableError(
                        "scheduling is down", outcome_unknown=False
                    )
                ),
            )
        ],
    )

    assert _results(response)[0]["status"] == "incomplete"
    # And nothing was removed on either side: the chat store is only touched after the
    # scheduler answers.
    assert await _chat_counts(session_id) == (1, 1, 1)
    assert await _scheduler_counts(session_id) == (1, 1)


async def test_an_unreachable_chat_store_is_reported_incomplete(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    await _seed_chat_side(session_id)
    await _seed_scheduler_side(session_id)

    with capture_logs() as logs:
        response = await _admin(
            scheduling_channel,
            f"/admin/sessions/{session_id}",
            patches=[
                patch(
                    "chat.api.admin.chat_repository.delete_session",
                    new=AsyncMock(side_effect=RuntimeError("postgres is down")),
                )
            ],
        )

    assert _results(response)[0]["status"] == "incomplete"
    incomplete = next(e for e in logs if e["event"] == "session.delete_incomplete")
    assert incomplete["failed_at"] == "chat_store"


async def test_re_running_an_incomplete_deletion_completes_it(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    # This is what makes "re-run the incomplete ones" a real instruction: the second
    # attempt converges rather than failing on what the first already removed.
    session_id = new_id()
    await _seed_chat_side(session_id)
    await _seed_scheduler_side(session_id)

    first = await _admin(
        scheduling_channel,
        f"/admin/sessions/{session_id}",
        patches=[
            patch(
                "chat.api.admin.chat_repository.delete_session",
                new=AsyncMock(side_effect=RuntimeError("postgres is down")),
            )
        ],
    )
    second = await _admin(scheduling_channel, f"/admin/sessions/{session_id}")

    assert _results(first)[0]["status"] == "incomplete"
    assert _results(second)[0]["status"] == "deleted"
    assert await _chat_counts(session_id) == (0, 0, 0)
    assert await _scheduler_counts(session_id) == (0, 0)


async def test_deleting_all_sessions_reports_each_on_its_own_terms(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    first = new_id()
    second = new_id()
    await _seed_chat_side(first)
    await _seed_chat_side(second)
    await _seed_scheduler_side(first)
    await _seed_scheduler_side(second)

    response = await _admin(scheduling_channel, "/admin/sessions")

    results = {r["session_id"]: r for r in _results(response)}
    assert set(results) == {first, second}
    assert all(r["status"] == "deleted" for r in results.values())
    assert await _chat_counts(first) == (0, 0, 0)
    assert await _chat_counts(second) == (0, 0, 0)


# --- a leaked chunk is not a partial outcome ------------------------------------------


async def test_a_failed_chunk_removal_is_not_an_incomplete_deletion(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    # The rows that vouched for those chunks are already gone, so nothing can retrieve
    # them. Reporting the leak would send an admin back to re-run something that already
    # achieved every observable effect.
    session_id = new_id()
    await _seed_chat_side(session_id)
    await _seed_scheduler_side(session_id)

    with capture_logs() as logs:
        response = await _admin(
            scheduling_channel,
            f"/admin/sessions/{session_id}",
            patches=[
                patch(
                    "chat.api.admin.delete_by_session",
                    new=AsyncMock(side_effect=RuntimeError("qdrant is down")),
                )
            ],
        )

    assert _results(response)[0]["status"] == "deleted"
    assert await _chat_counts(session_id) == (0, 0, 0)
    events = [e["event"] for e in logs]
    assert "session.delete_incomplete" not in events
    assert "critical.dependency_unreachable" not in events


async def test_a_leaked_chunk_can_no_longer_be_retrieved(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    await _seed_chat_side(session_id)

    await _admin(
        scheduling_channel,
        f"/admin/sessions/{session_id}",
        patches=[
            patch(
                "chat.api.admin.delete_by_session",
                new=AsyncMock(side_effect=RuntimeError("qdrant is down")),
            )
        ],
    )

    # The points are still there - and unreachable, because no row names their
    # revision live and the session that owned them is gone.
    assert session_id in await _chunk_sessions()
    async with session_factory() as session:
        remaining = await session.execute(
            select(func.count())
            .select_from(FaqEntry)
            .where(FaqEntry.session_id == session_id)
        )
    assert int(remaining.scalar_one()) == 0


# --- the guard still applies here -----------------------------------------------------


async def test_a_request_with_no_secret_deletes_nothing(
    scheduling_channel: grpc.aio.Channel,
) -> None:
    session_id = new_id()
    await _seed_chat_side(session_id)
    await _seed_scheduler_side(session_id)

    response = await _admin(
        scheduling_channel, f"/admin/sessions/{session_id}", secret=None
    )

    assert response.status_code == 403
    assert json.loads(response.text) == {"detail": "refused"}
    assert await _chat_counts(session_id) == (1, 1, 1)
    assert await _scheduler_counts(session_id) == (1, 1)
