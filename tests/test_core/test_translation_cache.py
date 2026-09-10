"""TTL, capacity and metric contracts for the in-memory translation cache."""

import pytest

from src.services.language import translation


@pytest.fixture(autouse=True)
def clean_cache():
    translation.reset_translation_cache()
    yield
    translation.reset_translation_cache()


def test_cacheability_boundary_is_sixty_characters_and_six_words():
    assert translation._is_cacheable("a" * 60)
    assert not translation._is_cacheable("a" * 61)
    assert translation._is_cacheable("one two three four five six")
    assert not translation._is_cacheable("one two three four five six seven")


def test_cache_hit_miss_and_ttl_expiry_are_measured(monkeypatch):
    clock = 100.0
    monkeypatch.setattr(translation.time, "monotonic", lambda: clock)
    key = translation._cache_key("conversation", "hello", "en", "vi", "peer")
    translation._translation_cache[key] = ("xin chào", clock)

    assert translation._read_translation_cache(key) == "xin chào"
    assert translation.translation_cache_metrics() == {
        "size": 1,
        "max_size": 500,
        "ttl_seconds": 1800,
        "hits": 1,
        "misses": 0,
        "hit_rate": 1.0,
    }

    clock += 1801
    assert translation._read_translation_cache(key) is None
    metrics = translation.translation_cache_metrics()
    assert metrics["size"] == 0
    assert metrics["hits"] == 1
    assert metrics["misses"] == 1
    assert metrics["hit_rate"] == 0.5


def test_missing_cache_value_counts_as_a_miss():
    key = translation._cache_key("conversation", "hello", "en", "vi", "peer")

    assert translation._read_translation_cache(key) is None
    assert translation.translation_cache_metrics()["misses"] == 1


def test_cache_capacity_evicts_old_half_before_inserting(monkeypatch):
    monkeypatch.setattr(translation.time, "monotonic", lambda: 10.0)
    for index in range(translation._CACHE_MAX_SIZE):
        translation._translation_cache[(str(index), "x", "en", "vi", "peer", "natural")] = (
            "y",
            10.0,
        )

    translation._cache_primary_translation(
        snapshot={"conversation_id": "new", "original_text": "hello"},
        source_language="en",
        target_language="vi",
        honorific_profile="peer",
        translation_tone="natural",
        translated_text="xin chào",
    )

    assert len(translation._translation_cache) == 251
