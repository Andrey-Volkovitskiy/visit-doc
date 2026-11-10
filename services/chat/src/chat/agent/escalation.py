"""Calling a person: one implementation, four callers, one application point.

Four things can decide a conversation needs a person - the classifier labelling the
message as one of six causes that call one (an urgent condition, evident distress, a
request for a human, a booking for someone else, a request the assistant is not
authorized to serve, or a follow-up to a request already with staff), the model calling
`escalate_to_staff`, the FAQ path abstaining, and a booking tool failing - and none of
them writes the transition.

Calling staff never silences the assistant. Every cause marks the message that caused
it and puts the conversation in the staff queue, and the patient is told so by a fixed
sentence (`HANDOFF_TEXT`) while the assistant goes on answering whatever else they ask.
Only a staff member's own gesture - a post, or the console's switch - stops it
replying, and that is the pause, not this module.

Each caller *records a request* into one per-turn `EscalationRequests` collector -
every applicable cause, not only the strongest, so the log keeps what precedence
discards (spec 009 FR-047) - and `turn.py` applies the collected result once, after the
graph has completed (FR-006).

The collector is a plain mutable object rather than a LangGraph state key, deliberately:
the specialists can run concurrently, and concurrent writes to one state key are
exactly what LangGraph rejects. Appending to a shared object is not a state write, and
the resolution below is a precedence over a *set*, so the order two branches happened to
record in cannot change what the patient's message ends up marked with.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from chat.core.logging import get_logger
from chat.domain.models import (
    OPEN_REQUEST_MARKS,
    SOMBRE_MARKS,
    AttentionMark,
    EscalationReason,
    Message,
    MessageSender,
)
from chat.repositories import chat_repository

# Strongest claim on a person first. Safety outranks everything: a patient describing
# an urgent condition, then one in evident distress. A patient asking for a human *is* a
# person wanting a person; a booking the assistant may not make and a request it is not
# authorized to serve both need a person to do the thing; a follow-up adds to something
# a person already holds; a corpus gap is a hole in the clinic's own documents; a
# failure is a thing to retry, and the one whose "they can just try again" is the
# weaker claim to give up (research #6, spec 009 FR-047). This orders the *mark* and
# the one notice a turn carries.
_PRECEDENCE: tuple[EscalationReason, ...] = (
    EscalationReason.URGENT_CONDITION,
    EscalationReason.DISTRESS,
    EscalationReason.PATIENT_ASKED_FOR_PERSON,
    EscalationReason.BOOKING_FOR_ANOTHER_PERSON,
    EscalationReason.NOT_AUTHORIZED,
    EscalationReason.FORWARDED_FOLLOW_UP,
    EscalationReason.CORPUS_COULD_NOT_ANSWER,
    EscalationReason.ASSISTANT_FAILED,
)


def in_precedence_order(reasons: Iterable[EscalationReason]) -> list[EscalationReason]:
    """Return `reasons` strongest claim first, with each cause appearing once.

    The one place any caller may order causes. A second ordering written down
    elsewhere is a second answer to "which cause is this turn's": the router would
    hand off for one cause while `message_mark` recorded another, and nothing would
    say the two had disagreed.
    """
    raised = set(reasons)
    return [reason for reason in _PRECEDENCE if reason in raised]


# Every `EscalationReason` is also an `AttentionMark` of the same name - the mark
# records on the message what the reason records on the conversation, so the two cannot
# disagree about why a person was called.
_MARK_BY_REASON = {reason: AttentionMark(reason.value) for reason in _PRECEDENCE}

# What the visitor is told when a person is fetched because they asked for one. Fixed
# text rather than a generated sentence: there is nothing here for a model to decide,
# and the two things it must not do - promise a time, or imply the assistant has handed
# the conversation over and stopped - are exactly what a generated one could get wrong.
#
# Deliberately not the same string as `escalate_to_staff`'s tool result, which is
# written for the model reading it back rather than for the person reading it.
HANDOFF_MESSAGE = (
    "Sure. I've passed this to a member of the clinic's staff. "
    "They'll follow up with you here shortly. In the meantime, I can still help "
    "with anything else."
)

# What a patient is told when a message adds to a request staff already hold - so a
# detail sent after the hand-off is not one they have to wonder about.
FOLLOW_UP_MESSAGE = (
    "I've added this to the request already with our staff, so it won't get lost. "
    "They'll follow up with you here."
)

# One constant per cause the patient is told about, in one table beside the
# precedence. A corpus gap and a failure have none: a gap is named by the reply that
# could not answer it, and a failure has no reply to carry one.
#
# Every text is appended to the reply verbatim, never paraphrased: a turn that hands
# off and nothing else sends the text alone, and a turn that also answered something
# sends it after the answer. What each must convey is the contract; the wording is a
# copy decision. None of them promises a time, and none claims the assistant will
# handle the thing after all.
#
# The urgent one does not judge whether this is an emergency: the assistant does not
# triage (FR-044), so the only safe sentence is the one that is right under every
# reading of the message.
HANDOFF_TEXT: dict[EscalationReason, str] = {
    EscalationReason.PATIENT_ASKED_FOR_PERSON: HANDOFF_MESSAGE,
    EscalationReason.NOT_AUTHORIZED: (
        "I'm not authorized to handle that request, so I've forwarded it to our staff. "
        "They'll follow up with you shortly. Feel free to ask if you need help with "
        "anything else in the meantime."
    ),
    EscalationReason.URGENT_CONDITION: (
        "This appears to be an emergency, I've immediately notified the clinic staff. "
        "A member of our team will follow up with you here as soon as possible."
    ),
    EscalationReason.DISTRESS: (
        "I'm sorry you're going through this. I've passed your message to a member of "
        "the clinic's staff, and a person will follow up with you here."
    ),
    EscalationReason.BOOKING_FOR_ANOTHER_PERSON: (
        "Appointments in this chat are booked for you, so I can't arrange one for "
        "someone else. I've passed this to the clinic's staff and a member of the "
        "team will arrange it with you."
    ),
    EscalationReason.FORWARDED_FOLLOW_UP: FOLLOW_UP_MESSAGE,
}

# What separates a notice from the reply it follows.
NOTICE_SEPARATOR = "\n\n"


def notice_for(reasons: Iterable[EscalationReason]) -> EscalationReason | None:
    """Return the cause whose notice a turn raising `reasons` carries, or None.

    The strongest cause that has a text, and only that one: two notices about one
    hand-off read as two hand-offs, and the stronger one already says that staff have
    the message. None when no cause raised has a text - nothing, a corpus gap, a
    failure.
    """
    return next(
        (reason for reason in in_precedence_order(reasons) if reason in HANDOFF_TEXT),
        None,
    )


def notice_suffix(reply: str, reason: EscalationReason | None) -> str:
    """Return the text that follows `reply` to give the patient `reason`'s notice.

    Empty when there is no notice; the notice alone after an empty reply; otherwise the
    separator and the notice. The one place this is decided, so the token streamed to
    the patient and the text stored and logged are the same characters - a reply the
    model left empty must not be stored opening on a blank line the log does not show.
    """
    if reason is None:
        return ""
    notice = HANDOFF_TEXT[reason]
    return f"{NOTICE_SEPARATOR}{notice}" if reply else notice


def with_notice(reply: str, reason: EscalationReason | None) -> str:
    """Return `reply` with `reason`'s notice appended, or unchanged without one."""
    return f"{reply}{notice_suffix(reply, reason)}"


@dataclass(frozen=True)
class ForwardedRequest:
    """A patient message staff hold and have not answered yet.

    `mark` says why it was forwarded; `text` is the message as the patient wrote it,
    which is what a later message is compared against to decide whether it adds to it.
    """

    text: str
    mark: AttentionMark


def open_requests(messages: Sequence[Message]) -> list[ForwardedRequest]:
    """Return every request in `messages` with staff and still open, oldest first.

    Read off the marks rather than stored beside them: a request is open exactly while
    its message carries one of `OPEN_REQUEST_MARKS`, and a staff message clears those,
    so "staff answered it" and "it is no longer open" are one write, not two to keep in
    step. A stored mark this build does not name is not an open request.
    """
    forwarded: list[ForwardedRequest] = []
    for message in messages:
        if message.sender != MessageSender.PATIENT.value:
            continue
        mark = _OPEN_MARK_BY_VALUE.get(message.attention_mark or "")
        if mark is not None:
            forwarded.append(ForwardedRequest(text=message.content, mark=mark))
    return forwarded


_OPEN_MARK_BY_VALUE = {mark.value: mark for mark in OPEN_REQUEST_MARKS}


# What every generated reply is told while `calls_for_calm` holds. One sentence for
# every prompt that writes to the patient, appended after its own instructions, so the
# small-talk reply, an FAQ answer, a booking reply and a merged reply all keep the same
# register. It names no symptom and quotes nothing: the FAQ answerer is shown no
# conversation, and this does not hand it one.
CALM_TONE = (
    "A message earlier in this conversation described an urgent condition or showed "
    "distress, and a member of the clinic's staff has it. Keep this reply plain and "
    "calm: no cheerfulness, no exclamation marks, no emoji, no pleasantries."
)


def with_calm_tone(system_prompt: str, *, calm: bool) -> str:
    """Return `system_prompt`, followed by `CALM_TONE` when `calm`."""
    return f"{system_prompt}\n\n{CALM_TONE}" if calm else system_prompt


def calls_for_calm(
    forwarded: Sequence[ForwardedRequest], reasons: Iterable[EscalationReason]
) -> bool:
    """Whether everything the assistant says this turn must be plain and calm.

    True while an urgent condition or evident distress is with staff - raised by an
    earlier message and still open, or raised by this one. A cheerful pleasantry right
    after "I can't breathe" is the reply this exists to prevent.
    """
    return any(request.mark in SOMBRE_MARKS for request in forwarded) or any(
        _MARK_BY_REASON[reason] in SOMBRE_MARKS for reason in reasons
    )


class EscalationRequests:
    """Every call to staff raised during one turn, and what they resolve to.

    Mutable and shared: the tool handler reaches it through `ToolContext`, and the
    router and the specialists through the graph's state. Nothing here writes to a
    store - resolving is pure, and `apply_escalation()` is the only writer.
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
        decides the mark, and the log keeps every call - a discarded request still
        happened, and still has to be accounted for.
        """
        return tuple(self._recorded)

    @property
    def message_mark(self) -> AttentionMark | None:
        """The one mark this turn's patient message carries, or None for no call.

        One mark per message, never a set: when a mixed-intent turn raises two calls
        the stronger of the two is what the message carries, and the weaker survives in
        the log rather than being lost.
        """
        ordered = in_precedence_order(self._recorded)
        return _MARK_BY_REASON[ordered[0]] if ordered else None


async def apply_escalation(
    session: AsyncSession,
    chat_id: str,
    session_id: str,
    message_id: str,
    requests: EscalationRequests,
    *,
    taken_over: bool,
) -> None:
    """Apply one turn's collected calls to staff, and record every one of them.

    Args:
        message_id: The patient message this turn answered - the one that caused the
            call, and the only message a turn can mark. Bound by the caller rather than
            supplied per request, so a model cannot address another message any more
            than it can address another conversation.
        taken_over: Whether a person has taken the conversation over since `message_id`
            arrived. Supplied rather than read here because the caller has already
            established it under the same lock - the reply's own insert evaluates it in
            its `WHERE` - and one turn's two writes must act on one answer, not on two
            reads of it.

    Does nothing at all when nothing was recorded, which is the ordinary turn, and
    nothing either when `taken_over` - the calls are recorded, and none of them applied.

    Must be called holding `chat_id`'s advisory lock. That lock is what makes
    `taken_over` still true by the time the writes below act on it: every gesture that
    takes a conversation - a staff message, the console's switch - writes under it too,
    so none of them can land between the caller establishing it and this returning.
    """
    mark = requests.message_mark
    if mark is None:
        return

    if taken_over:
        # A person answered while this turn was still running. Applying anything now
        # would put the conversation back in the queue they just took it out of.
        _record_unchanged(requests.recorded, chat_id, message_id)
        return

    await chat_repository.set_attention_mark(
        session, chat_id, session_id, message_id, mark
    )
    # Set only if unset: the conversation has been waiting since the first thing that
    # needed a person, and re-stamping would send it to the back of a queue ordered by
    # how long each has waited. The answer says whether this call was the one that put
    # it there, which is the transition `escalation.raised` points at.
    emphasized = await chat_repository.mark_attention(session, chat_id, session_id)
    _record(requests, chat_id, message_id, emphasized=emphasized)


def _record_unchanged(
    reasons: Iterable[EscalationReason], chat_id: str, message_id: str
) -> None:
    """Record every request as having transitioned nothing, best-effort.

    Wrapped whole in a `try`: recording follows a decision and never gates one, and
    there is nowhere left to report a failure of the reporting path itself.
    """
    try:
        logger = get_logger()
        for reason in reasons:
            logger.info(
                "escalation.unchanged",
                chat_id=chat_id,
                requested_reason=reason,
                message_id=message_id,
            )
    except Exception:  # noqa: BLE001, S110 - see the docstring.
        pass


def _record(
    requests: EscalationRequests, chat_id: str, message_id: str, *, emphasized: bool
) -> None:
    """Record one entry per request, best-effort.

    Args:
        emphasized: Whether this turn was the one that put the conversation in the
            queue, rather than finding it waiting there already.

    `escalation.raised` and `escalation.unchanged` are mutually exclusive for one
    request, and that is the point of having both: `escalation.raised` is what a count
    of handoffs is drawn from, and a no-op logged as a raise would over-count every
    conversation that was already waiting. A request is therefore raised only when it
    can point at the transition - the conversation entering the queue - and that
    transition is claimed once, by the strongest cause, which leaves every other
    request unchanged.

    Wrapped whole in a `try`: recording follows a transition and never gates one, so a
    log entry that could not be written cannot un-happen a handoff that already
    occurred.
    """
    try:
        ordered = in_precedence_order(requests.recorded)
        raising = ordered[0] if emphasized and ordered else None
        logger = get_logger()
        raised = False
        unchanged: list[EscalationReason] = []
        for reason in requests.recorded:
            if reason is raising and not raised:
                logger.info(
                    "escalation.raised",
                    chat_id=chat_id,
                    reason=reason,
                    message_id=message_id,
                )
                raised = True
            else:
                unchanged.append(reason)
        _record_unchanged(unchanged, chat_id, message_id)
    except Exception:  # noqa: BLE001, S110 - see the docstring.
        pass
