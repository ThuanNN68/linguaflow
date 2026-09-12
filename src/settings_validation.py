"""Cross-field settings validation, grouped by operational concern.

Field shape and environment loading stay in :mod:`src.config`.  Keeping the
rules here makes it possible to review storage, production and model-selection
safety independently without changing the flat environment-variable contract.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from src.config import Settings


def validate_storage_and_media(settings: Settings) -> None:
    """Validate credentials required by the selected durable media backend."""
    if settings.attachment_storage_backend == "s3":
        if not settings.s3_bucket.strip():
            raise ValueError("S3_BUCKET is required when ATTACHMENT_STORAGE_BACKEND=s3")
        if bool(settings.s3_access_key_id) != bool(settings.s3_secret_access_key):
            raise ValueError("S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY must be configured together")
    if settings.attachment_storage_backend == "supabase" and (
        not settings.supabase_url.strip() or not settings.supabase_server_key
    ):
        raise ValueError(
            "SUPABASE_URL and SUPABASE_SECRET_KEY are required for the Supabase attachment backend"
        )
    if settings.tts_provider == "openai" and not settings.openai_api_key.strip():
        raise ValueError("OPENAI_API_KEY is required when TTS_PROVIDER=openai")
    if settings.tts_provider == "elevenlabs" and not settings.elevenlabs_api_key.strip():
        raise ValueError("ELEVENLABS_API_KEY is required when TTS_PROVIDER=elevenlabs")
    if settings.tts_provider == "google" and not settings.google_tts_api_key.strip():
        raise ValueError("GOOGLE_TTS_API_KEY is required when TTS_PROVIDER=google")
    if bool(settings.web_push_vapid_public_key) != bool(settings.web_push_vapid_private_key):
        raise ValueError("WEB_PUSH_VAPID_PUBLIC_KEY and WEB_PUSH_VAPID_PRIVATE_KEY must be configured together")


def validate_production_delivery(settings: Settings) -> None:
    """Validate public URL and transactional-email safety in production."""
    if settings.app_env != "production":
        return
    if urlparse(settings.frontend_url).scheme != "https":
        raise ValueError("FRONTEND_URL must use https in production")
    if settings.email_provider in ("memory", "console"):
        raise ValueError(
            f"EMAIL_PROVIDER='{settings.email_provider}' is not allowed in production. "
            "Production requires EMAIL_PROVIDER='smtp' with valid SMTP credentials."
        )
    if settings.email_provider == "smtp":
        required = {
            "SMTP_HOST": settings.smtp_host,
            "SMTP_PORT": settings.smtp_port,
            "SMTP_USER": settings.smtp_user,
            "SMTP_PASSWORD": settings.smtp_password,
            "SMTP_FROM_EMAIL": settings.smtp_from_email,
        }
        missing = [name for name, value in required.items() if not value or not str(value).strip()]
        if missing:
            raise ValueError(f"Production SMTP configuration is missing required fields: {', '.join(missing)}")


def validate_telemetry(settings: Settings) -> None:
    """Require an explicit collector whenever distributed tracing is enabled."""
    if settings.otel_enabled and not settings.otel_exporter_otlp_endpoint.strip():
        raise ValueError("OTEL_EXPORTER_OTLP_ENDPOINT is required when OTEL_ENABLED=true")


def warn_model_quality(settings: Settings) -> None:
    """Emit actionable warnings for valid but misleading model combinations."""
    if settings.semantic_glossary_enabled and settings.glossary_similarity_threshold == 0:
        from src.services.shared.embeddings import DEFAULT_MODELS, GLOSSARY_THRESHOLDS

        model = settings.embedding_model or DEFAULT_MODELS.get(settings.embedding_provider, "")
        if model not in GLOSSARY_THRESHOLDS:
            logging.getLogger(__name__).warning(
                "SEMANTIC_GLOSSARY_ENABLED is on but %r has no measured threshold, "
                "so it will match almost nothing. Measure it and add a row to "
                "GLOSSARY_THRESHOLDS, or set GLOSSARY_SIMILARITY_THRESHOLD to override.",
                model,
            )

    provider, model = settings.resolve_assistant_llm()
    judge_provider, judge_model = settings.resolve_assistant_judge()
    if (provider, model) == (judge_provider, judge_model):
        logging.getLogger(__name__).warning(
            "The Assistant Agent's judge is the same model that generates (%s/%s). "
            "Scores from this configuration are self-judged and are not comparable "
            "with other runs. Set ASSISTANT_JUDGE_PROVIDER.",
            judge_provider,
            judge_model or "<provider default>",
        )
