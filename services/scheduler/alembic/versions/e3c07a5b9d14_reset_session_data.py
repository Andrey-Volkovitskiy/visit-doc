"""delete every session's scheduling data, so 007 starts from an empty system

Revision ID: e3c07a5b9d14
Revises: b4c7e19d2a58
Create Date: 2026-09-01

DESTRUCTIVE, and data only - **the schema does not change**. This revision exists so
the reset that 007 requires is ordered and recorded with the deploy rather than
remembered, and so that the chat service's own destructive revision is not the only
half of a reset that has to span two databases.

Practitioners and patients are deleted; their appointments follow by the foreign-key
cascades 005 created, which 006 deliberately left status-blind - so cancelled
appointments go with them, which is what "everything that session owns" means.

`downgrade()` cannot restore any of it.

"""

from collections.abc import Sequence

from alembic import op

revision: str = "e3c07a5b9d14"
down_revision: str | None = "b4c7e19d2a58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Order matters only for legibility: an appointment cannot outlive either of these
    # rows, so whichever goes first takes its appointments with it.
    op.execute("DELETE FROM practitioners")
    op.execute("DELETE FROM patients")


def downgrade() -> None:
    # Nothing to undo, and nothing that could be. The rows this revision removed are
    # gone; a downgrade returns the schema to where it already is.
    pass
