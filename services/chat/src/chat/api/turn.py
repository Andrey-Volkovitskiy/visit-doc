"""`POST /chat` — the streaming turn endpoint."""

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from enum import StrEnum

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


class ChatVanishedError(RuntimeError):
    """Raised when the chat a turn resolved is no longer the session's by the time the
    turn's message is written - deleted in between the two.
    """


class ReplyOutcome(StrEnum):
    """What became of the reply a turn generated.

    Four values because the caller owes the patient a different ending for each, or owes
    the log a different account of it, and a `bool` would fold them together. A write
    that failed is none of these - it raises, so a turn whose outcome is unknown never
    reports one.

    Only `STORED` ends the turn in a reply. Every other member - including any added
    later - ends it in `cancelled`, so the ending a turn gives the patient is decided
    by one comparison rather than by a branch per member that a new one could miss.
    """

    # The reply is in the thread, and only now may the patient be shown it.
    STORED = "stored"
    # A person took the conversation over while this turn was finishing. Nothing was
    # written, and the patient sees the turn end without an answer (FR-013a).
    TAKEN_OVER = "taken_over"
    # This turn produced no reply to store - it was superseded before it completed.
    NOT_GENERATED = "not_generated"
    # The chat was deleted while this turn was running, so the reply had nowhere to go.
    # Distinct from `TAKEN_OVER` because no person did anything: recording it as a
    # takeover would put a staff member in a conversation nobody ever touched.
    CHAT_GONE = "chat_gone"


# The write's own three answers in this turn's terms. `NOT_GENERATED` has no counterpart
# here on purpose: it is the outcome of a turn that attempted no write at all.
_OUTCOME_BY_REPLY_WRITE = {
    chat_repository.ReplyWrite.STORED: ReplyOutcome.STORED,
    chat_repository.ReplyWrite.TAKEN_OVER: ReplyOutcome.TAKEN_OVER,
    chat_repository.ReplyWrite.CHAT_GONE: ReplyOutcome.CHAT_GONE,
}


def _log_chat_vanished(chat_id: str, message_id: str) -> None:
    """Record that the chat this turn was answering was deleted while it ran.

    `info`, not `error`: a conversation deleted mid-turn is a race this turn is built to
    lose safely - nothing was written and nothing is inconsistent - and it must not read
    in the log like the pipeline failures around it.
    """
    get_logger().info("turn.chat_vanished", chat_id=chat_id, message_id=message_id)


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
    *,
    on_stored: Callable[[ChatDoneEvent], None],
) -> ReplyOutcome:
    """Write what this turn produced and what it decided, under the chat's lock.

    Args:
        done_event: The completion event whose reply is to be stored, or None when
            there is none to store - the pipeline did not complete, or a newer message
            superseded this turn.
        answer: The text streamed to the patient, stored whenever `done_event` carries
            no message of its own.
        on_stored: Called with `done_event` the moment its insert has committed, and
            not called at all otherwise. Called from inside the locked section on
            purpose: it is what shows the patient the reply, the insert having
            succeeded is the whole of what makes that safe, and deferring it until
            this returns would let a failure in the escalation writes below strand a
            reply that is in the thread and was never delivered.

    Returns: what became of the reply - see `ReplyOutcome`. The caller needs it to
        decide whether the patient may be shown one.

    Raises: the store's own error if a write could not be completed.

    Holds the lock a staff post takes, for the whole of both writes: the reply and the
    transition either both precede a staff member taking the conversation over or both
    follow it, never one of each. Without it the transition lands on top of the clears
    that post just made, and the conversation falls silent again with a person already
    in it.

    The lock is not on its own enough to decide the reply, though - it is taken *after*
    this turn has deregistered itself, so a staff member can post in between and find no
    generation to cancel. That is why the reply's write carries the takeover guard in
    its own `WHERE` rather than relying on having got here first.

    Waits for the lock however long it takes. The wait is bounded in practice by what
    every holder does under it - a handful of statements, never a person's typing - and
    a bounded wait would have to answer with either a reply nobody stored or writes
    nobody serialized, both worse than the wait itself.
    """
    # Pinned, because the section commits and the lock lives on the connection rather
    # than on the transaction - see `pinned_session`.
    async with pinned_session() as db_session:
        await chat_repository.lock_chat(db_session, chat.id)
        try:
            outcome = ReplyOutcome.NOT_GENERATED
            if done_event is not None:
                # `message` is set only when there is no streamed text to show, which
                # today is the FAQ abstention case. `grounded` stays NULL for a
                # booking-only reply: it was never retrieved against, so it is neither
                # grounded nor abstaining.
                write = await chat_repository.create_assistant_reply_unless_taken_over(
                    db_session,
                    id=str(ULID()),
                    chat_id=chat.id,
                    session_id=chat.session_id,
                    answering_message_id=patient_message_id,
                    content=done_event.message or answer,
                    grounded=done_event.grounded,
                    citations=[c.model_dump() for c in done_event.citations],
                    reply_to_message_ids=reply_to_message_ids,
                )
                outcome = _OUTCOME_BY_REPLY_WRITE[write]
                if outcome is ReplyOutcome.STORED:
                    on_stored(done_event)
                if outcome is ReplyOutcome.CHAT_GONE:
                    _log_chat_vanished(chat.id, patient_message_id)
            # The insert above evaluated the takeover guard in its own `WHERE`, so its
            # answer is the one to act on here; only a turn that wrote nothing has to
            # ask, and it asks once. Nothing can change the answer in between - every
            # gesture that takes a conversation writes under this same lock.
            taken_over = (
                outcome is ReplyOutcome.TAKEN_OVER
                if done_event is not None
                else await chat_repository.taken_over_since(
                    db_session, chat.id, chat.session_id, patient_message_id
                )
            )
            # Last, and after the reply above has been both written and shown: the turn
            # runs to its end and the conversation transitions at the end of it, so a
            # mixed-intent message whose halves both ran delivers both before anything
            # is silenced.
            await apply_escalation(
                db_session,
                chat.id,
                chat.session_id,
                patient_message_id,
                escalation,
                taken_over=taken_over,
            )
            return outcome
        finally:
            await chat_repository.release_chat_lock_after_commit(db_session, chat.id)


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
        failed before this turn's ending was sent.

    Cancels any still-running generation for `chat` before starting this one; yields a
    `cancelled` line instead of a reply if this turn is itself superseded before it
    completes, if a person took the conversation over before its reply was stored, or if
    the chat was deleted while the turn ran. Yields a `silent` line, and nothing else,
    when the assistant may not speak in this conversation.

    Every turn ends in exactly one terminal line - `done`, `cancelled` or `silent` - or
    in a broken stream, and never in a stream that simply stops: a client that saw no
    ending leaves the turn in progress on the patient's screen for as long as they stay
    in the conversation. `done` is sent only once the reply behind it has committed,
    and nothing after a terminal line may change or add to it.
    """
    queue: asyncio.Queue[ChatTokenEvent | ChatDoneEvent | ChatCancelledEvent | None] = (
        asyncio.Queue()
    )
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

                Tokens are queued as they arrive, but the `done` event is queued only
                once the reply behind it has committed - the patient is never shown a
                finished reply the thread does not hold. Exactly one terminal event is
                queued either way: `done` when the reply was stored, `cancelled` for
                every other ending, so no completed turn leaves the patient's pane
                waiting on a line that never comes.
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
                        if isinstance(event, ChatDoneEvent):
                            # Held back rather than streamed as it arrives: `done` is
                            # the patient being shown a finished reply, and whether
                            # this one is a reply the thread will hold is not settled
                            # until the registry check and the writes below.
                            done_event = event
                            continue
                        if isinstance(event, ChatTokenEvent):
                            queue.put_nowait(event)
                            answer_parts.append(event.text)
                            continue
                        # Neither shape this turn's contract declares. `run_turn` casts
                        # what `graph.astream` yields rather than checking it, so a
                        # third shape arrives here as an assertion nobody made good.
                        # Dropped rather than forwarded, because a line the client
                        # cannot name is read by its parser as a completed turn and
                        # would end the turn on an empty reply; dropped rather than
                        # raised, because a reply that generated fine is not worth
                        # discarding over one event nothing here can interpret. Logged,
                        # so a drift in what the graph writes is not silent.
                        get_logger().error(
                            "turn.unknown_event", event_type=type(event).__name__
                        )

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
                    outcome = await _persist_outcome(
                        chat,
                        patient_message.id,
                        reply_to_message_ids,
                        escalation,
                        reply,
                        "".join(answer_parts),
                        # `done` goes on the wire from inside the write, the instant
                        # the reply's insert commits: streamed only once it is stored,
                        # so the reply on screen and the reply in the thread are the
                        # same one, and no later failure can leave a stored reply
                        # undelivered.
                        on_stored=queue.put_nowait,
                    )
                    if outcome is not ReplyOutcome.STORED:
                        # The other half of the pair, so exactly one terminal event
                        # leaves this turn however it ended: `done` above when the reply
                        # was written, `cancelled` here for every other outcome - a
                        # person taking the conversation, a supersede, or whatever a
                        # later member names. To the patient they are the same thing,
                        # a turn that ends without an answer, and the tokens already
                        # sent are not one.
                        queue.put_nowait(ChatCancelledEvent())
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
        try:
            # Pinned, because the section below commits (the patient message's insert)
            # and the lock it holds lives on the connection, not on the transaction. An
            # engine-bound session would hand that connection back at the commit and
            # take the lock with it - see `pinned_session`.
            async with pinned_session() as db_session:
                # Serializes this whole section per chat: without it, a concurrent
                # sibling message's history read can miss a message whose insert hasn't
                # committed yet - and a staff post could land between this turn passing
                # the gate and its generation being registered, leaving a reply nothing
                # could cancel.
                await chat_repository.lock_chat(db_session, chat.id)
                try:
                    # Read inside the lock, in the same section that inserts the
                    # message: a staff post landing between the read and the insert
                    # would otherwise produce a message answered by an assistant that
                    # had already been silenced. This is also the only point that
                    # provably precedes classification, retrieval, every tool call and
                    # every generation call.
                    state = await chat_repository.get_conversation_state(
                        db_session, chat.id, chat.session_id
                    )
                    history_rows = await chat_repository.list_messages(
                        db_session, chat.id
                    )
                    # Inserted synchronously, as soon as it's validated - before
                    # generation starts (research.md #3). Reuses `turn_id` as its id
                    # (research.md #4).
                    patient_message = await chat_repository.create_message(
                        db_session,
                        id=turn_id,
                        chat_id=chat.id,
                        session_id=chat.session_id,
                        sender=MessageSender.PATIENT,
                        content=message,
                    )
                    if patient_message is None:
                        # The chat stopped being this session's between being resolved
                        # and being written into - deleted, in that window. Nothing was
                        # stored, so there is nothing to answer. Raised rather than
                        # returned, so the lock's release and this session's close both
                        # unwind before the ending below is sent.
                        raise ChatVanishedError(
                            f"chat {chat.id} vanished before its message was stored"
                        )
                    silenced_by = _silenced_by(state)
                    if silenced_by is None:
                        task = await launch(history_rows, patient_message)
                    else:
                        # Kept, not rejected, and marked with the reason nothing
                        # answered it - which is also the signal a later turn reads to
                        # know it must not answer it retroactively. No registry is built
                        # and no graph is constructed: the requirement is not that no
                        # reply is stored, it is that no call is made.
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
                    await chat_repository.release_chat_lock_after_commit(
                        db_session, chat.id
                    )
        except ChatVanishedError:
            # An expected race, not a failure, and caught here because letting it out
            # cannot end the turn well: this response's status line went out before the
            # body did, so a raise from a body iterator reaches the patient as a dropped
            # connection and the log as an ASGI crash - indistinguishable from the
            # pipeline genuinely breaking. `cancelled` is the true ending, the same one
            # the reply's own write earns when the chat goes in the later window.
            _log_chat_vanished(chat.id, turn_id)
            yield (ChatCancelledEvent().model_dump_json() + "\n").encode()
            return

        if task is None:
            yield (ChatSilentEvent().model_dump_json() + "\n").encode()
            return

        streamed_terminal = False
        while True:
            item = await queue.get()
            if item is None:
                break
            yield (item.model_dump_json() + "\n").encode()
            if not isinstance(item, ChatTokenEvent):
                streamed_terminal = True

        if task.cancelled():
            if not streamed_terminal:
                yield (ChatCancelledEvent().model_dump_json() + "\n").encode()
            return
        # Collected before it is decided what to do with it, so a failure nobody ends up
        # raising is still taken off the task rather than warned about at collection.
        exc = task.exception()
        if streamed_terminal:
            # The turn is settled and the patient has been told how it ended, so nothing
            # here may say otherwise. A failure after that point - an escalation write,
            # a lock release - is recorded where it happened and goes no further:
            # breaking the stream now would replace an answer already in the thread with
            # an error, and hand the patient back a question that has been answered.
            return
        if exc is not None:
            raise exc
        # No terminal event, and no failure to account for the absence: the pipeline
        # completed without settling a reply. The patient is owed an ending regardless -
        # a stream that just stops leaves the turn in progress on their screen - and
        # `cancelled` is the true one, since nothing was stored to show them.
        yield (ChatCancelledEvent().model_dump_json() + "\n").encode()


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
