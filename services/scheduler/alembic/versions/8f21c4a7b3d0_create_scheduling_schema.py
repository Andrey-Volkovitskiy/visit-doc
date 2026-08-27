"""create scheduling schema

Revision ID: 8f21c4a7b3d0
Revises:
Create Date: 2026-08-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8f21c4a7b3d0"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ULID = sa.String(26)
_NAME = sa.String(200)


def upgrade() -> None:
    # btree_gist lets an exclusion constraint mix an equality operator (on a plain
    # column) with an overlap operator (on a range) in one constraint - which is exactly
    # the shape "same practitioner AND overlapping interval" needs.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    # PostgreSQL ships tsrange/daterange/int4range but no range over bare `time`, and a
    # working range's non-overlap rule needs one. Keeps the columns readable as TIME.
    op.execute("CREATE TYPE timerange AS RANGE (subtype = time)")

    op.create_table(
        "practitioners",
        sa.Column("id", _ULID, primary_key=True),
        sa.Column("session_id", _ULID, nullable=False),
        sa.Column("full_name", _NAME, nullable=False),
        sa.Column("specialty", sa.String(64), nullable=False),
        sa.Column("appointment_duration_minutes", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "session_id", "full_name", name="practitioners_name_unique"
        ),
        sa.CheckConstraint(
            "appointment_duration_minutes BETWEEN 5 AND 480",
            name="practitioners_duration_range",
        ),
    )
    op.create_index("ix_practitioners_session_id", "practitioners", ["session_id"])

    op.create_table(
        "working_ranges",
        sa.Column("id", _ULID, primary_key=True),
        sa.Column(
            "practitioner_id",
            _ULID,
            sa.ForeignKey("practitioners.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.CheckConstraint(
            "weekday BETWEEN 0 AND 6", name="working_ranges_weekday_range"
        ),
        sa.CheckConstraint("end_time > start_time", name="working_ranges_ordered"),
    )
    op.create_index(
        "ix_working_ranges_practitioner_id", "working_ranges", ["practitioner_id"]
    )
    op.execute(
        "ALTER TABLE working_ranges ADD CONSTRAINT working_ranges_no_overlap "
        "EXCLUDE USING gist (practitioner_id WITH =, weekday WITH =, "
        "timerange(start_time, end_time) WITH &&)"
    )

    op.create_table(
        "patients",
        sa.Column("id", _ULID, primary_key=True),
        sa.Column("session_id", _ULID, nullable=False),
        sa.Column("chat_id", _ULID, nullable=False),
        sa.Column("full_name", _NAME, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("chat_id", name="patients_chat_unique"),
        sa.UniqueConstraint("session_id", "full_name", name="patients_name_unique"),
    )
    op.create_index("ix_patients_session_id", "patients", ["session_id"])

    op.create_table(
        "appointments",
        sa.Column("id", _ULID, primary_key=True),
        sa.Column("session_id", _ULID, nullable=False),
        sa.Column(
            "patient_id",
            _ULID,
            sa.ForeignKey("patients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "practitioner_id",
            _ULID,
            sa.ForeignKey("practitioners.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("starts_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("ends_at > starts_at", name="appointments_ordered"),
        sa.UniqueConstraint(
            "idempotency_key", name="appointments_idempotency_key_unique"
        ),
    )
    op.create_index("ix_appointments_session_id", "appointments", ["session_id"])
    op.create_index("ix_appointments_patient_id", "appointments", ["patient_id"])
    op.create_index(
        "ix_appointments_practitioner_id", "appointments", ["practitioner_id"]
    )
    # tsrange is half-open - [start, end) - so back-to-back appointments do not
    # conflict, which is what makes a contiguous slot grid bookable. These two
    # constraints, not any application check, are what make concurrent attempts on one
    # slot resolve to exactly one winner.
    op.execute(
        "ALTER TABLE appointments ADD CONSTRAINT appointments_patient_no_overlap "
        "EXCLUDE USING gist (patient_id WITH =, tsrange(starts_at, ends_at) WITH &&)"
    )
    op.execute(
        "ALTER TABLE appointments ADD CONSTRAINT appointments_practitioner_no_overlap "
        "EXCLUDE USING gist (practitioner_id WITH =, "
        "tsrange(starts_at, ends_at) WITH &&)"
    )


def downgrade() -> None:
    op.drop_table("appointments")
    op.drop_table("patients")
    op.drop_table("working_ranges")
    op.drop_table("practitioners")
    op.execute("DROP TYPE IF EXISTS timerange")
