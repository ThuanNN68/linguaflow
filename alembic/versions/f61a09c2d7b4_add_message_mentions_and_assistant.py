"""add durable message mentions and assistant flag

Revision ID: f61a09c2d7b4
Revises: e51a72f4b903, b43d9a0f5312
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f61a09c2d7b4"
down_revision: str | Sequence[str] | None = ("e51a72f4b903", "b43d9a0f5312")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("mentions_json", sa.Text(), nullable=False, server_default="[]"))
    op.add_column("messages", sa.Column("assistant_generated", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("messages", "assistant_generated")
    op.drop_column("messages", "mentions_json")
