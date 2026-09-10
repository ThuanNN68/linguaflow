"""Phase 1 contracts for representing voice messages without running STT."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.schemas.chat import (
    MessageResponse,
    RealtimeMessage,
    SendMessageEvent,
    SendVoiceMessageEvent,
    VoiceTranscriptionCompletedEvent,
    VoiceTranscriptionFailedEvent,
)

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


def _history_payload(**changes):
    payload = {
        "id": "message-1",
        "client_message_id": "client-1",
        "conversation_id": "conversation-1",
        "sender_id": "sender-1",
        "original_text": "Hello",
        "source_language": "en",
        "created_at": NOW,
    }
    payload.update(changes)
    return payload


def _realtime_payload(**changes):
    payload = {
        "id": "message-1",
        "conversation_id": "conversation-1",
        "sender_id": "sender-1",
        "original_text": "Hello",
        "created_at": NOW,
    }
    payload.update(changes)
    return payload


def test_text_message_schema_defaults_are_backward_compatible() -> None:
    history = MessageResponse(**_history_payload())
    realtime = RealtimeMessage(**_realtime_payload())

    assert history.message_type == "text"
    assert history.transcription_status is None
    assert realtime.message_type == "text"
    assert realtime.transcription_status is None
    assert history.original_text == "Hello"
    assert realtime.original_text == "Hello"


@pytest.mark.parametrize(
    ("status", "original_text"),
    [
        ("pending", ""),
        ("completed", "Đây là toàn bộ nội dung tin nhắn thoại."),
        ("failed", ""),
    ],
)
def test_voice_lifecycle_states_serialize_in_history_and_realtime(status: str, original_text: str) -> None:
    history = MessageResponse(
        **_history_payload(
            message_type="voice",
            transcription_status=status,
            original_text=original_text,
        )
    )
    realtime = RealtimeMessage(
        **_realtime_payload(
            message_type="voice",
            transcription_status=status,
            original_text=original_text,
        )
    )

    for serialized in (
        history.model_dump(mode="json"),
        realtime.model_dump(mode="json"),
    ):
        assert serialized["message_type"] == "voice"
        assert serialized["transcription_status"] == status
        assert serialized["original_text"] == original_text


@pytest.mark.parametrize(
    ("message_type", "status", "original_text"),
    [
        ("text", "pending", "Hello"),
        ("voice", None, ""),
        ("voice", "pending", "Voice message"),
        ("voice", "completed", ""),
        ("voice", "completed", "   "),
        ("voice", "failed", "Transcription unavailable"),
    ],
)
def test_message_schemas_reject_impossible_lifecycle_states(
    message_type: str, status: str | None, original_text: str
) -> None:
    for schema, payload in (
        (MessageResponse, _history_payload()),
        (RealtimeMessage, _realtime_payload()),
    ):
        with pytest.raises(ValidationError):
            schema(
                **{
                    **payload,
                    "message_type": message_type,
                    "transcription_status": status,
                    "original_text": original_text,
                }
            )


def test_deleted_completed_voice_response_can_redact_its_transcript() -> None:
    response = MessageResponse(
        **_history_payload(
            message_type="voice",
            transcription_status="completed",
            original_text="",
            deleted_at=NOW,
        )
    )

    assert response.original_text == ""
    assert response.transcription_status == "completed"


def test_send_voice_contract_requires_an_attachment_and_forbids_text() -> None:
    payload = {
        "type": "send_voice_message",
        "client_message_id": "voice-client-1",
        "conversation_id": "conversation-1",
        "attachment_id": "recording.webm",
        "reply_to_message_id": None,
    }

    event = SendVoiceMessageEvent.model_validate(payload)
    assert event.attachment_id == "recording.webm"
    assert "text" not in event.model_dump()

    with pytest.raises(ValidationError):
        SendVoiceMessageEvent.model_validate({key: value for key, value in payload.items() if key != "attachment_id"})
    with pytest.raises(ValidationError):
        SendVoiceMessageEvent.model_validate({**payload, "attachment_id": "   "})
    with pytest.raises(ValidationError):
        SendVoiceMessageEvent.model_validate({**payload, "text": "fake transcript"})
    with pytest.raises(ValidationError):
        SendVoiceMessageEvent.model_validate({**payload, "sender_id": "forged"})


@pytest.mark.parametrize("text", [None, "", "   "])
def test_existing_send_message_contract_still_requires_non_blank_text(
    text: str | None,
) -> None:
    payload = {
        "type": "send_message",
        "client_message_id": "text-client-1",
        "conversation_id": "conversation-1",
    }
    if text is not None:
        payload["text"] = text

    with pytest.raises(ValidationError):
        SendMessageEvent.model_validate(payload)


def test_transcription_completed_event_preserves_the_full_transcript() -> None:
    transcript = "Dòng một có mã API-217.\nDòng hai giữ nguyên số 0123456789."
    event = VoiceTranscriptionCompletedEvent(
        message_id="message-1",
        conversation_id="conversation-1",
        original_text=transcript,
        source_language="vi",
    )

    assert event.model_dump(mode="json") == {
        "type": "voice_transcription_completed",
        "message_id": "message-1",
        "conversation_id": "conversation-1",
        "original_text": transcript,
        "source_language": "vi",
        "transcription_status": "completed",
    }

    with pytest.raises(ValidationError):
        VoiceTranscriptionCompletedEvent(
            message_id="message-1",
            conversation_id="conversation-1",
            original_text="   ",
            source_language="vi",
        )


def test_transcription_failed_event_contains_no_placeholder_text() -> None:
    event = VoiceTranscriptionFailedEvent(
        message_id="message-1",
        conversation_id="conversation-1",
        retryable=True,
    )

    assert event.model_dump(mode="json") == {
        "type": "voice_transcription_failed",
        "message_id": "message-1",
        "conversation_id": "conversation-1",
        "transcription_status": "failed",
        "retryable": True,
    }
