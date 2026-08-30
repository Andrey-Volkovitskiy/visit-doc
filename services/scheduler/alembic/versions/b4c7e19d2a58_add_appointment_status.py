"""add appointment status

Revision ID: b4c7e19d2a58
Revises: 8f21c4a7b3d0
Create Date: 2026-08-30

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4c7e19d2a58"
down_revision: str | None = "8f21c4a7b3d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every object below carries this predicate, and that is the whole migration: an
# appointment can now be present in the store while counting for nothing. Written out
# rather than imported so the migration keeps describing the schema it created even if
# the enum's values change later.
_IS_STANDING = "status = 'standing'"

_PATIENT_OVERLAP = "appointments_patient_no_overlap"
_PRACTITIONER_OVERLAP = "appointments_practitioner_no_overlap"
_KEY_CONSTRAINT = "appointments_idempotency_key_unique"
_KEY_INDEX = "ix_appointments_idempotency_key_standing"


def upgrade() -> None:
    # Existing rows are standing, which is exactly what they are - so the default does
    # the backfill and no separate UPDATE is needed.
    op.add_column(
        "appointments",
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="standing",
        ),
    )
    op.create_check_constraint(
        "appointments_status_valid",
        "appointments",
        "status IN ('standing', 'cancelled')",
    )

    # The booking key lives as long as the appointment *stands*, not as long as its
    # record exists. PostgreSQL has no partial UNIQUE constraint, so the constraint
    # becomes a partial unique index - cancelling removes the row from it, freeing the
    # key in the same statement that cancels the appointment.
    #
    # Swapped BEFORE the exclusion constraints below, and that order is load-bearing.
    # PostgreSQL checks a row against its indexes in creation order and reports only the
    # first violation, so the key index has to keep the position its constraint held in
    # the original table. Recreated after the overlap rules, it would sit behind them,
    # and two identical concurrent booking attempts - which collide on the key AND on
    # both overlap rules - would be answered "you are already busy then" instead of
    # replaying the appointment the winner just created.
    op.drop_constraint(_KEY_CONSTRAINT, "appointments", type_="unique")
    op.execute(
        f"CREATE UNIQUE INDEX {_KEY_INDEX} ON appointments (idempotency_key) "
        f"WHERE ({_IS_STANDING})"
    )

    # Both overlap rules become partial. This is what makes a cancelled appointment stop
    # occupying its slot at the datastore rather than by an application filter something
    # could forget - and it is why a cancelled slot is bookable again immediately.
    # PostgreSQL cannot add a WHERE to an existing constraint, so each is dropped and
    # recreated.
    op.drop_constraint(_PATIENT_OVERLAP, "appointments", type_="unique")
    op.execute(
        f"ALTER TABLE appointments ADD CONSTRAINT {_PATIENT_OVERLAP} "
        "EXCLUDE USING gist (patient_id WITH =, tsrange(starts_at, ends_at) WITH &&) "
        f"WHERE ({_IS_STANDING})"
    )
    op.drop_constraint(_PRACTITIONER_OVERLAP, "appointments", type_="unique")
    op.execute(
        f"ALTER TABLE appointments ADD CONSTRAINT {_PRACTITIONER_OVERLAP} "
        "EXCLUDE USING gist (practitioner_id WITH =, "
        "tsrange(starts_at, ends_at) WITH &&) "
        f"WHERE ({_IS_STANDING})"
    )

    # Both legs of the two-axis listing filter on patient and status and order by start.
    op.create_index(
        "ix_appointments_patient_status_starts",
        "appointments",
        ["patient_id", "status", "starts_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_appointments_patient_status_starts", table_name="appointments")

    # Restoring the unconditional key constraint can fail where a cancelled row and a
    # standing one share a key - which the partial index above deliberately allows.
    # That is a real conflict, not a migration defect: the previous release cannot
    # represent two appointments holding one key, so it must be resolved before rolling
    # back rather than silently discarded here.
    op.drop_index(_KEY_INDEX, table_name="appointments")
    op.create_unique_constraint(_KEY_CONSTRAINT, "appointments", ["idempotency_key"])

    # Both of these are *more* likely to fail than the key constraint above, for the
    # same kind of reason and by way of the feature's headline flow: cancelling an
    # appointment and rebooking the freed slot leaves a cancelled row and a standing row
    # sharing one `tsrange` for the same patient and practitioner, which an
    # unconditional EXCLUDE rejects. That is a real conflict rather than a migration
    # defect - the previous release cannot represent a cancelled appointment at all - so
    # it must be resolved before rolling back rather than silently discarded here.
    op.drop_constraint(_PRACTITIONER_OVERLAP, "appointments", type_="unique")
    op.execute(
        f"ALTER TABLE appointments ADD CONSTRAINT {_PRACTITIONER_OVERLAP} "
        "EXCLUDE USING gist (practitioner_id WITH =, "
        "tsrange(starts_at, ends_at) WITH &&)"
    )
    op.drop_constraint(_PATIENT_OVERLAP, "appointments", type_="unique")
    op.execute(
        f"ALTER TABLE appointments ADD CONSTRAINT {_PATIENT_OVERLAP} "
        "EXCLUDE USING gist (patient_id WITH =, tsrange(starts_at, ends_at) WITH &&)"
    )

    op.drop_constraint("appointments_status_valid", "appointments", type_="check")
    op.drop_column("appointments", "status")
