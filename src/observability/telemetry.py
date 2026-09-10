"""Optional OpenTelemetry instrumentation for HTTP, SQLAlchemy and HTTPX."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncEngine

    from src.config import Settings

logger = logging.getLogger(__name__)

_provider: Any | None = None
_status: dict[str, Any] = {"enabled": False, "configured": False, "service_name": None}


def _parse_headers(raw: str) -> dict[str, str]:
    """Parse OTLP's comma-separated ``key=value`` header format."""
    headers: dict[str, str] = {}
    for item in raw.split(","):
        if not item.strip():
            continue
        key, separator, value = item.partition("=")
        if not separator or not key.strip():
            raise ValueError("OTEL_EXPORTER_OTLP_HEADERS must contain comma-separated key=value pairs")
        headers[key.strip()] = value.strip()
    return headers


def configure_runtime_telemetry(app: FastAPI, engine: AsyncEngine, settings: Settings) -> bool:
    """Install process-wide OTEL tracing once, returning whether it is active.

    Imports are deliberately lazy. A development process with tracing disabled
    pays neither SDK startup time nor exporter threads.
    """
    global _provider, _status
    if not settings.otel_enabled:
        _status = {"enabled": False, "configured": False, "service_name": settings.otel_service_name}
        return False
    if _provider is not None:
        return True

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

    provider = TracerProvider(
        resource=Resource.create(
            {
                SERVICE_NAME: settings.otel_service_name,
                SERVICE_VERSION: app.version,
                "deployment.environment.name": settings.app_env,
            }
        ),
        sampler=ParentBased(TraceIdRatioBased(settings.otel_trace_sample_ratio)),
    )
    exporter = OTLPSpanExporter(
        endpoint=settings.otel_exporter_otlp_endpoint,
        headers=_parse_headers(settings.otel_exporter_otlp_headers),
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider, excluded_urls="/health,/health/live,/health/ready")
    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine, tracer_provider=provider)
    HTTPXClientInstrumentor().instrument(tracer_provider=provider)

    _provider = provider
    _status = {
        "enabled": True,
        "configured": True,
        "service_name": settings.otel_service_name,
        "sample_ratio": settings.otel_trace_sample_ratio,
        "exporter": "otlp/http",
    }
    logger.info("OpenTelemetry tracing enabled for %s", settings.otel_service_name)
    return True


def runtime_telemetry_status() -> dict[str, Any]:
    """Return non-secret configuration state for the admin health endpoint."""
    return dict(_status)


def shutdown_runtime_telemetry() -> None:
    """Flush queued spans during graceful application shutdown."""
    global _provider
    if _provider is not None:
        _provider.shutdown()
        _provider = None
