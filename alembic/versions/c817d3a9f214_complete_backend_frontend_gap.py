"""complete backend/frontend gap persistence

Revision ID: c817d3a9f214
Revises: b43d9a0f5312
"""

from alembic import op
import sqlalchemy as sa


revision = "c817d3a9f214"
down_revision = "b43d9a0f5312"
branch_labels = None
depends_on = None

_TONE_CHECK = "translation_tone IN ('natural', 'formal', 'casual', 'friendly')"


def upgrade() -> None:
    op.add_column("users", sa.Column("bio", sa.Text(), nullable=True))
    op.add_column(
        "conversation_members",
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("conversation_members", sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "conversation_members",
        sa.Column("is_muted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("auto_translate", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("show_original_by_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("translation_tone", sa.String(length=20), nullable=False, server_default="natural"),
        sa.Column("sound_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("read_receipts", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("ai_smart_assistance", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(_TONE_CHECK, name="ck_user_settings_translation_tone"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "blocked_users",
        sa.Column("blocker_id", sa.String(length=36), nullable=False),
        sa.Column("blocked_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("blocker_id <> blocked_id", name="ck_blocked_users_not_self"),
        sa.ForeignKeyConstraint(["blocker_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["blocked_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("blocker_id", "blocked_id"),
    )
    op.create_index("ix_blocked_users_blocked_id", "blocked_users", ["blocked_id"])
    op.create_table(
        "saved_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("message_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "message_id", name="uq_saved_messages_user_message"),
    )
    op.create_index("ix_saved_messages_user_created_id", "saved_messages", ["user_id", "created_at", "id"])
    op.create_table(
        "message_reactions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("message_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("emoji", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id", "user_id", "emoji", name="uq_message_reactions_message_user_emoji"),
    )
    op.create_index("ix_message_reactions_message_id", "message_reactions", ["message_id"])

    op.add_column("translation_results", sa.Column("translation_tone", sa.String(length=20), nullable=True))
    op.execute("UPDATE translation_results SET translation_tone = 'natural' WHERE translation_tone IS NULL")
    op.alter_column("translation_results", "translation_tone", existing_type=sa.String(length=20), nullable=False, server_default="natural")
    op.drop_constraint("uq_translation_results_message_target_profile", "translation_results", type_="unique")
    op.create_unique_constraint(
        "uq_translation_results_message_target_profile_tone",
        "translation_results",
        ["message_id", "target_language", "honorific_profile", "translation_tone"],
    )
    op.create_check_constraint("ck_translation_results_translation_tone", "translation_results", _TONE_CHECK)
    op.add_column("translation_attempts", sa.Column("translation_tone", sa.String(length=20), nullable=True))
    op.execute("UPDATE translation_attempts SET translation_tone = 'natural' WHERE translation_tone IS NULL")
    op.alter_column("translation_attempts", "translation_tone", existing_type=sa.String(length=20), nullable=False, server_default="natural")
    op.create_check_constraint("ck_translation_attempts_translation_tone", "translation_attempts", _TONE_CHECK)

    op.create_table(
        "call_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("caller_id", sa.String(length=36), nullable=False),
        sa.Column("callee_id", sa.String(length=36), nullable=False),
        sa.Column("call_type", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("provider_room_name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("call_type IN ('voice', 'video')", name="ck_call_sessions_call_type"),
        sa.CheckConstraint("status IN ('ringing', 'accepted', 'rejected', 'ended', 'missed', 'failed')", name="ck_call_sessions_status"),
        sa.CheckConstraint("caller_id <> callee_id", name="ck_call_sessions_not_self"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["caller_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["callee_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_call_sessions_conversation_created", "call_sessions", ["conversation_id", "created_at"])
    op.create_index("ix_call_sessions_caller_created", "call_sessions", ["caller_id", "created_at"])
    op.create_index("ix_call_sessions_callee_created", "call_sessions", ["callee_id", "created_at"])
    op.create_index("ix_call_sessions_status", "call_sessions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_call_sessions_status", table_name="call_sessions")
    op.drop_index("ix_call_sessions_callee_created", table_name="call_sessions")
    op.drop_index("ix_call_sessions_caller_created", table_name="call_sessions")
    op.drop_index("ix_call_sessions_conversation_created", table_name="call_sessions")
    op.drop_table("call_sessions")
    op.drop_constraint("ck_translation_attempts_translation_tone", "translation_attempts", type_="check")
    op.drop_column("translation_attempts", "translation_tone")
    op.drop_constraint("ck_translation_results_translation_tone", "translation_results", type_="check")
    op.drop_constraint("uq_translation_results_message_target_profile_tone", "translation_results", type_="unique")
    # The previous key cannot represent multiple tones. Retain one deterministic
    # row per old bucket before restoring it; dependent feedback/edit rows obey
    # their existing foreign-key policy.
    op.execute(
        "DELETE FROM translation_results WHERE id NOT IN ("
        "SELECT MIN(id) FROM translation_results "
        "GROUP BY message_id, target_language, honorific_profile)"
    )
    op.create_unique_constraint(
        "uq_translation_results_message_target_profile",
        "translation_results",
        ["message_id", "target_language", "honorific_profile"],
    )
    op.drop_column("translation_results", "translation_tone")
    op.drop_index("ix_message_reactions_message_id", table_name="message_reactions")
    op.drop_table("message_reactions")
    op.drop_index("ix_saved_messages_user_created_id", table_name="saved_messages")
    op.drop_table("saved_messages")
    op.drop_index("ix_blocked_users_blocked_id", table_name="blocked_users")
    op.drop_table("blocked_users")
    op.drop_table("user_settings")
    op.drop_column("conversation_members", "is_muted")
    op.drop_column("conversation_members", "pinned_at")
    op.drop_column("conversation_members", "is_pinned")
    op.drop_column("users", "bio")
