"""Compatibility router aggregating domain-owned REST endpoints.

New endpoint code belongs in the matching domain module.  This object stays to
preserve the stable import used by the application entry point.
"""

from fastapi import APIRouter

from src.api import attachments, auth, calendar, chat, integrations, intelligence, profile

router = APIRouter()
for _domain_router in (
    auth.router,
    profile.router,
    calendar.router,
    chat.router,
    intelligence.router,
    attachments.router,
    integrations.router,
):
    # The pinned FastAPI version represents nested routers as lazy wrappers.
    # Flatten here so the application entry point receives concrete routes and
    # keeps the same OpenAPI and dispatch behaviour as the former single file.
    router.routes.extend(_domain_router.routes)
