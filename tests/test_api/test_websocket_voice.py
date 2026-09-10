"""WebSocket integration tests for the Phase 3 voice-message lifecycle."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from starlette.websockets import WebSocketDisconnect

from src.api import websocket as websocket_module
from src.core.security import create_access_token
from src.database.models import Attachment, Message
from tests.conftest import auth_headers_for_user


def _authenticate(socket, user) -> None:
    socket.send_json({"type": "auth", "token": create_access_token(subject=user.id)})
    assert socket.receive_json() == {"type": "auth_ok", "user_id": user.id}


def _send_voice(
    socket,
    *,
    conversation_id: str,
    client_message_id: str,
    attachment_id: str,
) -> None:
    socket.send_json(
        {
            "type": "send_voice_message",
            "client_message_id": client_message_id,
            "conversation_id": conversation_id,
            "attachment_id": attachment_id,
        }
    )


async def _upload_audio(client, headers, conversation_id: str, name: str) -> dict:
    response = await client.post(
        f"/api/v1/conversations/{conversation_id}/attachments",
        headers=headers,
        files={"file": (name, b"browser-audio-bytes", "audio/webm; codecs=opus")},
    )
    assert response.status_code == 201
    return response.json()


def _forbid_text_dependent_schedulers(monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("voice messages must not schedule Phase 4 text work")

    monkeypatch.setattr(websocket_module, "schedule_translations", forbidden)
    monkeypatch.setattr(websocket_module, "schedule_commitment_detection", forbidden)
    monkeypatch.setattr(websocket_module, "schedule_profile_inference", forbidden)
    monkeypatch.setattr(websocket_module, "schedule_message_embedding", forbidden)
    monkeypatch.setattr(websocket_module, "schedule_assistant_mention", forbidden)


@pytest.mark.asyncio
async def test_pending_voice_is_persisted_fanned_out_and_scheduled_exactly_once(
    client,
    test_db,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
    ws_client,
    monkeypatch,
):
    conversation = await conversation_factory(test_user, [test_user_two])
    uploaded = await _upload_audio(
        client,
        test_user_headers,
        conversation.id,
        "pending-voice.webm",
    )
    scheduled: list[dict] = []
    monkeypatch.setattr(
        websocket_module,
        "schedule_voice_transcription",
        lambda **kwargs: scheduled.append(kwargs),
    )
    _forbid_text_dependent_schedulers(monkeypatch)
    test_client, _ = ws_client

    with test_client.websocket_connect("/api/v1/ws") as sender:
        with test_client.websocket_connect("/api/v1/ws") as recipient:
            _authenticate(sender, test_user)
            _authenticate(recipient, test_user_two)
            _send_voice(
                sender,
                conversation_id=conversation.id,
                client_message_id="voice-ws-1",
                attachment_id=uploaded["id"],
            )
            acknowledgement = sender.receive_json()
            delivery = recipient.receive_json()

            _send_voice(
                sender,
                conversation_id=conversation.id,
                client_message_id="voice-ws-1",
                attachment_id=uploaded["id"],
            )
            retry_acknowledgement = sender.receive_json()

    assert acknowledgement["type"] == "message_created"
    assert acknowledgement["client_message_id"] == "voice-ws-1"
    assert acknowledgement["message"]["message_type"] == "voice"
    assert acknowledgement["message"]["transcription_status"] == "pending"
    assert acknowledgement["message"]["original_text"] == ""
    assert acknowledgement["message"]["sender_id"] == test_user.id
    assert acknowledgement["message"]["attachment"]["id"] == uploaded["id"]
    assert delivery == {
        "type": "message_received",
        "message": acknowledgement["message"],
    }
    assert retry_acknowledgement == acknowledgement
    assert len(scheduled) == 1
    assert scheduled[0]["message_id"] == acknowledgement["message"]["id"]
    assert scheduled[0]["conversation_id"] == conversation.id

    await test_db.rollback()
    stored = await test_db.get(Message, acknowledgement["message"]["id"])
    attachment = await test_db.get(Attachment, uploaded["id"])
    assert stored.transcription_status == "pending"
    assert attachment.message_id == stored.id
    assert (
        await test_db.scalar(select(func.count()).select_from(Message).where(Message.client_message_id == "voice-ws-1"))
        == 1
    )


@pytest.mark.asyncio
async def test_voice_client_message_id_conflict_is_explicit_and_does_not_launch_stt(
    client,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
    ws_client,
    monkeypatch,
):
    conversation = await conversation_factory(test_user, [test_user_two])
    first = await _upload_audio(client, test_user_headers, conversation.id, "conflict-one.webm")
    second = await _upload_audio(client, test_user_headers, conversation.id, "conflict-two.webm")
    scheduled = []
    monkeypatch.setattr(
        websocket_module,
        "schedule_voice_transcription",
        lambda **kwargs: scheduled.append(kwargs),
    )
    _forbid_text_dependent_schedulers(monkeypatch)
    test_client, _ = ws_client

    with test_client.websocket_connect("/api/v1/ws") as sender:
        _authenticate(sender, test_user)
        _send_voice(
            sender,
            conversation_id=conversation.id,
            client_message_id="voice-conflict-ws",
            attachment_id=first["id"],
        )
        sender.receive_json()
        _send_voice(
            sender,
            conversation_id=conversation.id,
            client_message_id="voice-conflict-ws",
            attachment_id=second["id"],
        )
        conflict = sender.receive_json()

    assert conflict["type"] == "error"
    assert conflict["code"] == "client_message_id_conflict"
    assert len(scheduled) == 1


@pytest.mark.asyncio
async def test_voice_send_requires_authentication(ws_client):
    test_client, manager = ws_client

    with test_client.websocket_connect("/api/v1/ws") as socket:
        _send_voice(
            socket,
            conversation_id="conversation-id",
            client_message_id="unauthenticated-voice",
            attachment_id="audio.webm",
        )
        error = socket.receive_json()
        assert error["code"] == "authentication_required"
        with pytest.raises(WebSocketDisconnect) as exc_info:
            socket.receive_json()
        assert exc_info.value.code == 4401

    assert manager.connections == {}


@pytest.mark.asyncio
async def test_outsider_cannot_send_voice_into_conversation(
    client,
    test_user,
    test_user_headers,
    test_user_two,
    test_user_three,
    conversation_factory,
    ws_client,
    monkeypatch,
):
    conversation = await conversation_factory(test_user, [test_user_two])
    uploaded = await _upload_audio(client, test_user_headers, conversation.id, "outsider.webm")
    scheduled = []
    monkeypatch.setattr(
        websocket_module,
        "schedule_voice_transcription",
        lambda **kwargs: scheduled.append(kwargs),
    )
    test_client, _ = ws_client

    with test_client.websocket_connect("/api/v1/ws") as outsider:
        _authenticate(outsider, test_user_three)
        _send_voice(
            outsider,
            conversation_id=conversation.id,
            client_message_id="outsider-voice",
            attachment_id=uploaded["id"],
        )
        error = outsider.receive_json()

    assert error["code"] == "not_conversation_member"
    assert scheduled == []


@pytest.mark.asyncio
async def test_foreign_and_non_audio_attachments_have_explicit_socket_errors(
    client,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
    ws_client,
    monkeypatch,
):
    conversation = await conversation_factory(test_user, [test_user_two])
    foreign = await _upload_audio(
        client,
        auth_headers_for_user(test_user_two),
        conversation.id,
        "foreign.webm",
    )
    document_response = await client.post(
        f"/api/v1/conversations/{conversation.id}/attachments",
        headers=test_user_headers,
        files={"file": ("document.pdf", b"%PDF", "application/pdf")},
    )
    assert document_response.status_code == 201
    document = document_response.json()
    monkeypatch.setattr(
        websocket_module,
        "schedule_voice_transcription",
        lambda **kwargs: None,
    )
    test_client, _ = ws_client

    with test_client.websocket_connect("/api/v1/ws") as sender:
        _authenticate(sender, test_user)
        _send_voice(
            sender,
            conversation_id=conversation.id,
            client_message_id="foreign-voice",
            attachment_id=foreign["id"],
        )
        foreign_error = sender.receive_json()
        _send_voice(
            sender,
            conversation_id=conversation.id,
            client_message_id="document-voice",
            attachment_id=document["id"],
        )
        document_error = sender.receive_json()

    assert foreign_error["code"] == "voice_attachment_not_owned"
    assert document_error["code"] == "voice_attachment_not_audio"


@pytest.mark.asyncio
async def test_malformed_voice_event_keeps_socket_usable_for_unchanged_text_path(
    test_user,
    test_user_two,
    conversation_factory,
    ws_client,
    monkeypatch,
):
    conversation = await conversation_factory(test_user, [test_user_two])
    monkeypatch.setattr(websocket_module, "schedule_translations", lambda **kwargs: None)
    monkeypatch.setattr(websocket_module, "schedule_commitment_detection", lambda **kwargs: None)
    monkeypatch.setattr(websocket_module, "schedule_profile_inference", lambda **kwargs: None)
    monkeypatch.setattr(websocket_module, "schedule_message_embedding", lambda **kwargs: None)
    test_client, _ = ws_client

    with test_client.websocket_connect("/api/v1/ws") as sender:
        _authenticate(sender, test_user)
        sender.send_json(
            {
                "type": "send_voice_message",
                "client_message_id": "malformed-voice",
                "conversation_id": conversation.id,
            }
        )
        error = sender.receive_json()
        sender.send_json(
            {
                "type": "send_message",
                "client_message_id": "text-after-voice-error",
                "conversation_id": conversation.id,
                "text": "Text still works",
            }
        )
        acknowledgement = sender.receive_json()

    assert error["code"] == "invalid_event"
    assert acknowledgement["type"] == "message_created"
    assert acknowledgement["message"]["message_type"] == "text"
    assert acknowledgement["message"]["transcription_status"] is None
    assert acknowledgement["message"]["original_text"] == "Text still works"
