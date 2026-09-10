"""Service tests for atomic voice creation, strict claims, and idempotency."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from src.database.models import Attachment, Message
from src.services.messaging.chat import (
    ChatService,
    VoiceAttachmentClaimedError,
    VoiceAttachmentConversationError,
    VoiceAttachmentNotAudioError,
    VoiceAttachmentNotFoundError,
    VoiceAttachmentOwnershipError,
    VoiceMessageIdConflictError,
)


async def _add_attachment(
    db,
    *,
    attachment_id: str,
    conversation_id: str,
    uploader_id: str,
    filename: str = "recording.webm",
    content_type: str = "audio/webm; codecs=opus",
    size: int = 1234,
    message_id: str | None = None,
) -> Attachment:
    attachment = Attachment(
        id=attachment_id,
        conversation_id=conversation_id,
        uploader_id=uploader_id,
        filename=filename,
        content_type=content_type,
        size=size,
        message_id=message_id,
    )
    db.add(attachment)
    await db.commit()
    return attachment


@pytest.mark.asyncio
async def test_voice_message_and_audio_claim_commit_as_one_durable_result(
    test_db,
    test_user,
    test_user_two,
    conversation_factory,
):
    conversation = await conversation_factory(test_user, [test_user_two])
    attachment = await _add_attachment(
        test_db,
        attachment_id="voice-atomic.webm",
        conversation_id=conversation.id,
        uploader_id=test_user.id,
    )

    result = await ChatService(test_db).send_voice_message(
        sender_id=test_user.id,
        conversation_id=conversation.id,
        client_message_id="voice-atomic-1",
        attachment_id=attachment.id,
    )

    assert result.created is True
    assert result.recipient_ids == (test_user_two.id,)
    assert result.message.message_type == "voice"
    assert result.message.original_text == ""
    assert result.message.transcription_status == "pending"

    await test_db.refresh(attachment)
    assert attachment.message_id == result.message.id
    assert await test_db.scalar(select(func.count()).select_from(Message).where(Message.id == result.message.id)) == 1


@pytest.mark.asyncio
async def test_equivalent_voice_retry_returns_same_message_and_claim(
    test_db,
    test_user,
    test_user_two,
    conversation_factory,
):
    conversation = await conversation_factory(test_user, [test_user_two])
    attachment = await _add_attachment(
        test_db,
        attachment_id="voice-retry.webm",
        conversation_id=conversation.id,
        uploader_id=test_user.id,
    )
    service = ChatService(test_db)

    first = await service.send_voice_message(
        sender_id=test_user.id,
        conversation_id=conversation.id,
        client_message_id="voice-retry-1",
        attachment_id=attachment.id,
    )
    retry = await service.send_voice_message(
        sender_id=test_user.id,
        conversation_id=conversation.id,
        client_message_id="voice-retry-1",
        attachment_id=attachment.id,
    )

    assert first.created is True
    assert retry.created is False
    assert retry.message.id == first.message.id
    assert (
        await test_db.scalar(
            select(func.count()).select_from(Message).where(Message.client_message_id == "voice-retry-1")
        )
        == 1
    )
    await test_db.refresh(attachment)
    assert attachment.message_id == first.message.id


@pytest.mark.asyncio
async def test_voice_idempotency_rejects_a_different_attachment_or_message_type(
    test_db,
    test_user,
    test_user_two,
    conversation_factory,
):
    conversation = await conversation_factory(test_user, [test_user_two])
    first_attachment = await _add_attachment(
        test_db,
        attachment_id="voice-conflict-one.webm",
        conversation_id=conversation.id,
        uploader_id=test_user.id,
    )
    second_attachment = await _add_attachment(
        test_db,
        attachment_id="voice-conflict-two.webm",
        conversation_id=conversation.id,
        uploader_id=test_user.id,
    )
    service = ChatService(test_db)
    await service.send_voice_message(
        sender_id=test_user.id,
        conversation_id=conversation.id,
        client_message_id="voice-conflict-1",
        attachment_id=first_attachment.id,
    )

    with pytest.raises(VoiceMessageIdConflictError):
        await service.send_voice_message(
            sender_id=test_user.id,
            conversation_id=conversation.id,
            client_message_id="voice-conflict-1",
            attachment_id=second_attachment.id,
        )

    await service.send_message(
        sender_id=test_user.id,
        conversation_id=conversation.id,
        client_message_id="text-key-1",
        text="ordinary text",
    )
    with pytest.raises(VoiceMessageIdConflictError):
        await service.send_voice_message(
            sender_id=test_user.id,
            conversation_id=conversation.id,
            client_message_id="text-key-1",
            attachment_id=second_attachment.id,
        )


@pytest.mark.asyncio
async def test_missing_voice_attachment_is_rejected_without_creating_a_message(
    test_db,
    test_user,
    test_user_two,
    conversation_factory,
):
    conversation = await conversation_factory(test_user, [test_user_two])

    with pytest.raises(VoiceAttachmentNotFoundError):
        await ChatService(test_db).send_voice_message(
            sender_id=test_user.id,
            conversation_id=conversation.id,
            client_message_id="voice-missing-1",
            attachment_id="missing.webm",
        )
    await test_db.rollback()

    assert (
        await test_db.scalar(
            select(func.count()).select_from(Message).where(Message.client_message_id == "voice-missing-1")
        )
        == 0
    )


@pytest.mark.asyncio
async def test_voice_attachment_must_match_conversation_and_authenticated_uploader(
    test_db,
    test_user,
    test_user_two,
    conversation_factory,
):
    conversation = await conversation_factory(test_user, [test_user_two])
    other_conversation = await conversation_factory(test_user, [])
    other_conversation_attachment = await _add_attachment(
        test_db,
        attachment_id="other-conversation.webm",
        conversation_id=other_conversation.id,
        uploader_id=test_user.id,
    )
    other_uploader_attachment = await _add_attachment(
        test_db,
        attachment_id="other-uploader.webm",
        conversation_id=conversation.id,
        uploader_id=test_user_two.id,
    )
    service = ChatService(test_db)

    with pytest.raises(VoiceAttachmentConversationError):
        await service.send_voice_message(
            sender_id=test_user.id,
            conversation_id=conversation.id,
            client_message_id="wrong-conversation-1",
            attachment_id=other_conversation_attachment.id,
        )

    with pytest.raises(VoiceAttachmentOwnershipError):
        await service.send_voice_message(
            sender_id=test_user.id,
            conversation_id=conversation.id,
            client_message_id="wrong-uploader-1",
            attachment_id=other_uploader_attachment.id,
        )


@pytest.mark.asyncio
async def test_claimed_or_non_audio_attachment_is_rejected_without_partial_voice_row(
    test_db,
    test_user,
    test_user_two,
    conversation_factory,
):
    conversation = await conversation_factory(test_user, [test_user_two])
    carrier = Message(
        client_message_id="carrier-1",
        conversation_id=conversation.id,
        sender_id=test_user.id,
        original_text="existing file message",
        source_language="en",
    )
    test_db.add(carrier)
    await test_db.commit()
    claimed = await _add_attachment(
        test_db,
        attachment_id="claimed.webm",
        conversation_id=conversation.id,
        uploader_id=test_user.id,
        message_id=carrier.id,
    )
    document = await _add_attachment(
        test_db,
        attachment_id="document.pdf",
        conversation_id=conversation.id,
        uploader_id=test_user.id,
        filename="document.pdf",
        content_type="application/pdf",
    )
    service = ChatService(test_db)

    with pytest.raises(VoiceAttachmentClaimedError):
        await service.send_voice_message(
            sender_id=test_user.id,
            conversation_id=conversation.id,
            client_message_id="claimed-voice-1",
            attachment_id=claimed.id,
        )

    with pytest.raises(VoiceAttachmentNotAudioError):
        await service.send_voice_message(
            sender_id=test_user.id,
            conversation_id=conversation.id,
            client_message_id="document-voice-1",
            attachment_id=document.id,
        )
    await test_db.rollback()

    assert (
        await test_db.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.client_message_id.in_(["claimed-voice-1", "document-voice-1"]))
        )
        == 0
    )
