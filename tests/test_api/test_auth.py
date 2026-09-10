"""Tests for authentication API endpoints."""

import re
from urllib.parse import parse_qs, urlparse

import pytest

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
async def test_register_persists_account_and_returns_session(client):
    response = await register_and_verify(
        client,
        {
            "username": "new_user",
            "email": "new@example.com",
            "password": "securepass123",
            "display_name": "New User",
            "preferred_language": "vi",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["user"]["username"] == "new_user"
    assert data["user"]["display_name"] == "New User"
    assert data["user"]["email"] == "new@example.com"
    assert data["access_token"]
    assert data["refresh_token"]

    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": "new@example.com", "password": "securepass123", "remember": True},
    )
    assert login_response.status_code == 200
    assert login_response.json()["user"]["username"] == "new_user"


@pytest.mark.asyncio
async def test_register_rejects_duplicate_email_and_username(client):
    payload = {
        "username": "unique_user",
        "email": "unique@example.com",
        "password": "securepass123",
        "preferred_language": "en",
    }
    assert (await register_and_verify(client, payload)).status_code == 200
    duplicate_email = {**payload, "username": "another_user"}
    duplicate_username = {**payload, "email": "another@example.com"}
    assert (await client.post("/api/v1/auth/register", json=duplicate_email)).status_code == 409
    assert (await client.post("/api/v1/auth/register", json=duplicate_username)).status_code == 409


@pytest.mark.asyncio
async def test_refresh_rotates_token_and_logout_revokes_session(client):
    registered = await register_and_verify(
        client,
        {
            "username": "session_user",
            "email": "session@example.com",
            "password": "securepass123",
            "preferred_language": "en",
        },
    )
    first_refresh = registered.json()["refresh_token"]
    refreshed = await client.post("/api/v1/auth/refresh", json={"refresh_token": first_refresh})
    assert refreshed.status_code == 200
    second_refresh = refreshed.json()["refresh_token"]
    assert second_refresh != first_refresh
    assert (await client.post("/api/v1/auth/refresh", json={"refresh_token": first_refresh})).status_code == 401

    logout = await client.post("/api/v1/auth/logout", json={"refresh_token": second_refresh})
    assert logout.status_code == 204
    assert (await client.post("/api/v1/auth/refresh", json={"refresh_token": second_refresh})).status_code == 401


@pytest.mark.asyncio
async def test_password_reset_changes_password_and_revokes_sessions(client):
    registered = await register_and_verify(
        client,
        {
            "username": "reset_user",
            "email": "reset@example.com",
            "password": "oldpassword123",
            "preferred_language": "vi",
        },
    )
    old_refresh = registered.json()["refresh_token"]
    _memory_sender.clear()
    forgot = await client.post(
        "/api/v1/auth/password/forgot", json={"email": "reset@example.com"}
    )
    assert forgot.status_code == 200
    reset_token = forgot.json()["reset_token"]
    assert reset_token
    assert len(_memory_sender.sent_emails) == 1
    reset_email = _memory_sender.sent_emails[0]
    assert reset_email.to_email == "reset@example.com"
    assert "http://localhost:3000/reset-password?" in reset_email.body_text
    token_in_link = parse_qs(urlparse(re.search(r"https?://\S+", reset_email.body_text).group(0)).query)["token"][0]
    assert token_in_link == reset_token

    reset = await client.post(
        "/api/v1/auth/password/reset",
        json={"token": reset_token, "new_password": "newpassword456"},
    )
    assert reset.status_code == 204
    assert (await client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})).status_code == 401
    assert (await client.post(
        "/api/v1/auth/login",
        json={"email": "reset@example.com", "password": "oldpassword123"},
    )).status_code == 401
    assert (await client.post(
        "/api/v1/auth/login",
        json={"email": "reset@example.com", "password": "newpassword456"},
    )).status_code == 200
    assert (await client.post(
        "/api/v1/auth/password/reset",
        json={"token": reset_token, "new_password": "anotherpassword789"},
    )).status_code == 400


@pytest.mark.asyncio
async def test_forgot_password_does_not_reveal_unknown_email(client):
    _memory_sender.clear()
    response = await client.post(
        "/api/v1/auth/password/forgot", json={"email": "missing@example.com"}
    )
    assert response.status_code == 200
    assert response.json()["reset_token"] is None
    assert _memory_sender.sent_emails == []


@pytest.mark.asyncio
async def test_login_success(client, test_user):
    """Test successful login with valid credentials."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "test@example.com", "password": "testpassword"},
    )

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    # Token should be a non-empty JWT string
    assert len(data["access_token"]) > 20


@pytest.mark.asyncio
async def test_login_invalid_email(client, test_user):
    """Test login with non-existent email."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "nonexistent@example.com", "password": "testpassword"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"


@pytest.mark.asyncio
async def test_login_invalid_password(client, test_user):
    """Test login with wrong password."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "test@example.com", "password": "wrongpassword"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"


@pytest.mark.asyncio
async def test_login_missing_fields(client):
    """Test login with missing fields."""
    # Missing password
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "test@example.com"},
    )
    assert response.status_code == 422  # Validation error

    # Missing email
    response = await client.post(
        "/api/v1/auth/login",
        json={"password": "testpassword"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_current_user_success(client, test_user, test_user_headers):
    """Test getting current user with valid token."""
    response = await client.get("/api/v1/auth/me", headers=test_user_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "test@example.com"
    assert data["role"] == "member"
    assert data["preferred_language"] == "en"
    assert "id" in data
    assert "created_at" in data
    # Ensure password hash is not exposed
    assert "password" not in data
    assert "password_hash" not in data


@pytest.mark.asyncio
async def test_get_current_user_no_token(client):
    """Test getting current user without token."""
    response = await client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or expired token"


@pytest.mark.asyncio
async def test_get_current_user_invalid_token(client):
    """Test getting current user with invalid token."""
    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer invalid_token_here"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or expired token"


@pytest.mark.asyncio
async def test_get_current_user_malformed_auth_header(client):
    """Test getting current user with malformed Authorization header."""
    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "NotBearer token"},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_update_language_success(client, test_user, test_user_headers):
    """Test updating preferred language."""
    response = await client.put(
        "/api/v1/auth/me/language",
        headers=test_user_headers,
        json={"preferred_language": "vi"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["preferred_language"] == "vi"
    assert data["email"] == "test@example.com"  # Other fields unchanged


@pytest.mark.asyncio
async def test_update_language_persistence(client, test_user, test_user_headers):
    """Test that language preference persists after update."""
    # Update language
    await client.put(
        "/api/v1/auth/me/language",
        headers=test_user_headers,
        json={"preferred_language": "ja"},
    )

    # Get user again
    response = await client.get("/api/v1/auth/me", headers=test_user_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["preferred_language"] == "ja"


@pytest.mark.asyncio
async def test_update_language_invalid_code(client, test_user, test_user_headers):
    """Test updating with unsupported language code."""
    response = await client.put(
        "/api/v1/auth/me/language",
        headers=test_user_headers,
        json={"preferred_language": "invalid_code"},
    )

    assert response.status_code == 422  # Validation error


@pytest.mark.asyncio
async def test_update_language_case_insensitive(client, test_user, test_user_headers):
    """Test that language code is normalized to lowercase."""
    response = await client.put(
        "/api/v1/auth/me/language",
        headers=test_user_headers,
        json={"preferred_language": "EN"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["preferred_language"] == "en"


@pytest.mark.asyncio
async def test_update_language_no_auth(client):
    """Test updating language without authentication."""
    response = await client.put(
        "/api/v1/auth/me/language",
        json={"preferred_language": "vi"},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_password_not_in_response(client, test_user, test_user_headers):
    """Test that password_hash is never exposed in any response."""
    # Check get user response
    response = await client.get("/api/v1/auth/me", headers=test_user_headers)
    data = response.json()

    # Ensure sensitive fields are not in response
    assert "password" not in data
    assert "password_hash" not in data

    # Check all fields are strings or expected types
    for key, value in data.items():
        assert not key.lower().startswith("password"), f"Found password field: {key}"


@pytest.mark.asyncio
async def test_user_id_from_token_not_request(client, test_user, test_admin, test_admin_headers):
    """Test that user identity comes from JWT token, not request body."""
    # Use admin's token to get user info
    response = await client.get("/api/v1/auth/me", headers=test_admin_headers)

    assert response.status_code == 200
    data = response.json()
    # Should return admin's info, not test_user's
    assert data["email"] == "admin@example.com"
    assert data["role"] == "admin"


@pytest.mark.asyncio
async def test_patch_user_settings_rejects_explicit_null(client, test_user, test_user_headers):
    """Explicit null for non-nullable boolean/tone fields returns 422, not 500."""
    response = await client.patch(
        "/api/v1/auth/me/settings",
        headers=test_user_headers,
        json={"auto_translate": None},
    )
    assert response.status_code == 422

    response2 = await client.patch(
        "/api/v1/auth/me/settings",
        headers=test_user_headers,
        json={"sound_enabled": None},
    )
    assert response2.status_code == 422


@pytest.mark.asyncio
async def test_patch_user_settings_rejects_empty_body(client, test_user, test_user_headers):
    """Empty PATCH {} must return 422 validation error."""
    response = await client.patch(
        "/api/v1/auth/me/settings",
        headers=test_user_headers,
        json={},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_patch_user_settings_updates_and_preserves_omitted(client, test_user, test_user_headers):
    """Partial update updates only specified fields and preserves existing ones."""
    res1 = await client.patch(
        "/api/v1/auth/me/settings",
        headers=test_user_headers,
        json={"auto_translate": False, "translation_tone": "formal"},
    )
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["auto_translate"] is False
    assert data1["translation_tone"] == "formal"
    assert data1["show_original_by_default"] is False

    # Partial update sound_enabled only
    res2 = await client.patch(
        "/api/v1/auth/me/settings",
        headers=test_user_headers,
        json={"sound_enabled": False},
    )
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["sound_enabled"] is False
    assert data2["auto_translate"] is False  # preserved
    assert data2["translation_tone"] == "formal"  # preserved
