"""add voice message lifecycle fields

Revision ID: a3f1c7e9b2d4
Revises: 9c4d2e7f1a6b
Create Date: 2026-08-26

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a3f1c7e9b2d4"
down_revision: str | Sequence[str] | None = "9c4d2e7f1a6b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MESSAGE_TYPE_CHECK = "message_type IN ('text', 'voice')"
_TRANSCRIPTION_STATUS_CHECK = (
    "transcription_status IS NULL OR transcription_status IN ('pending', 'completed', 'failed')"
)
_MESSAGE_LIFECYCLE_CHECK = (
    "(message_type = 'text' AND transcription_status IS NULL) OR "
    "(message_type = 'voice' AND transcription_status IS NOT NULL AND ("
    "(transcription_status IN ('pending', 'failed') AND original_text = '') OR "
    "(transcription_status = 'completed' AND length(trim(original_text)) > 0)"
    "))"
)


def upgrade() -> None:
    """Add a backward-compatible lifecycle for text and voice messages."""
    op.add_column(
        "messages",
        sa.Column(
            "message_type",
            sa.String(length=10),
            nullable=False,
            server_default="text",
        ),
    )
    op.add_column(
        "messages",
        sa.Column("transcription_status", sa.String(length=20), nullable=True),
    )
    op.create_check_constraint(
        "ck_messages_message_type",
        "messages",
        _MESSAGE_TYPE_CHECK,
    )
    op.create_check_constraint(
        "ck_messages_transcription_status",
        "messages",
        _TRANSCRIPTION_STATUS_CHECK,
    )
    op.create_check_constraint(
        "ck_messages_voice_lifecycle",
        "messages",
        _MESSAGE_LIFECYCLE_CHECK,
    )


def downgrade() -> None:
    """Remove only the voice lifecycle introduced by this revision."""
    op.drop_constraint("ck_messages_voice_lifecycle", "messages", type_="check")
    op.drop_constraint("ck_messages_transcription_status", "messages", type_="check")
    op.drop_constraint("ck_messages_message_type", "messages", type_="check")
    op.drop_column("messages", "transcription_status")
    op.drop_column("messages", "message_type")
