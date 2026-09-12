"""add contextual field provenance to action proposals

Revision ID: 7c9d2e4f6a18
Revises: 68a296abc040
Create Date: 2026-09-12 11:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7c9d2e4f6a18"
down_revision: str | Sequence[str] | None = "68a296abc040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("action_proposals", sa.Column("time_source_message_id", sa.String(length=36), nullable=True))
    op.add_column("action_proposals", sa.Column("details_source_message_id", sa.String(length=36), nullable=True))
    op.create_foreign_key(
        "fk_action_proposals_time_source_message_id_messages",
        "action_proposals", "messages", ["time_source_message_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_action_proposals_details_source_message_id_messages",
        "action_proposals", "messages", ["details_source_message_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_action_proposals_details_source_message_id_messages", "action_proposals", type_="foreignkey")
    op.drop_constraint("fk_action_proposals_time_source_message_id_messages", "action_proposals", type_="foreignkey")
    op.drop_column("action_proposals", "details_source_message_id")
    op.drop_column("action_proposals", "time_source_message_id")
