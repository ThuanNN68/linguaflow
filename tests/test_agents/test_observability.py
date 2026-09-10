"""Tests for agent tracing (F-03.4), across both backends.

Focus: neither missing configuration nor a failed init may break the
translation flow, and the provider switch has to actually switch — a run
configured for Braintrust must not quietly export to Langfuse because its keys
happen to be in the environment.
"""

from __future__ import annotations

from unittest.mock import patch

from src.agents import observability
from src.agents.observability import (
    build_runnable_config,
    get_trace_handler,
    verify_tracing_credentials,
)
from src.config import Settings

MODULE = "src.agents.observability"


def setup_function() -> None:
    """Clear the cache between tests — the handler is memoised with lru_cache."""
    observability._init_trace_handler.cache_clear()


def make_settings(
    provider: str = "langfuse",
    public_key: str = "",
    secret_key: str = "",
    braintrust_key: str = "",
):
    """A stand-in for the process settings, carrying both providers' keys."""

    class FakeSettings:
        observability_provider = provider
        langfuse_public_key = public_key
        langfuse_secret_key = secret_key
        langfuse_host = "https://cloud.langfuse.com"
        braintrust_api_key = braintrust_key
        braintrust_project = "linguaflow-test"

    return FakeSettings()


def test_handler_is_none_when_langfuse_keys_missing():
    """Missing both keys must skip tracing and return None."""
    with patch(f"{MODULE}.get_settings", return_value=make_settings()):
        assert get_trace_handler() is None


def test_handler_is_none_when_only_public_key_set():
    """A missing secret_key must also skip tracing, not half-initialise it."""
    with patch(f"{MODULE}.get_settings", return_value=make_settings(public_key="pk")):
        assert get_trace_handler() is None


def test_config_is_empty_when_tracing_disabled():
    """When tracing is disabled, runnable config must be empty."""
    with patch(f"{MODULE}.get_settings", return_value=make_settings()):
        assert build_runnable_config() == {}


def test_metadata_is_passed_into_config():
    """Provided metadata keys must be attached under config metadata."""
    with patch(f"{MODULE}.get_settings", return_value=make_settings()):
        config = build_runnable_config(conversation_id="c1", message_id="m1")

    assert config["metadata"] == {"conversation_id": "c1", "message_id": "m1"}
    assert "callbacks" not in config


def test_metadata_drops_none_values():
    """None values in metadata arguments must be filtered out."""
    with patch(f"{MODULE}.get_settings", return_value=make_settings()):
        config = build_runnable_config(conversation_id="c1", translation_id=None)

    assert config["metadata"] == {"conversation_id": "c1"}


def test_handler_is_none_when_langfuse_init_fails():
    """A Langfuse failure (bad key, no connection) must still let the agent run."""
    settings = make_settings(public_key="pk", secret_key="sk")

    with (
        patch(f"{MODULE}.get_settings", return_value=settings),
        patch("langfuse.Langfuse", side_effect=RuntimeError("connection refused")),
    ):
        assert get_trace_handler() is None
        assert build_runnable_config() == {}


def test_langfuse_host_accepts_the_sdk_env_name(monkeypatch):
    """LANGFUSE_BASE_URL is the name the Langfuse SDK documents.

    A .env written against those docs used to be dropped by `extra="ignore"`,
    leaving the host on its EU default — which answers 401 to a US key, on a
    background thread, where nothing in this process would notice.
    """
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com")

    settings = Settings(_env_file=None, jwt_secret="test-secret")

    assert settings.langfuse_host == "https://us.cloud.langfuse.com"


def test_verify_is_false_when_tracing_is_disabled():
    """No keys means tracing is off by choice — probe nothing, report False."""
    with patch(f"{MODULE}.get_settings", return_value=make_settings()):
        assert verify_tracing_credentials() is False


def test_verify_is_true_when_langfuse_credentials_are_accepted():
    """A successful auth_check is the one signal that traces will arrive."""
    settings = make_settings(public_key="pk", secret_key="sk")
    client = type("Client", (), {"auth_check": lambda self: True})()

    with (
        patch(f"{MODULE}.get_settings", return_value=settings),
        patch(f"{MODULE}.get_trace_handler", return_value=object()),
        patch("langfuse.get_client", return_value=client),
    ):
        assert verify_tracing_credentials() is True


def test_verify_is_false_when_the_langfuse_probe_raises():
    """Tracing is optional: a rejected or unreachable host must not stop startup."""
    settings = make_settings(public_key="pk", secret_key="sk")

    with (
        patch(f"{MODULE}.get_settings", return_value=settings),
        patch(f"{MODULE}.get_trace_handler", return_value=object()),
        patch("langfuse.get_client", side_effect=RuntimeError("401 Unauthorized")),
    ):
        assert verify_tracing_credentials() is False


def test_provider_none_skips_tracing_even_with_keys_present():
    """`none` is a decision, not an absence — configured keys must not override it."""
    settings = make_settings(
        provider="none",
        public_key="pk",
        secret_key="sk",
        braintrust_key="sk-braintrust",
    )

    with patch(f"{MODULE}.get_settings", return_value=settings):
        assert get_trace_handler() is None
        assert verify_tracing_credentials() is False
        assert build_runnable_config() == {}


def test_braintrust_is_not_used_when_langfuse_is_the_chosen_provider():
    """The switch has to switch.

    Both providers' keys living in one `.env` is the normal state during a
    move between them, and the failure that state invites is exporting to the
    backend nobody is watching.
    """
    settings = make_settings(
        provider="langfuse",
        public_key="pk",
        secret_key="sk",
        braintrust_key="sk-braintrust",
    )
    handler = object()

    with (
        patch(f"{MODULE}.get_settings", return_value=settings),
        patch(f"{MODULE}._init_braintrust_handler") as braintrust_init,
        patch(f"{MODULE}._init_langfuse_handler", return_value=handler),
    ):
        assert get_trace_handler() is handler
        braintrust_init.assert_not_called()


def test_braintrust_handler_is_none_when_its_key_is_missing():
    """Selecting Braintrust without a key disables tracing rather than raising."""
    settings = make_settings(provider="braintrust")

    with patch(f"{MODULE}.get_settings", return_value=settings):
        assert get_trace_handler() is None
        assert build_runnable_config() == {}


def test_braintrust_handler_is_none_when_init_fails():
    """A Braintrust failure must degrade the same way a Langfuse one does."""
    settings = make_settings(provider="braintrust", braintrust_key="sk-test")

    with (
        patch(f"{MODULE}.get_settings", return_value=settings),
        patch("braintrust.init_logger", side_effect=RuntimeError("Invalid API key")),
    ):
        assert get_trace_handler() is None
        assert build_runnable_config() == {}


def test_braintrust_callback_is_attached_when_the_handler_builds():
    """The handler is only useful if it reaches `graph.ainvoke()`'s config."""
    settings = make_settings(provider="braintrust", braintrust_key="sk-test")
    handler = object()

    with (
        patch(f"{MODULE}.get_settings", return_value=settings),
        patch(f"{MODULE}._init_braintrust_handler", return_value=handler),
    ):
        config = build_runnable_config(conversation_id="c1")

    assert config["callbacks"] == [handler]
    assert config["metadata"] == {"conversation_id": "c1"}


def test_verify_is_true_when_braintrust_login_is_accepted():
    """`login` is the one Braintrust call that actually authenticates."""
    settings = make_settings(provider="braintrust", braintrust_key="sk-test")

    with (
        patch(f"{MODULE}.get_settings", return_value=settings),
        patch(f"{MODULE}.get_trace_handler", return_value=object()),
        patch("braintrust.login", return_value=None) as login,
    ):
        assert verify_tracing_credentials() is True
        login.assert_called_once()


def test_verify_is_false_when_braintrust_rejects_the_key():
    """A rejected key must be reported at startup, not discovered in an empty UI."""
    settings = make_settings(provider="braintrust", braintrust_key="ysk-typo")

    with (
        patch(f"{MODULE}.get_settings", return_value=settings),
        patch(f"{MODULE}.get_trace_handler", return_value=object()),
        patch("braintrust.login", side_effect=ValueError("Invalid API key")),
    ):
        assert verify_tracing_credentials() is False
