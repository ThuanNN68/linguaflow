"""Tests for circuit breaker module."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    embedding_breaker,
    embedding_fallback_breaker,
)
from src.services.shared import embeddings
from src.services.shared.llm import _CircuitProtectedChatModel


def test_circuit_breaker_initial_state_closed():
    breaker = CircuitBreaker("test", failure_threshold=3, recovery_timeout=1.0)
    assert breaker.state == CircuitState.CLOSED
    assert breaker.allow_request() is True


def test_circuit_breaker_trips_after_failures():
    breaker = CircuitBreaker("test", failure_threshold=3, recovery_timeout=1.0)
    breaker.record_failure()
    assert breaker.state == CircuitState.CLOSED
    assert breaker.allow_request() is True

    breaker.record_failure()
    assert breaker.state == CircuitState.CLOSED
    assert breaker.allow_request() is True

    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN
    assert breaker.allow_request() is False


def test_circuit_breaker_recovers_after_cooldown():
    breaker = CircuitBreaker("test", failure_threshold=2, recovery_timeout=0.1)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN

    time.sleep(0.15)
    assert breaker.state == CircuitState.HALF_OPEN
    assert breaker.allow_request() is True

    # Success in half-open resets to closed
    breaker.record_success()
    assert breaker.state == CircuitState.CLOSED
    assert breaker.allow_request() is True


def test_circuit_breaker_half_open_failure_reopens():
    breaker = CircuitBreaker("test", failure_threshold=2, recovery_timeout=0.1)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN

    time.sleep(0.15)
    assert breaker.state == CircuitState.HALF_OPEN

    # Failure during half-open immediately trips back to open
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN
    assert breaker.allow_request() is False


def test_circuit_breaker_status():
    breaker = CircuitBreaker("test", failure_threshold=3, recovery_timeout=5.0)
    status = breaker.status()
    assert status["name"] == "test"
    assert status["state"] == "closed"
    assert status["failure_count"] == 0
    assert status["failure_threshold"] == 3


@pytest.mark.asyncio
async def test_async_call_records_provider_success_and_failure():
    breaker = CircuitBreaker("provider", failure_threshold=1, recovery_timeout=60)

    with pytest.raises(RuntimeError, match="provider down"):
        await breaker.call(AsyncMock(side_effect=RuntimeError("provider down")))
    assert breaker.state == CircuitState.OPEN

    with pytest.raises(CircuitOpenError):
        await breaker.call(AsyncMock(return_value="never"))


@pytest.mark.asyncio
async def test_llm_proxy_guards_the_actual_ainvoke_call():
    breaker = CircuitBreaker("llm-test", failure_threshold=1, recovery_timeout=60)
    provider = SimpleNamespace(ainvoke=AsyncMock(side_effect=TimeoutError("slow")))
    protected = _CircuitProtectedChatModel(provider, breaker)

    with pytest.raises(TimeoutError):
        await protected.ainvoke("prompt")
    assert breaker.state == CircuitState.OPEN

    with pytest.raises(CircuitOpenError):
        await protected.ainvoke("prompt")
    provider.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_embedding_breaker_skips_calls_after_threshold(monkeypatch):
    provider = SimpleNamespace(ainvoke=AsyncMock())
    provider.aembed_query = AsyncMock(side_effect=TimeoutError("embedding down"))
    monkeypatch.setattr(embeddings, "get_embedder", lambda _settings=None: provider)

    for _ in range(embedding_breaker.failure_threshold):
        assert await embeddings.embed("hello") is None
    assert embedding_breaker.state == CircuitState.OPEN

    assert await embeddings.embed("hello") is None
    assert provider.aembed_query.await_count == embedding_breaker.failure_threshold


@pytest.mark.asyncio
async def test_successful_embedding_fallback_does_not_reset_primary_failures(monkeypatch):
    primary = SimpleNamespace(aembed_query=AsyncMock(side_effect=TimeoutError("primary down")))
    fallback = SimpleNamespace(aembed_query=AsyncMock(return_value=[0.0] * embeddings.EMBEDDING_DIM))
    providers = iter((primary, fallback))
    monkeypatch.setattr(embeddings, "get_embedder", lambda _settings=None: next(providers))

    assert await embeddings.embed("hello", breaker=embedding_breaker) is None
    assert await embeddings.embed("hello", breaker=embedding_fallback_breaker) is not None

    assert embedding_breaker.status()["failure_count"] == 1
    assert embedding_fallback_breaker.status()["failure_count"] == 0
