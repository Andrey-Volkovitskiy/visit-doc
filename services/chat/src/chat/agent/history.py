"""Message rows -> conversation bursts -> alternating Claude `messages` history."""

from collections.abc import Sequence
from typing import Any, Literal, cast

from anthropic.types import MessageParam
from pydantic import BaseModel

from chat.domain.models import AttentionMark, Message, MessageSender

_Role = Literal["user", "assistant"]

# The one mark that records a *consequence* of silence rather than a call to staff: a
# message that arrived while the assistant could not reply and that nothing answered.
_UNANSWERED = AttentionMark.UNANSWERED


def split_into_bursts(history: list[Message]) -> list[list[Message]]:
    """Group `history` into contiguous same-side runs, in order.

    Returns: `history`, partitioned in order into contiguous same-side runs - each
        inner list is one burst.

    A turn has two sides: the patient, and whoever replies to them, so "same side"
    means same patient-or-not status, not same exact `sender` - e.g. an assistant
    message immediately followed by a staff one is still one burst.
    """
    bursts: list[list[Message]] = []
    for message in history:
        is_patient = message.sender == MessageSender.PATIENT
        same_side = bursts and (bursts[-1][-1].sender == MessageSender.PATIENT) == (
            is_patient
        )
        if same_side:
            bursts[-1].append(message)
        else:
            bursts.append([message])
    return bursts


def exclude_silent_window(bursts: list[list[Message]]) -> list[list[Message]]:
    """Split the trailing burst so a turn answers only what arrived after the silence.

    Returns: `bursts`, with its trailing patient-sided burst split at the last message
        carrying the `unanswered` mark - the marked prefix becoming its own preceding
        burst, and only the remainder being the trailing burst a turn answers.

    Messages that arrived while the assistant could not reply were kept, and a person
    was meant to answer them; going back and answering them once a pause elapses would
    reply to a question the patient has moved on from, or that staff already handled.
    They stay in `bursts` as context, which is why this splits rather than drops.

    The mark is the signal because it is the only one still available on a later turn:
    a pause's deadline is gone by the time one runs, and an escalation never had one.
    Returns `bursts` unchanged when there is nothing marked - the ordinary case.
    """
    if not bursts:
        return bursts
    trailing = bursts[-1]
    if trailing[0].sender != MessageSender.PATIENT:
        return bursts

    last_silent = next(
        (
            index
            for index in range(len(trailing) - 1, -1, -1)
            if trailing[index].attention_mark == _UNANSWERED
        ),
        None,
    )
    # The second condition cannot arise within a turn - the trailing message is the one
    # just inserted, and the gate returned before this point if it was marked - and is
    # here so the function is total for any history it is handed.
    if last_silent is None or last_silent == len(trailing) - 1:
        return bursts
    return [*bursts[:-1], trailing[: last_silent + 1], trailing[last_silent + 1 :]]


def derive_reply_to_message_ids(bursts: list[list[Message]]) -> list[str]:
    """Return the ids of every message in `bursts`'s trailing burst, in order.

    Precondition: `bursts`'s last burst is patient-sided - i.e. `history` already
    ends with the current, not-yet-answered patient message. Not enforced with a
    runtime assert/guard here - covered by a test for the empty-history case instead.
    """
    if not bursts:
        return []
    return [m.id for m in bursts[-1]]


# What a specialist is told about the messages it must read but must not answer. One
# sentence in one place: both specialists render it, and two copies would drift.
SILENT_WINDOW_NOTE = (
    "Earlier messages a member of the clinic's staff was handling. Read them for "
    "context only: do not answer them and do not act on them, because a person has "
    "already dealt with them."
)


def silent_window(bursts: list[list[Message]]) -> list[Message]:
    """Return the messages `exclude_silent_window` held back from this turn's answer.

    Returns: the held-back burst, or an empty list when this turn follows no silence.

    Identified by the shape rather than by re-reading the marks: `split_into_bursts`
    never produces two consecutive patient-sided bursts, so a pair of them exists only
    where `exclude_silent_window` split one - which makes the earlier of the two the
    silent window by construction.
    """
    if len(bursts) < 2:
        return []
    is_patient = MessageSender.PATIENT
    if bursts[-2][0].sender != is_patient or bursts[-1][0].sender != is_patient:
        return []
    return bursts[-2]


def render_silent_window(silenced: list[Message]) -> str:
    """Render the silent window for a prompt, or the empty string when there is none.

    Rendered into the entry a specialist builds for itself rather than left to
    `to_claude_messages`: that render rejoins the window with the message this turn
    answers, and a model reading one merged entry has nothing to tell them apart by.
    """
    if not silenced:
        return ""
    return SILENT_WINDOW_NOTE + "\n" + "\n\n".join(m.content for m in silenced)


def trailing_question(bursts: list[list[Message]]) -> str:
    """Return the text of the trailing burst - the message(s) this turn answers.

    Deliberately not read off `to_claude_messages`'s last entry. That render rejoins two
    consecutive patient-sided bursts into one, because the Messages API forbids
    consecutive same-role entries - and the split `exclude_silent_window` makes is
    exactly such a pair. Reading the question off the render would therefore put a
    message from the silent window straight back into the question this turn answers,
    which is the one thing that must never happen to it.

    Returns the empty string for an empty history.
    """
    if not bursts:
        return ""
    return "\n\n".join(message.content for message in bursts[-1])


def bound_to_last_n_turns(
    bursts: list[list[Message]], n: int = 5
) -> list[list[Message]]:
    """Truncate `bursts` to its last `n` complete turns.

    A turn is one contiguous patient-message burst immediately followed by one reply
    burst - not necessarily all from the same sender. A trailing, still-unanswered
    patient burst at the end of `bursts` isn't itself a turn yet - it doesn't count
    against the `n` budget, and is always kept.
    """
    trailing = bursts[-1:]
    complete = bursts[:-1]

    # Each complete turn is exactly one (patient burst, reply burst) pair - keep the
    # last `n` pairs, i.e. the last `2 * n` bursts.
    kept_bursts = complete[-(2 * n) :] if n > 0 else []

    return kept_bursts + trailing


def to_claude_messages(bursts: list[list[Message]]) -> list[MessageParam]:
    """Build the alternating `user`/`assistant` list for a Claude `messages` call.

    One `MessageParam` per burst, in order - a burst's several messages are joined by
    a blank line into that one entry's content, satisfying the Messages API's strict
    alternation requirement without needing to merge anything itself (`bursts` is
    already partitioned into same-side runs by `split_into_bursts`). Role is derived
    from the burst's side (patient vs. not), not from checking each message's
    `sender == ASSISTANT` - a `staff`-authored burst (grouped onto the non-patient side
    by `split_into_bursts`) still correctly maps to Claude role `"assistant"` (the
    clinic's side of the conversation) this way.

    Two consecutive same-role bursts are rejoined into one entry. `split_into_bursts`
    never produces them, but `exclude_silent_window` does - it splits one patient burst
    in two - and the Messages API requires strict alternation, so the rejoining happens
    here rather than being a rule every caller of that function has to remember.
    """
    entries: list[MessageParam] = []
    for burst in bursts:
        is_patient = burst[0].sender == MessageSender.PATIENT
        role: _Role = "user" if is_patient else "assistant"
        content = "\n\n".join(m.content for m in burst)
        if entries and entries[-1]["role"] == role:
            previous = cast(str, entries[-1]["content"])
            entries[-1] = cast(
                MessageParam, {"role": role, "content": f"{previous}\n\n{content}"}
            )
            continue
        entries.append(cast(MessageParam, {"role": role, "content": content}))
    return entries


# A thinking block's `signature` is the API's own integrity token over that block -
# a few hundred opaque characters that say nothing about what the model saw or
# decided. Dropped from the *rendering* only; the block itself still goes back to the
# API untouched, which is the one thing that must not change about it.
_UNLOGGED_BLOCK_FIELDS = frozenset({"signature"})


def _as_mapping(block: Any) -> dict[str, Any]:
    """Return one content block as a plain mapping, whoever built it.

    A block is an SDK object when it came back from the model and a plain dict when
    this service built it, and the two render very differently - the object as an
    opaque repr. Both become a mapping here so one logged conversation reads the same
    throughout.
    """
    if isinstance(block, dict):
        mapping = block
    elif isinstance(block, BaseModel):
        mapping = block.model_dump()
    else:
        return {"type": type(block).__name__}
    return {
        key: value
        for key, value in mapping.items()
        if key not in _UNLOGGED_BLOCK_FIELDS
    }


def to_loggable_messages(messages: Sequence[MessageParam]) -> list[dict[str, Any]]:
    """Render a Claude `messages` list as plain data, for a debug log entry.

    Returns: one entry per message, each holding its role and its content - a string
        as-is, a block list as one mapping per block.

    Nothing is summarized, and the only field dropped is a thinking block's
    `signature`: this exists to answer "what did the model actually see", and a field
    left out of the rendering is one that cannot be ruled out as the reason it
    answered the way it did.
    """
    loggable: list[dict[str, Any]] = []
    for message in messages:
        content = message["content"]
        loggable.append(
            {
                "role": message["role"],
                "content": (
                    content
                    if isinstance(content, str)
                    else [_as_mapping(block) for block in content]
                ),
            }
        )
    return loggable
