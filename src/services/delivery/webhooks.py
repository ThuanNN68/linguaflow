"""Durable, signed outbound webhook delivery."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import socket
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import httpx
from sqlalchemy import select

from src.config import get_settings
from src.database import get_async_session_maker
from src.database.models import WebhookDelivery, WebhookEndpoint

WEBHOOK_EVENTS = frozenset({"message.created", "message.deleted", "user.suspended", "user.unsuspended"})
_TASKS: set[asyncio.Task[None]] = set()


async def validate_webhook_url(url: str) -> str:
    parsed = urlparse(url)
    settings = get_settings()
    if parsed.scheme not in ({"http", "https"} if settings.app_env != "production" else {"https"}) or not parsed.hostname:
        raise ValueError("Webhook URL must use HTTPS")
    if settings.webhook_allow_private_networks:
        return url
    try:
        addresses = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, parsed.port or 443)
    except socket.gaierror:
        raise ValueError("Webhook hostname could not be resolved") from None
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError("Webhook URL must not target a private network")
    return url


async def enqueue_webhook_event(event: str, payload: dict[str, object]) -> None:
    if event not in WEBHOOK_EVENTS:
        return
    maker = get_async_session_maker()
    async with maker() as db:
        endpoints = (await db.scalars(select(WebhookEndpoint).where(WebhookEndpoint.is_active.is_(True)))).all()
        deliveries: list[WebhookDelivery] = []
        envelope = json.dumps({"event": event, "data": payload}, sort_keys=True, separators=(",", ":"))
        for endpoint in endpoints:
            if event not in json.loads(endpoint.events_json):
                continue
            delivery = WebhookDelivery(endpoint_id=endpoint.id, event=event, payload_json=envelope)
            db.add(delivery)
            deliveries.append(delivery)
        await db.commit()
        ids = [delivery.id for delivery in deliveries]
    for delivery_id in ids:
        _schedule_delivery(delivery_id)


def schedule_webhook_event(event: str, payload: dict[str, object]) -> None:
    task = asyncio.create_task(enqueue_webhook_event(event, payload))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


def _schedule_delivery(delivery_id: str) -> None:
    task = asyncio.create_task(_deliver_with_retries(delivery_id))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def _deliver_with_retries(delivery_id: str) -> None:
    settings = get_settings()
    maker = get_async_session_maker()
    while True:
        async with maker() as db:
            delivery = await db.get(WebhookDelivery, delivery_id)
            if delivery is None or delivery.status in {"delivered", "failed"}:
                return
            endpoint = await db.get(WebhookEndpoint, delivery.endpoint_id)
            if endpoint is None or not endpoint.is_active:
                delivery.status = "failed"
                delivery.last_error = "endpoint inactive"
                await db.commit()
                return
            wait = max(0.0, ((delivery.next_attempt_at or datetime.now(UTC)).replace(tzinfo=UTC) - datetime.now(UTC)).total_seconds())
            url, secret, body, event = endpoint.url, endpoint.secret, delivery.payload_json, delivery.event
        if wait:
            await asyncio.sleep(min(wait, 30))
            continue
        try:
            await validate_webhook_url(url)
            timestamp = str(int(time.time()))
            signature = hmac.new(secret.encode(), f"{timestamp}.{body}".encode(), hashlib.sha256).hexdigest()
            async with httpx.AsyncClient(timeout=settings.webhook_timeout_seconds, follow_redirects=False) as client:
                response = await client.post(url, content=body, headers={
                    "Content-Type": "application/json",
                    "X-LinguaFlow-Event": event,
                    "X-LinguaFlow-Delivery": delivery_id,
                    "X-LinguaFlow-Timestamp": timestamp,
                    "X-LinguaFlow-Signature": f"sha256={signature}",
                })
            response.raise_for_status()
            error = None
        except (httpx.HTTPError, ValueError) as exc:
            error = type(exc).__name__
        async with maker() as db:
            delivery = await db.get(WebhookDelivery, delivery_id)
            if delivery is None:
                return
            delivery.attempts += 1
            if error is None:
                delivery.status = "delivered"
                delivery.delivered_at = datetime.now(UTC)
                delivery.last_error = None
                await db.commit()
                return
            delivery.last_error = error
            if delivery.attempts >= settings.webhook_max_attempts:
                delivery.status = "failed"
                await db.commit()
                return
            delivery.status = "pending"
            delivery.next_attempt_at = datetime.now(UTC) + timedelta(seconds=min(2 ** delivery.attempts, 30))
            await db.commit()


async def recover_webhook_deliveries() -> int:
    maker = get_async_session_maker()
    async with maker() as db:
        ids = list(
            (
                await db.scalars(
                    select(WebhookDelivery.id).where(WebhookDelivery.status == "pending").limit(200)
                )
            ).all()
        )
    for delivery_id in ids:
        _schedule_delivery(delivery_id)
    return len(ids)


async def stop_webhook_deliveries() -> None:
    tasks = tuple(_TASKS)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
