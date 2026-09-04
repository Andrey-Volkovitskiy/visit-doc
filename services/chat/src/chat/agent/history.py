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

    Messages that arrived while the assistant could not reply were kept for a person to
    answer, and a mark that is still set means no person has: a staff reply clears every
    one of them. So the split holds back messages nothing has answered, and they go on
    waiting for a person after it - the silence ending is not the conversation being
    dealt with. Answering them here would speak over the staff member they are still
    flagged for, and would answer a question the patient may have moved on from. They
    stay in `bursts` as context, which is why this splits rather than drops.

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
# text in one place: both specialists render it, and two copies would drift.
#
# It says nobody has answered them because nobody has: a staff reply clears the mark
# that puts a message here, so every message this note is rendered for is one still
# waiting for a person. Telling the model otherwise invited it to assure the patient
# that a human had dealt with something no human had seen.
SILENT_WINDOW_NOTE = (
    "Earlier messages that arrived while the conversation was with the clinic's staff "
    "and the assistant could not reply. Nobody has answered them, and they are still "
    "waiting for a member of staff. Read them for context only: do not answer them, do "
    "not act on them, and do not tell the patient that they have been dealt with or "
    "replied to. If the message you are answering asks one of them again, answer it as "
    "newly asked."
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


# What a model is told about the clinic's own words that open the window it can see.
# One text in one place, for the same reason `SILENT_WINDOW_NOTE` is.
#
# Not the opposite instruction to that one, though the two runs are opposites in one
# respect: the silent window holds messages nobody has answered, while these are the
# clinic's own words the patient has often already answered. That is the whole reason
# they are carried - "yes please" is unreadable without the offer it accepts.
#
# But the note stops at saying what they are, and does not tell the model to act on
# them. It cannot know that it should: this burst is also what a window lands on when
# `bound_to_last_n_turns` cuts mid-turn, leaving a clinic reply whose own question is
# off the front and which the patient answered turns ago. Told to act on that, the
# booking loop re-offers a slot already taken. So the note leaves the model to read
# the patient's reply and judge, and forbids only the one thing that is never right -
# reading the clinic's words as the patient's.
OPENING_CLINIC_NOTE = (
    "Earlier in this conversation the clinic said the following. It is the clinic's "
    "own words, not the patient's - never attribute it to them, and do not treat it "
    "as a request the patient has made. The message you are answering may be a reply "
    "to it; it may also be something the patient answered earlier, or something no "
    "longer shown has already dealt with. Read it as context and let the patient's "
    "own words below decide what this turn is about."
)

# Marks where the clinic's folded-in words stop and the patient's own begin. Both runs
# join their messages with a blank line, so without this the seam is the same "\n\n"
# used *inside* each of them: two patient messages folded behind the note read as one
# three-paragraph clinic message, under a note saying never to attribute that text to
# the patient. It is also what keeps `OPENING_CLINIC_NOTE` from being an unfenced trust
# marker sitting in patient-authored text.
PATIENT_RESUMES_HEADING = "The patient's own messages, from here on:"

# Labels the half of a rewritten entry that is actually the request, for a specialist
# that replaces the trailing entry with a prompt of its own. Lives here beside the
# other two headings so the seams a model is asked to read are one vocabulary.
ANSWERING_HEADING = "The message you are answering:"


def _split_at_first_patient_burst(
    bursts: list[list[Message]],
) -> tuple[list[list[Message]], list[list[Message]]]:
    """Split `bursts` where the patient first speaks.

    Returns: the clinic-sided bursts that come before the patient's first, and the run
        from that first patient burst onwards. The second is empty only for a history
        holding no patient message at all.

    A clinic-sided burst at the front is the one burst the window cannot pair: whatever
    it answers is either off the front of the window - `bound_to_last_n_turns` cuts a
    fixed number of bursts, and a silent window's split makes that cut land mid-turn -
    or was never said, as when staff open a conversation the patient has not spoken in
    yet. The Messages API requires the first entry to use the `user` role, so it cannot
    be rendered where it sits.

    It is separated rather than discarded. Dropping it loses conversation the model
    needs: staff who open with "Dr. Chen has a slot Friday at 3 - shall I book it?" and
    a patient who replies "yes please" leave a turn whose whole subject is in the burst
    that went, and a classifier reading "yes please" alone cannot find the booking
    intent in it. Relabelling it as the patient's is the other wrong answer - that puts
    a sentence the patient never wrote into their mouth for the rest of the
    conversation. So it is carried as labelled context instead, which is what
    `render_silent_window` already does for the other run of messages the alternating
    list cannot hold.
    """
    first_patient = next(
        (
            index
            for index, burst in enumerate(bursts)
            if burst[0].sender == MessageSender.PATIENT
        ),
        None,
    )
    if first_patient is None:
        return bursts, []
    return bursts[:first_patient], bursts[first_patient:]


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

    Leading clinic-sided bursts are handled here for the same reason: the Messages API
    also requires the first entry to use the `user` role and rejects the whole call
    otherwise, which would fail every model call of the turn, not just this rendering.
    They are not dropped - they are folded into the first `user` entry behind
    `OPENING_CLINIC_NOTE`, and `PATIENT_RESUMES_HEADING` marks where they stop. See
    `_split_at_first_patient_burst` for why neither dropping nor relabelling them is
    good enough. The fold is all a caller that sends these entries unchanged needs -
    the classifier is one. A caller that *replaces* the trailing entry must go through
    `replace_trailing_entry`, because the entry it replaces is the folded one whenever
    this render produced exactly one.

    Both rules belong to the wire format, so they are enforced where the wire format is
    built - the burst structure itself is left as it was, since `trailing_question`,
    `silent_window` and `derive_reply_to_message_ids` read it for their own purposes.
    Returns an empty list only for a history holding no patient message at all, which
    a turn never has: the message it is answering is always in it.
    """
    opening_clinic, answerable = _split_at_first_patient_burst(bursts)
    entries: list[MessageParam] = []
    for burst in answerable:
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
    if not entries:
        return entries
    rendered = _render_opening_bursts(opening_clinic)
    if rendered:
        # Prepended to the first entry rather than sent as one of its own: an entry of
        # its own would have to carry a role, and the two roles available are the two
        # wrong answers - `assistant` is what the API refuses first, and `user` is the
        # relabelling that puts the clinic's words in the patient's mouth. Inside the
        # entry, behind a note naming whose words they are and a heading marking where
        # they stop, it is context rather than attribution.
        first = cast(str, entries[0]["content"])
        entries[0] = cast(
            MessageParam,
            {
                "role": "user",
                "content": f"{rendered}\n\n{PATIENT_RESUMES_HEADING}\n{first}",
            },
        )
    return entries


def render_opening_clinic(bursts: list[list[Message]]) -> str:
    """Render the clinic's window-opening words, or "" when the window opens correctly.

    The counterpart of `render_silent_window`, and needed for the same reason: a
    specialist that replaces the trailing entry with a prompt of its own must carry
    this run into that prompt itself, because the entry it replaces is the very entry
    `to_claude_messages` folded the run into whenever the render produced only one.
    That is precisely the case the fold exists for - staff open with an offer and the
    patient answers it in the next message - so leaving it to the fold drops the run
    exactly where it matters most.

    Pair it with `replace_trailing_entry`, which decides whether this belongs in a
    given prompt; a specialist that prepends it unconditionally would repeat it
    whenever the fold's own entry survived.
    """
    opening_clinic, _ = _split_at_first_patient_burst(bursts)
    return _render_opening_bursts(opening_clinic)


def _render_opening_bursts(opening_clinic: list[list[Message]]) -> str:
    """Render the note over the clinic bursts, or "" when there are none."""
    if not opening_clinic:
        return ""
    said = "\n\n".join(m.content for burst in opening_clinic for m in burst)
    return f"{OPENING_CLINIC_NOTE}\n{said}"


def replace_trailing_entry(
    entries: list[MessageParam], body: str, *, opening_clinic: str
) -> list[MessageParam]:
    """Return `entries` with the trailing entry's content replaced by `body`.

    Args:
        entries: what `to_claude_messages` rendered for this turn.
        body: the prompt the specialist wants the model to answer, headings and all.
        opening_clinic: `render_opening_clinic` for the same bursts - re-prepended to
            `body` exactly when the entry being replaced is the one the fold went into,
            and ignored otherwise.

    A specialist replaces the trailing entry because it has a prompt of its own to put
    there. Writing that as `[*entries[:-1], new]` is wrong in the one case the fold was
    written for: `to_claude_messages` folds the clinic's opening words into `entries[0]`
    and, when the whole window renders as a single entry, `entries[0]` *is* the trailing
    entry - so the slice throws the fold away and the specialist answers "yes please"
    with the offer it accepts deleted. Hence the conditional here rather than at each
    call site, where it was twice forgotten.

    An empty `entries` takes the prepend too: nothing carries the fold, so the prompt
    must.
    """
    folded_here = bool(opening_clinic) and len(entries) <= 1
    content = f"{opening_clinic}\n\n{body}" if folded_here else body
    return [*entries[:-1], cast(MessageParam, {"role": "user", "content": content})]


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
