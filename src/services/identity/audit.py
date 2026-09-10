"""Minimal, append-only audit events for security-sensitive state changes."""

from __future__ import annotations

import json
from collections.abc import Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import AuditLog


async def record_audit_event(
    db: AsyncSession,
    *,
    actor_id: str | None,
    action: str,
    resource_type: str,
    resource_id: str | None,
    metadata: Mapping[str, str | int | bool | None] | None = None,
) -> AuditLog:
    """Stage an event in the caller's transaction without sensitive payloads."""
    event = AuditLog(
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata_json=json.dumps(dict(metadata or {}), sort_keys=True, separators=(",", ":")),
    )
    db.add(event)
    return event
