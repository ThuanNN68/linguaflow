"""add calendar_events and reminders

What a confirmed proposal becomes, and the nudges owed against it. Before this,
`action_proposals.status = "confirmed"` was a terminal state with no consumer
(CONTRACT.md §5 notes 21-22, ADR-33).

Revision ID: d7e3b1c9a486
Revises: c9a4d2e81f37
Create Date: 2026-08-27

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d7e3b1c9a486"
down_revision: str | Sequence[str] | None = "c9a4d2e81f37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Literals rather than imports from `src.database.models`: a migration
# describes the schema at one moment, and importing the live tuples would make
# this file quietly change meaning when a value is added later.
_SOURCES = ("assistant", "manual", "google")
_STATUSES = ("active", "cancelled")
_SYNC_STATES = ("local_only", "pending_push", "synced", "remote_only")


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    """Create both tables. Nothing to backfill: neither existed before."""
    op.create_table(
        "calendar_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # SET NULL: where an entry came from should outlive the proposal row.
        sa.Column(
            "action_proposal_id",
            sa.String(length=36),
            sa.ForeignKey("action_proposals.id", ondelete="SET NULL"),
        ),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("details", sa.Text()),
        sa.Column("location", sa.Text()),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column("all_day", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("timezone", sa.String(length=64)),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("google_event_id", sa.String(length=255)),
        sa.Column("google_calendar_id", sa.String(length=255)),
        sa.Column("google_etag", sa.String(length=255)),
        sa.Column(
            "sync_state", sa.String(length=16), nullable=False, server_default="local_only"
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(_in_clause("source", _SOURCES), name="ck_calendar_events_source"),
        sa.CheckConstraint(_in_clause("status", _STATUSES), name="ck_calendar_events_status"),
        sa.CheckConstraint(
            _in_clause("sync_state", _SYNC_STATES), name="ck_calendar_events_sync_state"
        ),
        sa.CheckConstraint(
            "ends_at IS NULL OR ends_at >= starts_at", name="ck_calendar_events_ends_after_starts"
        ),
    )
    op.create_index(
        "ix_calendar_events_user_starts_at", "calendar_events", ["user_id", "starts_at"]
    )
    op.create_index("ix_calendar_events_google_event_id", "calendar_events", ["google_event_id"])

    op.create_table(
        "reminders",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "calendar_event_id",
            sa.String(length=36),
            sa.ForeignKey("calendar_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("remind_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("dismissed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    # The scheduler's claim is `WHERE remind_at <= now() AND delivered_at IS
    # NULL`, so the index is those two columns in that order.
    op.create_index("ix_reminders_due", "reminders", ["remind_at", "delivered_at"])
    op.create_index("ix_reminders_user_id", "reminders", ["user_id"])


def downgrade() -> None:
    """Drop both, reminders first — it points at calendar_events."""
    op.drop_index("ix_reminders_user_id", table_name="reminders")
    op.drop_index("ix_reminders_due", table_name="reminders")
    op.drop_table("reminders")
    op.drop_index("ix_calendar_events_google_event_id", table_name="calendar_events")
    op.drop_index("ix_calendar_events_user_starts_at", table_name="calendar_events")
    op.drop_table("calendar_events")
