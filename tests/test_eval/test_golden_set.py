"""Structural checks on the golden set.

Cheap guards against the ways a hand-maintained JSONL file goes wrong: a
duplicated id silently overwrites a scenario, a missing field crashes a run
forty samples in, and a language the system does not support is scored against
a translation the agent was never asked to produce.

Fixtures live in this file rather than tests/conftest.py, which is shared
across all feature areas.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.schemas.auth import SUPPORTED_LANGUAGES

GOLDEN_SET = Path(__file__).resolve().parents[2] / "eval" / "golden_set.jsonl"

# The languages the golden set was built to cover. A subset of
# SUPPORTED_LANGUAGES: the product accepts thirteen codes, and the evaluation
# makes no claim about the five it never exercises.
EVALUATED_LANGUAGES = {"vi", "en", "zh", "fr", "es", "ja", "de", "th"}

REQUIRED_KEYS = {
    "id",
    "category",
    "chat_type",
    "context_level",
    "source_language",
    "target_language",
    "context_messages",
    "original_text",
    "expected_translation",
    "note",
}

# Fields only the samples that test them carry. Optional rather than required
# because absent is a real state, not a gap: it is what every conversation looks
# like before anything has been inferred about it, so a sample without them
# exercises exactly the prompt the sample always exercised. Making them required
# would mean editing fifty-three rows to write "" in three places.
OPTIONAL_KEYS = {
    "domain",
    "audience",
    "honorific_profile",
    "sender_honorific_profile",
}

# The four standings the schema allows. Repeated here rather than imported so a
# value silently added to the database does not silently become valid data.
HONORIFIC_PROFILES = {"senior", "peer", "junior", "client"}


@pytest.fixture(scope="module")
def samples() -> list[dict]:
    """Every row of the golden set, parsed."""
    with GOLDEN_SET.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def test_every_row_has_the_same_keys(samples):
    """A missing field would fail a run partway through, after paying for it."""
    for sample in samples:
        missing = REQUIRED_KEYS - set(sample)
        assert not missing, f"{sample.get('id')} is missing {sorted(missing)}"
        unknown = set(sample) - REQUIRED_KEYS - OPTIONAL_KEYS
        assert not unknown, f"{sample.get('id')} carries unknown {sorted(unknown)}"


def test_ids_are_unique(samples):
    """Ids identify samples across runs, so a duplicate hides one of them."""
    ids = [sample["id"] for sample in samples]

    assert len(ids) == len(set(ids))


def test_languages_are_ones_the_system_supports(samples):
    """Scoring a pair the product cannot offer measures nothing useful."""
    for sample in samples:
        assert sample["source_language"] in SUPPORTED_LANGUAGES, sample["id"]
        assert sample["target_language"] in SUPPORTED_LANGUAGES, sample["id"]


def test_classification_fields_use_known_values(samples):
    """The report groups by these, and a typo would create a bucket of one."""
    for sample in samples:
        assert sample["chat_type"] in {"direct", "group"}, sample["id"]
        assert sample["context_level"] in {"rich", "poor"}, sample["id"]


def test_both_chat_types_and_both_context_levels_are_represented(samples):
    """The report breaks results down by these, so an empty bucket is a hole.

    Group threads and bilingual threads exercise different behaviour, as do
    rich and poor context; a set that lost one of them would still produce a
    report, just one that quietly stopped measuring something.
    """
    assert {s["chat_type"] for s in samples} == {"direct", "group"}
    assert {s["context_level"] for s in samples} == {"rich", "poor"}


def test_the_set_covers_the_languages_it_was_built_for(samples):
    """Coverage is the point of the set: a language absent here is untested.

    These eight are the scope the set was written to, not the full thirteen
    `SUPPORTED_LANGUAGES` allows — the product accepts more codes than the
    evaluation makes any claim about, and this test records which.
    """
    covered = {s["source_language"] for s in samples} | {
        s["target_language"] for s in samples
    }

    assert EVALUATED_LANGUAGES <= covered


def test_a_declared_standing_is_one_the_schema_allows(samples):
    """A sample naming a fifth standing would exercise a prompt branch that
    cannot exist in production, and score it as if it could."""
    for sample in samples:
        standing = sample.get("honorific_profile")
        if standing:
            assert standing in HONORIFIC_PROFILES, sample["id"]


def test_the_audience_samples_differ_only_in_who_is_reading(samples):
    """The pair is the measurement. If the two rows drifted apart in wording,
    a difference in score would no longer be evidence that audience mattered."""
    pair = [s for s in samples if s["category"] == "glossary_audience"]

    assert len(pair) == 2
    assert pair[0]["original_text"] == pair[1]["original_text"]
    assert {s["audience"] for s in pair} == {"internal", "client"}
    assert pair[0]["expected_translation"] != pair[1]["expected_translation"]


def test_the_honorific_samples_cover_more_than_one_language(samples):
    """Vietnamese marks standing with pronouns, Japanese with keigo and often no
    pronoun at all. A rule that only works for one of them is a pronoun table
    wearing a relationship's clothes (ADR-23)."""
    rows = [s for s in samples if s["category"] == "honorific_recipient"]

    assert len(rows) >= 3
    assert len({s["target_language"] for s in rows}) >= 2
    assert len({s["honorific_profile"] for s in rows}) >= 2


def _borrowed_from(history_line: str, translation: str) -> str:
    """What the translation appears to have taken from this history line.

    Two rules rather than one, because the check has to work for both writing
    systems in this set. Where the line has spaces, whole words of four
    characters or more are compared: Vietnamese shares so many short letter
    runs between unrelated words that a character test flags `anh nhận` against
    `rảnh nhé`. Where it has none, as in Japanese, there are no word boundaries
    to split on, so a run of three characters is the signal instead.

    Returns the borrowed fragment, or "" when there is nothing shared.
    """
    if " " in history_line.strip():
        for word in history_line.split():
            cleaned = word.strip(":,.?!").lower()
            if len(cleaned) >= 4 and cleaned in translation.lower():
                return cleaned
        return ""

    for size in range(len(history_line), 2, -1):
        for start in range(len(history_line) - size + 1):
            run = history_line[start : start + size]
            if run in translation:
                return run
    return ""


def test_the_context_bleed_samples_put_the_bait_in_the_history_only(samples):
    """The category only measures anything if the thing that must not appear
    appears in the context and nowhere in the message being translated."""
    rows = [s for s in samples if s["category"] == "context_bleed"]

    assert len(rows) >= 2
    assert len({s["target_language"] for s in rows}) >= 2
    for sample in rows:
        assert sample["context_messages"], sample["id"]
        assert sample["context_level"] == "rich", sample["id"]
        # Nothing the history says may legitimately belong in the answer, or a
        # leak would be indistinguishable from a correct translation. Measured
        # as a shared run of characters rather than shared words, because
        # Japanese writes no spaces for a word check to split on.
        for line in sample["context_messages"]:
            borrowed = _borrowed_from(line, sample["expected_translation"])
            assert not borrowed, f"{sample['id']} shares {borrowed!r} with its history"


def test_one_honorific_sample_offers_a_relationship_that_is_not_the_readers(samples):
    """A pair of address forms between two other speakers is the trap: borrowing
    it renders the reader into a relationship they are not part of (ADR-23)."""
    bystander = [
        s
        for s in samples
        if s["category"] == "honorific_recipient"
        and s.get("honorific_profile") == "peer"
        and s["context_messages"]
    ]

    assert bystander, "no sample offers a bystander relationship to borrow"
    for sample in bystander:
        history = " ".join(sample["context_messages"])
        assert "anh" in history and "em" in history, sample["id"]
        assert "anh" not in sample["expected_translation"].split(), sample["id"]
