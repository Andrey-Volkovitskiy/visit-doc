from datetime import datetime

import pytest
from chat.domain.schemas import ChatRequest
from chat.domain.validation import is_meaningless
from pydantic import ValidationError
from ulid import ULID


@pytest.mark.parametrize(
    "text",
    ["", "   ", "\n\n", "---", "-  -   -", "Question:\nAnswer:", "question: \nanswer:"],
)
def test_meaningless_content_is_detected(text: str) -> None:
    assert is_meaningless(text)


@pytest.mark.parametrize(
    "text",
    [
        "Visiting hours are Mon-Fri 8am-5pm.",
        "Question: What is your address?\nAnswer: 15 Smith St, London",
        "ok",
    ],
)
def test_meaningful_content_is_not_flagged(text: str) -> None:
    assert not is_meaningless(text)


@pytest.mark.parametrize(
    "local_now",
    [
        "2026-08-14T09:00:00Z",
        "2026-08-14T09:00:00+00:00",
        "2026-08-14T09:00:00-05:00",
    ],
)
def test_chat_request_rejects_a_timezone_aware_local_now(local_now: str) -> None:
    with pytest.raises(ValidationError):
        ChatRequest(chat_id=str(ULID()), message="hi", local_now=local_now)


def test_chat_request_accepts_a_naive_local_now() -> None:
    request = ChatRequest(
        chat_id=str(ULID()), message="hi", local_now="2026-08-14T09:00:00"
    )
    assert request.local_now == datetime(2026, 8, 14, 9, 0)
    assert request.local_now.tzinfo is None


def test_chat_request_requires_local_now() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(chat_id=str(ULID()), message="hi")


def test_chat_request_requires_chat_id() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(message="hi", local_now="2026-08-14T09:00:00")


def test_chat_request_rejects_a_malformed_chat_id() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(chat_id="too-short", message="hi", local_now="2026-08-14T09:00:00")
