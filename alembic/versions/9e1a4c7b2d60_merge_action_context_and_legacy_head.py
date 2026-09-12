"""merge action-context and legacy migration heads

Revision ID: 9e1a4c7b2d60
Revises: 7c9d2e4f6a18, f8c2d4a6b9e1
Create Date: 2026-09-12 11:35:00
"""

from collections.abc import Sequence

revision: str = "9e1a4c7b2d60"
down_revision: str | Sequence[str] | None = ("7c9d2e4f6a18", "f8c2d4a6b9e1")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Join independent histories; schema changes are in their parent revisions."""


def downgrade() -> None:
    """Split histories again without altering schema."""
