"""store Google Meet requests and conference links on calendar events.

Revision ID: a4f7c9e2b1d0
Revises: 68a296abc040
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4f7c9e2b1d0"
down_revision: str | Sequence[str] | None = "68a296abc040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "calendar_events",
        sa.Column("create_google_meet", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("calendar_events", sa.Column("google_meet_url", sa.String(length=2048)))
    op.add_column("calendar_events", sa.Column("google_meet_request_id", sa.String(length=64)))


def downgrade() -> None:
    op.drop_column("calendar_events", "google_meet_request_id")
    op.drop_column("calendar_events", "google_meet_url")
    op.drop_column("calendar_events", "create_google_meet")
