"""add escalation, pause and attention state to chats, and a mark to messages

Revision ID: a1f4c8e07b32
Revises: c93b1e07a248
Create Date: 2026-09-01

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1f4c8e07b32"
down_revision: str | None = "c93b1e07a248"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Two independent pairs of facts, deliberately not one.
    #
    # `escalated_at`/`escalation_reason` answer "may the assistant speak here"; they are
    # cleared by a staff message OR by the console's switch. `attention_since` answers
    # "has a person acted here"; it is cleared by a staff message and by nothing else.
    # The two disagree in both directions and that is the point: a failure emphasizes
    # without silencing, and returning the assistant with the switch ends the silence
    # without answering the patient. One column carrying both would make each of those
    # a special case (spec 007 FR-003d, FR-017b, FR-027d).
    #
    # Every column is nullable by nature rather than by omission: an ordinary open
    # conversation is one where all four are NULL, so this migration needs no default
    # and no backfill, and can run against a live table.
    op.add_column(
        "chats", sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("chats", sa.Column("escalation_reason", sa.String(32), nullable=True))
    # A stored deadline, never a running timer: it has to survive a reload, a second
    # tab, and a restart of this process, and two tabs have to count down together.
    op.add_column(
        "chats",
        sa.Column("assistant_paused_until", sa.DateTime(timezone=True), nullable=True),
    )
    # A timestamp rather than a boolean because it also orders the console's list: among
    # the conversations needing a person, the one waiting longest comes first.
    op.add_column(
        "chats", sa.Column("attention_since", sa.DateTime(timezone=True), nullable=True)
    )
    # The invariant lives here rather than in application code: an escalation always
    # carries exactly one reason, and a reason never outlives the escalation that set
    # it. Written as an equality of NULL-ness so neither half can be set alone.
    op.create_check_constraint(
        "ck_chats_escalation_reason_with_escalated_at",
        "chats",
        "(escalated_at IS NULL) = (escalation_reason IS NULL)",
    )
    # The console's listing filters by session and orders by attention, and it is polled
    # every couple of seconds per open tab - the one recurring read this feature adds.
    op.create_index(
        "ix_chats_session_attention", "chats", ["session_id", "attention_since"]
    )

    # Which of four kinds this message is asking for, or NULL for "no mark / cleared".
    # Only ever set on a patient message. Whether a kind ever clears is a property of
    # the kind, read from one constant in the domain layer - there is deliberately no
    # `cleared_at` here to disagree with it.
    op.add_column("messages", sa.Column("attention_mark", sa.String(32), nullable=True))
    # Partial: the statement that clears a chat's marks and the read that asks whether a
    # chat holds one both address only marked rows, and marked rows are a small minority
    # of a chat's messages.
    op.create_index(
        "ix_messages_chat_attention_mark",
        "messages",
        ["chat_id", "attention_mark"],
        postgresql_where=sa.text("attention_mark IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_messages_chat_attention_mark", table_name="messages")
    op.drop_column("messages", "attention_mark")
    op.drop_index("ix_chats_session_attention", table_name="chats")
    op.drop_constraint(
        "ck_chats_escalation_reason_with_escalated_at", "chats", type_="check"
    )
    op.drop_column("chats", "attention_since")
    op.drop_column("chats", "assistant_paused_until")
    op.drop_column("chats", "escalation_reason")
    op.drop_column("chats", "escalated_at")
