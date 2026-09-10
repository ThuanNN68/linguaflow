"""Contracts for security audit events and irreversible self-service erasure."""

import pytest
from sqlalchemy import select

from src.database.models import AuditLog, Message, User


@pytest.mark.asyncio
async def test_consent_change_records_non_content_audit_event(client, test_db, test_user, test_user_headers):
    response = await client.put(
        "/api/v1/auth/me/agent-consents",
        headers=test_user_headers,
        json={"consents": {"store_memory": True}},
    )

    assert response.status_code == 200
    event = await test_db.scalar(select(AuditLog).where(AuditLog.action == "assistant.consent_granted"))
    assert event is not None
    assert event.actor_id == test_user.id
    assert "store_memory" in event.metadata_json
    assert "message" not in event.metadata_json


@pytest.mark.asyncio
async def test_account_erasure_tombstones_identity_and_withdraws_messages(
    client, test_db, test_user, test_user_headers, test_user_two, conversation_factory
):
    conversation = await conversation_factory(test_user, [test_user, test_user_two])
    message = Message(
        client_message_id="erase-account-message",
        conversation_id=conversation.id,
        sender_id=test_user.id,
        original_text="personal content to erase",
        source_language="en",
    )
    test_db.add(message)
    await test_db.commit()

    response = await client.request(
        "DELETE",
        "/api/v1/auth/me",
        headers=test_user_headers,
        json={"confirmation": "DELETE", "current_password": "testpassword"},
    )

    assert response.status_code == 204
    await test_db.refresh(test_user)
    await test_db.refresh(message)
    assert test_user.deleted_at is not None
    assert test_user.email.endswith("@deleted.invalid")
    assert test_user.avatar_url is None
    assert message.original_text == ""
    assert message.deleted_at is not None
    assert await test_db.scalar(select(AuditLog).where(AuditLog.action == "account.erased")) is not None

    assert (await client.get("/api/v1/auth/me", headers=test_user_headers)).status_code == 401
    assert await test_db.scalar(select(User).where(User.id == test_user.id)) is not None


@pytest.mark.asyncio
async def test_account_erasure_requires_confirmation_and_current_password(client, test_user_headers):
    rejected_phrase = await client.request("DELETE", "/api/v1/auth/me", headers=test_user_headers, json={"confirmation": "NO"})
    rejected_password = await client.request("DELETE", "/api/v1/auth/me", headers=test_user_headers, json={"confirmation": "DELETE"})
    assert rejected_phrase.status_code == 422
    assert rejected_password.status_code == 403


@pytest.mark.asyncio
async def test_audit_log_is_visible_to_admin_only(client, test_user_headers, test_admin_headers):
    denied = await client.get("/api/v1/admin/audit-logs", headers=test_user_headers)
    allowed = await client.get("/api/v1/admin/audit-logs", headers=test_admin_headers)
    assert denied.status_code == 403
    assert allowed.status_code == 200
