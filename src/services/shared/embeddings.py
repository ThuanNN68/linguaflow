"""Embedding factory — selects a provider through the EMBEDDING_PROVIDER setting.

The same arrangement `llm.py` uses, for the same reason: free tiers differ, and
a provider that throttles mid-demo has to be swapped from `.env` rather than
from the code (ADR-25).

pgvector stores the vectors and measures the distance between them; it does not
produce them. The requirement that decides the choice here is multilingual
reach — unless "staging env" and "môi trường stg" land near each other, grouping
corrections by meaning achieves nothing.

Every provider must return vectors of `EMBEDDING_DIM` dimensions, because that
is the width of the columns. Changing provider to one with a different width is
a migration plus a pass to re-embed everything, never a configuration change,
and `embed` refuses rather than writing a vector the column cannot hold.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from src.config import Settings, get_settings
from src.core.circuit_breaker import CircuitBreaker, embedding_breaker, embedding_fallback_breaker
from src.database.models import EMBEDDING_DIM

logger = logging.getLogger(__name__)

# Set by `preload_local_models()`, and false until it has run. Everything that
# would reach for a local model consults it first, so a process that never
# preloaded degrades to the remote providers instead of importing torch under a
# running event loop.
_LOCAL_MODELS_READY = False


class LocalModelsUnavailableError(RuntimeError):
    """Raised instead of importing torch on a thread that must not."""


def preload_local_models() -> bool:
    """Import `sentence-transformers` while there is no event loop running.

    Both local-model paths in this project — the assistant's cross-encoder and
    this module's `local` embedding fallback — used to import it lazily on first
    use, from inside a request. On Windows that killed the process outright: the
    import pulls torch, whose extension modules abort with an access violation
    when first loaded under a running asyncio loop. An access violation is not a
    Python exception, so the `try`/`except` guarding those imports never saw it;
    the server simply vanished mid-request, taking every open WebSocket with it.

    Call this at import time from the application entry point, before uvicorn
    starts its loop. Returns whether the package is actually there: a deployment
    that has not installed it (ADR-18) gets False and skips both features, which
    is the behaviour it already had.

    Never inside a test run, for the reason the fallback below already gives: by
    the time `conftest.py` imports the application the session has loaded plenty
    else, which is the ordering that makes the torch import abort. Tests that
    mean to exercise a local model construct it explicitly.
    """
    global _LOCAL_MODELS_READY
    if "pytest" in sys.modules:
        _LOCAL_MODELS_READY = False
        return False
    try:
        import sentence_transformers  # noqa: F401

        _LOCAL_MODELS_READY = True
    except Exception:
        logger.info(
            "sentence-transformers is not installed; assistant reranking and "
            "the local embedding fallback are unavailable and will be skipped."
        )
        _LOCAL_MODELS_READY = False
    return _LOCAL_MODELS_READY


def local_models_ready() -> bool:
    """Whether a local model may be constructed on this process."""
    return _LOCAL_MODELS_READY


# Model used when EMBEDDING_MODEL is left empty. All three are multilingual;
# the widths differ, which is exactly why the width is pinned in the schema and
# checked below rather than trusted.
DEFAULT_MODELS: dict[str, str] = {
    # `models/text-embedding-004` was withdrawn and now answers 404. Its
    # replacement returns 3072 dimensions by default, which is why the width is
    # requested explicitly below rather than accepted.
    "gemini": "models/gemini-embedding-001",
    "openai": "text-embedding-3-small",
    # LaBSE, because it is the only local model measured to do semantic work:
    # 4 of 8 term variants at zero false positives, against 1 for e5 and 3 for
    # MiniLM — and e5's single hit means it is a surface matcher, not a semantic
    # one. MiniLM-L12-v2 sat here first and returns 384 dimensions, which the
    # `vector(768)` columns reject outright, so `local` had never once run.
    "local": "sentence-transformers/LaBSE",
}

# Which settings field carries each provider's key, and so — since
# pydantic-settings upper-cases field names — which variable to name in the
# error. `local` needs none: that is the point of it.
PROVIDER_KEY_FIELD: dict[str, str] = {
    "gemini": "google_api_key",
    "openai": "openai_api_key",
}


class EmbeddingConfigError(RuntimeError):
    """Raised when the provider is unknown, or its API key is missing."""


# Clients already built, keyed on the configuration that shapes them. The API
# key is deliberately not part of the key, exactly as in `llm.py`: it would put
# the secret into a module-level structure that any traceback dumping locals
# would render.
_CLIENTS: dict[tuple[str, str], Any] = {}


def _build_client(provider: str, model: str, api_key: str) -> Any:
    """Import and construct one provider's client.

    Imported inside the function so a provider nobody uses does not have to be
    installed — `sentence-transformers` in particular pulls in torch, which is
    several hundred megabytes and has no place in an image that will never call
    it (ADR-18).

    The key is passed in rather than left to the provider package to find. Both
    packages fall back to reading the environment, and this project keeps its
    keys in `.env`, which pydantic-settings reads *without* exporting — so the
    client would raise for a missing key that `get_embedder` had just confirmed
    was present. `embed` swallows every failure by design, so the visible effect
    was not an error but silence: both retrieval flags could be switched on and
    do nothing at all.
    """
    # The width is asked for, not accepted. `message_embeddings.embedding` and
    # `glossary_entries.embedding` are `vector(768)` columns, and both current
    # hosted models return something wider unless told otherwise — a vector of
    # the wrong width is rejected by the column, so this is the difference
    # between retrieval working and every insert failing.
    if provider == "gemini":
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        return GoogleGenerativeAIEmbeddings(model=model, google_api_key=api_key, output_dimensionality=EMBEDDING_DIM)
    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(model=model, api_key=api_key, dimensions=EMBEDDING_DIM)

    # `HuggingFaceEmbeddings` imports sentence-transformers, and therefore torch,
    # which aborts the process outright when it is first imported from inside a
    # running event loop. `preload_local_models()` does that import at startup;
    # if it has not run, this provider is unavailable rather than fatal — a
    # missing local fallback costs semantic glossary matching, while the crash
    # cost every open WebSocket on the server.
    if not local_models_ready():
        raise LocalModelsUnavailableError(
            "The local embedding provider needs sentence-transformers imported "
            "before the event loop starts; see preload_local_models()."
        )

    from langchain_huggingface import HuggingFaceEmbeddings

    return HuggingFaceEmbeddings(model_name=model)


def with_embedding(settings: Settings, provider: str, model: str = "") -> Settings:
    """A copy of ``settings`` that embeds with a different model.

    Overriding the *settings* rather than threading `provider` and `model`
    arguments through `embed`, `embed_with_model`, `embedding_model_name` and
    `get_embedder` keeps one rule in one place: whichever model a Settings names
    is the model every function reads. The quota fallback below already works
    this way, and a second mechanism beside it would be one more thing to keep
    in agreement.

    It also replaces `object.__setattr__` on the cached singleton, which the
    sweep script resorted to: that mutates the configuration every *other* caller
    in the process is reading, so a measurement could change the behaviour of the
    thing it was measuring.

    Args:
        settings: The configuration to base the copy on.
        provider: Provider to embed with. Empty keeps the current one.
        model: Model on that provider. Empty means the provider's default.
    """
    if not provider or provider == settings.embedding_provider:
        if not model or model == settings.embedding_model:
            return settings
    return settings.model_copy(
        update={
            "embedding_provider": provider or settings.embedding_provider,
            "embedding_model": model,
        }
    )


def assistant_embedding_settings(settings: Settings | None = None) -> Settings:
    """Configuration that embeds with the Assistant Agent's own model (ADR-39).

    The assistant's retrieval accuracy requirement is far above what the
    translation path needed, so it may run a different — usually larger and
    slower — embedding model. Its vectors live in `assistant_chunks`, never in
    `message_embeddings`, so the two never have to share a space.

    Every caller that embeds for the assistant must go through this, including
    the query side: a chunk stored by one model and a query embedded by another
    produce a ranking that looks fine and is meaningless.
    """
    settings = settings or get_settings()
    provider, model = settings.resolve_assistant_embedding()
    return with_embedding(settings, provider, model)


def get_embedder(settings: Settings | None = None) -> Any:
    """Return the embedding client for the configured provider.

    Args:
        settings: configuration to read. Defaults to the process settings.

    Raises:
        EmbeddingConfigError: unknown provider, or its API key is missing.
    """
    settings = settings or get_settings()
    provider = settings.embedding_provider

    if provider not in DEFAULT_MODELS:
        raise EmbeddingConfigError(
            f"Unknown EMBEDDING_PROVIDER: {provider}. Expected one of: {', '.join(sorted(DEFAULT_MODELS))}"
        )

    # Checked before the provider package is imported, so a missing key reports
    # itself rather than surfacing as an ImportError about an absent package.
    key_field = PROVIDER_KEY_FIELD.get(provider)
    api_key = getattr(settings, key_field, "") if key_field else ""
    if key_field and not api_key:
        raise EmbeddingConfigError(
            f"EMBEDDING_PROVIDER={provider} but {key_field.upper()} is not set. "
            f"Add {key_field.upper()} to .env, or set EMBEDDING_PROVIDER=local "
            "to embed in-process without an API key."
        )

    model = settings.embedding_model or DEFAULT_MODELS[provider]
    cached = _CLIENTS.get((provider, model))
    if cached is None:
        cached = _build_client(provider, model, api_key)
        _CLIENTS[(provider, model)] = cached
    return cached


def embedding_model_name(settings: Settings | None = None) -> str:
    """Name the model an embedding came from, for the `embedding_model` column.

    Stored beside every vector so one produced by another model is recognisable
    instead of being compared in the wrong space — which returns a confident
    wrong answer rather than an error.
    """
    settings = settings or get_settings()
    return settings.embedding_model or DEFAULT_MODELS.get(settings.embedding_provider, "")


# What a term has to score to be injected without an exact string match, per
# model. A cosine threshold is a property of an embedding space, not of a task,
# so one number cannot serve several models — measured on this project's own
# terms, `gemini-embedding-001` separates true variants from unrelated words
# cleanly while `multilingual-e5-base` puts both at 0.83 +/- 0.01.
#
# Every number here is set for **precision over recall**, deliberately. A term
# below the line is simply absent from the glossary and the model translates it
# itself; a term above it is *forced* into the output by the prompt. Missing a
# real variant costs a slightly flatter translation. Matching a wrong one
# changes what the message says.
GLOSSARY_THRESHOLDS: dict[str, float] = {
    # Measured 22/08: true variants 0.586-0.756, unrelated words 0.477-0.574.
    # 0.65 sits clear of every unrelated pair and gives up the two weakest true
    # ones on purpose.
    "models/gemini-embedding-001": 0.65,
    # Measured 22/08 on the same pairs: unrelated words top out at 0.616 and
    # 6 of 8 true variants clear it, against 8 of 8 for -001. The newer model
    # retrieves passages better and separates terms slightly worse — two
    # different jobs, which is why the two are configured independently.
    "models/gemini-embedding-2": 0.65,
    # The local models, at the highest threshold that lets no wrong pair
    # through. An earlier note here claimed no threshold existed because the
    # worst true pair scores below the best false one — that only rules out a
    # threshold with full recall. Set the line above every false pair instead
    # and each model keeps the matches it is *sure* of, which is the trade this
    # project wants everywhere: a term below the line is absent from the
    # glossary and the model translates it itself.
    #
    # What they keep, out of 8 semantic variants and 6 typos:
    #   LaBSE   4 semantic, 1 typo  — the only local model doing semantic work
    #   e5      1 semantic, 6 typos — a surface matcher wearing an embedding
    #   MiniLM  3 semantic, 2 typos — and 384 dimensions, so it needs a migration
    "sentence-transformers/LaBSE": 0.675,
    "intfloat/multilingual-e5-base": 0.855,
    #
    # Mistral is absent for a different reason: `mistral-embed` returns 1024
    # dimensions and rejects `output_dimension`, `codestral-embed` returns 1536,
    # and the columns are `vector(768)`. Adding it is a migration plus a pass to
    # re-embed everything, and it would then exclude every model that currently
    # works — a schema decision, not a configuration one (ADR-25).
}

# A model nobody has measured gets a threshold that admits almost nothing, so
# switching provider under quota pressure degrades to exact matching rather than
# to random terms. Measure it and add a row above to turn it on properly.
UNMEASURED_GLOSSARY_THRESHOLD = 0.95


def glossary_threshold_for(model: str, settings: Settings | None = None) -> float:
    """The similarity a term must reach in this model's space.

    Args:
        model: The model that produced the query vector, resolved.
        settings: Configuration. A non-zero `glossary_similarity_threshold`
            overrides the table, for measuring a new model without editing code.
    """
    settings = settings or get_settings()
    if settings.glossary_similarity_threshold > 0:
        return settings.glossary_similarity_threshold
    return GLOSSARY_THRESHOLDS.get(model, UNMEASURED_GLOSSARY_THRESHOLD)


async def embed_with_model(text: str, *, settings: Settings | None = None) -> tuple[list[float] | None, str]:
    """Embed one string, and say which model actually produced the vector.

    The name is not decoration. Vectors from two models occupy different spaces;
    comparing them returns a number that means nothing, and storing one under
    the other's name poisons the table for good. When a hosted provider is out
    of quota this falls back to a local model, so the *configured* name and the
    *producing* name stop agreeing — every caller that stores or compares a
    vector needs the second one.

    The fallback is what keeps a quota failure from becoming a service failure,
    and it degrades in the safe direction on its own: a local query vector
    matches no Gemini-stored entry, so semantic glossary matching quietly
    returns nothing and exact matching carries the message. The reader sees a
    translation and never learns an embedding budget ran out.

    Returns:
        The vector and its model, or `(None, "")` when every provider failed.
    """
    settings = settings or get_settings()
    vector = await embed(text, settings=settings, breaker=embedding_breaker)
    if vector is not None:
        return vector, embedding_model_name(settings)

    fallback = settings.embedding_fallback_provider
    if not fallback or fallback == settings.embedding_provider:
        return None, ""

    # Never inside a test run. The fallback imports sentence-transformers, which
    # pulls torch into the process; under pytest that has crashed the suite
    # outright on Windows, and even where it does not, a test that silently
    # loads half a gigabyte is one nobody will run twice. Tests that mean to
    # exercise the fallback construct it explicitly.
    if "pytest" in sys.modules:
        return None, ""

    # Only a provider that *had* a key and still failed gets a fallback. Without
    # a key it never ran, and that is a misconfiguration rather than a quota
    # running out — falling back would paper over it, and would pull a
    # half-gigabyte local model into a process that was never meant to have one,
    # which is exactly what happened to the test suite when this rule was
    # missing.
    key_field = PROVIDER_KEY_FIELD.get(settings.embedding_provider)
    if key_field and not getattr(settings, key_field, ""):
        return None, ""

    logger.warning(
        "Embedding provider %r returned nothing; falling back to %s. Semantic "
        "glossary matching is inert until it recovers, because a vector from "
        "another model cannot be compared with the stored ones.",
        settings.embedding_provider,
        fallback,
    )
    local = settings.model_copy(
        update={
            "embedding_provider": fallback,
            "embedding_model": DEFAULT_MODELS[fallback],
        }
    )
    vector = await embed(text, settings=local, breaker=embedding_fallback_breaker)
    if vector is None:
        return None, ""
    return vector, embedding_model_name(local)


async def embed(
    text: str,
    *,
    settings: Settings | None = None,
    breaker: CircuitBreaker = embedding_breaker,
) -> list[float] | None:
    """Turn one piece of text into a vector, or return None.

    None on every failure, never an exception. Callers are a graph node that
    must not fail a translation and a background miner nobody is watching;
    neither has anything useful to do with an error, and both degrade to the
    behaviour they had before embeddings existed.

    Args:
        text: The text to embed. Blank text returns None without a call.
        settings: configuration to read. Defaults to the process settings.

    Returns:
        A vector of `EMBEDDING_DIM` floats, or None.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return None

    if not breaker.allow_request():
        logger.info("Embedding circuit breaker is open; skipping provider call")
        return None

    try:
        client = get_embedder(settings)
        vector = await client.aembed_query(cleaned)
    except Exception as exc:
        breaker.record_failure()
        logger.warning("Embedding %d characters failed: %s", len(cleaned), exc)
        return None

    if not vector:
        breaker.record_failure()
        return None
    if len(vector) != EMBEDDING_DIM:
        breaker.record_failure()
        # Refused rather than truncated or padded. The column has a fixed width,
        # and a vector reshaped to fit it would be silently meaningless: every
        # distance computed against it would be wrong and nothing would say so.
        logger.error(
            "Embedding provider returned %d dimensions, schema expects %d. "
            "Changing provider needs a migration and a re-embedding pass.",
            len(vector),
            EMBEDDING_DIM,
        )
        return None
    breaker.record_success()
    return list(vector)
