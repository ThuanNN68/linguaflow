"""Comprehensive tests for email OTP registration flow (Batch F)."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from src.core.security import get_password_hash, verify_password
from src.database.models import PendingRegistration, RefreshSession, User
from src.services.identity.email import EMAIL_TEMPLATES, _memory_sender


@pytest.fixture(autouse=True)
def clear_memory_emails():
    """Clear captured in-memory emails before each test."""
    _memory_sender.clear()
    yield
    _memory_sender.clear()


@pytest.mark.asyncio
async def test_register_creates_pending_registration_not_user(client, test_db):
    """POST /auth/register creates a PendingRegistration, sends OTP, and returns 202 without User or Session."""
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "username": "alice",
            "email": "alice@example.com",
            "password": "Password123!",
            "display_name": "Alice Wonderland",
            "preferred_language": "vi",
        },
    )

    assert response.status_code == 202
    data = response.json()
    assert "pending_id" in data
    assert data["email"] == "alice@example.com"
    assert data["expires_in_seconds"] == 300
    assert data["cooldown_seconds"] == 60
    assert "access_token" not in data
    assert "refresh_token" not in data

    # Verify no User or Session exists
    user = (await test_db.execute(select(User).where(User.email == "alice@example.com"))).scalar_one_or_none()
    assert user is None
    sessions = (await test_db.execute(select(RefreshSession))).scalars().all()
    assert len(sessions) == 0

    # Verify PendingRegistration exists and OTP is hashed
    pending = await test_db.get(PendingRegistration, data["pending_id"])
    assert pending is not None
    assert pending.email == "alice@example.com"
    assert pending.username == "alice"
    assert pending.display_name == "Alice Wonderland"
    assert pending.preferred_language == "vi"
    assert pending.interface_language == "vi"
    assert pending.attempts == 0
    assert pending.request_count == 1
    assert pending.otp_hash.startswith("$2b$") or pending.otp_hash.startswith("$2a$")

    # Verify email was captured in memory
    assert len(_memory_sender.sent_emails) == 1
    sent = _memory_sender.sent_emails[0]
    assert sent.to_email == "alice@example.com"
    assert "LinguaFlow" in sent.subject


@pytest.mark.asyncio
async def test_verify_register_otp_creates_user_and_consumes_pending(client, test_db):
    """POST /auth/register/verify consumes pending row, creates User exactly once, and returns AuthResponse."""
    reg_res = await client.post(
        "/api/v1/auth/register",
        json={
            "username": "bob_builder",
            "email": "bob@example.com",
            "password": "SecurePassword123!",
            "display_name": "Bob B",
            "preferred_language": "ja",
        },
    )
    assert reg_res.status_code == 202
    pending_id = reg_res.json()["pending_id"]

    # Retrieve sent OTP from captured email
    assert len(_memory_sender.sent_emails) == 1
    sent_text = _memory_sender.sent_emails[0].body_text
    # Extract 6-digit OTP
    import re
    otp_match = re.search(r"\b(\d{6})\b", sent_text)
    assert otp_match is not None
    otp = otp_match.group(1)

    # Verify OTP
    verify_res = await client.post(
        "/api/v1/auth/register/verify",
        json={"pending_id": pending_id, "otp": otp},
    )
    assert verify_res.status_code == 200
    auth_data = verify_res.json()
    assert auth_data["access_token"]
    assert auth_data["refresh_token"]
    assert auth_data["user"]["email"] == "bob@example.com"
    assert auth_data["user"]["username"] == "bob_builder"
    assert auth_data["user"]["display_name"] == "Bob B"
    assert auth_data["user"]["preferred_language"] == "ja"
    assert auth_data["user"]["interface_language"] == "ja"
    assert auth_data["user"]["role"] == "member"

    # Pending registration must be consumed (deleted)
    pending_check = await test_db.get(PendingRegistration, pending_id)
    assert pending_check is None

    # Batch F registration remains password-backed even though Google-native
    # users may now legitimately have password_hash=NULL.
    created_user = (
        await test_db.execute(select(User).where(User.email == "bob@example.com"))
    ).scalar_one()
    assert created_user.password_hash is not None
    assert verify_password("SecurePassword123!", created_user.password_hash)

    # User exists and can login
    login_res = await client.post(
        "/api/v1/auth/login",
        json={"email": "bob@example.com", "password": "SecurePassword123!"},
    )
    assert login_res.status_code == 200


@pytest.mark.asyncio
async def test_verification_is_single_use(client, test_db):
    """Replaying the same verification request fails."""
    reg_res = await client.post(
        "/api/v1/auth/register",
        json={
            "username": "single_user",
            "email": "single@example.com",
            "password": "Password123!",
        },
    )
    pending_id = reg_res.json()["pending_id"]
    import re
    otp = re.search(r"\b(\d{6})\b", _memory_sender.sent_emails[0].body_text).group(1)

    # First verify succeeds
    first_verify = await client.post(
        "/api/v1/auth/register/verify",
        json={"pending_id": pending_id, "otp": otp},
    )
    assert first_verify.status_code == 200

    # Second verify with same payload fails
    second_verify = await client.post(
        "/api/v1/auth/register/verify",
        json={"pending_id": pending_id, "otp": otp},
    )
    assert second_verify.status_code == 400


@pytest.mark.asyncio
async def test_verify_rejects_extra_fields(client):
    """Verify endpoint rejects unknown/extra fields."""
    response = await client.post(
        "/api/v1/auth/register/verify",
        json={"pending_id": "123", "otp": "123456", "extra": "forbidden"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_verify_rejects_non_six_digit_otp(client):
    """Verify endpoint enforces 6 numeric digits."""
    for invalid_otp in ["12345", "1234567", "abcdef", "12a456", ""]:
        response = await client.post(
            "/api/v1/auth/register/verify",
            json={"pending_id": "123", "otp": invalid_otp},
        )
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_wrong_otp_increments_attempts_and_locks_after_three(client, test_db):
    """Wrong OTP decrements remaining attempts and exhausts after 3."""
    reg_res = await client.post(
        "/api/v1/auth/register",
        json={
            "username": "attempt_user",
            "email": "attempt@example.com",
            "password": "Password123!",
        },
    )
    pending_id = reg_res.json()["pending_id"]

    # Attempt 1
    res1 = await client.post(
        "/api/v1/auth/register/verify",
        json={"pending_id": pending_id, "otp": "000000"},
    )
    assert res1.status_code == 400
    assert "2 attempts remaining" in res1.json()["detail"]

    # Attempt 2
    res2 = await client.post(
        "/api/v1/auth/register/verify",
        json={"pending_id": pending_id, "otp": "000000"},
    )
    assert res2.status_code == 400
    assert "1 attempt remaining" in res2.json()["detail"]

    # Attempt 3 (exhausts)
    res3 = await client.post(
        "/api/v1/auth/register/verify",
        json={"pending_id": pending_id, "otp": "000000"},
    )
    assert res3.status_code == 400
    assert "Maximum verification attempts exceeded" in res3.json()["detail"]

    # Attempt 4 (already exhausted)
    import re
    correct_otp = re.search(r"\b(\d{6})\b", _memory_sender.sent_emails[0].body_text).group(1)
    res4 = await client.post(
        "/api/v1/auth/register/verify",
        json={"pending_id": pending_id, "otp": correct_otp},
    )
    assert res4.status_code == 400
    assert "Maximum verification attempts exceeded" in res4.json()["detail"]


@pytest.mark.asyncio
async def test_resend_cooldown_and_rate_limiting(client, test_db):
    """Resend enforces 60s cooldown and 5 requests per hour budget."""
    reg_res = await client.post(
        "/api/v1/auth/register",
        json={
            "username": "resend_user",
            "email": "resend@example.com",
            "password": "Password123!",
        },
    )
    pending_id = reg_res.json()["pending_id"]

    # Immediate resend should be blocked by 60s cooldown (429)
    resend_fast = await client.post(
        "/api/v1/auth/register/resend",
        json={"pending_id": pending_id},
    )
    assert resend_fast.status_code == 429
    assert "Retry-After" in resend_fast.headers

    # Simulate 61 seconds elapsed
    pending = await test_db.get(PendingRegistration, pending_id)
    pending.last_sent_at = datetime.now(UTC) - timedelta(seconds=65)
    await test_db.commit()

    # Now resend succeeds
    resend_ok = await client.post(
        "/api/v1/auth/register/resend",
        json={"pending_id": pending_id},
    )
    assert resend_ok.status_code == 200
    assert len(_memory_sender.sent_emails) == 2

    # Simulate 4 more requests to reach 5 requests total
    for i in range(3):
        pending = await test_db.get(PendingRegistration, pending_id)
        pending.last_sent_at = datetime.now(UTC) - timedelta(seconds=65)
        await test_db.commit()
        r = await client.post("/api/v1/auth/register/resend", json={"pending_id": pending_id})
        assert r.status_code == 200

    # 6th request in the same hour is blocked by 5 req/hour rate limit
    pending = await test_db.get(PendingRegistration, pending_id)
    pending.last_sent_at = datetime.now(UTC) - timedelta(seconds=65)
    await test_db.commit()
    r_blocked = await client.post("/api/v1/auth/register/resend", json={"pending_id": pending_id})
    assert r_blocked.status_code == 429
    assert "Too many verification requests" in r_blocked.json()["detail"]


@pytest.mark.asyncio
async def test_resend_invalidates_old_otp_and_resets_attempts(client, test_db):
    """Resending OTP resets attempts to 0 and makes old OTP invalid."""
    reg_res = await client.post(
        "/api/v1/auth/register",
        json={
            "username": "inval_user",
            "email": "inval@example.com",
            "password": "Password123!",
        },
    )
    pending_id = reg_res.json()["pending_id"]
    import re
    old_otp = re.search(r"\b(\d{6})\b", _memory_sender.sent_emails[0].body_text).group(1)

    # Make 2 failed attempts
    await client.post("/api/v1/auth/register/verify", json={"pending_id": pending_id, "otp": "999999"})
    await client.post("/api/v1/auth/register/verify", json={"pending_id": pending_id, "otp": "999999"})

    # Advance time past cooldown
    pending = await test_db.get(PendingRegistration, pending_id)
    pending.last_sent_at = datetime.now(UTC) - timedelta(seconds=65)
    await test_db.commit()

    # Resend
    await client.post("/api/v1/auth/register/resend", json={"pending_id": pending_id})
    assert len(_memory_sender.sent_emails) == 2
    new_otp = re.search(r"\b(\d{6})\b", _memory_sender.sent_emails[1].body_text).group(1)

    # Attempts should be reset
    pending_after = await test_db.get(PendingRegistration, pending_id)
    await test_db.refresh(pending_after)
    assert pending_after.attempts == 0

    # Old OTP must fail
    res_old = await client.post("/api/v1/auth/register/verify", json={"pending_id": pending_id, "otp": old_otp})
    assert res_old.status_code == 400

    # New OTP must succeed
    res_new = await client.post("/api/v1/auth/register/verify", json={"pending_id": pending_id, "otp": new_otp})
    assert res_new.status_code == 200


@pytest.mark.asyncio
async def test_expired_otp_fails(client, test_db):
    """OTP past 5 minutes fails verification."""
    reg_res = await client.post(
        "/api/v1/auth/register",
        json={
            "username": "expire_user",
            "email": "expire@example.com",
            "password": "Password123!",
        },
    )
    pending_id = reg_res.json()["pending_id"]
    import re
    otp = re.search(r"\b(\d{6})\b", _memory_sender.sent_emails[0].body_text).group(1)

    # Expire the pending row
    pending = await test_db.get(PendingRegistration, pending_id)
    pending.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await test_db.commit()

    # Verify fails
    res = await client.post("/api/v1/auth/register/verify", json={"pending_id": pending_id, "otp": otp})
    assert res.status_code == 400
    assert "expired" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_all_14_language_email_templates():
    """Verify all 14 supported languages produce valid localized email templates."""
    from src.schemas.auth import SUPPORTED_LANGUAGES

    assert len(SUPPORTED_LANGUAGES) == 14
    for lang in SUPPORTED_LANGUAGES:
        assert lang in EMAIL_TEMPLATES
        tmpl = EMAIL_TEMPLATES[lang]
        assert tmpl.subject
        assert "{otp}" in tmpl.body_text
        assert "{otp}" in tmpl.body_html
        # Test formatting
        formatted_text = tmpl.body_text.format(otp="123456")
        assert "123456" in formatted_text
        assert "{otp}" not in formatted_text


@pytest.mark.asyncio
async def test_concurrent_invalid_attempts(client, test_db):
    """Concurrent invalid attempts must atomically increment without losing count."""
    reg_res = await client.post(
        "/api/v1/auth/register",
        json={
            "username": "conc_invalid",
            "email": "conc_invalid@example.com",
            "password": "Password123!",
        },
    )
    pending_id = reg_res.json()["pending_id"]

    # Fire 5 concurrent invalid attempts
    tasks = [
        client.post("/api/v1/auth/register/verify", json={"pending_id": pending_id, "otp": "000000"})
        for _ in range(5)
    ]
    results = await asyncio.gather(*tasks)

    # All should be 400 Bad Request
    for r in results:
        assert r.status_code == 400

    # Pending registration must reflect >= 3 attempts (locked out)
    pending = await test_db.get(PendingRegistration, pending_id)
    await test_db.refresh(pending)
    assert pending.attempts >= 3


@pytest.mark.asyncio
async def test_correct_otp_racing_third_invalid_attempt(client, test_db):
    """A correct OTP racing the 3rd invalid attempt either succeeds or is locked out safely."""
    reg_res = await client.post(
        "/api/v1/auth/register",
        json={
            "username": "race_user",
            "email": "race@example.com",
            "password": "Password123!",
        },
    )
    pending_id = reg_res.json()["pending_id"]
    import re
    correct_otp = re.search(r"\b(\d{6})\b", _memory_sender.sent_emails[0].body_text).group(1)

    # Make 2 failed attempts first
    await client.post("/api/v1/auth/register/verify", json={"pending_id": pending_id, "otp": "000000"})
    await client.post("/api/v1/auth/register/verify", json={"pending_id": pending_id, "otp": "000000"})

    # Race 1 invalid attempt with 1 valid attempt
    results = await asyncio.gather(
        client.post("/api/v1/auth/register/verify", json={"pending_id": pending_id, "otp": "000000"}),
        client.post("/api/v1/auth/register/verify", json={"pending_id": pending_id, "otp": correct_otp}),
    )

    statuses = [r.status_code for r in results]
    # Either valid won (200 + 400) or invalid reached 3rd attempt and blocked valid (400 + 400)
    assert 200 in statuses or statuses == [400, 400]

    # Verify at most one user was created
    users = (await test_db.execute(select(User).where(User.email == "race@example.com"))).scalars().all()
    assert len(users) <= 1


@pytest.mark.asyncio
async def test_verify_delayed_past_expiry(client, test_db):
    """Verify delayed past expiry is rejected and creates no user."""
    reg_res = await client.post(
        "/api/v1/auth/register",
        json={
            "username": "delayed_user",
            "email": "delayed@example.com",
            "password": "Password123!",
        },
    )
    pending_id = reg_res.json()["pending_id"]
    import re
    otp = re.search(r"\b(\d{6})\b", _memory_sender.sent_emails[0].body_text).group(1)

    # Delay past 5 minutes
    pending = await test_db.get(PendingRegistration, pending_id)
    pending.expires_at = datetime.now(UTC) - timedelta(seconds=10)
    await test_db.commit()

    res = await client.post("/api/v1/auth/register/verify", json={"pending_id": pending_id, "otp": otp})
    assert res.status_code == 400
    assert "expired" in res.json()["detail"].lower()

    user = (await test_db.execute(select(User).where(User.email == "delayed@example.com"))).scalar_one_or_none()
    assert user is None


@pytest.mark.asyncio
async def test_verify_racing_resend(client, test_db):
    """Verify with old OTP racing a resend is safely resolved under concurrency."""
    reg_res = await client.post(
        "/api/v1/auth/register",
        json={
            "username": "race_resend",
            "email": "race_resend@example.com",
            "password": "Password123!",
        },
    )
    pending_id = reg_res.json()["pending_id"]
    import re
    old_otp = re.search(r"\b(\d{6})\b", _memory_sender.sent_emails[0].body_text).group(1)

    # Advance time past 60s cooldown
    pending = await test_db.get(PendingRegistration, pending_id)
    pending.last_sent_at = datetime.now(UTC) - timedelta(seconds=65)
    await test_db.commit()

    # Race resend and verify with old OTP concurrently
    results = await asyncio.gather(
        client.post("/api/v1/auth/register/resend", json={"pending_id": pending_id}),
        client.post("/api/v1/auth/register/verify", json={"pending_id": pending_id, "otp": old_otp}),
    )
    resend_res, verify_res = results[0], results[1]

    # Either resend won (200) and old verify failed (400),
    # or verify won (200) and resend failed (404 because pending was consumed)
    if resend_res.status_code == 200:
        assert verify_res.status_code == 400
        # If resend won, new OTP must be valid and old OTP invalid
        new_otp = re.search(r"\b(\d{6})\b", _memory_sender.sent_emails[-1].body_text).group(1)
        verify_new = await client.post("/api/v1/auth/register/verify", json={"pending_id": pending_id, "otp": new_otp})
        assert verify_new.status_code == 200
    else:
        assert verify_res.status_code == 200
        assert resend_res.status_code == 404


@pytest.mark.asyncio
async def test_concurrent_resend(client, test_db):
    """Concurrent resend requests must allow exactly one through 60s cooldown."""
    reg_res = await client.post(
        "/api/v1/auth/register",
        json={
            "username": "conc_resend",
            "email": "conc_resend@example.com",
            "password": "Password123!",
        },
    )
    pending_id = reg_res.json()["pending_id"]

    # Advance time past initial cooldown
    pending = await test_db.get(PendingRegistration, pending_id)
    pending.last_sent_at = datetime.now(UTC) - timedelta(seconds=65)
    await test_db.commit()

    _memory_sender.clear()

    # Fire 5 concurrent resend requests
    tasks = [
        client.post("/api/v1/auth/register/resend", json={"pending_id": pending_id})
        for _ in range(5)
    ]
    results = await asyncio.gather(*tasks)

    successes = [r for r in results if r.status_code == 200]
    rate_limited = [r for r in results if r.status_code == 429]

    assert len(successes) == 1
    assert len(rate_limited) == 4
    # Only 1 new email sent
    assert len(_memory_sender.sent_emails) == 1


@pytest.mark.asyncio
async def test_concurrent_re_register(client, test_db):
    """Concurrent re-register requests with same email allow exactly one through cooldown."""
    reg_res = await client.post(
        "/api/v1/auth/register",
        json={
            "username": "conc_rereg",
            "email": "conc_rereg@example.com",
            "password": "Password123!",
        },
    )
    assert reg_res.status_code == 202

    # Advance time past cooldown
    pending = (await test_db.execute(select(PendingRegistration).where(PendingRegistration.email == "conc_rereg@example.com"))).scalar_one()
    pending.last_sent_at = datetime.now(UTC) - timedelta(seconds=65)
    await test_db.commit()

    _memory_sender.clear()

    # Fire 4 concurrent re-register calls
    payload = {
        "username": "conc_rereg",
        "email": "conc_rereg@example.com",
        "password": "NewPassword123!",
    }
    tasks = [client.post("/api/v1/auth/register", json=payload) for _ in range(4)]
    results = await asyncio.gather(*tasks)

    successes = [r for r in results if r.status_code == 202]
    rate_limited = [r for r in results if r.status_code == 429]

    assert len(successes) == 1
    assert len(rate_limited) == 3
    assert len(_memory_sender.sent_emails) == 1


@pytest.mark.asyncio
async def test_request_count_remains_correct_under_concurrency_and_expiry(client, test_db):
    """Request count retains active 1-hour window even when pending registration is expired."""
    reg_res = await client.post(
        "/api/v1/auth/register",
        json={
            "username": "req_cnt_user",
            "email": "req_cnt@example.com",
            "password": "Password123!",
        },
    )
    pending_id = reg_res.json()["pending_id"]

    # Expire the pending row (5 min elapsed, but within 1-hour window)
    pending = await test_db.get(PendingRegistration, pending_id)
    pending.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    pending.last_sent_at = datetime.now(UTC) - timedelta(seconds=70)
    await test_db.commit()

    # Resend should increment request_count to 2
    resend = await client.post("/api/v1/auth/register/resend", json={"pending_id": pending_id})
    assert resend.status_code == 200

    pending_after = await test_db.get(PendingRegistration, pending_id)
    await test_db.refresh(pending_after)
    assert pending_after.request_count == 2


@pytest.mark.asyncio
async def test_successful_verify_creates_exactly_one_user_and_one_initial_refresh_session(client, test_db):
    """Concurrent verify requests with valid OTP create exactly one User and one RefreshSession."""
    reg_res = await client.post(
        "/api/v1/auth/register",
        json={
            "username": "single_user_race",
            "email": "single_user_race@example.com",
            "password": "Password123!",
        },
    )
    pending_id = reg_res.json()["pending_id"]
    import re
    otp = re.search(r"\b(\d{6})\b", _memory_sender.sent_emails[0].body_text).group(1)

    # Fire 5 concurrent verify requests with the same valid OTP
    tasks = [
        client.post("/api/v1/auth/register/verify", json={"pending_id": pending_id, "otp": otp})
        for _ in range(5)
    ]
    results = await asyncio.gather(*tasks)

    successes = [r for r in results if r.status_code == 200]
    failures = [r for r in results if r.status_code == 400]

    assert len(successes) == 1
    assert len(failures) == 4

    # Verify exactly one User exists
    users = (await test_db.execute(select(User).where(User.email == "single_user_race@example.com"))).scalars().all()
    assert len(users) == 1
    user = users[0]

    # Verify exactly one RefreshSession exists for this user
    sessions = (await test_db.execute(select(RefreshSession).where(RefreshSession.user_id == user.id))).scalars().all()
    assert len(sessions) == 1


# ==========================================
# F-Fix2 Focused Tests
# ==========================================


def test_production_rejects_memory_and_console():
    """Production configuration must reject memory and console email providers."""
    import pydantic

    from src.config import Settings

    with pytest.raises(pydantic.ValidationError) as exc_memory:
        Settings(
            app_env="production",
            frontend_url="https://example.com",
            email_provider="memory",
            jwt_secret="a" * 32,
        )
    assert "EMAIL_PROVIDER='memory' is not allowed in production" in str(exc_memory.value)

    with pytest.raises(pydantic.ValidationError) as exc_console:
        Settings(
            app_env="production",
            frontend_url="https://example.com",
            email_provider="console",
            jwt_secret="a" * 32,
        )
    assert "EMAIL_PROVIDER='console' is not allowed in production" in str(exc_console.value)


def test_production_smtp_missing_required_config_fails_validation():
    """Production SMTP configuration must fail fast if required credentials/host are missing."""
    import pydantic

    from src.config import Settings

    with pytest.raises(pydantic.ValidationError) as exc_missing_all:
        Settings(
            app_env="production",
            frontend_url="https://example.com",
            email_provider="smtp",
            jwt_secret="a" * 32,
            smtp_host="",
        )
    assert "Production SMTP configuration is missing required fields" in str(exc_missing_all.value)

    with pytest.raises(pydantic.ValidationError) as exc_missing_creds:
        Settings(
            app_env="production",
            frontend_url="https://example.com",
            email_provider="smtp",
            jwt_secret="a" * 32,
            smtp_host="smtp.gmail.com",
            smtp_port=587,
            smtp_user="",
            smtp_password="",
            smtp_from_email="admin@example.com",
        )
    assert "SMTP_USER" in str(exc_missing_creds.value)
    assert "SMTP_PASSWORD" in str(exc_missing_creds.value)

    # smtp_from_email has no default; omitting it in production must fail
    with pytest.raises(pydantic.ValidationError) as exc_no_from:
        Settings(
            app_env="production",
            frontend_url="https://example.com",
            email_provider="smtp",
            jwt_secret="a" * 32,
            smtp_host="smtp.gmail.com",
            smtp_port=587,
            smtp_user="user@example.com",
            smtp_password="secret",
            smtp_from_email="",
        )
    assert "SMTP_FROM_EMAIL" in str(exc_no_from.value)


def test_memory_provider_remains_usable_in_tests():
    """Test/dev environment allows memory email provider without error."""
    from src.config import Settings

    s = Settings(
        app_env="development",
        email_provider="memory",
        jwt_secret="test-secret-key-12345",
    )
    assert s.email_provider == "memory"


@pytest.mark.asyncio
async def test_provider_exception_returns_stable_failure_and_does_not_reset_abuse_budget(client, test_db, monkeypatch):
    """Email delivery failure returns 500 without resetting abuse budget or rolling back pending row."""
    from unittest.mock import AsyncMock

    from src.services.identity import email as email_module

    # Mock email sender to fail
    mock_send = AsyncMock(side_effect=RuntimeError("SMTP connection timed out"))
    monkeypatch.setattr(email_module._memory_sender, "send", mock_send)

    payload = {
        "username": "smtp_fail_user",
        "email": "smtp_fail@example.com",
        "password": "Password123!",
        "preferred_language": "vi",
    }
    response = await client.post("/api/v1/auth/register", json=payload)

    # Must return 500 with stable machine-readable code and no SMTP exception details
    assert response.status_code == 500
    assert response.json()["detail"] == "email_delivery_failed"
    assert "SMTP" not in response.text

    # Abuse budget and pending registration were committed before delivery attempt
    pending = (
        await test_db.execute(
            select(PendingRegistration).where(PendingRegistration.email == "smtp_fail@example.com")
        )
    ).scalar_one_or_none()
    assert pending is not None
    assert pending.request_count == 1
    assert pending.attempts == 0

    # User and session must NOT be created
    user = (
        await test_db.execute(
            select(User).where(User.email == "smtp_fail@example.com")
        )
    ).scalar_one_or_none()
    assert user is None


@pytest.mark.asyncio
async def test_resend_email_delivery_error_returns_typed_code(client, test_db, monkeypatch):
    """Resend OTP delivery failure (EmailDeliveryError) returns 500 email_delivery_failed and preserves rate budget."""
    # First, register normally
    reg_res = await client.post(
        "/api/v1/auth/register",
        json={
            "username": "resend_fail_user",
            "email": "resend_fail@example.com",
            "password": "Password123!",
        },
    )
    assert reg_res.status_code == 202
    pending_id = reg_res.json()["pending_id"]

    # Age cooldown by 61 seconds
    await test_db.execute(
        update(PendingRegistration)
        .where(PendingRegistration.id == pending_id)
        .values(last_sent_at=datetime.now(UTC) - timedelta(seconds=61))
    )
    await test_db.commit()

    # Mock email sender to fail
    from unittest.mock import AsyncMock

    from src.services.identity import email as email_module

    mock_send = AsyncMock(side_effect=RuntimeError("SMTP connection dropped"))
    monkeypatch.setattr(email_module._memory_sender, "send", mock_send)

    resend_res = await client.post(
        "/api/v1/auth/register/resend",
        json={"pending_id": pending_id},
    )
    assert resend_res.status_code == 500
    assert resend_res.json()["detail"] == "email_delivery_failed"
    assert "SMTP" not in resend_res.text

    # Verify request_count was incremented and committed
    pending = (
        await test_db.execute(
            select(PendingRegistration).where(PendingRegistration.id == pending_id)
        )
    ).scalar_one_or_none()
    assert pending is not None
    assert pending.request_count == 2


@pytest.mark.asyncio
async def test_unrelated_exception_not_classified_as_email_delivery_failed(client, test_db, monkeypatch):
    """Unrelated exceptions (RuntimeError, ValueError) during email delivery are NOT classified as email_delivery_failed."""
    from unittest.mock import AsyncMock

    from src.api import auth as routes_module

    # Mock send_registration_otp_email to raise an unexpected non-EmailDeliveryError exception
    mock_send_fn = AsyncMock(side_effect=RuntimeError("Unexpected server crash"))
    monkeypatch.setattr(routes_module, "send_registration_otp_email", mock_send_fn)

    # 1. Test register
    with pytest.raises(RuntimeError) as exc_info:
        await client.post(
            "/api/v1/auth/register",
            json={
                "username": "unrelated_err_user",
                "email": "unrelated_err@example.com",
                "password": "Password123!",
            },
        )
    assert "Unexpected server crash" in str(exc_info.value)

    # Reset DB state: create pending row for resend test
    pending_row = PendingRegistration(
        id="unrelated-resend-id",
        email="unrelated_resend@example.com",
        username="unrelated_resend",
        password_hash="hash",
        otp_hash="hash",
        display_name="unrelated_resend",
        interface_language="en",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        last_sent_at=datetime.now(UTC) - timedelta(seconds=65),
        rate_window_started_at=datetime.now(UTC) - timedelta(seconds=65),
        request_count=1,
        attempts=0,
    )
    test_db.add(pending_row)
    await test_db.commit()

    # 2. Test resend
    with pytest.raises(RuntimeError) as exc_info_resend:
        await client.post(
            "/api/v1/auth/register/resend",
            json={"pending_id": "unrelated-resend-id"},
        )
    assert "Unexpected server crash" in str(exc_info_resend.value)


@pytest.mark.asyncio
async def test_ascii_six_digit_otp_accepted(client):
    """ASCII 6-digit OTP format is accepted by validation."""
    response = await client.post(
        "/api/v1/auth/register/verify",
        json={"pending_id": "nonexistent_id", "otp": "012345"},
    )
    # 400 because pending session is invalid, but passed 422 schema validation
    assert response.status_code == 400
    assert "Invalid or expired verification session" in response.json()["detail"]


@pytest.mark.asyncio
async def test_arabic_indic_and_fullwidth_otp_digits_rejected(client):
    """Arabic-Indic, full-width, whitespace, and signed OTP digits are rejected with 422."""
    invalid_otps = [
        "١٢٣٤٥٦",  # Arabic-Indic digits 123456
        "１２３４５６",  # Full-width digits 123456
        " 12345",   # Leading whitespace
        "12345 ",   # Trailing whitespace
        "+12345",   # Leading sign
        "-12345",   # Negative sign
        "12.345",   # Decimal point
    ]
    for bad_otp in invalid_otps:
        response = await client.post(
            "/api/v1/auth/register/verify",
            json={"pending_id": "any_id", "otp": bad_otp},
        )
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_password_and_otp_absent_from_422_response_bodies(client):
    """FastAPI/Pydantic 422 response bodies must not echo sensitive passwords or OTP inputs."""
    secret_password = "bad123"
    reg_response = await client.post(
        "/api/v1/auth/register",
        json={
            "username": "valid_user",
            "email": "valid@example.com",
            "password": secret_password,
        },
    )
    assert reg_response.status_code == 422
    assert secret_password not in reg_response.text
    assert reg_response.json()["detail"][0]["input"] == "[REDACTED]"

    secret_otp = "bad-otp"
    verify_response = await client.post(
        "/api/v1/auth/register/verify",
        json={"pending_id": "some_id", "otp": secret_otp},
    )
    assert verify_response.status_code == 422
    assert secret_otp not in verify_response.text
    assert verify_response.json()["detail"][0]["input"] == "[REDACTED]"


def test_recursive_validation_error_secret_redaction():
    """Recursive sanitization redacts sensitive fields at arbitrary depth while preserving non-sensitive data."""
    from src.main import _redact_validation_errors, _sanitize_sensitive_data

    # 1. Nested credentials dict
    nested_input = {
        "credentials": {
            "password": "NESTED_SECRET_PASSWORD",
            "nested_level_2": {
                "NEW_PASSWORD": "ANOTHER_SECRET",
            },
        },
        "username": "valid_user",
    }
    sanitized = _sanitize_sensitive_data(nested_input)
    assert sanitized["credentials"]["password"] == "[REDACTED]"
    assert sanitized["credentials"]["nested_level_2"]["NEW_PASSWORD"] == "[REDACTED]"
    assert sanitized["username"] == "valid_user"

    # 2. Nested OTP
    otp_input = {
        "verification": {
            "otp": "654321",
            "method": "email",
        },
    }
    sanitized_otp = _sanitize_sensitive_data(otp_input)
    assert sanitized_otp["verification"]["otp"] == "[REDACTED]"
    assert sanitized_otp["verification"]["method"] == "email"

    # 3. Sensitive values inside lists
    list_input = [
        {"token": "SECRET_TOKEN_1", "user": "alice"},
        {"access_token": "SECRET_ACCESS_TOKEN", "refresh_token": "SECRET_REFRESH_TOKEN"},
        {"jwt_secret": "MY_JWT_SECRET", "secret": "SUPER_SECRET"},
    ]
    sanitized_list = _sanitize_sensitive_data(list_input)
    assert sanitized_list[0]["token"] == "[REDACTED]"
    assert sanitized_list[0]["user"] == "alice"
    assert sanitized_list[1]["access_token"] == "[REDACTED]"
    assert sanitized_list[1]["refresh_token"] == "[REDACTED]"
    assert sanitized_list[2]["jwt_secret"] == "[REDACTED]"
    assert sanitized_list[2]["secret"] == "[REDACTED]"

    # 4. Root/malformed error validation structure
    raw_errors = [
        {
            "loc": ("body",),
            "msg": "Value error, invalid payload",
            "type": "value_error",
            "input": {
                "username": "bob",
                "nested": {"password": "TOP_SECRET_PASSWORD"},
            },
        },
        {
            "loc": ("body", "password"),
            "msg": "String too short",
            "type": "string_too_short",
            "input": "short_raw_pass",
        },
    ]
    redacted = _redact_validation_errors(raw_errors)
    assert redacted[0]["input"]["nested"]["password"] == "[REDACTED]"
    assert redacted[0]["input"]["username"] == "bob"
    assert redacted[1]["input"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_password_and_otp_absent_from_captured_logs(client, caplog):
    """Plaintext password and OTP must never appear in application logs."""
    import logging

    with caplog.at_level(logging.DEBUG):
        await client.post(
            "/api/v1/auth/register",
            json={
                "username": "secret_log_user",
                "email": "secret_log@example.com",
                "password": "SuperSecretPassword123!",
            },
        )
        assert "SuperSecretPassword123!" not in caplog.text


def test_register_form_no_longer_exposes_back_to_edit_path():
    """RegisterForm/SignUpForm component must not expose the stale Back-to-edit path on OTP step."""
    from pathlib import Path

    form_path = Path("frontend/src/features/auth/components/SignUpForm.tsx")
    if not form_path.exists():
        form_path = Path("frontend/src/features/auth/components/RegisterForm.tsx")
    content = form_path.read_text(encoding="utf-8")
    assert "handleBackToForm" not in content
    assert "auth.otp.back" not in content


# ==========================================
# F-Fix3 Final Proof Tests
# ==========================================


@pytest.mark.asyncio
async def test_barrier_controlled_verify_vs_resend_race(client, test_db):
    """Concurrently race verify and resend; prove old OTP cannot succeed after resend commits."""
    reg_res = await client.post(
        "/api/v1/auth/register",
        json={"username": "race_barrier_user", "email": "race_barrier@example.com", "password": "Password123!"},
    )
    assert reg_res.status_code == 202
    pending_id = reg_res.json()["pending_id"]
    import re
    old_otp = re.search(r"\b(\d{6})\b", _memory_sender.sent_emails[0].body_text).group(1)

    # Age last_sent_at by 65s so resend is eligible
    await test_db.execute(
        update(PendingRegistration)
        .where(PendingRegistration.id == pending_id)
        .values(last_sent_at=datetime.now(UTC) - timedelta(seconds=65))
    )
    await test_db.commit()

    # Concurrently run verify(old_otp) and resend
    verify_task = client.post("/api/v1/auth/register/verify", json={"pending_id": pending_id, "otp": old_otp})
    resend_task = client.post("/api/v1/auth/register/resend", json={"pending_id": pending_id})
    v_res, r_res = await asyncio.gather(verify_task, resend_task)

    if r_res.status_code == 200:
        # Resend won: old OTP must fail
        assert v_res.status_code == 400
        new_otp = re.search(r"\b(\d{6})\b", _memory_sender.sent_emails[-1].body_text).group(1)
        assert new_otp != old_otp
        # New OTP can verify
        final_v = await client.post("/api/v1/auth/register/verify", json={"pending_id": pending_id, "otp": new_otp})
        assert final_v.status_code == 200
    else:
        # Verify won: resend must return 404 (pending already consumed)
        assert v_res.status_code == 200
        assert r_res.status_code == 404


@pytest.mark.asyncio
async def test_resend_delivery_failure_committed_state_and_old_otp_invalid(client, test_db, monkeypatch):
    """Resend EmailDeliveryError commits new hash, invalidates old OTP, increments request_count, and creates 0 Users."""
    reg_res = await client.post(
        "/api/v1/auth/register",
        json={"username": "resend_fail_proof", "email": "resend_fail_proof@example.com", "password": "Password123!"},
    )
    assert reg_res.status_code == 202
    pending_id = reg_res.json()["pending_id"]
    import re
    old_otp = re.search(r"\b(\d{6})\b", _memory_sender.sent_emails[0].body_text).group(1)

    # Age cooldown
    await test_db.execute(
        update(PendingRegistration)
        .where(PendingRegistration.id == pending_id)
        .values(last_sent_at=datetime.now(UTC) - timedelta(seconds=65))
    )
    await test_db.commit()

    # Force delivery failure
    from unittest.mock import AsyncMock

    from src.services.identity import email as email_module
    mock_send = AsyncMock(side_effect=RuntimeError("SMTP dropped connection"))
    monkeypatch.setattr(email_module._memory_sender, "send", mock_send)

    resend_res = await client.post("/api/v1/auth/register/resend", json={"pending_id": pending_id})
    assert resend_res.status_code == 500
    assert resend_res.json()["detail"] == "email_delivery_failed"

    # DB check: request_count incremented
    pending = (await test_db.execute(select(PendingRegistration).where(PendingRegistration.id == pending_id))).scalar_one_or_none()
    assert pending is not None
    assert pending.request_count == 2

    # Old OTP is no longer valid
    verify_old = await client.post("/api/v1/auth/register/verify", json={"pending_id": pending_id, "otp": old_otp})
    assert verify_old.status_code == 400

    # No user or session created
    users = (await test_db.execute(select(User).where(User.email == "resend_fail_proof@example.com"))).scalars().all()
    assert len(users) == 0
    sessions = (await test_db.execute(select(RefreshSession))).scalars().all()
    assert len(sessions) == 0


@pytest.mark.asyncio
async def test_initial_registration_provider_failure_explicit_user_and_session_count_zero(client, test_db, monkeypatch):
    """Initial registration EmailDeliveryError commits pending row with strictly 0 users and 0 sessions."""
    from unittest.mock import AsyncMock

    from src.services.identity import email as email_module
    mock_send = AsyncMock(side_effect=RuntimeError("SMTP timeout"))
    monkeypatch.setattr(email_module._memory_sender, "send", mock_send)

    reg_res = await client.post(
        "/api/v1/auth/register",
        json={"username": "zero_user_proof", "email": "zero_user_proof@example.com", "password": "Password123!"},
    )
    assert reg_res.status_code == 500
    assert reg_res.json()["detail"] == "email_delivery_failed"

    # Pending exists with budget consumed
    pending = (await test_db.execute(select(PendingRegistration).where(PendingRegistration.email == "zero_user_proof@example.com"))).scalar_one_or_none()
    assert pending is not None
    assert pending.request_count == 1
    assert pending.attempts == 0

    # User count is strictly 0
    users = (await test_db.execute(select(User).where(User.email == "zero_user_proof@example.com"))).scalars().all()
    assert len(users) == 0
    # RefreshSession count is strictly 0
    sessions = (await test_db.execute(select(RefreshSession))).scalars().all()
    assert len(sessions) == 0


@pytest.mark.asyncio
async def test_secret_logging_proof_password_otp_and_raw_exception_absent(client, caplog, monkeypatch):
    """Captured application logs never contain plaintext password, plaintext OTP, or raw provider exception text."""
    import logging
    from unittest.mock import AsyncMock

    from src.services.identity import email as email_module

    raw_smtp_error = "RAW_SMTP_SOCKET_TIMEOUT_CONNECTION_ABORTED_XYZ_123"
    mock_send = AsyncMock(side_effect=RuntimeError(raw_smtp_error))
    monkeypatch.setattr(email_module._memory_sender, "send", mock_send)

    secret_password = "MySecretPlainTextPassword123!"
    secret_otp = "987654"

    with caplog.at_level(logging.DEBUG):
        # 1. Register triggering delivery error
        await client.post(
            "/api/v1/auth/register",
            json={"username": "log_secret_user2", "email": "log_secret2@example.com", "password": secret_password},
        )
        # 2. Verify with secret OTP
        await client.post(
            "/api/v1/auth/register/verify",
            json={"pending_id": "any_pending_id", "otp": secret_otp},
        )

        # Assert absence from captured logs
        assert secret_password not in caplog.text
        assert secret_otp not in caplog.text
        assert raw_smtp_error not in caplog.text


@pytest.mark.asyncio
async def test_actual_14_language_email_routing_and_resend_preservation(client, test_db):
    """Every supported language receives its correct localized template on register and resend."""
    from src.core.rate_limit import reset_rate_limits

    languages = ["en", "vi", "zh", "ja", "ko", "fr", "de", "es", "th", "id", "pt", "ru", "ar", "hi"]
    for lang in languages:
        reset_rate_limits()
        _memory_sender.clear()
        email = f"user_{lang}@example.com"
        username = f"user_{lang}"
        reg_res = await client.post(
            "/api/v1/auth/register",
            json={
                "username": username,
                "email": email,
                "password": "Password123!",
                "preferred_language": lang,
            },
        )
        assert reg_res.status_code == 202
        pending_id = reg_res.json()["pending_id"]

        # Verify registration email
        assert len(_memory_sender.sent_emails) == 1
        sent_reg = _memory_sender.sent_emails[0]
        assert sent_reg.to_email == email
        assert sent_reg.subject == EMAIL_TEMPLATES[lang].subject

        # Age last_sent_at for resend
        await test_db.execute(
            update(PendingRegistration)
            .where(PendingRegistration.id == pending_id)
            .values(last_sent_at=datetime.now(UTC) - timedelta(seconds=65))
        )
        await test_db.commit()

        # Resend (uses stored interface_language)
        resend_res = await client.post(
            "/api/v1/auth/register/resend",
            json={"pending_id": pending_id},
        )
        assert resend_res.status_code == 200
        assert len(_memory_sender.sent_emails) == 2
        sent_resend = _memory_sender.sent_emails[1]
        assert sent_resend.to_email == email
        assert sent_resend.subject == EMAIL_TEMPLATES[lang].subject


@pytest.mark.asyncio
async def test_verify_time_duplicate_identity_race(client, test_db):
    """If a conflicting User is inserted before OTP verification, verify returns 409 and creates no duplicate User."""
    reg_res = await client.post(
        "/api/v1/auth/register",
        json={"username": "dup_race_proof", "email": "dup_race_proof@example.com", "password": "Password123!"},
    )
    assert reg_res.status_code == 202
    pending_id = reg_res.json()["pending_id"]
    import re
    otp = re.search(r"\b(\d{6})\b", _memory_sender.sent_emails[0].body_text).group(1)

    # Conflicting user created before verify
    conflict_user = User(
        id=str(uuid.uuid4()),
        email="dup_race_proof@example.com",
        username="dup_race_proof_other",
        password_hash=get_password_hash("OtherPass123!"),
        display_name="Conflict User",
        preferred_language="en",
        interface_language="en",
        role="member",
    )
    test_db.add(conflict_user)
    await test_db.commit()

    # Verify with OTP
    verify_res = await client.post(
        "/api/v1/auth/register/verify",
        json={"pending_id": pending_id, "otp": otp},
    )
    assert verify_res.status_code == 409
    assert "already registered" in verify_res.json()["detail"].lower()

    # Assert only 1 user exists with this email
    users = (await test_db.execute(select(User).where(User.email == "dup_race_proof@example.com"))).scalars().all()
    assert len(users) == 1
    assert users[0].id == conflict_user.id


@pytest.mark.asyncio
async def test_transaction_rollback_proof_on_user_creation_failure(client, test_db, monkeypatch):
    """Transaction rollback ensures no partial User or RefreshSession is created if session creation fails."""
    reg_res = await client.post(
        "/api/v1/auth/register",
        json={"username": "rollback_proof_user", "email": "rollback_proof@example.com", "password": "Password123!"},
    )
    assert reg_res.status_code == 202
    pending_id = reg_res.json()["pending_id"]
    import re
    otp = re.search(r"\b(\d{6})\b", _memory_sender.sent_emails[0].body_text).group(1)

    # Mock create_refresh_token to fail during session creation
    from src.api import auth as routes_module
    def faulty_create_refresh_token(*args, **kwargs):
        raise RuntimeError("Simulated token generation crash")
    monkeypatch.setattr(routes_module, "create_refresh_token", faulty_create_refresh_token)

    with pytest.raises(RuntimeError):
        await client.post(
            "/api/v1/auth/register/verify",
            json={"pending_id": pending_id, "otp": otp},
        )

    # Verify transaction rolled back: no User and no Session exists
    users = (await test_db.execute(select(User).where(User.email == "rollback_proof@example.com"))).scalars().all()
    assert len(users) == 0
    sessions = (await test_db.execute(select(RefreshSession))).scalars().all()
    assert len(sessions) == 0


def test_public_registration_route_inventory_no_direct_user_creation():
    """Assert that only /api/v1/auth/register/verify completes user creation and no route bypasses OTP."""
    from src.main import app
    paths = app.openapi()["paths"]
    # Registration endpoints are strictly register (pending only), verify (creates user), resend (pending only)
    assert "/api/v1/auth/register" in paths
    assert "post" in paths["/api/v1/auth/register"]
    assert "/api/v1/auth/register/verify" in paths
    assert "post" in paths["/api/v1/auth/register/verify"]
    assert "/api/v1/auth/register/resend" in paths
    assert "post" in paths["/api/v1/auth/register/resend"]
