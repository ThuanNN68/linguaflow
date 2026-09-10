"""Standards-based Web Push delivery for background notifications."""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import delete, select

from src.config import get_settings
from src.database import get_async_session_maker
from src.database.models import PushSubscription

_TASKS: set[asyncio.Task[None]] = set()


def schedule_push(user_ids: tuple[str, ...], *, title: str, body: str, url: str) -> None:
    if not user_ids or not get_settings().web_push_vapid_private_key:
        return
    task = asyncio.create_task(_send_push(user_ids, title=title, body=body, url=url))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def _send_push(user_ids: tuple[str, ...], *, title: str, body: str, url: str) -> None:
    from pywebpush import WebPushException, webpush

    settings = get_settings()
    maker = get_async_session_maker()
    async with maker() as db:
        subscriptions = (await db.scalars(select(PushSubscription).where(PushSubscription.user_id.in_(user_ids)))).all()
        expired: list[str] = []
        payload = json.dumps({"title": title, "body": body[:160], "url": url})
        for subscription in subscriptions:
            info = {"endpoint": subscription.endpoint, "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth}}
            try:
                await asyncio.to_thread(webpush, subscription_info=info, data=payload,
                    vapid_private_key=settings.web_push_vapid_private_key,
                    vapid_claims={"sub": settings.web_push_subject})
            except WebPushException as exc:
                if exc.response is not None and exc.response.status_code in {404, 410}:
                    expired.append(subscription.id)
        if expired:
            await db.execute(delete(PushSubscription).where(PushSubscription.id.in_(expired)))
            await db.commit()
