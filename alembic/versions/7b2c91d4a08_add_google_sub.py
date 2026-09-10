"""add users.google_sub

Google Sign-In subject identifier for Batch G. Supports both explicit Settings
linking and full Google sign-in identity resolution.

Revision ID: 7b2c91d4a08
Revises: ef06ca79ef49
Create Date: 2026-08-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7b2c91d4a08'
down_revision: Union[str, Sequence[str], None] = 'ef06ca79ef49'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add nullable google_sub column with unique constraint and index."""
    op.add_column(
        'users',
        sa.Column('google_sub', sa.String(length=255), nullable=True),
    )
    # PostgreSQL: unique index with NULL exclusion handled via partial index
    # SQLite: unique index allows multiple NULLs natively
    op.create_index(
        'ix_users_google_sub',
        'users',
        ['google_sub'],
        unique=True,
    )


def downgrade() -> None:
    """Drop google_sub column and its index."""
    op.drop_index('ix_users_google_sub', table_name='users')
    op.drop_column('users', 'google_sub')
