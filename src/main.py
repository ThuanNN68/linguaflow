"""FastAPI application entry point.

Wires the CORS middleware, the REST router and the WebSocket router — both under
`/api/v1` — and exposes `/health` for deployment probes. The translation agent is
reached through the chat flow, not from here; see `src/agents/graph.py`.
"""

# ruff: noqa: E402 - the guarded `sentence_transformers` import below has to
# run before every other import in this file. See the comment on it.

import os
import sys
from pathlib import Path

# Same file `Settings` reads (`src/config.py` anchors it the same way). Resolved
# from this module rather than the working directory: a service started with a
# cwd other than the repo root would otherwise read no `.env` here while
# `Settings` read the real one, and the two would disagree about what is on.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


# Before every other import in this file, deliberately, and before anything
# under `src.` in particular.
#
# `sentence-transformers` pulls torch, and on Windows torch's extension modules
# abort the process with an access violation — not a Python exception, so no
# `try`/`except` anywhere can catch it — if any part of this application's
# import graph is already loaded when they initialise. Measured: importing
# `sentence_transformers` before `src.main` succeeds, after it aborts, and the
# usual `KMP_DUPLICATE_LIB_OK` workaround does not help.
#
# The reranker and the local embedding fallback both used to import it lazily,
# from inside a request. That is how a second question to the assistant killed
# the server outright, taking every open WebSocket with it.
#
# The setting is read from the raw environment here rather than through
# `Settings`, because importing `src.config` is already too late.
def _setting_before_config_is_importable(name: str, default: str) -> str:
    """Read one setting without importing anything from `src`."""
    raw = os.environ.get(name)
    if raw is None:
        try:
            with open(_ENV_FILE, encoding="utf-8") as handle:
                for line in handle:
                    key, _, value = line.partition("=")
                    if key.strip() == name:
                        raw = value
                        break
        except OSError:
            raw = None
    return (raw if raw is not None else default).strip().strip('"').strip("'")


def _local_models_wanted() -> bool:
    """Whether anything in this deployment may need a local model.

    Two features want one, not just the reranker: the assistant's cross-encoder,
    and the `local` embedding provider — used either directly or as the quota
    fallback ADR-25 added, which is the whole point of that safety net. Gating
    the import on the reranker alone meant `ASSISTANT_RERANK_ENABLED=false`
    silently took the embedding fallback down with it, so a deployment that
    turned off reranking lost semantic glossary matching the next time the
    embedding provider hit its quota — with nothing in the logs naming the flag
    that caused it.

    Always false under pytest, for the reason `embeddings.py` already gives
    about its own fallback: by the time `conftest.py` imports this module the
    test session has loaded plenty else, which is exactly the ordering that
    makes the torch import abort — and a suite that quietly pulls half a
    gigabyte is one nobody will run twice. Tests that mean to exercise a local
    model construct it explicitly.
    """
    if "pytest" in sys.modules:
        return False
    off = {"0", "false", "no", "off"}
    if _setting_before_config_is_importable("ASSISTANT_RERANK_ENABLED", "true").lower() not in off:
        return True
    return "local" in {
        _setting_before_config_is_importable("EMBEDDING_PROVIDER", "gemini").lower(),
        _setting_before_config_is_importable("EMBEDDING_FALLBACK_PROVIDER", "local").lower(),
    }


if _local_models_wanted():
    try:
        import sentence_transformers  # noqa: F401
    except Exception:  # pragma: no cover - depends on what is installed
        # Absent is fine and expected on an image built without it (ADR-18):
        # `preload_local_models()` reports it and both features stay off.
        pass

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.requests import Request
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.gzip import GZipMiddleware

from src.agents.observability import verify_tracing_credentials
from src.api.admin import router as admin_router
from src.api.metrics import router as metrics_router
from src.api.routes import router
from src.api.system_health import router as system_health_router
from src.api.websocket import connection_manager
from src.api.websocket import router as websocket_router
from src.config import configure_logging, get_settings
from src.core.rate_limit import AuthRateLimitMiddleware, limiter
from src.database import get_db, get_engine
from src.observability import configure_runtime_telemetry, shutdown_runtime_telemetry
from src.services.assistant.agent_consent import ConsentRequiredError
from src.services.calendar.reminder_scheduler import start_reminder_scheduler
from src.services.shared.embeddings import preload_local_models
from src.services.voice.voice_transcription import recover_pending_voice_transcriptions
from src.services.delivery.webhooks import recover_webhook_deliveries, stop_webhook_deliveries

logger = logging.getLogger(__name__)

# Records whether the guarded import at the top of this file succeeded, so the
# reranker and the local embedding fallback both know whether they may build a
# model. Cheap here: the module is already in `sys.modules` or already known to
# be absent.
#
# The condition must stay in step with `_local_models_wanted()` above. Setting
# the flag for a feature whose import was skipped would send that feature back
# to importing torch itself, from inside a request — the crash this whole
# arrangement exists to prevent.
_settings = get_settings()
if _settings.assistant_rerank_enabled or "local" in {
    _settings.embedding_provider,
    _settings.embedding_fallback_provider,
}:
    preload_local_models()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown lifecycle events."""
    settings = get_settings()
    configure_logging(settings)
    logger.info("Starting %s in %s mode", settings.app_name, settings.app_env)

    # Blocking, so it runs once here rather than on any request path.
    verify_tracing_credentials()

    # Empty REDIS_URL deliberately preserves the in-process development mode.
    # When configured, every worker subscribes before it accepts traffic.
    await connection_manager.start(settings.redis_url)

    # The schema is not created here. Alembic owns it (ADR-06), and the
    # container runs `alembic upgrade head` before this process starts, so an
    # application that also created tables would let the two disagree in silence
    # — `create_all` adds missing tables but never alters an existing one.

    # The reminder clock. Started here because this is the only place with a
    # lifecycle, and stopped below so a reload does not leave a second loop
    # running against the same table (ADR-33).
    scheduler = start_reminder_scheduler(publisher=connection_manager, settings=settings)
    recovered_voice_messages = await recover_pending_voice_transcriptions(
        publisher=connection_manager,
    )
    if recovered_voice_messages:
        logger.info("Recovered %s pending voice transcription(s)", recovered_voice_messages)
    recovered_webhooks = await recover_webhook_deliveries()
    if recovered_webhooks:
        logger.info("Recovered %s pending webhook delivery attempt(s)", recovered_webhooks)
    app.state.startup_monotonic = time.monotonic()
    app.state.reminder_scheduler = scheduler

    yield

    if scheduler is not None:
        scheduler.shutdown(wait=False)
    await connection_manager.stop()
    await stop_webhook_deliveries()
    shutdown_runtime_telemetry()
    logger.info("Shutting down")


app = FastAPI(
    title=_settings.app_name,
    description="Real-time multilingual collaboration API",
    version="1.0.0",
    lifespan=lifespan,
)

_SENSITIVE_FIELD_NAMES = frozenset(
    {
        "password",
        "new_password",
        "otp",
        "token",
        "access_token",
        "refresh_token",
        "jwt_secret",
        "secret",
    }
)


def _sanitize_sensitive_data(val: Any) -> Any:
    """Recursively redact values of sensitive keys at arbitrary depth."""
    if isinstance(val, dict):
        sanitized = {}
        for k, v in val.items():
            if isinstance(k, str) and k.lower() in _SENSITIVE_FIELD_NAMES:
                sanitized[k] = "[REDACTED]"
            else:
                sanitized[k] = _sanitize_sensitive_data(v)
        return sanitized
    elif isinstance(val, list | tuple):
        return [_sanitize_sensitive_data(item) for item in val]
    return val


def _redact_validation_errors(errors: list[dict]) -> list[dict]:
    """Redact raw values of sensitive fields and nested sensitive keys from validation error details."""
    cleaned = []
    for err in errors:
        item = dict(err)
        loc = item.get("loc", ())
        is_loc_sensitive = any(isinstance(k, str) and k.lower() in _SENSITIVE_FIELD_NAMES for k in loc)
        if is_loc_sensitive and "input" in item:
            item["input"] = "[REDACTED]"
        elif "input" in item:
            item["input"] = _sanitize_sensitive_data(item["input"])
        cleaned.append(item)
    return cleaned


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Return 422 with redacted sensitive fields to prevent secret echoing."""
    encoded_errors = jsonable_encoder(exc.errors())
    return JSONResponse(
        status_code=422,
        content={"detail": _redact_validation_errors(encoded_errors)},
    )


@app.exception_handler(ConsentRequiredError)
async def consent_required_handler(request: Request, exc: ConsentRequiredError):
    """Return 403 CONSENT_REQUIRED naming the scope the caller still needs.

    Handled once here rather than in each route's except chain: every assistant
    endpoint needs the same answer, and the ones not written yet should get it
    without anyone remembering to add a clause.

    The scope travels in the body because this 403 is one the user can clear
    themselves — the interface reads it and opens that permission, instead of
    making them hunt through settings (`docs/api/contract.md` §6).
    """
    return JSONResponse(
        status_code=403,
        content={
            "code": "CONSENT_REQUIRED",
            "message": "The assistant needs your permission for this action.",
            "scope": exc.scope,
            "detail": str(exc),
        },
    )


settings = get_settings()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(AuthRateLimitMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=settings.cors_origin_regex or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")
app.include_router(metrics_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")
app.include_router(system_health_router, prefix="/api/v1")
app.include_router(websocket_router, prefix="/api/v1")

# Instrument after middleware and routes are registered but before the ASGI app
# serves its first request. The engine is created here only when tracing is on.
if settings.otel_enabled:
    configure_runtime_telemetry(app, get_engine(), settings)


def _base_health_payload() -> dict[str, Any]:
    """Return stable service metadata shared by all deployment probes."""
    return {
        "status": "ok",
        "service": "LinguaFlow API",
        "version": app.version,
        "env": settings.app_env,
    }


@app.get("/health/live")
async def liveness() -> dict[str, Any]:
    """Prove that the API process is running without calling dependencies."""
    return {**_base_health_payload(), "checks": {"api": "ok"}}


async def _readiness_response(db: AsyncSession) -> dict[str, Any] | JSONResponse:
    """Confirm the API can serve requests that depend on PostgreSQL."""
    payload = _base_health_payload()
    try:
        # A deployment probe must fail within a bounded time, while still
        # allowing a cold managed-PostgreSQL TLS/pooler handshake to complete.
        async with asyncio.timeout(settings.database_readiness_timeout_seconds):
            await db.execute(select(1))
    except Exception as exc:
        await db.rollback()
        logger.error("Database readiness check failed (%s): %s", type(exc).__name__, exc)
        payload.update(
            {
                "status": "unavailable",
                "checks": {"api": "ok", "database": "unavailable"},
            }
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=payload,
        )

    payload["checks"] = {"api": "ok", "database": "ok"}
    return payload


@app.get("/health", response_model=None)
async def health(db: AsyncSession = Depends(get_db)) -> dict[str, Any] | JSONResponse:
    """Production readiness probe retained at Render's existing path."""
    return await _readiness_response(db)


@app.get("/health/ready", response_model=None)
async def readiness(db: AsyncSession = Depends(get_db)) -> dict[str, Any] | JSONResponse:
    """Explicit readiness alias for infrastructure that separates probes."""
    return await _readiness_response(db)
