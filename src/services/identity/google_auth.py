"""Google Sign-In token verification using google-auth library.

Architecture note: This service does NOT use the /tokeninfo HTTP endpoint.
Instead it verifies the JWT signature locally using Google's public keys fetched
by the google-auth library. This avoids a network round-trip on every request
and is the recommended approach per Google's documentation.
"""

import asyncio
import logging
from dataclasses import dataclass

from google.auth import exceptions as google_exceptions
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from src.config import Settings, get_settings

logger = logging.getLogger("src")


class GoogleAuthError(Exception):
    """Raised when Google token verification fails."""


@dataclass
class GoogleUserInfo:
    """Payload extracted from a verified Google ID token."""

    google_sub: str  # Google's unique user ID (stable across accounts)
    email: str  # Normalized lowercase email
    email_verified: bool  # Whether Google has verified this email address
    name: str | None  # display_name, may be absent
    picture: str | None  # avatar URL, informational only


def _verify_id_token_sync(credential: str, settings: Settings) -> GoogleUserInfo:
    """Verify a Google ID token and return the embedded user info.

    Args:
        credential: The JWT string from the Google Identity Services library.
        settings: Application settings (injected for testability).

    Returns:
        GoogleUserInfo extracted from the token.

    Raises:
        GoogleAuthError: Token is invalid, expired, audience mismatch, or email unverified.
    """
    if not settings.google_oauth_client_id:
        raise GoogleAuthError(
            "Google Sign-In is not configured. Set GOOGLE_OAUTH_CLIENT_ID in .env."
        )

    try:
        # google-auth fetches Google's public certificates on first call and caches them.
        # It validates the signature against Google's certificates and rejects
        # tokens whose aud claim doesn't match the configured client_id.
        idinfo: dict = id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            settings.google_oauth_client_id,
            clock_skew_in_seconds=120,
        )
    except google_exceptions.GoogleAuthError as exc:
        logger.warning("Google token verification failed: %s", exc)
        raise GoogleAuthError("Google token verification failed") from exc
    except ValueError as exc:
        # Raised when the token is malformed, not a JWT, etc.
        logger.warning("Malformed Google token: %s", exc)
        raise GoogleAuthError("Malformed Google token") from exc

    # Required fields per OpenID Connect spec
    google_sub: str | None = idinfo.get("sub")
    raw_email: str | None = idinfo.get("email")
    if not google_sub or not raw_email:
        raise GoogleAuthError("Token missing required 'sub' or 'email' claim")

    if idinfo.get("email_verified") is not True:
        raise GoogleAuthError("Google email is not verified")

    normalized_email = raw_email.strip().lower()

    return GoogleUserInfo(
        google_sub=google_sub,
        email=normalized_email,
        email_verified=True,
        name=idinfo.get("name"),
        picture=idinfo.get("picture"),
    )


async def verify_google_token(credential: str) -> GoogleUserInfo:
    """Async entry point. Runs the sync verifier off the event loop.

    Uses asyncio.to_thread so the blocking HTTP call to Google's JWKS endpoint
    does not block other requests while waiting.
    """
    return await asyncio.to_thread(_verify_id_token_sync, credential, get_settings())
