"""add patient_id and cached patient_name to chats

Revision ID: c93b1e07a248
Revises: 044df0236efe
Create Date: 2026-08-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c93b1e07a248"
down_revision: str | None = "044df0236efe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable by design, not by omission: a chat created while scheduling is
    # unavailable has no patient yet, and every pre-existing chat is in exactly that
    # state after this migration. No foreign key is possible - the patient lives in the
    # scheduler's own database, so this column holds an opaque id and nothing more.
    op.add_column("chats", sa.Column("patient_id", sa.String(26), nullable=True))
    op.create_index("ix_chats_patient_id", "chats", ["patient_id"])
    # A display name this service caches but never authors: the scheduler owns it, and
    # there is no RPC to list patients, so the chat list would otherwise have nothing
    # to render after a reload.
    op.add_column("chats", sa.Column("patient_name", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("chats", "patient_name")
    op.drop_index("ix_chats_patient_id", table_name="chats")
    op.drop_column("chats", "patient_id")
