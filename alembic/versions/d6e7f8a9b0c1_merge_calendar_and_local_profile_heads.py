"""Merge the calendar/agent history with local profile additions.

Revision ID: d6e7f8a9b0c1
Revises: c4d8e2f6a719, f2b6d84c05e1
Create Date: 2026-08-27
"""

from collections.abc import Sequence


revision: str = "d6e7f8a9b0c1"
down_revision: str | Sequence[str] | None = ("c4d8e2f6a719", "f2b6d84c05e1")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Join independent schema histories without altering tables."""


def downgrade() -> None:
    """Split the histories without altering tables."""
