"""`POST /chat` — the streaming turn endpoint."""

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime

import grpc
from anthropic import AsyncAnthropic
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from qdrant_client import AsyncQdrantClient
from ulid import ULID
from voyageai.client_async import AsyncClient as VoyageAsyncClient

from chat.agent import history
from chat.agent.escalation import EscalationRequests, apply_escalation
from chat.agent.generation_registry import (
    clear_if_current,
    register_and_cancel_previous,
)
from chat.agent.graph import run_turn
from chat.agent.tools.registry import ToolContext
from chat.api.dependencies import get_voyage_client
from chat.api.provisioning import provision_patient
from chat.api.session_cookie import read_session_id
from chat.core.config import get_settings
from chat.core.correlation import bind_turn_id
from chat.core.logging import get_logger
from chat.db.session import pinned_session, session_factory
from chat.domain.models import AttentionMark, Chat, Message, MessageSender
from chat.domain.schemas import (
    ChatCancelledEvent,
    ChatDoneEvent,
    ChatRequest,
    ChatSilentEvent,
    ChatTokenEvent,
)
from chat.rag.retriever import TurnPipelineError
from chat.repositories import chat_repository, faq_repository

router = APIRouter()

# Pipeline steps backed by an FR-015-scoped dependency (qdrant/anthropic_api) - not
# "embedding" (Voyage) or "groundedness" (pure computation), per spec.md Assumptions.
_CRITICAL_DEPENDENCY_BY_STEP = {"retrieval": "qdrant", "generation": "anthropic_api"}

# Used in the booking prompt until a chat has a real patient. The scheduler owns
# patient names, so this side never invents one that could then disagree with it.
_PATIENT_PLACEHOLDER_NAME = "the patient"


def _silenced_by(state: chat_repository.ConversationState | None) -> str | None:
    """Return which state stops the assistant replying here, or None if none does.

    Returns: `"escalation"` while staff have been called and nobody has answered,
        `"pause"` while a staff member is leading the conversation, or None when the
        assistant may speak.

    The two are distinguished because the mark left on the message does not record
    which was in force, and they end in different ways.
    """
    if state is None or state.may_assistant_reply:
        return None
    return "escalation" if state.escalated_at is not None else "pause"


async def _resolve_chat(request: Request, chat_id: str) -> Chat:
    """Return the chat `chat_id` identifies within the request's cookie session.

    Raises: HTTPException 404 if the request carries no session cookie, the session is
        unrecognized, or `chat_id` belongs to another session - all reported
        identically, so a probing caller learns nothing from which one it hit.

    Never creates a session or a chat: both arrive via `POST /chats`.
    """
    session_id = read_session_id(request)
    if session_id is None:
        raise HTTPException(status_code=404, detail="chat not found")
    async with session_factory() as db_session:
        chat = await chat_repository.get_chat(db_session, chat_id, session_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="chat not found")
    return chat


async def _persist_outcome(
    chat: Chat,
    patient_message_id: str,
    reply_to_message_ids: list[str],
    escalation: EscalationRequests,
    done_event: ChatDoneEvent | None,
    answer: str,
) -> None:
    """Write what this turn produced and what it decided, under the chat's lock.

    Args:
        done_event: The completion event whose reply is to be stored, or None when
            there is none to store - the pipeline did not complete, or a newer message
            superseded this turn.
        answer: The text streamed to the patient, stored whenever `done_event` carries
            no message of its own.

    Holds the lock a staff post takes, for the whole of both writes: the reply and the
    transition either both precede a staff member taking the conversation over or both
    follow it, never one of each. Without it the transition lands on top of the clears
    that post just made, and the conversation falls silent again with a person already
    in it.
    """
    # Pinned, because the section commits and the lock lives on the connection rather
    # than on the transaction - see `pinned_session`.
    async with pinned_session() as db_session:
        await chat_repository.lock_chat(db_session, chat.id)
        try:
            if done_event is not None:
                # `message` is set only when there is no streamed text to show, which
                # today is the FAQ abstention case. `grounded` stays NULL for a
                # booking-only reply: it was never retrieved against, so it is neither
                # grounded nor abstaining.
                await chat_repository.create_message(
                    db_session,
                    id=str(ULID()),
                    chat_id=chat.id,
                    sender=MessageSender.ASSISTANT,
                    content=done_event.message or answer,
                    grounded=done_event.grounded,
                    citations=[c.model_dump() for c in done_event.citations],
                    reply_to_message_ids=reply_to_message_ids,
                )
            # After the graph has completed and the reply has been delivered: the turn
            # runs to its end and the conversation transitions at the end of it, so a
            # mixed-intent message whose halves both ran delivers both before anything
            # is silenced.
            await apply_escalation(
                db_session, chat.id, chat.session_id, patient_message_id, escalation
            )
        finally:
            await chat_repository.release_chat_lock(db_session, chat.id)


async def _event_stream(
    qdrant_client: AsyncQdrantClient,
    voyage_client: VoyageAsyncClient,
    anthropic_client: AsyncAnthropic,
    scheduling_channel: grpc.aio.Channel,
    message: str,
    chat: Chat,
    local_now: datetime,
    live_revisions: list[str],
) -> AsyncIterator[bytes]:
    """Insert `message`, run the pipeline under cancel-and-restart, stream NDJSON lines.

    Args:
        local_now: The visitor's own clock, forwarded into graph state - the only clock
            any past/upcoming/horizon judgement in this turn is made against.
        live_revisions: Every revision this session publishes. Read by the caller,
            before streaming begins, so a store that could not be read fails the
            request outright instead of being reported to the patient as a corpus with
            no answer for them.

    Raises: TurnPipelineError propagated from `run_pipeline`'s task, if the pipeline
        failed.

    Cancels any still-running generation for `chat` before starting this one; yields a
    `cancelled` line instead of a reply if this turn is itself superseded before it
    completes. Yields a `silent` line, and nothing else, when the assistant may not
    speak in this conversation.
    """
    queue: asyncio.Queue[ChatTokenEvent | ChatDoneEvent | None] = asyncio.Queue()
    escalation = EscalationRequests()

    with bind_turn_id() as turn_id:

        async def launch(
            history_rows: list[Message], patient_message: Message
        ) -> "asyncio.Task[None]":
            """Start this turn's graph, returning the task that runs it.

            Registers the task before returning, and is called with the chat's lock
            held: a staff post cannot slip between this turn passing the gate and its
            generation becoming cancellable, which would leave a reply nothing could
            stop.
            """
            bursts = history.exclude_silent_window(
                history.split_into_bursts([*history_rows, patient_message])
            )
            reply_to_message_ids = history.derive_reply_to_message_ids(bursts)
            # Fires unconditionally, before the cancellable graph task below even
            # exists - unlike intent.classified/turn.completed, not gated on the turn
            # completing (research.md #8), and ahead of classification rather than only
            # ahead of generation.
            get_logger().info(
                "turn.message_received",
                message=history.trailing_question(bursts),
                message_ids_unified=reply_to_message_ids,
            )

            # The ambient facts only. Which tools a node may call is that node's own
            # declaration, made beside it in `agent/graph.py` - this side has no
            # business deciding what the booking step is allowed to reach for.
            tool_context = ToolContext(
                channel=scheduling_channel,
                settings=get_settings(),
                session_id=chat.session_id,
                patient_id=chat.patient_id,
                local_now=local_now,
                escalation=escalation,
            )

            async def run_pipeline() -> None:
                """Run this turn's graph, queue its events, persist the reply.

                Raises: TurnPipelineError propagated from `answer_faq_node`.
                """
                answer_parts: list[str] = []
                done_event: ChatDoneEvent | None = None
                try:
                    async for event in run_turn(
                        qdrant_client,
                        voyage_client,
                        anthropic_client,
                        bursts,
                        reply_to_message_ids,
                        live_revisions,
                        escalation=escalation,
                        patient_name=chat.patient_name or _PATIENT_PLACEHOLDER_NAME,
                        local_now=local_now,
                        tool_context=tool_context,
                    ):
                        queue.put_nowait(event)
                        if isinstance(event, ChatTokenEvent):
                            answer_parts.append(event.text)
                        elif isinstance(event, ChatDoneEvent):
                            done_event = event

                    # Stored only once the pipeline completes successfully (abstention
                    # included), and only if a newer message hasn't already superseded
                    # this one (FR-015, research.md #3/#9).
                    #
                    # Deregistered here rather than after the writes below, because
                    # those take the chat's lock: a staff post takes that lock first and
                    # only then asks for a cancellation, so a turn still registered
                    # while queued on the lock would be a cancellation waiting on the
                    # very lock its canceller holds.
                    reply = (
                        done_event
                        if done_event is not None and clear_if_current(chat.id, task)
                        else None
                    )
                    await _persist_outcome(
                        chat,
                        patient_message.id,
                        reply_to_message_ids,
                        escalation,
                        reply,
                        "".join(answer_parts),
                    )
                except TurnPipelineError as exc:
                    logger = get_logger()
                    logger.error(
                        "turn.error",
                        pipeline_step=exc.pipeline_step,
                        error_detail=str(exc.cause),
                    )
                    dependency = _CRITICAL_DEPENDENCY_BY_STEP.get(exc.pipeline_step)
                    if dependency is not None:
                        logger.critical(
                            "critical.dependency_unreachable",
                            dependency=dependency,
                            error_detail=str(exc.cause),
                        )
                    raise
                except Exception as exc:
                    get_logger().error(
                        "turn.error", pipeline_step="unknown", error_detail=str(exc)
                    )
                    raise
                finally:
                    queue.put_nowait(None)
                    clear_if_current(chat.id, task)

            task: asyncio.Task[None] = asyncio.create_task(run_pipeline())
            await register_and_cancel_previous(chat.id, turn_id, task)
            return task

        task: asyncio.Task[None] | None = None
        # Pinned, because the section below commits (the patient message's insert) and
        # the lock it holds lives on the connection, not on the transaction. An
        # engine-bound session would hand that connection back at the commit and take
        # the lock with it - see `pinned_session`.
        async with pinned_session() as db_session:
            # Serializes this whole section per chat: without it, a concurrent sibling
            # message's history read can miss a message whose insert hasn't committed
            # yet - and a staff post could land between this turn passing the gate and
            # its generation being registered, leaving a reply nothing could cancel.
            await chat_repository.lock_chat(db_session, chat.id)
            try:
                # Read inside the lock, in the same section that inserts the message: a
                # staff post landing between the read and the insert would otherwise
                # produce a message answered by an assistant that had already been
                # silenced. This is also the only point that provably precedes
                # classification, retrieval, every tool call and every generation call.
                state = await chat_repository.get_conversation_state(
                    db_session, chat.id, chat.session_id
                )
                history_rows = await chat_repository.list_messages(db_session, chat.id)
                # Inserted synchronously, as soon as it's validated - before generation
                # starts (research.md #3). Reuses `turn_id` as its id (research.md #4).
                patient_message = await chat_repository.create_message(
                    db_session,
                    id=turn_id,
                    chat_id=chat.id,
                    sender=MessageSender.PATIENT,
                    content=message,
                )
                silenced_by = _silenced_by(state)
                if silenced_by is None:
                    task = await launch(history_rows, patient_message)
                else:
                    # Kept, not rejected, and marked with the reason nothing answered
                    # it - which is also the signal a later turn reads to know it must
                    # not answer it retroactively. No registry is built and no graph is
                    # constructed: the requirement is not that no reply is stored, it is
                    # that no call is made.
                    await chat_repository.set_attention_mark(
                        db_session,
                        chat.id,
                        chat.session_id,
                        patient_message.id,
                        AttentionMark.UNANSWERED,
                    )
                    await chat_repository.mark_attention(
                        db_session, chat.id, chat.session_id
                    )
                    get_logger().info(
                        "message.unanswered",
                        chat_id=chat.id,
                        message_id=patient_message.id,
                        silenced_by=silenced_by,
                    )
            finally:
                await chat_repository.release_chat_lock(db_session, chat.id)

        if task is None:
            yield (ChatSilentEvent().model_dump_json() + "\n").encode()
            return

        while True:
            item = await queue.get()
            if item is None:
                break
            yield (item.model_dump_json() + "\n").encode()

        if task.cancelled():
            yield (ChatCancelledEvent().model_dump_json() + "\n").encode()
            return
        exc = task.exception()
        if exc is not None:
            raise exc


@router.post("/chat")
async def post_chat(chat_request: ChatRequest, request: Request) -> StreamingResponse:
    """Send a message to one chat and receive the streamed reply.

    Raises: HTTPException 404 if there is no session cookie, or `chat_id` belongs to
        another session.
    """
    qdrant_client = request.app.state.qdrant_client
    voyage_client = get_voyage_client(request)
    anthropic_client = request.app.state.anthropic_client
    scheduling_channel = request.app.state.scheduling_channel

    chat = await _resolve_chat(request, chat_request.chat_id)
    # Read before the StreamingResponse exists, because once streaming has begun the
    # status line is already sent and a failure can only be a truncated body. An empty
    # result is a session that has added nothing and abstains; a read that failed is an
    # unreachable dependency and must never be presented as the first.
    try:
        async with session_factory() as db_session:
            live_revisions = await faq_repository.live_revisions(
                db_session, chat.session_id
            )
    except Exception as exc:
        get_logger().critical(
            "critical.dependency_unreachable",
            dependency="postgres",
            error_detail=str(exc),
        )
        raise HTTPException(
            status_code=503,
            detail="the clinic's documents could not be read; nothing was answered",
        ) from exc
    # A chat created while scheduling was unreachable has no patient yet. Retrying here
    # is what lets it acquire one on any later turn rather than staying degraded until
    # the visitor happens to create a new chat. A failure is not fatal: the turn still
    # runs, and the booking tools report themselves unavailable.
    await provision_patient(scheduling_channel, chat)

    return StreamingResponse(
        _event_stream(
            qdrant_client,
            voyage_client,
            anthropic_client,
            scheduling_channel,
            chat_request.message,
            chat,
            chat_request.local_now,
            live_revisions,
        ),
        media_type="application/x-ndjson",
    )
