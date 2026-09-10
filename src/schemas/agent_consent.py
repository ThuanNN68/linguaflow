"""Request and response bodies for assistant permissions (`CONTRACT.md` §3.15)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from src.database.models import AGENT_CONSENT_SCOPES


class AgentConsentEntry(BaseModel):
    """One permission and its current state."""

    model_config = ConfigDict(from_attributes=True)

    scope: str
    is_granted: bool
    granted_at: datetime | None = None
    revoked_at: datetime | None = None


class AgentConsentsResponse(BaseModel):
    """Every permission for the current account, always the full vocabulary."""

    policy_version: str
    consents: list[AgentConsentEntry]


class AgentConsentsUpdate(BaseModel):
    """Partial update; only the named scopes change."""

    model_config = ConfigDict(extra="forbid")

    consents: dict[str, bool]

    @field_validator("consents")
    @classmethod
    def scopes_must_be_known(cls, value: dict[str, bool]) -> dict[str, bool]:
        unknown = sorted(set(value) - set(AGENT_CONSENT_SCOPES))
        if unknown:
            raise ValueError(f"Unknown consent scope(s): {', '.join(unknown)}")
        return value

    @model_validator(mode="after")
    def require_a_change(self) -> AgentConsentsUpdate:
        if not self.consents:
            raise ValueError("At least one consent scope must be provided")
        return self
