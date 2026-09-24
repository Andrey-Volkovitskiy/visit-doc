"""Pydantic request/response DTOs for the admin API.

Every time here is a local wall-clock value with no offset: `HH:MM` (or
`YYYY-MM-DDTHH:MM:SS`) on the wire, a naive `time` (or `datetime`) in Python. The
validators are what stop one arriving with a zone attached.
"""

from datetime import datetime, time

from pydantic import BaseModel, Field, field_validator, model_validator
from shared_models.localtime import (
    format_local_time,
    parse_local_datetime,
    parse_local_time,
)
from shared_models.scheduling import Specialty, Weekday

from scheduler.domain.models import NAME_LENGTH

_MIN_DURATION_MINUTES = 5
_MAX_DURATION_MINUTES = 480


class WorkingRangeIn(BaseModel):
    """One span of a practitioner's weekly schedule, as supplied by a caller."""

    weekday: Weekday
    start_time: time
    end_time: time

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def _parse_local_time(cls, value: object) -> object:
        """Raises: ValueError if `value` is not an offset-free local time."""
        if isinstance(value, str):
            return parse_local_time(value)
        return value

    @field_validator("start_time", "end_time")
    @classmethod
    def _reject_timezone_aware(cls, value: time) -> time:
        """Raises: ValueError if `value` carries a timezone offset."""
        if value.tzinfo is not None:
            raise ValueError("times must carry no timezone offset")
        return value

    @model_validator(mode="after")
    def _reject_unordered(self) -> "WorkingRangeIn":
        """Raises: ValueError if the range does not end after it starts."""
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class WorkingRangeOut(BaseModel):
    """One span of a practitioner's weekly schedule, as returned."""

    weekday: Weekday
    start_time: str
    end_time: str


class PractitionerCreate(BaseModel):
    """`POST /practitioners` body.

    Every field is optional: a bare `{}` yields an immediately bookable practitioner
    with a pool name, General Practice, Monday-Friday 09:00-17:00, and 60-minute
    appointments. An explicitly empty `schedule` is different from an omitted one - it
    means someone listed but never bookable, which is a legal state.
    """

    full_name: str | None = Field(default=None, min_length=1, max_length=NAME_LENGTH)
    specialty: Specialty | None = None
    appointment_duration_minutes: int | None = Field(
        default=None, ge=_MIN_DURATION_MINUTES, le=_MAX_DURATION_MINUTES
    )
    schedule: list[WorkingRangeIn] | None = None


class PractitionerUpdate(BaseModel):
    """`PATCH /practitioners/{id}` body; omitted fields are left untouched.

    Edits that invalidate existing appointments - narrowing the schedule, changing the
    duration - are accepted, and those appointments keep the times they were agreed at.
    Only later bookings are validated against the new settings.
    """

    full_name: str | None = Field(default=None, min_length=1, max_length=NAME_LENGTH)
    specialty: Specialty | None = None
    appointment_duration_minutes: int | None = Field(
        default=None, ge=_MIN_DURATION_MINUTES, le=_MAX_DURATION_MINUTES
    )
    schedule: list[WorkingRangeIn] | None = None


class PractitionerOut(BaseModel):
    """A practitioner and their schedule."""

    id: str
    full_name: str
    specialty: Specialty
    appointment_duration_minutes: int
    schedule: list[WorkingRangeOut]


class PatientOut(BaseModel):
    """A patient and the chat they belong to."""

    id: str
    chat_id: str
    full_name: str


# The largest page this route will answer. Not a product decision - the console asks
# for far fewer - but a ceiling on what one request may cost, since the far end of the
# window is now optional and a practitioner's calendar grows without bound.
_MAX_LIMIT = 500


class PractitionerAppointmentsQuery(BaseModel):
    """`GET /practitioners/{id}/appointments` query: what to read, and how much.

    The caller decides all three; this side only answers, so it has no clock, no notion
    of how far ahead a console cares to look, and no opinion about how many rows a
    screen can hold.

    `starts_before` is optional, and omitting it means *no far end* - every standing
    appointment from `ends_after` onward, however far ahead it sits. That is the
    ordinary read; a far end is for a caller that genuinely wants a window.

    `limit` is required, and deliberately has no default. A route answering an unbounded
    future must be told how much of it to send: a default here would be this side
    inventing the caller's page size, and an uncapped answer is a table scan waiting to
    be returned over HTTP.
    """

    ends_after: datetime
    starts_before: datetime | None = None
    limit: int = Field(ge=1, le=_MAX_LIMIT)

    @field_validator("ends_after", "starts_before", mode="before")
    @classmethod
    def _parse_local_datetime(cls, value: object) -> object:
        """Raises: ValueError if `value` is not an offset-free local date-time."""
        if isinstance(value, str):
            return parse_local_datetime(value)
        return value

    @field_validator("ends_after", "starts_before")
    @classmethod
    def _reject_timezone_aware(cls, value: datetime | None) -> datetime | None:
        """Raises: ValueError if `value` carries a timezone offset."""
        if value is not None and value.tzinfo is not None:
            raise ValueError("date-times must carry no timezone offset")
        return value

    @model_validator(mode="after")
    def _reject_empty_window(self) -> "PractitionerAppointmentsQuery":
        """Raises: ValueError if a far end was given that the window never reaches.

        Only when one was given: no far end is not an empty window, it is every
        appointment from `ends_after` on.
        """
        if self.starts_before is not None and self.starts_before <= self.ends_after:
            raise ValueError("starts_before must be after ends_after")
        return self


class PractitionerAppointmentOut(BaseModel):
    """One standing appointment on a practitioner's calendar, with its patient named."""

    id: str
    patient_full_name: str
    starts_at: str
    ends_at: str


class PractitionerAppointmentsOut(BaseModel):
    """A practitioner's standing appointments from `ends_after` on, in start order.

    An empty list means nobody is booked, and only that: a practitioner this session
    cannot see is a 404, never an empty list.

    `has_more` says whether the query had to stop at `limit`, and is read from a row
    beyond it rather than inferred from the count - a list exactly `limit` long is the
    one case "as many as we asked for" cannot tell apart from "and that was all".
    """

    appointments: list[PractitionerAppointmentOut]
    has_more: bool


def to_working_range_out(weekday: Weekday, start: time, end: time) -> WorkingRangeOut:
    """Render one persisted working range for the wire."""
    return WorkingRangeOut(
        weekday=weekday,
        start_time=format_local_time(start),
        end_time=format_local_time(end),
    )
