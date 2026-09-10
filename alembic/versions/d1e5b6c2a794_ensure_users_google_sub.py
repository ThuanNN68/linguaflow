"""ensure users.google_sub exists after merged migration histories

Revision ID: d1e5b6c2a794
Revises: c8d4a12f9b73
"""

import sqlalchemy as sa

from alembic import op

revision = "d1e5b6c2a794"
down_revision = "c8d4a12f9b73"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the Google identity column when a legacy branch omitted it."""
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("users")}
    if "google_sub" not in columns:
        op.add_column("users", sa.Column("google_sub", sa.String(length=255), nullable=True))
    indexes = {index["name"] for index in inspector.get_indexes("users")}
    if "ix_users_google_sub" not in indexes:
        op.create_index("ix_users_google_sub", "users", ["google_sub"], unique=True)


def downgrade() -> None:
    """Keep identity data intact when rolling back the compatibility revision."""
