"""merge google identity and message mentions heads

Revision ID: c8d4a12f9b73
Revises: f61a09c2d7b4, 8c3d1e4f5a6b
"""

from collections.abc import Sequence

revision: str = "c8d4a12f9b73"
down_revision: str | Sequence[str] | None = ("f61a09c2d7b4", "8c3d1e4f5a6b")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join independent migration histories without modifying data."""


def downgrade() -> None:
    """Split the histories again; schema changes live in their own revisions."""
