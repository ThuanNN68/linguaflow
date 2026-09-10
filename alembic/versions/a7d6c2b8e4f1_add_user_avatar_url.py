"""add durable avatar data URL to users

Revision ID: a7d6c2b8e4f1
Revises: 9c4d2e7f1a6b
"""

from alembic import op
import sqlalchemy as sa


revision = "a7d6c2b8e4f1"
down_revision = "9c4d2e7f1a6b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("avatar_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "avatar_url")
