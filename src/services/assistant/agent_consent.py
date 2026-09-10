"""Permissions a user has granted to the assistant agent.

Conversation membership answers "who is allowed to see this". It does not
answer "does this person agree to a machine reading it, remembering it, and
writing it to their calendar". Those are separate questions and this module is
the second one.

The check lives here rather than in the callers, for the same reason
`schedule_correction_record` keeps its consent check inside the service: a gate
that every call site has to remember is a gate that a future call site will
forget. Callers ask for `require_consent` or `has_consent` and get one
behaviour.

Two entry points because there are two kinds of caller. A request the user made
should fail loudly, so `require_consent` raises and the route answers 403 with
the scope name. Background work the user did not ask for should not turn a
missing permission into a failure in the path that triggered it, so
`has_consent` returns a bool and the worker returns quietly.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import (
    AGENT_CONSENT_POLICY_VERSION,
    AGENT_CONSENT_SCOPES,
    AgentConsent,
)


class ConsentRequiredError(Exception):
    """The user has not granted the scope this operation needs."""

    def __init__(self, scope: str) -> None:
        super().__init__(f"Assistant consent required for scope '{scope}'")
        self.scope = scope


def _assert_known(scope: str) -> None:
    """Reject a scope the vocabulary does not contain.

    A typo here would otherwise read as "not granted" and disable a feature
    silently, which is the hardest kind of consent bug to notice: everything
    keeps working, just never for anyone.
    """
    if scope not in AGENT_CONSENT_SCOPES:
        raise ValueError(f"Unknown agent consent scope: {scope!r}")


async def has_consent(db: AsyncSession, user_id: str, scope: str) -> bool:
    """Report whether ``user_id`` currently grants ``scope``.

    No row means not granted. The default is closed on purpose — a user who has
    never seen the question has not answered it.
    """
    _assert_known(scope)
    granted = await db.scalar(
        select(AgentConsent.is_granted).where(
            AgentConsent.user_id == user_id,
            AgentConsent.scope == scope,
        )
    )
    return bool(granted)


async def require_consent(db: AsyncSession, user_id: str, scope: str) -> None:
    """Raise :class:`ConsentRequiredError` unless ``scope`` is granted."""
    if not await has_consent(db, user_id, scope):
        raise ConsentRequiredError(scope)


async def get_consents(db: AsyncSession, user_id: str) -> list[AgentConsent]:
    """Return every scope for ``user_id``, materialising the ones never touched.

    Always the full vocabulary, in declaration order. A scope with no row is a
    scope that is *not granted*, not one that does not exist — returning it
    absent would leave the interface unable to render the question at all.

    The placeholders are not persisted. Writing a row for a permission nobody
    has answered would make `created_at` describe when the list was rendered
    rather than when the user decided anything.
    """
    rows = (
        await db.scalars(select(AgentConsent).where(AgentConsent.user_id == user_id))
    ).all()
    by_scope = {row.scope: row for row in rows}
    return [
        by_scope.get(scope)
        or AgentConsent(
            user_id=user_id,
            scope=scope,
            is_granted=False,
            policy_version=AGENT_CONSENT_POLICY_VERSION,
        )
        for scope in AGENT_CONSENT_SCOPES
    ]


async def set_consents(
    db: AsyncSession, user_id: str, changes: dict[str, bool]
) -> list[AgentConsent]:
    """Grant or revoke the named scopes and return the full current set.

    Revoking clears the flag and stamps `revoked_at`; it never deletes the row,
    so "never asked" stays distinguishable from "asked and refused".

    Every grant is stamped with the policy version in force at the time. A grant
    made under an older version stays readable as such, which is what lets the
    interface re-ask about that one scope instead of all of them.
    """
    for scope in changes:
        _assert_known(scope)

    try:
        await _apply(db, user_id, changes)
        await db.commit()
    except IntegrityError:
        # Two requests inserting the first row for the same scope at once. The
        # unique constraint keeps the table correct; what must not happen is
        # dropping this caller's decision on the floor, so re-read the rows the
        # winner created and apply the change again on top of them. Once is
        # enough: the second attempt updates existing rows and inserts nothing.
        await db.rollback()
        await _apply(db, user_id, changes)
        await db.commit()

    return await get_consents(db, user_id)


async def _apply(db: AsyncSession, user_id: str, changes: dict[str, bool]) -> None:
    """Stage the requested grants and revocations without committing."""
    now = datetime.now(UTC)
    existing = {
        row.scope: row
        for row in (
            await db.scalars(select(AgentConsent).where(AgentConsent.user_id == user_id))
        ).all()
    }

    for scope, is_granted in changes.items():
        row = existing.get(scope)
        if row is None:
            row = AgentConsent(
                user_id=user_id, scope=scope, policy_version=AGENT_CONSENT_POLICY_VERSION
            )
            db.add(row)
        row.is_granted = is_granted
        if is_granted:
            row.granted_at = now
            row.revoked_at = None
            # Re-granting re-stamps the version: the user has just answered the
            # question as it reads today, whatever it read when they first saw it.
            row.policy_version = AGENT_CONSENT_POLICY_VERSION
        else:
            row.revoked_at = now
