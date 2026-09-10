"""Unit tests for in-memory WebSocket connection tracking."""

import json

import pytest

from src.services.messaging.connection_manager import ConnectionManager


class FakeWebSocket:
    """Small hashable WebSocket stand-in that records JSON frames."""

    def __init__(self, error: OSError | None = None) -> None:
        self.error = error
        self.sent_events: list[dict[str, object]] = []

    async def send_json(self, event: dict[str, object]) -> None:
        if self.error is not None:
            raise self.error
        self.sent_events.append(event)


@pytest.mark.asyncio
async def test_send_to_user_delivers_to_every_active_socket() -> None:
    manager = ConnectionManager()
    first_tab = FakeWebSocket()
    second_tab = FakeWebSocket()
    event = {"type": "message_received", "message": {"id": "message-1"}}

    manager.connect("user-1", first_tab)  # type: ignore[arg-type]
    manager.connect("user-1", second_tab)  # type: ignore[arg-type]

    await manager.send_to_user("user-1", event)

    assert first_tab.sent_events == [event]
    assert second_tab.sent_events == [event]


@pytest.mark.asyncio
async def test_offline_users_are_a_no_op_and_multi_user_fanout_is_deduplicated() -> None:
    manager = ConnectionManager()
    online_socket = FakeWebSocket()
    event = {"type": "message_received", "message": {"id": "message-1"}}

    manager.connect("online-user", online_socket)  # type: ignore[arg-type]

    await manager.send_to_user("offline-user", event)
    await manager.send_to_users(["online-user", "offline-user", "online-user"], event)

    assert online_socket.sent_events == [event]
    assert "offline-user" not in manager.connections


def test_disconnect_removes_only_the_closed_socket_for_a_user() -> None:
    manager = ConnectionManager()
    closed_tab = FakeWebSocket()
    remaining_tab = FakeWebSocket()

    manager.connect("user-1", closed_tab)  # type: ignore[arg-type]
    manager.connect("user-1", remaining_tab)  # type: ignore[arg-type]
    manager.disconnect("user-1", closed_tab)  # type: ignore[arg-type]

    assert manager.connections["user-1"] == {remaining_tab}  # type: ignore[comparison-overlap]


@pytest.mark.asyncio
async def test_failed_socket_is_removed_without_affecting_another_tab() -> None:
    manager = ConnectionManager()
    failed_tab = FakeWebSocket(error=OSError("connection closed"))
    healthy_tab = FakeWebSocket()
    event = {"type": "message_received", "message": {"id": "message-1"}}

    manager.connect("user-1", failed_tab)  # type: ignore[arg-type]
    manager.connect("user-1", healthy_tab)  # type: ignore[arg-type]

    await manager.send_to_user("user-1", event)

    assert manager.connections["user-1"] == {healthy_tab}  # type: ignore[comparison-overlap]
    assert healthy_tab.sent_events == [event]


@pytest.mark.asyncio
async def test_redis_backplane_publishes_one_deduplicated_fanout_envelope() -> None:
    class Redis:
        def __init__(self) -> None:
            self.published = []

        async def publish(self, channel, payload):
            self.published.append((channel, json.loads(payload)))

    manager = ConnectionManager()
    socket = FakeWebSocket()
    redis = Redis()
    manager._redis = redis
    manager.connect("user-1", socket)  # type: ignore[arg-type]
    event = {"type": "message_received", "message": {"id": "message-1"}}

    await manager.send_to_users(["user-1", "user-2", "user-1"], event)

    assert socket.sent_events == [event]
    assert len(redis.published) == 1
    envelope = redis.published[0][1]
    assert envelope["user_ids"] == ["user-1", "user-2"]
    assert envelope["event"] == event
