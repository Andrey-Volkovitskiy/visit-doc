"""No golden case's own words appear in a prompt the assistant is given.

A prompt example that is a golden case, word for word, turns that case from a measure of
how the assistant reads a message it has never seen into a check that it follows its
own example - and the example usually states the label or the split the case is scored
on. Against the classifier's and the booking loop's prompts as they stood on
2026-09-18 this test flags 25 cases, twelve of them quoted beside their own label or
split. This fails the moment another one is added.

What it checks is verbatim reuse: a case's message, each sentence or comma-separated
clause of it, a patient turn of its history, or its scripted reply, found in a prompt
after normalizing case, punctuation and whitespace. A paraphrase passes, so choosing
an example's topic and wording away from the golden set remains a reviewer's job.
"""

import importlib
import json
import pkgutil
import re
from pathlib import Path

import chat.agent
import pytest

_CASES = Path(__file__).resolve().parents[2] / "golden" / "cases.json"

# Shorter sentences ("Thanks!", "Is it free?") are ordinary phrasing, not a case.
_MIN_WORDS = 3
# A module-level string this long is a prompt, a prompt fragment or a fixed reply.
_MIN_PROMPT_LENGTH = 60
_LEADING_CONJUNCTION = re.compile(r"^(?:and|but|also|so) ")


def _normalized(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9' ]", " ", text.lower()).split())


def _prompts() -> dict[str, str]:
    """Every long upper-case string constant in `chat.agent`, by dotted name."""
    found: dict[str, str] = {}
    for info in pkgutil.walk_packages(chat.agent.__path__, "chat.agent."):
        module = importlib.import_module(info.name)
        for name, value in vars(module).items():
            is_long_text = isinstance(value, str) and len(value) >= _MIN_PROMPT_LENGTH
            if name.isupper() and is_long_text:
                found[f"{info.name}.{name}"] = value
    return found


def _patient_texts() -> list[tuple[str, str]]:
    """Every text a case puts in the patient's mouth, as (case id, text).

    The clinic's side of a history is left out: a case may quote one of the
    assistant's own fixed replies there, which is the conversation being realistic,
    not a prompt being shown the case.
    """
    texts: list[tuple[str, str]] = []
    for case in json.loads(_CASES.read_text(encoding="utf-8")):
        texts.append((case["id"], case["message"]))
        texts.extend(
            (case["id"], turn["text"])
            for turn in case.get("history", [])
            if turn["role"] == "user"
        )
        reply = case.get("scheduling", {}).get("reply")
        if reply:
            texts.append((case["id"], reply))
    return texts


def _fragments(text: str) -> set[str]:
    """The text, its sentences and its clauses, normalized, long enough to count.

    A leading "and", "but", "also" or "so" is dropped from a clause, since a prompt
    quoting the second half of a message usually quotes it without one.
    """
    parts = {text, *re.split(r"(?<=[?.!,])\s+", text)}
    fragments = {_LEADING_CONJUNCTION.sub("", _normalized(part)) for part in parts}
    return {f for f in fragments if len(f.split()) >= _MIN_WORDS}


def test_the_scan_finds_the_prompts_it_guards() -> None:
    # A scan that found nothing would pass for the wrong reason.
    prompts = _prompts()

    assert "chat.agent.classify_intent._SYSTEM_PROMPT" in prompts
    assert "chat.agent.handle_booking._SYSTEM_PROMPT" in prompts
    assert "chat.agent.answer_faq._SYSTEM_PROMPT" in prompts


def test_the_scan_catches_a_case_pasted_into_a_prompt() -> None:
    prompt = _normalized('For example, "What should I bring, and is it free?" is two.')

    assert any(
        f" {fragment} " in f" {prompt} "
        for fragment in _fragments("What should I bring, and is it free?")
    )


@pytest.mark.parametrize(("case_id", "text"), _patient_texts())
def test_no_prompt_quotes_a_golden_case(case_id: str, text: str) -> None:
    fragments = _fragments(text)
    quoted = [
        (source, fragment)
        for source, prompt in _prompts().items()
        for fragment in fragments
        if f" {fragment} " in f" {_normalized(prompt)} "
    ]

    assert quoted == [], f"{case_id}'s words are in a prompt: {quoted}"
