"""order a thread by the order of its writes, not by a clock

Revision ID: b7e2a9c41d05
Revises: 575865d33df1
Create Date: 2026-09-22 17:00:00.000000

Adds `messages.seq`, an identity column the database assigns at insert, and the
`(chat_id, seq)` index a thread is read through. Until now a thread was ordered by
`created_at` - the writing transaction's start time - and "did staff post after this
message" compared two of them. Both are only as right as the clock: a clock stepped
backwards between two writes stamps the later row earlier, and the thread comes back
reply-before-question. WSL2 steps its clock, and the Postgres container shares that
kernel, so this was observed, not supposed.

Existing rows are numbered in the order they read today, `created_at` then `id`, and the
identity continues after the highest number, so every row already stored keeps its
place and every later write lands after all of them.

The downgrade drops the index and the column; ordering falls back to `created_at`.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e2a9c41d05"
down_revision: str | Sequence[str] | None = "575865d33df1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add `seq`, number the stored rows in their current order, make it identity."""
    op.add_column("messages", sa.Column("seq", sa.BigInteger(), nullable=True))
    op.execute(
        """
        UPDATE messages AS m
        SET seq = numbered.n
        FROM (
            SELECT id, row_number() OVER (ORDER BY created_at, id) AS n
            FROM messages
        ) AS numbered
        WHERE m.id = numbered.id
        """
    )
    op.alter_column("messages", "seq", nullable=False)
    op.execute("ALTER TABLE messages ALTER COLUMN seq ADD GENERATED ALWAYS AS IDENTITY")
    op.execute(
        "SELECT setval(pg_get_serial_sequence('messages', 'seq'), "
        "COALESCE((SELECT max(seq) FROM messages), 0) + 1, false)"
    )
    op.create_index("ix_messages_chat_seq", "messages", ["chat_id", "seq"])


def downgrade() -> None:
    """Drop the index and the column; ordering falls back to `created_at`."""
    op.drop_index("ix_messages_chat_seq", table_name="messages")
    op.drop_column("messages", "seq")
