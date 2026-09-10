"""Admin-only runtime health and system metrics."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from fastapi import APIRouter, Depends, Request

from src.api.websocket import connection_manager
from src.core.circuit_breaker import (
    embedding_breaker,
    embedding_fallback_breaker,
    fallback_translator_breaker,
    llm_breaker,
)
from src.core.deps import get_admin_user
from src.core.rate_limit import rate_limit_status
from src.database import get_engine
from src.database.models import User
from src.observability import runtime_telemetry_status
from src.services.language.translation import _BACKGROUND_TASKS, translation_cache_metrics

logger = logging.getLogger(__name__)

router = APIRouter()


def _db_pool_status() -> dict[str, Any]:
    """Return both the human-readable SQLAlchemy status and queue counters."""
    pool = get_engine().pool

    def read_counter(name: str) -> int | None:
        value = getattr(pool, name, None)
        try:
            return int(value()) if callable(value) else None
        except (TypeError, ValueError, NotImplementedError):
            return None

    return {
        "status": pool.status(),
        "size": read_counter("size"),
        "checked_in": read_counter("checkedin"),
        "checked_out": read_counter("checkedout"),
        "overflow": read_counter("overflow"),
    }


def _memory_usage_mb() -> float | None:
    """Resident memory in MiB."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 2)
    except (OSError, ValueError, IndexError):
        pass
    try:
        import psutil

        return round(psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024), 2)
    except Exception:
        return None


@router.get("/health/system")
async def system_health(
    request: Request,
    _admin: User = Depends(get_admin_user),
) -> dict[str, Any]:
    """Operational metrics for the administrator dashboard."""
    connections = connection_manager.connections
    cache = translation_cache_metrics()
    startup = getattr(request.app.state, "startup_monotonic", None)
    uptime = round(time.monotonic() - startup, 2) if startup is not None else None
    scheduler = getattr(request.app.state, "reminder_scheduler", None)

    return {
        "uptime_seconds": uptime,
        "websocket_connections": {
            "online_users": len(connections),
            "total_sockets": sum(len(sockets) for sockets in connections.values()),
        },
        "background_tasks_active": len(_BACKGROUND_TASKS),
        "translation_cache_size": cache["size"],
        "translation_cache_hit_rate": cache["hit_rate"],
        "translation_cache": cache,
        "db_pool_status": _db_pool_status(),
        "circuit_breakers": {
            "llm": llm_breaker.status(),
            "embedding": embedding_breaker.status(),
            "embedding_fallback": embedding_fallback_breaker.status(),
            "fallback_translator": fallback_translator_breaker.status(),
        },
        "memory_usage_mb": _memory_usage_mb(),
        "reminder_scheduler_running": bool(scheduler and getattr(scheduler, "running", False)),
        "rate_limits": rate_limit_status(),
        "distributed_tracing": runtime_telemetry_status(),
    }
