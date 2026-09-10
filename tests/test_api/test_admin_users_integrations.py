"""Admin account controls and durable delivery registration."""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from src.database.models import PushSubscription, RefreshSession


@pytest.mark.asyncio
async def test_admin_can_suspend_and_unsuspend_user(client, test_db, test_admin_headers, test_user, test_user_headers, monkeypatch):
    monkeypatch.setattr("src.api.admin.schedule_webhook_event", lambda *args, **kwargs: None)
    response = await client.post(f"/api/v1/admin/users/{test_user.id}/suspend", headers=test_admin_headers, json={"reason": "Abuse investigation"})
    assert response.status_code == 200
    assert response.json()["suspension_reason"] == "Abuse investigation"
    assert (await client.get("/api/v1/auth/me", headers=test_user_headers)).status_code == 401
    sessions = (await test_db.scalars(select(RefreshSession).where(RefreshSession.user_id == test_user.id))).all()
    assert all(session.revoked_at is not None for session in sessions)
    response = await client.post(f"/api/v1/admin/users/{test_user.id}/unsuspend", headers=test_admin_headers)
    assert response.status_code == 200
    assert response.json()["suspended_at"] is None


@pytest.mark.asyncio
async def test_member_cannot_manage_users(client, test_user_headers):
    assert (await client.get("/api/v1/admin/users", headers=test_user_headers)).status_code == 403


@pytest.mark.asyncio
async def test_admin_webhook_secret_is_only_returned_on_create(client, test_admin_headers, monkeypatch):
    monkeypatch.setattr("src.api.integrations.validate_webhook_url", AsyncMock(return_value="https://hooks.example.test/events"))
    created = await client.post("/api/v1/admin/webhooks", headers=test_admin_headers, json={"url": "https://hooks.example.test/events", "events": ["message.created"]})
    assert created.status_code == 201
    assert created.json()["secret"]
    listed = await client.get("/api/v1/admin/webhooks", headers=test_admin_headers)
    assert listed.status_code == 200
    assert listed.json()[0]["secret"] is None


@pytest.mark.asyncio
async def test_push_subscription_is_upserted_and_removed(client, test_db, test_user, test_user_headers):
    user_id = test_user.id
    payload = {"endpoint": "https://push.example.test/subscription", "keys": {"p256dh": "p" * 32, "auth": "a" * 16}}
    assert (await client.put("/api/v1/push/subscriptions", headers=test_user_headers, json=payload)).status_code == 204
    row = await test_db.scalar(select(PushSubscription).where(PushSubscription.user_id == user_id))
    assert row is not None
    response = await client.delete("/api/v1/push/subscriptions", headers=test_user_headers, params={"endpoint": payload["endpoint"]})
    assert response.status_code == 204
    test_db.expire_all()
    assert await test_db.scalar(select(PushSubscription).where(PushSubscription.user_id == user_id)) is None
