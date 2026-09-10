"""Tests for application-level routes that belong to no feature."""

import pytest


@pytest.mark.asyncio
async def test_health_endpoint_reports_ok(client):
    """Verify that the health check endpoint returns 200 and ok status."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "LinguaFlow API"
    assert data["checks"] == {"api": "ok", "database": "ok"}


@pytest.mark.asyncio
async def test_liveness_does_not_require_database(client):
    """The process-only probe remains available for diagnosing DB failures."""
    response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["checks"] == {"api": "ok"}


@pytest.mark.asyncio
async def test_legacy_chat_endpoint_is_gone(client):
    """The template `/chat` and `/status` endpoints were removed with the
    template agent they served (docs/api/contract.md section 3.3)."""
    assert (await client.post("/api/v1/chat", json={"message": "hi"})).status_code == 404
    assert (await client.get("/api/v1/status")).status_code == 404
