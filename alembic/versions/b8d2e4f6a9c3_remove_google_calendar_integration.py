"""remove Google Calendar synchronization and Google Meet fields.

Revision ID: b8d2e4f6a9c3
Revises: a4f7c9e2b1d0
Create Date: 2026-09-07
"""

from collections.abc import Sequence

from alembic import op


revision: str = "b8d2e4f6a9c3"
down_revision: str | Sequence[str] | None = "a4f7c9e2b1d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Remove stored Google credentials and remote-calendar metadata."""
    op.drop_table("calendar_links")
    op.drop_index("ix_calendar_events_google_event_id", table_name="calendar_events")
    op.drop_constraint("ck_calendar_events_sync_state", "calendar_events", type_="check")
    for column in (
        "google_meet_request_id",
        "google_meet_url",
        "create_google_meet",
        "google_etag",
        "google_calendar_id",
        "google_event_id",
        "sync_state",
    ):
        op.drop_column("calendar_events", column)


def downgrade() -> None:
    raise NotImplementedError("Google Calendar synchronization was intentionally removed")
