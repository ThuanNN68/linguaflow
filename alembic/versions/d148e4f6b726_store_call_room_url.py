"""store the canonical RTC room URL for each call

Revision ID: d148e4f6b726
Revises: c817d3a9f214
"""

from alembic import op
import sqlalchemy as sa


revision = "d148e4f6b726"
down_revision = "c817d3a9f214"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing local rows predate the callable API and therefore have no
    # provider room URL. They are terminal/non-joinable, so an empty value is
    # an accurate, migration-safe backfill.
    op.add_column(
        "call_sessions",
        sa.Column("provider_room_url", sa.String(length=500), nullable=False, server_default=""),
    )
    op.alter_column("call_sessions", "provider_room_url", server_default=None)


def downgrade() -> None:
    op.drop_column("call_sessions", "provider_room_url")
