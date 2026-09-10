"""add google subject to users

Revision ID: e51a72f4b903
Revises: d4e9a30b7c12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e51a72f4b903"
down_revision: Union[str, Sequence[str], None] = "d4e9a30b7c12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("google_subject", sa.String(length=255), nullable=True))
    op.create_index("ix_users_google_subject", "users", ["google_subject"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_google_subject", table_name="users")
    op.drop_column("users", "google_subject")
