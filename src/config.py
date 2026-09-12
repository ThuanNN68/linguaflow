"""Application configuration, loaded once per process from the environment.

Fields are grouped into blocks by concern; add a setting inside the block it
belongs to rather than at the end of the class, so parallel branches editing
different concerns do not collide.

`jwt_secret` is the one field with no usable default — it must come from the
environment. Everything else defaults so the test suite and the evaluation
harness can run without a `.env` file.
"""

import logging
import sys
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Values that look configured but are not. Everything here has been copied out
# of a template at some point, so none of them may guard a real deployment.
_PLACEHOLDER_JWT_SECRETS = frozenset({"your-secret-key-here", "change-me", "changeme", "secret", "dev-secret"})
_MIN_PRODUCTION_JWT_SECRET_LENGTH = 32
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Application configuration loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        # Resolve from this module rather than the caller's working directory.
        # Commands such as pytest may run from `frontend/`, but must still load
        # the repository's shared local configuration.
        env_file=_PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = "LinguaFlow"
    app_env: Literal["development", "production", "test"] = "development"
    app_port: int = Field(default=8000, ge=1, le=65535)
    app_host: str = "0.0.0.0"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    cors_origins: str = "http://localhost:3000"
    public_frontend_origin: str = ""
    cors_origin_regex: str = ""
    # Public browser address used in transactional-email links. This is kept
    # separate from CORS because an API may accept several trusted origins,
    # while a password-reset email must lead to exactly one canonical site.
    frontend_url: str = "http://localhost:3000"

    # LLM
    llm_provider: Literal["groq", "deepseek", "gemini", "openai", "mistral"] = "groq"
    llm_model: str = ""  # Empty = use the provider default (see services/llm.py)
    # Translation is not a creative task — a low temperature keeps the model on
    # the format rules in TRANSLATE_SYSTEM_PROMPT instead of paraphrasing.
    llm_temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    llm_timeout_seconds: int = Field(default=10, ge=1, le=120)
    # Caps generation cost. A chat message never needs more; anything longer is
    # the model explaining itself, which validate_output rejects anyway.
    llm_max_tokens: int = Field(default=1024, ge=64, le=8192)

    # Who scores the evaluation runs, and with which model. Both empty means
    # "the same provider that did the translating", which the report then flags
    # as self-judging (ADR-17). They live in configuration rather than only as
    # command-line flags because the judge is what a score is comparable
    # against: two runs judged by different models are not the same measurement,
    # and a flag typed differently on two days is the easiest way to end up with
    # exactly that without noticing.
    llm_judge_provider: Literal["", "groq", "deepseek", "gemini", "openai", "mistral"] = ""
    judge_model: str = ""  # Empty = the judge provider's default model

    # The Assistant Agent picks its own models, independently of translation.
    # Not a convenience: the two agents are judged on different things. A
    # translation is scored against a reference sentence and must answer inside
    # NFR-01, while the assistant reads long threads, plans several steps and is
    # scored on whether it invented anything — so the model that is right for one
    # is routinely wrong for the other, and the retrieval accuracy this agent
    # needs is far higher than the translation path ever required (ADR-37).
    #
    # Empty means "inherit the translation setting", so a deployment that has
    # configured nothing behaves exactly as it does today. Same arrangement as
    # `stt_provider` / `stt_model` below: an independent role whose configuration
    # never mutates another role's.
    assistant_llm_provider: Literal["", "groq", "deepseek", "gemini", "openai", "mistral"] = ""
    assistant_llm_model: str = ""
    assistant_judge_provider: Literal["", "groq", "deepseek", "gemini", "openai", "mistral"] = ""
    assistant_judge_model: str = ""
    assistant_embedding_provider: Literal["", "gemini", "openai", "local"] = ""
    assistant_embedding_model: str = ""

    # Assistant retrieval is always on — there is deliberately no flag to turn it
    # off. `RAG_CONTEXT_ENABLED` governs the *translation* agent's semantic
    # context and stays off (ADR-27); the assistant cannot answer "what did we
    # decide about the deadline" without reaching past the recent window, so for
    # it retrieval is the feature rather than an optimisation. What still gates
    # it is the user's `store_memory` consent, which is a permission, not a
    # configuration switch.
    #
    # Two numbers because retrieval runs in two stages: `top_k` is how many
    # candidates the hybrid search hands to the reranker, `top_n` how many
    # survive into the prompt. Widening the first costs a cheap vector scan;
    # widening the second costs context and invites the model to pad the answer
    # with near-misses.
    assistant_retrieval_top_k: int = Field(default=8, ge=1, le=50)
    assistant_rerank_top_n: int = Field(default=4, ge=1, le=20)
    # The assistant writes longer than the translator does, and needs its own
    # ceiling for it. `LLM_MAX_TOKENS` is sized for one translated chat message
    # -- the comment on it says so -- and sharing it truncated summaries and
    # task lists in the middle of a sentence, which reads as the assistant
    # trailing off rather than as a limit being hit.
    assistant_llm_max_tokens: int = Field(default=4096, ge=256, le=8192)
    # The reranker is the one part of this stack that runs a local model, so it
    # is also the one part an operator may need to switch off without editing
    # code: `sentence-transformers` pulls torch into the process, and a host
    # that cannot load it took the whole server down rather than degrading.
    # False keeps the fused hybrid ranking, which is the same fallback a
    # deployment without the package already gets.
    assistant_rerank_enabled: bool = True

    # API key per provider — only the one matching LLM_PROVIDER needs a value
    groq_api_key: str = ""
    deepseek_api_key: str = ""
    # Accepts either name. Google's own documentation and SDK use
    # `GEMINI_API_KEY`, so a .env written from those docs was read as no key at
    # all — and `embed` reports every failure as None, so the visible effect was
    # a quota error from the *old* key rather than anything pointing at the new
    # one. Same trap, same fix, as LANGFUSE_HOST below.
    google_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    )
    openai_api_key: str = ""
    mistral_api_key: str = ""

    # Speech-to-text is configured independently from LLM_PROVIDER. Gemini STT
    # reuses the existing GOOGLE_API_KEY/GEMINI_API_KEY resolution above, but
    # changing either provider/model role must never mutate the other.
    stt_provider: Literal["gemini", "openai"] = "gemini"
    stt_model: str = "gemini-3.5-transcribe"
    stt_fallback_provider: Literal["", "gemini", "openai"] = "openai"
    stt_fallback_model: str = "gpt-4o-mini-transcribe"
    stt_timeout_seconds: int = Field(default=60, ge=1, le=300)
    stt_retry_attempts: int = Field(default=3, ge=1, le=5)

    # Google Identity Services authentication.
    #
    # The client ID is not a secret — the browser needs the same value to
    # request an ID token. Declared once here; it used to appear twice in this
    # file, the later one silently shadowing the earlier, which was harmless
    # only until a second Google field was added beside one of them.
    #
    # Obtain both from Google Cloud Console → APIs & Services → Credentials →
    # OAuth client ID (Web application).
    google_oauth_client_id: str = ""
    # Agent — number of recent messages used as translation context (PRD: 3-5)
    agent_context_size: int = Field(default=5, ge=0, le=20)
    # Deadline for one whole translation run, covering detection, the LLM call
    # and the secondary provider. The per-call timeouts below do not bound the
    # total, so this is what stops a background task running forever (ADR-14).
    translation_timeout_seconds: int = Field(default=30, ge=5, le=300)
    # How often the reminder queue is polled (ADR-33). Sixty seconds is the
    # resolution a reminder is worth — nobody can tell 14:45:00 from 14:45:40 —
    # and a tighter loop is a query per second against a usually empty table.
    reminder_scan_interval_seconds: int = Field(default=60, ge=10, le=3600)
    # Set to false to stop the background scheduler without removing the code
    # path. Tests need it off: a loop firing mid-suite would deliver another
    # test's reminders and make failures depend on timing (ADR-15's rule about
    # every background flow having a switch).
    reminder_scheduler_enabled: bool = True
    # Optional shared realtime backplane. Empty keeps the lightweight
    # single-process manager used in development and tests.
    redis_url: str = ""

    # Embeddings (ADR-25). pgvector stores and compares the vectors; something
    # still has to produce them, and the requirement that decides the choice is
    # multilingual reach: unless "staging env" and "staging environment" land near
    # each other, grouping corrections by meaning is pointless.
    #
    # `local` runs sentence-transformers in-process — no quota, and no message
    # leaves the container, which is the kill switch ADR-15 asks for. It costs
    # a large dependency, so it is an opt-in extra rather than a requirement.
    embedding_provider: Literal["gemini", "openai", "local"] = "gemini"
    # Empty means the provider's default. The width, unlike this, is *not*
    # configurable: it is EMBEDDING_DIM in src/database/models.py, because two
    # developers with different .env files would otherwise describe two
    # different schemas.
    embedding_model: str = ""
    # Where embedding goes when the configured provider runs out of quota.
    # Empty disables it; `local` embeds in-process, needs no key and cannot be
    # rate limited, so a translation never stops because an embedding budget
    # did — and the reader is never told, because there is nothing they could
    # do about it (ADR-25).
    #
    # On by default: an exhausted quota should not become an outage, and the
    # two local packages now ship in requirements.txt for exactly this. The
    # cost is real — they pull torch, roughly half a gigabyte, into the image.
    # `embed_with_model` declines to fall back inside a test run, because a
    # test that quietly loads a model that size is a test nobody can run twice.
    embedding_fallback_provider: Literal["", "local"] = "local"

    # Each adds an embedding call to the request path, and NFR-01 is the
    # tightest figure in the project, so both were turned on against
    # measurements rather than hopes — and only one of them earned it.
    # Semantic glossary matching is on; semantic *context* retrieval is not.
    # They were measured separately and the answers differ: retrieval could not
    # find the line that mattered often enough to pay for, while term matching
    # separates cleanly on the model named below (ADR-26, ADR-27).
    semantic_glossary_enabled: bool = True
    rag_context_enabled: bool = False
    # Override for the per-model table in `services/embeddings.py`. Zero means
    # "use the table", which is the normal setting: a cosine threshold belongs
    # to an embedding space, so one number cannot serve several models, and the
    # provider now changes by itself when a quota runs out. Set this only to
    # measure a new model without editing code.
    #
    # Every threshold in that table is set for precision over recall. A term
    # below the line is absent from the glossary and the model translates it
    # itself; a term above it is *forced* into the output by the prompt. The
    # two mistakes do not cost the same.
    glossary_similarity_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    rag_top_k: int = Field(default=3, ge=1, le=10)

    # Secondary translation provider tried when the LLM path fails (ADR-07).
    # Disable to go straight back to returning the untranslated message.
    fallback_translator_enabled: bool = True
    fallback_translator_timeout_seconds: int = Field(default=5, ge=1, le=30)

    # Ordinary direct RTC calls.  The Daily server API key never reaches the
    # frontend; the browser receives only a short-lived meeting token.
    rtc_provider: Literal["daily", "disabled"] = "daily"
    daily_api_key: str = ""
    daily_api_base: str = "https://api.daily.co/v1"
    call_ring_timeout_seconds: int = Field(default=45, ge=10, le=300)
    call_token_ttl_seconds: int = Field(default=3600, ge=60, le=86400)

    # Observability (F-03.4). Which backend receives the traces, or none.
    #
    # A switch rather than a hard-wired vendor because a trace backend is the
    # one dependency whose failure mode is silence: it never breaks a
    # translation, so the only way to tell a working exporter from a broken one
    # is to be able to point the same flow at another and compare (ADR-29).
    observability_provider: Literal["braintrust", "langfuse", "none"] = "braintrust"

    # Braintrust. An empty key disables tracing whatever the provider says.
    braintrust_api_key: str = ""
    # The project traces are filed under. Braintrust creates it on first use, so
    # a typo here does not fail — it opens a second, empty project instead.
    braintrust_project: str = "linguaflow"

    # Langfuse, kept selectable after the move to Braintrust. Empty keys disable
    # tracing.
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    # Accepts either env name. The Langfuse SDK itself reads LANGFUSE_BASE_URL,
    # so a .env written against the SDK's own docs would otherwise be dropped by
    # `extra="ignore"` and silently fall back to the EU default — which answers
    # 401 to a US key. The region here must match the region the keys came from.
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com",
        validation_alias=AliasChoices("LANGFUSE_HOST", "LANGFUSE_BASE_URL"),
    )

    # Vendor-neutral service tracing. This is separate from the LLM callback
    # provider above: OTEL follows HTTP and SQL operations across services,
    # while Braintrust/Langfuse capture model-specific inputs and outputs.
    otel_enabled: bool = False
    otel_service_name: str = "linguaflow-api"
    otel_exporter_otlp_endpoint: str = ""
    otel_exporter_otlp_headers: str = ""
    otel_trace_sample_ratio: float = Field(default=0.1, ge=0.0, le=1.0)

    # Database. The driver must be async — `create_async_engine` cannot open a
    # bare `postgresql://` URL, so the default carries the asyncpg driver.
    #
    # PostgreSQL rather than SQLite even for development (ADR-22): the glossary,
    # the correction log and the message memory are searched by cosine distance
    # over pgvector columns, and SQLite has no `vector` type at all. Keeping
    # SQLite for development would mean a second retrieval path that production
    # never runs. `docker compose up -d postgres` provides it.
    database_url: str = "postgresql+asyncpg://linguaflow:linguaflow@localhost:5432/linguaflow"
    # PostgreSQL connections opened per process. Kept small on purpose: one
    # WebSocket holds one session for as long as it stays open, so the pool has
    # to be sized against concurrent sockets rather than requests per second.
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_max_overflow: int = Field(default=10, ge=0, le=50)
    # Managed PostgreSQL poolers can leave an idle TCP connection stale. Recycle
    # it before the provider-side idle timeout and fail a saturated pool rather
    # than making an API request wait indefinitely.
    database_pool_recycle_seconds: int = Field(default=900, ge=60, le=86_400)
    database_pool_timeout_seconds: int = Field(default=30, ge=1, le=120)
    # SQLAlchemy echo includes bound parameters. Leaving it implicitly on in
    # development therefore writes message text and completed voice transcripts
    # to the console. Keep it opt-in in every environment.
    database_echo: bool = False
    # The first managed-PostgreSQL connection can take longer than a pooled
    # request during a cold start. Keep readiness strict, but avoid reporting a
    # healthy database as unavailable before its TLS/pooler handshake finishes.
    database_readiness_timeout_seconds: int = Field(default=30, ge=5, le=120)

    # Vector Store
    chroma_persist_dir: str = "./data/chroma"
    attachment_storage_backend: Literal["auto", "local", "supabase", "s3"] = "auto"
    upload_dir: str = "./data/uploads"
    max_upload_size_bytes: int = Field(default=20 * 1024 * 1024, ge=1)
    supabase_url: str = ""
    # Supabase server-only credential; it must never be exposed to a browser.
    # ``SUPABASE_SECRET_KEY`` is the canonical name.  Keep the historical
    # ``SUPABASE_SERVICE_ROLE_KEY`` alias so existing Render environments do
    # not fail during startup after the storage validation was tightened.
    supabase_secret_key: str = Field(
        default="",
        validation_alias=AliasChoices("SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY"),
    )
    supabase_storage_bucket: str = "attachments"
    s3_bucket: str = ""
    s3_region: str = "us-east-1"
    s3_endpoint_url: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_session_token: str = ""
    s3_force_path_style: bool = False
    attachment_presign_ttl_seconds: int = Field(default=300, ge=30, le=3600)

    # Server-side fallback when Web Speech API is unavailable in the browser.
    # Providers are opt-in because synthesis has a direct per-character cost.
    tts_provider: Literal["disabled", "openai", "elevenlabs", "google"] = "disabled"
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "alloy"
    tts_timeout_seconds: int = Field(default=30, ge=3, le=120)
    elevenlabs_api_key: str = ""
    elevenlabs_api_base: str = "https://api.elevenlabs.io/v1"
    google_tts_api_key: str = ""

    # Web Push is disabled until a VAPID key pair is configured.
    web_push_vapid_public_key: str = ""
    web_push_vapid_private_key: str = ""
    web_push_subject: str = "mailto:admin@localhost"

    # Durable outbound webhook delivery. Admins select from this bounded set.
    webhook_timeout_seconds: int = Field(default=10, ge=1, le=30)
    webhook_max_attempts: int = Field(default=5, ge=1, le=10)
    webhook_allow_private_networks: bool = False

    # JWT Authentication
    jwt_secret: str = ""  # Required: set in environment
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = Field(default=1440, ge=1, le=10080)  # 24h default, max 7 days
    refresh_expire_days: int = Field(default=30, ge=1, le=90)
    password_reset_expire_minutes: int = Field(default=30, ge=5, le=120)

    # Email & OTP Verification (Batch F)
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_from_name: str = "LinguaFlow"
    smtp_use_tls: bool = True
    smtp_timeout_seconds: int = Field(default=15, ge=3, le=60)
    email_provider: Literal["smtp", "console", "memory"] = "memory"

    @field_validator("database_url")
    @classmethod
    def database_url_must_name_an_async_driver(cls, v: str) -> str:
        """Rewrite a plain PostgreSQL URL onto the async driver.

        Managed databases hand out `postgres://…` or `postgresql://…`, which
        SQLAlchemy reads as the synchronous psycopg driver. `create_async_engine`
        then fails with "the asyncio extension requires an async driver" — an
        error that says nothing about the connection string it came from. Since
        asyncpg is the only PostgreSQL driver this project installs, the fix is
        never a different one, so it is applied here rather than asked of
        whoever pastes the URL into the deployment settings.
        """
        for prefix in ("postgresql+", "postgres+"):
            if v.startswith(prefix):
                return v
        for prefix in ("postgresql://", "postgres://"):
            if v.startswith(prefix):
                return f"postgresql+asyncpg://{v[len(prefix) :]}"
        return v

    @property
    def cors_origin_list(self) -> list[str]:
        """The allowed origins as a list, from the comma-separated setting.

        Entries are stripped: a value written the way a human writes a list,
        `http://a, http://b`, would otherwise produce ` http://b`, which matches
        no browser Origin header and fails as a CORS error with nothing in the
        logs to explain it.
        """
        origins = [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]
        # The browser-facing local development app is part of the documented
        # contract. Keep these origins available even when a machine-level
        # CORS_ORIGINS variable overrides the repository's .env value.
        for local_origin in (
            "http://localhost:3000",
            "http://localhost:3001",
            "http://localhost:3002",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:3001",
            "http://127.0.0.1:3002",
        ):
            if local_origin not in origins:
                origins.append(local_origin)
        if self.public_frontend_origin and self.public_frontend_origin not in origins:
            origins.append(self.public_frontend_origin)
        return origins

    @field_validator("jwt_secret")
    @classmethod
    def jwt_secret_must_be_set(cls, v: str, info) -> str:
        """Reject a missing secret anywhere, and a guessable one in production.

        Anyone holding this value can mint a token for any account, so in
        production the placeholder that ships in `.env.example` — which passes an
        "is it empty" check perfectly well — is treated as no secret at all.
        """
        generate = 'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(48))"'
        if not v:
            raise ValueError(f"JWT_SECRET environment variable is required. {generate}")
        if info.data.get("app_env") == "production":
            if v in _PLACEHOLDER_JWT_SECRETS:
                raise ValueError(f"JWT_SECRET is still the example value. {generate}")
            if len(v) < _MIN_PRODUCTION_JWT_SECRET_LENGTH:
                raise ValueError(
                    "JWT_SECRET must be at least "
                    f"{_MIN_PRODUCTION_JWT_SECRET_LENGTH} characters in production. "
                    f"{generate}"
                )
        return v

    @field_validator("frontend_url")
    @classmethod
    def validate_frontend_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("FRONTEND_URL must be an absolute http(s) URL")
        return normalized

    @model_validator(mode="after")
    def validate_remote_storage_config(self) -> "Settings":
        """Run the storage/media validation group."""
        from src.settings_validation import validate_storage_and_media

        validate_storage_and_media(self)
        return self

    @model_validator(mode="after")
    def _warn_unmeasured_embedding_model(self) -> "Settings":
        """Run model-quality warnings after all model roles are resolved."""
        from src.settings_validation import warn_model_quality

        warn_model_quality(self)
        return self

    @model_validator(mode="after")
    def validate_production_and_email_config(self) -> "Settings":
        """Run public-delivery and email validation as one concern."""
        from src.settings_validation import validate_production_delivery

        validate_production_delivery(self)
        return self

    @model_validator(mode="after")
    def validate_observability_config(self) -> "Settings":
        """Run vendor-neutral telemetry validation."""
        from src.settings_validation import validate_telemetry

        validate_telemetry(self)
        return self

    def resolve_assistant_llm(self) -> tuple[str, str]:
        """Which provider and model the Assistant Agent generates with.

        Empty assistant settings inherit the translation ones, so a deployment
        that configured nothing keeps working. The pair is resolved here rather
        than at each call site because "empty means inherit" is exactly the kind
        of rule that gets applied in three places and forgotten in a fourth —
        and the fourth then silently runs the wrong model while the report says
        otherwise.

        Returns:
            ``(provider, model)``. The model may be empty, which every caller
            passes to `get_llm` as "use that provider's default".
        """
        provider = self.assistant_llm_provider or self.llm_provider
        # The model only carries over when the provider did. `LLM_MODEL` names a
        # model *on the translation provider*, so inheriting it onto a different
        # provider would ask, say, Mistral for a Gemini model and fail at the
        # first call.
        if self.assistant_llm_model:
            return provider, self.assistant_llm_model
        return provider, (self.llm_model if provider == self.llm_provider else "")

    def resolve_assistant_judge(self) -> tuple[str, str]:
        """Which provider and model score the Assistant Agent's output.

        Falls back to the generating model rather than to the translation judge:
        a judge chosen for scoring sentence-level translation has no standing to
        grade a multi-step plan, so inheriting it would look configured while
        measuring something else. Falling back to the generator is at least an
        honest self-judge, which `_warn_assistant_judges_itself` then reports.
        """
        if self.assistant_judge_provider:
            return self.assistant_judge_provider, self.assistant_judge_model
        return self.resolve_assistant_llm()

    def resolve_assistant_embedding(self) -> tuple[str, str]:
        """Which provider and model embed the assistant's chunks and queries.

        Both halves must travel together to every caller. Vectors from two models
        occupy different spaces, so `assistant_chunks.embedding_model` records
        which one produced each row and retrieval filters on it — a query
        embedded by one model and compared against another's rows returns a
        confidently wrong ranking rather than an error.
        """
        provider = self.assistant_embedding_provider or self.embedding_provider
        if self.assistant_embedding_model:
            return provider, self.assistant_embedding_model
        return provider, (self.embedding_model if provider == self.embedding_provider else "")

    @property
    def supabase_server_key(self) -> str:
        """Return the Supabase server credential, if configured."""
        return self.supabase_secret_key.strip()


def _force_utf8_streams() -> None:
    """Make stdout/stderr able to carry every language the agent translates.

    On Windows the console defaults to the cp1252 code page, which can encode
    only Western European text. A single Vietnamese, Chinese, Japanese, Korean,
    Thai, Arabic, Hindi, Russian, Greek, Turkish or Polish character in a log
    record — a translated message quoted back in an exception, a `%r` of the
    detected text — then raises ``UnicodeEncodeError`` inside the logging
    handler instead of being written. Since that happens while reporting an
    earlier failure, the original error is lost as well.

    Both halves are needed: ``reconfigure`` makes Python emit UTF-8 bytes, and
    ``SetConsoleOutputCP(65001)`` makes the console read them as UTF-8 rather
    than as mojibake. ``errors="backslashreplace"`` is the last line of defence
    for a stream that cannot be switched at all (a pipe opened by another tool),
    so an unencodable character degrades to an escape rather than to a crash.

    Everything here is best-effort: a redirected or already-wrapped stream may
    expose neither method, and no logging setup is worth failing a process over.
    """
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:  # noqa: BLE001 - console code page is cosmetic
            pass

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:  # noqa: BLE001 - a detached stream cannot be fixed
            pass


def configure_logging(settings: Settings | None = None) -> None:
    """Apply LOG_LEVEL to the root logger.

    Without this the root logger stays at its WARNING default and every
    ``logger.info`` in the agent is discarded — including the record of when a
    translation fell back, which is the main operational signal the agent emits.

    LOG_LEVEL is applied to the ``src`` logger only, not the root. Setting the
    root level would switch on INFO for every installed library — ``httpx`` alone
    emits one request line per LLM call, which would bury the evaluation
    harness's own progress output.

    Also switches stdout/stderr to UTF-8, so a log line carrying non-Western
    text cannot raise ``UnicodeEncodeError`` on a Windows console — see
    `_force_utf8_streams`.

    Safe to call more than once: the handler is replaced rather than stacked, so
    repeated calls cannot duplicate every line.

    Args:
        settings: Configuration to read. Defaults to the process settings.
    """
    settings = settings or get_settings()

    # Before any handler is attached: a cp1252 console turns a Vietnamese log
    # line into UnicodeEncodeError rather than into output.
    _force_utf8_streams()

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    app_logger = logging.getLogger("src")
    app_logger.handlers.clear()
    app_logger.addHandler(handler)
    app_logger.setLevel(settings.log_level)
    # Handled here, so don't also hand records to whatever the root has attached.
    app_logger.propagate = False

    # Tracing failures happen on the OTel exporter's background thread, in a
    # logger outside the `src` tree. Left alone they reach stderr unformatted via
    # logging.lastResort and would vanish entirely the day a file handler is
    # added. Attached at WARNING so their routine INFO chatter stays out.
    for name in ("opentelemetry", "langfuse"):
        library_logger = logging.getLogger(name)
        library_logger.handlers.clear()
        library_logger.addHandler(handler)
        library_logger.setLevel(logging.WARNING)
        library_logger.propagate = False


@lru_cache
def get_settings() -> Settings:
    """Return the cached singleton Settings instance."""
    return Settings()
