"""add durable assistant work and retryable reminders

Revision ID: a4d8c2e7f901
Revises: 9e1a4c7b2d60
Create Date: 2026-09-12 23:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a4d8c2e7f901"
down_revision: str | Sequence[str] | None = "9e1a4c7b2d60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("client_timezone", sa.String(length=64), nullable=True))
    op.add_column("reminders", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("reminders", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("reminders", sa.Column("attempts", sa.Integer(), server_default="0", nullable=False))
    op.add_column("reminders", sa.Column("last_error", sa.String(length=500), nullable=True))
    op.create_index("ix_action_proposals_time_source_message_id", "action_proposals", ["time_source_message_id"])
    op.create_index("ix_action_proposals_details_source_message_id", "action_proposals", ["details_source_message_id"])
    op.create_table(
        "assistant_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("message_id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("requester_id", sa.String(length=36), nullable=False),
        sa.Column("request_text", sa.Text(), nullable=False),
        sa.Column("trusted_timezone", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name="ck_assistant_jobs_status",
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requester_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id", name="uq_assistant_jobs_message_id"),
    )
    op.create_index("ix_assistant_jobs_ready", "assistant_jobs", ["status", "available_at"])


def downgrade() -> None:
    op.drop_index("ix_assistant_jobs_ready", table_name="assistant_jobs")
    op.drop_table("assistant_jobs")
    op.drop_index("ix_action_proposals_details_source_message_id", table_name="action_proposals")
    op.drop_index("ix_action_proposals_time_source_message_id", table_name="action_proposals")
    op.drop_column("reminders", "last_error")
    op.drop_column("reminders", "attempts")
    op.drop_column("reminders", "next_attempt_at")
    op.drop_column("reminders", "claimed_at")
    op.drop_column("messages", "client_timezone")
