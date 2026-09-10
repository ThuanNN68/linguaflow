"""Keep explicit assistant mentions private to their requester.

Revision ID: b2f8d4c1a903
Revises: a7d6c2b8e4f1
"""

from alembic import op
import sqlalchemy as sa


revision = "b2f8d4c1a903"
down_revision = "a7d6c2b8e4f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the column, unless the other branch's migration already did.

    `c9a4d2e81f37_add_message_visibility` adds the same `visible_to_user_id`
    from the other side of the fork, and it already guards itself this way. Two
    branches arriving at the same column is not a mistake — private assistant
    replies and message visibility are the same feature approached from two
    directions — but only one of the two migrations was safe to run second, so
    a database that took the other path first met a bare `DuplicateColumnError`
    here, at `alembic upgrade head`, after the merge had already looked clean.

    Guarding costs one catalogue read on a fresh installation and makes the
    order the two ran in stop mattering.
    """
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("messages")}
    if "visible_to_user_id" not in columns:
        op.add_column(
            "messages",
            sa.Column(
                "visible_to_user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=True,
            ),
        )

    indexes = {index["name"] for index in inspector.get_indexes("messages")}
    if "ix_messages_conversation_visible_created_at" not in indexes:
        op.create_index(
            "ix_messages_conversation_visible_created_at",
            "messages",
            ["conversation_id", "visible_to_user_id", "created_at"],
        )


def downgrade() -> None:
    # `IF EXISTS` for the same reason: the other branch's migration may have
    # already taken them away.
    op.execute("DROP INDEX IF EXISTS ix_messages_conversation_visible_created_at")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS visible_to_user_id")
