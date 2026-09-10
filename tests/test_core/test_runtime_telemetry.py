"""Tests for optional vendor-neutral runtime tracing configuration."""

import pytest

from src.config import Settings
from src.observability.telemetry import _parse_headers, configure_runtime_telemetry


def test_otel_requires_explicit_exporter_endpoint() -> None:
    with pytest.raises(ValueError, match="OTEL_EXPORTER_OTLP_ENDPOINT"):
        Settings(_env_file=None, jwt_secret="test-secret", otel_enabled=True)


def test_disabled_telemetry_does_not_import_or_instrument() -> None:
    settings = Settings(_env_file=None, jwt_secret="test-secret", otel_enabled=False)
    assert configure_runtime_telemetry(object(), object(), settings) is False


def test_otlp_headers_are_parsed_without_exposing_them_in_status() -> None:
    assert _parse_headers("Authorization=Bearer token, tenant=linguaflow") == {
        "Authorization": "Bearer token",
        "tenant": "linguaflow",
    }


def test_malformed_otlp_header_is_rejected() -> None:
    with pytest.raises(ValueError, match="key=value"):
        _parse_headers("missing-separator")
