"""Tests for typing relay and online presence (docs/api/contract.md §3.5, §4.1, §4.2)."""

import pytest

from src.core.security import create_access_token
from tests.conftest import auth_headers_for_user


def _authenticate(socket, user) -> None:
    """Authenticate a test socket the same way the chat client does."""
    socket.send_json({"type": "auth", "token": create_access_token(subject=user.id)})
    assert socket.receive_json() == {"type": "auth_ok", "user_id": user.id}


@pytest.mark.asyncio
async def test_typing_reaches_other_members_but_not_the_person_typing(
    conversation_factory,
    test_user,
    test_user_two,
    ws_client,
):
    """The composer that produced the event must not echo it back."""
    conversation = await conversation_factory(test_user, [test_user_two])
    test_client, _ = ws_client

    with test_client.websocket_connect("/api/v1/ws") as typist:
        _authenticate(typist, test_user)
        with test_client.websocket_connect("/api/v1/ws") as watcher:
            _authenticate(watcher, test_user_two)

            typist.send_json({
                "type": "typing",
                "conversation_id": conversation.id,
                "is_typing": True,
            })
            relayed = watcher.receive_json()

            # Proving the typist got nothing back: the next frame it receives is
            # the answer to a different request, not its own typing event.
            typist.send_json({"type": "unknown_event"})
            next_for_typist = typist.receive_json()

    assert relayed == {
        "type": "typing",
        "conversation_id": conversation.id,
        "user_id": test_user.id,
        "is_typing": True,
    }
    assert next_for_typist["type"] == "error"


@pytest.mark.asyncio
async def test_stopping_typing_is_relayed_too(
    conversation_factory,
    test_user,
    test_user_two,
    ws_client,
):
    """Without the false notice the indicator would never switch off."""
    conversation = await conversation_factory(test_user, [test_user_two])
    test_client, _ = ws_client

    with test_client.websocket_connect("/api/v1/ws") as typist:
        _authenticate(typist, test_user)
        with test_client.websocket_connect("/api/v1/ws") as watcher:
            _authenticate(watcher, test_user_two)
            typist.send_json({
                "type": "typing",
                "conversation_id": conversation.id,
                "is_typing": False,
            })
            relayed = watcher.receive_json()

    assert relayed["is_typing"] is False


@pytest.mark.asyncio
async def test_typing_into_a_conversation_you_are_not_in_is_refused(
    conversation_factory,
    test_user,
    test_user_two,
    test_user_three,
    ws_client,
):
    """Otherwise any account could announce itself into a private conversation."""
    conversation = await conversation_factory(test_user, [test_user_two])
    test_client, _ = ws_client

    with test_client.websocket_connect("/api/v1/ws") as outsider:
        _authenticate(outsider, test_user_three)
        outsider.send_json({
            "type": "typing",
            "conversation_id": conversation.id,
            "is_typing": True,
        })
        error = outsider.receive_json()

    assert error["type"] == "error"
    assert error["code"] == "not_conversation_member"


@pytest.mark.asyncio
async def test_a_malformed_typing_event_is_reported_not_ignored(
    test_user,
    ws_client,
):
    """A client sending the wrong shape should be told, not silently dropped."""
    test_client, _ = ws_client

    with test_client.websocket_connect("/api/v1/ws") as socket:
        _authenticate(socket, test_user)
        socket.send_json({"type": "typing", "conversation_id": "abc"})
        error = socket.receive_json()

    assert error["type"] == "error"
    assert error["code"] == "invalid_event"


@pytest.mark.asyncio
async def test_conversation_list_reports_who_holds_a_live_socket(
    client,
    conversation_factory,
    test_user,
    test_user_headers,
    test_user_two,
):
    """Presence is read from the connection registry, so nobody connected is nobody online."""
    await conversation_factory(test_user, [test_user_two])

    listed = (await client.get("/api/v1/conversations", headers=test_user_headers)).json()[0]

    assert listed["online_member_ids"] == []


@pytest.mark.asyncio
async def test_online_members_are_reported_per_conversation(
    conversation_factory,
    test_user,
    test_user_two,
    ws_client,
):
    """A member with an open socket shows up for the other members of that conversation."""
    await conversation_factory(test_user, [test_user_two])
    test_client, _ = ws_client

    with test_client.websocket_connect("/api/v1/ws") as socket:
        _authenticate(socket, test_user_two)
        response = test_client.get(
            "/api/v1/conversations",
            headers=auth_headers_for_user(test_user),
        )

    assert response.status_code == 200
    assert response.json()[0]["online_member_ids"] == [test_user_two.id]
