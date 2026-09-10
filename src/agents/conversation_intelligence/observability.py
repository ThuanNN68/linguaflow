"""Metadata-only structured logging and telemetry for Conversation Intelligence (B-09).

Invariants:
- Never log raw message content, prompt text, or user secrets.
- Telemetry or logging failures must NEVER raise exceptions or break operations.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.agents.observability import build_runnable_config, get_trace_handler

logger = logging.getLogger("src.intelligence")

__all__ = [
    "LatencyTimer",
    "build_runnable_config",
    "get_trace_handler",
    "log_intelligence_event",
]


def log_intelligence_event(
    operation: str,
    status: str,
    latency_ms: float,
    provider: str = "",
    model: str = "",
    conversation_id: str | None = None,
    message_id: str | None = None,
    proposal_count: int | None = None,
    error_code: str | None = None,
    **kwargs: Any,
) -> None:
    """Log operation metadata safely.

    Guarantees no raw conversation content is logged, and logging failures never raise.
    """
    try:
        payload: dict[str, Any] = {
            "event": "conversation_intelligence",
            "operation": operation,
            "status": status,
            "latency_ms": round(latency_ms, 2),
        }
        if provider:
            payload["provider"] = provider
        if model:
            payload["model"] = model
        if conversation_id:
            payload["conversation_id"] = str(conversation_id)
        if message_id:
            payload["message_id"] = str(message_id)
        if proposal_count is not None:
            payload["proposal_count"] = proposal_count
        if error_code:
            payload["error_code"] = str(error_code)

        # Include additional safe metadata (no text/token values)
        for k, v in kwargs.items():
            if k not in ("text", "content", "prompt", "messages", "token", "password", "key", "secret"):
                payload[k] = v

        if status == "success":
            logger.info("Intelligence op completed: %s", payload)
        elif status in ("timeout", "provider_error", "invalid_output"):
            logger.warning("Intelligence op failed [%s]: %s", status, payload)
        else:
            logger.info("Intelligence op event [%s]: %s", status, payload)
    except Exception:
        # Observability / logging failures must never crash operations
        pass


class LatencyTimer:
    """Context manager / helper for measuring execution latency in milliseconds."""

    def __init__(self) -> None:
        self.start_time: float = 0.0
        self.latency_ms: float = 0.0

    def __enter__(self) -> LatencyTimer:
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.latency_ms = (time.perf_counter() - self.start_time) * 1000.0
