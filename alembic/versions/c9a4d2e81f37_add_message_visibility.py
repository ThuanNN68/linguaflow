"""add message visibility

Lets the assistant answer a mention privately inside a group: the reply belongs
to whoever invoked it, not to every member (CONTRACT.md §5 note 20, ADR-31).

Revision ID: c9a4d2e81f37
Revises: b8f1c72d4e90
Create Date: 2026-08-27

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c9a4d2e81f37"
down_revision: str | Sequence[str] | None = "b8f1c72d4e90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the two columns, defaulting every existing row to public.

    `server_default="public"` is what makes this safe on a live table: a message
    written before this column existed was readable by every member, and that is
    exactly what `public` means. Backfilling would say the same thing more
    slowly.
    """
    # An earlier local privacy migration already added ``visible_to_user_id``
    # on some developer databases. Keep this upstream migration safe for both
    # histories while still creating the column on a fresh installation.
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("messages")}
    if "visibility" not in columns:
        op.add_column(
            "messages",
            sa.Column("visibility", sa.String(length=16), nullable=False, server_default="public"),
        )
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

    constraints = {constraint["name"] for constraint in inspector.get_check_constraints("messages")}
    if "ck_messages_visibility" not in constraints:
        op.create_check_constraint(
            "ck_messages_visibility",
            "messages",
            "visibility IN ('public', 'private')",
        )
    if "ck_messages_private_names_a_reader" not in constraints:
        op.create_check_constraint(
            "ck_messages_private_names_a_reader",
            "messages",
            "visibility = 'public' OR visible_to_user_id IS NOT NULL",
        )


def downgrade() -> None:
    """Drop both columns.

    Going down makes every private assistant reply readable by the whole
    conversation again, because there is no longer a column saying otherwise.
    Anyone reversing this on a database with real traffic should delete the
    `assistant_generated` rows first.
    """
    op.drop_constraint("ck_messages_private_names_a_reader", "messages", type_="check")
    op.drop_constraint("ck_messages_visibility", "messages", type_="check")
    op.drop_column("messages", "visible_to_user_id")
    op.drop_column("messages", "visibility")
