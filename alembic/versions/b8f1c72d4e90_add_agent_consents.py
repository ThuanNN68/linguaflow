"""add agent_consents

Permissions a user grants to the assistant agent (CONTRACT.md §3.15). A table
rather than more columns on `user_settings`, because a permission has to record
when it was given and taken back, and its policy version is per-scope.

Revision ID: b8f1c72d4e90
Revises: 9c4d2e7f1a6b
Create Date: 2026-08-27

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b8f1c72d4e90"
down_revision: str | Sequence[str] | None = "9c4d2e7f1a6b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Kept as a literal rather than imported from `src.database.models`: a migration
# describes the schema at one point in history, and importing the live tuple
# would make this file silently change meaning when a scope is added later.
_SCOPES = (
    "read_conversations",
    "proactive_scan",
    "store_memory",
    "calendar_read",
    "calendar_write",
)


def upgrade() -> None:
    """Create the consent table. No backfill: absence means not granted."""
    op.create_table(
        "agent_consents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("is_granted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("granted_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("policy_version", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "scope IN (" + ", ".join(f"'{scope}'" for scope in _SCOPES) + ")",
            name="ck_agent_consents_scope",
        ),
        sa.UniqueConstraint("user_id", "scope", name="uq_agent_consents_user_scope"),
    )
    op.create_index("ix_agent_consents_user_id", "agent_consents", ["user_id"])


def downgrade() -> None:
    """Drop the table. Every row is a permission, none of it is derived data."""
    op.drop_index("ix_agent_consents_user_id", table_name="agent_consents")
    op.drop_table("agent_consents")
