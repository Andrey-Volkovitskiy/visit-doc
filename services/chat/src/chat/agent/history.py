"""Message rows -> conversation bursts -> alternating Claude `messages` history."""

from typing import Literal, cast

from anthropic.types import MessageParam

from chat.domain.models import Message, MessageSender

_Role = Literal["user", "assistant"]


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


def derive_reply_to_message_ids(bursts: list[list[Message]]) -> list[str]:
    """Return the ids of every message in `bursts`'s trailing burst, in order.

    Precondition: `bursts`'s last burst is patient-sided - i.e. `history` already
    ends with the current, not-yet-answered patient message. Not enforced with a
    runtime assert/guard here - covered by a test for the empty-history case instead.
    """
    if not bursts:
        return []
    return [m.id for m in bursts[-1]]


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
    `sender == ASSISTANT` - a future `staff`-authored burst (grouped onto the
    non-patient side by `split_into_bursts`) still correctly maps to Claude role
    `"assistant"` (the clinic's side of the conversation) this way.
    """
    entries: list[MessageParam] = []
    for burst in bursts:
        is_patient = burst[0].sender == MessageSender.PATIENT
        role: _Role = "user" if is_patient else "assistant"
        content = "\n\n".join(m.content for m in burst)
        entries.append(cast(MessageParam, {"role": role, "content": content}))
    return entries
