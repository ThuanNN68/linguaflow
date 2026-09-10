"""add pending_registrations

Pending user registration table for email OTP verification (Batch F).

Revision ID: d4e9f60a12b3
Revises: c3a8e57d21b4
Create Date: 2026-08-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e9f60a12b3'
down_revision: Union[str, Sequence[str], None] = 'c3a8e57d21b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'pending_registrations',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('username', sa.String(length=50), nullable=False),
        sa.Column('display_name', sa.String(length=100), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('preferred_language', sa.String(length=10), nullable=False),
        sa.Column('interface_language', sa.String(length=10), nullable=False),
        sa.Column('otp_hash', sa.String(length=255), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_sent_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('rate_window_started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('request_count', sa.Integer(), nullable=False, server_default='1'),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_pending_registrations_email',
        'pending_registrations',
        ['email'],
        unique=True,
    )
    op.create_index(
        'ix_pending_registrations_username',
        'pending_registrations',
        ['username'],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_pending_registrations_username', table_name='pending_registrations')
    op.drop_index('ix_pending_registrations_email', table_name='pending_registrations')
    op.drop_table('pending_registrations')
