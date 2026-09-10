"""add glossary, profiles and honorific translations

Three things arrive together because they are one feature: a glossary that
pins how a term is rendered, a per-conversation profile that decides which
rendering applies, and a standing on every translation so the address forms
match the reader.

Written by `--autogenerate` and then corrected, because the draft was wrong in
five ways that only show up against a database with rows in it:

1. It emitted `pgvector.sqlalchemy.vector.VECTOR(...)` without importing
   pgvector, so it would have failed with NameError before touching the
   database.
2. It never created the `vector` extension. Autogenerate compares tables and
   has no concept of an extension, so on a fresh production database every
   `vector` column would fail with "type vector does not exist".
3. It added `honorific_profile` as NOT NULL with no default and no backfill,
   which raises NotNullViolation on any database that already has
   translations — that is, all of them. Added nullable, filled with 'peer',
   then tightened, the same three steps as c3a8e57d21b4.
4. It proposed dropping the server defaults on `pending_registrations.attempts`
   and `.request_count`. That is pre-existing drift between the model, which
   defaults in Python, and the database, which was given a server default by an
   earlier migration. It belongs to a different feature and changing it here
   would alter behaviour nobody asked about, so it is removed.
5. Its downgrade restored the narrower unique constraint without first removing
   the rows that made it necessary, so it would fail with a duplicate key on
   exactly the databases the feature had been running on.

'peer' is the backfill value because it is the neutral standing. Filling with
'client' or 'senior' would assert something about translations produced before
the concept existed.

Revision ID: ef06ca79ef49
Revises: d4e9f60a12b3
Create Date: 2026-08-20 18:04:54.271020

"""
from typing import Sequence, Union

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'ef06ca79ef49'
down_revision: Union[str, Sequence[str], None] = 'd4e9f60a12b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Kept in step with EMBEDDING_DIM in src/database/models.py. A migration must
# not import from the application: the model will move on and this file has to
# keep describing the database as it was at this revision.
EMBEDDING_DIM = 768


def upgrade() -> None:
    """Upgrade schema."""
    # Must come first: every `vector` column below depends on it, and the
    # statement is a no-op on a database where the extension is already
    # installed. On managed PostgreSQL the extension has to be available to the
    # connecting role — see docs/operations/deployment.md.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table('glossary_entries',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('source_term', sa.String(length=200), nullable=False),
    sa.Column('source_term_normalized', sa.String(length=200), nullable=False),
    sa.Column('target_term', sa.String(length=200), nullable=False),
    sa.Column('source_language', sa.String(length=10), nullable=False),
    sa.Column('target_language', sa.String(length=10), nullable=False),
    sa.Column('domain', sa.String(length=50), nullable=False),
    sa.Column('audience', sa.String(length=50), nullable=False),
    sa.Column('keep_verbatim', sa.Boolean(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('approved_by', sa.String(length=36), nullable=True),
    sa.Column('embedding', pgvector.sqlalchemy.Vector(EMBEDDING_DIM), nullable=True),
    sa.Column('embedding_model', sa.String(length=100), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('active', 'retired')", name='ck_glossary_entries_status'),
    sa.ForeignKeyConstraint(['approved_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_term_normalized', 'source_language', 'target_language', 'domain', 'audience', name='uq_glossary_entries_term_scope')
    )
    op.create_index('ix_glossary_entries_languages', 'glossary_entries', ['source_language', 'target_language', 'status'], unique=False)
    op.create_index('ix_glossary_entries_embedding', 'glossary_entries', ['embedding'], unique=False, postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})

    op.create_table('glossary_proposals',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('source_term', sa.String(length=200), nullable=False),
    sa.Column('source_term_normalized', sa.String(length=200), nullable=False),
    sa.Column('target_term', sa.String(length=200), nullable=False),
    sa.Column('source_language', sa.String(length=10), nullable=False),
    sa.Column('target_language', sa.String(length=10), nullable=False),
    sa.Column('domain', sa.String(length=50), nullable=False),
    sa.Column('audience', sa.String(length=50), nullable=False),
    sa.Column('keep_verbatim', sa.Boolean(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('occurrence_count', sa.Integer(), nullable=False),
    sa.Column('distinct_user_count', sa.Integer(), nullable=False),
    sa.Column('rationale', sa.Text(), nullable=False),
    sa.Column('reviewed_by', sa.String(length=36), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reject_reason', sa.Text(), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.Vector(EMBEDDING_DIM), nullable=True),
    sa.Column('embedding_model', sa.String(length=100), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('pending', 'approved', 'rejected')", name='ck_glossary_proposals_status'),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_glossary_proposals_status_created_at', 'glossary_proposals', ['status', 'created_at'], unique=False)
    op.create_index('ix_glossary_proposals_embedding', 'glossary_proposals', ['embedding'], unique=False, postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})

    op.create_table('glossary_proposal_citations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('proposal_id', sa.String(length=36), nullable=False),
    sa.Column('anonymized_snippet', sa.Text(), nullable=False),
    sa.Column('observed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['proposal_id'], ['glossary_proposals.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_glossary_proposal_citations_proposal_id', 'glossary_proposal_citations', ['proposal_id'], unique=False)

    op.create_table('conversation_profiles',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('domain', sa.String(length=50), nullable=False),
    sa.Column('audience', sa.String(length=50), nullable=False),
    sa.Column('message_count_at_last_run', sa.Integer(), nullable=False),
    sa.Column('consecutive_stable_runs', sa.Integer(), nullable=False),
    sa.Column('locked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('rationale', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('conversation_id', name='uq_conversation_profiles_conversation')
    )

    op.create_table('participant_profiles',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('honorific_profile', sa.String(length=20), nullable=False),
    sa.Column('inferred_by', sa.String(length=20), nullable=False),
    sa.Column('confidence', sa.Integer(), nullable=False),
    sa.Column('rationale', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("honorific_profile IN ('senior', 'peer', 'junior', 'client')", name='ck_participant_profiles_honorific_profile'),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('conversation_id', 'user_id', name='uq_participant_profiles_conversation_user')
    )
    op.create_index('ix_participant_profiles_conversation_id', 'participant_profiles', ['conversation_id'], unique=False)

    op.create_table('message_embeddings',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('message_id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.Vector(EMBEDDING_DIM), nullable=True),
    sa.Column('embedding_model', sa.String(length=100), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('message_id', name='uq_message_embeddings_message')
    )
    op.create_index('ix_message_embeddings_embedding', 'message_embeddings', ['embedding'], unique=False, postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})

    op.create_table('correction_log',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('source_phrase', sa.String(length=200), nullable=False),
    sa.Column('corrected_target', sa.String(length=200), nullable=False),
    sa.Column('source_language', sa.String(length=10), nullable=False),
    sa.Column('target_language', sa.String(length=10), nullable=False),
    sa.Column('domain', sa.String(length=50), nullable=False),
    sa.Column('audience', sa.String(length=50), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('translation_id', sa.String(length=36), nullable=True),
    sa.Column('consent_to_share', sa.Boolean(), nullable=False),
    sa.Column('anonymized_snippet', sa.Text(), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.Vector(EMBEDDING_DIM), nullable=True),
    sa.Column('embedding_model', sa.String(length=100), nullable=False),
    sa.Column('observed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['translation_id'], ['translation_results.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_correction_log_consent_observed_at', 'correction_log', ['consent_to_share', 'observed_at'], unique=False)
    op.create_index('ix_correction_log_embedding', 'correction_log', ['embedding'], unique=False, postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})

    # Three steps rather than one, on both tables: the column is NOT NULL and
    # the tables already have rows, so it is added nullable, filled, and only
    # then tightened. No server_default is set — the application always supplies
    # a value, and a default here would hide a write path that forgot to.
    op.add_column('translation_attempts', sa.Column('honorific_profile', sa.String(length=20), nullable=True))
    op.execute("UPDATE translation_attempts SET honorific_profile = 'peer' WHERE honorific_profile IS NULL")
    op.alter_column('translation_attempts', 'honorific_profile', existing_type=sa.String(length=20), nullable=False)

    op.add_column('translation_results', sa.Column('honorific_profile', sa.String(length=20), nullable=True))
    op.execute("UPDATE translation_results SET honorific_profile = 'peer' WHERE honorific_profile IS NULL")
    op.alter_column('translation_results', 'honorific_profile', existing_type=sa.String(length=20), nullable=False)

    # Widening the key, so the old constraint has to go before the new one can
    # exist. Order matters only in that direction; nothing is rejected in
    # between because every row now holds the same 'peer'.
    op.drop_constraint('uq_translation_results_message_target', 'translation_results', type_='unique')
    op.create_unique_constraint(
        'uq_translation_results_message_target_profile',
        'translation_results',
        ['message_id', 'target_language', 'honorific_profile'],
    )
    op.create_check_constraint(
        'ck_translation_results_honorific_profile',
        'translation_results',
        "honorific_profile IN ('senior', 'peer', 'junior', 'client')",
    )


def downgrade() -> None:
    """Downgrade schema.

    Loses data, and cannot avoid it. The old constraint allows one row per
    (message_id, target_language), so every surplus standing has to be deleted
    before it can be restored — otherwise the constraint fails with a duplicate
    key on precisely the databases where the feature had been running. The row
    kept is the one with the smallest `id`: arbitrary, but deterministic.

    That delete cascades into `feedbacks` and `translation_edits`, and nulls
    `translation_attempts.translation_id` and `correction_log.translation_id`
    through their ON DELETE SET NULL.

    The `vector` extension is deliberately left installed: dropping it would
    take out anything else in the database that uses it, and it costs nothing
    to leave.
    """
    op.drop_constraint('ck_translation_results_honorific_profile', 'translation_results', type_='check')
    op.drop_constraint('uq_translation_results_message_target_profile', 'translation_results', type_='unique')
    op.execute(
        "DELETE FROM translation_results WHERE id NOT IN ("
        "  SELECT MIN(id) FROM translation_results GROUP BY message_id, target_language"
        ")"
    )
    op.create_unique_constraint(
        'uq_translation_results_message_target',
        'translation_results',
        ['message_id', 'target_language'],
    )
    op.drop_column('translation_results', 'honorific_profile')
    op.drop_column('translation_attempts', 'honorific_profile')

    op.drop_index('ix_correction_log_embedding', table_name='correction_log', postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.drop_index('ix_correction_log_consent_observed_at', table_name='correction_log')
    op.drop_table('correction_log')

    op.drop_index('ix_message_embeddings_embedding', table_name='message_embeddings', postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.drop_table('message_embeddings')

    op.drop_index('ix_participant_profiles_conversation_id', table_name='participant_profiles')
    op.drop_table('participant_profiles')

    op.drop_table('conversation_profiles')

    op.drop_index('ix_glossary_proposal_citations_proposal_id', table_name='glossary_proposal_citations')
    op.drop_table('glossary_proposal_citations')

    op.drop_index('ix_glossary_proposals_embedding', table_name='glossary_proposals', postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.drop_index('ix_glossary_proposals_status_created_at', table_name='glossary_proposals')
    op.drop_table('glossary_proposals')

    op.drop_index('ix_glossary_entries_embedding', table_name='glossary_entries', postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.drop_index('ix_glossary_entries_languages', table_name='glossary_entries')
    op.drop_table('glossary_entries')
