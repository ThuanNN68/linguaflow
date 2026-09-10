"""The Assistant Agent resolves its own models, never the translation agent's.

These are pure `Settings` tests: no database, no provider package, no network.
The rule they hold in place is small and easy to break by accident — "empty
means inherit, but a model never crosses providers" — and breaking it is silent,
because the wrong model answers perfectly well and only the report is wrong.
"""

from __future__ import annotations

import pytest

from src.config import Settings
from src.services.shared.embeddings import (
    assistant_embedding_settings,
    embedding_model_name,
    with_embedding,
)


def _settings(**overrides) -> Settings:
    """A Settings built from these values alone, with no `.env` behind it.

    `_env_file=None` is load-bearing rather than tidiness. `Settings` reads the
    project's `.env`, which is gitignored and different on every machine — so
    without this the assertions below depend on whether the developer running
    them happens to have `ASSISTANT_JUDGE_PROVIDER` set. They passed for a week
    and then failed the moment real values were put in that file, which is the
    worst way for a test to be wrong: it reports the developer's configuration,
    not the code.
    """
    base = {
        "jwt_secret": "x" * 40,
        "llm_provider": "gemini",
        "llm_model": "gemini-3.7-flash",
        "embedding_provider": "gemini",
        "embedding_model": "models/gemini-embedding-001",
    }
    return Settings(_env_file=None, **{**base, **overrides})


def test_assistant_llm_inherits_translation_model_when_unconfigured():
    assert _settings().resolve_assistant_llm() == ("gemini", "gemini-3.7-flash")


def test_assistant_llm_drops_inherited_model_when_provider_differs():
    """LLM_MODEL names a model on the translation provider and cannot travel.

    Carrying `gemini-3.7-flash` over to Mistral would fail at the first call, so
    an assistant provider given without a model must fall through to that
    provider's own default — reported here as the empty string `get_llm` reads
    as "use your default".
    """
    provider, model = _settings(assistant_llm_provider="mistral").resolve_assistant_llm()
    assert (provider, model) == ("mistral", "")


def test_assistant_llm_keeps_explicit_model_across_providers():
    resolved = _settings(
        assistant_llm_provider="mistral",
        assistant_llm_model="mistral-large-latest",
    ).resolve_assistant_llm()
    assert resolved == ("mistral", "mistral-large-latest")


def test_assistant_judge_falls_back_to_the_generating_model():
    """Not to the translation judge, which grades a different kind of output."""
    settings = _settings(llm_judge_provider="mistral", judge_model="mistral-medium-latest")
    assert settings.resolve_assistant_judge() == settings.resolve_assistant_llm()


def test_assistant_judge_overrides_without_touching_the_translation_judge():
    settings = _settings(
        llm_judge_provider="mistral",
        judge_model="mistral-medium-latest",
        assistant_judge_provider="openai",
        assistant_judge_model="gpt-4o-mini",
    )
    assert settings.resolve_assistant_judge() == ("openai", "gpt-4o-mini")
    assert (settings.llm_judge_provider, settings.judge_model) == (
        "mistral",
        "mistral-medium-latest",
    )


def test_settings_warns_when_the_assistant_judge_is_its_own_generator(caplog):
    with caplog.at_level("WARNING"):
        _settings()
    assert "self-judged" in caplog.text


def test_settings_stays_quiet_when_the_assistant_judge_is_a_different_model(caplog):
    with caplog.at_level("WARNING"):
        _settings(assistant_judge_provider="mistral")
    assert "self-judged" not in caplog.text


def test_assistant_embedding_settings_leave_the_translation_side_unchanged():
    """The two agents' vectors live in different tables and must not converge.

    A copy rather than a mutation, because `get_settings` is an `lru_cache`
    singleton the whole process reads: mutating it to embed one assistant chunk
    would change what every concurrent translation embeds with.
    """
    base = _settings(assistant_embedding_provider="local")
    assistant = assistant_embedding_settings(base)

    assert assistant.embedding_provider == "local"
    assert embedding_model_name(assistant) == "sentence-transformers/LaBSE"
    assert base.embedding_provider == "gemini"
    assert embedding_model_name(base) == "models/gemini-embedding-001"


def test_assistant_embedding_settings_return_the_same_object_when_inheriting():
    """No copy when nothing changes, so the cached client is reused as-is."""
    base = _settings()
    assert assistant_embedding_settings(base) is base


def test_with_embedding_names_the_model_it_was_given():
    swapped = with_embedding(_settings(), "local", "intfloat/multilingual-e5-base")
    assert embedding_model_name(swapped) == "intfloat/multilingual-e5-base"


@pytest.mark.parametrize("field", ["assistant_retrieval_top_k", "assistant_rerank_top_n"])
def test_assistant_retrieval_sizes_reject_zero(field):
    """Zero would return no context at all and read as "retrieval is off"."""
    with pytest.raises(ValueError):
        _settings(**{field: 0})
