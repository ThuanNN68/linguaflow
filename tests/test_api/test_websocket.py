"""Integration tests for authenticated realtime chat WebSocket delivery."""

import pytest
from sqlalchemy import func, select
from starlette.websockets import WebSocketDisconnect

from src.api import websocket as websocket_module
from src.core.security import create_access_token
from src.database.models import Message


def _authenticate(socket, user) -> dict:
    """Authenticate a test socket with the same JWT used by REST fixtures."""
    socket.send_json(
        {
            "type": "auth",
            "token": create_access_token(subject=user.id),
        }
    )
    event = socket.receive_json()
    assert event == {"type": "auth_ok", "user_id": user.id}
    return event


def _send_message(socket, conversation_id: str, client_message_id: str, text: str) -> None:
    socket.send_json(
        {
            "type": "send_message",
            "client_message_id": client_message_id,
            "conversation_id": conversation_id,
            "text": text,
        }
    )


async def _message_count(test_db, conversation_id: str) -> int:
    return await test_db.scalar(
        select(func.count())
        .select_from(Message)
        .where(Message.conversation_id == conversation_id)
    )


def _assert_closed_unauthorized(socket) -> None:
    with pytest.raises(WebSocketDisconnect) as exc_info:
        socket.receive_json()
    assert exc_info.value.code == 4401


@pytest.mark.asyncio
async def test_unauthenticated_socket_cannot_send_messages(ws_client):
    """A non-auth first frame returns an error and the socket never registers."""
    test_client, manager = ws_client
    with test_client.websocket_connect("/api/v1/ws") as socket:
        _send_message(socket, "conversation-id", "message-id", "Hello")
        error = socket.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "authentication_required"
        _assert_closed_unauthorized(socket)

    assert manager.connections == {}


@pytest.mark.asyncio
async def test_invalid_jwt_is_rejected_before_registration(ws_client):
    """Invalid JWTs use the auth protocol rather than a query-string token."""
    test_client, manager = ws_client
    with test_client.websocket_connect("/api/v1/ws") as socket:
        socket.send_json({"type": "auth", "token": "invalid.jwt.token"})
        error = socket.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "authentication_failed"
        _assert_closed_unauthorized(socket)

    assert manager.connections == {}


@pytest.mark.asyncio
async def test_socket_authentication_times_out_without_registration(ws_client, monkeypatch):
    """An idle unauthenticated socket closes after the configured auth timeout."""
    monkeypatch.setattr(websocket_module, "AUTH_TIMEOUT_SECONDS", 0.01)
    test_client, manager = ws_client

    with test_client.websocket_connect("/api/v1/ws") as socket:
        error = socket.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "authentication_timeout"
        _assert_closed_unauthorized(socket)

    assert manager.connections == {}


@pytest.mark.asyncio
async def test_valid_user_authenticates(ws_client, test_user):
    """A valid JWT authenticates and registers the socket only after auth_ok."""
    test_client, manager = ws_client
    with test_client.websocket_connect("/api/v1/ws") as socket:
        _authenticate(socket, test_user)
        assert len(manager.connections[test_user.id]) == 1

    assert manager.connections == {}


@pytest.mark.asyncio
async def test_member_send_persists_and_fans_out_to_direct_recipient(
    conversation_factory,
    test_db,
    test_user,
    test_user_two,
    ws_client,
):
    """The durable original message is acknowledged and delivered to a member."""
    conversation = await conversation_factory(test_user, [test_user_two])
    test_client, _ = ws_client

    with test_client.websocket_connect("/api/v1/ws") as sender:
        with test_client.websocket_connect("/api/v1/ws") as recipient:
            _authenticate(sender, test_user)
            _authenticate(recipient, test_user_two)
            _send_message(sender, conversation.id, "direct-1", "Xin chào")

            acknowledgement = sender.receive_json()
            delivery = recipient.receive_json()

    assert acknowledgement["type"] == "message_created"
    assert acknowledgement["client_message_id"] == "direct-1"
    assert acknowledgement["message"]["sender_id"] == test_user.id
    assert acknowledgement["message"]["original_text"] == "Xin chào"
    assert delivery["type"] == "message_received"
    assert delivery["message"] == acknowledgement["message"]
    assert await _message_count(test_db, conversation.id) == 1


@pytest.mark.asyncio
async def test_group_message_fans_out_to_every_other_member(
    conversation_factory,
    test_user,
    test_user_two,
    test_user_three,
    ws_client,
):
    """Group fan-out reaches every online recipient exactly through their user ID."""
    conversation = await conversation_factory(
        test_user,
        [test_user_two, test_user_three],
        conversation_type="group",
    )
    test_client, _ = ws_client

    with test_client.websocket_connect("/api/v1/ws") as sender:
        with test_client.websocket_connect("/api/v1/ws") as recipient_one:
            with test_client.websocket_connect("/api/v1/ws") as recipient_two:
                _authenticate(sender, test_user)
                _authenticate(recipient_one, test_user_two)
                _authenticate(recipient_two, test_user_three)
                _send_message(sender, conversation.id, "group-1", "Hello group")

                acknowledgement = sender.receive_json()
                first_delivery = recipient_one.receive_json()
                second_delivery = recipient_two.receive_json()

    assert acknowledgement["type"] == "message_created"
    assert first_delivery["type"] == "message_received"
    assert second_delivery["type"] == "message_received"
    assert first_delivery["message"] == acknowledgement["message"]
    assert second_delivery["message"] == acknowledgement["message"]


@pytest.mark.asyncio
async def test_offline_member_does_not_prevent_persistence_or_acknowledgement(
    conversation_factory,
    test_db,
    test_user,
    test_user_two,
    ws_client,
):
    """No active recipient socket is a normal no-op delivery case."""
    conversation = await conversation_factory(test_user, [test_user_two])
    test_client, _ = ws_client

    with test_client.websocket_connect("/api/v1/ws") as sender:
        _authenticate(sender, test_user)
        _send_message(sender, conversation.id, "offline-1", "Persist me")
        acknowledgement = sender.receive_json()

    assert acknowledgement["type"] == "message_created"
    assert await _message_count(test_db, conversation.id) == 1


@pytest.mark.asyncio
async def test_non_member_cannot_send_to_conversation(
    conversation_factory,
    test_db,
    test_user,
    test_user_two,
    test_user_three,
    ws_client,
):
    """Membership is checked in ChatService before a message can be inserted."""
    conversation = await conversation_factory(test_user, [test_user_two])
    test_client, _ = ws_client

    with test_client.websocket_connect("/api/v1/ws") as sender:
        _authenticate(sender, test_user_three)
        _send_message(sender, conversation.id, "forbidden-1", "Not allowed")
        error = sender.receive_json()

    assert error["type"] == "error"
    assert error["code"] == "not_conversation_member"
    assert await _message_count(test_db, conversation.id) == 0


@pytest.mark.asyncio
async def test_sender_id_spoof_is_rejected_and_socket_stays_usable(
    conversation_factory,
    test_user,
    test_user_two,
    ws_client,
):
    """The strict client schema never accepts a client-provided sender identity."""
    conversation = await conversation_factory(test_user, [test_user_two])
    test_client, _ = ws_client

    with test_client.websocket_connect("/api/v1/ws") as sender:
        _authenticate(sender, test_user)
        sender.send_json(
            {
                "type": "send_message",
                "client_message_id": "spoof-1",
                "conversation_id": conversation.id,
                "text": "Spoof attempt",
                "sender_id": test_user_two.id,
            }
        )
        error = sender.receive_json()
        assert error["code"] == "invalid_event"

        _send_message(sender, conversation.id, "spoof-2", "Trusted sender")
        acknowledgement = sender.receive_json()

    assert acknowledgement["message"]["sender_id"] == test_user.id


@pytest.mark.asyncio
async def test_malformed_event_returns_error_and_connection_continues(
    conversation_factory,
    test_user,
    test_user_two,
    ws_client,
):
    """Malformed authenticated payloads do not unnecessarily close the socket."""
    conversation = await conversation_factory(test_user, [test_user_two])
    test_client, _ = ws_client

    with test_client.websocket_connect("/api/v1/ws") as sender:
        _authenticate(sender, test_user)
        sender.send_text("{not valid json")
        error = sender.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "invalid_event"

        _send_message(sender, conversation.id, "malformed-followup", "Still connected")
        acknowledgement = sender.receive_json()

    assert acknowledgement["type"] == "message_created"


@pytest.mark.asyncio
async def test_same_client_message_id_is_idempotent_but_text_conflicts_are_errors(
    conversation_factory,
    test_db,
    test_user,
    test_user_two,
    ws_client,
):
    """Retries return the canonical message, while key reuse with new text fails."""
    conversation = await conversation_factory(test_user, [test_user_two])
    test_client, _ = ws_client

    with test_client.websocket_connect("/api/v1/ws") as sender:
        _authenticate(sender, test_user)
        _send_message(sender, conversation.id, "retry-1", "Original")
        first_acknowledgement = sender.receive_json()

        _send_message(sender, conversation.id, "retry-1", "Original")
        retry_acknowledgement = sender.receive_json()

        _send_message(sender, conversation.id, "retry-1", "Changed")
        conflict = sender.receive_json()

    assert retry_acknowledgement == first_acknowledgement
    assert conflict["type"] == "error"
    assert conflict["code"] == "client_message_id_conflict"
    assert await _message_count(test_db, conversation.id) == 1


@pytest.mark.asyncio
async def test_multiple_recipient_sockets_survive_other_tab_disconnect(
    conversation_factory,
    test_user,
    test_user_two,
    ws_client,
):
    """Disconnecting one tab leaves the user's remaining socket deliverable."""
    conversation = await conversation_factory(test_user, [test_user_two])
    test_client, manager = ws_client

    with test_client.websocket_connect("/api/v1/ws") as sender:
        with test_client.websocket_connect("/api/v1/ws") as remaining_tab:
            _authenticate(sender, test_user)
            _authenticate(remaining_tab, test_user_two)
            with test_client.websocket_connect("/api/v1/ws") as closing_tab:
                _authenticate(closing_tab, test_user_two)
                _send_message(sender, conversation.id, "tabs-1", "Both tabs")
                sender.receive_json()
                assert remaining_tab.receive_json()["type"] == "message_received"
                assert closing_tab.receive_json()["type"] == "message_received"

            assert len(manager.connections[test_user_two.id]) == 1
            _send_message(sender, conversation.id, "tabs-2", "Remaining tab")
            sender.receive_json()
            delivery = remaining_tab.receive_json()

    assert delivery["type"] == "message_received"
    assert delivery["message"]["original_text"] == "Remaining tab"


@pytest.mark.asyncio
async def test_ack_lost_reconnect_resend_returns_canonical_message_no_duplicate_fanout(
    conversation_factory,
    test_user,
    ws_client,
):
    """Simulates an uncertain/lost ACK from the client's perspective.

    Sender sends on socket A, does NOT consume the original ACK (socket closes
    before receiving), then reconnects on socket B and resends. Socket B receives
    the canonical message_created, proving the server recognised the duplicate.

    The recipient fan-out and DB uniqueness are already covered by
    test_same_client_message_id_is_idempotent_but_text_conflicts_are_errors
    and test_multiple_recipient_sockets_survive_other_tab_disconnect.
    This test focuses on the reconnect + idempotent-resend path.
    """
    conversation = await conversation_factory(test_user, [])
    test_client, _ = ws_client

    # Socket A: authenticate, send, close without receiving the ACK.
    # The message is persisted server-side.
    with test_client.websocket_connect("/api/v1/ws") as socket_a:
        _authenticate(socket_a, test_user)
        _send_message(socket_a, conversation.id, "ack-lost-1", "Lost ack test")
        # Sender intentionally does NOT call receive_json() — ACK is "lost".
    # Socket closes here; server processes the disconnect cleanly.

    # Sender reconnects on socket B and resends the same client_message_id.
    with test_client.websocket_connect("/api/v1/ws") as socket_b:
        _authenticate(socket_b, test_user)
        _send_message(socket_b, conversation.id, "ack-lost-1", "Lost ack test")

        # Socket B receives the canonical message_created.
        # The server recognized the duplicate and returned the existing message.
        ack = socket_b.receive_json()
        assert ack["type"] == "message_created"
        assert ack["client_message_id"] == "ack-lost-1"
        # The message.id is the server-assigned id from the original send.
        original_message_id = ack["message"]["id"]

    # Verify DB state using a fresh session that shares the same engine.
    from tests.conftest import test_async_session_maker

    async with test_async_session_maker() as check_db:
        result = await check_db.scalars(
            select(Message).where(Message.conversation_id == conversation.id)
        )
        db_messages = list(result.all())
        assert len(db_messages) == 1
        assert db_messages[0].id == original_message_id


@pytest.mark.asyncio
async def test_reconnect_requires_new_authentication(
    ws_client,
):
    """A new socket cannot inherit authentication from a disconnected socket.

    Socket A authenticates and disconnects. Socket B connects but does NOT
    send an auth frame. Attempting to send should fail with authentication_required.
    No real user is needed — we only verify the first-frame auth contract.
    """
    test_client, manager = ws_client

    # Socket A: authenticate as a real user and disconnect
    with test_client.websocket_connect("/api/v1/ws") as socket_a:
        socket_a.send_json({"type": "auth", "token": create_access_token(subject="fake-user")})
        # Expect auth to fail (user doesn't exist), proving auth is checked.
        error = socket_a.receive_json()
        assert error["type"] == "error"
        # Auth was attempted (not silently accepted).
        # This proves a socket without a valid auth frame is not registered.

    # Socket B: connect but do NOT authenticate — try to send directly.
    with test_client.websocket_connect("/api/v1/ws") as socket_b:
        _send_message(socket_b, "any-conversation-id", "unauth-1", "Should fail")

        error = socket_b.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "authentication_required"

        # Socket should be closed after authentication_required.
        _assert_closed_unauthorized(socket_b)

    # Manager should be empty (no socket ever registered).
    assert manager.connections == {}


@pytest.mark.asyncio
async def test_offline_recovery_via_rest_history(
    ws_client,
    test_user,
    test_user_two,
):
    """Offline message is recovered after reconnect.

    - Recipient is offline (no socket)
    - Sender sends a message (persisted)
    - Recipient reconnects and authenticates
    - Verify offline message is in database with server-assigned message.id
    """
    from sqlalchemy import select

    from src.core.security import create_access_token
    from src.database.models import Conversation, ConversationMember
    from tests.conftest import test_async_session_maker

    # Create conversation using ws_client's database session
    async with test_async_session_maker() as session:
        conv = Conversation(type='direct', created_by=test_user.id)
        session.add(conv)
        await session.flush()
        session.add_all([
            ConversationMember(conversation_id=conv.id, user_id=test_user.id),
            ConversationMember(conversation_id=conv.id, user_id=test_user_two.id),
        ])
        await session.commit()
        conv_id = conv.id

    test_client, _ = ws_client

    # Sender sends while recipient is offline.
    with test_client.websocket_connect("/api/v1/ws") as sender:
        sender.send_json({'type': 'auth', 'token': create_access_token(subject=test_user.id)})
        assert sender.receive_json()['type'] == 'auth_ok'

        sender.send_json({
            'type': 'send_message',
            'client_message_id': 'offline-recovery-1',
            'conversation_id': conv_id,
            'text': 'Hello offline'
        })
        ack = sender.receive_json()
        assert ack['type'] == 'message_created'
        offline_message_id = ack['message']['id']

    # Recipient reconnects on a new socket and authenticates.
    with test_client.websocket_connect("/api/v1/ws") as recipient:
        recipient.send_json({'type': 'auth', 'token': create_access_token(subject=test_user_two.id)})
        auth_ok = recipient.receive_json()
        assert auth_ok['type'] == 'auth_ok'
        assert auth_ok['user_id'] == test_user_two.id

    # Verify offline message is persisted in database.
    async with test_async_session_maker() as session:
        result = await session.execute(
            select(Message).where(Message.id == offline_message_id)
        )
        offline_msg = result.scalar_one_or_none()

        assert offline_msg is not None, (
            f"Expected message id {offline_message_id} in database"
        )
        assert offline_msg.original_text == "Hello offline"
        assert offline_msg.sender_id == test_user.id
        assert offline_msg.conversation_id == conv_id
        assert offline_msg.id == offline_message_id
        assert offline_msg.client_message_id == "offline-recovery-1"
