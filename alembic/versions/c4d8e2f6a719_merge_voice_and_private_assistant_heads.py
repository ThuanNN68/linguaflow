"""Merge voice-message and private-assistant migration heads.

Revision ID: c4d8e2f6a719
Revises: a3f1c7e9b2d4, b2f8d4c1a903
Create Date: 2026-08-27
"""

from collections.abc import Sequence


revision: str = "c4d8e2f6a719"
down_revision: str | Sequence[str] | None = ("a3f1c7e9b2d4", "b2f8d4c1a903")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Merge point; schema changes were applied by the parent revisions."""


def downgrade() -> None:
    """Merge point; schema changes are reverted by the parent revisions."""
