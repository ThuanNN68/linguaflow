"""Transactional account erasure with referentially-safe message tombstones."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import (
    ActionProposal,
    AgentConsent,
    AssistantAttempt,
    AssistantUserMemory,
    Attachment,
    BlockedUser,
    CalendarEvent,
    ConversationMember,
    Feedback,
    Message,
    MessageReaction,
    ParticipantProfile,
    PasswordResetToken,
    RefreshSession,
    SavedMessage,
    TranslationEdit,
    User,
    UserSettings,
)
from src.services.identity.audit import record_audit_event
from src.services.messaging.attachment_storage import AttachmentStorage


class AccountErasureError(RuntimeError):
    """Account data could not be erased safely."""


async def erase_account(db: AsyncSession, user: User, *, storage: AttachmentStorage | None = None) -> None:
    """Erase direct personal data and turn the account into an inert tombstone."""
    if user.deleted_at is not None:
        return

    attachments = (await db.scalars(select(Attachment).where(Attachment.uploader_id == user.id))).all()
    object_store = storage or AttachmentStorage()
    try:
        for attachment in attachments:
            await object_store.delete(attachment)
    except Exception as exc:
        raise AccountErasureError("Unable to erase every attachment; no account data was changed") from exc

    now = datetime.now(UTC)
    message_ids = list((await db.scalars(select(Message.id).where(Message.sender_id == user.id))).all())
    for model, column in (
        (RefreshSession, RefreshSession.user_id), (PasswordResetToken, PasswordResetToken.user_id),
        (UserSettings, UserSettings.user_id), (AgentConsent, AgentConsent.user_id),
        (AssistantUserMemory, AssistantUserMemory.user_id), (CalendarEvent, CalendarEvent.user_id),
        (Feedback, Feedback.user_id), (TranslationEdit, TranslationEdit.editor_id),
        (ParticipantProfile, ParticipantProfile.user_id), (SavedMessage, SavedMessage.user_id),
        (MessageReaction, MessageReaction.user_id), (ActionProposal, ActionProposal.owner_user_id),
    ):
        await db.execute(delete(model).where(column == user.id))
    await db.execute(delete(BlockedUser).where((BlockedUser.blocker_id == user.id) | (BlockedUser.blocked_id == user.id)))
    await db.execute(delete(ConversationMember).where(ConversationMember.user_id == user.id))
    await db.execute(delete(Attachment).where(Attachment.uploader_id == user.id))

    if message_ids:
        await db.execute(update(Message).where(Message.id.in_(message_ids)).values(
            original_text="", mentions_json="[]", deleted_at=now, edited_at=now
        ))
        await db.execute(update(AssistantAttempt).where(AssistantAttempt.user_id == user.id).values(
            user_id=None, source_message_id=None
        ))

    await record_audit_event(
        db, actor_id=user.id, action="account.erased", resource_type="user", resource_id=user.id,
        metadata={"message_count": len(message_ids), "attachment_count": len(attachments)},
    )
    user.email = f"deleted-{user.id}@deleted.invalid"
    user.username = None
    user.display_name = "Deleted user"
    user.avatar_url = None
    user.bio = None
    user.password_hash = "!erased-account!"
    user.google_sub = None
    user.role = "deleted"
    user.preferred_language = "en"
    user.interface_language = "en"
    user.deleted_at = now
    await db.commit()
