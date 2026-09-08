"""`answer_small_talk`: the reply to a message that asks for nothing.

The node has one job and a long list of things it must not do. Retrieval, tools and
citations are absent by construction - it is given nothing factual, so it can invent
nothing factual - and what is left to pin down is the call it makes (one, cheap,
bounded), the prompt's stated limits, and that a failure behaves like every other
generation failure rather than becoming a silent empty reply.
"""

from unittest.mock import MagicMock

import pytest
from chat.agent.small_talk import SmallTalkResult, answer_small_talk
from chat.core.config import Settings
from chat.core.errors import TurnPipelineError
from chat.domain.models import Message, MessageSender
from chat.domain.schemas import ChatTokenEvent

from .conftest import fake_anthropic_client

_TOKENS = ["You're ", "welcome!"]


def _bursts(*contents: str) -> list[list[Message]]:
    """One patient-sided burst per content, alternating sides, trailing patient."""
    bursts: list[list[Message]] = []
    for index, content in enumerate(contents):
        sender = (
            MessageSender.PATIENT
            if index % 2 == len(contents) % 2 - 1 or index == len(contents) - 1
            else MessageSender.ASSISTANT
        )
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


async def test_it_records_nothing_against_the_turns_calls_to_staff() -> None:
    from chat.agent.escalation import EscalationRequests

    escalation = EscalationRequests()
    await _run(fake_anthropic_client(_TOKENS), "Thanks!")

    assert escalation.recorded == ()


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
