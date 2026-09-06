"""replace messages.grounded with faq_verdict

Revision ID: 7c76ca5716e8
Revises: d2b91c6f4e08
Create Date: 2026-09-06 13:38:40.241530

Discards `grounded` rather than converting it. No value is translated, in either
direction, and both directions are therefore correct ONLY against an empty `messages`
table - which was verified in `visitdoc_chat` and `visitdoc_chat_test` when this was
written.

A surviving row would come out with a NULL verdict, and NULL in this column means "no
FAQ specialist ran" - a false statement about a turn that had one. That is why there is
no backfill: the only mapping available would be `false -> abstained_similarity_floor`,
a guess about which of three gates a pre-phase turn failed, in a change whose whole
point is that those gates are now told apart.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7c76ca5716e8"
down_revision: str | Sequence[str] | None = "d2b91c6f4e08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the verdict column and drop the boolean it replaces."""
    op.add_column("messages", sa.Column("faq_verdict", sa.String(32), nullable=True))
    op.drop_column("messages", "grounded")


def downgrade() -> None:
    """Restore the boolean and drop the verdict. Lossy, like the upgrade."""
    op.add_column("messages", sa.Column("grounded", sa.Boolean(), nullable=True))
    op.drop_column("messages", "faq_verdict")
