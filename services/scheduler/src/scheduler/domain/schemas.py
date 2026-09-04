"""Pydantic request/response DTOs for the admin API.

Every time here is a local wall-clock value with no offset: `HH:MM` on the wire, a naive
`time` in Python. The validators are what stop one arriving with a zone attached.
"""

from datetime import time

from pydantic import BaseModel, Field, field_validator, model_validator
from shared_models.localtime import format_local_time, parse_local_time
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


def to_working_range_out(weekday: Weekday, start: time, end: time) -> WorkingRangeOut:
    """Render one persisted working range for the wire."""
    return WorkingRangeOut(
        weekday=weekday,
        start_time=format_local_time(start),
        end_time=format_local_time(end),
    )
