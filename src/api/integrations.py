"""Authenticated Web Push and administrator-owned outbound webhooks."""

from __future__ import annotations

import json
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, HttpUrl, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.core.deps import get_admin_user, get_current_user
from src.database import get_db
from src.database.models import PushSubscription, User, WebhookDelivery, WebhookEndpoint
from src.services.delivery.webhooks import WEBHOOK_EVENTS, validate_webhook_url
from src.services.identity.audit import record_audit_event

router = APIRouter()


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=16, max_length=512)
    auth: str = Field(min_length=8, max_length=256)


class PushSubscriptionRequest(BaseModel):
    endpoint: HttpUrl
    keys: PushKeys


class WebhookCreateRequest(BaseModel):
    url: HttpUrl
    events: list[str] = Field(min_length=1)

    @field_validator("events")
    @classmethod
    def known_events(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value))
        unknown = set(normalized) - WEBHOOK_EVENTS
        if unknown:
            raise ValueError(f"Unsupported webhook events: {', '.join(sorted(unknown))}")
        return normalized


class WebhookResponse(BaseModel):
    id: str
    url: str
    events: list[str]
    is_active: bool
    created_at: datetime
    secret: str | None = None


def _webhook_response(endpoint: WebhookEndpoint, *, reveal_secret: bool = False) -> WebhookResponse:
    return WebhookResponse(id=endpoint.id, url=endpoint.url, events=json.loads(endpoint.events_json),
        is_active=endpoint.is_active, created_at=endpoint.created_at,
        secret=endpoint.secret if reveal_secret else None)


@router.get("/push/vapid-public-key")
async def push_public_key(_user: User = Depends(get_current_user)) -> dict[str, str | bool]:
    key = get_settings().web_push_vapid_public_key
    return {"enabled": bool(key), "public_key": key}


@router.put("/push/subscriptions", status_code=status.HTTP_204_NO_CONTENT)
async def upsert_push_subscription(
    payload: PushSubscriptionRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    endpoint_url = str(payload.endpoint)
    existing = await db.scalar(select(PushSubscription).where(PushSubscription.endpoint == endpoint_url))
    if existing is None:
        db.add(PushSubscription(user_id=user.id, endpoint=endpoint_url, p256dh=payload.keys.p256dh,
            auth=payload.keys.auth, user_agent=request.headers.get("user-agent", "")[:500]))
    else:
        existing.user_id = user.id
        existing.p256dh = payload.keys.p256dh
        existing.auth = payload.keys.auth
        existing.user_agent = request.headers.get("user-agent", "")[:500]
    await db.commit()


@router.delete("/push/subscriptions", status_code=status.HTTP_204_NO_CONTENT)
async def delete_push_subscription(
    endpoint: HttpUrl,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await db.execute(delete(PushSubscription).where(PushSubscription.user_id == user.id, PushSubscription.endpoint == str(endpoint)))
    await db.commit()


@router.get("/admin/webhooks", response_model=list[WebhookResponse])
async def list_webhooks(
    _admin: User = Depends(get_admin_user), db: AsyncSession = Depends(get_db)
) -> list[WebhookResponse]:
    endpoints = (await db.scalars(select(WebhookEndpoint).order_by(WebhookEndpoint.created_at.desc()))).all()
    return [_webhook_response(endpoint) for endpoint in endpoints]


@router.post("/admin/webhooks", response_model=WebhookResponse, status_code=status.HTTP_201_CREATED)
async def create_webhook(
    payload: WebhookCreateRequest,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> WebhookResponse:
    url = str(payload.url)
    try:
        await validate_webhook_url(url)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    endpoint = WebhookEndpoint(url=url, secret=secrets.token_urlsafe(32),
        events_json=json.dumps(payload.events), created_by=admin.id)
    db.add(endpoint)
    await db.flush()
    await record_audit_event(db, actor_id=admin.id, action="webhook.created", resource_type="webhook", resource_id=endpoint.id)
    await db.commit()
    return _webhook_response(endpoint, reveal_secret=True)


@router.delete("/admin/webhooks/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    endpoint_id: str,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    endpoint = await db.get(WebhookEndpoint, endpoint_id)
    if endpoint is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook was not found")
    await record_audit_event(db, actor_id=admin.id, action="webhook.deleted", resource_type="webhook", resource_id=endpoint.id)
    await db.delete(endpoint)
    await db.commit()


@router.get("/admin/webhook-deliveries")
async def list_webhook_deliveries(
    _admin: User = Depends(get_admin_user), db: AsyncSession = Depends(get_db)
) -> list[dict[str, object]]:
    rows = (await db.scalars(select(WebhookDelivery).order_by(WebhookDelivery.created_at.desc()).limit(100))).all()
    return [{"id": row.id, "endpoint_id": row.endpoint_id, "event": row.event, "status": row.status,
        "attempts": row.attempts, "last_error": row.last_error, "created_at": row.created_at} for row in rows]
