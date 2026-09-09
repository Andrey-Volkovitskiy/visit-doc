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
    bound_to_last_n_turns,
    to_claude_messages_separating_silence,
    to_loggable_messages,
)
from chat.core.config import get_settings
from chat.core.errors import TurnPipelineError
from chat.core.logging import get_logger
from chat.domain.models import Message
from chat.domain.schemas import ChatTokenEvent

# Short by construction: the longest thing this node should ever produce is two
# sentences, and a cap is cheaper than a prompt asking nicely for brevity.
#
# Kept small deliberately, which means it can bite - so `SmallTalkResult.truncated`
# says when it did. Raising it was rejected: a larger cap does not remove the case, it
# only makes a mid-sentence reply rarer and correspondingly harder to notice.
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

    Deliberately no citations and no verdict: this node retrieves nothing, and a field
    holding an always-empty list would invite a reader to believe one could arrive.

    `truncated` is the exception to that reasoning, because it is a fact about the reply
    the patient actually received. A reply that ran out of room ends mid-sentence and is
    otherwise indistinguishable from a short complete one - same shape, same absence of
    an error - so without this the record says the turn went fine.
    """

    reply_text: str
    truncated: bool = False


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
            turn's answer are separated from it here too, exactly as every other model
            call in the turn separates them: nothing this node is told may fold a
            message still waiting for a person into the one it is replying to.

    Yields: `ChatTokenEvent`s as they stream, then exactly one `SmallTalkResult`, whose
        `truncated` says whether the reply ran into `_MAX_TOKENS`.

    Raises: TurnPipelineError("generation", ...) - the same failure every other
        generated reply raises, deliberately: a small-talk turn that breaks is a broken
        turn, not a new category of outcome (spec 009 FR-017).

    There is no collect mode. Small talk is dropped whenever any other intent applies
    (FR-008), so this node only ever runs alone and its reply is never merged.
    """
    settings = get_settings()
    bounded = bound_to_last_n_turns(bursts, n=settings.CONTEXT_TURNS)
    # Separated rather than rendered flat: the prompt below can forbid claiming staff
    # were notified, but it cannot forbid answering a question this node cannot see is
    # not the one it was given. See `to_claude_messages_separating_silence`.
    messages = to_claude_messages_separating_silence(bounded)
    get_logger().debug(
        "small_talk.model_request", messages=to_loggable_messages(messages)
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
            # Read after the loop rather than from a `message_delta` event: the SDK
            # accumulates the final message, and this is the documented way to ask why
            # it stopped. Inside the `try` because a failure to obtain it is a failure
            # of the same call.
            final = await stream_response.get_final_message()
    except Exception as exc:
        raise TurnPipelineError("generation", exc) from exc

    reply_text = "".join(parts)
    truncated = final.stop_reason == "max_tokens"
    if truncated:
        # Logged, not raised, and staff are not called: the tokens have already reached
        # the patient, so there is nothing left to fail cleanly, and paging a person
        # over a clipped pleasantry is the queue noise this whole path exists to stop.
        # What is owed is a record, and this is it.
        get_logger().warning(
            "small_talk.truncated",
            max_tokens=_MAX_TOKENS,
            answer_chars=len(reply_text),
        )
    yield SmallTalkResult(reply_text=reply_text, truncated=truncated)
