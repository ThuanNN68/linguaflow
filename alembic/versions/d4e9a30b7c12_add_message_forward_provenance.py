"""add message forward provenance

Revision ID: d4e9a30b7c12
Revises: c3a8e57d21b4
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d4e9a30b7c12"
down_revision: Union[str, Sequence[str], None] = "c3a8e57d21b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("forwarded_from_message_id", sa.String(length=36), nullable=True))
    op.create_foreign_key("fk_messages_forwarded_from_message", "messages", "messages", ["forwarded_from_message_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint("fk_messages_forwarded_from_message", "messages", type_="foreignkey")
    op.drop_column("messages", "forwarded_from_message_id")
