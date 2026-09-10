"""Chat persistence models."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .constants import (
    MESSAGE_LIFECYCLE_CHECK,
    MESSAGE_TYPES,
    MESSAGE_VISIBILITIES,
    TRANSCRIPTION_STATUSES,
    TRANSLATION_TONES,
    _in_clause,
)


class Conversation(Base):
    """A durable direct or group chat conversation."""

    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(
            "type IN ('direct', 'group')",
            name="ck_conversations_type",
        ),
        Index("ix_conversations_created_by", "created_by"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    type: Mapped[str] = mapped_column(String(10), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ConversationMember(Base):
    """A user's membership in a conversation."""

    __tablename__ = "conversation_members"
    __table_args__ = (Index("ix_conversation_members_user_id_conversation_id", "user_id", "conversation_id"),)

    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    role: Mapped[str] = mapped_column(String(10), nullable=False, default="member", server_default="member")
    # How far this member has read. Null means they have never opened the
    # conversation, so everything in it counts as unread.
    last_read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # These are deliberately membership state: pinning or muting a thread must
    # never affect what another member sees.
    is_pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_muted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")


class UserSettings(Base):
    """Optional per-user UI and translation preferences, created lazily."""

    __tablename__ = "user_settings"
    __table_args__ = (
        CheckConstraint(
            _in_clause("translation_tone", TRANSLATION_TONES),
            name="ck_user_settings_translation_tone",
        ),
    )

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    auto_translate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    show_original_by_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    translation_tone: Mapped[str] = mapped_column(
        String(20), nullable=False, default="natural", server_default="natural"
    )
    sound_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    read_receipts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    ai_smart_assistance: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), server_default=func.now()
    )


class BlockedUser(Base):
    """A directional social block between two accounts."""

    __tablename__ = "blocked_users"
    __table_args__ = (
        CheckConstraint("blocker_id <> blocked_id", name="ck_blocked_users_not_self"),
        Index("ix_blocked_users_blocked_id", "blocked_id"),
    )

    blocker_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    blocked_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CallSession(Base):
    """Durable state for a normal direct audio/video call.

    Provider join credentials are intentionally absent.  They are short lived
    and issued on demand only to a participant after application authorization.
    """

    __tablename__ = "call_sessions"
    __table_args__ = (
        CheckConstraint("call_type IN ('voice', 'video')", name="ck_call_sessions_call_type"),
        CheckConstraint(
            "status IN ('ringing', 'accepted', 'rejected', 'ended', 'missed', 'failed')",
            name="ck_call_sessions_status",
        ),
        CheckConstraint("caller_id <> callee_id", name="ck_call_sessions_not_self"),
        Index("ix_call_sessions_conversation_created", "conversation_id", "created_at"),
        Index("ix_call_sessions_caller_created", "caller_id", "created_at"),
        Index("ix_call_sessions_callee_created", "callee_id", "created_at"),
        Index("ix_call_sessions_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    caller_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    callee_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    call_type: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_room_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # The provider returns the canonical room URL when the room is created.
    # It can contain a custom Daily domain, so reconstructing it from the room
    # name would send participants to the wrong place.
    provider_room_url: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Message(Base):
    """An original chat message persisted before realtime delivery."""

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(
            _in_clause("message_type", MESSAGE_TYPES),
            name="ck_messages_message_type",
        ),
        CheckConstraint(
            f"transcription_status IS NULL OR {_in_clause('transcription_status', TRANSCRIPTION_STATUSES)}",
            name="ck_messages_transcription_status",
        ),
        CheckConstraint(
            MESSAGE_LIFECYCLE_CHECK,
            name="ck_messages_voice_lifecycle",
        ),
        UniqueConstraint(
            "sender_id",
            "conversation_id",
            "client_message_id",
            name="uq_messages_sender_conversation_client_message",
        ),
        Index("ix_messages_conversation_created_at_id", "conversation_id", "created_at", "id"),
        CheckConstraint(
            _in_clause("visibility", MESSAGE_VISIBILITIES),
            name="ck_messages_visibility",
        ),
        # A private message nobody is named on would be readable by no one and
        # deletable by no process that knows to look for it.
        CheckConstraint(
            "visibility = 'public' OR visible_to_user_id IS NOT NULL",
            name="ck_messages_private_names_a_reader",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    client_message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    sender_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    # Existing and new ordinary messages remain `text` without a transcription
    # state. Voice rows start with empty original_text/pending, then replace the
    # same canonical field with the complete transcript before becoming
    # completed. No second transcript column is intentionally introduced.
    message_type: Mapped[str] = mapped_column(String(10), nullable=False, default="text", server_default="text")
    transcription_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Structured @mentions are stored alongside the original text so history
    # and realtime deliveries agree without reparsing display names.
    mentions_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    # Assistant replies are ordinary durable messages, but the UI renders them
    # as the in-thread assistant rather than as the member who invoked it.
    assistant_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    # Who may read this row. `public` is every member, and is what an ordinary
    # message is; `private` is the person named below and nobody else.
    #
    # This exists because the assistant answers a mention inside a group, and
    # its answer can summarise what other people committed to. Delivered to the
    # whole group that is both noisy and a disclosure about members who never
    # asked for it, so the answer belongs to the person who invoked it (ADR-31).
    #
    # Enforced in the WHERE clause of every read, never by dropping rows after
    # fetching them: a filter in the serializer still puts the text on the wire.
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="public", server_default="public")
    visible_to_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    # Provisional on insert â€” it is the sender's preferred_language, which says
    # what they usually write in, not what this message is in. The agent's
    # detect_language node overwrites it (docs/api/contract.md section 4.3).
    source_language: Mapped[str] = mapped_column(String(10), nullable=False, default="en")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    # SET NULL rather than CASCADE: withdrawing a message must not take the
    # replies to it down as well â€” they are other people's words.
    reply_to_message_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    # A forward is a new message (and is translated for its new recipients),
    # while this link lets clients label its provenance.
    forwarded_from_message_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    edited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # Removal is soft: `translation_results` and `translation_attempts` reference
    # this row, so deleting it would take the measurement evidence ADR-16 exists
    # to preserve down with it, silently skewing the fallback rate in Â§3.4.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class SavedMessage(Base):
    """A caller-specific bookmark for a durable message."""

    __tablename__ = "saved_messages"
    __table_args__ = (
        UniqueConstraint("user_id", "message_id", name="uq_saved_messages_user_message"),
        Index("ix_saved_messages_user_created_id", "user_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    message_id: Mapped[str] = mapped_column(String(36), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class MessageReaction(Base):
    """One explicit emoji a member attached to a message."""

    __tablename__ = "message_reactions"
    __table_args__ = (
        UniqueConstraint("message_id", "user_id", "emoji", name="uq_message_reactions_message_user_emoji"),
        Index("ix_message_reactions_message_id", "message_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    message_id: Mapped[str] = mapped_column(String(36), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    emoji: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Attachment(Base):
    """A file uploaded to a conversation, optionally carried by a message.

    `message_id` is nullable because the file is uploaded before the message
    that carries it exists: the client uploads, gets an id back, then sends the
    message referencing it (docs/api/contract.md Â§3.7).
    """

    __tablename__ = "attachments"
    __table_args__ = (
        Index("ix_attachments_conversation_id", "conversation_id"),
        Index("ix_attachments_message_id", "message_id"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    # SET NULL keeps the stored file reachable for audit if its message goes.
    message_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    uploader_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
