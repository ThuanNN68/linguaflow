"""Tests for the WebSocket hook that schedules translations.

Only the hook is tested here — `schedule_translations` is monkeypatched, so no
agent runs and nothing reaches a network. What matters is *when* it is called.

Note for anyone extending this file: the existing WebSocket tests in
`test_websocket.py` do not patch the hook, and are safe only because the
background task opens the application database, where their temp-database
conversations do not exist, so it returns before reaching the agent. Do not rely
on that — patch the hook in any new test that sends a message.
"""

from __future__ import annotations

import pytest

from src.core.security import create_access_token


def authenticate(websocket, user):
    """Complete the in-band auth handshake and return the auth_ok payload."""
    websocket.send_json({"type": "auth", "token": create_access_token(user.id)})
    return websocket.receive_json()


@pytest.fixture
def recorded_schedule(monkeypatch):
    """Replace the real scheduler and record every call."""
    calls: list[dict] = []

    def recorder(*, message, publisher, **kwargs):
        calls.append({"message_id": message.id, "text": message.original_text})

    monkeypatch.setattr("src.api.websocket.schedule_translations", recorder)
    return calls


@pytest.fixture
def recorded_commitment_schedule(monkeypatch):
    """Record the detached B-10 scheduler without running an LLM in a socket test."""
    calls: list[dict] = []

    def recorder(*, message_id, conversation_id, sender_id, publisher):
        calls.append(
            {
                "message_id": message_id,
                "conversation_id": conversation_id,
                "sender_id": sender_id,
                "publisher": publisher,
            }
        )

    monkeypatch.setattr("src.api.websocket.schedule_commitment_detection", recorder)
    return calls


@pytest.mark.asyncio
async def test_schedules_a_translation_for_a_new_message(
    ws_client, test_user, test_user_two, conversation_factory, recorded_schedule
):
    client, _manager = ws_client
    conversation = await conversation_factory(test_user, [test_user, test_user_two])

    with client.websocket_connect("/api/v1/ws") as websocket:
        assert authenticate(websocket, test_user)["type"] == "auth_ok"
        websocket.send_json(
            {
                "type": "send_message",
                "client_message_id": "m-1",
                "conversation_id": conversation.id,
                "text": "Deploy xong chua anh?",
            }
        )
        assert websocket.receive_json()["type"] == "message_created"

    assert len(recorded_schedule) == 1
    assert recorded_schedule[0]["text"] == "Deploy xong chua anh?"


@pytest.mark.asyncio
async def test_new_message_schedules_detached_commitment_detection_after_delivery(
    ws_client,
    test_user,
    test_user_two,
    conversation_factory,
    recorded_schedule,
    recorded_commitment_schedule,
):
    client, _manager = ws_client
    conversation = await conversation_factory(test_user, [test_user, test_user_two])

    with client.websocket_connect("/api/v1/ws") as websocket:
        assert authenticate(websocket, test_user)["type"] == "auth_ok"
        websocket.send_json(
            {
                "type": "send_message",
                "client_message_id": "commitment-hook-1",
                "conversation_id": conversation.id,
                "text": "I'll send the report tomorrow.",
            }
        )
        # The acknowledgement is delivered without waiting for a detector.
        assert websocket.receive_json()["type"] == "message_created"

    assert len(recorded_schedule) == 1
    assert len(recorded_commitment_schedule) == 1
    call = recorded_commitment_schedule[0]
    assert call["conversation_id"] == conversation.id
    assert call["sender_id"] == test_user.id
    assert isinstance(call["message_id"], str)


@pytest.mark.asyncio
async def test_does_not_reschedule_an_idempotent_resend(
    ws_client, test_user, test_user_two, conversation_factory, recorded_schedule
):
    """A client retrying after a dropped socket must not pay for a second translation."""
    client, _manager = ws_client
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    payload = {
        "type": "send_message",
        "client_message_id": "m-repeat",
        "conversation_id": conversation.id,
        "text": "Chào bạn nhé",
    }

    with client.websocket_connect("/api/v1/ws") as websocket:
        authenticate(websocket, test_user)
        websocket.send_json(payload)
        websocket.receive_json()
        websocket.send_json(payload)
        websocket.receive_json()

    assert len(recorded_schedule) == 1


@pytest.mark.asyncio
async def test_does_not_schedule_when_the_message_is_rejected(
    ws_client, test_user, recorded_schedule
):
    """Nothing was persisted, so there is nothing to translate."""
    client, _manager = ws_client

    with client.websocket_connect("/api/v1/ws") as websocket:
        authenticate(websocket, test_user)
        websocket.send_json(
            {
                "type": "send_message",
                "client_message_id": "m-1",
                "conversation_id": "does-not-exist",
                "text": "Chào bạn nhé",
            }
        )
        assert websocket.receive_json()["type"] == "error"

    assert recorded_schedule == []
