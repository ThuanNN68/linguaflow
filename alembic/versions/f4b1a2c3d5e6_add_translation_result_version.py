"""add version and unique constraint on translation_results

Revision ID: f4b1a2c3d5e6
Revises: e2f8a1c9b3d4
"""

from alembic import op
import sqlalchemy as sa


revision = "f4b1a2c3d5e6"
down_revision = "e2f8a1c9b3d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "translation_results",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.drop_index("ix_translation_results_lookup", table_name="translation_results")
    op.create_index(
        "ix_translation_results_lookup",
        "translation_results",
        ["message_id", "target_language", "honorific_profile", "translation_tone", "version"],
    )
    op.create_unique_constraint(
        "uq_translation_results_bucket_version",
        "translation_results",
        ["message_id", "target_language", "honorific_profile", "translation_tone", "version"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_translation_results_bucket_version", "translation_results", type_="unique")
    op.drop_index("ix_translation_results_lookup", table_name="translation_results")
    op.create_index(
        "ix_translation_results_lookup",
        "translation_results",
        ["message_id", "target_language", "honorific_profile", "translation_tone"],
    )
    op.drop_column("translation_results", "version")
