"""add calendar_links

One user's connection to their Google Calendar, with the tokens encrypted
(CONTRACT.md §5 note 23, ADR-35).

Revision ID: e5c8a71b39d2
Revises: d7e3b1c9a486
Create Date: 2026-08-27

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5c8a71b39d2"
down_revision: str | Sequence[str] | None = "d7e3b1c9a486"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the table. Keyed by user_id: one connection per person or none."""
    op.create_table(
        "calendar_links",
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "google_calendar_id", sa.String(length=255), nullable=False, server_default="primary"
        ),
        # Text rather than String: Fernet ciphertext is base64 and grows with the
        # plaintext, and a length cap here would truncate a token into silence.
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=False),
        sa.Column("access_token_encrypted", sa.Text()),
        sa.Column("token_expires_at", sa.DateTime(timezone=True)),
        sa.Column("sync_token", sa.Text()),
        sa.Column("sync_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("last_sync_error", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    """Drop the table.

    This discards refresh tokens, so every linked user has to grant access again
    afterwards. That is the right way round: the alternative is leaving live
    credentials behind in a schema that no longer knows what they are for.
    """
    op.drop_table("calendar_links")
