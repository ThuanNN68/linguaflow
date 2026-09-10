"""REST exposure for the Phase 1 voice-message lifecycle contract."""

import pytest

from src.database.models import Attachment, Message


@pytest.mark.asyncio
async def test_history_exposes_a_pending_voice_message_without_fake_text(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
) -> None:
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = Message(
        client_message_id="voice-pending-1",
        conversation_id=conversation.id,
        sender_id=test_user.id,
        original_text="",
        message_type="voice",
        transcription_status="pending",
        source_language="vi",
    )
    test_db.add(message)
    await test_db.flush()
    test_db.add(
        Attachment(
            id="voice-pending-1.webm",
            conversation_id=conversation.id,
            message_id=message.id,
            uploader_id=test_user.id,
            filename="voice-message.webm",
            content_type="audio/webm",
            size=128,
        )
    )
    await test_db.commit()

    response = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages",
        headers=test_user_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["message_type"] == "voice"
    assert body[0]["transcription_status"] == "pending"
    assert body[0]["original_text"] == ""
    assert body[0]["attachment"]["content_type"] == "audio/webm"
