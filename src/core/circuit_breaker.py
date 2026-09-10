"""Lightweight circuit breaker for external service calls.

Prevents cascading failures when an external provider (LLM, embedding,
fallback translator) is down.  Instead of every request waiting for
a timeout, the breaker opens after consecutive failures and routes
requests directly to the fallback path — cutting latency from the
configured timeout (up to 10 s) to near zero.

This is an in-memory, single-process implementation matching the
deployment model (ADR-18: one replica, ConnectionManager in RAM).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any, TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """Raised immediately when a provider circuit is not accepting work."""


class CircuitBreaker:
    """Track consecutive failures and short-circuit calls when a provider is down."""

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout

        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._probe_running = False
        self._total_trips = 0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        """Current state, considering automatic recovery cooldown."""
        with self._lock:
            if self._state is CircuitState.OPEN and time.monotonic() - self._opened_at >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._probe_running = False
                logger.info(
                    "Circuit breaker '%s' entering half-open state after %.0fs cooldown",
                    self.name,
                    self.recovery_timeout,
                )
            return self._state

    def allow_request(self) -> bool:
        """Whether a call should be attempted."""
        with self._lock:
            if self._state is CircuitState.CLOSED:
                return True
            if self._state is CircuitState.OPEN and time.monotonic() - self._opened_at >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._probe_running = True
                return True
            if self._state is CircuitState.HALF_OPEN:
                if not self._probe_running:
                    self._probe_running = True
                    return True
                return False
            return False

    def record_success(self) -> None:
        """A call succeeded — reset the breaker to closed."""
        with self._lock:
            if self._state is not CircuitState.CLOSED:
                logger.info("Circuit breaker '%s' closed after successful probe", self.name)
            self._state = CircuitState.CLOSED
            self._failures = 0
            self._probe_running = False

    def record_failure(self) -> None:
        """A call failed — increment the counter and possibly trip the breaker."""
        with self._lock:
            self._failures += 1
            self._probe_running = False
            if self._state is CircuitState.HALF_OPEN or self._failures >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                self._total_trips += 1
                logger.warning(
                    "Circuit breaker '%s' OPEN after %d consecutive failures (trip #%d). "
                    "Requests will bypass this provider for %.0fs.",
                    self.name,
                    self._failures,
                    self._total_trips,
                    self.recovery_timeout,
                )

    async def call(self, operation: Callable[[], Awaitable[T]]) -> T:
        """Run one async provider operation under this breaker's state machine."""
        if not self.allow_request():
            raise CircuitOpenError(f"Circuit breaker '{self.name}' is open")
        try:
            result = await operation()
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result

    def reset(self) -> None:
        """Return to the initial state; used to isolate tests."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failures = 0
            self._opened_at = 0.0
            self._probe_running = False
            self._total_trips = 0

    def status(self) -> dict[str, Any]:
        """Snapshot for the system health endpoint."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self._failures,
            "failure_threshold": self.failure_threshold,
            "total_trips": self._total_trips,
            "recovery_timeout_seconds": self.recovery_timeout,
        }


# Singleton breakers for external providers
llm_breaker = CircuitBreaker("llm", failure_threshold=5, recovery_timeout=60.0)
embedding_breaker = CircuitBreaker("embedding", failure_threshold=5, recovery_timeout=60.0)
embedding_fallback_breaker = CircuitBreaker("embedding_fallback", failure_threshold=5, recovery_timeout=60.0)
fallback_translator_breaker = CircuitBreaker("fallback_translator", failure_threshold=5, recovery_timeout=60.0)
