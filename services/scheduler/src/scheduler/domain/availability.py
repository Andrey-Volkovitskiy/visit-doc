"""The one slot-grid implementation, used by both availability and booking.

Every predicate that decides whether a start time is bookable is written exactly once,
here, and both `CheckAvailability` and `BookAppointment`'s validator read it. That is
what makes "every offered slot is bookable at the moment it is offered" true by
construction rather than by two implementations happening to agree - a boundary written
twice is a boundary that eventually differs by an equals sign.

Intervals are half-open throughout: `[start, end)`. An appointment ending at 10:00 does
not conflict with one starting at 10:00, which is what makes a contiguous grid bookable
at all, and it matches the semantics the database's exclusion constraints enforce.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from shared_models.scheduling import BookingFailureReason, Weekday


@dataclass(frozen=True)
class Interval:
    """A half-open local time interval, `[start, end)`."""

    start: datetime
    end: datetime

    def overlaps(self, other: "Interval") -> bool:
        """Whether the two intervals share any instant, treating both as half-open."""
        return self.start < other.end and other.start < self.end


@dataclass(frozen=True)
class DailyRange:
    """One working range: a weekday and the span of local time it covers."""

    weekday: Weekday
    start_time: time
    end_time: time


def _minutes(value: time) -> int:
    """Return `value` as whole minutes from midnight."""
    return value.hour * 60 + value.minute


def _at(day: date, minutes_from_midnight: int) -> datetime:
    """Return the local date-time `minutes_from_midnight` into `day`."""
    return datetime.combine(day, time()) + timedelta(minutes=minutes_from_midnight)


def is_in_past(start: datetime, local_now: datetime) -> bool:
    """Whether `start` is at or before the caller's clock.

    The boundary itself counts as past: a slot starting at exactly `local_now` is
    refused, so everything bookable is also listable under a strictly-after filter.
    """
    return start <= local_now


def is_beyond_horizon(start: datetime, local_now: datetime, horizon_days: int) -> bool:
    """Whether `start` falls past the booking horizon, whose boundary is inclusive."""
    return start > local_now + timedelta(days=horizon_days)


def containing_range(schedule: list[DailyRange], slot: Interval) -> DailyRange | None:
    """Return the single working range that wholly contains `slot`, if any.

    A slot spanning two contiguous ranges is contained by neither and yields None, which
    is what keeps an appointment inside one range rather than merely inside working
    time.
    """
    weekday = slot.start.weekday()
    slot_start = _minutes(slot.start.time())
    # Measured from the start day so a slot running past midnight is longer than any
    # range rather than wrapping around into one.
    slot_end = slot_start + int((slot.end - slot.start).total_seconds() // 60)
    for working_range in schedule:
        if working_range.weekday != weekday:
            continue
        if _minutes(working_range.start_time) <= slot_start and slot_end <= _minutes(
            working_range.end_time
        ):
            return working_range
    return None


def is_on_grid(
    working_range: DailyRange, start: datetime, duration_minutes: int
) -> bool:
    """Whether `start` falls on `working_range`'s own grid of appointment starts.

    Each range anchors its own grid at its own start, so two contiguous ranges do not
    share one and no slot straddles their junction.

    A start carrying seconds or microseconds is on no grid at all. Every generated slot
    lands on a whole minute, and the minute-resolution arithmetic below would otherwise
    silently truncate the remainder - accepting a start that is off the grid, overlaps
    two adjacent slots, and can run past the end of the range that contains it.
    """
    if start.second or start.microsecond:
        return False
    offset = _minutes(start.time()) - _minutes(working_range.start_time)
    return offset >= 0 and offset % duration_minutes == 0


def validate_start(
    start: datetime,
    *,
    schedule: list[DailyRange],
    duration_minutes: int,
    local_now: datetime,
    horizon_days: int,
) -> BookingFailureReason | None:
    """Return the first rule `start` breaks, or None if it breaks none.

    Evaluated in the fixed refusal precedence - in-past, then beyond-horizon, then
    outside-schedule, then off-grid - so an attempt breaking several rules always
    reports the same one. Overlap is deliberately absent: that is the datastore's to
    decide, at insert, and it comes last in the same precedence.

    A start inside no working range is `OUTSIDE_SCHEDULE` and never `OFF_GRID`, since
    being off a grid presupposes a range to be off the grid of.
    """
    if is_in_past(start, local_now):
        return BookingFailureReason.IN_PAST
    if is_beyond_horizon(start, local_now, horizon_days):
        return BookingFailureReason.BEYOND_HORIZON
    slot = Interval(start, start + timedelta(minutes=duration_minutes))
    working_range = containing_range(schedule, slot)
    if working_range is None:
        return BookingFailureReason.OUTSIDE_SCHEDULE
    if not is_on_grid(working_range, start, duration_minutes):
        return BookingFailureReason.OFF_GRID
    return None


def clamp_window(
    from_date: date, to_date: date, max_window_days: int
) -> tuple[date, bool]:
    """Clamp a requested availability window to the maximum span.

    Returns: the effective end date, and whether it was clamped.

    Raises: ValueError if `to_date` precedes `from_date`. A reversed window would
        otherwise walk zero days and produce an empty, untruncated result - which the
        contract reserves for "genuinely nothing bookable", so the caller would be told
        a free week is fully booked.

    An over-wide window is never an error - it comes back shortened and marked.
    """
    if to_date < from_date:
        raise ValueError(f"to_date {to_date} precedes from_date {from_date}")
    latest = from_date + timedelta(days=max_window_days - 1)
    if to_date > latest:
        return latest, True
    return to_date, False


def available_starts(
    *,
    schedule: list[DailyRange],
    duration_minutes: int,
    busy: list[Interval],
    from_date: date,
    to_date: date,
    local_now: datetime,
    horizon_days: int,
    max_window_days: int,
    max_slots: int,
) -> tuple[list[datetime], bool]:
    """Return the start times bookable in the window, and whether the result was capped.

    Args:
        busy: Every interval already taken, by the practitioner *or* by the requesting
            patient with anyone. Both must be excluded, or a slot could be offered that
            the patient's own commitments would then refuse.

    Returns: the bookable starts in ascending order, and a flag that is true when the
        window was clamped or more starts existed than the cap allows - so an empty list
        with the flag false is the only thing that means "genuinely nothing bookable".

    Raises: ValueError propagated from `clamp_window()` if `to_date` precedes
        `from_date`.

    Slots are generated per working range, walking from that range's own start in
    duration-length steps and dropping a trailing remainder too short for a whole
    appointment; a range shorter than one appointment therefore contributes nothing.
    Every other filter is `validate_start`'s, so an offered slot cannot fail a rule the
    booking path would apply.
    """
    effective_end, truncated = clamp_window(from_date, to_date, max_window_days)
    # Bucketed once instead of scanned whole per candidate slot: a slot can only
    # collide with something on its own day, so an appointment-heavy practitioner would
    # otherwise cost every generated slot a pass over every booking in the window.
    busy_by_day = _busy_by_day(busy)
    starts: list[datetime] = []
    day = from_date
    while day <= effective_end:
        for working_range in schedule:
            if working_range.weekday != day.weekday():
                continue
            starts.extend(
                _range_slots(
                    working_range,
                    day,
                    duration_minutes,
                    busy_by_day.get(day, ()),
                    local_now,
                    horizon_days,
                )
            )
        # Days are walked in ascending order and each range's slots are generated in
        # ascending order, so once the cap is reached nothing later can displace an
        # already-collected start - the remaining days are work whose result is thrown
        # away.
        if len(starts) > max_slots:
            return sorted(starts)[:max_slots], True
        day += timedelta(days=1)

    starts.sort()
    if len(starts) > max_slots:
        return starts[:max_slots], True
    return starts, truncated


def _busy_by_day(busy: list[Interval]) -> dict[date, list[Interval]]:
    """Group busy intervals by every local day they touch.

    An interval is filed under its end date as well as its start, so an appointment
    running across midnight is still seen by the following day's slots.
    """
    by_day: dict[date, list[Interval]] = {}
    for interval in busy:
        day = interval.start.date()
        last = interval.end.date()
        while day <= last:
            by_day.setdefault(day, []).append(interval)
            day += timedelta(days=1)
    return by_day


def _range_slots(
    working_range: DailyRange,
    day: date,
    duration_minutes: int,
    busy: "Sequence[Interval]",
    local_now: datetime,
    horizon_days: int,
) -> list[datetime]:
    """Return the bookable starts one working range contributes on `day`."""
    starts: list[datetime] = []
    range_end = _minutes(working_range.end_time)
    offset = _minutes(working_range.start_time)
    while offset + duration_minutes <= range_end:
        start = _at(day, offset)
        slot = Interval(start, start + timedelta(minutes=duration_minutes))
        offset += duration_minutes
        if is_in_past(start, local_now) or is_beyond_horizon(
            start, local_now, horizon_days
        ):
            continue
        if any(slot.overlaps(taken) for taken in busy):
            continue
        starts.append(start)
    return starts
