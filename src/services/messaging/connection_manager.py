"""WebSocket connections with an optional Redis cross-process backplane."""

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

logger = logging.getLogger(__name__)
_BACKPLANE_CHANNEL = "linguaflow:websocket:v1"


class ConnectionManager:
    """Track live sockets by user without owning application business logic.

    This manager intentionally has no database or authorization dependency.  A
    WebSocket endpoint authenticates and accepts a socket before registering it
    here, then supplies already-authorized recipient user IDs for fan-out.
    """

    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = {}
        self._instance_id = uuid.uuid4().hex
        self._redis: Any | None = None
        self._pubsub: Any | None = None
        self._listener_task: asyncio.Task[None] | None = None

    async def start(self, redis_url: str) -> None:
        """Connect the optional Redis pub/sub backplane."""
        if not redis_url.strip() or self._listener_task is not None:
            return
        from redis.asyncio import from_url

        redis = from_url(redis_url.strip(), decode_responses=True)
        await redis.ping()
        pubsub = redis.pubsub(ignore_subscribe_messages=True)
        await pubsub.subscribe(_BACKPLANE_CHANNEL)
        self._redis = redis
        self._pubsub = pubsub
        self._listener_task = asyncio.create_task(
            self._listen_for_remote_events(),
            name="websocket-redis-backplane",
        )
        logger.info("WebSocket Redis backplane connected")

    async def stop(self) -> None:
        """Stop the backplane listener and release Redis resources."""
        task = self._listener_task
        self._listener_task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self._pubsub is not None:
            await self._pubsub.aclose()
        if self._redis is not None:
            await self._redis.aclose()
        self._pubsub = None
        self._redis = None

    async def _listen_for_remote_events(self) -> None:
        assert self._pubsub is not None
        try:
            async for message in self._pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    envelope = json.loads(message["data"])
                    if envelope.get("origin") == self._instance_id:
                        continue
                    user_ids = envelope["user_ids"]
                    event = envelope["event"]
                    if not isinstance(user_ids, list) or not isinstance(event, dict):
                        continue
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    logger.warning("Discarded malformed WebSocket backplane event")
                    continue
                for user_id in user_ids:
                    if isinstance(user_id, str):
                        await self._send_local(user_id, event)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("WebSocket Redis backplane listener stopped unexpectedly")

    @property
    def connections(self) -> dict[str, set[WebSocket]]:
        """Expose the live-connection mapping for narrow operational inspection."""
        return self._connections

    def connect(self, user_id: str, websocket: WebSocket) -> None:
        """Register an already accepted, authenticated socket for ``user_id``."""
        self._connections.setdefault(user_id, set()).add(websocket)

    def register(self, user_id: str, websocket: WebSocket) -> None:
        """Alias for :meth:`connect` that emphasizes registration semantics."""
        self.connect(user_id, websocket)

    def disconnect(self, user_id: str, websocket: WebSocket) -> None:
        """Remove only ``websocket``, preserving any other sockets for the user."""
        sockets = self._connections.get(user_id)
        if sockets is None:
            return

        sockets.discard(websocket)
        if not sockets:
            del self._connections[user_id]

    def is_online(self, user_id: str) -> bool:
        """Whether ``user_id`` currently holds at least one live socket."""
        return bool(self._connections.get(user_id))

    def online_user_ids(self, candidates: Iterable[str]) -> tuple[str, ...]:
        """Filter ``candidates`` down to those with a live socket, order kept."""
        return tuple(user_id for user_id in candidates if self.is_online(user_id))

    async def _send_local(self, user_id: str, event: Mapping[str, Any]) -> None:
        """Deliver an event to every live socket for one user.

        Offline users are deliberately a no-op.  A socket that has disconnected
        while an event is being sent is removed without affecting the user's
        remaining sockets or delivery to any other recipient.
        """
        for websocket in tuple(self._connections.get(user_id, ())):
            try:
                await websocket.send_json(dict(event))
            except (OSError, RuntimeError, WebSocketDisconnect):
                self.disconnect(user_id, websocket)

    async def send_to_user(self, user_id: str, event: Mapping[str, Any]) -> None:
        """Deliver to all tabs for a user, including tabs in other processes."""
        await self.send_to_users((user_id,), event)

    async def send_to_users(self, user_ids: Iterable[str], event: Mapping[str, Any]) -> None:
        """Deliver an event to each distinct user ID in ``user_ids``."""
        recipients = tuple(dict.fromkeys(user_ids))
        for user_id in recipients:
            await self._send_local(user_id, event)
        if self._redis is not None and recipients:
            envelope = json.dumps(
                {
                    "origin": self._instance_id,
                    "user_ids": recipients,
                    "event": dict(event),
                },
                separators=(",", ":"),
            )
            try:
                await self._redis.publish(_BACKPLANE_CHANNEL, envelope)
            except Exception:
                # Local delivery has already succeeded. Surface the degraded
                # cross-process path without turning message persistence into
                # an apparent client failure that invites duplicate retries.
                logger.exception("WebSocket Redis publish failed")
