"""Add assistant_attempts, the Assistant Agent's measurement log (ADR-16, ADR-40).

Revision ID: c4a8e02f7d61
Revises: b7d3f91a4c62
Create Date: 2026-08-28

"""

import sqlalchemy as sa

from alembic import op

revision = "c4a8e02f7d61"
down_revision = "b7d3f91a4c62"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=True),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("source_message_id", sa.String(length=36), nullable=True),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=30), server_default="", nullable=False),
        sa.Column(
            "model_configured", sa.String(length=100), server_default="", nullable=False
        ),
        sa.Column("replans", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tool_calls", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tools_failed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tools_used", sa.Text(), server_default="[]", nullable=False),
        sa.Column("proposals_created", sa.Integer(), server_default="0", nullable=False),
        sa.Column("proposals_executed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("memory_lines", sa.Integer(), server_default="0", nullable=False),
        sa.Column("memory_recalled", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_code", sa.String(length=80), server_default="", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # SET NULL throughout: deleting a conversation, an account or a message
        # must not delete the evidence that the assistant was asked something.
        # The same choice `translation_attempts.translation_id` makes.
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["source_message_id"], ["messages.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "outcome IN ('answered', 'proposed', 'executed', 'clarified', "
            "'refused', 'empty', 'error')",
            name="ck_assistant_attempts_outcome",
        ),
    )
    op.create_index(
        "ix_assistant_attempts_created", "assistant_attempts", ["created_at"]
    )
    op.create_index(
        "ix_assistant_attempts_conversation",
        "assistant_attempts",
        ["conversation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_assistant_attempts_conversation", table_name="assistant_attempts")
    op.drop_index("ix_assistant_attempts_created", table_name="assistant_attempts")
    op.drop_table("assistant_attempts")
