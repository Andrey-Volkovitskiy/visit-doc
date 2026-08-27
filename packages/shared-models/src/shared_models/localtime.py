"""Parsing and formatting for the local wall-clock values both services exchange.

There is no timezone anywhere in this system: nothing is stored, converted, or
configurable, so a value carrying an offset (or a `Z`) is not a differently-expressed
local time - it is a different kind of value, and accepting it would silently assert a
zone that does not exist. Every function here therefore rejects one rather than
normalizing it.

The wire forms are ISO-8601 without an offset: `"2026-08-14T09:00:00"` for a date-time,
`"2026-08-14"` for a date, and `"09:00"` for a time.
"""

from datetime import date, datetime, time

_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%S"
_TIME_FORMAT = "%H:%M"


def parse_local_datetime(value: str) -> datetime:
    """Parse an offset-free ISO-8601 date-time into a naive `datetime`.

    Raises: ValueError if `value` is not an ISO-8601 date-time, carries an offset or a
        trailing `Z`, or omits the time part entirely.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"not an ISO-8601 local date-time: {value!r}") from exc
    if parsed.tzinfo is not None:
        raise ValueError(f"local date-time must carry no timezone offset: {value!r}")
    if "T" not in value and " " not in value:
        raise ValueError(f"local date-time must include a time part: {value!r}")
    return parsed


def format_local_datetime(value: datetime) -> str:
    """Render a naive `datetime` as an offset-free ISO-8601 string, always to seconds.

    Raises: ValueError if `value` is timezone-aware.
    """
    if value.tzinfo is not None:
        raise ValueError("local date-time must be timezone-naive")
    return value.strftime(_DATETIME_FORMAT)


def parse_local_date(value: str) -> date:
    """Parse a `YYYY-MM-DD` string into a `date`.

    Raises: ValueError if `value` is not a bare local date - a date-time, or anything
        carrying an offset, is rejected rather than truncated.
    """
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"not a local date: {value!r}") from exc


def format_local_date(value: date) -> str:
    """Render a `date` as `YYYY-MM-DD`."""
    return value.isoformat()


def parse_local_time(value: str) -> time:
    """Parse an offset-free `HH:MM` (or `HH:MM:SS`) string into a naive `time`.

    Raises: ValueError if `value` is not a local time or carries an offset.
    """
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"not a local time: {value!r}") from exc
    if parsed.tzinfo is not None:
        raise ValueError(f"local time must carry no timezone offset: {value!r}")
    return parsed


def format_local_time(value: time) -> str:
    """Render a naive `time` as `HH:MM`.

    Raises: ValueError if `value` is timezone-aware.
    """
    if value.tzinfo is not None:
        raise ValueError("local time must be timezone-naive")
    return value.strftime(_TIME_FORMAT)
