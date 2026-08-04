import pytest
from chat.domain.validation import is_meaningless


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
