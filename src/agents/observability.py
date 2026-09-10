"""Tracing for the Translation Agent (F-03.4).

The backend is chosen by `OBSERVABILITY_PROVIDER` — `braintrust` (the default
since 22/08), `langfuse`, or `none`. Both providers reach LangGraph the same
way, as a LangChain callback handler attached to `graph.ainvoke()`, so the
choice costs one function and no change anywhere on the translation path.

Braintrust is the current backend and Langfuse is kept selectable rather than
deleted, for the reason in ADR-29: a trace backend never breaks a translation
when it fails, so the only cheap way to tell a working exporter from a broken
one is to point the same flow at the other and compare.

Verified against braintrust 0.34.0, where the LangChain integration lives in
the main package (`braintrust.integrations.langchain`); the separate
`braintrust-langchain` distribution is deprecated. Verified against langfuse
4.14.3, where `CallbackHandler` lives in `langfuse.langchain` and only accepts
`public_key` — `secret_key` and `host` are configured on the `Langfuse` client.
Re-check this module if either pinned major version changes.

Tracing is optional and degrades silently throughout: an unset key, a missing
package or a failed client init all return None and leave the translation flow
untouched.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from src.config import Settings, get_settings

logger = logging.getLogger(__name__)


def _init_braintrust_handler(settings: Settings) -> Any | None:
    """Build the Braintrust callback handler, or None when unavailable."""
    if not settings.braintrust_api_key:
        logger.info("BRAINTRUST_API_KEY not set, skipping tracing")
        return None

    try:
        from braintrust import init_logger
        from braintrust.integrations.langchain import BraintrustCallbackHandler
    except ImportError:
        logger.info("braintrust package not installed, skipping tracing")
        return None

    try:
        # The logger names the project spans are filed under, and the handler
        # binds to it. Passing the key explicitly rather than relying on the
        # SDK reading the environment keeps one source of configuration: the
        # process may have been started with a `.env` the SDK never sees.
        project_logger = init_logger(
            project=settings.braintrust_project,
            api_key=settings.braintrust_api_key,
        )
        return BraintrustCallbackHandler(logger=project_logger)
    except Exception as exc:
        logger.warning("Braintrust init failed, skipping tracing: %s", exc)
        return None


def _init_langfuse_handler(settings: Settings) -> Any | None:
    """Build the Langfuse callback handler, or None when unavailable."""
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        logger.info(
            "LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY not set, skipping tracing"
        )
        return None

    try:
        from langfuse import Langfuse
        from langfuse.langchain import CallbackHandler
    except ImportError:
        logger.info("langfuse package not installed, skipping tracing")
        return None

    try:
        # Initialise the singleton client the handler will reuse
        Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        return CallbackHandler(public_key=settings.langfuse_public_key)
    except Exception as exc:
        logger.warning("Langfuse init failed, skipping tracing: %s", exc)
        return None


@lru_cache(maxsize=1)
def _init_trace_handler() -> Any | None:
    """Build the configured provider's callback handler once per process."""
    settings = get_settings()

    if settings.observability_provider == "none":
        logger.info("OBSERVABILITY_PROVIDER=none, skipping tracing")
        return None
    if settings.observability_provider == "braintrust":
        return _init_braintrust_handler(settings)
    return _init_langfuse_handler(settings)


def get_trace_handler() -> Any | None:
    """Return the configured callback handler, or None when unavailable."""
    return _init_trace_handler()


def _verify_braintrust(settings: Settings) -> bool:
    """Probe Braintrust once and say whether spans will be accepted."""
    if not settings.braintrust_api_key:
        return False
    if get_trace_handler() is None:
        return False

    try:
        from braintrust import login

        # Blocking, and the only call here that actually authenticates: the
        # handler above builds happily on a rejected key.
        login(api_key=settings.braintrust_api_key)
    except Exception as exc:
        logger.error(
            "Braintrust credentials rejected (%s). Traces will be dropped. "
            "Check BRAINTRUST_API_KEY — a Braintrust key begins with `sk-`.",
            exc,
        )
        return False

    logger.info(
        "Braintrust tracing enabled, project %s", settings.braintrust_project
    )
    return True


def _verify_langfuse(settings: Settings) -> bool:
    """Probe Langfuse once and say whether spans will be accepted."""
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return False

    # Builds the singleton client with our host and keys. Without this the
    # module-level client is unconfigured and auth_check reports "not
    # initialized", which would look like a credential problem it isn't.
    if get_trace_handler() is None:
        return False

    try:
        from langfuse import get_client

        if get_client().auth_check():
            logger.info("Langfuse tracing enabled, host %s", settings.langfuse_host)
            return True
    except Exception as exc:
        logger.error(
            "Langfuse credentials rejected by %s (%s). Traces will be dropped. "
            "Check that the host region matches the keys — a US key sent to the "
            "EU host answers 401.",
            settings.langfuse_host,
            exc,
        )
        return False

    logger.error(
        "Langfuse credentials rejected by %s. Traces will be dropped. Check that "
        "the host region matches the keys — a US key sent to the EU host answers 401.",
        settings.langfuse_host,
    )
    return False


def verify_tracing_credentials() -> bool:
    """Probe the configured backend once at startup and report the result loudly.

    Constructing a client authenticates nothing with either provider: spans are
    exported later by a background thread, so bad credentials surface as an
    HTTP 401 nobody in this process is watching for. Tracing then looks enabled
    while recording nothing. A single blocking check at startup turns that into
    one clear log line.

    Both probes block, which is why this is called from the application
    lifespan and nowhere else.

    Returns:
        True when tracing is verified working. False when it is disabled, the
        package is missing, or the credentials were rejected — never raises,
        because tracing is optional and must not block startup.
    """
    settings = get_settings()

    if settings.observability_provider == "none":
        return False
    if settings.observability_provider == "braintrust":
        return _verify_braintrust(settings)
    return _verify_langfuse(settings)


def build_runnable_config(**metadata: Any) -> dict[str, Any]:
    """Build the config to pass to ``graph.ainvoke()``.

    Attaches the configured provider's callback when available, plus
    per-request metadata (conversation_id, message_id, ...) used to filter
    traces in whichever UI is receiving them.

    Example:
        config = build_runnable_config(conversation_id=cid, message_id=mid)
        result = await graph.ainvoke(state, config=config)
    """
    config: dict[str, Any] = {}

    handler = get_trace_handler()
    if handler is not None:
        config["callbacks"] = [handler]

    if metadata:
        config["metadata"] = {k: v for k, v in metadata.items() if v is not None}

    return config
