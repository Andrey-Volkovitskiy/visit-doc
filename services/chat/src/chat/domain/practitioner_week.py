"""The console's week: the seven local days a practitioner's appointments are read for.

Decided here and nowhere else. The scheduler answers for whatever range it is given and
has no notion of a week, and the browser sends only its own clock, so the rule is one
pure function of that clock.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta

# Today counts as the first of them.
_WEEK_DAYS = 7


@dataclass(frozen=True)
class WeekBounds:
    """The window a practitioner's week is read for, both ends naive local times.

    An appointment belongs to it when it ends after `ends_after` and starts before
    `starts_before`, so one already under way at `ends_after` is in it.
    """

    ends_after: datetime
    starts_before: datetime


def practitioner_week_bounds(local_now: datetime) -> WeekBounds:
    """Return the week from `local_now` to midnight at the start of the eighth day.

    Args:
        local_now: The viewer's own clock, naive: there is no timezone to carry.

    The far end is a midnight rather than seven days after `local_now`, so the whole of
    the seventh day is in the window whatever time it is read at. Crossing midnight
    needs nothing: the next read carries the new date, and the window moves with it.
    """
    start_of_today = datetime.combine(local_now.date(), time.min)
    return WeekBounds(
        ends_after=local_now,
        starts_before=start_of_today + timedelta(days=_WEEK_DAYS),
    )
