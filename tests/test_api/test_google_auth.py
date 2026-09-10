"""Tests for Google Sign-In as a Full Authentication Provider (Batch G Spec Correction).

Covers:
    1. Verification security (invalid JWT, missing client ID, wrong audience, missing sub/email, email_verified=False)
    2. Existing google_sub login (session creation, idempotency, no duplicate users)
    3. Verified email auto-link (case-insensitive email match, google_sub=None, password_hash preservation, password login still works)
    4. Conflicting Google binding (email exists with different google_sub -> 409 Conflict, no mutation)
    5. First-time Google-native signup/login (user creation, password_hash=NULL, role=member, collision-safe username)
    6. Race safety (IntegrityError rollback during auto-create and auto-link)
    7. Password behavior (Google-only user password login returns generic 401, password preservation)
    8. Unlink behavior (idempotent when unlinked, password-backed user can unlink, Google-only user cannot unlink -> 409)
    9. Response privacy (google_sub & password_hash never exposed, google_linked & has_password correctly derived)
    10. Explicit link from Settings (POST /auth/me/google/link)
"""

from unittest.mock import AsyncMock, patch

import pytest
from google.auth import exceptions as google_exceptions
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings
from src.database.models import User
from src.services.identity.google_auth import (
    GoogleAuthError,
    GoogleUserInfo,
    _verify_id_token_sync,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def auth_headers(user: User) -> dict[str, str]:
    from src.core.security import create_access_token
    return {"Authorization": f"Bearer {create_access_token(subject=user.id)}"}


def make_google_info(
    google_sub: str = "g_123456",
    email: str = "user@gmail.com",
    email_verified: bool = True,
    name: str | None = "Test User",
) -> GoogleUserInfo:
    return GoogleUserInfo(
        google_sub=google_sub,
        email=email.strip().lower(),
        email_verified=email_verified,
        name=name,
        picture=None,
    )


async def register_user(
    db: AsyncSession,
    email: str,
    password: str | None = "testpassword",
    google_sub: str | None = None,
    role: str = "member",
    preferred_language: str = "en",
    interface_language: str = "en",
    username: str | None = None,
    display_name: str | None = None,
) -> User:
    """Create a user with real or null password_hash and optional google_sub."""
    from src.core.security import get_password_hash
    pw_hash = get_password_hash(password) if password else None
    user = User(
        email=email.strip().lower(),
        password_hash=pw_hash,
        role=role,
        preferred_language=preferred_language,
        interface_language=interface_language,
        google_sub=google_sub,
        username=username,
        display_name=display_name,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# 1. Verification Security Tests
# ---------------------------------------------------------------------------

def test_verifier_missing_client_id_raises_safe_error():
    """When GOOGLE_OAUTH_CLIENT_ID is not configured, token verification fails safely."""
    settings = Settings(google_oauth_client_id="")
    with pytest.raises(GoogleAuthError, match="Google Sign-In is not configured"):
        _verify_id_token_sync("dummy.token", settings)


def test_verifier_google_auth_error_raises_safe_error():
    """When google-auth rejects the token (wrong audience / signature), raise GoogleAuthError."""
    settings = Settings(google_oauth_client_id="expected-client-id.apps.googleusercontent.com")
    with patch("google.oauth2.id_token.verify_oauth2_token") as mock_verify:
        mock_verify.side_effect = google_exceptions.GoogleAuthError("Audience mismatch")
        with pytest.raises(GoogleAuthError, match="Google token verification failed"):
            _verify_id_token_sync("wrong.audience.token", settings)


def test_verifier_malformed_token_raises_safe_error():
    """When token is malformed, raise GoogleAuthError."""
    settings = Settings(google_oauth_client_id="expected-client-id.apps.googleusercontent.com")
    with patch("google.oauth2.id_token.verify_oauth2_token") as mock_verify:
        mock_verify.side_effect = ValueError("Invalid JWT format")
        with pytest.raises(GoogleAuthError, match="Malformed Google token"):
            _verify_id_token_sync("not-a-jwt", settings)


@pytest.mark.parametrize(
    "claims",
    [
        {"email": "user@gmail.com", "email_verified": True},
        {"sub": "g_123", "email_verified": True},
    ],
)
def test_verifier_missing_sub_or_email_raises_safe_error(claims: dict[str, object]):
    """A verified token still needs both immutable subject and email claims."""
    settings = Settings(google_oauth_client_id="expected-client-id.apps.googleusercontent.com")
    with patch("google.oauth2.id_token.verify_oauth2_token") as mock_verify:
        mock_verify.return_value = claims
        with pytest.raises(GoogleAuthError, match="Token missing required"):
            _verify_id_token_sync("token.missing.sub", settings)


@pytest.mark.parametrize("email_verified", [False, "true", "True", 1, None])
def test_verifier_requires_boolean_true_email_verified(email_verified: object):
    """Only a boolean True claim may authorize email-based identity resolution."""
    settings = Settings(google_oauth_client_id="expected-client-id.apps.googleusercontent.com")
    with patch("google.oauth2.id_token.verify_oauth2_token") as mock_verify:
        mock_verify.return_value = {
            "sub": "g_123",
            "email": "unverified@gmail.com",
            "email_verified": email_verified,
        }
        with pytest.raises(GoogleAuthError, match="Google email is not verified"):
            _verify_id_token_sync("token.unverified", settings)


def test_verifier_success_normalizes_email():
    """Valid token verification returns normalized lowercase email and GoogleUserInfo."""
    settings = Settings(google_oauth_client_id="expected-client-id.apps.googleusercontent.com")
    with patch("google.oauth2.id_token.verify_oauth2_token") as mock_verify:
        mock_verify.return_value = {
            "sub": "g_valid_123",
            "email": "  User.Name@GMAIL.COM  ",
            "email_verified": True,
            "name": "Valid User",
            "picture": "https://example.com/avatar.png",
        }
        info = _verify_id_token_sync("valid.token", settings)
        assert info.google_sub == "g_valid_123"
        assert info.email == "user.name@gmail.com"
        assert info.email_verified is True
        assert info.name == "Valid User"
        assert info.picture == "https://example.com/avatar.png"
        args, kwargs = mock_verify.call_args
        assert args[0] == "valid.token"
        assert args[2] == settings.google_oauth_client_id
        assert kwargs["clock_skew_in_seconds"] == 120


# ---------------------------------------------------------------------------
# 2. Existing google_sub Login (Case 1)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_google_login_existing_google_sub_creates_session(
    client: AsyncClient,
    test_db: AsyncSession,
):
    """An account with a matching google_sub logs in normally with access & refresh tokens."""
    user = await register_user(
        test_db, email="existing@gmail.com", google_sub="g_sub_123"
    )
    count_before = (await test_db.execute(select(func.count(User.id)))).scalar_one()

    with patch("src.api.auth.verify_google_token", new_callable=AsyncMock) as mock_v:
        mock_v.return_value = make_google_info(google_sub="g_sub_123", email="existing@gmail.com")
        response = await client.post(
            "/api/v1/auth/google/login",
            json={"credential": "valid.google.jwt"},
        )

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["user"]["id"] == user.id
    assert data["user"]["email"] == "existing@gmail.com"
    assert data["user"]["google_linked"] is True
    assert data["user"]["has_password"] is True
    assert (await test_db.execute(select(func.count(User.id)))).scalar_one() == count_before


# ---------------------------------------------------------------------------
# 3. Verified Email Auto-Link (Case 2)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_google_login_auto_links_existing_password_user_by_verified_email(
    client: AsyncClient,
    test_db: AsyncSession,
):
    """An existing password-backed user with google_sub=None is automatically linked on Google login."""
    user = await register_user(
        test_db,
        email="autolink@gmail.com",
        password="MySecretPassword123!",
        google_sub=None,
        role="admin",
        preferred_language="vi",
        interface_language="vi",
        username="kept-username",
        display_name="Kept Display Name",
    )
    user_id = user.id
    original_pw_hash = user.password_hash

    with patch("src.api.auth.verify_google_token", new_callable=AsyncMock) as mock_v:
        mock_v.return_value = make_google_info(
            google_sub="g_new_sub_456",
            email="AUTOLINK@GMAIL.COM",  # Case-insensitive
            email_verified=True,
        )
        response = await client.post(
            "/api/v1/auth/google/login",
            json={"credential": "valid.google.jwt"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["user"]["id"] == user_id
    assert data["user"]["google_linked"] is True
    assert data["user"]["has_password"] is True

    # Verify DB state: same identity, password, profile, role and preferences are preserved.
    await test_db.commit()
    test_db.expire_all()

    refreshed = (await test_db.execute(select(User).where(User.id == user_id))).scalar_one()
    assert refreshed.google_sub == "g_new_sub_456"
    assert refreshed.password_hash == original_pw_hash
    assert refreshed.role == "admin"
    assert refreshed.preferred_language == "vi"
    assert refreshed.interface_language == "vi"
    assert refreshed.username == "kept-username"
    assert refreshed.display_name == "Kept Display Name"

    # Verify password login still works afterward
    pw_login = await client.post(
        "/api/v1/auth/login",
        json={"email": "autolink@gmail.com", "password": "MySecretPassword123!"},
    )
    assert pw_login.status_code == 200
    assert pw_login.json()["user"]["google_linked"] is True
    assert pw_login.json()["user"]["has_password"] is True


# ---------------------------------------------------------------------------
# 4. Conflicting Google Binding
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_google_login_conflicting_google_sub_returns_409(
    client: AsyncClient,
    test_db: AsyncSession,
):
    """If an existing account with the same email already has a different google_sub, reject with 409."""
    user = await register_user(
        test_db,
        email="bound@gmail.com",
        google_sub="g_original_sub",
    )
    user_id = user.id

    with patch("src.api.auth.verify_google_token", new_callable=AsyncMock) as mock_v:
        mock_v.return_value = make_google_info(
            google_sub="g_different_attacker_sub",
            email="bound@gmail.com",
        )
        response = await client.post(
            "/api/v1/auth/google/login",
            json={"credential": "valid.google.jwt"},
        )

    assert response.status_code == 409
    assert "already linked" in response.json()["detail"].lower()

    # Verify existing binding was not mutated
    await test_db.commit()
    test_db.expire_all()
    refreshed = (await test_db.execute(select(User).where(User.id == user_id))).scalar_one()
    assert refreshed.google_sub == "g_original_sub"


@pytest.mark.asyncio
async def test_google_login_case_variant_legacy_email_collision_fails_closed(
    client: AsyncClient,
    test_db: AsyncSession,
):
    """A malformed case-variant legacy database must not select an arbitrary user."""
    from src.core.security import get_password_hash

    first = User(email="CaseCollision@gmail.com", password_hash=get_password_hash("Password123!"))
    second = User(email="casecollision@gmail.com", password_hash=get_password_hash("Password123!"))
    test_db.add_all([first, second])
    await test_db.commit()
    first_id = first.id
    second_id = second.id

    with patch("src.api.auth.verify_google_token", new_callable=AsyncMock) as mock_v:
        mock_v.return_value = make_google_info(
            google_sub="g_case_collision",
            email="CASECOLLISION@GMAIL.COM",
        )
        response = await client.post(
            "/api/v1/auth/google/login",
            json={"credential": "valid.google.jwt"},
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "Email identity conflict."
    test_db.expire_all()
    users = (await test_db.execute(select(User).where(User.id.in_([first_id, second_id])))).scalars().all()
    assert all(user.google_sub is None for user in users)


# ---------------------------------------------------------------------------
# 5. First-Time Google-Native User Creation (Case 3)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_google_login_creates_google_native_user(
    client: AsyncClient,
    test_db: AsyncSession,
):
    """When no sub or email match exists, a new Google-native user is created and logged in."""
    await register_user(
        test_db,
        email="taken-username@gmail.com",
        username="google-native-person",
    )
    count_before = (await test_db.execute(select(func.count(User.id)))).scalar_one()

    with patch("src.api.auth.verify_google_token", new_callable=AsyncMock) as mock_v:
        mock_v.return_value = make_google_info(
            google_sub="g_fresh_sub_789",
            email="newgoogleuser@gmail.com",
            name="Google Native Person",
        )
        response = await client.post(
            "/api/v1/auth/google/login",
            json={"credential": "valid.google.jwt"},
        )

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["user"]["email"] == "newgoogleuser@gmail.com"
    assert data["user"]["google_linked"] is True
    assert data["user"]["has_password"] is False
    assert "google_sub" not in data["user"]
    assert "password_hash" not in data["user"]

    # Check user in DB
    await test_db.commit()
    test_db.expire_all()

    count_after = (await test_db.execute(select(func.count(User.id)))).scalar_one()
    assert count_after == count_before + 1

    created_user = (
        await test_db.execute(select(User).where(User.google_sub == "g_fresh_sub_789"))
    ).scalar_one()
    assert created_user.email == "newgoogleuser@gmail.com"
    assert created_user.password_hash is None  # Google-native: no password
    assert created_user.role == "member"
    # Google-native accounts deliberately have no username, so an existing
    # username derived from the display name cannot make account creation fail.
    assert created_user.username is None

    google_native_me = await client.get(
        "/api/v1/auth/me",
        headers=auth_headers(created_user),
    )
    assert google_native_me.status_code == 200
    assert google_native_me.json()["google_linked"] is True
    assert google_native_me.json()["has_password"] is False
    assert "google_sub" not in google_native_me.json()
    assert "password_hash" not in google_native_me.json()

    # Second login with same Google account returns same User
    with patch("src.api.auth.verify_google_token", new_callable=AsyncMock) as mock_v:
        mock_v.return_value = make_google_info(
            google_sub="g_fresh_sub_789",
            email="newgoogleuser@gmail.com",
        )
        response2 = await client.post(
            "/api/v1/auth/google/login",
            json={"credential": "valid.google.jwt"},
        )
    assert response2.status_code == 200
    assert response2.json()["user"]["id"] == created_user.id

    count_third = (await test_db.execute(select(func.count(User.id)))).scalar_one()
    assert count_third == count_after  # No duplicate created


# ---------------------------------------------------------------------------
# 6. Race Safety & IntegrityError Recovery
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_google_login_auto_create_integrity_error_reconciles_to_canonical_user(
    client: AsyncClient,
    test_db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
):
    """A real unique-constraint race rolls back and returns the canonical account."""
    from src.api import auth as routes_module

    canonical = await register_user(
        test_db,
        email="racecreate@gmail.com",
        password=None,
        google_sub="g_race_create",
    )
    original_by_sub = routes_module._user_by_google_sub
    original_by_email = routes_module._user_by_google_email
    lookup_counts = {"sub": 0, "email": 0}

    async def hide_first_sub_lookup(db: AsyncSession, google_sub: str) -> User | None:
        lookup_counts["sub"] += 1
        if lookup_counts["sub"] == 1:
            return None
        return await original_by_sub(db, google_sub)

    async def hide_first_email_lookup(db: AsyncSession, email: str) -> User | None:
        lookup_counts["email"] += 1
        if lookup_counts["email"] == 1:
            return None
        return await original_by_email(db, email)

    # Simulate the only gap that remains after the initial reads: another
    # transaction committed the canonical user just before our INSERT.
    monkeypatch.setattr(routes_module, "_user_by_google_sub", hide_first_sub_lookup)
    monkeypatch.setattr(routes_module, "_user_by_google_email", hide_first_email_lookup)
    with patch("src.api.auth.verify_google_token", new_callable=AsyncMock) as mock_v:
        mock_v.return_value = make_google_info(
            google_sub="g_race_create",
            email="racecreate@gmail.com",
        )
        response = await client.post(
            "/api/v1/auth/google/login",
            json={"credential": "valid.google.jwt"},
        )

    assert response.status_code == 200
    assert response.json()["user"]["id"] == canonical.id
    # The test-db session can still execute after the endpoint's rollback and
    # there is still exactly one owner of both unique identities.
    users = (await test_db.execute(select(User).where(User.email == "racecreate@gmail.com"))).scalars().all()
    assert [user.id for user in users] == [canonical.id]
    assert (await test_db.execute(select(func.count(User.id)))).scalar_one() == 1


@pytest.mark.asyncio
async def test_google_auto_link_claim_is_compare_and_swap_and_cannot_rebind(
    test_db: AsyncSession,
):
    """Two requests with different subjects cannot overwrite the first claim."""
    from src.api.auth import _claim_google_sub

    user = await register_user(test_db, email="racer@gmail.com", google_sub=None)
    user_id = user.id
    winner = await _claim_google_sub(
        test_db,
        user_id=user_id,
        google_sub="g_first_subject",
    )
    loser = await _claim_google_sub(
        test_db,
        user_id=user.id,
        google_sub="g_second_subject",
    )

    assert winner is not None
    assert loser is None
    persisted = (await test_db.execute(select(User).where(User.id == user_id))).scalar_one()
    assert persisted.google_sub == "g_first_subject"


@pytest.mark.asyncio
async def test_google_auto_link_integrity_error_rolls_back_and_session_remains_usable(
    test_db: AsyncSession,
):
    """A duplicate-sub IntegrityError is recovered without poisoning the session."""
    from src.api.auth import _claim_google_sub

    target = await register_user(test_db, email="race-target@gmail.com", google_sub=None)
    owner = await register_user(
        test_db,
        email="race-owner@gmail.com",
        google_sub="g_already_claimed",
    )
    target_id = target.id
    owner_id = owner.id

    result = await _claim_google_sub(
        test_db,
        user_id=target_id,
        google_sub="g_already_claimed",
    )

    assert result is None
    # A query after the failed UPDATE proves rollback occurred and subsequent
    # work does not raise PendingRollbackError.
    users = (await test_db.execute(select(User).where(User.id.in_([target_id, owner_id])))).scalars().all()
    by_id = {user.id: user for user in users}
    assert by_id[target_id].google_sub is None
    assert by_id[owner_id].google_sub == "g_already_claimed"


@pytest.mark.asyncio
async def test_database_rejects_removing_a_google_native_users_only_provider(
    test_db: AsyncSession,
):
    """The database constraint protects lockout safety beyond request state."""
    user = await register_user(
        test_db,
        email="db-guard@gmail.com",
        password=None,
        google_sub="g_db_guard",
    )
    user_id = user.id

    with pytest.raises(IntegrityError):
        await test_db.execute(
            User.__table__.update().where(User.id == user_id).values(google_sub=None)
        )
    await test_db.rollback()
    persisted = (await test_db.execute(select(User).where(User.id == user_id))).scalar_one()
    assert persisted.google_sub == "g_db_guard"


# ---------------------------------------------------------------------------
# 7. Password Behavior
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_password_login_for_google_only_user_returns_401_generic(
    client: AsyncClient,
    test_db: AsyncSession,
):
    """A Google-only user (password_hash=None) receives standard 401 on password login, never crashes."""
    user = await register_user(
        test_db,
        email="googleonly@gmail.com",
        password=None,
        google_sub="g_only_sub",
    )
    assert user.password_hash is None

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "googleonly@gmail.com", "password": "AnyAttemptedPassword123!"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"


# ---------------------------------------------------------------------------
# 8. Unlink Behavior & Lockout Protection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_google_unlink_password_backed_user_success(
    client: AsyncClient,
    test_db: AsyncSession,
):
    """A user with a password can unlink their Google account."""
    user = await register_user(
        test_db,
        email="unlinkable@gmail.com",
        password="MyPassword123!",
        google_sub="g_unlink_me",
    )
    user_id = user.id

    response = await client.delete(
        "/api/v1/auth/me/google/link",
        headers=auth_headers(user),
    )

    assert response.status_code == 200
    assert response.json()["google_linked"] is False

    await test_db.commit()
    test_db.expire_all()
    refreshed = (await test_db.execute(select(User).where(User.id == user_id))).scalar_one()
    assert refreshed.google_sub is None


@pytest.mark.asyncio
async def test_google_unlink_google_only_user_rejected_with_409(
    client: AsyncClient,
    test_db: AsyncSession,
):
    """A Google-only user (password_hash=None) cannot unlink their only login method."""
    user = await register_user(
        test_db,
        email="googleonly_unlink@gmail.com",
        password=None,
        google_sub="g_cant_unlink",
    )
    user_id = user.id

    response = await client.delete(
        "/api/v1/auth/me/google/link",
        headers=auth_headers(user),
    )

    assert response.status_code == 409
    assert "password" in response.json()["detail"].lower()

    # Verify google_sub was NOT removed
    await test_db.commit()
    test_db.expire_all()
    refreshed = (await test_db.execute(select(User).where(User.id == user_id))).scalar_one()
    assert refreshed.google_sub == "g_cant_unlink"


@pytest.mark.asyncio
async def test_google_unlink_idempotent_when_already_unlinked(
    client: AsyncClient,
    test_db: AsyncSession,
):
    """Unlinking an already unlinked account returns 200 with google_linked: false."""
    user = await register_user(
        test_db, email="already_unlinked@gmail.com", password="Pass", google_sub=None
    )

    response = await client.delete(
        "/api/v1/auth/me/google/link",
        headers=auth_headers(user),
    )
    assert response.status_code == 200
    assert response.json()["google_linked"] is False


# ---------------------------------------------------------------------------
# 9. Response Privacy & Derived State
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_response_privacy_never_exposes_google_sub_or_password_hash(
    client: AsyncClient,
    test_db: AsyncSession,
):
    """Neither google_sub nor password_hash must leak in any API response."""
    user = await register_user(
        test_db,
        email="privacy_check@gmail.com",
        password="MySecretPassword123!",
        google_sub="g_super_secret_sub",
    )

    def assert_safe_user(payload: dict[str, object], *, linked: bool, has_password: bool) -> None:
        assert "google_sub" not in payload
        assert "password_hash" not in payload
        assert payload["google_linked"] is linked
        assert payload["has_password"] is has_password

    # 1. Password login response
    password_login = await client.post(
        "/api/v1/auth/login",
        json={"email": "privacy_check@gmail.com", "password": "MySecretPassword123!"},
    )
    assert password_login.status_code == 200
    password_data = password_login.json()
    assert_safe_user(password_data["user"], linked=True, has_password=True)

    # 2. Google login response
    with patch("src.api.auth.verify_google_token", new_callable=AsyncMock) as mock_v:
        mock_v.return_value = make_google_info(
            google_sub="g_super_secret_sub",
            email="privacy_check@gmail.com",
        )
        google_login = await client.post(
            "/api/v1/auth/google/login",
            json={"credential": "valid.google.jwt"},
        )
    assert google_login.status_code == 200
    assert_safe_user(google_login.json()["user"], linked=True, has_password=True)

    # 3. Refresh response
    refresh = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": password_data["refresh_token"]},
    )
    assert refresh.status_code == 200
    assert_safe_user(refresh.json()["user"], linked=True, has_password=True)

    # 4. GET /auth/me
    resp_me = await client.get("/api/v1/auth/me", headers=auth_headers(user))
    assert resp_me.status_code == 200
    assert_safe_user(resp_me.json(), linked=True, has_password=True)

    # 5. PUT /auth/me/language
    resp_lang = await client.put(
        "/api/v1/auth/me/language",
        json={"preferred_language": "ja"},
        headers=auth_headers(user),
    )
    assert resp_lang.status_code == 200
    assert_safe_user(resp_lang.json(), linked=True, has_password=True)

    # 6. PUT /auth/me/interface-language
    resp_interface = await client.put(
        "/api/v1/auth/me/interface-language",
        json={"interface_language": "de"},
        headers=auth_headers(user),
    )
    assert resp_interface.status_code == 200
    assert_safe_user(resp_interface.json(), linked=True, has_password=True)


# ---------------------------------------------------------------------------
# 10. Explicit Link from Settings
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_explicit_google_link_from_settings_success(
    client: AsyncClient,
    test_db: AsyncSession,
):
    """An authenticated user can link Google from Settings even if emails differ."""
    user = await register_user(
        test_db, email="linguauser@example.com", password="Password123!", google_sub=None
    )
    user_id = user.id

    with patch("src.api.auth.verify_google_token", new_callable=AsyncMock) as mock_v:
        mock_v.return_value = make_google_info(
            google_sub="g_explicit_link",
            email="differentgoogleemail@gmail.com",
            email_verified=True,
        )
        response = await client.post(
            "/api/v1/auth/me/google/link",
            json={"credential": "valid.google.jwt"},
            headers=auth_headers(user),
        )

    assert response.status_code == 200
    assert response.json()["google_linked"] is True

    await test_db.commit()
    test_db.expire_all()
    refreshed = (await test_db.execute(select(User).where(User.id == user_id))).scalar_one()
    assert refreshed.google_sub == "g_explicit_link"


@pytest.mark.asyncio
async def test_explicit_google_link_conflict_keeps_both_bindings_unchanged(
    client: AsyncClient,
    test_db: AsyncSession,
):
    """A Settings-link conflict is a stable 409 after the failed claim rollback."""
    user_a = await register_user(
        test_db,
        email="settings-link-a@example.com",
        google_sub=None,
    )
    user_b = await register_user(
        test_db,
        email="settings-link-b@example.com",
        google_sub="g_owned_by_user_b",
    )
    user_a_id = user_a.id
    user_b_id = user_b.id

    with patch("src.api.auth.verify_google_token", new_callable=AsyncMock) as mock_v:
        mock_v.return_value = make_google_info(
            google_sub="g_owned_by_user_b",
            email="different-google-email@example.com",
        )
        response = await client.post(
            "/api/v1/auth/me/google/link",
            json={"credential": "valid.google.jwt"},
            headers=auth_headers(user_a),
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "This Google account is already linked to another user."

    test_db.expire_all()
    users = (
        await test_db.execute(select(User).where(User.id.in_([user_a_id, user_b_id])))
    ).scalars().all()
    bindings = {user.id: user.google_sub for user in users}
    assert bindings[user_a_id] is None
    assert bindings[user_b_id] == "g_owned_by_user_b"
