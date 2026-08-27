"""Tests for the slot grid: which starts are offered, and which are not.

Pure-function tests against `domain/availability.py` - the same module the booking
validator reads, which is what makes an offered slot bookable by construction.
"""

from datetime import date, datetime, time, timedelta

import pytest
from scheduler.core.config import Settings
from scheduler.domain.availability import (
    DailyRange,
    Interval,
    available_starts,
    clamp_window,
    validate_start,
)
from shared_models.scheduling import BookingFailureReason

# 2026-08-18 is a Tuesday; 2026-08-16 a Sunday.
_TUESDAY = date(2026, 8, 18)
_WEDNESDAY = date(2026, 8, 19)
_SUNDAY = date(2026, 8, 16)
_TUESDAY_RANGE = DailyRange(weekday=1, start_time=time(9, 0), end_time=time(12, 0))
_LOCAL_NOW = datetime(2026, 8, 17, 8, 0)
_HORIZON_DAYS = 90
# The production caps, read from Settings rather than restated, so a change to either
# is exercised here rather than silently diverging from what the service applies.
_SETTINGS = Settings(SCHEDULER_DATABASE_URL="postgresql+asyncpg://u:p@localhost/db")
MAX_WINDOW_DAYS = _SETTINGS.AVAILABILITY_MAX_WINDOW_DAYS
MAX_SLOTS = _SETTINGS.AVAILABILITY_MAX_SLOTS


def _starts(
    *,
    schedule: list[DailyRange],
    duration_minutes: int = 60,
    busy: list[Interval] | None = None,
    from_date: date = _TUESDAY,
    to_date: date = _TUESDAY,
    local_now: datetime = _LOCAL_NOW,
) -> tuple[list[datetime], bool]:
    return available_starts(
        schedule=schedule,
        duration_minutes=duration_minutes,
        busy=busy or [],
        from_date=from_date,
        to_date=to_date,
        local_now=local_now,
        horizon_days=_HORIZON_DAYS,
        max_window_days=MAX_WINDOW_DAYS,
        max_slots=MAX_SLOTS,
    )


def test_the_grid_walks_from_the_working_ranges_own_start() -> None:
    starts, _ = _starts(schedule=[_TUESDAY_RANGE])

    assert starts == [
        datetime(2026, 8, 18, 9, 0),
        datetime(2026, 8, 18, 10, 0),
        datetime(2026, 8, 18, 11, 0),
    ]


def test_a_trailing_remainder_shorter_than_one_appointment_is_not_offered() -> None:
    # 09:00-11:30 fits two 60-minute slots; the last half hour is not a slot.
    schedule = [DailyRange(weekday=1, start_time=time(9, 0), end_time=time(11, 30))]
    starts, _ = _starts(schedule=schedule)

    assert starts == [datetime(2026, 8, 18, 9, 0), datetime(2026, 8, 18, 10, 0)]


def test_a_range_shorter_than_one_appointment_yields_no_slots_at_all() -> None:
    schedule = [DailyRange(weekday=1, start_time=time(9, 0), end_time=time(9, 45))]
    starts, _ = _starts(schedule=schedule)

    assert starts == []


def test_two_contiguous_ranges_each_anchor_their_own_grid() -> None:
    # 09:00-10:30 and 10:30-12:00, in 60-minute slots. Each range fits exactly one
    # whole slot from its own start; 10:00 is never offered, because it would run
    # across the junction into a range it does not belong to. Treating the pair as one
    # 09:00-12:00 span would have offered 09:00, 10:00 and 11:00 instead.
    schedule = [
        DailyRange(weekday=1, start_time=time(9, 0), end_time=time(10, 30)),
        DailyRange(weekday=1, start_time=time(10, 30), end_time=time(12, 0)),
    ]
    starts, _ = _starts(schedule=schedule)

    assert starts == [datetime(2026, 8, 18, 9, 0), datetime(2026, 8, 18, 10, 30)]


def test_a_slot_overlapping_an_existing_appointment_is_excluded() -> None:
    busy = [Interval(datetime(2026, 8, 18, 9, 30), datetime(2026, 8, 18, 10, 30))]
    starts, _ = _starts(schedule=[_TUESDAY_RANGE], busy=busy)

    assert starts == [datetime(2026, 8, 18, 11, 0)]


def test_a_slot_starting_exactly_when_a_busy_interval_ends_is_offered() -> None:
    busy = [Interval(datetime(2026, 8, 18, 9, 0), datetime(2026, 8, 18, 10, 0))]
    starts, _ = _starts(schedule=[_TUESDAY_RANGE], busy=busy)

    assert datetime(2026, 8, 18, 10, 0) in starts
    assert datetime(2026, 8, 18, 9, 0) not in starts


def test_a_slot_starting_exactly_at_local_now_is_not_offered() -> None:
    starts, _ = _starts(
        schedule=[_TUESDAY_RANGE], local_now=datetime(2026, 8, 18, 9, 0)
    )

    assert datetime(2026, 8, 18, 9, 0) not in starts
    assert starts[0] == datetime(2026, 8, 18, 10, 0)


def test_a_slot_one_minute_after_local_now_is_offered() -> None:
    starts, _ = _starts(
        schedule=[_TUESDAY_RANGE], local_now=datetime(2026, 8, 18, 8, 59)
    )

    assert starts[0] == datetime(2026, 8, 18, 9, 0)


def test_a_slot_exactly_at_the_horizon_boundary_is_offered() -> None:
    local_now = datetime(2026, 8, 18, 9, 0) - timedelta(days=_HORIZON_DAYS)
    starts, _ = _starts(schedule=[_TUESDAY_RANGE], local_now=local_now)

    assert datetime(2026, 8, 18, 9, 0) in starts


def test_a_slot_one_minute_past_the_horizon_is_not_offered() -> None:
    local_now = datetime(2026, 8, 18, 8, 59) - timedelta(days=_HORIZON_DAYS)
    starts, _ = _starts(schedule=[_TUESDAY_RANGE], local_now=local_now)

    assert datetime(2026, 8, 18, 9, 0) not in starts


def test_a_local_now_on_a_different_day_from_the_host_clock_still_filters() -> None:
    """A stray `datetime.now()` in the walk would ignore this `local_now` entirely.

    The caller's clock is deliberately set to a day the host's certainly is not, so a
    server-side clock reading would produce a different answer.
    """
    starts, _ = _starts(
        schedule=[_TUESDAY_RANGE],
        local_now=datetime(2026, 8, 18, 10, 0),
    )

    assert starts == [datetime(2026, 8, 18, 11, 0)]


def test_a_grandfathered_appointment_removes_the_slots_it_overlaps() -> None:
    # An appointment left over from a wider schedule, now outside the current range's
    # own hours at one end but still overlapping a current slot.
    busy = [Interval(datetime(2026, 8, 18, 8, 30), datetime(2026, 8, 18, 9, 30))]
    starts, _ = _starts(schedule=[_TUESDAY_RANGE], busy=busy)

    assert datetime(2026, 8, 18, 9, 0) not in starts
    assert starts == [datetime(2026, 8, 18, 10, 0), datetime(2026, 8, 18, 11, 0)]


def test_a_grandfathered_appointments_own_time_is_never_offered_back() -> None:
    # 08:30 lies outside every current range, so the walk never generates it - the grid
    # comes from the current schedule, not from what was once booked.
    busy = [Interval(datetime(2026, 8, 18, 8, 30), datetime(2026, 8, 18, 9, 30))]
    starts, _ = _starts(schedule=[_TUESDAY_RANGE], busy=busy)

    assert datetime(2026, 8, 18, 8, 30) not in starts


def test_a_weekday_with_no_working_range_yields_nothing() -> None:
    starts, _ = _starts(schedule=[_TUESDAY_RANGE], from_date=_SUNDAY, to_date=_SUNDAY)

    assert starts == []


def test_a_window_spanning_several_days_returns_them_in_order() -> None:
    schedule = [
        DailyRange(weekday=1, start_time=time(9, 0), end_time=time(10, 0)),
        DailyRange(weekday=2, start_time=time(9, 0), end_time=time(10, 0)),
    ]
    starts, truncated = _starts(
        schedule=schedule, from_date=_TUESDAY, to_date=_WEDNESDAY
    )

    assert starts == [datetime(2026, 8, 18, 9, 0), datetime(2026, 8, 19, 9, 0)]
    assert truncated is False


def test_a_window_longer_than_the_cap_is_clamped_and_marked_truncated() -> None:
    schedule = [DailyRange(weekday=1, start_time=time(9, 0), end_time=time(10, 0))]
    starts, truncated = _starts(
        schedule=schedule,
        from_date=_TUESDAY,
        to_date=_TUESDAY + timedelta(days=60),
    )

    assert truncated is True
    # Two Tuesdays fall inside a 14-day window from a Tuesday; the rest are clamped off.
    assert starts == [
        datetime(2026, 8, 18, 9, 0),
        datetime(2026, 8, 25, 9, 0),
    ]
    assert all(s.date() < _TUESDAY + timedelta(days=MAX_WINDOW_DAYS) for s in starts)


def test_more_starts_than_the_cap_are_truncated_rather_than_refused() -> None:
    # 15-minute slots across a full working week produce well over the cap.
    schedule = [
        DailyRange(weekday=weekday, start_time=time(9, 0), end_time=time(17, 0))
        for weekday in range(5)
    ]
    starts, truncated = _starts(
        schedule=schedule,
        duration_minutes=15,
        from_date=_TUESDAY,
        to_date=_TUESDAY + timedelta(days=6),
    )

    assert truncated is True
    assert len(starts) == MAX_SLOTS
    assert starts == sorted(starts)


def test_an_empty_result_with_no_truncation_means_genuinely_nothing_bookable() -> None:
    busy = [Interval(datetime(2026, 8, 18, 9, 0), datetime(2026, 8, 18, 12, 0))]
    starts, truncated = _starts(schedule=[_TUESDAY_RANGE], busy=busy)

    assert starts == []
    assert truncated is False


def test_an_empty_schedule_offers_nothing() -> None:
    starts, truncated = _starts(schedule=[])

    assert starts == []
    assert truncated is False


def test_every_offered_start_passes_the_booking_validator() -> None:
    schedule = [
        DailyRange(weekday=1, start_time=time(9, 0), end_time=time(10, 30)),
        DailyRange(weekday=1, start_time=time(10, 30), end_time=time(12, 0)),
    ]
    starts, _ = _starts(schedule=schedule, duration_minutes=45)

    assert starts
    for start in starts:
        assert (
            validate_start(
                start,
                schedule=schedule,
                duration_minutes=45,
                local_now=_LOCAL_NOW,
                horizon_days=_HORIZON_DAYS,
            )
            is None
        )


def test_a_start_inside_no_working_range_is_outside_schedule_never_off_grid() -> None:
    reason = validate_start(
        datetime(2026, 8, 18, 14, 15),
        schedule=[_TUESDAY_RANGE],
        duration_minutes=60,
        local_now=_LOCAL_NOW,
        horizon_days=_HORIZON_DAYS,
    )

    assert reason is BookingFailureReason.OUTSIDE_SCHEDULE


def test_a_start_inside_a_range_but_off_its_grid_is_off_grid() -> None:
    reason = validate_start(
        datetime(2026, 8, 18, 9, 30),
        schedule=[_TUESDAY_RANGE],
        duration_minutes=60,
        local_now=_LOCAL_NOW,
        horizon_days=_HORIZON_DAYS,
    )

    assert reason is BookingFailureReason.OFF_GRID


@pytest.mark.parametrize(
    "start",
    [
        datetime(2026, 8, 18, 9, 0, 45),
        datetime(2026, 8, 18, 9, 0, 0, 500_000),
        # The last slot of the range: truncated to 11:00 it fits exactly, so it passes
        # the containment check and would really end at 12:00:45 - past the range.
        datetime(2026, 8, 18, 11, 0, 45),
    ],
)
def test_a_start_carrying_seconds_is_never_on_the_grid(start: datetime) -> None:
    """Minute-resolution arithmetic would truncate the remainder away.

    The stored appointment keeps the full precision, so an accepted start would sit off
    every offered slot, overlap the two adjacent ones, and be able to end after the
    working range it was validated against.
    """
    reason = validate_start(
        start,
        schedule=[_TUESDAY_RANGE],
        duration_minutes=60,
        local_now=_LOCAL_NOW,
        horizon_days=_HORIZON_DAYS,
    )

    assert reason is BookingFailureReason.OFF_GRID


def test_a_reversed_window_is_rejected_rather_than_reported_as_empty() -> None:
    # ([], truncated=False) is the contract's "genuinely nothing bookable", so a
    # reversed window must not be able to produce it.
    with pytest.raises(ValueError, match="precedes"):
        clamp_window(date(2026, 8, 25), date(2026, 8, 18), MAX_WINDOW_DAYS)


def test_an_appointment_spanning_two_contiguous_ranges_is_outside_schedule() -> None:
    schedule = [
        DailyRange(weekday=1, start_time=time(9, 0), end_time=time(10, 0)),
        DailyRange(weekday=1, start_time=time(10, 0), end_time=time(11, 0)),
    ]
    # On the first range's grid, but 90 minutes runs past its end into the second.
    reason = validate_start(
        datetime(2026, 8, 18, 9, 0),
        schedule=schedule,
        duration_minutes=90,
        local_now=_LOCAL_NOW,
        horizon_days=_HORIZON_DAYS,
    )

    assert reason is BookingFailureReason.OUTSIDE_SCHEDULE


def test_a_start_at_exactly_local_now_is_in_past() -> None:
    reason = validate_start(
        datetime(2026, 8, 18, 9, 0),
        schedule=[_TUESDAY_RANGE],
        duration_minutes=60,
        local_now=datetime(2026, 8, 18, 9, 0),
        horizon_days=_HORIZON_DAYS,
    )

    assert reason is BookingFailureReason.IN_PAST


def test_in_past_outranks_outside_schedule_when_both_hold() -> None:
    # A Sunday (no range at all) in the past reports the past, not the schedule.
    reason = validate_start(
        datetime(2026, 8, 16, 14, 0),
        schedule=[_TUESDAY_RANGE],
        duration_minutes=60,
        local_now=_LOCAL_NOW,
        horizon_days=_HORIZON_DAYS,
    )

    assert reason is BookingFailureReason.IN_PAST


def test_beyond_horizon_outranks_outside_schedule_when_both_hold() -> None:
    far_future = _LOCAL_NOW + timedelta(days=_HORIZON_DAYS + 1)
    # Land on a Sunday, which has no working range either.
    while far_future.weekday() != 6:
        far_future += timedelta(days=1)

    reason = validate_start(
        far_future,
        schedule=[_TUESDAY_RANGE],
        duration_minutes=60,
        local_now=_LOCAL_NOW,
        horizon_days=_HORIZON_DAYS,
    )

    assert reason is BookingFailureReason.BEYOND_HORIZON


def test_outside_schedule_outranks_off_grid_when_both_could_be_argued() -> None:
    # 12:30 is past the range's end entirely: there is no range to be off the grid of.
    reason = validate_start(
        datetime(2026, 8, 18, 12, 30),
        schedule=[_TUESDAY_RANGE],
        duration_minutes=60,
        local_now=_LOCAL_NOW,
        horizon_days=_HORIZON_DAYS,
    )

    assert reason is BookingFailureReason.OUTSIDE_SCHEDULE
