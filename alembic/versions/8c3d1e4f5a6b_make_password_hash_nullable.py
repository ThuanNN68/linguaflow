"""make users.password_hash nullable

Support Google-native users who do not have a password (Batch G).

Revision ID: 8c3d1e4f5a6b
Revises: 7b2c91d4a08
Create Date: 2026-08-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8c3d1e4f5a6b'
down_revision: Union[str, Sequence[str], None] = '7b2c91d4a08'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Make users.password_hash nullable for Google-backed users only."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column(
            'password_hash',
            existing_type=sa.String(length=255),
            nullable=True,
        )
        batch_op.create_check_constraint(
            'ck_users_has_auth_provider',
            'password_hash IS NOT NULL OR google_sub IS NOT NULL',
        )


def downgrade() -> None:
    """Make users.password_hash non-nullable.

    Note: Any Google-native users with password_hash=NULL must have a password
    set before downgrading to NOT NULL.
    """
    bind = op.get_bind()
    passwordless_users = bind.execute(
        sa.text('SELECT COUNT(*) FROM users WHERE password_hash IS NULL')
    ).scalar_one()
    if passwordless_users:
        raise RuntimeError(
            'Cannot downgrade while Google-native users have password_hash=NULL. '
            'Set passwords or remove those accounts first.'
        )

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint('ck_users_has_auth_provider', type_='check')
        batch_op.alter_column(
            'password_hash',
            existing_type=sa.String(length=255),
            nullable=False,
        )
