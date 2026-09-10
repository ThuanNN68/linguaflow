"""Tests for the consent endpoints and the 403 the assistant returns without them.

The important assertion is the one about `scope` on the error body: it is what
lets the interface open the right permission instead of sending the user to hunt
through settings, and nothing else in the response carries that information.

Fixtures are local to this file rather than in tests/conftest.py, which is shared
across all feature areas.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from src.database.models import AGENT_CONSENT_SCOPES


@pytest.mark.asyncio
async def test_consents_endpoint_lists_every_scope_before_any_answer(
    client: AsyncClient, test_user_headers
) -> None:
    """A fresh account must still receive the full list to be asked about."""
    response = await client.get("/api/v1/auth/me/agent-consents", headers=test_user_headers)

    assert response.status_code == 200
    body = response.json()
    assert [entry["scope"] for entry in body["consents"]] == list(AGENT_CONSENT_SCOPES)
    assert all(entry["is_granted"] is False for entry in body["consents"])
    assert body["policy_version"]


@pytest.mark.asyncio
async def test_putting_one_scope_does_not_disturb_the_others(
    client: AsyncClient, test_user_headers
) -> None:
    """A partial update is partial; unnamed scopes keep their state."""
    await client.put(
        "/api/v1/auth/me/agent-consents",
        headers=test_user_headers,
        json={"consents": {"read_conversations": True, "store_memory": True}},
    )
    response = await client.put(
        "/api/v1/auth/me/agent-consents",
        headers=test_user_headers,
        json={"consents": {"store_memory": False}},
    )

    assert response.status_code == 200
    states = {entry["scope"]: entry["is_granted"] for entry in response.json()["consents"]}
    assert states["read_conversations"] is True
    assert states["store_memory"] is False


@pytest.mark.asyncio
async def test_putting_an_unknown_scope_is_rejected_as_invalid(
    client: AsyncClient, test_user_headers
) -> None:
    """Silently ignoring a misspelled scope would look like a successful grant."""
    response = await client.put(
        "/api/v1/auth/me/agent-consents",
        headers=test_user_headers,
        json={"consents": {"read_everything": True}},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_consents_endpoint_requires_authentication(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/me/agent-consents")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_summary_without_consent_returns_403_naming_the_scope(
    client: AsyncClient, test_user, test_user_headers, conversation_factory
) -> None:
    """The refusal has to say which permission is missing, not just refuse."""
    conversation = await conversation_factory(test_user, [test_user])

    response = await client.post(
        f"/api/v1/conversations/{conversation.id}/summary",
        headers=test_user_headers,
        json={},
    )

    assert response.status_code == 403
    body = response.json()
    assert body["code"] == "CONSENT_REQUIRED"
    assert body["scope"] == "read_conversations"


@pytest.mark.asyncio
async def test_one_users_consent_does_not_unlock_the_assistant_for_another(
    client: AsyncClient,
    test_user,
    test_user_headers,
    test_user_two,
    conversation_factory,
) -> None:
    """Both share the conversation; only one agreed, so only one is served."""
    conversation = await conversation_factory(
        test_user, [test_user, test_user_two], conversation_type="group"
    )
    await client.put(
        "/api/v1/auth/me/agent-consents",
        headers=test_user_headers,
        json={"consents": {"read_conversations": True}},
    )

    from tests.conftest import auth_headers_for_user

    response = await client.post(
        f"/api/v1/conversations/{conversation.id}/summary",
        headers=auth_headers_for_user(test_user_two),
        json={},
    )

    assert response.status_code == 403
    assert response.json()["scope"] == "read_conversations"
