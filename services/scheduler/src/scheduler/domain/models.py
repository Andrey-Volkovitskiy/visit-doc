"""SQLAlchemy 2.0 declarative models for the scheduling domain.

Every domain time here is a timezone-naive local wall-clock value: `TIMESTAMP WITHOUT
TIME ZONE` for appointment bounds, `TIME WITHOUT TIME ZONE` for working-range bounds.
`created_at`/`updated_at` are the sole exception - audit metadata, not a judgement about
anyone's day.

`session_id`, `chat_id`, and `patient_id`-on-`chats` are opaque ids from the chat
service's database. There is no foreign key to them and there cannot be one: the two
services own separate databases.
"""

from datetime import datetime, time
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Time,
    UniqueConstraint,
    func,
    literal_column,
)
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql.elements import ColumnClause

_ULID_LENGTH = 26
NAME_LENGTH = 200

# The half-open interval each appointment occupies, as the exclusion constraints see it.
# `tsrange` is `[start, end)`, so a 09:00-10:00 appointment does not conflict with a
# 10:00-11:00 one - which is what makes a contiguous slot grid bookable at all. The
# availability walk must treat overlap the same way, or the offer path and the write
# path would disagree about what "overlap" means.
_APPOINTMENT_INTERVAL: ColumnClause[Any] = literal_column("tsrange(starts_at, ends_at)")
# The equivalent for a working range. PostgreSQL ships no range type over bare `time`,
# so `timerange` is created by the initial migration.
_WORKING_RANGE_INTERVAL: ColumnClause[Any] = literal_column(
    "timerange(start_time, end_time)"
)


class Base(DeclarativeBase):
    """Declarative base for all SQLAlchemy models."""


def all_table_names() -> tuple[str, ...]:
    """Return every table this service owns, in declaration order.

    Read from the metadata rather than listed by hand, so a suite truncating "every
    scheduling table" between tests keeps doing that after a table is added - a
    hand-written list silently leaves the new one's rows to leak into the next test.
    """
    return tuple(table.name for table in Base.metadata.sorted_tables)


class Practitioner(Base):
    """Someone a patient can book an appointment with, within one session.

    `specialty` is stored as a plain string column, not a database-level enum, so a
    future value can be added with no schema migration - callers should still only ever
    write it via a `shared_models.scheduling.Specialty` member, never a bare string
    literal. It is display and matching data only: no availability, grid, or booking
    rule reads it.
    """

    __tablename__ = "practitioners"
    __table_args__ = (
        UniqueConstraint("session_id", "full_name", name="practitioners_name_unique"),
        CheckConstraint(
            "appointment_duration_minutes BETWEEN 5 AND 480",
            name="practitioners_duration_range",
        ),
        Index("ix_practitioners_session_id", "session_id"),
    )

    id: Mapped[str] = mapped_column(String(_ULID_LENGTH), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(_ULID_LENGTH), nullable=False)
    full_name: Mapped[str] = mapped_column(String(NAME_LENGTH), nullable=False)
    specialty: Mapped[str] = mapped_column(String(64), nullable=False)
    appointment_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WorkingRange(Base):
    """One continuous span on one weekday during which a practitioner takes bookings.

    A practitioner's schedule is the set of their ranges; zero ranges means a
    practitioner who is listed but never bookable. Carries no audit columns: a schedule
    edit replaces these rows wholesale rather than updating them, so the practitioner's
    own `updated_at` is the meaningful record.
    """

    __tablename__ = "working_ranges"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="working_ranges_weekday_range"),
        CheckConstraint("end_time > start_time", name="working_ranges_ordered"),
        ExcludeConstraint(
            ("practitioner_id", "="),
            ("weekday", "="),
            (_WORKING_RANGE_INTERVAL, "&&"),
            name="working_ranges_no_overlap",
            using="gist",
        ),
        Index("ix_working_ranges_practitioner_id", "practitioner_id"),
    )

    id: Mapped[str] = mapped_column(String(_ULID_LENGTH), primary_key=True)
    practitioner_id: Mapped[str] = mapped_column(
        String(_ULID_LENGTH),
        ForeignKey("practitioners.id", ondelete="CASCADE"),
        nullable=False,
    )
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)


class Patient(Base):
    """The person one chat books on behalf of, paired with that chat permanently."""

    __tablename__ = "patients"
    __table_args__ = (
        UniqueConstraint("chat_id", name="patients_chat_unique"),
        UniqueConstraint("session_id", "full_name", name="patients_name_unique"),
        Index("ix_patients_session_id", "session_id"),
    )

    id: Mapped[str] = mapped_column(String(_ULID_LENGTH), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(_ULID_LENGTH), nullable=False)
    chat_id: Mapped[str] = mapped_column(String(_ULID_LENGTH), nullable=False)
    full_name: Mapped[str] = mapped_column(String(NAME_LENGTH), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Appointment(Base):
    """One booked slot: a patient, a practitioner, and a half-open local time interval.

    `session_id` is denormalized off the patient so every read path can scope to a
    session without a join. `ends_at` records the practitioner's duration as of
    creation, which is why a later schedule or duration edit leaves stored appointments
    untouched - the row itself *is* the record of what was agreed.

    `idempotency_key` is written only on a successfully created appointment, and dies
    with it: a refused attempt leaves its key free to be tried again.
    """

    __tablename__ = "appointments"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="appointments_ordered"),
        UniqueConstraint("idempotency_key", name="appointments_idempotency_key_unique"),
        ExcludeConstraint(
            ("patient_id", "="),
            (_APPOINTMENT_INTERVAL, "&&"),
            name="appointments_patient_no_overlap",
            using="gist",
        ),
        ExcludeConstraint(
            ("practitioner_id", "="),
            (_APPOINTMENT_INTERVAL, "&&"),
            name="appointments_practitioner_no_overlap",
            using="gist",
        ),
        Index("ix_appointments_session_id", "session_id"),
        Index("ix_appointments_patient_id", "patient_id"),
        Index("ix_appointments_practitioner_id", "practitioner_id"),
    )

    id: Mapped[str] = mapped_column(String(_ULID_LENGTH), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(_ULID_LENGTH), nullable=False)
    patient_id: Mapped[str] = mapped_column(
        String(_ULID_LENGTH),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
    )
    practitioner_id: Mapped[str] = mapped_column(
        String(_ULID_LENGTH),
        ForeignKey("practitioners.id", ondelete="CASCADE"),
        nullable=False,
    )
    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False
    )
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
