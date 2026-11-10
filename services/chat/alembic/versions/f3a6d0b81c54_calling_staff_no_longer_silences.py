"""calling staff no longer silences the assistant: drop the escalation columns

Revision ID: f3a6d0b81c54
Revises: e8c3f15a2d97
Create Date: 2026-11-10 12:00:00.000000

`chats.escalated_at` and `chats.escalation_reason` held the one silence that had no
deadline: the assistant stopped replying when it called staff for an urgent condition,
distress, a request for a person or a booking for someone else, until a staff member
answered. Calling staff now never silences - the conversation keeps its place in the
queue through `attention_since` and the message keeps its mark - so the two columns
and the check pairing them are dropped. Only the pause still silences.

The downgrade restores the columns empty: which conversations were silenced is not
recoverable, and an empty column is what a conversation nobody escalated holds.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a6d0b81c54"
down_revision: str | Sequence[str] | None = "e8c3f15a2d97"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop the escalation check and the two columns it paired."""
    op.drop_constraint(
        "ck_chats_escalation_reason_with_escalated_at", "chats", type_="check"
    )
    op.drop_column("chats", "escalation_reason")
    op.drop_column("chats", "escalated_at")


def downgrade() -> None:
    """Restore the two columns, empty, and the check pairing them."""
    op.add_column(
        "chats", sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("chats", sa.Column("escalation_reason", sa.String(32), nullable=True))
    op.create_check_constraint(
        "ck_chats_escalation_reason_with_escalated_at",
        "chats",
        "(escalated_at IS NULL) = (escalation_reason IS NULL)",
    )
