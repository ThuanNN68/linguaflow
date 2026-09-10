"""add dismissed_at to action_proposals

Lets somebody clear a finished proposal out of their task inbox without
touching what it produced. `dismissed_at` hides the row from that list and
nothing else: an approved proposal has already created a calendar event, and
that event and its reminders go on exactly as before. Deleting the row would
take the calendar entry with it through `calendar_events.action_proposal_id`,
which is the opposite of what tidying a finished list should do.

Hand-trimmed to this one column. Autogenerate also proposed dropping
`users.google_subject`, two GIN indexes on `assistant_chunks`, the
`ix_messages_conversation_visible_created_at` index, and two check
constraints. That is pre-existing drift between the models and this database,
unrelated to this change and destructive if applied here; it belongs in its own
migration written deliberately, not as a side effect of adding a column.

Revision ID: 68a296abc040
Revises: 23422cc34ecb
Create Date: 2026-08-29 15:51:34.765100

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '68a296abc040'
down_revision: Union[str, Sequence[str], None] = '23422cc34ecb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the nullable dismissal timestamp."""
    op.add_column(
        "action_proposals",
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Drop it. Nothing else read it, so no data is stranded."""
    op.drop_column("action_proposals", "dismissed_at")
