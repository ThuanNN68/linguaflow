"""Authorization and payload contracts for extended system health."""

import pytest

from src.api.system_health import router


@pytest.mark.asyncio
async def test_system_health_is_admin_only(client, test_user_headers, test_admin_headers):
    anonymous = await client.get("/api/v1/health/system")
    member = await client.get("/api/v1/health/system", headers=test_user_headers)
    admin = await client.get("/api/v1/health/system", headers=test_admin_headers)

    assert anonymous.status_code == 401
    assert member.status_code == 403
    assert admin.status_code == 200


@pytest.mark.asyncio
async def test_system_health_returns_complete_runtime_metrics(client, test_admin_headers):
    response = await client.get("/api/v1/health/system", headers=test_admin_headers)
    payload = response.json()

    assert response.status_code == 200
    assert set(payload["websocket_connections"]) == {"online_users", "total_sockets"}
    assert isinstance(payload["background_tasks_active"], int)
    assert payload["translation_cache_size"] == payload["translation_cache"]["size"]
    assert payload["translation_cache_hit_rate"] == payload["translation_cache"]["hit_rate"]
    assert set(payload["circuit_breakers"]) == {
        "llm",
        "embedding",
        "embedding_fallback",
        "fallback_translator",
    }
    assert set(payload["db_pool_status"]) == {
        "status",
        "size",
        "checked_in",
        "checked_out",
        "overflow",
    }
    assert isinstance(payload["db_pool_status"]["status"], str)
    assert payload["memory_usage_mb"] > 0
    assert isinstance(payload["reminder_scheduler_running"], bool)
    assert payload["rate_limits"]["api"] == "60/minute"
    assert payload["rate_limits"]["llm"] == "20/minute"
    assert payload["distributed_tracing"]["enabled"] is False
    assert payload["distributed_tracing"]["configured"] is False


def test_system_health_router_is_registered():
    assert "/health/system" in {route.path for route in router.routes}
