"""`/console` — the staff side of one session's conversations.

Every route is scoped to the cookie's session by filtering on it, never by checking
ownership afterwards, so a conversation belonging to another session is
indistinguishable from one that never existed.

Deliberately separate from `api/admin.py`: a maintenance surface sharing a module with
this one is one refactor away from sharing its router and appearing in the published
schema.
"""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from chat.agent.generation_registry import cancel_for_chat
from chat.api.session_cookie import read_session_id
from chat.clients import scheduler_rest
from chat.clients.scheduler_rest import (
    SchedulerTimeoutError,
    SchedulerUnreachableError,
)
from chat.core.config import get_settings
from chat.core.logging import get_logger
from chat.db.session import pinned_session, session_factory
from chat.domain.models import Chat, MessageSender
from chat.domain.schemas import (
    AssistantStateOut,
    AssistantSwitchWrite,
    ConsoleConversationOut,
    ConsoleConversationsResponse,
    MessageOut,
    StaffMessageWrite,
)
from chat.repositories import chat_repository
from chat.repositories.chat_repository import ConsoleConversation, ConversationState

router = APIRouter()


async def _resolve_chat(request: Request, chat_id: str) -> tuple[str, Chat]:
    """Return the cookie's session id and the chat it owns.

    Returns: the session id, and the chat `chat_id` names within it.

    Raises: HTTPException 404 if the request carries no session cookie, or `chat_id`
        belongs to another session - reported identically, so a probing caller learns
        nothing from which one they hit.
    """
    session_id = read_session_id(request)
    if session_id is None:
        raise HTTPException(status_code=404, detail="chat not found")
    async with session_factory() as db_session:
        chat = await chat_repository.get_chat(db_session, chat_id, session_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="chat not found")
    return session_id, chat


def _waited_seconds(state: ConversationState) -> int:
    """Return how long the conversation had been escalated, for the record.

    A duration written to a log entry rather than a deadline anything acts on, so it is
    measured here rather than in SQL - and floored at zero, since a few milliseconds of
    skew between this process's clock and the database's must not produce a negative
    wait.
    """
    if state.escalated_at is None:
        return 0
    return max(0, int((datetime.now(UTC) - state.escalated_at).total_seconds()))


async def _release_lock(db_session: AsyncSession, chat_id: str) -> None:
    """Release `chat_id`'s advisory lock without letting the release decide the answer.

    Belongs in the `finally` of a locked section whose writes have already committed.
    Those writes are durable whatever happens here, so a release that fails is recorded
    and goes no further: raising would replace the answer the commit earned with a 500,
    and a staff member told their reply was not sent sends it again - which the patient
    reads as the clinic answering the same thing twice.

    Recorded at `critical` because a lock that did not release is unreleasable from
    here: every later turn and staff action in the chat it keys waits on it forever.
    `release_chat_lock` records the stranded lock itself; this entry is the other half
    of it - that the caller was nevertheless told their write succeeded, so nobody
    reading the log has to infer which of the two happened.
    """
    try:
        await chat_repository.release_chat_lock(db_session, chat_id)
    except Exception as exc:  # noqa: BLE001 - see the docstring: nothing raised here
        # can undo a committed write, so nothing raised here may change the answer.
        get_logger().critical(
            "chat.lock_release_failed", chat_id=chat_id, error_detail=str(exc)
        )


async def _start_pause(
    db_session: AsyncSession, chat_id: str, session_id: str, *, paused_by: str
) -> ConversationState | None:
    """Silence the assistant for the configured window, restarting any running pause.

    Args:
        paused_by: `"staff_message"` or `"switch"` - the two gestures that write this.
            They write the identical deadline, so this exists to make a silence
            traceable, not because anything behaves differently.

    Returns: the conversation's state as the pause left it, or None if `chat_id` is not
        this session's, in which case nothing was silenced.

    The record is written from what the write reported about itself - the deadline it
    wrote and the pause it replaced - so the section holding the chat's lock spends no
    round trip reading back what it just did.
    """
    written = await chat_repository.set_paused_until(
        db_session, chat_id, session_id, get_settings().ASSISTANT_PAUSE_SECONDS
    )
    get_logger().info(
        "assistant.paused",
        chat_id=chat_id,
        until=written.state.assistant_paused_until if written is not None else None,
        paused_by=paused_by,
        restarted=written is not None and written.restarted,
    )
    return None if written is None else written.state


def _state_out(state: ConversationState | None) -> AssistantStateOut:
    """Render what the assistant may do, for a caller that just changed it."""
    if state is None:
        return AssistantStateOut(assistant_may_reply=True, pause_seconds_remaining=None)
    return AssistantStateOut(
        assistant_may_reply=state.may_assistant_reply,
        pause_seconds_remaining=state.pause_seconds_remaining,
    )


def _row(conversation: ConsoleConversation) -> ConsoleConversationOut:
    """Render one listed conversation."""
    return ConsoleConversationOut(
        chat_id=conversation.chat_id,
        patient_name=conversation.patient_name,
        last_message_at=conversation.last_message_at,
        emphasized=conversation.emphasized,
        escalated=conversation.escalated_at is not None,
        escalation_reason=conversation.escalation_reason,
        attention_since=conversation.attention_since,
        assistant_may_reply=conversation.may_assistant_reply,
        pause_seconds_remaining=conversation.pause_seconds_remaining,
    )


@router.get("/console/conversations")
async def list_conversations(request: Request) -> ConsoleConversationsResponse:
    """Return every conversation in the session, in the staff side's display order.

    Read-only, and never an error: a session with no conversations, and a request
    carrying no session cookie at all, both answer with the same empty shape - exactly
    as `GET /chats` already answers a first arrival.
    """
    session_id = read_session_id(request)
    if session_id is None:
        return ConsoleConversationsResponse(attention_total=0, conversations=[])

    async with session_factory() as db_session:
        rows = await chat_repository.list_conversations_for_console(
            db_session, session_id
        )
    return ConsoleConversationsResponse(
        attention_total=sum(1 for row in rows if row.emphasized),
        conversations=[_row(row) for row in rows],
    )


@router.post("/console/chats/{chat_id}/messages", status_code=201)
async def post_staff_message(
    chat_id: str, body: StaffMessageWrite, request: Request
) -> MessageOut:
    """Post as staff into the patient's own thread, taking the conversation with it.

    Raises: HTTPException 404 if there is no session cookie, or `chat_id` belongs to
        another session.

    Replying *is* taking the conversation, so one post also ends any escalation, stops
    the conversation waiting, and clears every mark a person speaking answers - the
    permanent kinds excepted, since a staff member answering does not mean the corpus
    gained the entry it was missing. Accepted in every conversation of the session,
    escalated or not: there is none a staff member must escalate first in order to speak
    in, and none they may not speak in twice.
    """
    session_id, chat = await _resolve_chat(request, chat_id)

    # Pinned for as long as the lock is held: the section commits, and an engine-bound
    # session would return its connection - and the lock on it - to the pool there.
    async with pinned_session() as db_session:
        # The same lock a turn takes, so a post and a turn can never interleave: a turn
        # that has read the conversation's state must finish reading and inserting
        # before this changes it.
        await chat_repository.lock_chat(db_session, chat.id)
        try:
            # Any reply still being generated is abandoned whole: a partial answer
            # written alongside a staff member's own is worse than none, and the turn
            # registered itself inside this same lock, so none can slip past here.
            cancelled_generation = await cancel_for_chat(chat.id)
            message = await chat_repository.create_message(
                db_session,
                id=str(ULID()),
                chat_id=chat.id,
                sender=MessageSender.STAFF,
                content=body.content,
            )
            # Read before the clears, because what this post *ended* is only knowable
            # from the state it found.
            state = await chat_repository.get_conversation_state(
                db_session, chat.id, session_id
            )
            await chat_repository.clear_escalation(db_session, chat.id, session_id)
            await chat_repository.clear_attention(db_session, chat.id, session_id)
            marks_cleared = await chat_repository.clear_clearable_marks(
                db_session, chat.id, session_id
            )
            await _start_pause(
                db_session, chat.id, session_id, paused_by="staff_message"
            )
        finally:
            await _release_lock(db_session, chat.id)

    ended_escalation = state is not None and state.escalated_at is not None
    logger = get_logger()
    if ended_escalation and state is not None:
        logger.info(
            "escalation.ended",
            chat_id=chat.id,
            ended_by="staff_message",
            escalated_for=state.escalation_reason,
            waited_seconds=_waited_seconds(state),
        )
    logger.info(
        "staff.message_posted",
        chat_id=chat.id,
        message_id=message.id,
        marks_cleared=marks_cleared,
        ended_escalation=ended_escalation,
        cancelled_generation=cancelled_generation,
    )
    return MessageOut.model_validate(message)


@router.post("/console/chats/{chat_id}/assistant")
async def set_assistant(
    chat_id: str, body: AssistantSwitchWrite, request: Request
) -> AssistantStateOut:
    """Turn the assistant on or off in one conversation.

    Raises: HTTPException 404 if there is no session cookie, or `chat_id` belongs to
        another session.

    On clears both silences - the escalation and any running pause. Off writes the
    identical pause a staff message writes, and cancels any reply in flight. Neither
    direction touches whether the conversation still needs a person, or any mark on a
    message: taking a conversation is not answering it, and handing it back is not
    answering it either, which is what makes the control safe to use freely.

    Valid in every state. Turning on an assistant that is already on changes nothing and
    is not an error, and turning off an escalated conversation changes nothing
    observable, since an escalation has no deadline and still governs.
    """
    session_id, chat = await _resolve_chat(request, chat_id)

    # Pinned for the same reason `post_staff_message` pins: the lock must outlive the
    # commits inside the section, and only a held connection can carry it that far.
    async with pinned_session() as db_session:
        await chat_repository.lock_chat(db_session, chat.id)
        try:
            if body.enabled:
                after = await _resume(db_session, chat.id, session_id)
            else:
                await cancel_for_chat(chat.id)
                after = await _start_pause(
                    db_session, chat.id, session_id, paused_by="switch"
                )
        finally:
            await _release_lock(db_session, chat.id)

    return _state_out(after)


async def _resume(
    db_session: AsyncSession, chat_id: str, session_id: str
) -> ConversationState | None:
    """Let the assistant speak here again, clearing both silences.

    Returns: the conversation's state as the clears left it, reported by the second of
        them, or None if `chat_id` is not this session's.

    Reads the state once before writing, because what a resumption *ended* - and
    whether it ended anything at all - is only knowable from the state it found. That
    read is this direction's alone: the state afterwards comes back from the write.

    Records nothing when the assistant was already speaking: a switch moved to the
    position it was already in resumed nothing, and an entry saying otherwise would put
    a resumption in the log that never happened.
    """
    before = await chat_repository.get_conversation_state(
        db_session, chat_id, session_id
    )
    await chat_repository.clear_escalation(db_session, chat_id, session_id)
    after = await chat_repository.clear_pause(db_session, chat_id, session_id)
    if before is None or before.may_assistant_reply:
        return after

    logger = get_logger()
    if before.escalated_at is not None:
        logger.info(
            "escalation.ended",
            chat_id=chat_id,
            ended_by="switch",
            escalated_for=before.escalation_reason,
            waited_seconds=_waited_seconds(before),
        )
    logger.info("assistant.resumed", chat_id=chat_id, resumed_by="switch")
    return after


# --- the practitioner proxy ---------------------------------------------------------
#
# Four routes that re-implement nothing. Every rule, default and refusal belongs to the
# scheduler, which owns practitioners; this side carries the session the browser is not
# allowed to read and relays the answer exactly as it came back (FR-035, FR-036).

_UNREACHABLE_DETAIL = "scheduling is unavailable; nothing was changed"
_TIMEOUT_DETAIL = (
    "scheduling did not answer; the change may not have been applied - try again"
)


async def _proxy(
    request: Request, method: str, path: str, body: Any | None = None
) -> Response:
    """Forward one practitioner request to the scheduler and relay its answer.

    Raises:
        HTTPException 401: the request carries no session cookie, so there is no
            session to act for. Reported before anything is sent.
        HTTPException 503: the scheduler could not be reached, so nothing was changed.
        HTTPException 504: the scheduler did not answer, so what it did is unknown -
            the caller is told to try again rather than told an outcome.

    The relayed response keeps the scheduler's status code and its body verbatim,
    including a refusal's own wording: a duplicate name, overlapping working ranges and
    a practitioner belonging to another session are its judgements to make and to
    explain.
    """
    session_id = read_session_id(request)
    if session_id is None:
        raise HTTPException(status_code=401, detail="no session")

    try:
        proxied = await scheduler_rest.forward(
            request.app.state.http_session,
            get_settings().SCHEDULING_HTTP_BASE_URL,
            method,
            path,
            session_id,
            body,
        )
    except SchedulerUnreachableError as exc:
        raise HTTPException(status_code=503, detail=_UNREACHABLE_DETAIL) from exc
    except SchedulerTimeoutError as exc:
        raise HTTPException(status_code=504, detail=_TIMEOUT_DETAIL) from exc

    if proxied.body is None:
        return Response(status_code=proxied.status_code)
    return JSONResponse(status_code=proxied.status_code, content=proxied.body)


@router.get("/console/practitioners")
async def list_practitioners(request: Request) -> Response:
    """Return the session's practitioners, exactly as the scheduler renders them."""
    return await _proxy(request, "GET", "/practitioners")


@router.post("/console/practitioners")
async def create_practitioner(
    request: Request, body: dict[str, Any] | None = None
) -> Response:
    """Create a practitioner, with every field the caller left out defaulted.

    An empty body is a valid create: the defaults - including the pool-assigned name -
    are the scheduler's, and a console that supplied its own would be a second copy of
    them.
    """
    return await _proxy(request, "POST", "/practitioners", body or {})


@router.patch("/console/practitioners/{practitioner_id}")
async def update_practitioner(
    practitioner_id: str, request: Request, body: dict[str, Any] | None = None
) -> Response:
    """Edit a practitioner; fields the caller omits are left untouched."""
    return await _proxy(
        request, "PATCH", f"/practitioners/{practitioner_id}", body or {}
    )


@router.delete("/console/practitioners/{practitioner_id}")
async def delete_practitioner(practitioner_id: str, request: Request) -> Response:
    """Delete a practitioner and, by the scheduler's cascade, their appointments."""
    return await _proxy(request, "DELETE", f"/practitioners/{practitioner_id}")
