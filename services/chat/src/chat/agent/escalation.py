"""Calling a person: one implementation, three callers, one application point.

Four things can decide a conversation needs a person - the classifier labelling the
message `call_staff`, the model calling `escalate_to_staff`, the FAQ path abstaining,
and a booking tool failing - and none of them writes the transition. Each *records a
request* into one per-turn `EscalationRequests` collector; `turn.py` applies the
collected result once, after the graph has completed.

Only that shape satisfies both of the requirements that govern this (research #5):

- **FR-001a**: one implementation reachable by several callers, producing the same
  state, the same record and the same reason handling - so the three cannot each write
  their own.
- **FR-006**: the turn runs to completion first and the state takes effect at the end of
  it - so no caller may write the transition at the moment it decides. Within the turn
  that escalates, the assistant still speaks; silence begins with the *next* message.

The collector is a plain mutable object rather than a LangGraph state key, deliberately:
the two specialists can run concurrently, and concurrent writes to one state key are
exactly what LangGraph rejects. Appending to a shared object is not a state write, and
the resolution below is a precedence over a *set*, so the order two branches happened to
record in cannot change what the patient's conversation ends up in.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from chat.core.logging import get_logger
from chat.domain.models import AttentionMark, EscalationReason
from chat.repositories import chat_repository

# Strongest claim on a person first. A patient asking for a human *is* a person wanting
# a person; a corpus gap is a hole in the clinic's own documents; a failure is a thing
# to retry, and the one whose "they can just try again" is the weaker claim to give up
# (research #6).
_PRECEDENCE: tuple[EscalationReason, ...] = (
    EscalationReason.PATIENT_ASKED_FOR_PERSON,
    EscalationReason.CORPUS_COULD_NOT_ANSWER,
    EscalationReason.ASSISTANT_FAILED,
)

# The reasons that stop the assistant replying. `ASSISTANT_FAILED` is absent by
# requirement, not by omission: a failure raises attention without silencing, because
# the thing that broke may already be working again (FR-003d).
_SILENCING = frozenset(
    {
        EscalationReason.PATIENT_ASKED_FOR_PERSON,
        EscalationReason.CORPUS_COULD_NOT_ANSWER,
    }
)

# Every `EscalationReason` is also an `AttentionMark` of the same name - the mark
# records on the message what the reason records on the conversation, so the two cannot
# disagree about why a person was called.
_MARK_BY_REASON = {reason: AttentionMark(reason.value) for reason in _PRECEDENCE}

# What the visitor is told when a person is fetched because they asked for one. Fixed
# text rather than a generated sentence: there is nothing here for a model to decide,
# and the two things it must not do - promise a time, or imply the assistant will keep
# handling this - are exactly what a generated one could get wrong.
#
# Deliberately not the same string as `escalate_to_staff`'s tool result, which is
# written for the model reading it back rather than for the person reading it.
HANDOFF_MESSAGE = (
    "I've passed this to a member of the clinic's staff. They'll reply to you here, "
    "in this conversation."
)


class EscalationRequests:
    """Every call to staff raised during one turn, and what they resolve to.

    Mutable and shared: the tool handler reaches it through `ToolContext`, and the two
    specialists through the graph's state. Nothing here writes to a store - resolving is
    pure, and `apply_escalation()` is the only writer.
    """

    def __init__(self) -> None:
        """Start with no calls to staff recorded for this turn."""
        self._recorded: list[EscalationReason] = []

    def record(self, reason: EscalationReason) -> None:
        """Record that something in this turn decided a person is needed."""
        self._recorded.append(reason)

    @property
    def recorded(self) -> tuple[EscalationReason, ...]:
        """Every request, in the order it arrived.

        Kept in full even where the precedence below discards one: the precedence
        decides the mark, and the log keeps every call (FR-033).
        """
        return tuple(self._recorded)

    @property
    def conversation_reason(self) -> EscalationReason | None:
        """The reason this turn silences the conversation for, or None if it does not.

        None is a real answer rather than an absent one: a turn whose only call was
        `assistant_failed` needs a person without the assistant going quiet.
        """
        return next(
            (
                reason
                for reason in _PRECEDENCE
                if reason in _SILENCING and reason in self._recorded
            ),
            None,
        )

    @property
    def message_mark(self) -> AttentionMark | None:
        """The one mark this turn's patient message carries, or None for no call.

        One mark per message: the spec models it as singular, so when a mixed-intent
        turn raises two calls the stronger is stored and the weaker survives in the log
        (research #6).
        """
        reason = next(
            (reason for reason in _PRECEDENCE if reason in self._recorded), None
        )
        return None if reason is None else _MARK_BY_REASON[reason]


async def apply_escalation(
    session: AsyncSession,
    chat_id: str,
    session_id: str,
    message_id: str,
    requests: EscalationRequests,
) -> None:
    """Apply one turn's collected calls to staff, and record every one of them.

    Args:
        message_id: The patient message this turn answered - the one that caused the
            call, and the only message a turn can mark. Bound by the caller rather than
            supplied per request, so a model cannot address another message any more
            than it can address another conversation.

    Does nothing at all when nothing was recorded, which is the ordinary turn.
    """
    mark = requests.message_mark
    if mark is None:
        return

    await chat_repository.set_attention_mark(session, message_id, mark)
    # Set only if unset: the conversation has been waiting since the first thing that
    # needed a person, and re-stamping would send it to the back of a queue ordered by
    # how long each has waited.
    await chat_repository.mark_attention(session, chat_id, session_id)

    silencing = requests.conversation_reason
    transitioned = False
    existing_reason: str | None = None
    if silencing is not None:
        # The guard is in the write's own `WHERE`, so a second call cannot overwrite the
        # reason that first silenced the conversation (FR-007).
        transitioned = await chat_repository.set_escalated(
            session, chat_id, session_id, silencing
        )
        existing_reason = (
            silencing.value
            if transitioned
            else await _existing_reason(session, chat_id, session_id)
        )

    _record(requests, chat_id, message_id, silencing, transitioned, existing_reason)


async def _existing_reason(
    session: AsyncSession, chat_id: str, session_id: str
) -> str | None:
    """Return the reason a conversation is already escalated for, for the record."""
    state = await chat_repository.get_conversation_state(session, chat_id, session_id)
    return None if state is None else state.escalation_reason


def _record(
    requests: EscalationRequests,
    chat_id: str,
    message_id: str,
    silencing: EscalationReason | None,
    transitioned: bool,
    existing_reason: str | None,
) -> None:
    """Record one entry per request, best-effort.

    `escalation.raised` and `escalation.unchanged` are mutually exclusive for one
    request, and that is the point of having both: SC-010 counts escalation records
    against conversations actually silenced, and a no-op logged as a raise would
    over-count every one of them.

    Wrapped whole in a `try`: recording follows a transition and never gates one, so a
    log entry that could not be written cannot un-happen a handoff that already occurred
    (FR-034).
    """
    try:
        logger = get_logger()
        raised = False
        for reason in requests.recorded:
            if reason not in _SILENCING:
                # It transitioned something - the conversation became emphasized - but
                # it did not silence, and `silenced` is the field that says so.
                logger.info(
                    "escalation.raised",
                    chat_id=chat_id,
                    reason=reason,
                    message_id=message_id,
                    silenced=False,
                )
            elif transitioned and not raised and reason is silencing:
                logger.info(
                    "escalation.raised",
                    chat_id=chat_id,
                    reason=reason,
                    message_id=message_id,
                    silenced=True,
                )
                raised = True
            else:
                # Either the conversation was already escalated, or this was the
                # weaker of two silencing calls in one turn. Both reasons are carried,
                # because the point of the record is that the second did not overwrite
                # the first.
                logger.info(
                    "escalation.unchanged",
                    chat_id=chat_id,
                    requested_reason=reason,
                    existing_reason=existing_reason,
                    message_id=message_id,
                )
    except Exception:  # noqa: BLE001, S110 - see the docstring: recording follows a
        # transition and never gates one, and there is nowhere left to report a failure
        # of the reporting path itself.
        pass
