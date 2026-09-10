"""allow multiple translation versions to preserve feedback history

Revision ID: e2f8a1c9b3d4
Revises: d148e4f6b726
"""

from alembic import op
import sqlalchemy as sa


revision = "e2f8a1c9b3d4"
down_revision = "d148e4f6b726"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_translation_results_message_target_profile_tone", "translation_results", type_="unique")
    op.create_index(
        "ix_translation_results_lookup",
        "translation_results",
        ["message_id", "target_language", "honorific_profile", "translation_tone"],
    )


def downgrade() -> None:
    op.drop_index("ix_translation_results_lookup", table_name="translation_results")
    op.create_unique_constraint(
        "uq_translation_results_message_target_profile_tone",
        "translation_results",
        ["message_id", "target_language", "honorific_profile", "translation_tone"],
    )
