"""Secondary translation provider, used when the LLM path fails (ADR-07).

Sits between the LLM and the last-resort behaviour of handing the recipient the
untranslated message: a degraded translation still lets the conversation
continue, which a raw passthrough does not.

Two properties this module must keep:

* **It never raises.** Every failure — package missing, network down, provider
  rate-limited, unsupported language pair — returns ``None``, and the caller
  keeps the original text it already holds (NFR-02).
* **It never blocks the event loop.** ``deep-translator`` is synchronous, so the
  call runs in a worker thread under a timeout. The timeout releases the
  coroutine but cannot kill the thread, which is acceptable because the thread
  only holds one HTTP request.

The provider is Google Translate's public web endpoint, reached through
``deep-translator``. It needs no API key, which is why it suits the MVP, but it
is an unofficial endpoint: treat an outage as expected, not exceptional.
"""

from __future__ import annotations

import asyncio
import logging

from src.config import get_settings
from src.core.circuit_breaker import fallback_translator_breaker

logger = logging.getLogger(__name__)

# Written to AgentState["model"] so a translation produced here is
# distinguishable from an LLM one in translation_results and in Langfuse.
FALLBACK_MODEL_NAME = "deep-translator:google"

# deep-translator's code for "detect the source language yourself"
AUTO_SOURCE = "auto"


def _translate_sync(text: str, source_language: str, target_language: str) -> str | None:
    """Blocking call into deep-translator. Runs in a worker thread.

    Args:
        text: Message to translate.
        source_language: ISO 639-1 code, or empty to let the provider detect it.
        target_language: ISO 639-1 code of the recipient's language.

    Returns:
        The provider's translation, or None when it produced no result.
    """
    from deep_translator import GoogleTranslator

    translator = GoogleTranslator(
        source=source_language or AUTO_SOURCE,
        target=target_language,
    )
    return translator.translate(text)


async def translate_with_secondary_provider(
    text: str,
    target_language: str,
    source_language: str = "",
) -> str | None:
    """Translate with the secondary provider, or return None if it cannot.

    Args:
        text: the original message.
        target_language: ISO 639-1 code of the recipient's language.
        source_language: ISO 639-1 code of the sender's language. Empty falls
            back to the provider's own detection, which is the right choice when
            the agent's own detection is what failed.

    Returns:
        The translated text, or None when the provider is disabled,
        unreachable, or returned nothing usable.
    """
    settings = get_settings()

    if not settings.fallback_translator_enabled:
        return None
    if not text or not target_language:
        return None
    if source_language and source_language == target_language:
        return None

    if not fallback_translator_breaker.allow_request():
        logger.info("Fallback translator circuit breaker is OPEN, skipping")
        return None

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_translate_sync, text, source_language, target_language),
            timeout=settings.fallback_translator_timeout_seconds,
        )
    except TimeoutError:
        logger.warning(
            "Fallback translator timed out after %ss",
            settings.fallback_translator_timeout_seconds,
        )
        fallback_translator_breaker.record_failure()
        return None
    except Exception as exc:
        logger.warning("Fallback translator failed: %s: %s", type(exc).__name__, exc)
        fallback_translator_breaker.record_failure()
        return None

    if not isinstance(result, str) or not result.strip():
        # The value itself is a translation of a user's message, so only its
        # shape is logged: server logs are read by people the conversation
        # never included.
        logger.warning(
            "Fallback translator returned no usable text: %s of length %d",
            type(result).__name__,
            len(result) if isinstance(result, str) else 0,
        )
        fallback_translator_breaker.record_failure()
        return None

    fallback_translator_breaker.record_success()
    return result.strip()
