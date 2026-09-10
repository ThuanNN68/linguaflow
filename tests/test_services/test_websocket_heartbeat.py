"""Application heartbeat closes sockets that stop acknowledging pings."""

import asyncio

import pytest

from src.api import websocket as websocket_module


class HeartbeatSocket:
    def __init__(self, pong: asyncio.Event | None = None) -> None:
        self.pong = pong
        self.sent: list[dict[str, str]] = []
        self.closed: tuple[int, str] | None = None

    async def send_json(self, event: dict[str, str]) -> None:
        self.sent.append(event)
        if self.pong is not None:
            self.pong.set()

    async def close(self, *, code: int, reason: str) -> None:
        self.closed = (code, reason)


@pytest.mark.asyncio
async def test_heartbeat_accepts_pong_and_keeps_socket_open(monkeypatch):
    # A zero interval creates a tight loop when the fake answers immediately;
    # some event-loop schedulers can then starve this test before it cancels the
    # task. Wait on the pong itself so the synchronization is deterministic.
    monkeypatch.setattr(websocket_module, "HEARTBEAT_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(websocket_module, "HEARTBEAT_TIMEOUT_SECONDS", 0.1)
    pong = asyncio.Event()
    socket = HeartbeatSocket(pong)

    task = asyncio.create_task(websocket_module._heartbeat(socket, pong))
    await asyncio.wait_for(pong.wait(), timeout=1)
    task.cancel()
    await asyncio.wait_for(task, timeout=1)

    assert socket.sent
    assert socket.closed is None


@pytest.mark.asyncio
async def test_heartbeat_closes_socket_after_missing_pong(monkeypatch):
    monkeypatch.setattr(websocket_module, "HEARTBEAT_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(websocket_module, "HEARTBEAT_TIMEOUT_SECONDS", 0.01)
    socket = HeartbeatSocket()

    await websocket_module._heartbeat(socket, asyncio.Event())

    assert socket.sent == [{"type": "ping"}]
    assert socket.closed == (1001, "heartbeat timeout")
