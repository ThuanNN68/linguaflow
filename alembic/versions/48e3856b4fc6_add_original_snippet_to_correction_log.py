"""add original snippet to correction log

Revision ID: 48e3856b4fc6
Revises: 8c3d1e4f5a6b
Create Date: 2026-08-24 14:45:10.887454

`pending_registrations.attempts`/`request_count` server-default drift that
autogenerate also reported is pre-existing (those columns use a Python-side
`default=`, not `server_default=`) and unrelated to this change — left out
rather than folded into a migration named for `correction_log`.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '48e3856b4fc6'
down_revision: Union[str, Sequence[str], None] = '8c3d1e4f5a6b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Nullable first, backfilled, then tightened — `correction_log` already has
    rows in any deployment past its first migration, and `NOT NULL` with no
    default would fail against them (same shape as `c3a8e57d21b4`'s
    `interface_language`).
    """
    op.add_column(
        'correction_log',
        sa.Column('original_snippet', sa.Text(), nullable=True),
    )
    op.execute(
        "UPDATE correction_log SET original_snippet = '' "
        "WHERE original_snippet IS NULL"
    )
    with op.batch_alter_table('correction_log') as batch:
        batch.alter_column(
            'original_snippet',
            existing_type=sa.Text(),
            nullable=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('correction_log', schema=None) as batch_op:
        batch_op.drop_column('original_snippet')
