"""Tests for `classify_intent()` (research.md #3)."""

import pytest
from chat.agent.classify_intent import ClassificationFailedError, classify_intent
from chat.domain.models import Message, MessageSender
from chat.domain.schemas import IntentLabel

from .conftest import fake_classify_intent_client

_CONTEXT: list[list[Message]] = [
    [Message(sender=MessageSender.PATIENT, content="when can I visit?", id="turn-1")]
]


async def test_classify_intent_returns_single_label() -> None:
    client = fake_classify_intent_client([IntentLabel.FAQ_QUESTION])

    result = await classify_intent(client, _CONTEXT)

    assert result.intents == [IntentLabel.FAQ_QUESTION]


async def test_classify_intent_returns_multiple_labels() -> None:
    labels = [IntentLabel.FAQ_QUESTION, IntentLabel.BOOKING]
    client = fake_classify_intent_client(labels)

    result = await classify_intent(client, _CONTEXT)

    assert result.intents == [IntentLabel.FAQ_QUESTION, IntentLabel.BOOKING]


async def test_classify_intent_returns_catch_all_label() -> None:
    client = fake_classify_intent_client([IntentLabel.UNKNOWN])

    result = await classify_intent(client, _CONTEXT)

    assert result.intents == [IntentLabel.UNKNOWN]


async def test_classify_intent_raises_on_api_error() -> None:
    client = fake_classify_intent_client(call_error=RuntimeError("boom"))

    with pytest.raises(ClassificationFailedError):
        await classify_intent(client, _CONTEXT)


async def test_classify_intent_raises_on_unparseable_response() -> None:
    client = fake_classify_intent_client(None)

    with pytest.raises(ClassificationFailedError):
        await classify_intent(client, _CONTEXT)


async def test_classify_intent_raises_when_model_returns_classification_failed() -> (
    None
):
    client = fake_classify_intent_client([IntentLabel.CLASSIFICATION_FAILED])

    with pytest.raises(ClassificationFailedError):
        await classify_intent(client, _CONTEXT)
