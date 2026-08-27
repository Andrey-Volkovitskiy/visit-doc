"""`POST /chat` — the streaming turn endpoint."""

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from typing import cast

import grpc
from anthropic import AsyncAnthropic
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from qdrant_client import AsyncQdrantClient
from sqlalchemy.exc import SQLAlchemyError
from ulid import ULID
from voyageai.client_async import AsyncClient as VoyageAsyncClient

from chat.agent import history
from chat.agent.generation_registry import (
    clear_if_current,
    register_and_cancel_previous,
)
from chat.agent.graph import run_turn
from chat.agent.tools.registry import ToolContext, ToolRegistry
from chat.agent.tools.scheduling_tools import SCHEDULING_TOOLS
from chat.api.dependencies import get_voyage_client
from chat.api.provisioning import provision_patient
from chat.api.session_cookie import read_session_id
from chat.core.config import get_settings
from chat.core.correlation import bind_turn_id
from chat.core.logging import get_logger
from chat.db.session import session_factory
from chat.domain.models import Chat, MessageSender
from chat.domain.schemas import (
    ChatCancelledEvent,
    ChatDoneEvent,
    ChatRequest,
    ChatTokenEvent,
)
from chat.rag.retriever import TurnPipelineError
from chat.repositories import chat_repository

router = APIRouter()

# Pipeline steps backed by an FR-015-scoped dependency (qdrant/anthropic_api) - not
# "embedding" (Voyage) or "groundedness" (pure computation), per spec.md Assumptions.
_CRITICAL_DEPENDENCY_BY_STEP = {"retrieval": "qdrant", "generation": "anthropic_api"}

# Used in the booking prompt until a chat has a real patient. The scheduler owns
# patient names, so this side never invents one that could then disagree with it.
_PATIENT_PLACEHOLDER_NAME = "the patient"


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


async def _event_stream(
    qdrant_client: AsyncQdrantClient,
    voyage_client: VoyageAsyncClient,
    anthropic_client: AsyncAnthropic,
    scheduling_channel: grpc.aio.Channel,
    message: str,
    chat: Chat,
    local_now: datetime,
) -> AsyncIterator[bytes]:
    """Insert `message`, run the pipeline under cancel-and-restart, stream NDJSON lines.

    Args:
        local_now: The visitor's own clock, forwarded into graph state - the only clock
            any past/upcoming/horizon judgement in this turn is made against.

    Raises: TurnPipelineError propagated from `run_pipeline`'s task, if the pipeline
        failed.

    Cancels any still-running generation for `chat` before starting this one; yields a
    `cancelled` line instead of a reply if this turn is itself superseded before it
    completes.
    """
    with bind_turn_id() as turn_id:
        async with session_factory() as db_session:
            # Serializes this whole section per chat: without it, a concurrent sibling
            # message's history read can miss a message whose insert hasn't committed
            # yet.
            await chat_repository.lock_chat(db_session, chat.id)
            try:
                history_rows = await chat_repository.list_messages(db_session, chat.id)
                # Inserted synchronously, as soon as it's validated - before
                # generation starts (research.md #3). Reuses `turn_id` as its id
                # (research.md #4).
                patient_message = await chat_repository.create_message(
                    db_session,
                    id=turn_id,
                    chat_id=chat.id,
                    sender=MessageSender.PATIENT,
                    content=message,
                )
            finally:
                # A statement above may have aborted `db_session`'s transaction, in
                # which case Postgres refuses this unlock too (`InFailedSQL-
                # TransactionError`), masking the real error and leaking the
                # advisory lock on this pooled connection. Only roll back - and
                # retry - on that actual failure path: an unconditional rollback
                # would also expire `chat`/`history_rows` (loaded above) despite
                # `expire_on_commit=False`, which governs `commit()` but not
                # `rollback()`, forcing a doomed refresh once `db_session` closes
                # below and they're used detached.
                try:
                    await chat_repository.unlock_chat(db_session, chat.id)
                except SQLAlchemyError:
                    await db_session.rollback()
                    await chat_repository.unlock_chat(db_session, chat.id)

        history_rows = [*history_rows, patient_message]
        bursts = history.split_into_bursts(history_rows)
        reply_to_message_ids = history.derive_reply_to_message_ids(bursts)
        # Fires unconditionally, before the cancellable graph task below even exists -
        # unlike intent.classified/turn.completed, not gated on the turn completing
        # (research.md #8). Moved here (out of answer_faq(), where it used to live) so
        # it precedes classification too, not just generation.
        get_logger().info(
            "turn.message_received",
            message=cast(str, history.to_claude_messages(bursts)[-1]["content"]),
            message_ids_unified=reply_to_message_ids,
        )

        registry = ToolRegistry(
            SCHEDULING_TOOLS,
            ToolContext(
                channel=scheduling_channel,
                settings=get_settings(),
                session_id=chat.session_id,
                patient_id=chat.patient_id,
                local_now=local_now,
            ),
        )

        queue: asyncio.Queue[ChatTokenEvent | ChatDoneEvent | None] = asyncio.Queue()

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
                    patient_name=chat.patient_name or _PATIENT_PLACEHOLDER_NAME,
                    local_now=local_now,
                    registry=registry,
                ):
                    queue.put_nowait(event)
                    if isinstance(event, ChatTokenEvent):
                        answer_parts.append(event.text)
                    elif isinstance(event, ChatDoneEvent):
                        done_event = event

                # Inserted only once the pipeline completes successfully (abstention
                # included), and only if a newer message hasn't already superseded
                # this one (FR-015, research.md #3/#9).
                if done_event is not None and clear_if_current(chat.id, task):
                    # `message` is set only when there is no streamed text to show,
                    # which today is the FAQ abstention case. `grounded` stays NULL for
                    # a booking-only reply: it was never retrieved against, so it is
                    # neither grounded nor abstaining.
                    content = done_event.message or "".join(answer_parts)
                    citations_payload = [c.model_dump() for c in done_event.citations]
                    async with session_factory() as insert_session:
                        await chat_repository.create_message(
                            insert_session,
                            id=str(ULID()),
                            chat_id=chat.id,
                            sender=MessageSender.ASSISTANT,
                            content=content,
                            grounded=done_event.grounded,
                            citations=citations_payload,
                            reply_to_message_ids=reply_to_message_ids,
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
        ),
        media_type="application/x-ndjson",
    )
