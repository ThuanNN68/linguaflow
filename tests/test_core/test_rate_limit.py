"""Integration contracts for HTTP and WebSocket rate limiting."""

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from src.core.rate_limit import (
    WebSocketRateLimitError,
    check_websocket_message_rate,
    get_request_key,
    limiter,
)
from src.core.security import create_access_token


@pytest.mark.asyncio
async def test_login_is_limited_to_five_attempts_per_ip(client):
    payload = {"email": "missing@example.com", "password": "wrong", "remember": False}
    responses = [await client.post("/api/v1/auth/login", json=payload) for _ in range(6)]

    assert [response.status_code for response in responses[:5]] == [401] * 5
    assert responses[5].status_code == 429
    assert int(responses[5].headers["Retry-After"]) >= 1


@pytest.mark.asyncio
async def test_register_is_limited_before_validation_or_email_delivery(client):
    responses = [await client.post("/api/v1/auth/register", json={}) for _ in range(6)]

    assert [response.status_code for response in responses[:5]] == [422] * 5
    assert responses[5].status_code == 429
    assert "Retry-After" in responses[5].headers


@pytest.mark.asyncio
async def test_llm_routes_are_limited_to_twenty_requests_per_user(client, test_user_headers):
    path = (
        "/api/v1/conversations/00000000-0000-0000-0000-000000000000/"
        "messages/00000000-0000-0000-0000-000000000000/extract-actions"
    )
    responses = [await client.post(path, headers=test_user_headers) for _ in range(21)]

    assert all(response.status_code != 429 for response in responses[:20])
    assert responses[20].status_code == 429
    assert "Retry-After" in responses[20].headers


@pytest.mark.asyncio
async def test_general_rest_budget_is_shared_across_routes_for_one_user(test_user):
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    @app.get("/one")
    async def one():
        return {"ok": True}

    @app.get("/two")
    async def two():
        return {"ok": True}

    headers = {"Authorization": f"Bearer {create_access_token(test_user.id)}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = [await client.get("/one", headers=headers) for _ in range(30)]
        second = [await client.get("/two", headers=headers) for _ in range(31)]

    assert all(response.status_code == 200 for response in first + second[:30])
    assert second[30].status_code == 429
    assert "Retry-After" in second[30].headers


def test_authenticated_key_comes_from_jwt_subject(test_user):
    token = create_access_token(test_user.id)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"authorization", f"Bearer {token}".encode())],
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )

    assert get_request_key(request) == f"user:{test_user.id}"


def test_websocket_messages_are_limited_per_user_with_retry_after():
    for _ in range(20):
        check_websocket_message_rate("member-1")

    with pytest.raises(WebSocketRateLimitError) as exc_info:
        check_websocket_message_rate("member-1")

    assert exc_info.value.retry_after >= 1
    check_websocket_message_rate("member-2")
