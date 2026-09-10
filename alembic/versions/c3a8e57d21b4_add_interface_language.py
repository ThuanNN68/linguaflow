"""add users.interface_language

The language the interface is drawn in, separate from the one messages are
translated into (docs/api/contract.md §1.2).

Added in three steps rather than one, because the column is `NOT NULL` and the
table already has rows: create it nullable, fill every row from that account's
own `preferred_language`, then tighten it. Filling from `preferred_language`
rather than from the `en` default is what keeps existing accounts looking
exactly as they did the day before the upgrade.

SQLite cannot ALTER a column, so the tightening step runs inside a batch
operation, which rebuilds the table. That is safe here and nothing else
references `users.interface_language`.

Revision ID: c3a8e57d21b4
Revises: b7f2c91d4a08
Create Date: 2026-08-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3a8e57d21b4'
down_revision: Union[str, Sequence[str], None] = 'b7f2c91d4a08'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'users',
        sa.Column('interface_language', sa.String(length=10), nullable=True),
    )
    op.execute(
        "UPDATE users SET interface_language = preferred_language "
        "WHERE interface_language IS NULL"
    )
    with op.batch_alter_table('users') as batch:
        batch.alter_column(
            'interface_language',
            existing_type=sa.String(length=10),
            nullable=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('users') as batch:
        batch.drop_column('interface_language')
