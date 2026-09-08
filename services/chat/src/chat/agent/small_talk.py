"""`answer_small_talk`: the reply to a message that asks for nothing.

A greeting, an acknowledgement, a thank-you, a reaction, or a fragment with no
recoverable meaning. None of them is a question, so none of them has an answer to
retrieve, and none of them needs a person - which is what this module exists to make
true: before it, every such message took the FAQ path, abstained, and called staff.

The reply is generated rather than picked from a list of canned sentences: "Let me
think a bit" and "OMG" want different replies, and a per-category constant would either
proliferate or read as a machine. What makes generating safe here is that the node is
given nothing factual - no corpus, no tools, no patient record beyond the conversation
itself - so there is nothing for it to get wrong, and the prompt forbids inventing any.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass

from anthropic import AsyncAnthropic

from chat.agent.history import (
    ANSWERING_HEADING,
    bound_to_last_n_turns,
    render_opening_clinic,
    render_silent_window,
    replace_trailing_entry,
    silent_window,
    to_claude_messages,
    trailing_question,
)
from chat.core.config import get_settings
from chat.core.errors import TurnPipelineError
from chat.domain.models import Message
from chat.domain.schemas import ChatTokenEvent

# Short by construction: the longest thing this node should ever produce is two
# sentences, and a cap is cheaper than a prompt asking nicely for brevity.
_MAX_TOKENS = 150

_SYSTEM_PROMPT = """You are the receptionist of a medical clinic, replying to a patient
message that asks for nothing - a greeting, an acknowledgement, a thank-you, a
farewell, a reaction, or a note that they are thinking something over.

Reply in one or two short sentences, warm and professional, the way clinic reception
would. Match what was actually said: acknowledge a thank-you, greet a greeting, wish
someone well when they say goodbye.

You have no information about this clinic, this patient, or their appointments, and you
must never imply otherwise. NEVER state or hint at a policy, a price, a date, a time, an
appointment, or a practitioner. Never promise that anyone will follow up, and never say
that staff have been notified - nobody has been.

If the message is a greeting you may say in general terms how you can help - answering
questions about the clinic and arranging appointments - and invite them to ask.

If the message cannot be understood at all, say so plainly and ask them to rephrase.

Never give medical advice or comment on symptoms of any kind."""


@dataclass(frozen=True)
class SmallTalkResult:
    """What `answer_small_talk` produces for the turn.

    Deliberately just the text: this node retrieves nothing, so it has no citations and
    no verdict to report, and a field holding an always-empty list would invite a reader
    to believe one could arrive.
    """

    reply_text: str


async def answer_small_talk(
    anthropic_client: AsyncAnthropic,
    bursts: list[list[Message]],
) -> AsyncIterator[ChatTokenEvent | SmallTalkResult]:
    """Reply to a message that asks for nothing, and do nothing else.

    Args:
        bursts: The chat's full conversation history, partitioned into contiguous
            same-side runs, with the trailing burst the patient message being answered.
            Bounded here, before the call, exactly as every other node bounds it - "ok"
            means one thing after arrival instructions and another after a slot offer,
            so the reply needs the exchange around it. Messages held back from this
            turn's answer are separated from it here too, exactly as `answer_faq` and
            `handle_booking` separate them: nothing this node is told may fold a message
            still waiting for a person into the one it is replying to.

    Yields: `ChatTokenEvent`s as they stream, then exactly one `SmallTalkResult`.

    Raises: TurnPipelineError("generation", ...) - the same failure every other
        generated reply raises, deliberately: a small-talk turn that breaks is a broken
        turn, not a new category of outcome (spec 009 FR-017).

    There is no collect mode. Small talk is dropped whenever any other intent applies
    (FR-008), so this node only ever runs alone and its reply is never merged.
    """
    settings = get_settings()
    bounded = bound_to_last_n_turns(bursts, n=settings.CONTEXT_TURNS)
    messages = to_claude_messages(bounded)
    silenced = render_silent_window(silent_window(bounded))
    if silenced:
        # A turn that follows a silent window has two consecutive patient-sided bursts,
        # and `to_claude_messages` rejoins them into one entry - so without this the
        # messages a staff member was meant to answer arrive as part of the message this
        # node is answering, with nothing to tell them apart by. The prompt below can
        # forbid claiming staff were notified; it cannot forbid answering a question
        # this node cannot see is not the one it was given. Restated through
        # `replace_trailing_entry` for the reason both other specialists use it: that
        # entry is also the one carrying the clinic's opening words whenever the render
        # produced only one.
        messages = replace_trailing_entry(
            messages,
            f"{silenced}\n\n{ANSWERING_HEADING}\n{trailing_question(bounded)}",
            opening_clinic=render_opening_clinic(bounded),
        )

    parts: list[str] = []
    try:
        async with anthropic_client.messages.stream(
            model=settings.CLASSIFICATION_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=messages,
        ) as stream_response:
            async for event in stream_response:
                if event.type == "text":
                    parts.append(event.text)
                    yield ChatTokenEvent(text=event.text)
    except Exception as exc:
        raise TurnPipelineError("generation", exc) from exc

    yield SmallTalkResult(reply_text="".join(parts))
