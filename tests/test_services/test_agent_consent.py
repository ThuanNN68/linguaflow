"""Tests for the permissions a user grants the assistant (`CONTRACT.md` §3.15).

A regression here fails silently in the worst direction: the assistant would go
on reading conversations, embedding them and writing to calendars for people who
never agreed, and nothing would look broken. So the cases worth pinning are the
defaults and the shape of a revocation, not the happy path.

Fixtures are local to this file rather than in tests/conftest.py, which is shared
across all feature areas.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import (
    AGENT_CONSENT_POLICY_VERSION,
    AGENT_CONSENT_SCOPES,
    AgentConsent,
)
from src.services.assistant.agent_consent import (
    ConsentRequiredError,
    get_consents,
    has_consent,
    require_consent,
    set_consents,
)


@pytest.mark.asyncio
async def test_a_scope_never_answered_reads_as_not_granted(
    test_db: AsyncSession, test_user
) -> None:
    """No row is the closed state, not an unknown one."""
    for scope in AGENT_CONSENT_SCOPES:
        assert await has_consent(test_db, test_user.id, scope) is False


@pytest.mark.asyncio
async def test_require_consent_names_the_missing_scope(
    test_db: AsyncSession, test_user
) -> None:
    """The scope travels on the exception so the API can tell the user which one."""
    with pytest.raises(ConsentRequiredError) as excinfo:
        await require_consent(test_db, test_user.id, "read_conversations")
    assert excinfo.value.scope == "read_conversations"


@pytest.mark.asyncio
async def test_granting_one_scope_leaves_the_others_closed(
    test_db: AsyncSession, test_user
) -> None:
    """Permissions are independent; agreeing to one is not agreeing to the rest."""
    await set_consents(test_db, test_user.id, {"read_conversations": True})

    assert await has_consent(test_db, test_user.id, "read_conversations") is True
    assert await has_consent(test_db, test_user.id, "proactive_scan") is False
    assert await has_consent(test_db, test_user.id, "calendar_write") is False


@pytest.mark.asyncio
async def test_get_consents_returns_every_scope_including_untouched_ones(
    test_db: AsyncSession, test_user
) -> None:
    """The interface cannot render a question it was not sent."""
    await set_consents(test_db, test_user.id, {"store_memory": True})
    rows = await get_consents(test_db, test_user.id)

    assert [row.scope for row in rows] == list(AGENT_CONSENT_SCOPES)
    assert sum(1 for row in rows if row.is_granted) == 1


@pytest.mark.asyncio
async def test_revoking_keeps_the_row_and_stamps_the_time(
    test_db: AsyncSession, test_user
) -> None:
    """"Never asked" and "asked and refused" must stay distinguishable."""
    await set_consents(test_db, test_user.id, {"calendar_read": True})
    await set_consents(test_db, test_user.id, {"calendar_read": False})

    row = await test_db.scalar(
        select(AgentConsent).where(
            AgentConsent.user_id == test_user.id,
            AgentConsent.scope == "calendar_read",
        )
    )
    assert row is not None
    assert row.is_granted is False
    assert row.granted_at is not None
    assert row.revoked_at is not None


@pytest.mark.asyncio
async def test_granting_again_clears_the_earlier_revocation(
    test_db: AsyncSession, test_user
) -> None:
    """A re-grant is a current permission, not a permission with a revoked date."""
    await set_consents(test_db, test_user.id, {"proactive_scan": True})
    await set_consents(test_db, test_user.id, {"proactive_scan": False})
    await set_consents(test_db, test_user.id, {"proactive_scan": True})

    row = await test_db.scalar(
        select(AgentConsent).where(
            AgentConsent.user_id == test_user.id,
            AgentConsent.scope == "proactive_scan",
        )
    )
    assert row is not None
    assert row.is_granted is True
    assert row.revoked_at is None


@pytest.mark.asyncio
async def test_every_grant_records_the_policy_version_in_force(
    test_db: AsyncSession, test_user
) -> None:
    """Without this, a sixth scope would count as pre-approved by everyone."""
    rows = await set_consents(test_db, test_user.id, {"read_conversations": True})
    granted = next(row for row in rows if row.scope == "read_conversations")
    assert granted.policy_version == AGENT_CONSENT_POLICY_VERSION


@pytest.mark.asyncio
async def test_an_unknown_scope_is_a_programming_error_not_a_denial(
    test_db: AsyncSession, test_user
) -> None:
    """A typo must not read as "not granted" and disable a feature invisibly."""
    with pytest.raises(ValueError):
        await has_consent(test_db, test_user.id, "read_converstaions")
    with pytest.raises(ValueError):
        await set_consents(test_db, test_user.id, {"calendar_wrte": True})


@pytest.mark.asyncio
async def test_consent_is_scoped_to_one_account(
    test_db: AsyncSession, test_user, test_user_two
) -> None:
    """One user agreeing does not open the assistant on anybody else's data."""
    await set_consents(test_db, test_user.id, {"read_conversations": True})

    assert await has_consent(test_db, test_user_two.id, "read_conversations") is False
