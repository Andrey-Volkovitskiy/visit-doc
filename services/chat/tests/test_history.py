"""Tests for `split_into_bursts()`/`derive_reply_to_message_ids()`'s trailing-burst
selection, `bound_to_last_n_turns()`'s turn-boundary truncation (research.md #5/#9,
data-model.md), and `to_claude_messages()`'s same-side merge into alternating
`user`/`assistant` entries (research.md §5).
"""

from anthropic.types import ThinkingBlock, ToolUseBlock
from chat.agent.history import (
    bound_to_last_n_turns,
    derive_reply_to_message_ids,
    exclude_silent_window,
    render_silent_window,
    silent_window,
    split_into_bursts,
    to_claude_messages,
    to_loggable_messages,
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


# --- to_loggable_messages ------------------------------------------------------------


def test_to_loggable_messages_keeps_plain_text_content_as_is() -> None:
    messages = to_claude_messages(
        split_into_bursts([_row(MessageSender.PATIENT, "Any slots Friday?", id="p1")])
    )

    assert to_loggable_messages(messages) == [
        {"role": "user", "content": "Any slots Friday?"}
    ]


def test_to_loggable_messages_renders_sdk_blocks_as_plain_data() -> None:
    # The model's own reply arrives as SDK objects, which would otherwise reach the log
    # as an opaque repr.
    block = ToolUseBlock(
        id="toolu_1",
        name="check_availability",
        input={"practitioner_id": "abc"},
        type="tool_use",
    )

    loggable = to_loggable_messages([{"role": "assistant", "content": [block]}])

    # Field-by-field rather than whole-dict, so a field the SDK adds later doesn't fail
    # a test that is about the object becoming plain data at all.
    logged_block = loggable[0]["content"][0]
    assert isinstance(logged_block, dict)
    assert logged_block["type"] == "tool_use"
    assert logged_block["name"] == "check_availability"
    assert logged_block["input"] == {"practitioner_id": "abc"}


def test_to_loggable_messages_keeps_service_built_blocks() -> None:
    result_block = {"type": "tool_result", "tool_use_id": "toolu_1", "content": "{}"}

    loggable = to_loggable_messages([{"role": "user", "content": [result_block]}])

    assert loggable[0]["content"] == [result_block]


def test_to_loggable_messages_drops_the_thinking_signature() -> None:
    # The integrity token the API puts on a thinking block: a few hundred opaque
    # characters that say nothing about what the model saw.
    block = ThinkingBlock(
        thinking="", signature="EowDCpABCBEYAipA5RLe", type="thinking"
    )

    logged_block = to_loggable_messages([{"role": "assistant", "content": [block]}])[0][
        "content"
    ][0]

    assert logged_block == {"thinking": "", "type": "thinking"}


# --- 007 (FR-026): a third sender, and the seam 003 left for it --------------------
#
# `split_into_bursts` and `to_claude_messages` were written against the patient /
# not-patient distinction rather than against `sender == ASSISTANT`, and both docstrings
# say so. These tests pin that claim: if either needs a change for a staff message, the
# docstrings are what must be corrected with it.


def test_a_staff_message_joins_the_clinics_side_of_the_conversation() -> None:
    history = [
        _row(MessageSender.PATIENT, "is anyone there?", id="p1"),
        _row(MessageSender.ASSISTANT, "I've asked a colleague to look.", id="a1"),
        _row(MessageSender.STAFF, "Hi - I've got this now.", id="s1"),
        _row(MessageSender.PATIENT, "thank you", id="p2"),
    ]

    bursts = split_into_bursts(history)

    assert [_ids(burst) for burst in bursts] == [["p1"], ["a1", "s1"], ["p2"]]


def test_a_staff_burst_reaches_claude_as_the_assistant_role() -> None:
    history = [
        _row(MessageSender.PATIENT, "is anyone there?", id="p1"),
        _row(MessageSender.STAFF, "Hi - I've got this now.", id="s1"),
        _row(MessageSender.PATIENT, "thank you", id="p2"),
    ]

    entries = to_claude_messages(split_into_bursts(history))

    assert entries == [
        {"role": "user", "content": "is anyone there?"},
        {"role": "assistant", "content": "Hi - I've got this now."},
        {"role": "user", "content": "thank you"},
    ]


def test_a_staff_message_ends_a_patients_burst() -> None:
    # It is a reply, so the messages before it are answered and the ones after it are
    # a new turn - which is what keeps `derive_reply_to_message_ids` honest.
    history = [
        _row(MessageSender.PATIENT, "are you there", id="p1"),
        _row(MessageSender.PATIENT, "hello?", id="p2"),
        _row(MessageSender.STAFF, "I'm here.", id="s1"),
        _row(MessageSender.PATIENT, "great", id="p3"),
    ]

    bursts = split_into_bursts(history)

    assert derive_reply_to_message_ids(bursts) == ["p3"]


def test_an_assistant_reply_and_a_staff_reply_merge_into_one_entry() -> None:
    history = [
        _row(MessageSender.PATIENT, "when can I visit?", id="p1"),
        _row(MessageSender.ASSISTANT, "I don't have a confident answer.", id="a1"),
        _row(MessageSender.STAFF, "Visiting hours are 8am to 5pm.", id="s1"),
    ]

    entries = to_claude_messages(split_into_bursts(history))

    assert entries == [
        {"role": "user", "content": "when can I visit?"},
        {
            "role": "assistant",
            "content": (
                "I don't have a confident answer.\n\nVisiting hours are 8am to 5pm."
            ),
        },
    ]


# --- 007 (FR-019a/b): a turn answers only what arrived after the silence -----------


def _marked(sender: MessageSender, content: str, id: str, mark: str | None) -> Message:
    row = _row(sender, content, id)
    row.attention_mark = mark
    return row


def test_messages_that_arrived_while_silent_are_not_answered_retroactively() -> None:
    # FR-019a: a person was meant to answer them. Coming back to them once a pause
    # elapses answers a question the patient has moved on from, or that staff handled.
    history = [
        _marked(MessageSender.PATIENT, "are you there?", id="p1", mark="unanswered"),
        _marked(MessageSender.PATIENT, "hello?", id="p2", mark="unanswered"),
        _marked(MessageSender.PATIENT, "what are your hours?", id="p3", mark=None),
    ]

    bursts = exclude_silent_window(split_into_bursts(history))

    assert derive_reply_to_message_ids(bursts) == ["p3"]


def test_the_silenced_messages_stay_in_the_history_as_context() -> None:
    # The spec requires it in terms: they remain part of the conversation it reads for
    # context. Which is why this splits the burst rather than dropping anything.
    history = [
        _marked(MessageSender.PATIENT, "are you there?", id="p1", mark="unanswered"),
        _marked(MessageSender.PATIENT, "what are your hours?", id="p2", mark=None),
    ]

    bursts = exclude_silent_window(split_into_bursts(history))

    assert [_ids(burst) for burst in bursts] == [["p1"], ["p2"]]


def test_a_cleared_mark_puts_its_message_back_in_the_burst() -> None:
    # A staff reply clears the clearable marks, and a staff reply would have broken the
    # burst anyway - so an unmarked message is simply an ordinary one again.
    history = [
        _marked(MessageSender.PATIENT, "are you there?", id="p1", mark=None),
        _marked(MessageSender.PATIENT, "what are your hours?", id="p2", mark=None),
    ]

    bursts = exclude_silent_window(split_into_bursts(history))

    assert derive_reply_to_message_ids(bursts) == ["p1", "p2"]


def test_only_the_last_silenced_message_decides_where_the_split_falls() -> None:
    history = [
        _marked(MessageSender.PATIENT, "one", id="p1", mark="unanswered"),
        _marked(MessageSender.PATIENT, "two", id="p2", mark=None),
        _marked(MessageSender.PATIENT, "three", id="p3", mark="unanswered"),
        _marked(MessageSender.PATIENT, "four", id="p4", mark=None),
    ]

    bursts = exclude_silent_window(split_into_bursts(history))

    assert [_ids(burst) for burst in bursts] == [["p1", "p2", "p3"], ["p4"]]


def test_a_permanent_mark_does_not_exclude_its_message() -> None:
    # Only `unanswered` records that nothing answered a message. The other three record
    # that staff were called, on turns that answered the patient perfectly well.
    history = [
        _marked(MessageSender.PATIENT, "book me in", id="p1", mark="assistant_failed"),
        _marked(MessageSender.PATIENT, "and again", id="p2", mark=None),
    ]

    bursts = exclude_silent_window(split_into_bursts(history))

    assert derive_reply_to_message_ids(bursts) == ["p1", "p2"]


def test_an_unmarked_history_is_returned_exactly_as_it_was() -> None:
    history = [
        _row(MessageSender.PATIENT, "when can I visit?", id="p1"),
        _row(MessageSender.ASSISTANT, "8am to 5pm.", id="a1"),
        _row(MessageSender.PATIENT, "and on Sunday?", id="p2"),
    ]
    bursts = split_into_bursts(history)

    assert exclude_silent_window(bursts) == bursts


def test_an_empty_history_survives_the_exclusion() -> None:
    assert exclude_silent_window([]) == []


def test_the_split_bursts_rejoin_into_one_user_entry() -> None:
    # The Messages API requires strict alternation, and the split above is the one
    # place in the system that produces two consecutive patient-sided bursts. This is
    # where they are put back together, so no caller has to remember to.
    history = [
        _marked(MessageSender.PATIENT, "are you there?", id="p1", mark="unanswered"),
        _marked(MessageSender.PATIENT, "what are your hours?", id="p2", mark=None),
    ]

    entries = to_claude_messages(exclude_silent_window(split_into_bursts(history)))

    assert entries == [
        {"role": "user", "content": "are you there?\n\nwhat are your hours?"}
    ]


def test_rejoining_keeps_the_alternation_around_it() -> None:
    history = [
        _row(MessageSender.PATIENT, "when can I visit?", id="p1"),
        _row(MessageSender.ASSISTANT, "8am to 5pm.", id="a1"),
        _marked(MessageSender.PATIENT, "are you there?", id="p2", mark="unanswered"),
        _marked(MessageSender.PATIENT, "and on Sunday?", id="p3", mark=None),
    ]

    entries = to_claude_messages(exclude_silent_window(split_into_bursts(history)))

    assert [entry["role"] for entry in entries] == ["user", "assistant", "user"]
    assert entries[-1]["content"] == "are you there?\n\nand on Sunday?"


def test_the_silent_window_is_the_burst_the_exclusion_held_back() -> None:
    history = [
        _marked(MessageSender.PATIENT, "are you there?", id="p1", mark="unanswered"),
        _marked(MessageSender.PATIENT, "hello?", id="p2", mark="unanswered"),
        _marked(MessageSender.PATIENT, "what are your hours?", id="p3", mark=None),
    ]

    bursts = exclude_silent_window(split_into_bursts(history))

    assert _ids(silent_window(bursts)) == ["p1", "p2"]


def test_an_ordinary_history_has_no_silent_window() -> None:
    # `split_into_bursts` never produces two consecutive patient-sided bursts, so the
    # shape this looks for exists only where the exclusion made it.
    history = [
        _row(MessageSender.PATIENT, "when can I visit?", id="p1"),
        _row(MessageSender.ASSISTANT, "8am to 5pm.", id="a1"),
        _row(MessageSender.PATIENT, "and on Sunday?", id="p2"),
    ]

    assert silent_window(split_into_bursts(history)) == []
    assert silent_window(exclude_silent_window(split_into_bursts(history))) == []


def test_a_burst_of_quick_messages_is_not_a_silent_window() -> None:
    # Three lines typed in a row are one question, and 003's merging rule still owns
    # them - nothing was silenced, so nothing is held back.
    history = [
        _row(MessageSender.PATIENT, "When can I see", id="p1"),
        _row(MessageSender.PATIENT, "Dr. Josh?", id="p2"),
    ]

    bursts = exclude_silent_window(split_into_bursts(history))

    assert silent_window(bursts) == []
    assert derive_reply_to_message_ids(bursts) == ["p1", "p2"]


def test_an_empty_history_has_no_silent_window() -> None:
    assert silent_window([]) == []


def test_the_rendered_window_names_it_as_context_and_not_as_a_request() -> None:
    # The whole point of rendering it separately: a model reading one merged entry has
    # nothing to tell the two apart by, and acting on the window would answer - or
    # cancel - something a person had already taken over.
    history = [
        _marked(
            MessageSender.PATIENT,
            "cancel my Tuesday slot",
            id="p1",
            mark="unanswered",
        ),
        _marked(MessageSender.PATIENT, "when can I visit?", id="p2", mark=None),
    ]

    rendered = render_silent_window(
        silent_window(exclude_silent_window(split_into_bursts(history)))
    )

    assert "cancel my Tuesday slot" in rendered
    assert "when can I visit?" not in rendered
    assert "do not answer them" in rendered
    assert "do not act on them" in rendered


def test_nothing_is_rendered_when_nothing_was_silenced() -> None:
    assert render_silent_window([]) == ""
