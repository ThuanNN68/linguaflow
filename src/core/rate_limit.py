"""Rate limiting for HTTP and WebSocket traffic.

REST limits are enforced by SlowAPI with a shared Redis moving-window store
when ``REDIS_URL`` is configured, and an in-memory store otherwise. A
validated access-token subject is the key for authenticated traffic; requests
that cannot yet be authenticated fall back to the client address. WebSockets
cannot return HTTP 429 after upgrade, so their message budget uses the same
``limits`` engine and exposes a retry duration to the socket handler.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from fastapi import Request
from limits import parse
from limits.storage import storage_from_string
from limits.strategies import MovingWindowRateLimiter
from slowapi import Limiter
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from src.config import get_settings
from src.core.security import decode_token

AUTH_RATE_LIMIT = "5/minute"
API_RATE_LIMIT = "60/minute"
LLM_RATE_LIMIT = "20/minute"
WEBSOCKET_RATE_LIMIT = "20/minute"


def _configured_storage_uri() -> str:
    """Use the realtime Redis deployment as the shared throttle backend."""
    redis_url = get_settings().redis_url.strip()
    return redis_url or "memory://"


_STORAGE_URI = _configured_storage_uri()
_STORAGE_KIND = "redis" if _STORAGE_URI.startswith(("redis://", "rediss://")) else "memory"


def get_client_ip(request: Request) -> str:
    """Return the originating address used for unauthenticated throttling."""
    # Uvicorn rewrites ``request.client`` from forwarding headers only when the
    # connection comes from a configured trusted proxy. Reading X-Forwarded-For
    # here directly would let any client choose a new key and bypass the limit.
    return request.client.host if request.client else "unknown"


def get_authenticated_user_id(request: Request) -> str | None:
    """Read a validated JWT subject without performing a database query."""
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    payload = decode_token(token)
    subject = payload.get("sub") if payload else None
    return subject if isinstance(subject, str) and subject else None


def get_request_key(request: Request) -> str:
    """Key authenticated traffic by user and all other traffic by address."""
    user_id = get_authenticated_user_id(request)
    if user_id:
        return f"user:{user_id}"
    return f"ip:{get_client_ip(request)}"


limiter = Limiter(
    key_func=get_request_key,
    application_limits=[API_RATE_LIMIT],
    storage_uri=_STORAGE_URI,
    strategy="moving-window",
    headers_enabled=True,
    retry_after="delta-seconds",
)

auth_limit = limiter.limit(AUTH_RATE_LIMIT, key_func=get_client_ip)
llm_limit = limiter.limit(LLM_RATE_LIMIT, key_func=get_request_key)

_auth_storage = storage_from_string(_STORAGE_URI)
_auth_limiter = MovingWindowRateLimiter(_auth_storage)
_auth_limit = parse(AUTH_RATE_LIMIT)
_AUTH_PATHS = frozenset({"/api/v1/auth/login", "/api/v1/auth/register"})


class AuthRateLimitMiddleware:
    """Throttle auth attempts before FastAPI parses and validates the body.

    SlowAPI endpoint decorators execute after request validation. This ASGI
    guard uses the same ``limits`` moving-window engine so malformed auth
    attempts cannot bypass the brute-force budget.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") not in _AUTH_PATHS:
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        key = f"ip:{get_client_ip(request)}"
        namespace = f"auth:{scope['path']}"
        if _auth_limiter.hit(_auth_limit, key, namespace):
            await self.app(scope, receive, send)
            return

        reset_at, _remaining = _auth_limiter.get_window_stats(_auth_limit, key, namespace)
        retry_after = max(1, math.ceil(reset_at - time.time()))
        response = JSONResponse(
            status_code=429,
            content={"error": "rate_limit_exceeded", "detail": "Too many requests"},
            headers={"Retry-After": str(retry_after)},
        )
        await response(scope, receive, send)


@dataclass(frozen=True, slots=True)
class WebSocketRateLimitError(Exception):
    """A post-upgrade message exceeded the user's WebSocket budget."""

    retry_after: int


_websocket_storage = storage_from_string(_STORAGE_URI)
_websocket_limiter = MovingWindowRateLimiter(_websocket_storage)
_websocket_limit = parse(WEBSOCKET_RATE_LIMIT)


def check_websocket_message_rate(user_id: str) -> None:
    """Consume one message allowance or raise with seconds until retry."""
    key = f"user:{user_id}"
    if _websocket_limiter.hit(_websocket_limit, key, "websocket-messages"):
        return
    reset_at, _remaining = _websocket_limiter.get_window_stats(_websocket_limit, key, "websocket-messages")
    raise WebSocketRateLimitError(retry_after=max(1, math.ceil(reset_at - time.time())))


def reset_rate_limits() -> None:
    """Clear in-memory counters; used by isolated tests and local restarts."""
    limiter.reset()
    _auth_storage.reset()
    _websocket_storage.reset()


def rate_limit_status() -> dict[str, object]:
    """Expose configuration—not user keys—to the admin health endpoint."""
    return {
        "storage": _STORAGE_KIND,
        "strategy": "moving-window",
        "auth": AUTH_RATE_LIMIT,
        "api": API_RATE_LIMIT,
        "llm": LLM_RATE_LIMIT,
        "websocket_messages": WEBSOCKET_RATE_LIMIT,
    }
