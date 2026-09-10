"""Contract tests for the direct-call signalling state machine."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

import src.api.chat as routes
from src.core.security import create_access_token
from src.database.models import CallSession
from tests.conftest import auth_headers_for_user


class FakeRTCProvider:
    """In-memory RTC adapter: tests must never create a real Daily room."""

    name = "daily"

    def __init__(self) -> None:
        self.created_rooms: list[str] = []
        self.closed_rooms: list[str] = []

    async def create_room(self, room_name: str) -> str:
        self.created_rooms.append(room_name)
        return f"https://{room_name}.example.daily.co"

    async def create_join_token(self, room_name: str, user_id: str, *, owner: bool) -> str:
        return f"join-{room_name}-{user_id}-{'owner' if owner else 'guest'}"

    async def close_room(self, room_name: str) -> None:
        self.closed_rooms.append(room_name)

    def room_url(self, room_name: str) -> str:
        return f"https://{room_name}.example.daily.co"


def _authenticate(socket, user) -> None:
    socket.send_json({"type": "auth", "token": create_access_token(subject=user.id)})
    assert socket.receive_json() == {"type": "auth_ok", "user_id": user.id}


@pytest.mark.asyncio
async def test_direct_call_rings_then_issues_private_tokens_after_accept(
    client,
    conversation_factory,
    test_user,
    test_user_two,
    monkeypatch,
) -> None:
    """Only the callee can accept, and neither token is leaked while ringing."""
    provider = FakeRTCProvider()
    monkeypatch.setattr(routes, "get_rtc_provider", lambda: provider)
    conversation = await conversation_factory(test_user, [test_user_two])

    started = await client.post(
        f"/api/v1/conversations/{conversation.id}/calls",
        headers=auth_headers_for_user(test_user),
        json={"call_type": "video"},
    )
    assert started.status_code == 201
    ringing = started.json()
    assert ringing["status"] == "ringing"
    assert ringing["room_url"] is None
    assert ringing["join_token"] is None

    # The caller cannot join before the recipient has explicitly accepted.
    premature_join = await client.get(
        f"/api/v1/calls/{ringing['call_id']}/join",
        headers=auth_headers_for_user(test_user),
    )
    assert premature_join.status_code == 409

    accepted = await client.post(
        f"/api/v1/calls/{ringing['call_id']}/accept",
        headers=auth_headers_for_user(test_user_two),
    )
    assert accepted.status_code == 200
    callee_join = accepted.json()
    assert callee_join["status"] == "accepted"
    assert callee_join["room_url"].startswith("https://linguaflow-")
    assert callee_join["join_token"].endswith("-guest")

    caller_join = await client.get(
        f"/api/v1/calls/{ringing['call_id']}/join",
        headers=auth_headers_for_user(test_user),
    )
    assert caller_join.status_code == 200
    assert caller_join.json()["join_token"].endswith("-owner")

    ended = await client.post(
        f"/api/v1/calls/{ringing['call_id']}/end",
        headers=auth_headers_for_user(test_user),
    )
    assert ended.status_code == 200
    assert ended.json()["status"] == "ended"
    assert len(provider.closed_rooms) == 1


@pytest.mark.asyncio
async def test_only_callee_can_reject_a_ringing_call(
    client,
    conversation_factory,
    test_user,
    test_user_two,
    monkeypatch,
) -> None:
    provider = FakeRTCProvider()
    monkeypatch.setattr(routes, "get_rtc_provider", lambda: provider)
    conversation = await conversation_factory(test_user, [test_user_two])

    started = await client.post(
        f"/api/v1/conversations/{conversation.id}/calls",
        headers=auth_headers_for_user(test_user),
        json={"call_type": "voice"},
    )
    call_id = started.json()["call_id"]

    caller_reject = await client.post(
        f"/api/v1/calls/{call_id}/reject",
        headers=auth_headers_for_user(test_user),
    )
    assert caller_reject.status_code == 409

    rejected = await client.post(
        f"/api/v1/calls/{call_id}/reject",
        headers=auth_headers_for_user(test_user_two),
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert len(provider.closed_rooms) == 1


@pytest.mark.asyncio
async def test_call_state_is_delivered_to_both_online_websocket_participants(
    conversation_factory,
    test_user,
    test_user_two,
    ws_client,
    monkeypatch,
) -> None:
    """The callee gets an incoming prompt and the caller gets the accept event."""
    provider = FakeRTCProvider()
    monkeypatch.setattr(routes, "get_rtc_provider", lambda: provider)
    conversation = await conversation_factory(test_user, [test_user_two])
    test_client, _ = ws_client

    with test_client.websocket_connect("/api/v1/ws") as caller_socket:
        with test_client.websocket_connect("/api/v1/ws") as callee_socket:
            _authenticate(caller_socket, test_user)
            _authenticate(callee_socket, test_user_two)

            started = test_client.post(
                f"/api/v1/conversations/{conversation.id}/calls",
                headers=auth_headers_for_user(test_user),
                json={"call_type": "voice"},
            )
            assert started.status_code == 201
            call_id = started.json()["call_id"]
            incoming = callee_socket.receive_json()
            assert incoming == {
                "type": "call_incoming",
                "call_id": call_id,
                "conversation_id": conversation.id,
                "caller_id": test_user.id,
                "callee_id": test_user_two.id,
                "call_type": "voice",
                "status": "ringing",
            }

            accepted = test_client.post(
                f"/api/v1/calls/{call_id}/accept",
                headers=auth_headers_for_user(test_user_two),
            )
            assert accepted.status_code == 200
            caller_notification = caller_socket.receive_json()
            assert caller_notification["type"] == "call_accepted"
            assert caller_notification["call_id"] == call_id
            # Provider credentials are intentionally not sent on WebSocket.
            assert "join_token" not in caller_notification


@pytest.mark.asyncio
async def test_stale_ringing_call_expires_to_missed_and_permits_new_call(
    client,
    test_db,
    conversation_factory,
    test_user,
    test_user_two,
    monkeypatch,
) -> None:
    """Stale ringing calls transition to missed and unblock starting another call."""
    provider = FakeRTCProvider()
    monkeypatch.setattr(routes, "get_rtc_provider", lambda: provider)
    conversation = await conversation_factory(test_user, [test_user_two])

    started = await client.post(
        f"/api/v1/conversations/{conversation.id}/calls",
        headers=auth_headers_for_user(test_user),
        json={"call_type": "voice"},
    )
    assert started.status_code == 201
    call_id = started.json()["call_id"]

    # Artificially age the call past the ring timeout
    call = await test_db.get(CallSession, call_id)
    assert call is not None
    call.created_at = datetime.now(UTC) - timedelta(seconds=60)
    await test_db.commit()

    # Starting a new call should lazily expire the stale one and succeed
    new_call = await client.post(
        f"/api/v1/conversations/{conversation.id}/calls",
        headers=auth_headers_for_user(test_user),
        json={"call_type": "video"},
    )
    assert new_call.status_code == 201
    assert new_call.json()["call_id"] != call_id

    # Verify old call is now missed
    await test_db.refresh(call)
    assert call.status == "missed"
    assert call.ended_at is not None


@pytest.mark.asyncio
async def test_expired_call_cannot_be_accepted(
    client,
    test_db,
    conversation_factory,
    test_user,
    test_user_two,
    monkeypatch,
) -> None:
    """Callee cannot accept a ringing call that has passed ring timeout."""
    provider = FakeRTCProvider()
    monkeypatch.setattr(routes, "get_rtc_provider", lambda: provider)
    conversation = await conversation_factory(test_user, [test_user_two])

    started = await client.post(
        f"/api/v1/conversations/{conversation.id}/calls",
        headers=auth_headers_for_user(test_user),
        json={"call_type": "voice"},
    )
    call_id = started.json()["call_id"]

    # Age the call past timeout
    call = await test_db.get(CallSession, call_id)
    assert call is not None
    call.created_at = datetime.now(UTC) - timedelta(seconds=60)
    await test_db.commit()

    accepted = await client.post(
        f"/api/v1/calls/{call_id}/accept",
        headers=auth_headers_for_user(test_user_two),
    )
    assert accepted.status_code == 409
    assert "expired" in accepted.json()["detail"].lower()

    await test_db.refresh(call)
    assert call.status == "missed"


@pytest.mark.asyncio
async def test_user_cannot_participate_in_multiple_simultaneous_calls(
    client,
    conversation_factory,
    test_user,
    test_user_two,
    test_user_three,
    monkeypatch,
) -> None:
    """A user in an active/ringing call cannot participate in another across any conversation."""
    provider = FakeRTCProvider()
    monkeypatch.setattr(routes, "get_rtc_provider", lambda: provider)

    conv1 = await conversation_factory(test_user, [test_user_two])
    conv2 = await conversation_factory(test_user_three, [test_user])
    conv3 = await conversation_factory(test_user, [test_user_three])

    # test_user starts call with test_user_two
    c1 = await client.post(
        f"/api/v1/conversations/{conv1.id}/calls",
        headers=auth_headers_for_user(test_user),
        json={"call_type": "voice"},
    )
    assert c1.status_code == 201
    call1_id = c1.json()["call_id"]

    # test_user cannot start another outgoing call in conv3 while ringing
    blocked_outgoing = await client.post(
        f"/api/v1/conversations/{conv3.id}/calls",
        headers=auth_headers_for_user(test_user),
        json={"call_type": "video"},
    )
    assert blocked_outgoing.status_code == 409

    # test_user_three cannot call test_user while test_user has an active ringing call
    blocked_incoming = await client.post(
        f"/api/v1/conversations/{conv2.id}/calls",
        headers=auth_headers_for_user(test_user_three),
        json={"call_type": "voice"},
    )
    assert blocked_incoming.status_code == 409

    # Accept first call
    accept_res = await client.post(
        f"/api/v1/calls/{call1_id}/accept",
        headers=auth_headers_for_user(test_user_two),
    )
    assert accept_res.status_code == 200

    # test_user_three still cannot call test_user while in accepted call
    blocked_while_accepted = await client.post(
        f"/api/v1/conversations/{conv2.id}/calls",
        headers=auth_headers_for_user(test_user_three),
        json={"call_type": "voice"},
    )
    assert blocked_while_accepted.status_code == 409

    # End the active call
    end_res = await client.post(
        f"/api/v1/calls/{call1_id}/end",
        headers=auth_headers_for_user(test_user),
    )
    assert end_res.status_code == 200

    # After first call ends, test_user_three can successfully call test_user
    allowed = await client.post(
        f"/api/v1/conversations/{conv2.id}/calls",
        headers=auth_headers_for_user(test_user_three),
        json={"call_type": "voice"},
    )
    assert allowed.status_code == 201


@pytest.mark.asyncio
async def test_rtc_provider_unavailable_returns_503(
    client,
    conversation_factory,
    test_user,
    test_user_two,
    monkeypatch,
) -> None:
    """When RTC provider is disabled or unavailable, endpoints return HTTP 503, not 500."""
    from src.services.messaging.rtc import RTCProviderUnavailableError

    def _raise_unavailable():
        raise RTCProviderUnavailableError("RTC calling is disabled")

    monkeypatch.setattr(routes, "get_rtc_provider", _raise_unavailable)
    conversation = await conversation_factory(test_user, [test_user_two])

    res = await client.post(
        f"/api/v1/conversations/{conversation.id}/calls",
        headers=auth_headers_for_user(test_user),
        json={"call_type": "voice"},
    )
    assert res.status_code == 503
    assert res.json()["detail"] == "RTC calling is disabled"
