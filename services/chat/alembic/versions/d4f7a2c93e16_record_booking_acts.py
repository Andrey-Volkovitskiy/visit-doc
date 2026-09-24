"""record what the assistant did to the schedule

Revision ID: d4f7a2c93e16
Revises: b7e2a9c41d05
Create Date: 2026-09-24 12:00:00.000000

Adds `booking_acts`: one row per attempt the assistant made to book, reschedule or
cancel an appointment, hung off the patient message the turn was answering. A row is
inserted before the request leaves for the scheduler, with no outcome, and settled at
most once afterwards - so a missing row means nothing was sent, and a row with no
outcome means the answer was never recorded.

`seq` is an identity column, ordering a message's acts by the order they were written
rather than by a clock, for the reason `messages.seq` was added. Both foreign keys
cascade, so an act goes with its chat or its message and never outlives either.

Nothing is backfilled: no act was recorded before this revision, and none can be
reconstructed from the thread.

The downgrade drops the table, and with it every recorded act.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4f7a2c93e16"
down_revision: str | Sequence[str] | None = "b7e2a9c41d05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create `booking_acts`, its five checks and its two indexes."""
    op.create_table(
        "booking_acts",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("session_id", sa.String(26), nullable=False),
        sa.Column(
            "chat_id",
            sa.String(26),
            sa.ForeignKey("chats.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            sa.String(26),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=True),
        sa.Column("refusal_reason", sa.String(48), nullable=True),
        sa.Column("appointment_id", sa.String(26), nullable=True),
        sa.Column("practitioner_id", sa.String(26), nullable=False),
        sa.Column("practitioner_full_name", sa.String(200), nullable=True),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("ends_at", sa.DateTime(), nullable=True),
        sa.Column("previous_practitioner_id", sa.String(26), nullable=True),
        sa.Column("previous_practitioner_full_name", sa.String(200), nullable=True),
        sa.Column("previous_starts_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "operation IN ('book', 'reschedule', 'cancel')",
            name="ck_booking_acts_operation",
        ),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN "
            "('done', 'unchanged', 'refused', 'not_sent', 'unknown')",
            name="ck_booking_acts_outcome",
        ),
        sa.CheckConstraint(
            "COALESCE(outcome = 'refused', false) = (refusal_reason IS NOT NULL)",
            name="ck_booking_acts_refusal_reason_with_refused",
        ),
        sa.CheckConstraint(
            "(outcome IS NULL) = (settled_at IS NULL)",
            name="ck_booking_acts_settled_with_outcome",
        ),
        sa.CheckConstraint(
            "(operation = 'reschedule') = "
            "(previous_starts_at IS NOT NULL AND previous_practitioner_id IS NOT NULL)",
            name="ck_booking_acts_previous_with_reschedule",
        ),
    )
    op.create_index("ix_booking_acts_chat_seq", "booking_acts", ["chat_id", "seq"])
    op.create_index("ix_booking_acts_message", "booking_acts", ["message_id"])


def downgrade() -> None:
    """Drop the table and every act recorded in it."""
    op.drop_index("ix_booking_acts_message", table_name="booking_acts")
    op.drop_index("ix_booking_acts_chat_seq", table_name="booking_acts")
    op.drop_table("booking_acts")
