"""`answer_small_talk`: the reply to a message that asks for nothing.

The node has one job and a long list of things it must not do. Retrieval, tools and
citations are absent by construction - it is given nothing factual, so it can invent
nothing factual - and what is left to pin down is the call it makes (one, cheap,
bounded), the prompt's stated limits, and that a failure behaves like every other
generation failure rather than becoming a silent empty reply.
"""

from unittest.mock import MagicMock

import pytest
from chat.agent.history import ANSWERING_HEADING, SILENT_WINDOW_NOTE
from chat.agent.small_talk import SmallTalkResult, answer_small_talk
from chat.core.config import Settings
from chat.core.errors import TurnPipelineError
from chat.domain.models import Message, MessageSender
from chat.domain.schemas import ChatTokenEvent
from structlog.testing import capture_logs

from .conftest import fake_anthropic_client

_TOKENS = ["You're ", "welcome!"]


def _bursts(*contents: str) -> list[list[Message]]:
    """One burst per content, strictly alternating, with the trailing one the patient's.

    Sides are assigned from the *end*: a turn's history always ends with the message
    being answered, and any burst count then alternates back from it - which is the
    only shape `split_into_bursts` can produce.
    """
    bursts: list[list[Message]] = []
    for index, content in enumerate(contents):
        from_end = len(contents) - 1 - index
        sender = MessageSender.PATIENT if from_end % 2 == 0 else MessageSender.ASSISTANT
        bursts.append([Message(sender=sender, content=content, id=f"m{index}")])
    return bursts


async def _run(client: MagicMock, *contents: str) -> list[object]:
    return [event async for event in answer_small_talk(client, _bursts(*contents))]


async def test_it_streams_its_tokens_and_ends_with_its_result() -> None:
    events = await _run(fake_anthropic_client(_TOKENS), "Thanks!")

    assert [e.text for e in events if isinstance(e, ChatTokenEvent)] == _TOKENS
    result = events[-1]
    assert isinstance(result, SmallTalkResult)
    assert result.reply_text == "You're welcome!"


async def test_it_makes_exactly_one_model_call() -> None:
    client = fake_anthropic_client(_TOKENS)

    await _run(client, "Thanks!")

    assert client.messages.stream.call_count == 1
    # Nothing else: no embedding, no search, no tool loop. `.create` is the
    # classification call's method, which this node never makes.
    assert client.messages.create.call_count == 0


async def test_it_runs_on_the_cheap_model() -> None:
    client = fake_anthropic_client(_TOKENS)

    await _run(client, "Thanks!")

    kwargs = client.messages.stream.call_args.kwargs
    assert kwargs["model"] == Settings().CLASSIFICATION_MODEL


async def test_it_builds_no_tool_registry() -> None:
    client = fake_anthropic_client(_TOKENS)

    await _run(client, "Thanks!")

    assert "tools" not in client.messages.stream.call_args.kwargs


async def test_it_sees_the_conversation_bounded_to_the_context_window() -> None:
    client = fake_anthropic_client(_TOKENS)
    turns = [f"message {index}" for index in range(40)]

    await _run(client, *turns)

    messages = client.messages.stream.call_args.kwargs["messages"]
    # Bounded, not the whole history: an acknowledgement needs the last exchange, and
    # the bound is the one every other node already applies.
    assert 0 < len(messages) <= 2 * Settings().CONTEXT_TURNS + 1


async def test_it_is_given_no_way_to_call_staff_at_all() -> None:
    # Asserted on the signature rather than on a collector: this node is never handed
    # one, so a test that made its own and checked it stayed empty would pass with the
    # call to staff written straight back in.
    import inspect

    parameters = inspect.signature(answer_small_talk).parameters

    assert "escalation" not in parameters
    assert set(parameters) == {"anthropic_client", "bursts"}


async def test_the_prompt_states_what_the_reply_may_never_contain() -> None:
    from chat.agent.small_talk import _SYSTEM_PROMPT

    prompt = _SYSTEM_PROMPT.lower()
    for forbidden in ("policy", "price", "date", "time", "practitioner", "appointment"):
        assert forbidden in prompt
    assert "never" in prompt


async def test_the_prompt_allows_a_greeting_to_offer_help_in_general_terms() -> None:
    from chat.agent.small_talk import _SYSTEM_PROMPT

    assert "how you can help" in _SYSTEM_PROMPT.lower()


async def test_the_prompt_covers_the_message_that_cannot_be_understood() -> None:
    from chat.agent.small_talk import _SYSTEM_PROMPT

    assert "rephrase" in _SYSTEM_PROMPT.lower()


async def test_a_failing_call_raises_the_same_pipeline_error_generation_does() -> None:
    client = fake_anthropic_client(_TOKENS, stream_error=RuntimeError("upstream"))

    with pytest.raises(TurnPipelineError) as raised:
        await _run(client, "Thanks!")

    # The same stage name every other generation failure carries, so a failure here is
    # counted, logged and reported exactly as one on the FAQ path is - no new
    # semantics, and no silent empty reply.
    assert raised.value.pipeline_step == "generation"


async def test_it_separates_messages_a_person_is_still_owed() -> None:
    """A silent window is context, never part of the message this node answers.

    `to_claude_messages` rejoins two consecutive patient-sided bursts into one entry,
    which is exactly the shape `exclude_silent_window` leaves behind - so without the
    note the messages held back for a staff member arrive inside the entry being
    replied to, and a warm "of course, that's all sorted" is what this node would
    write over them.
    """
    client = fake_anthropic_client(_TOKENS)
    held_back = [
        Message(sender=MessageSender.PATIENT, content="my bill is wrong", id="p0")
    ]
    answering = [
        Message(sender=MessageSender.PATIENT, content="thanks anyway!", id="p1")
    ]

    [event async for event in answer_small_talk(client, [held_back, answering])]

    entry = client.messages.stream.call_args.kwargs["messages"][-1]["content"]
    assert SILENT_WINDOW_NOTE in entry
    assert "my bill is wrong" in entry
    assert entry.endswith(f"{ANSWERING_HEADING}\nthanks anyway!")


async def test_an_ordinary_turn_carries_no_silent_window_note() -> None:
    # The note is a seam a model has to read, so it is absent when there is nothing to
    # separate: an ordinary pleasantry's prompt is the conversation and nothing else.
    client = fake_anthropic_client(_TOKENS)

    await _run(client, "Thanks!")

    entries = client.messages.stream.call_args.kwargs["messages"]
    assert SILENT_WINDOW_NOTE not in " ".join(str(e["content"]) for e in entries)


# --- a reply that ran out of room ----------------------------------------------------


async def test_a_reply_that_hit_the_cap_is_recorded_as_truncated() -> None:
    """The cap is small, so hitting it has to be visible.

    The tokens are already on the wire by the time this is known - the node streams as
    it goes - so nothing here can retract a half-sentence. What it can do is stop the
    turn from recording it as an ordinary short reply, which is what left a truncated
    pleasantry indistinguishable from a complete one.
    """
    client = fake_anthropic_client(["Of course, take your"], stop_reason="max_tokens")

    events = await _run(client, "Let me think a bit")

    result = events[-1]
    assert isinstance(result, SmallTalkResult)
    assert result.truncated is True
    assert result.reply_text == "Of course, take your"


async def test_a_reply_that_finished_on_its_own_is_not_marked_truncated() -> None:
    client = fake_anthropic_client(_TOKENS)

    events = await _run(client, "Thanks!")

    result = events[-1]
    assert isinstance(result, SmallTalkResult)
    assert result.truncated is False


async def test_a_truncated_reply_is_reported_where_an_operator_will_see_it() -> None:
    client = fake_anthropic_client(["Of course, take your"], stop_reason="max_tokens")

    with capture_logs() as logs:
        await _run(client, "Let me think a bit")

    entry = next(e for e in logs if e["event"] == "small_talk.truncated")
    assert entry["log_level"] == "warning"
    assert entry["max_tokens"] == 150
