"""Opening the run's session and a chat per case, over a stubbed HTTP surface."""

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from chat.api.session_cookie import COOKIE_NAME
from chat.domain.models import Chat, Session
from golden_harness.driver.session import (
    ChatIdentity,
    ChatNotFoundError,
    SessionError,
    chat_identity,
    live_corpus,
    new_case_chat,
    open_run_session,
    restore_session,
    session_patient_ids,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_BASE_URL = "http://localhost:8000"
_SESSION_ID = "01K5SESSAAAAAAAAAAAAAAAAAA"


class _Chats:
    """A stub of `POST /chats`: mints a session on a cookieless request, as the service
    does, and a fresh chat id on every call."""

    def __init__(self, *, mint_on_every_call: bool = False, status: int = 201) -> None:
        self.requests: list[httpx.Request] = []
        self._count = 0
        self._mint_on_every_call = mint_on_every_call
        self._status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "GET" and request.url.path == "/faq":
            return httpx.Response(200, json=_FAQ)
        assert (request.method, request.url.path) == ("POST", "/chats")
        if self._status != 201:
            return httpx.Response(self._status, json={"detail": "boom"})
        self._count += 1
        headers = {}
        if self._mint_on_every_call or COOKIE_NAME not in request.headers.get(
            "cookie", ""
        ):
            headers["set-cookie"] = f"{COOKIE_NAME}={_SESSION_ID}; HttpOnly; Path=/"
        body = {
            "id": f"01K5CHAT{self._count:018d}",
            "patient_name": "Grace Hopper",
            "created_at": "2026-09-14T10:00:00Z",
            "last_message_at": None,
        }
        return httpx.Response(201, json=body, headers=headers)


_FAQ = [
    {
        "id": 7,
        "content": "Please bring a photo ID.",
        "created_at": "2026-09-14T10:00:00Z",
        "updated_at": "2026-09-14T10:00:00Z",
    }
]


def _client(handler: _Chats) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=_BASE_URL)


async def test_open_run_session_keeps_the_minted_cookie_and_the_first_chat() -> None:
    handler = _Chats()
    async with _client(handler) as client:
        opened = await open_run_session(client)

        assert opened.session_id == _SESSION_ID
        assert opened.chat_id == "01K5CHAT000000000000000001"
        assert client.cookies.get(COOKIE_NAME) == _SESSION_ID
    assert [(r.method, r.url.path) for r in handler.requests] == [("POST", "/chats")]


async def test_open_run_session_refuses_a_response_that_mints_no_session() -> None:
    handler = _Chats()
    async with _client(handler) as client:
        client.cookies.set(COOKIE_NAME, "01K5SOMEONEELSESSESSION000")

        with pytest.raises(SessionError, match="no session"):
            await open_run_session(client)


async def test_open_run_session_refuses_a_status_other_than_created() -> None:
    async with _client(_Chats(status=503)) as client:
        with pytest.raises(SessionError, match="503"):
            await open_run_session(client)


async def test_new_case_chat_sends_the_same_cookie_for_a_fresh_chat_each_call() -> None:
    handler = _Chats()
    async with _client(handler) as client:
        await open_run_session(client)

        first = await new_case_chat(client)
        second = await new_case_chat(client)

    assert first != second
    for request in handler.requests[1:]:
        assert f"{COOKIE_NAME}={_SESSION_ID}" in request.headers["cookie"]


async def test_new_case_chat_refuses_a_response_that_mints_a_new_session() -> None:
    handler = _Chats(mint_on_every_call=True)
    async with _client(handler) as client:
        await open_run_session(client)

        with pytest.raises(SessionError, match="new session"):
            await new_case_chat(client)


async def test_restore_session_sends_the_recorded_session_without_minting_one() -> None:
    handler = _Chats()
    async with _client(handler) as client:
        restore_session(client, _SESSION_ID)

        chat_id = await new_case_chat(client)

    assert chat_id == "01K5CHAT000000000000000001"
    assert f"{COOKIE_NAME}={_SESSION_ID}" in handler.requests[0].headers["cookie"]


async def test_live_corpus_reads_the_sessions_faq() -> None:
    handler = _Chats()
    async with _client(handler) as client:
        restore_session(client, _SESSION_ID)

        entries = await live_corpus(client)

    assert [(e.id, e.content) for e in entries] == [(7, "Please bring a photo ID.")]
    assert (handler.requests[0].method, handler.requests[0].url.path) == ("GET", "/faq")


async def test_live_corpus_refuses_a_status_other_than_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    async with _client(handler) as client:
        with pytest.raises(SessionError, match="GET /faq answered 500"):
            await live_corpus(client)


@pytest.mark.parametrize(
    "call", [open_run_session, new_case_chat, live_corpus], ids=lambda c: c.__name__
)
async def test_a_transport_error_is_raised_to_the_run_not_swallowed(call: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with _client(handler) as client:
        with pytest.raises(httpx.ConnectError):
            await call(client)


async def _plant_chat(
    db: async_sessionmaker[AsyncSession],
    session_id: str,
    chat_id: str,
    patient_id: str | None,
) -> None:
    async with db() as session:
        if await session.get(Session, session_id) is None:
            session.add(Session(id=session_id, created_at=datetime.now(UTC)))
            await session.flush()
        session.add(Chat(id=chat_id, session_id=session_id, patient_id=patient_id))
        await session.commit()


async def test_chat_identity_reads_the_chats_patient_within_its_session(
    chat_db: async_sessionmaker[AsyncSession],
) -> None:
    await _plant_chat(chat_db, _SESSION_ID, "01K5CHATWITHPATIENT0000000", "01K5PAT1")

    async with chat_db() as session:
        identity = await chat_identity(
            session, _SESSION_ID, "01K5CHATWITHPATIENT0000000"
        )

    assert identity == ChatIdentity(session_id=_SESSION_ID, patient_id="01K5PAT1")


async def test_chat_identity_reports_a_chat_provisioned_with_no_patient(
    chat_db: async_sessionmaker[AsyncSession],
) -> None:
    await _plant_chat(chat_db, _SESSION_ID, "01K5CHATNOPATIENT000000000", None)

    async with chat_db() as session:
        identity = await chat_identity(
            session, _SESSION_ID, "01K5CHATNOPATIENT000000000"
        )

    assert identity.patient_id is None


async def test_chat_identity_does_not_resolve_another_sessions_chat(
    chat_db: async_sessionmaker[AsyncSession],
) -> None:
    other_session = "01K5OTHERSESSION0000000000"
    await _plant_chat(chat_db, other_session, "01K5CHATOFANOTHER000000000", "01K5PAT2")
    await _plant_chat(chat_db, _SESSION_ID, "01K5CHATOFOURS00000000000", "01K5PAT3")

    async with chat_db() as session:
        with pytest.raises(ChatNotFoundError):
            await chat_identity(session, _SESSION_ID, "01K5CHATOFANOTHER000000000")


async def test_session_patient_ids_reads_every_patient_of_the_sessions_chats_only(
    chat_db: async_sessionmaker[AsyncSession],
) -> None:
    other_session = "01K5OTHERSESSION0000000000"
    await _plant_chat(chat_db, _SESSION_ID, "01K5CHATA00000000000000000", "01K5PATA")
    await _plant_chat(chat_db, _SESSION_ID, "01K5CHATB00000000000000000", None)
    await _plant_chat(chat_db, _SESSION_ID, "01K5CHATC00000000000000000", "01K5PATC")
    await _plant_chat(chat_db, other_session, "01K5CHATD00000000000000000", "01K5PATD")

    async with chat_db() as session:
        patients = await session_patient_ids(session, _SESSION_ID)

    assert patients == ["01K5PATA", "01K5PATC"]
