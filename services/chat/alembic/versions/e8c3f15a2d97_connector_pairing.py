"""pair the Claude app with a session: codes, clients, grants and tokens

Revision ID: e8c3f15a2d97
Revises: d4f7a2c93e16
Create Date: 2026-09-28 12:00:00.000000

Adds the staff connector's six tables. A session's pairing code is keyed by the session,
so one usable code per session is the primary key rather than a rule. The OAuth tables
hold registered clients, sign-ins in progress, one-minute authorization codes, grants
and their tokens. Every secret is stored as a 64-character SHA-256 digest. Codes, grants
and (through their grant) tokens cascade from the session, so deleting a session ends
every pairing it owned; clients and sign-ins in progress belong to no session.

`booking_acts` gains `ix_booking_acts_session_settled` for the recent-changes window,
and a check that a done act names its appointment: the count is distinct appointments,
and a NULL id would drop out of it silently. No existing row violates it.

The downgrade drops the six tables, and with them every pairing, and removes the index
and the check.

"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8c3f15a2d97"
down_revision: str | Sequence[str] | None = "d4f7a2c93e16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _created_at() -> sa.Column[datetime]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )


def _session_fk() -> sa.Column[str]:
    return sa.Column(
        "session_id",
        sa.String(26),
        sa.ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )


def _client_fk() -> sa.Column[str]:
    return sa.Column(
        "client_id",
        sa.String(43),
        sa.ForeignKey("oauth_clients.client_id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    """Create the six connector tables, then index and constrain `booking_acts`."""
    op.create_table(
        "mcp_pairing_codes",
        sa.Column(
            "session_id",
            sa.String(26),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("code_hash", sa.CHAR(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
    )
    op.create_table(
        "oauth_clients",
        sa.Column("client_id", sa.String(43), primary_key=True),
        sa.Column("client_name", sa.String(100), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        _created_at(),
    )
    op.create_table(
        "oauth_authorization_requests",
        sa.Column("id_hash", sa.CHAR(64), primary_key=True),
        _client_fk(),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("code_challenge", sa.String(128), nullable=False),
        sa.Column("state", sa.Text(), nullable=True),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column(
            "failed_attempts",
            sa.SmallInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "failed_attempts BETWEEN 0 AND 5",
            name="ck_oauth_authorization_requests_failed_attempts",
        ),
    )
    op.create_table(
        "oauth_authorization_codes",
        sa.Column("code_hash", sa.CHAR(64), primary_key=True),
        _session_fk(),
        _client_fk(),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("code_challenge", sa.String(128), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_oauth_authorization_codes_session",
        "oauth_authorization_codes",
        ["session_id"],
    )
    op.create_table(
        "oauth_grants",
        sa.Column("id", sa.String(26), primary_key=True),
        _session_fk(),
        _client_fk(),
        sa.Column("client_name", sa.String(100), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        _created_at(),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_oauth_grants_session", "oauth_grants", ["session_id"])
    op.create_table(
        "oauth_tokens",
        sa.Column("token_hash", sa.CHAR(64), primary_key=True),
        sa.Column(
            "grant_id",
            sa.String(26),
            sa.ForeignKey("oauth_grants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.CheckConstraint("kind IN ('access', 'refresh')", name="ck_oauth_tokens_kind"),
        sa.CheckConstraint(
            "kind = 'refresh' OR used_at IS NULL",
            name="ck_oauth_tokens_used_only_refresh",
        ),
    )
    op.create_index("ix_oauth_tokens_grant", "oauth_tokens", ["grant_id"])

    op.create_index(
        "ix_booking_acts_session_settled",
        "booking_acts",
        ["session_id", "settled_at"],
    )
    op.create_check_constraint(
        "ck_booking_acts_done_with_appointment",
        "booking_acts",
        "outcome IS DISTINCT FROM 'done' OR appointment_id IS NOT NULL",
    )


def downgrade() -> None:
    """Drop the connector tables and every pairing in them; unconstrain `booking_acts`."""
    op.drop_constraint(
        "ck_booking_acts_done_with_appointment", "booking_acts", type_="check"
    )
    op.drop_index("ix_booking_acts_session_settled", table_name="booking_acts")
    op.drop_index("ix_oauth_tokens_grant", table_name="oauth_tokens")
    op.drop_table("oauth_tokens")
    op.drop_index("ix_oauth_grants_session", table_name="oauth_grants")
    op.drop_table("oauth_grants")
    op.drop_index(
        "ix_oauth_authorization_codes_session", table_name="oauth_authorization_codes"
    )
    op.drop_table("oauth_authorization_codes")
    op.drop_table("oauth_authorization_requests")
    op.drop_table("oauth_clients")
    op.drop_table("mcp_pairing_codes")
