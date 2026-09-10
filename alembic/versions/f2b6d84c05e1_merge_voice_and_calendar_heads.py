"""merge voice message and assistant calendar heads

Two features branched from `9c4d2e7f1a6b` at the same time: voice messages on
develop_v2, and the assistant's calendar and reminders here. Neither touches a
table the other defines — voice adds columns to `messages` and `attachments`,
calendar adds `calendar_events`, `reminders` and `calendar_links` — so this
rejoins the histories without changing anything.

Merge revisions are the established way this repo reconciles a fork; see
`a1b2c3d4e5f6`, `6f6eade07a08` and `9c4d2e7f1a6b`, all of which do the same with
a tuple `down_revision`.

Revision ID: f2b6d84c05e1
Revises: a3f1c7e9b2d4, e5c8a71b39d2
Create Date: 2026-08-27

"""

from collections.abc import Sequence

revision: str = "f2b6d84c05e1"
down_revision: str | Sequence[str] | None = ("a3f1c7e9b2d4", "e5c8a71b39d2")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Join the two schema histories without changing tables."""


def downgrade() -> None:
    """Split the schema histories without changing tables."""
