"""Pydantic schemas for authentication endpoints."""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Supported languages based on ISO 639-1 codes
SUPPORTED_LANGUAGES = {
    "en",  # English
    "vi",  # Vietnamese
    "ja",  # Japanese
    "zh",  # Chinese
    "ko",  # Korean
    "fr",  # French
    "de",  # German
    "es",  # Spanish
    "th",  # Thai
    "id",  # Indonesian
    "pt",  # Portuguese
    "ru",  # Russian
    "ar",  # Arabic
    "hi",  # Hindi
}


def normalize_language(value: str) -> str:
    """Lower-case a language code and reject anything unsupported.

    Shared by every schema that accepts a language, so the allowlist is checked
    in exactly one place (docs/api/contract.md section 1).

    Raises:
        ValueError: The code is not in SUPPORTED_LANGUAGES.
    """
    normalized = value.lower()
    if normalized not in SUPPORTED_LANGUAGES:
        raise ValueError(
            f"Unsupported language code: {value}. "
            f"Supported codes: {', '.join(sorted(SUPPORTED_LANGUAGES))}"
        )
    return normalized


def normalize_email(value: str) -> str:
    """Trim and lower-case an email so one address means one account.

    Applied on both registration and login; without it, registering as
    `Foo@x.com` would create an account that `foo@x.com` cannot reach.
    """
    return value.strip().lower()


def fallback_profile_names(
    email: str,
    username: str | None,
    display_name: str | None,
) -> tuple[str, str]:
    """Fill in the profile names an older account may not have.

    `username` and `display_name` arrived after the first accounts were created,
    so both are nullable in the database. Every response that carries them fills
    the gap the same way — with the local part of the email — so that no client
    has to decide what to show when a name is missing.

    Args:
        email: Address the fallback is derived from.
        username: Stored username, possibly None.
        display_name: Stored display name, possibly None.

    Returns:
        The username and display name to send, neither of them empty.
    """
    fallback = email.split("@", 1)[0]
    resolved_username = username or fallback
    return resolved_username, display_name or resolved_username


class LoginRequest(BaseModel):
    """Request schema for user login."""

    email: str = Field(..., min_length=1, max_length=255, description="User email")
    password: str = Field(..., min_length=1, description="User password")
    remember: bool = False

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value or "." not in value.rsplit("@", 1)[-1]:
            raise ValueError("Invalid email address")
        return value


class GoogleLoginRequest(BaseModel):
    """Google Identity Services credential sent by the browser (Batch G)."""

    credential: str = Field(..., min_length=1, max_length=8192, description="Google ID token (JWT) from the GIS library")
    remember: bool = True


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=100)
    preferred_language: str = Field(default="en", min_length=2, max_length=10)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Username may only contain letters, numbers, hyphens and underscores")
        return normalized

    @field_validator("email")
    @classmethod
    def normalize_register_email(cls, value: str) -> str:
        return LoginRequest.normalize_email(value)

    @field_validator("preferred_language")
    @classmethod
    def validate_registration_language(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in SUPPORTED_LANGUAGES:
            raise ValueError("Unsupported language code")
        return normalized


class PendingRegisterResponse(BaseModel):
    """Response returned when registration request creates a pending OTP state."""

    pending_id: str
    email: str
    expires_in_seconds: int = 300
    cooldown_seconds: int = 60
    message: str = "Verification code sent to your email"


class VerifyRegisterRequest(BaseModel):
    """Request schema for verifying a pending registration with a 6-digit numeric OTP."""

    model_config = ConfigDict(extra="forbid")

    pending_id: str = Field(..., min_length=1, max_length=100)
    otp: str = Field(..., min_length=6, max_length=6, pattern=r"^[0-9]{6}$")

    @field_validator("otp")
    @classmethod
    def validate_ascii_digits(cls, value: str) -> str:
        if not (len(value) == 6 and all(c in "0123456789" for c in value)):
            raise ValueError("OTP must be exactly 6 ASCII digits (0-9)")
        return value


class ResendRegisterOtpRequest(BaseModel):
    """Request schema for requesting a replacement OTP for a pending registration."""

    model_config = ConfigDict(extra="forbid")

    pending_id: str = Field(..., min_length=1, max_length=100)


class ResendRegisterOtpResponse(BaseModel):
    """Response returned when replacement OTP is generated and sent."""

    pending_id: str
    expires_in_seconds: int = 300
    cooldown_seconds: int = 60
    message: str = "New verification code sent to your email"


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=32)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(..., min_length=32)


class AccountDeletionRequest(BaseModel):
    """Explicit acknowledgement for an irreversible account-erasure request."""

    confirmation: str = Field(..., min_length=6, max_length=32)
    current_password: str | None = Field(default=None, min_length=8, max_length=128)

    @field_validator("confirmation")
    @classmethod
    def require_delete_phrase(cls, value: str) -> str:
        if value.strip() != "DELETE":
            raise ValueError("confirmation must equal DELETE")
        return "DELETE"


class ForgotPasswordRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)

    @field_validator("email")
    @classmethod
    def normalize_forgot_email(cls, value: str) -> str:
        return LoginRequest.normalize_email(value)


class ForgotPasswordResponse(BaseModel):
    message: str
    reset_token: str | None = None


class ResetPasswordRequest(BaseModel):
    token: str = Field(..., min_length=32)
    new_password: str = Field(..., min_length=8, max_length=128)


class TokenResponse(BaseModel):
    """Response schema for successful login."""

    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    """Response schema for user data (excludes sensitive fields)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    username: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None
    bio: str | None = None
    role: str
    preferred_language: str
    interface_language: str
    created_at: datetime
    # Not exposed in API responses — read from ORM object via from_attributes,
    # then excluded from JSON output. Declared here so Pydantic can extract it
    # from the User row for use in the model_validator below.
    google_sub: str | None = Field(default=None, exclude=True)
    password_hash: str | None = Field(default=None, exclude=True)
    # Derived: True when google_sub is set on the account.
    google_linked: bool = False
    # Derived: True when the user has a non-null password_hash set.
    has_password: bool = True

    @model_validator(mode="after")
    def fill_legacy_fields(self) -> "UserResponse":
        """Fill profile name gaps and compute derived auth state."""
        self.username, self.display_name = fallback_profile_names(
            self.email, self.username, self.display_name
        )
        # google_sub and password_hash are read from the ORM object via from_attributes,
        # then google_linked and has_password are derived so callers never see internal fields.
        self.google_linked = bool(self.google_sub)
        self.has_password = bool(self.password_hash and len(self.password_hash) > 0)
        return self


class AuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse


class UpdateLanguageRequest(BaseModel):
    """Request schema for updating preferred language."""

    preferred_language: str = Field(
        ...,
        description="ISO 639-1 language code",
        min_length=2,
        max_length=10,
        examples=["en", "vi", "ja"],
    )

    @field_validator("preferred_language")
    @classmethod
    def validate_language(cls, v: str) -> str:
        """Validate that the language code is supported."""
        return normalize_language(v)


class UpdateInterfaceLanguageRequest(BaseModel):
    """Request schema for updating the language of the interface (§1.2).

    Separate from `UpdateLanguageRequest` because the two settings answer
    different questions and change different things: this one repaints the
    screen immediately and costs nothing, while the reading language only
    affects messages sent from then on and spends LLM quota.
    """

    interface_language: str = Field(
        ...,
        description="ISO 639-1 language code",
        min_length=2,
        max_length=10,
        examples=["en", "vi", "ja"],
    )

    @field_validator("interface_language")
    @classmethod
    def validate_interface_language(cls, v: str) -> str:
        """Validate that the language code is supported."""
        return normalize_language(v)


class UserProfileUpdate(BaseModel):
    """Partial, self-service profile fields only."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=100)
    avatar_url: str | None = Field(default=None, max_length=700_000)
    bio: str | None = Field(default=None, max_length=500)

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("display_name must not be blank")
        return cleaned

    @field_validator("avatar_url")
    @classmethod
    def validate_avatar_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not re.fullmatch(r"data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/]+={0,2}", value):
            raise ValueError("avatar_url must be a PNG, JPEG, or WebP data URL")
        return value

    @field_validator("bio")
    @classmethod
    def normalize_bio(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @model_validator(mode="after")
    def require_a_change(self) -> "UserProfileUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one profile field must be provided")
        return self


class UserSettingsResponse(BaseModel):
    """Canonical persisted settings returned for the current account."""

    model_config = ConfigDict(from_attributes=True)

    auto_translate: bool
    show_original_by_default: bool
    translation_tone: Literal["natural", "formal", "casual", "friendly"]
    sound_enabled: bool
    read_receipts: bool
    ai_smart_assistance: bool
    updated_at: datetime | None = None


class UserSettingsUpdate(BaseModel):
    """Partial update; explicit fields preserve PATCH idempotency."""

    model_config = ConfigDict(extra="forbid")

    auto_translate: bool | None = None
    show_original_by_default: bool | None = None
    translation_tone: Literal["natural", "formal", "casual", "friendly"] | None = None
    sound_enabled: bool | None = None
    read_receipts: bool | None = None
    ai_smart_assistance: bool | None = None

    @model_validator(mode="after")
    def require_a_change(self) -> "UserSettingsUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one setting must be provided")
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"Setting '{field}' cannot be null")
        return self


# ----------------------------------------------------------------------
# Google Sign-In schemas (Batch G)
# ----------------------------------------------------------------------


class GoogleLinkResponse(BaseModel):
    """Response after linking or unlinking a Google account."""

    google_linked: bool
    message: str


class GoogleErrorResponse(BaseModel):
    """Error response when Google Sign-In fails."""

    detail: str = Field(
        ...,
        examples=[
            "Google token verification failed",
            "This Google account is already linked to another user",
            "This email is already linked to a different Google account.",
        ],
    )
