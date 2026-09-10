"""add action_proposals table

Supports AI action candidate extraction, HITL confirmation, and commitment detection (B-04/B-05/B-10).

Revision ID: a1b2c3d4e5f6
Revises: 8c3d1e4f5a6b, e51a72f4b903
Create Date: 2026-08-21
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = ('8c3d1e4f5a6b', 'e51a72f4b903')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'action_proposals',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('conversation_id', sa.String(length=36), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('source_message_id', sa.String(length=36), sa.ForeignKey('messages.id', ondelete='CASCADE'), nullable=False),
        sa.Column('owner_user_id', sa.String(length=36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_by_user_id', sa.String(length=36), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('source_mode', sa.String(length=32), nullable=False, server_default='on_demand'),
        sa.Column('action_type', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='pending_confirmation'),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('details', sa.Text(), nullable=True),
        sa.Column('location', sa.Text(), nullable=True),
        sa.Column('raw_time_expression', sa.Text(), nullable=True),
        sa.Column('scheduled_start_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('scheduled_end_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolved_timezone', sa.String(length=64), nullable=True),
        sa.Column('scheduled_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('confidence_score', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('clarification_prompt', sa.Text(), nullable=True),
        sa.Column('clarification_question', sa.Text(), nullable=True),
        sa.Column('missing_fields', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('clarification_rounds', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('idempotency_key', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('confirmed_by_user_id', sa.String(length=36), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('rejected_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('stale_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("action_type IN ('task', 'appointment')", name='ck_action_proposals_action_type'),
        sa.CheckConstraint("source_mode IN ('on_demand', 'proactive')", name='ck_action_proposals_source_mode'),
        sa.CheckConstraint("status IN ('needs_clarification', 'pending_confirmation', 'confirmed', 'rejected', 'stale')", name='ck_action_proposals_status'),
        sa.CheckConstraint('confidence_score >= 0 AND confidence_score <= 1', name='ck_action_proposals_confidence'),
        sa.CheckConstraint('clarification_rounds >= 0', name='ck_action_proposals_clarification_rounds'),
        sa.UniqueConstraint('idempotency_key', name='uq_action_proposals_idempotency_key'),
    )
    op.create_index('ix_action_proposals_conversation_id', 'action_proposals', ['conversation_id'])
    op.create_index('ix_action_proposals_source_message_id', 'action_proposals', ['source_message_id'])
    op.create_index('ix_action_proposals_owner_status', 'action_proposals', ['owner_user_id', 'status'])
    op.create_index('ix_action_proposals_status', 'action_proposals', ['status'])
    op.create_index('ix_action_proposals_idempotency_key', 'action_proposals', ['idempotency_key'])


def downgrade() -> None:
    op.drop_index('ix_action_proposals_idempotency_key', table_name='action_proposals')
    op.drop_index('ix_action_proposals_status', table_name='action_proposals')
    op.drop_index('ix_action_proposals_owner_status', table_name='action_proposals')
    op.drop_index('ix_action_proposals_source_message_id', table_name='action_proposals')
    op.drop_index('ix_action_proposals_conversation_id', table_name='action_proposals')
    op.drop_table('action_proposals')
