"""give FAQ entries an owning session and a live revision, on an emptied store

Revision ID: d2b91c6f4e08
Revises: a1f4c8e07b32
Create Date: 2026-09-01

DESTRUCTIVE. This revision deletes every session - and, by cascade, every chat and
message - along with every FAQ entry, before it adds the two columns below. That is not
a side effect: both columns are NOT NULL, and no row written before this feature can
satisfy either of them.

The deletion belongs inside the migration rather than in a runbook step beside it,
because this is the only place that can guarantee the table is empty at the instant
NOT NULL is applied. A runbook step that gets skipped fails the ALTER at deploy time,
which is a loud failure but a needless one.

`downgrade()` restores the schema and CANNOT restore the data.

Two stores are reset outside this file and cannot be reached from it: the scheduling
service's own database has its own data-only revision, and the retrieval store's
collection is dropped by hand before the service starts.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2b91c6f4e08"
down_revision: str | None = "a1f4c8e07b32"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Sessions first: chats and their messages follow by the FK cascade already on
    # them, so this one statement clears three tables. Entries predating ownership
    # belong to no session and so would survive it - they are deleted explicitly.
    op.execute("DELETE FROM sessions")
    op.execute("DELETE FROM faq_entries")

    # The owning session. Its cascade is what makes "a session's corpus dies with the
    # session" a consequence of deleting the row rather than a step someone sequences.
    op.add_column("faq_entries", sa.Column("session_id", sa.String(26), nullable=False))
    op.create_foreign_key(
        "fk_faq_entries_session_id_sessions",
        "faq_entries",
        "sessions",
        ["session_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_faq_entries_session", "faq_entries", ["session_id"])

    # The one revision of this entry's indexed chunks that retrieval may search. Not a
    # duplicate of anything the index holds: the index carries several revisions of an
    # entry and cannot say which is current, so the row says.
    #
    # Together with the column above, and with a published revision always holding at
    # least one chunk, this is what makes "listed" and "searchable" the same fact. No
    # CHECK constraint is needed to hold the pair together - an earlier design made
    # both nullable and needed one; NOT NULL leaves nothing to disagree.
    op.add_column(
        "faq_entries", sa.Column("live_revision", sa.String(26), nullable=False)
    )


def downgrade() -> None:
    # Schema only. The sessions, chats, messages and entries this revision deleted are
    # gone, and nothing here brings them back.
    op.drop_column("faq_entries", "live_revision")
    op.drop_index("ix_faq_entries_session", table_name="faq_entries")
    op.drop_constraint(
        "fk_faq_entries_session_id_sessions", "faq_entries", type_="foreignkey"
    )
    op.drop_column("faq_entries", "session_id")
