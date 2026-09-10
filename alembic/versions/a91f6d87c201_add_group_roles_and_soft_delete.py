"""add group roles and soft delete

Revision ID: a91f6d87c201
Revises: a1b2c3d4e5f6
"""
from alembic import op
import sqlalchemy as sa

revision = "a91f6d87c201"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversation_members", sa.Column("role", sa.String(10), nullable=False, server_default="member"))
    op.create_check_constraint("ck_conversation_members_role", "conversation_members", "role IN ('owner', 'admin', 'member')")
    op.execute("UPDATE conversation_members cm SET role = 'owner' FROM conversations c WHERE cm.conversation_id = c.id AND cm.user_id = c.created_by")
    op.add_column("conversations", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "deleted_at")
    op.drop_constraint("ck_conversation_members_role", "conversation_members", type_="check")
    op.drop_column("conversation_members", "role")
