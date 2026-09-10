"""LLM factory — selects a provider through the LLM_PROVIDER setting.

Switching providers only requires editing .env, never the code (ADR-10). This
matters because free tiers differ sharply in rate limits, so a provider that
throttles mid-demo has to be swapped quickly.
"""

from dataclasses import dataclass
from typing import Any, cast

from langchain_core.language_models.chat_models import BaseChatModel

from src.config import Settings, get_settings
from src.core.circuit_breaker import CircuitBreaker, llm_breaker

# Model used when LLM_MODEL is left empty.
#
# These go stale without warning. `llama-3.3-70b-versatile` sat here until Groq
# withdrew it, and because a failed LLM call is a fallback by design (NFR-02)
# the symptom was not an error but every message quietly arriving untranslated.
# Verified against each provider's own model listing on 28/08; when a whole run
# reports `is_fallback`, check these names before reading any other code.
DEFAULT_MODELS: dict[str, str] = {
    "groq": "openai/gpt-oss-120b",
    "deepseek": "deepseek-chat",
    "gemini": "gemini-2.5-flash",
    "openai": "gpt-4o-mini",
    "mistral": "mistral-small-latest",
}

# Settings field holding each provider's API key. pydantic-settings maps the
# field name to the environment variable by upper-casing it, so this one table
# also gives us the variable name to quote in error messages.
PROVIDER_KEY_FIELD: dict[str, str] = {
    "groq": "groq_api_key",
    "deepseek": "deepseek_api_key",
    "gemini": "google_api_key",
    "openai": "openai_api_key",
    "mistral": "mistral_api_key",
}

# DeepSeek is OpenAI-compatible, so it reuses the OpenAI client
DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class LLMConfigError(RuntimeError):
    """Raised when the provider is unknown or its API key is missing."""


# Chat models already built, keyed on the configuration that shapes them. The API
# key is deliberately not part of the key: it would put the secret in a
# module-level structure that any traceback dumping locals would render.
_CLIENTS: dict[tuple[str, str, float, int, int], BaseChatModel] = {}


class _CircuitProtectedChatModel:
    """Transparent model proxy that guards the provider's actual async call."""

    def __init__(self, model: BaseChatModel, breaker: CircuitBreaker) -> None:
        self._model = model
        self._breaker = breaker

    async def ainvoke(self, *args: Any, **kwargs: Any) -> Any:
        return await self._breaker.call(lambda: self._model.ainvoke(*args, **kwargs))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._model, name)


def extract_text(response: object) -> str:
    """Return the plain text of a chat-model response.

    Lives here rather than in a node so every caller of :func:`get_llm` — the
    translation nodes and the evaluation judge alike — shares one implementation.

    ``BaseMessage.text`` already flattens plain-string content and content-block
    lists. The ``content`` fallbacks cover response objects that only define that
    attribute: lightweight test doubles, and providers returning raw blocks.
    """
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text.strip()

    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        # Content-block form: [{"type": "text", "text": "..."}, ...]
        parts = [block["text"] for block in content if isinstance(block, dict) and isinstance(block.get("text"), str)]
        if parts:
            return "".join(parts).strip()

    return str(content).strip()


@dataclass(frozen=True, slots=True)
class LlmCallInfo:
    """What the provider reported about a call it just served.

    Every field defaults to empty. Callers get a fully-formed object whether the
    provider reported anything or not, so no reader has to test for None.
    """

    model_served: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = ""
    request_id: str = ""


def extract_call_info(response: object) -> LlmCallInfo:
    """Read the accounting metadata off a chat-model response.

    Separate from :func:`extract_text` rather than folded into it. That function
    is called by the nodes and by the evaluation judge, and returns a plain
    string on which both already depend; measurement must not change its shape.

    ``model_served`` is the model the provider says it used, which is not always
    the model that was requested — aliases resolve to a dated version, and
    providers reroute under load. Reading it from the client instead, as the
    translate node used to, records an intention rather than a fact.

    ``finish_reason`` earns its place: a value of ``"length"`` explains a whole
    class of fallback that currently looks like a model producing rambling
    output, when the output was really cut off mid-sentence by ``LLM_MAX_TOKENS``.

    Returns:
        The metadata found. An empty ``LlmCallInfo`` when the response carries
        none — including for the lightweight doubles used across the test suite,
        which define only ``content``. Never raises: telemetry must not be able
        to break a translation (NFR-02).
    """
    try:
        usage = getattr(response, "usage_metadata", None) or {}
        metadata = getattr(response, "response_metadata", None) or {}

        return LlmCallInfo(
            model_served=str(metadata.get("model_name") or metadata.get("model") or ""),
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            finish_reason=str(metadata.get("finish_reason") or ""),
            request_id=str(getattr(response, "id", "") or ""),
        )
    except Exception:  # noqa: BLE001 - measurement must never break a translation
        return LlmCallInfo()


def _build_llm(
    provider: str,
    model: str,
    temperature: float,
    timeout: int,
    max_tokens: int,
    api_key: str,
) -> BaseChatModel:
    """Construct a chat model, reusing the one already built for this config.

    Constructing a provider client costs roughly 0.9s — mostly building the HTTP
    client and its TLS context — which would otherwise be paid on every
    translated message. Reuse also keeps the underlying HTTP connection pool
    alive instead of renegotiating TLS per request.

    Note that the cached client outlives the event loop it was created on, so a
    process that runs several ``asyncio.run()`` calls must not share it.

    Args:
        provider: One of the keys in DEFAULT_MODELS.
        model: Model identifier to request from that provider.
        temperature: Sampling temperature.
        timeout: Per-request timeout in seconds.
        max_tokens: Upper bound on generated tokens.
        api_key: Credential for the provider. Deliberately excluded from the
            cache key so the secret never lands in a module-level structure.

    Returns:
        A chat model, reused across calls with the same configuration.
    """
    cache_key = (provider, model, temperature, timeout, max_tokens)
    cached = _CLIENTS.get(cache_key)
    if cached is not None:
        return cached

    common = {"model": model, "temperature": temperature, "timeout": timeout}

    if provider == "groq":
        from langchain_groq import ChatGroq

        client: BaseChatModel = ChatGroq(api_key=api_key, max_tokens=max_tokens, **common)
    elif provider == "deepseek":
        from langchain_openai import ChatOpenAI

        client = ChatOpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL, max_tokens=max_tokens, **common)
    elif provider == "mistral":
        from langchain_mistralai import ChatMistralAI

        # ChatMistralAI takes the timeout as `timeout` like the others but names
        # the cap `max_tokens`, so only the key differs from the OpenAI branch.
        client = ChatMistralAI(api_key=api_key, max_tokens=max_tokens, **common)
    elif provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        # ChatGoogleGenerativeAI names this max_output_tokens, not max_tokens —
        # it cannot go in `common` with the others.
        client = ChatGoogleGenerativeAI(google_api_key=api_key, max_output_tokens=max_tokens, **common)
    else:  # provider == "openai"
        from langchain_openai import ChatOpenAI

        client = ChatOpenAI(api_key=api_key, max_tokens=max_tokens, **common)

    _CLIENTS[cache_key] = client
    return client


def get_llm(
    settings: Settings | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> BaseChatModel:
    """Return the chat model for the configured provider.

    Args:
        settings: configuration to read. Defaults to the process settings.
        provider: overrides ``settings.llm_provider``. Used by the evaluation
            script to score with a different model than the one being tested.
        model: overrides the model that provider would otherwise use. Needed
            because ``LLM_MODEL`` belongs to the configured provider and cannot
            name a model on a different one — the judge, which is exactly that
            case, would otherwise be stuck on its provider's default.

    Raises:
        LLMConfigError: unknown provider, or its API key is missing.
    """
    settings = settings or get_settings()
    provider = provider or settings.llm_provider

    if provider not in DEFAULT_MODELS:
        raise LLMConfigError(f"Unknown LLM_PROVIDER: {provider}. Expected one of: {', '.join(sorted(DEFAULT_MODELS))}")

    # Validate the key *before* importing the provider package, so a missing key
    # reports a clear error instead of an ImportError when the package is absent.
    key_field = PROVIDER_KEY_FIELD[provider]
    api_key = getattr(settings, key_field, "")
    if not api_key:
        raise LLMConfigError(
            f"LLM_PROVIDER={provider} but {key_field.upper()} is not set. "
            f"Add {key_field.upper()} to .env or switch LLM_PROVIDER to another provider."
        )

    # An explicit provider override ignores LLM_MODEL, which belongs to the
    # provider configured in settings — unless the caller names a model itself.
    chosen_model = model or DEFAULT_MODELS[provider]
    if not model and provider == settings.llm_provider and settings.llm_model:
        chosen_model = settings.llm_model

    model_client = _build_llm(
        provider,
        chosen_model,
        settings.llm_temperature,
        settings.llm_timeout_seconds,
        settings.llm_max_tokens,
        api_key,
    )
    return cast(BaseChatModel, _CircuitProtectedChatModel(model_client, llm_breaker))


def get_assistant_llm(settings: Settings | None = None) -> BaseChatModel:
    """The chat model the Assistant Agent generates with (ADR-39).

    A thin wrapper over `get_llm`, and worth having: resolving the pair and
    unpacking it at every call site is how one of them ends up passing only the
    provider, which then silently runs that provider's default model while the
    evaluation report claims otherwise.

    Raises:
        LLMConfigError: unknown provider, or its API key is missing.
    """
    settings = settings or get_settings()
    provider, model = settings.resolve_assistant_llm()
    # Its own token ceiling, not the translator's. See the comment on
    # `assistant_llm_max_tokens`.
    return get_llm(
        settings=settings.model_copy(update={"llm_max_tokens": settings.assistant_llm_max_tokens}),
        provider=provider,
        model=model,
    )


def get_assistant_judge_llm(settings: Settings | None = None) -> BaseChatModel:
    """The model that scores the Assistant Agent's output in evaluation runs.

    Never used on a request path — a judge is an evaluation instrument, and one
    that ran in production would double the cost of every reply to produce a
    number nobody reads.

    Raises:
        LLMConfigError: unknown provider, or its API key is missing.
    """
    settings = settings or get_settings()
    provider, model = settings.resolve_assistant_judge()
    return get_llm(settings=settings, provider=provider, model=model)
