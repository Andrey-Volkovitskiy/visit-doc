"""move the verdict and the citations onto the request

Revision ID: 575865d33df1
Revises: 7c76ca5716e8
Create Date: 2026-09-10 12:00:00.000000

Replaces `messages.faq_verdict` (String(32)) and `messages.citations` (JSONB) with a
single `messages.request_outcomes` (JSONB): one entry per request the message carried,
`{position, question, answer, verdict, citations}`, in ascending position order.

Nothing is translated, in either direction. A turn may now answer one request and
abstain on another, so the two columns being dropped have no turn-wide value left to
hold - and a backfill would have to invent the `question` text, which was never stored,
leaving a field that is sometimes absent for every future reader to branch on.

That is legal only because every session was deleted first: on 2026-09-10 the
maintenance sweep removed 48 sessions, 412 chats, 905 messages, 432 FAQ entries, 412
patients and 96 practitioners, and all three stores - `visitdoc_chat`,
`visitdoc_scheduler` and Qdrant - were verified empty before this ran. Verify the same
before running it anywhere else.

The downgrade restores the two columns as NULL and drops the new one. It reverses the
schema and restores no data.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "575865d33df1"
down_revision: str | Sequence[str] | None = "7c76ca5716e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the per-request column and drop the two turn-level ones it replaces."""
    op.add_column("messages", sa.Column("request_outcomes", JSONB, nullable=True))
    op.drop_column("messages", "faq_verdict")
    op.drop_column("messages", "citations")


def downgrade() -> None:
    """Restore the two columns and drop the new one. Lossy, like the upgrade."""
    op.add_column("messages", sa.Column("faq_verdict", sa.String(32), nullable=True))
    op.add_column("messages", sa.Column("citations", JSONB, nullable=True))
    op.drop_column("messages", "request_outcomes")
