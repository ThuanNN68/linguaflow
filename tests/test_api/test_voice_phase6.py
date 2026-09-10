"""Phase 6 durable history, retry, preview, and search coverage."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from src.api import chat as routes_module
from src.database.models import Attachment, Message, TranslationResult
from tests.conftest import auth_headers_for_user


async def _seed_voice(
    db,
    *,
    conversation_id: str,
    sender_id: str,
    key: str,
    status: str,
    transcript: str = "",
    with_attachment: bool = True,
    content_type: str = "audio/webm; codecs=opus",
) -> tuple[Message, Attachment | None]:
    message = Message(
        client_message_id=key,
        conversation_id=conversation_id,
        sender_id=sender_id,
        original_text=transcript,
        message_type="voice",
        transcription_status=status,
        source_language="vi",
        created_at=datetime.now(UTC),
    )
    db.add(message)
    await db.flush()
    attachment = None
    if with_attachment:
        suffix = "webm" if content_type.startswith("audio/") else "pdf"
        attachment = Attachment(
            id=f"{key}.{suffix}",
            conversation_id=conversation_id,
            message_id=message.id,
            uploader_id=sender_id,
            filename=f"recording.{suffix}",
            content_type=content_type,
            size=256,
        )
        db.add(attachment)
    await db.commit()
    return message, attachment


@pytest.mark.asyncio
async def test_history_restores_pending_completed_translation_and_failed_voice(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    pending, pending_audio = await _seed_voice(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        key="phase6-history-pending",
        status="pending",
    )
    full_transcript = "Đây là toàn bộ lời nhắn. Mã đơn hàng là A-017."
    completed, completed_audio = await _seed_voice(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        key="phase6-history-completed",
        status="completed",
        transcript=full_transcript,
    )
    failed, failed_audio = await _seed_voice(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        key="phase6-history-failed",
        status="failed",
    )
    translation = TranslationResult(
        message_id=completed.id,
        target_language=test_user.preferred_language,
        translated_text="This is the complete message. The order code is A-017.",
        model="test-model",
        latency_ms=1,
        is_fallback=False,
    )
    test_db.add(translation)
    await test_db.commit()

    response = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages",
        headers=test_user_headers,
    )

    assert response.status_code == 200
    by_id = {item["id"]: item for item in response.json()}
    assert by_id[pending.id]["original_text"] == ""
    assert by_id[pending.id]["transcription_status"] == "pending"
    assert by_id[pending.id]["attachment"]["id"] == pending_audio.id
    assert by_id[completed.id]["original_text"] == full_transcript
    assert by_id[completed.id]["transcription_status"] == "completed"
    assert by_id[completed.id]["attachment"]["id"] == completed_audio.id
    assert by_id[completed.id]["translations"][0]["translation_id"] == translation.id
    assert by_id[failed.id]["original_text"] == ""
    assert by_id[failed.id]["transcription_status"] == "failed"
    assert by_id[failed.id]["attachment"]["id"] == failed_audio.id

    shared = await client.get(
        f"/api/v1/conversations/{conversation.id}/attachments",
        headers=test_user_headers,
    )
    assert shared.status_code == 200
    assert {item["id"] for item in shared.json()} == {
        pending_audio.id,
        completed_audio.id,
        failed_audio.id,
    }
    assert all(item["content_type"].startswith("audio/") for item in shared.json())


@pytest.mark.asyncio
async def test_voice_preview_exposes_lifecycle_without_storing_placeholder(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    voice, _ = await _seed_voice(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        key="phase6-preview",
        status="pending",
    )

    pending = (await client.get("/api/v1/conversations", headers=test_user_headers)).json()[0]
    assert pending["last_message"] == ""
    assert pending["last_message_type"] == "voice"
    assert pending["last_message_transcription_status"] == "pending"
    pending_time = pending["last_message_at"]
    pending_unread = pending["unread_count"]

    voice.transcription_status = "failed"
    await test_db.commit()
    failed = (await client.get("/api/v1/conversations", headers=test_user_headers)).json()[0]
    assert failed["last_message"] == ""
    assert failed["last_message_type"] == "voice"
    assert failed["last_message_transcription_status"] == "failed"

    voice.transcription_status = "completed"
    voice.original_text = "Bản ghi đầy đủ cho xem trước"
    test_db.add(
        TranslationResult(
            message_id=voice.id,
            target_language=test_user.preferred_language,
            translated_text="Complete transcript preview",
            model="test-model",
            latency_ms=1,
            is_fallback=False,
        )
    )
    await test_db.commit()
    completed = (await client.get("/api/v1/conversations", headers=test_user_headers)).json()[0]
    assert completed["last_message"] == "Complete transcript preview"
    assert completed["last_message_type"] == "voice"
    assert completed["last_message_transcription_status"] == "completed"
    assert completed["last_message_at"] == pending_time
    assert completed["unread_count"] == pending_unread


@pytest.mark.asyncio
async def test_search_finds_only_completed_canonical_voice_transcript(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    completed, _ = await _seed_voice(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        key="phase6-search-completed",
        status="completed",
        transcript="The canonical searchable zephyr phrase is complete.",
    )
    await _seed_voice(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        key="phase6-search-pending",
        status="pending",
    )
    await _seed_voice(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        key="phase6-search-failed",
        status="failed",
    )

    found = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages/search",
        headers=test_user_headers,
        params={"q": "zephyr phrase"},
    )
    placeholder = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages/search",
        headers=test_user_headers,
        params={"q": "Voice message"},
    )
    filename = await client.get(
        f"/api/v1/conversations/{conversation.id}/messages/search",
        headers=test_user_headers,
        params={"q": "recording.webm"},
    )

    assert [item["message"]["id"] for item in found.json()["items"]] == [completed.id]
    assert placeholder.json()["items"] == []
    assert filename.json()["items"] == []


@pytest.mark.asyncio
async def test_retry_is_member_scoped_reuses_message_and_attachment_and_launches_once(
    client,
    monkeypatch,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    test_user_three,
    conversation_factory,
):
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message, attachment = await _seed_voice(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        key="phase6-retry-success",
        status="failed",
    )
    launches: list[dict[str, object]] = []
    monkeypatch.setattr(
        routes_module,
        "schedule_voice_transcription",
        lambda **kwargs: launches.append(kwargs),
    )

    outsider = await client.post(
        f"/api/v1/messages/{message.id}/transcription/retry",
        headers=auth_headers_for_user(test_user_three),
    )
    response = await client.post(
        f"/api/v1/messages/{message.id}/transcription/retry",
        headers=test_user_headers,
    )
    duplicate = await client.post(
        f"/api/v1/messages/{message.id}/transcription/retry",
        headers=test_user_headers,
    )

    assert outsider.status_code == 403
    assert response.status_code == 202
    assert response.json() == {
        "message_id": message.id,
        "conversation_id": conversation.id,
        "transcription_status": "pending",
        "status": "scheduled",
    }
    assert duplicate.status_code == 409
    assert len(launches) == 1
    assert launches[0]["message_id"] == message.id
    await test_db.refresh(message)
    await test_db.refresh(attachment)
    assert message.transcription_status == "pending"
    assert message.original_text == ""
    assert attachment.message_id == message.id


@pytest.mark.asyncio
async def test_concurrent_retry_requests_launch_exactly_one_stt_task(
    client,
    monkeypatch,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message, _ = await _seed_voice(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        key="phase6-retry-race",
        status="failed",
    )
    launches: list[str] = []
    monkeypatch.setattr(
        routes_module,
        "schedule_voice_transcription",
        lambda **kwargs: launches.append(str(kwargs["message_id"])),
    )

    responses = await asyncio.gather(
        client.post(
            f"/api/v1/messages/{message.id}/transcription/retry",
            headers=test_user_headers,
        ),
        client.post(
            f"/api/v1/messages/{message.id}/transcription/retry",
            headers=test_user_headers,
        ),
    )

    assert sorted(response.status_code for response in responses) == [202, 409]
    assert launches == [message.id]


@pytest.mark.asyncio
async def test_retry_rejects_text_terminal_deleted_missing_and_invalid_audio(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
):
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    text = Message(
        client_message_id="phase6-retry-text",
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        original_text="ordinary text",
    )
    test_db.add(text)
    await test_db.commit()
    pending, _ = await _seed_voice(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        key="phase6-retry-pending",
        status="pending",
    )
    completed, _ = await _seed_voice(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        key="phase6-retry-completed",
        status="completed",
        transcript="Complete transcript",
    )
    deleted, _ = await _seed_voice(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        key="phase6-retry-deleted",
        status="failed",
    )
    deleted.deleted_at = datetime.now(UTC)
    missing, _ = await _seed_voice(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        key="phase6-retry-missing",
        status="failed",
        with_attachment=False,
    )
    invalid, _ = await _seed_voice(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        key="phase6-retry-invalid",
        status="failed",
        content_type="application/pdf",
    )
    await test_db.commit()

    for message in (text, pending, completed, deleted, missing, invalid):
        response = await client.post(
            f"/api/v1/messages/{message.id}/transcription/retry",
            headers=test_user_headers,
        )
        assert response.status_code == 409
