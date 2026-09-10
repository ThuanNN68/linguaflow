"""Tests for the secondary translation provider (ADR-07).

No test reaches the network: the blocking deep-translator call is patched out.
What matters here is that every failure mode returns None rather than raising,
because the caller treats None as "keep the original text" (NFR-02).

Fixtures live in this file rather than tests/conftest.py to avoid conflicting
with feature/f-01-2-auth-user-config, which rewrites conftest.
"""

from __future__ import annotations

import time
from unittest.mock import Mock

import pytest

from src.core.circuit_breaker import CircuitState, fallback_translator_breaker
from src.services.language.fallback_translator import translate_with_secondary_provider

MODULE = "src.services.language.fallback_translator"


def make_fallback_settings(is_enabled: bool = True, timeout: int = 5):
    """Create a mock settings object for fallback translator tests."""

    class FakeSettings:
        fallback_translator_enabled = is_enabled
        fallback_translator_timeout_seconds = timeout

    return FakeSettings()


@pytest.fixture
def settings_enabled(monkeypatch):
    monkeypatch.setattr(f"{MODULE}.get_settings", make_fallback_settings)


@pytest.mark.asyncio
async def test_returns_translation_on_success(monkeypatch, settings_enabled):
    """Successful secondary translation returns the translated text."""
    monkeypatch.setattr(f"{MODULE}._translate_sync", lambda *a: "Have you deployed?")

    result = await translate_with_secondary_provider("Deploy xong chưa?", "en", "vi")

    assert result == "Have you deployed?"


@pytest.mark.asyncio
async def test_returns_none_when_provider_fails(monkeypatch, settings_enabled):
    """A provider outage must be reported as None, never raised at the node."""

    def boom(*args):
        raise ConnectionError("google unreachable")

    monkeypatch.setattr(f"{MODULE}._translate_sync", boom)

    assert await translate_with_secondary_provider("Chào bạn", "en", "vi") is None


@pytest.mark.asyncio
async def test_returns_none_when_provider_times_out(monkeypatch):
    """A slow provider must not hold the chat flow open indefinitely."""
    monkeypatch.setattr(f"{MODULE}.get_settings", lambda: make_fallback_settings(timeout=1))
    monkeypatch.setattr(f"{MODULE}._translate_sync", lambda *a: time.sleep(1.5))

    assert await translate_with_secondary_provider("Chào bạn", "en", "vi") is None


@pytest.mark.asyncio
async def test_returns_none_when_disabled_by_config(monkeypatch):
    """FALLBACK_TRANSLATOR_ENABLED=false restores the plain passthrough."""
    monkeypatch.setattr(f"{MODULE}.get_settings", lambda: make_fallback_settings(is_enabled=False))

    def fail(*args):
        raise AssertionError("provider must not be called when disabled")

    monkeypatch.setattr(f"{MODULE}._translate_sync", fail)

    assert await translate_with_secondary_provider("Chào bạn", "en", "vi") is None


@pytest.mark.asyncio
async def test_skips_provider_when_source_matches_target(monkeypatch, settings_enabled):
    """Secondary provider must not be called when source and target languages are the same."""

    def fail(*args):
        raise AssertionError("provider must not be called for identical languages")

    monkeypatch.setattr(f"{MODULE}._translate_sync", fail)

    assert await translate_with_secondary_provider("Chào bạn", "vi", "vi") is None


@pytest.mark.asyncio
async def test_skips_provider_when_text_or_target_missing(monkeypatch, settings_enabled):
    """Secondary provider must not be called without text or target language."""

    def fail(*args):
        raise AssertionError("provider must not be called without text and target")

    monkeypatch.setattr(f"{MODULE}._translate_sync", fail)

    assert await translate_with_secondary_provider("", "en", "vi") is None
    assert await translate_with_secondary_provider("Chào bạn", "", "vi") is None


@pytest.mark.asyncio
async def test_returns_none_when_provider_returns_blank(monkeypatch, settings_enabled):
    """deep-translator can return None or blank text; both mean 'no result'."""
    monkeypatch.setattr(f"{MODULE}._translate_sync", lambda *a: "   ")
    assert await translate_with_secondary_provider("Chào bạn", "en", "vi") is None

    monkeypatch.setattr(f"{MODULE}._translate_sync", lambda *a: None)
    assert await translate_with_secondary_provider("Chào bạn", "en", "vi") is None
    assert fallback_translator_breaker.status()["failure_count"] == 2


@pytest.mark.asyncio
async def test_open_circuit_skips_secondary_provider(monkeypatch, settings_enabled):
    provider = Mock(side_effect=AssertionError("open circuit must skip provider"))
    monkeypatch.setattr(f"{MODULE}._translate_sync", provider)
    for _ in range(fallback_translator_breaker.failure_threshold):
        fallback_translator_breaker.record_failure()

    assert fallback_translator_breaker.state == CircuitState.OPEN
    assert await translate_with_secondary_provider("Chào bạn", "en", "vi") is None
    provider.assert_not_called()


@pytest.mark.asyncio
async def test_lets_provider_detect_when_source_unknown(monkeypatch, settings_enabled):
    """When the agent's own detection failed, the provider detects source language instead."""
    captured = {}

    def capture(text, source_language, target_language):
        captured["source"] = source_language
        return "Hello"

    monkeypatch.setattr(f"{MODULE}._translate_sync", capture)

    assert await translate_with_secondary_provider("Chào bạn", "en") == "Hello"
    assert captured["source"] == ""
