"""remove the legacy users.google_subject column

The application standardised on ``google_sub`` for Google's stable subject
identifier. Preserve any value that only exists in the legacy column before
removing the duplicate schema and its unique index.

Revision ID: f8c2d4a6b9e1
Revises: e7b3a1c9d5f2
Create Date: 2026-09-10 21:35:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f8c2d4a6b9e1"
down_revision: str | Sequence[str] | None = "e7b3a1c9d5f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Move legacy bindings to google_sub, then remove the duplicate column."""
    op.execute(
        """
        UPDATE users
        SET google_sub = google_subject
        WHERE google_sub IS NULL AND google_subject IS NOT NULL
        """
    )
    op.drop_index("ix_users_google_subject", table_name="users")
    op.drop_column("users", "google_subject")


def downgrade() -> None:
    """Restore the legacy column from the canonical Google subject value."""
    op.add_column("users", sa.Column("google_subject", sa.String(length=255), nullable=True))
    op.execute("UPDATE users SET google_subject = google_sub WHERE google_sub IS NOT NULL")
    op.create_index("ix_users_google_subject", "users", ["google_subject"], unique=True)
