"""The run's session and each case's chat, made through the chat service's HTTP API.

A run holds one session for all its cases, so the corpus is seeded and verified once,
and gives every case a chat of its own. The session is the `HttpOnly` cookie the service
mints on a cookieless `POST /chats`; the client carries it from then on, exactly as a
browser would.

The two ids no response carries - which session a chat is in, and the scheduler patient
it was provisioned with - are read from the chat database, scoped by the session; so are
the patients of all of a session's chats, which a resumed run releases.
"""

import httpx
from chat.api.session_cookie import COOKIE_NAME
from chat.domain.models import Chat
from chat.domain.schemas import ChatSummary, FaqEntry
from pydantic import BaseModel, ConfigDict, TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_FAQ_ENTRIES = TypeAdapter(list[FaqEntry])


class SessionError(RuntimeError):
    """The service did not open a session or a chat the way the run relies on."""


class ChatNotFoundError(LookupError):
    """No chat with that id belongs to that session."""


class OpenedSession(BaseModel):
    """The session a run opened, and the chat the service created with it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    chat_id: str


class ChatIdentity(BaseModel):
    """A chat's session and its scheduler patient.

    `patient_id` is None when the chat was provisioned with no patient.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    patient_id: str | None


async def open_run_session(client: httpx.AsyncClient) -> OpenedSession:
    """Mint a new session with `POST /chats`, leaving its cookie on `client`.

    Raises: SessionError when the service answers with anything but 201, or mints no
        session - which is what a client already carrying a known session gets.
    """
    response = await client.post("/chats")
    chat = _created_chat(response)
    session_id = response.cookies.get(COOKIE_NAME)
    if session_id is None:
        raise SessionError(
            "POST /chats minted no session; the client already carried one"
        )
    return OpenedSession(session_id=session_id, chat_id=chat.id)


def restore_session(client: httpx.AsyncClient, session_id: str) -> None:
    """Carry an existing session's cookie on `client`, so no new session is minted."""
    client.cookies.set(COOKIE_NAME, session_id)


async def new_case_chat(client: httpx.AsyncClient) -> str:
    """Create a fresh chat in the session `client` carries.

    Returns: the new chat's id

    Raises: SessionError when the service answers with anything but 201, or mints a
        new session - the carried one no longer exists, and a chat in a new session
        would answer from a corpus nobody verified.
    """
    response = await client.post("/chats")
    chat = _created_chat(response)
    if response.cookies.get(COOKIE_NAME) is not None:
        raise SessionError(
            "POST /chats minted a new session; the run's session no longer exists"
        )
    return chat.id


async def live_corpus(client: httpx.AsyncClient) -> list[FaqEntry]:
    """Read the corpus of the session `client` carries, in the service's listing order.

    Raises: SessionError when the service answers with anything but 200.
    """
    response = await client.get("/faq")
    if response.status_code != 200:
        raise SessionError(
            f"GET /faq answered {response.status_code}: {response.text[:500]}"
        )
    return _FAQ_ENTRIES.validate_json(response.content)


async def chat_identity(
    db: AsyncSession, session_id: str, chat_id: str
) -> ChatIdentity:
    """Read a chat's scheduler patient, finding the chat only within `session_id`.

    Raises: ChatNotFoundError when no chat `chat_id` belongs to `session_id`.
    """
    result = await db.execute(
        select(Chat.session_id, Chat.patient_id).where(
            Chat.id == chat_id, Chat.session_id == session_id
        )
    )
    row = result.one_or_none()
    if row is None:
        raise ChatNotFoundError(f"no chat {chat_id} in session {session_id}")
    return ChatIdentity(session_id=row.session_id, patient_id=row.patient_id)


async def session_patient_ids(db: AsyncSession, session_id: str) -> list[str]:
    """Read the scheduler patient of every chat in `session_id` that has one, once each.

    Returns: the patient ids, in the order of their chats' ids
    """
    result = await db.execute(
        select(Chat.patient_id)
        .where(Chat.session_id == session_id, Chat.patient_id.is_not(None))
        .order_by(Chat.id)
    )
    return list(dict.fromkeys(p for p in result.scalars() if p is not None))


def _created_chat(response: httpx.Response) -> ChatSummary:
    """Parse a `POST /chats` response.

    Raises: SessionError when the status is not 201.
    """
    if response.status_code != 201:
        raise SessionError(
            f"POST /chats answered {response.status_code}: {response.text[:500]}"
        )
    return ChatSummary.model_validate_json(response.content)
