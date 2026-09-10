"""Tests for turning corrections into glossary proposals.

The thresholds are what these guard, and they are the part with a cost in both
directions. Too low and one person's preference becomes house style for
everybody. Too high and the queue stays empty and the feature looks broken. In
between, the decision is invisible: nothing logs "this term was nearly
proposed".

Nothing here touches a database or a model, which is the point of keeping the
clustering pure.

Fixtures live in this file rather than tests/conftest.py, which is shared
across all feature areas.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.services.language.correction_log import build_snippet, extract_correction
from src.services.language.glossary_mining import (
    cluster_corrections,
    cosine,
    is_already_known,
)


@dataclass
class Row:
    """A stand-in for a `correction_log` row, carrying what clustering reads."""

    corrected_target: str
    user_id: str
    embedding: list[float] | None = None
    source_phrase: str = "the machine wording"
    source_language: str = "en"
    target_language: str = "vi"
    domain: str = ""
    audience: str = ""
    anonymized_snippet: str = "… some quoted usage …"
    id: str = field(default="r1")


def near(*leading: float) -> list[float]:
    """A short vector; width does not matter away from the database."""
    return list(leading)


# --- the extraction, which runs at edit time ---------------------------------


def test_a_single_replaced_phrase_is_extracted():
    """The shape worth mining: everything else is untouched, one term changed."""
    found = extract_correction(
        "Please review the user interface again", "Please review the UI again"
    )

    assert found == ("user interface", "UI")


def test_an_unchanged_edit_yields_nothing():
    assert extract_correction("Hello there", "Hello there") is None


def test_an_edit_that_reworks_the_sentence_yields_nothing():
    """Two separate changes mean the reader rewrote it, and no single pair of
    phrases describes what they meant."""
    assert (
        extract_correction(
            "Please review the interface before Friday",
            "Kindly check the UI before Friday",
        )
        is None
    )


def test_a_snippet_carries_no_identifiers():
    """Administrators are barred from reading conversation content; a snippet is
    the compromise, so it has to be one."""
    snippet = build_snippet(
        "Hi john@example.com please review the user interface before 20260820 thanks",
        "the user interface",
    )

    assert "john@example.com" not in snippet
    assert "20260820" not in snippet
    assert "user interface" in snippet


def test_an_empty_phrase_previews_the_start_of_the_text_instead_of_a_match():
    """`original_snippet` calls `build_snippet` with no phrase, since the
    machine's phrase is in the target language and will not appear in the
    sender's own wording. Nothing should be found to centre on, so the window
    falls back to the first few anonymised words."""
    snippet = build_snippet(
        "Hi john@example.com deploy len staging truoc 5 gio nhe", ""
    )

    assert "john@example.com" not in snippet
    assert snippet.startswith("Hi [email] deploy")
    assert snippet.endswith(" …")


# --- the clustering, which runs offline --------------------------------------


def test_two_wordings_of_one_idea_count_as_one_disagreement():
    """The reason embeddings are involved at all. Counted as strings, "UI" and
    "giao dien" each have one occurrence and neither ever crosses a threshold."""
    rows = [
        Row("UI", "u1", near(1.0, 0.0)),
        Row("giao dien", "u2", near(0.99, 0.1)),
        Row("UI", "u3", near(0.98, 0.15)),
    ]

    clusters = cluster_corrections(rows, similarity=0.85, min_count=3, min_users=2)

    assert len(clusters) == 1
    assert clusters[0].occurrence_count == 3
    assert clusters[0].distinct_user_count == 3


def test_one_persons_repeated_preference_is_not_proposed():
    """Five corrections from one person is a preference. The distinct-user
    threshold is the one that separates that from a house style."""
    rows = [Row("UI", "u1", near(1.0, 0.0)) for _ in range(5)]

    assert cluster_corrections(rows, similarity=0.85, min_count=3, min_users=2) == []


def test_a_disagreement_too_rare_to_be_a_pattern_is_not_proposed():
    rows = [Row("UI", "u1", near(1.0, 0.0)), Row("UI", "u2", near(1.0, 0.0))]

    assert cluster_corrections(rows, similarity=0.85, min_count=3, min_users=2) == []


def test_unrelated_corrections_do_not_reinforce_each_other():
    """Grouping too loosely would let three unrelated fixes add up to a term
    nobody proposed, and the reviewer would have no way to tell."""
    rows = [
        Row("UI", "u1", near(1.0, 0.0)),
        Row("deadline", "u2", near(0.0, 1.0)),
        Row("release", "u3", near(0.0, -1.0)),
    ]

    assert cluster_corrections(rows, similarity=0.85, min_count=3, min_users=2) == []


def test_corrections_from_different_language_pairs_are_never_merged():
    """A correction to an en→vi translation says nothing about en→ja, however
    close the two vectors happen to be."""
    rows = [
        Row("UI", "u1", near(1.0, 0.0)),
        Row("UI", "u2", near(1.0, 0.0)),
        Row("UI", "u3", near(1.0, 0.0), target_language="ja"),
    ]

    assert cluster_corrections(rows, similarity=0.85, min_count=3, min_users=2) == []


def test_a_correction_with_no_vector_still_counts_by_its_text():
    """Embedding can fail. A conversation where it did should contribute a
    smaller signal rather than none at all."""
    rows = [
        Row("UI", "u1", None),
        Row("UI", "u2", None),
        Row("UI", "u3", None),
    ]

    clusters = cluster_corrections(rows, similarity=0.85, min_count=3, min_users=2)

    assert len(clusters) == 1


def test_the_proposed_wording_is_the_one_most_people_used():
    """The cluster exists because several people converged; the majority wording
    is what they converged on."""
    rows = [
        Row("UI", "u1", near(1.0, 0.0)),
        Row("giao dien", "u2", near(0.99, 0.1)),
        Row("giao dien", "u3", near(0.98, 0.12)),
    ]

    clusters = cluster_corrections(rows, similarity=0.85, min_count=3, min_users=2)

    assert clusters[0].modal_pair()[1] == "giao dien"


def test_a_term_an_administrator_already_refused_is_not_proposed_again():
    """Asking again next week about a differently worded version of a rejected
    term is how a review queue stops being read."""
    rows = [
        Row("giao dien", "u1", near(1.0, 0.0)),
        Row("UI", "u2", near(0.99, 0.1)),
        Row("giao dien", "u3", near(0.98, 0.12)),
    ]
    cluster = cluster_corrections(rows, similarity=0.85, min_count=3, min_users=2)[0]

    refused = [("man hinh", near(0.99, 0.05))]

    assert is_already_known(cluster, refused, similarity=0.85) is True


def test_an_unrelated_decision_does_not_block_a_proposal():
    rows = [
        Row("UI", "u1", near(1.0, 0.0)),
        Row("UI", "u2", near(1.0, 0.0)),
        Row("UI", "u3", near(1.0, 0.0)),
    ]
    cluster = cluster_corrections(rows, similarity=0.85, min_count=3, min_users=2)[0]

    assert is_already_known(cluster, [("deadline", near(0.0, 1.0))], similarity=0.85) is False


def test_a_term_already_in_the_glossary_is_matched_by_its_text_alone():
    """A decision recorded before embeddings existed still has to count."""
    rows = [Row("UI", f"u{index}", None) for index in range(3)]
    cluster = cluster_corrections(rows, similarity=0.85, min_count=3, min_users=2)[0]

    assert is_already_known(cluster, [("ui", None)], similarity=0.85) is True


def test_cosine_is_one_for_identical_directions_and_zero_for_orthogonal():
    assert cosine([1.0, 0.0], [2.0, 0.0]) == 1.0
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0
