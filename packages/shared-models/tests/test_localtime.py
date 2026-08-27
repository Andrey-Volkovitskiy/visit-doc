from datetime import UTC, date, datetime, time, timedelta, timezone

import pytest
from shared_models.localtime import (
    format_local_date,
    format_local_datetime,
    format_local_time,
    parse_local_date,
    parse_local_datetime,
    parse_local_time,
)


def test_parse_returns_a_naive_datetime() -> None:
    parsed = parse_local_datetime("2026-08-14T09:00:00")
    assert parsed == datetime(2026, 8, 14, 9, 0, 0)
    assert parsed.tzinfo is None


def test_round_trips_through_format_and_parse() -> None:
    value = datetime(2026, 8, 14, 9, 30, 15)
    assert parse_local_datetime(format_local_datetime(value)) == value


def test_format_always_emits_seconds() -> None:
    assert format_local_datetime(datetime(2026, 8, 14, 9, 0)) == "2026-08-14T09:00:00"


def test_parse_accepts_a_value_without_seconds() -> None:
    assert parse_local_datetime("2026-08-14T09:00") == datetime(2026, 8, 14, 9, 0)


@pytest.mark.parametrize(
    "value",
    [
        "2026-08-14T09:00:00Z",
        "2026-08-14T09:00:00+00:00",
        "2026-08-14T09:00:00+02:00",
        "2026-08-14T09:00:00-05:00",
    ],
)
def test_parse_rejects_any_offset_or_z(value: str) -> None:
    with pytest.raises(ValueError):
        parse_local_datetime(value)


def test_parse_rejects_a_value_that_is_not_a_date_time_at_all() -> None:
    with pytest.raises(ValueError):
        parse_local_datetime("next Tuesday")


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 8, 14, 9, 0, tzinfo=UTC),
        datetime(2026, 8, 14, 9, 0, tzinfo=timezone(timedelta(hours=3))),
    ],
)
def test_format_rejects_a_timezone_aware_datetime(value: datetime) -> None:
    with pytest.raises(ValueError):
        format_local_datetime(value)


def test_date_round_trips() -> None:
    assert parse_local_date(format_local_date(date(2026, 8, 14))) == date(2026, 8, 14)


def test_parse_date_rejects_a_date_time() -> None:
    with pytest.raises(ValueError):
        parse_local_date("2026-08-14T09:00:00")


def test_time_round_trips_at_minute_precision() -> None:
    assert format_local_time(time(9, 0)) == "09:00"
    assert parse_local_time("09:00") == time(9, 0)


def test_parse_time_rejects_an_offset() -> None:
    with pytest.raises(ValueError):
        parse_local_time("09:00+02:00")


def test_format_time_rejects_a_timezone_aware_time() -> None:
    with pytest.raises(ValueError):
        format_local_time(time(9, 0, tzinfo=UTC))
