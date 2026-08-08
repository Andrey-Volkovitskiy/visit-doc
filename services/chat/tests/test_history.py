"""Tests for `build_history_messages()`'s same-sender merge pass (research.md §5)."""

from chat.agent.history import build_history_messages
from chat.domain.models import Message, MessageSender


def _row(sender: MessageSender, content: str, id: str) -> Message:
    return Message(sender=sender, content=content, id=id)


def test_no_prior_history_returns_only_current_message() -> None:
    entries, reply_ids = build_history_messages([], "what are your hours?", "cur")

    assert entries == [{"role": "user", "content": "what are your hours?"}]
    assert reply_ids == ["cur"]


def test_alternating_history_stays_unmerged() -> None:
    history = [
        _row(MessageSender.PATIENT, "I'm going to come on Tuesday", id="p1"),
        _row(MessageSender.ASSISTANT, "Noted.", id="a1"),
    ]
    entries, reply_ids = build_history_messages(
        history, "what are your hours that day?", "p2"
    )

    assert entries == [
        {"role": "user", "content": "I'm going to come on Tuesday"},
        {"role": "assistant", "content": "Noted."},
        {"role": "user", "content": "what are your hours that day?"},
    ]
    assert reply_ids == ["p2"]


def test_unanswered_patient_message_merges_with_current_message() -> None:
    history = [_row(MessageSender.PATIENT, "When can I see", id="p1")]
    entries, reply_ids = build_history_messages(history, "Dr. Josh?", "p2")

    assert entries == [{"role": "user", "content": "When can I see\n\nDr. Josh?"}]
    assert reply_ids == ["p1", "p2"]


def test_burst_of_three_unanswered_messages_all_merge() -> None:
    history = [
        _row(MessageSender.PATIENT, "Hi", id="p1"),
        _row(MessageSender.PATIENT, "quick question", id="p2"),
    ]
    entries, reply_ids = build_history_messages(history, "when do you open?", "p3")

    assert entries == [
        {"role": "user", "content": "Hi\n\nquick question\n\nwhen do you open?"}
    ]
    assert reply_ids == ["p1", "p2", "p3"]


def test_historical_assistant_content_used_verbatim() -> None:
    history = [
        _row(MessageSender.PATIENT, "what's the weather?", id="p1"),
        _row(
            MessageSender.ASSISTANT,
            "I don't have a confident answer to that.",
            id="a1",
        ),
    ]
    entries, reply_ids = build_history_messages(
        history, "ok, what about your hours?", "p2"
    )

    assert entries[1] == {
        "role": "assistant",
        "content": "I don't have a confident answer to that.",
    }
    assert reply_ids == ["p2"]


def test_reply_ids_stop_at_the_nearest_prior_assistant_message() -> None:
    history = [
        _row(MessageSender.PATIENT, "old unrelated question", id="p0"),
        _row(MessageSender.ASSISTANT, "old answer", id="a0"),
        _row(MessageSender.PATIENT, "When can I see", id="p1"),
    ]
    entries, reply_ids = build_history_messages(history, "Dr. Josh?", "p2")

    assert entries[-1] == {
        "role": "user",
        "content": "When can I see\n\nDr. Josh?",
    }
    assert reply_ids == ["p1", "p2"]
