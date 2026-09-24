"""`practitioner_week_bounds` - the one place the console's seven-day window is decided.

The window is today and the six days after it, counted from the viewer's own clock: an
appointment that has not yet ended is in it, including one already under way, and
anything starting from midnight at the start of the eighth day is not.
"""

from datetime import datetime

import pytest
from chat.domain.practitioner_week import WeekBounds, practitioner_week_bounds


@pytest.mark.parametrize(
    ("local_now", "starts_before"),
    [
        (datetime(2026, 9, 24, 14, 30), datetime(2026, 10, 1, 0, 0)),
        (datetime(2026, 9, 24, 23, 59, 59), datetime(2026, 10, 1, 0, 0)),
        (datetime(2026, 9, 24, 0, 0, 0), datetime(2026, 10, 1, 0, 0)),
        # Across a month end, a year end and a leap day.
        (datetime(2026, 9, 28, 9, 0), datetime(2026, 10, 5, 0, 0)),
        (datetime(2026, 12, 29, 9, 0), datetime(2027, 1, 5, 0, 0)),
        (datetime(2028, 2, 25, 9, 0), datetime(2028, 3, 3, 0, 0)),
    ],
)
def test_the_window_runs_from_now_to_midnight_starting_the_eighth_day(
    local_now: datetime, starts_before: datetime
) -> None:
    assert practitioner_week_bounds(local_now) == WeekBounds(
        ends_after=local_now, starts_before=starts_before
    )


def test_the_window_moves_on_at_midnight() -> None:
    # One second apart, a day apart at the far end: the next read after midnight
    # carries the new day, and the window follows it with nothing else to update.
    before = practitioner_week_bounds(datetime(2026, 9, 24, 23, 59, 59))
    after = practitioner_week_bounds(datetime(2026, 9, 25, 0, 0, 0))

    assert before.starts_before == datetime(2026, 10, 1, 0, 0)
    assert after.starts_before == datetime(2026, 10, 2, 0, 0)
