"""add group description

Revision ID: b43d9a0f5312
Revises: a91f6d87c201
"""
from alembic import op
import sqlalchemy as sa

revision = "b43d9a0f5312"
down_revision = "a91f6d87c201"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("description", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "description")
