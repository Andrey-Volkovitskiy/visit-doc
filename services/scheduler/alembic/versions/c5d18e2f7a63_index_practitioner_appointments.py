"""index a practitioner's appointments by status and start

Revision ID: c5d18e2f7a63
Revises: e3c07a5b9d14
Create Date: 2026-09-24

"""

from collections.abc import Sequence

from alembic import op

revision: str = "c5d18e2f7a63"
down_revision: str | None = "e3c07a5b9d14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_appointments_practitioner_status_starts"


def upgrade() -> None:
    # The console's read of one practitioner's week filters on practitioner and status
    # and bounds and orders by start - the patient listing's index, keyed the other way.
    op.create_index(_INDEX, "appointments", ["practitioner_id", "status", "starts_at"])


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="appointments")
