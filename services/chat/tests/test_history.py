"""Tests for `split_into_bursts()`/`derive_reply_to_message_ids()`'s trailing-burst
selection, `bound_to_last_n_turns()`'s turn-boundary truncation (research.md #5/#9,
data-model.md), and `to_claude_messages()`'s same-side merge into alternating
`user`/`assistant` entries (research.md §5).
"""

from chat.agent.history import (
    bound_to_last_n_turns,
    derive_reply_to_message_ids,
    split_into_bursts,
    to_claude_messages,
)
from chat.domain.models import Message, MessageSender


def _row(sender: MessageSender, content: str, id: str) -> Message:
    return Message(sender=sender, content=content, id=id)


def _ids(rows: list[Message]) -> list[str]:
    """`Message` has no `__eq__` - compare rows by id instead of object identity."""
    return [row.id for row in rows]


# --- to_claude_messages ------------------------------------------------------------


def test_no_history_produces_no_entries() -> None:
    assert to_claude_messages(split_into_bursts([])) == []


def test_alternating_history_stays_unmerged() -> None:
    history = [
        _row(MessageSender.PATIENT, "I'm going to come on Tuesday", id="p1"),
        _row(MessageSender.ASSISTANT, "Noted.", id="a1"),
        _row(MessageSender.PATIENT, "what are your hours that day?", id="p2"),
    ]

    entries = to_claude_messages(split_into_bursts(history))

    assert entries == [
        {"role": "user", "content": "I'm going to come on Tuesday"},
        {"role": "assistant", "content": "Noted."},
        {"role": "user", "content": "what are your hours that day?"},
    ]


def test_unanswered_patient_messages_merge_into_one_entry() -> None:
    history = [
        _row(MessageSender.PATIENT, "When can I see", id="p1"),
        _row(MessageSender.PATIENT, "Dr. Josh?", id="p2"),
    ]

    entries = to_claude_messages(split_into_bursts(history))

    assert entries == [{"role": "user", "content": "When can I see\n\nDr. Josh?"}]


def test_burst_of_three_unanswered_messages_all_merge() -> None:
    history = [
        _row(MessageSender.PATIENT, "Hi", id="p1"),
        _row(MessageSender.PATIENT, "quick question", id="p2"),
        _row(MessageSender.PATIENT, "when do you open?", id="p3"),
    ]

    entries = to_claude_messages(split_into_bursts(history))

    assert entries == [
        {"role": "user", "content": "Hi\n\nquick question\n\nwhen do you open?"}
    ]


def test_historical_assistant_content_used_verbatim() -> None:
    history = [
        _row(MessageSender.PATIENT, "what's the weather?", id="p1"),
        _row(
            MessageSender.ASSISTANT,
            "I don't have a confident answer to that.",
            id="a1",
        ),
        _row(MessageSender.PATIENT, "ok, what about your hours?", id="p2"),
    ]

    entries = to_claude_messages(split_into_bursts(history))

    assert entries[1] == {
        "role": "assistant",
        "content": "I don't have a confident answer to that.",
    }


# --- derive_reply_to_message_ids ----------------------------------------------------


def test_reply_ids_on_empty_history_is_empty() -> None:
    assert derive_reply_to_message_ids(split_into_bursts([])) == []


def test_reply_ids_is_just_the_current_message_when_no_prior_history() -> None:
    history = [_row(MessageSender.PATIENT, "what are your hours?", id="cur")]

    assert derive_reply_to_message_ids(split_into_bursts(history)) == ["cur"]


def test_reply_ids_covers_the_whole_trailing_unanswered_burst() -> None:
    history = [
        _row(MessageSender.PATIENT, "When can I see", id="p1"),
        _row(MessageSender.PATIENT, "Dr. Josh?", id="p2"),
    ]

    assert derive_reply_to_message_ids(split_into_bursts(history)) == ["p1", "p2"]


def test_reply_ids_stop_at_the_nearest_prior_assistant_message() -> None:
    history = [
        _row(MessageSender.PATIENT, "old unrelated question", id="p0"),
        _row(MessageSender.ASSISTANT, "old answer", id="a0"),
        _row(MessageSender.PATIENT, "When can I see", id="p1"),
        _row(MessageSender.PATIENT, "Dr. Josh?", id="p2"),
    ]

    assert derive_reply_to_message_ids(split_into_bursts(history)) == ["p1", "p2"]


# --- bound_to_last_n_turns -----------------------------------------------------------


def _turn(index: int) -> list[Message]:
    """One complete turn: a single patient message followed by a single reply."""
    return [
        _row(MessageSender.PATIENT, f"patient message {index}", id=f"p{index}"),
        _row(MessageSender.ASSISTANT, f"reply {index}", id=f"a{index}"),
    ]


def _bounded_ids(history: list[Message], n: int) -> list[str]:
    bounded_bursts = bound_to_last_n_turns(split_into_bursts(history), n=n)
    return [row.id for burst in bounded_bursts for row in burst]


# `bound_to_last_n_turns` unconditionally treats `bursts`'s last burst as the current,
# in-progress one - it never counts against the `n` budget (same precondition
# `derive_reply_to_message_ids` documents: the trailing burst is always patient-sided
# in production). Every case below appends one, so it's exercised consistently.
_TRAILING = [_row(MessageSender.PATIENT, "and one more thing", id="p_trailing")]


def test_bound_to_last_n_turns_returns_everything_when_fewer_than_n_turns() -> None:
    history = [*_turn(1), *_turn(2), *_TRAILING]

    assert _bounded_ids(history, n=5) == _ids(history)


def test_bound_to_last_n_turns_returns_everything_when_exactly_n_turns() -> None:
    history = [row for i in range(1, 6) for row in _turn(i)] + _TRAILING

    assert _bounded_ids(history, n=5) == _ids(history)


def test_bound_to_last_n_turns_drops_the_oldest_turn_when_over_the_limit() -> None:
    history = [row for i in range(1, 7) for row in _turn(i)] + _TRAILING

    expected = [row for i in range(2, 7) for row in _turn(i)] + _TRAILING
    assert _bounded_ids(history, n=5) == _ids(expected)


def test_bound_to_last_n_turns_on_empty_bursts_returns_empty() -> None:
    assert bound_to_last_n_turns([], n=5) == []


def test_bound_to_last_n_turns_counts_a_multi_message_burst_as_one_turn() -> None:
    burst_turn = [
        _row(MessageSender.PATIENT, "Hi", id="p0a"),
        _row(MessageSender.PATIENT, "quick question", id="p0b"),
        _row(MessageSender.ASSISTANT, "Sure, go ahead", id="a0"),
    ]
    history = [*burst_turn, *_turn(2), *_TRAILING]

    # n=1 keeps only the most recent complete turn (_turn(2)) plus the trailing burst
    # - the earlier burst_turn, despite being three rows/two patient messages, is
    # still exactly one turn, not two.
    assert _bounded_ids(history, n=1) == _ids([*_turn(2), *_TRAILING])


def test_bound_to_last_n_turns_always_keeps_a_trailing_unanswered_burst() -> None:
    trailing = [_row(MessageSender.PATIENT, "and one more thing", id="p_trailing")]
    history = [row for i in range(1, 7) for row in _turn(i)] + trailing

    result = _bounded_ids(history, n=5)

    # The 6 complete turns are still bounded to the last 5 (turns 2-6) - the trailing
    # unanswered burst doesn't consume any of that budget, and is always kept.
    expected = [row for i in range(2, 7) for row in _turn(i)] + trailing
    assert result == _ids(expected)


def test_bound_to_last_n_turns_trailing_unanswered_burst_alone_is_not_a_turn() -> None:
    trailing = [_row(MessageSender.PATIENT, "hello?", id="p_trailing")]

    assert _bounded_ids(trailing, n=5) == _ids(trailing)


# --- split_into_bursts ---------------------------------------------------------------


def test_split_into_bursts_on_empty_history_returns_no_bursts() -> None:
    assert split_into_bursts([]) == []


def test_split_into_bursts_groups_consecutive_same_side_messages() -> None:
    history = [
        _row(MessageSender.PATIENT, "Hi", id="p1"),
        _row(MessageSender.PATIENT, "quick question", id="p2"),
        _row(MessageSender.ASSISTANT, "Sure, go ahead", id="a1"),
        _row(MessageSender.PATIENT, "ok thanks", id="p3"),
    ]

    bursts = split_into_bursts(history)

    assert [_ids(burst) for burst in bursts] == [["p1", "p2"], ["a1"], ["p3"]]
