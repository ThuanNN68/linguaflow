"""merge admin/assistant and develop_v2 migration heads

Revision ID: 9c4d2e7f1a6b
Revises: 6f6eade07a08, e2c7a5d1b908
Create Date: 2026-08-24

"""

from collections.abc import Sequence


revision: str = "9c4d2e7f1a6b"
down_revision: str | Sequence[str] | None = ("6f6eade07a08", "e2c7a5d1b908")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Join the two schema histories without changing tables."""


def downgrade() -> None:
    """Split the schema histories without changing tables."""
