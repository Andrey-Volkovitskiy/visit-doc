"""`POST /chat` — the streaming turn endpoint."""

import asyncio
import contextvars
import re
from collections.abc import AsyncIterator, Callable
from contextlib import ExitStack
from datetime import datetime
from enum import StrEnum
from typing import Annotated

import grpc
from anthropic import AsyncAnthropic
from fastapi import APIRouter, Depends, HTTPException, Request
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
from chat.api.dependencies import get_rerank_client, get_voyage_client
from chat.api.provisioning import provision_patient
from chat.api.session_cookie import read_session_id
from chat.clients.anthropic_failure import AnthropicFailure, classify_failure
from chat.core.config import get_settings
from chat.core.correlation import bind_turn_id
from chat.core.errors import TurnPipelineError
from chat.core.logging import get_logger
from chat.db.session import pinned_session, session_factory
from chat.domain.models import (
    AttentionMark,
    Chat,
    EscalationReason,
    Message,
    MessageSender,
)
from chat.domain.schemas import (
    ChatCancelledEvent,
    ChatDoneEvent,
    ChatRequest,
    ChatSilentEvent,
    ChatTokenEvent,
)
from chat.observability import UNTRACED, TraceDirective, TurnOutcome, turn_trace
from chat.repositories import chat_repository, faq_repository

router = APIRouter()


def _always(_exc: Exception) -> bool:
    """Every failure of this step is its dependency being unreachable."""
    return True


def _model_api_was_unreachable(exc: Exception) -> bool:
    """Whether a failed generation call proves the model API never served it."""
    return classify_failure(exc) is AnthropicFailure.UNREACHABLE


# Pipeline steps backed by an FR-015-scoped dependency (qdrant/anthropic_api) - not
# "embedding" (Voyage) or "persistence" (a write the store refused is not the store
# being unreachable), per spec.md Assumptions.
#
# Reranking is absent for a different reason: it raises no pipeline step at all. Its
# failures are absorbed where they happen - the turn answers from the similarity
# survivors and records `answered_unreranked` - so nothing about a reranker outage ever
# reaches this classification. Its own `faq.reranking_unavailable` event is where an
# operator sees it.
#
# Each carries the test a failure of that step must pass before it may be called an
# outage, because naming the step is not enough on its own. "generation" is wrapped
# around a bare `except Exception` at three sites, so a rotated key's 401, an exhausted
# rate limit and a schema 400 all arrive tagged "generation" - each of them the API
# answering rather than the API being gone, and each of them an alert an operator
# learns to ignore. It shares that test with the classifier's own failure handler
# (`chat.clients.anthropic_failure`), so the two sites cannot disagree about what one
# status means. "retrieval" has no such test: it is left reporting every failure as an
# outage, which is what it has always done.
_CRITICAL_DEPENDENCY_BY_STEP: dict[str, tuple[str, Callable[[Exception], bool]]] = {
    "retrieval": ("qdrant", _always),
    "generation": ("anthropic_api", _model_api_was_unreachable),
}


def _unreachable_dependency_of(exc: TurnPipelineError) -> str | None:
    """Name the dependency `exc` proves unreachable, or None if it proves none."""
    entry = _CRITICAL_DEPENDENCY_BY_STEP.get(exc.pipeline_step)
    if entry is None:
        return None
    dependency, proves_outage = entry
    return dependency if proves_outage(exc.cause) else None


# Used in the booking prompt until a chat has a real patient. The scheduler owns
# patient names, so this side never invents one that could then disagree with it.
_PATIENT_PLACEHOLDER_NAME = "the patient"

# The headers an eval run sends to file a turn's trace under the run and the case. They
# are read off the request rather than declared as parameters, so the published schema
# of a patient-facing route gains no eval-only fields.
_TRACE_HEADER = "X-VisitDoc-Trace"
# The one value `X-VisitDoc-Trace` takes: a request can turn its own turn's tracing off,
# and never on.
_TRACE_OFF = "off"
_EVAL_RUN_HEADER = "X-VisitDoc-Eval-Run"
_EVAL_CASE_HEADER = "X-VisitDoc-Eval-Case"
# A run id is a ULID as the harness writes one: 26 upper-case Crockford characters,
# the first no higher than 7.
_EVAL_RUN_ID = re.compile(r"[0-7][0-9A-HJKMNP-TV-Z]{25}")
# A case id of the golden set: `G-<family letter>-<nn>`.
_EVAL_CASE_ID = re.compile(r"G-[a-z]-[0-9]{2}")


class ChatVanishedError(RuntimeError):
    """Raised when the chat a turn resolved is no longer the session's by the time the
    turn's message is written - deleted in between the two.
    """


class ReplyOutcome(StrEnum):
    """What became of the reply a turn generated.

    Five values because the caller owes the patient a different ending for each, or owes
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
    # The write declined for a reason this build has no name for - a `ReplyWrite` member
    # added on the other side of the translation below. Its own value rather than one of
    # the four above, each of which claims something particular happened: a person, a
    # supersede, a deletion. This one claims only that the reply was not stored, which
    # is the whole of what is known about it.
    DECLINED = "declined"


# The write's declining answers in this turn's terms. `STORED` is deliberately absent:
# it is answered by the comparison in `_outcome_of` instead, so the one member the
# patient's reply hangs on cannot be missing from a mapping. `NOT_GENERATED` has no
# counterpart here either - it is the outcome of a turn that attempted no write at all.
_OUTCOME_BY_REPLY_WRITE = {
    chat_repository.ReplyWrite.TAKEN_OVER: ReplyOutcome.TAKEN_OVER,
    chat_repository.ReplyWrite.CHAT_GONE: ReplyOutcome.CHAT_GONE,
}


def _outcome_of(write: chat_repository.ReplyWrite) -> ReplyOutcome:
    """Say what a reply's write became, in this turn's terms.

    Returns: the outcome the write names, or `DECLINED` for a `ReplyWrite` member this
        build carries no outcome for.

    Total by construction, and it has to be: this runs between the insert committing and
    the patient being shown what it wrote, so a lookup that could miss would turn a
    stored reply into an error on their screen. `STORED` is decided by comparison rather
    than by the mapping, and every member the mapping does not carry answers `DECLINED`
    rather than raising - whatever a later one names, it is not a stored reply, and the
    turn ends the same way for all of them.
    """
    if write is chat_repository.ReplyWrite.STORED:
        return ReplyOutcome.STORED
    outcome = _OUTCOME_BY_REPLY_WRITE.get(write)
    if outcome is None:
        # A drift in what the store answers rather than a failure of this turn, so it is
        # recorded and not raised - but recorded, because an outcome nothing here can
        # name is a translation that has fallen behind the store it translates.
        get_logger().error("turn.unknown_reply_write", reply_write=write)
        return ReplyOutcome.DECLINED
    return outcome


class VanishedWindow(StrEnum):
    """Which half of a turn the deletion of its chat landed in.

    Carried because the two are different events for whoever reads the log - one lost
    the turn of a message that is committed, the other never wrote the message at all -
    and nothing else in the entry separates them: a turn's message reuses its turn id,
    so the only id either window had to offer was the same string.
    """

    # The patient's message never landed: the insert found no chat to write into, so no
    # row with this turn's id exists and none ever will.
    PATIENT_MESSAGE = "patient_message"
    # The message committed and no answer followed it: the chat went after the turn
    # deregistered itself and before it could store a reply - whether the reply's own
    # insert was what found the chat gone, or the turn had no reply to insert.
    REPLY = "reply"


def _log_chat_vanished(
    chat_id: str, vanished_before: VanishedWindow, message_id: str | None
) -> None:
    """Record that the chat this turn was answering was deleted while it ran.

    Args:
        message_id: The patient message this turn committed, or None in the window
            where none was written - the field names a row that exists or names
            nothing, never an id nothing was ever stored under.

    `info`, not `error`: a conversation deleted mid-turn is a race this turn is built to
    lose safely - nothing was written and nothing is inconsistent - and it must not read
    in the log like the pipeline failures around it.
    """
    get_logger().info(
        "turn.chat_vanished",
        chat_id=chat_id,
        vanished_before=vanished_before,
        message_id=message_id,
    )


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

    Applies no escalation at all when the chat is gone - whether the reply's own write
    reported it, or, for a turn that settled no reply, the takeover read did: every
    write it would make is scoped to a conversation that no longer exists, and a record
    of the calls raised in such a turn would read as a person having been fetched into
    one. `turn.chat_vanished` is what that turn leaves in the log, and the whole of it.

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
                # today is the all-abstained turn and the collapse. `request_outcomes`
                # stays NULL for a booking-only reply: it was never retrieved against,
                # so it had no request with a gate to stop at - which is a different
                # thing from a half that ran and produced nothing, and is why the null
                # is carried through rather than flattened to a list.
                write = await chat_repository.create_assistant_reply_unless_taken_over(
                    db_session,
                    id=str(ULID()),
                    chat_id=chat.id,
                    session_id=chat.session_id,
                    answering_message_id=patient_message_id,
                    content=done_event.message or answer,
                    request_outcomes=(
                        [o.model_dump() for o in done_event.request_outcomes]
                        if done_event.request_outcomes is not None
                        else None
                    ),
                    reply_to_message_ids=reply_to_message_ids,
                )
                # Read off the write's own answer, ahead of anything derived from it:
                # the row has committed by now, and what shows the patient their reply
                # must not be able to fail. A lookup here that a later `ReplyWrite`
                # member was missing from would raise between the commit and the
                # delivery, leaving the reply in the thread and an error in its place.
                if write is chat_repository.ReplyWrite.STORED:
                    on_stored(done_event)
                outcome = _outcome_of(write)
                if outcome is ReplyOutcome.CHAT_GONE:
                    _log_chat_vanished(
                        chat.id, VanishedWindow.REPLY, patient_message_id
                    )
                    # Nothing below has anything left to act on: the escalation writes
                    # are all scoped to a chat that no longer exists, and a record of
                    # calls to staff would name a conversation nobody can be handed.
                    # The entry above is the whole account of a turn whose chat went.
                    return outcome
            # The insert above evaluated the takeover guard in its own `WHERE`, so its
            # answer is the one to act on here; only a turn whose write did not answer
            # it has to ask, and it asks once. Nothing can change the answer in between
            # - every gesture that takes a conversation writes under this same lock.
            #
            # A `DECLINED` write did not answer it: it says the reply was not stored and
            # nothing beyond that, so reading it as "and nobody took the conversation"
            # would be a guess - one that re-silences a patient against the very staff
            # member handling them when it is wrong.
            answered_by_the_write = (
                done_event is not None and outcome is not ReplyOutcome.DECLINED
            )
            if answered_by_the_write:
                taken_over = outcome is ReplyOutcome.TAKEN_OVER
            else:
                read = await chat_repository.get_takeover_since(
                    db_session, chat.id, chat.session_id, patient_message_id
                )
                if read is chat_repository.TakeoverRead.CHAT_GONE:
                    # The same ending the reply's own write earns in this window, and
                    # for the same reason: a turn that settled no reply is no likelier
                    # to have a conversation left to escalate. Read off the takeover
                    # itself rather than paid for with a lookup of its own - a chat
                    # that is gone returns no row for the predicate to be read from.
                    _log_chat_vanished(
                        chat.id, VanishedWindow.REPLY, patient_message_id
                    )
                    return ReplyOutcome.CHAT_GONE
                taken_over = read is chat_repository.TakeoverRead.TAKEN_OVER
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


async def _settle_the_failure(
    chat: Chat,
    patient_message_id: str,
    escalation: EscalationRequests,
    task: "asyncio.Task[None]",
    *,
    reply_delivered: bool,
) -> None:
    """Apply what this turn's failure leaves owing, calling staff for it if it owes one.

    Args:
        reply_delivered: Whether the patient was shown a reply before the failure -
            `run_pipeline`'s latch, true only once the reply's insert has committed and
            its `done` has been taken by the stream.

    A turn that ended *without* a reply is a message nobody answered, so a person is
    fetched for it - the weakest of the seven claims on one, and one of the three that
    do not silence: the thing that broke may already be working again, and the patient
    may keep asking while staff follow up.

    A turn that ended *with* one records no failure at all. The only writes that can
    fail after the reply is on the stream are the escalation's own - the lock's release
    swallows its own - so a failure past that point is one the patient was answered in
    spite of, and `assistant_failed` would
    fetch a person to a conversation that got its answer - the mark reading, to the
    staff member who opens it, as a patient left hanging. What such a turn does still
    owe is the escalation that failure interrupted, which is why this runs at all
    rather than being skipped for a delivered reply: the hole in the corpus that called
    staff before the write broke is still a hole, and its mark still has to land. The
    call below is that second attempt, and for a turn that collected nothing it writes
    nothing - `apply_escalation` makes that judgement, from the same collector.

    Recorded into the turn's own collector rather than written directly, so a turn that
    had already called staff for something stronger keeps that cause and that mark: the
    precedence decides, and a failure never outranks a hole in the corpus, a request
    the assistant may not serve, or a patient who needs a person.

    Deregisters `task` first, and that ordering is the invariant this call rests on: the
    write below queues on the chat's lock, a staff post takes that lock *before* it asks
    for a cancellation, and `pg_advisory_lock` has no timeout - so a turn still
    registered here would be a cancellation waiting on the very lock its canceller
    holds. The success path deregisters ahead of its own writes for exactly this
    reason; a failed turn owes the same. Idempotent: `run_pipeline`'s `finally` clears
    the same task again, and a task that is no longer current clears nothing.

    Never raises. The call to staff is what the patient is owed *after* the turn broke,
    so a failure here may not replace the account of what actually went wrong; it is
    logged as `turn.staff_call_failed` and the original error goes on propagating.
    """
    clear_if_current(chat.id, task)
    if not reply_delivered:
        escalation.record(EscalationReason.ASSISTANT_FAILED)
    try:
        await _persist_outcome(
            chat,
            patient_message_id,
            [],
            escalation,
            None,
            "",
            on_stored=_no_reply_to_deliver,
        )
    except Exception as exc:  # noqa: BLE001 - see the docstring: the original error wins
        get_logger().error(
            "turn.staff_call_failed",
            chat_id=chat.id,
            error_detail=str(exc),
        )


def _no_reply_to_deliver(_done: ChatDoneEvent) -> None:
    """Refuse to deliver a reply on a turn that produced none.

    `_persist_outcome` calls `on_stored` only for a reply it actually stored, and a
    failed turn hands it none - so reaching this is a change that started storing one,
    which the patient must not then be shown as the answer to a turn that broke.
    """
    raise RuntimeError("a failed turn has no reply to deliver")


async def _event_stream(
    qdrant_client: AsyncQdrantClient,
    voyage_client: VoyageAsyncClient,
    rerank_client: VoyageAsyncClient,
    anthropic_client: AsyncAnthropic,
    scheduling_channel: grpc.aio.Channel,
    message: str,
    chat: Chat,
    local_now: datetime,
    live_revisions: list[str],
    directive: TraceDirective,
) -> AsyncIterator[bytes]:
    """Insert `message`, run the pipeline under cancel-and-restart, stream NDJSON lines.

    Args:
        local_now: The visitor's own clock, forwarded into graph state - the only clock
            any past/upcoming/horizon judgement in this turn is made against.
        live_revisions: Every revision this session publishes. Read by the caller,
            before streaming begins, so a store that could not be read fails the
            request outright instead of being reported to the patient as a corpus with
            no answer for them.
        directive: What the request asked of the turn's trace.

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
            message_text = history.trailing_question(bursts)
            get_logger().info(
                "turn.message_received",
                message=message_text,
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

                Raises: TurnPipelineError propagated from `answer_faq_node`, or
                    wrapping a write of this turn's that the store could not complete.

                Tokens are queued as they arrive, but the `done` event is queued only
                once the reply behind it has committed - the patient is never shown a
                finished reply the thread does not hold. Exactly one terminal event is
                queued either way: `done` when the reply was stored, `cancelled` for
                every other ending, so no completed turn leaves the patient's pane
                waiting on a line that never comes.

                The whole run is the turn's trace, rooted here, and the root records
                whether the run completed, failed or was cancelled.
                """
                answer_parts: list[str] = []
                done_event: ChatDoneEvent | None = None
                # One thing, and only this thing: the reply committed *and* reached the
                # patient's stream. Set by `_deliver_reply` and by nothing else, never
                # cleared, and read only once the pipeline has broken - so what it
                # answers there is "was this turn a message nobody answered", which is
                # the question `_settle_the_failure` exists to ask.
                reply_delivered = False

                def _deliver_reply(event: ChatDoneEvent) -> None:
                    """Show the patient their reply, and record that they were shown it.

                    Latched after the queue has taken the event rather than before: a
                    `done` that never reached the stream is a reply the patient did not
                    get, and a turn that breaks afterwards still owes them a person.
                    """
                    nonlocal reply_delivered
                    queue.put_nowait(event)
                    reply_delivered = True

                with ExitStack() as ending:
                    # Registered before the trace is opened, so the stream is ended and
                    # this task deregistered however it ends - a trace that could not be
                    # opened included, which would otherwise leave the patient's stream
                    # waiting on a sentinel nothing sends. They run last-in first-out,
                    # after the root has closed: the sentinel, then a second
                    # deregistration for the paths that never reached the one below - a
                    # pipeline step that raised, or this task being cancelled mid-graph.
                    # Harmless after it: the registry has nothing of this task left to
                    # remove, and a newer turn's entry is not this task's to clear.
                    ending.callback(clear_if_current, chat.id, task)
                    ending.callback(queue.put_nowait, None)
                    root = ending.enter_context(
                        turn_trace(
                            turn_id,
                            chat_id=chat.id,
                            session_id=chat.session_id,
                            directive=directive,
                            input=message_text,
                        )
                    )
                    # Only a turn whose root is recorded has a trace to report; the
                    # SDK would name one for any other turn too, and it would never
                    # arrive.
                    if root.trace_id is not None:
                        get_logger().info("turn.traced", trace_id=root.trace_id)
                    try:
                        async for event in run_turn(
                            qdrant_client,
                            voyage_client,
                            rerank_client,
                            anthropic_client,
                            bursts,
                            reply_to_message_ids,
                            chat.session_id,
                            live_revisions,
                            escalation=escalation,
                            patient_name=chat.patient_name or _PATIENT_PLACEHOLDER_NAME,
                            local_now=local_now,
                            tool_context=tool_context,
                        ):
                            if isinstance(event, ChatDoneEvent):
                                # Held back rather than streamed as it arrives: `done`
                                # is the patient being shown a finished reply, and
                                # whether this one is a reply the thread will hold is
                                # not settled until the registry check and the writes
                                # below.
                                done_event = event
                                continue
                            if isinstance(event, ChatTokenEvent):
                                queue.put_nowait(event)
                                answer_parts.append(event.text)
                                continue
                            # Neither shape this turn's contract declares. `run_turn`
                            # casts what `graph.astream` yields rather than checking it,
                            # so a third shape arrives here as an assertion nobody made
                            # good. Dropped rather than forwarded, because a line the
                            # client cannot name is read by its parser as a completed
                            # turn and would end the turn on an empty reply; dropped
                            # rather than raised, because a reply that generated fine is
                            # not worth discarding over one event nothing here can
                            # interpret. Logged, so a drift in what the graph writes is
                            # not silent.
                            get_logger().error(
                                "turn.unknown_event", event_type=type(event).__name__
                            )

                        # Stored only once the pipeline completes successfully
                        # (abstention included), and only if a newer message hasn't
                        # already superseded this one (FR-015, research.md #3/#9).
                        #
                        # Deregistered here rather than after the writes below, because
                        # those take the chat's lock: a staff post takes that lock first
                        # and only then asks for a cancellation, so a turn still
                        # registered while queued on the lock would be a cancellation
                        # waiting on the very lock its canceller holds - and
                        # `pg_advisory_lock` has no timeout to end that wait.
                        #
                        # Its own statement, and not a term in the expression below: a
                        # turn that settled no reply queues on the same lock as one that
                        # did, so it may not be the one turn whose deregistration a
                        # short-circuit skips. The answer still decides the reply -
                        # False means a newer turn superseded this one, whose reply is
                        # the one the thread is owed, not this one's.
                        still_current = clear_if_current(chat.id, task)
                        reply = done_event if still_current else None
                        try:
                            outcome = await _persist_outcome(
                                chat,
                                patient_message.id,
                                reply_to_message_ids,
                                escalation,
                                reply,
                                "".join(answer_parts),
                                # `done` goes on the wire from inside the write, the
                                # instant the reply's insert commits: streamed only once
                                # it is stored, so the reply on screen and the reply in
                                # the thread are the same one, and no later failure can
                                # leave a stored reply undelivered. Through
                                # `_deliver_reply` rather than straight onto the queue,
                                # so the turn knows afterwards that the patient was
                                # answered.
                                on_stored=_deliver_reply,
                            )
                        except Exception as exc:
                            # Tagged with the step that actually failed, by the same
                            # mechanism every pipeline step uses. Untagged, these
                            # reached the catch-all below as `pipeline_step="unknown"`,
                            # which reads as a graph node blowing up and sends an
                            # operator to the pipeline rather than to the store. And
                            # once `done` is on the wire the stream returns normally, so
                            # this entry is the only record the writes failed at all.
                            raise TurnPipelineError("persistence", exc) from exc
                        if outcome is not ReplyOutcome.STORED:
                            # The other half of the pair, so exactly one terminal event
                            # leaves this turn however it ended: `done` above when the
                            # reply was written, `cancelled` here for every other
                            # outcome - a person taking the conversation, a supersede,
                            # or whatever a later member names. To the patient they are
                            # the same thing, a turn that ends without an answer, and
                            # the tokens already sent are not one. The trace says what
                            # the patient was sent: a takeover that landed after the
                            # deregistration above raises nothing into this turn, and
                            # is still not a turn that completed.
                            queue.put_nowait(ChatCancelledEvent())
                            root.set_outcome(TurnOutcome.CANCELLED)
                        else:
                            root.set_outcome(TurnOutcome.COMPLETED)
                    except asyncio.CancelledError:
                        # Superseded, not broken: recorded as its own ending, and
                        # passed on untouched.
                        root.set_outcome(TurnOutcome.CANCELLED)
                        raise
                    except TurnPipelineError as exc:
                        root.set_outcome(TurnOutcome.FAILED)
                        logger = get_logger()
                        logger.error(
                            "turn.error",
                            pipeline_step=exc.pipeline_step,
                            error_detail=str(exc.cause),
                        )
                        dependency = _unreachable_dependency_of(exc)
                        if dependency is not None:
                            logger.critical(
                                "critical.dependency_unreachable",
                                dependency=dependency,
                                error_detail=str(exc.cause),
                            )
                        # After the log line, so the turn's account of what broke is on
                        # the wire before the recovery's. A cancellation reaches
                        # neither: it is a `BaseException`, and a superseded turn is not
                        # a failure - the newer message is being answered, and marking
                        # this one would send a staff member to a conversation nothing
                        # is wrong with.
                        await _settle_the_failure(
                            chat,
                            patient_message.id,
                            escalation,
                            task,
                            reply_delivered=reply_delivered,
                        )
                        raise
                    except Exception as exc:
                        root.set_outcome(TurnOutcome.FAILED)
                        get_logger().error(
                            "turn.error", pipeline_step="unknown", error_detail=str(exc)
                        )
                        # The same for a failure this build cannot name: what the
                        # patient is owed does not depend on the code having anticipated
                        # the way it broke.
                        await _settle_the_failure(
                            chat,
                            patient_message.id,
                            escalation,
                            task,
                            reply_delivered=reply_delivered,
                        )
                        raise

            # The turn runs in a context of its own: a request that asked not to be
            # traced sets the flag there and only there, so every span the turn opens
            # - on any task it spawns, or thread it starts through `asyncio.to_thread`
            # - sees it, while this stream, the next turn and every other request
            # never do.
            context = contextvars.copy_context()
            if not directive.traced:
                context.run(UNTRACED.set, True)
            task: asyncio.Task[None] = asyncio.create_task(
                run_pipeline(), context=context
            )
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
            _log_chat_vanished(chat.id, VanishedWindow.PATIENT_MESSAGE, None)
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


def read_trace_directive(request: Request) -> TraceDirective:
    """Read what the request asks of its turn's trace from its headers.

    Resolved as a dependency, so a malformed request is refused before the route does
    anything with it: no message is stored and no turn runs.

    Raises: HTTPException 422 when `X-VisitDoc-Trace` carries anything but `off`
        (trimmed, in any case), when only one of the eval run and eval case headers is
        sent, or when either is malformed.
    """
    trace = request.headers.get(_TRACE_HEADER)
    if trace is not None and trace.strip().lower() != _TRACE_OFF:
        raise HTTPException(
            status_code=422, detail=f"{_TRACE_HEADER} takes only {_TRACE_OFF!r}"
        )
    run_id = request.headers.get(_EVAL_RUN_HEADER)
    case_id = request.headers.get(_EVAL_CASE_HEADER)
    if (run_id is None) != (case_id is None):
        raise HTTPException(
            status_code=422,
            detail=f"{_EVAL_RUN_HEADER} and {_EVAL_CASE_HEADER} are sent together",
        )
    if run_id is not None and _EVAL_RUN_ID.fullmatch(run_id) is None:
        raise HTTPException(
            status_code=422, detail=f"{_EVAL_RUN_HEADER} is not a run id"
        )
    if case_id is not None and _EVAL_CASE_ID.fullmatch(case_id) is None:
        raise HTTPException(
            status_code=422, detail=f"{_EVAL_CASE_HEADER} is not a case id"
        )
    return TraceDirective(
        traced=trace is None, eval_run_id=run_id, eval_case_id=case_id
    )


@router.post("/chat")
async def post_chat(
    chat_request: ChatRequest,
    request: Request,
    directive: Annotated[TraceDirective, Depends(read_trace_directive)],
) -> StreamingResponse:
    """Send a message to one chat and receive the streamed reply.

    Raises: HTTPException 404 if there is no session cookie, or `chat_id` belongs to
        another session; HTTPException 422 when the request's tracing headers are
        malformed (`read_trace_directive`).
    """
    qdrant_client = request.app.state.qdrant_client
    voyage_client = get_voyage_client(request)
    rerank_client = get_rerank_client(request)
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
            rerank_client,
            anthropic_client,
            scheduling_channel,
            chat_request.message,
            chat,
            chat_request.local_now,
            live_revisions,
            directive,
        ),
        media_type="application/x-ndjson",
    )
