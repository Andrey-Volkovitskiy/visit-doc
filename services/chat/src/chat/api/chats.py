"""`/chats` — the session's chats: list, create, delete, and per-chat history.

Every route is scoped to the cookie's session by filtering on it, never by checking
ownership afterwards, so a chat belonging to another session is indistinguishable from
one that never existed.
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response

from chat.agent.generation_registry import cancel_for_chat
from chat.api.provisioning import provision_patient
from chat.api.session_cookie import read_session_id, set_session_cookie
from chat.clients import scheduling
from chat.clients.scheduling import SchedulingError, SchedulingUnavailableError
from chat.core.config import get_settings
from chat.core.logging import get_logger
from chat.db.session import session_factory
from chat.domain.models import Chat
from chat.domain.schemas import (
    ChatHistoryResponse,
    ChatListResponse,
    ChatSummary,
    MessageOut,
)
from chat.repositories import chat_repository

router = APIRouter()


def _summary(
    chat: Chat, last_message_at: datetime | None, patient_name: str | None
) -> ChatSummary:
    """Build one chat-list row."""
    return ChatSummary(
        id=chat.id,
        patient_name=patient_name,
        created_at=chat.created_at,
        last_message_at=last_message_at,
    )


@router.get("/chats")
async def list_chats(request: Request) -> ChatListResponse:
    """Return the session's chats in display order, and whether a session was known.

    Read-only - never creates a session or a chat; a missing or unrecognized cookie
    returns an empty list rather than an error, reporting no session so the caller can
    tell a first arrival from a session whose chats were all deleted.
    """
    session_id = read_session_id(request)
    if session_id is None:
        return ChatListResponse(chats=[], session_exists=False)

    async with session_factory() as db_session:
        session_row = await chat_repository.get_session(db_session, session_id)
        if session_row is None:
            # A cookie naming a session that no longer exists is a first arrival too:
            # there is nothing to return to.
            return ChatListResponse(chats=[], session_exists=False)
        rows = await chat_repository.list_chats_for_session(db_session, session_row.id)

    return ChatListResponse(
        chats=[
            _summary(chat, last_message_at, chat.patient_name)
            for chat, last_message_at in rows
        ],
        session_exists=True,
    )


@router.get("/chats/{chat_id}/messages")
async def get_chat_messages(chat_id: str, request: Request) -> ChatHistoryResponse:
    """Return one chat's messages, oldest first.

    Raises: HTTPException 404 if there is no session cookie, or `chat_id` belongs to
        another session.
    """
    session_id = read_session_id(request)
    if session_id is None:
        raise HTTPException(status_code=404, detail="chat not found")

    async with session_factory() as db_session:
        chat = await chat_repository.get_chat(db_session, chat_id, session_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="chat not found")
        messages = await chat_repository.list_messages(db_session, chat.id)

    return ChatHistoryResponse(
        messages=[MessageOut.model_validate(m) for m in messages]
    )


@router.post("/chats", status_code=201)
async def create_chat(request: Request, response: Response) -> ChatSummary:
    """Create a chat for the session, minting the session itself on a first visit.

    The `Chat` row is committed *before* the scheduler is called, so an unreachable
    scheduler cannot fail chat creation: the chat comes back unnamed, still answers FAQ
    questions, and its patient is created on a later interaction.
    """
    session_id, is_new_session = await _resolve_session_id(request)

    async with session_factory() as db_session:
        chat = await chat_repository.create_chat(db_session, session_id)

    patient_name = await provision_patient(request.app.state.scheduling_channel, chat)
    get_logger().info(
        "chat.created",
        chat_id=chat.id,
        patient_id=chat.patient_id,
        provisioning_ok=patient_name is not None,
    )

    if is_new_session:
        set_session_cookie(response, session_id)
    return _summary(chat, None, patient_name)


@router.delete("/chats/{chat_id}", status_code=204)
async def delete_chat(chat_id: str, request: Request) -> None:
    """Delete a chat, its messages, its patient, and that patient's appointments.

    Raises:
        HTTPException 404: no session cookie, or the chat belongs to another session.
        HTTPException 502: the scheduler answered by rejecting the request, so nothing
            was deleted - here or there. A defect on this side rather than anything the
            caller chose, and sending the same request again is rejected again.
        HTTPException 503: the scheduler was unreachable, so nothing was deleted.
        HTTPException 504: the scheduler may or may not have deleted the patient -
            either it did not answer, or it failed in a way this build cannot place,
            which is not evidence that the patient survived. The request is safe to send
            again - deleting an already-absent patient succeeds - so the caller is told
            to retry rather than told an outcome. Reporting "nothing was deleted" here
            would be a guess, and the one that leaves a chat bound to a patient that no
            longer exists - which, per the ordering note below, costs that chat every
            scheduling capability it has.

    The scheduler goes first deliberately, and neither ordering fails harmlessly. A
    crash between the steps leaves a chat pointing at a patient that is already gone:
    nothing re-provisions it, so that chat can never book, list or check times again -
    it answers questions and refuses everything scheduling for the rest of its life,
    saying it has no patient record while still listing under that patient's name. A
    retried delete is the only thing that clears it, and only by removing the chat.
    The other ordering was rejected because its failure is worse and unreachable:
    deleting locally first would strand a patient and their appointments with no chat
    left to reach them.

    A turn still generating for this chat is cancelled only once the scheduler call has
    succeeded. Cancelling first would destroy an in-progress reply - it is never
    persisted - and then still refuse the deletion, leaving the patient a question with
    no answer and nothing to retry.
    """
    session_id = read_session_id(request)
    if session_id is None:
        raise HTTPException(status_code=404, detail="chat not found")

    async with session_factory() as db_session:
        chat = await chat_repository.get_chat(db_session, chat_id, session_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="chat not found")

    try:
        deletion = await scheduling.delete_patient_for_chat(
            request.app.state.scheduling_channel,
            get_settings(),
            session_id=session_id,
            chat_id=chat.id,
        )
    except SchedulingUnavailableError as exc:
        # Refused rather than half-done: the never-block guarantee covers chat
        # creation and answering, not deletion, and deleting locally anyway is exactly
        # the orphan this ordering exists to prevent.
        if exc.outcome_unknown:
            raise HTTPException(
                status_code=504,
                detail="scheduling did not answer; the deletion may not have been "
                "applied - try again",
            ) from exc
        raise HTTPException(
            status_code=503, detail="scheduling is unavailable; nothing was deleted"
        ) from exc
    except SchedulingError as exc:
        # The base class, not only the outage subclass: a status the scheduler
        # *answered* with is this request's failure too, and letting it escape would
        # answer a route that knows what happened with an unexplained 500. Only a
        # rejection - decided before anything is deleted - lets the caller be told that
        # nothing was. Either way nothing local is deleted: the chat, its patient and
        # any in-flight turn are left as they were, which is what makes the retry this
        # offers safe.
        nothing_happened = scheduling.rejected_before_writing(exc)
        get_logger().error(
            "chat.delete_rejected",
            chat_id=chat.id,
            error_type=type(exc).__name__,
            error_detail=str(exc),
            outcome_known=nothing_happened,
        )
        if nothing_happened:
            raise HTTPException(
                status_code=502,
                detail="scheduling refused the request; nothing was deleted",
            ) from exc
        raise HTTPException(
            status_code=504,
            detail="scheduling failed in a way this build cannot place; the deletion "
            "may or may not have been applied - try again",
        ) from exc

    turn_cancelled = await cancel_for_chat(chat.id)
    async with session_factory() as db_session:
        await chat_repository.delete_chat(db_session, chat.id, session_id)

    get_logger().info(
        "chat.deleted",
        chat_id=chat.id,
        patient_existed=deletion.patient_existed,
        appointments_deleted=deletion.appointments_deleted,
        turn_cancelled=turn_cancelled,
    )


async def _resolve_session_id(request: Request) -> tuple[str, bool]:
    """Return the visitor's session id, creating one if missing/unrecognized.

    Returns: `(session_id, is_new)` - `is_new` tells the caller whether to mint the
        `Set-Cookie` header.
    """
    cookie_session_id = read_session_id(request)
    async with session_factory() as db_session:
        if cookie_session_id is not None:
            existing = await chat_repository.get_session(db_session, cookie_session_id)
            if existing is not None:
                return existing.id, False
        new_session = await chat_repository.create_session(db_session)
        return new_session.id, True
