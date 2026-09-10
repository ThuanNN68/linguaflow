"""Tests for the interface language, separate from the reading one (§1.2)."""

import re

import pytest

from src.database.models import User
from src.services.identity.email import _memory_sender


async def register_and_verify(client, payload):
    _memory_sender.clear()
    reg = await client.post("/api/v1/auth/register", json=payload)
    if reg.status_code != 202:
        return reg
    pending_id = reg.json()["pending_id"]
    otp = re.search(r"\b(\d{6})\b", _memory_sender.sent_emails[0].body_text).group(1)
    return await client.post(
        "/api/v1/auth/register/verify", json={"pending_id": pending_id, "otp": otp}
    )


@pytest.mark.asyncio
async def test_registration_sets_both_languages_from_one_choice(client):
    """One language question at sign-up fills both settings (§1.3)."""
    response = await register_and_verify(
        client,
        {
            "email": "hanako@example.com",
            "password": "MatKhau!2345",
            "username": "hanako",
            "display_name": "花子",
            "preferred_language": "ja",
        },
    )

    assert response.status_code == 200
    user = response.json()["user"]
    assert user["preferred_language"] == "ja"
    assert user["interface_language"] == "ja"


@pytest.mark.asyncio
async def test_registration_without_a_language_defaults_both_to_english(client):
    """English is what a stranger who chose nothing gets (§1.3 step 1)."""
    response = await register_and_verify(
        client,
        {
            "email": "nochoice@example.com",
            "password": "MatKhau!2345",
            "username": "nochoice",
        },
    )

    assert response.status_code == 200
    user = response.json()["user"]
    assert user["preferred_language"] == "en"
    assert user["interface_language"] == "en"


@pytest.mark.asyncio
async def test_changing_the_interface_language_leaves_the_reading_one_alone(
    client,
    test_db,
    test_user,
    test_user_headers,
):
    """The two part company once the account exists.

    Reading Japanese messages behind a Vietnamese menu is a combination the
    product allows on purpose — the two settings answer different questions.
    """
    response = await client.put(
        "/api/v1/auth/me/interface-language",
        headers=test_user_headers,
        json={"interface_language": "vi"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["interface_language"] == "vi"
    assert body["preferred_language"] == "en"

    stored = await test_db.get(User, test_user.id)
    await test_db.refresh(stored)
    assert stored.interface_language == "vi"
    assert stored.preferred_language == "en"


@pytest.mark.asyncio
async def test_changing_the_reading_language_leaves_the_interface_alone(
    client,
    test_user,
    test_user_headers,
):
    """The reverse direction, which is the one that costs LLM quota."""
    response = await client.put(
        "/api/v1/auth/me/language",
        headers=test_user_headers,
        json={"preferred_language": "ja"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["preferred_language"] == "ja"
    assert body["interface_language"] == "en"


@pytest.mark.asyncio
async def test_unsupported_interface_language_is_rejected(client, test_user_headers):
    """The allowlist is the same one the reading language uses (§1 conventions)."""
    response = await client.put(
        "/api/v1/auth/me/interface-language",
        headers=test_user_headers,
        json={"interface_language": "xx"},
    )

    assert response.status_code == 422
