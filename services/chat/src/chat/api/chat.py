"""`POST`/`GET /chat` — NDJSON streaming + history endpoints."""

import asyncio
from collections.abc import AsyncIterator
from typing import cast

from anthropic import AsyncAnthropic
from fastapi import APIRouter, Request
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
from chat.api.dependencies import get_voyage_client
from chat.api.session_cookie import read_session_id, set_session_cookie
from chat.core.correlation import bind_turn_id
from chat.core.logging import get_logger
from chat.db.session import session_factory
from chat.domain.models import MessageSender
from chat.domain.schemas import (
    ChatCancelledEvent,
    ChatDoneEvent,
    ChatHistoryResponse,
    ChatRequest,
    ChatTokenEvent,
    MessageOut,
)
from chat.rag.retriever import TurnPipelineError
from chat.repositories import chat_repository

router = APIRouter()

# Pipeline steps backed by an FR-015-scoped dependency (qdrant/anthropic_api) - not
# "embedding" (Voyage) or "groundedness" (pure computation), per spec.md Assumptions.
_CRITICAL_DEPENDENCY_BY_STEP = {"retrieval": "qdrant", "generation": "anthropic_api"}


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


async def _event_stream(
    qdrant_client: AsyncQdrantClient,
    voyage_client: VoyageAsyncClient,
    anthropic_client: AsyncAnthropic,
    message: str,
    session_id: str,
) -> AsyncIterator[bytes]:
    """Insert `message`, run the pipeline under cancel-and-restart, stream NDJSON lines.

    Raises: TurnPipelineError propagated from `run_pipeline`'s task, if the pipeline
        failed.

    Cancels any still-running generation for `session_id`'s chat before starting this
    one; yields a `cancelled` line instead of a reply if this turn is itself superseded
    before it completes.
    """
    with bind_turn_id() as turn_id:
        async with session_factory() as db_session:
            # Serializes this whole section per session_id: without it, two
            # concurrent first messages for one session can each see "no chat yet"
            # and create two `Chat` rows, or a second concurrent message's history
            # read can miss a sibling message whose insert hasn't committed yet.
            await chat_repository.lock_session(db_session, session_id)
            try:
                chat = await chat_repository.get_or_create_chat_for_session(
                    db_session, session_id
                )
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
                    await chat_repository.unlock_session(db_session, session_id)
                except SQLAlchemyError:
                    await db_session.rollback()
                    await chat_repository.unlock_session(db_session, session_id)

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
                    content = (
                        "".join(answer_parts)
                        if done_event.grounded
                        else (done_event.message or "")
                    )
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
            except asyncio.CancelledError:
                raise
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
    """Ask a question and receive a streamed, grounded (or abstaining) answer."""
    qdrant_client = request.app.state.qdrant_client
    voyage_client = get_voyage_client(request)
    anthropic_client = request.app.state.anthropic_client

    session_id, is_new_session = await _resolve_session_id(request)

    response = StreamingResponse(
        _event_stream(
            qdrant_client,
            voyage_client,
            anthropic_client,
            chat_request.message,
            session_id,
        ),
        media_type="application/x-ndjson",
    )
    if is_new_session:
        set_session_cookie(response, session_id)
    return response


@router.get("/chat")
async def get_chat(request: Request) -> ChatHistoryResponse:
    """Return the visitor's current chat, in chronological order.

    Read-only - never creates a session or chat; a missing/unrecognized cookie or a
    session with no chat yet returns an empty history rather than an error.
    """
    session_id = read_session_id(request)
    if session_id is None:
        return ChatHistoryResponse(messages=[])

    async with session_factory() as db_session:
        session_row = await chat_repository.get_session(db_session, session_id)
        if session_row is None:
            return ChatHistoryResponse(messages=[])
        chat = await chat_repository.get_chat_for_session(db_session, session_row.id)
        if chat is None:
            return ChatHistoryResponse(messages=[])
        messages = await chat_repository.list_messages(db_session, chat.id)

    return ChatHistoryResponse(
        messages=[MessageOut.model_validate(m) for m in messages]
    )


@router.delete("/chat", status_code=204)
async def delete_chat(request: Request) -> None:
    """Permanently delete the visitor's current chat and start fresh.

    A no-op (still `204`) if there's no current chat to delete. Does **not** delete
    the session or touch the `visitdoc_session_id` cookie.
    """
    session_id = read_session_id(request)
    if session_id is None:
        return

    async with session_factory() as db_session:
        session_row = await chat_repository.get_session(db_session, session_id)
        if session_row is None:
            return
        chat = await chat_repository.get_chat_for_session(db_session, session_row.id)
        if chat is None:
            return
        await chat_repository.delete_chat(db_session, chat.id)
