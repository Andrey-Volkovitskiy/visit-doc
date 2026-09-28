"""`describe_recent_changes`: the sentence Claude is asked to relay as it stands.

Pinned as a table, because a sentence's wording is exactly the thing a small edit
changes without anyone noticing.
"""

import pytest
from chat.connectors.recent_changes_text import describe_recent_changes
from chat.repositories.booking_act_repository import RecentBookingChanges


def _changes(
    booked: int, cancelled: int, rescheduled: int, unknown: int
) -> RecentBookingChanges:
    return RecentBookingChanges(
        window_minutes=60,
        booked=booked,
        cancelled=cancelled,
        rescheduled=rescheduled,
        outcome_unknown=unknown,
    )


@pytest.mark.parametrize(
    ("counts", "text"),
    [
        # contracts/mcp-tools.md's table, row for row.
        (
            (3, 1, 2, 0),
            "Three appointments were booked, one cancelled and two rescheduled.",
        ),
        ((1, 0, 2, 0), "One appointment was booked and two rescheduled."),
        ((0, 1, 0, 0), "One appointment was cancelled."),
        ((12, 0, 0, 0), "12 appointments were booked."),
        ((0, 0, 0, 0), "No appointments were booked, cancelled or rescheduled."),
        (
            (3, 1, 2, 2),
            (
                "Three appointments were booked, one cancelled and two rescheduled. "
                "Two changes have an unknown outcome — check the Staff Console."
            ),
        ),
        (
            (0, 0, 0, 1),
            (
                "No appointments were booked, cancelled or rescheduled. "
                "One change has an unknown outcome — check the Staff Console."
            ),
        ),
    ],
)
def test_the_contracts_table(counts: tuple[int, int, int, int], text: str) -> None:
    assert describe_recent_changes(_changes(*counts)) == text


@pytest.mark.parametrize(
    ("counts", "text"),
    [
        ((10, 0, 0, 0), "Ten appointments were booked."),
        ((11, 0, 0, 0), "11 appointments were booked."),
        ((0, 10, 11, 0), "Ten appointments were cancelled and 11 rescheduled."),
        ((1, 11, 0, 0), "One appointment was booked and 11 cancelled."),
        ((0, 0, 1, 0), "One appointment was rescheduled."),
        (
            (0, 0, 0, 11),
            (
                "No appointments were booked, cancelled or rescheduled. "
                "11 changes have an unknown outcome — check the Staff Console."
            ),
        ),
    ],
)
def test_numbers_are_words_to_ten_and_digits_from_eleven(
    counts: tuple[int, int, int, int], text: str
) -> None:
    # FR-017a's boundary, in the subject, in a later clause and in the second sentence.
    assert describe_recent_changes(_changes(*counts)) == text


def test_the_order_is_booked_cancelled_rescheduled_whatever_the_counts() -> None:
    text = describe_recent_changes(_changes(1, 5, 9, 0))

    assert text == "One appointment was booked, five cancelled and nine rescheduled."
