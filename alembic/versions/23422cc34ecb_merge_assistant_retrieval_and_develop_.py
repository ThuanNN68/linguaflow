"""merge the assistant's retrieval tables with develop_v2

Both sides branched from `f2b6d84c05e1`. develop_v2 carried on through message
visibility, private assistant messages and user avatars up to `d6e7f8a9b0c1`;
this branch added the assistant's own retrieval and measurement tables —
`assistant_chunks`, `assistant_user_memory` and `assistant_attempts` — up to
`c4a8e02f7d61`.

This revision itself changes nothing; it only rejoins the histories. Without it
`alembic upgrade head` refuses with "Multiple head revisions are present" — a
failure that surfaces at deploy time rather than at merge time, because git sees
two files added on two branches and has nothing to conflict about.

Rejoining was not quite free, and the reason is worth recording. Both sides
added `messages.visible_to_user_id`: develop_v2 through
`b2f8d4c1a903_add_private_assistant_messages`, this branch through
`c9a4d2e81f37_add_message_visibility`. That is not duplicated work — private
assistant replies and message visibility are one feature approached from two
directions — but only the second of those was written to tolerate running after
the other. On a database that had taken this branch's path, the merged history
died on a bare `DuplicateColumnError`. `b2f8d4c1a903` now guards itself the same
way its counterpart already did, so the order they ran in stops mattering.

Merge revisions are the established way this repo reconciles a fork; see
`f2b6d84c05e1`, `d6e7f8a9b0c1` and `9c4d2e7f1a6b`, all of which do the same with
a tuple `down_revision`.

Revision ID: 23422cc34ecb
Revises: c4a8e02f7d61, d6e7f8a9b0c1
Create Date: 2026-08-28

"""

from collections.abc import Sequence

revision: str = "23422cc34ecb"
down_revision: str | Sequence[str] | None = ("c4a8e02f7d61", "d6e7f8a9b0c1")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Nothing to do: this only rejoins two independent histories."""


def downgrade() -> None:
    """Nothing to undo."""
