"""Grouping corrections that mean the same thing, so a term can be proposed.

The signal a glossary entry is worth having is not that somebody disagreed with
a translation once. It is that several people, independently, kept fixing the
same wording the same way. Everything here exists to find that pattern and to
refuse everything short of it.

Grouping is by meaning rather than by text, and that is the whole reason
embeddings are involved. "staging env" and "môi trường stg" are different
strings for one idea; counted as strings each has one occurrence and neither
ever crosses a threshold, and if both somehow did, the glossary would end up
with two near-duplicate entries for one concept.

Nothing in this module talks to a database or a model. It takes rows and
returns clusters, which is what makes the thresholds — the part that decides
whether a term reaches a human — testable without either.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

_WHITESPACE = re.compile(r"\s+")


class CorrectionRow(Protocol):
    """The fields clustering reads from a `correction_log` row."""

    id: str
    source_phrase: str
    corrected_target: str
    source_language: str
    target_language: str
    domain: str
    audience: str
    user_id: str
    anonymized_snippet: str
    embedding: Any


def normalize(text: str) -> str:
    """Fold a phrase to the form used for exact grouping and for the key."""
    return _WHITESPACE.sub(" ", (text or "").strip()).casefold()


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity between two vectors of equal width."""
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


@dataclass
class Cluster:
    """Corrections that mean the same thing, and what they add up to."""

    rows: list[Any] = field(default_factory=list)
    # The first row's vector, kept as the thing new members are compared
    # against. A running centroid was rejected: it drifts as members join, so
    # whether a row belongs would depend on the order rows arrived in.
    anchor: list[float] | None = None

    @property
    def occurrence_count(self) -> int:
        """How many corrections the cluster holds."""
        return len(self.rows)

    @property
    def distinct_user_count(self) -> int:
        """How many different people made them.

        The number that matters. Five corrections from one person is a personal
        preference; two from two people is a house style forming.
        """
        return len({row.user_id for row in self.rows})

    @property
    def scope(self) -> tuple[str, str, str, str]:
        """The language pair and subject scope shared by the cluster."""
        first = self.rows[0]
        return (
            first.source_language,
            first.target_language,
            _modal(row.domain for row in self.rows),
            _modal(row.audience for row in self.rows),
        )

    def modal_pair(self) -> tuple[str, str]:
        """The machine wording and the replacement most people settled on.

        Modal rather than first or newest: the cluster exists because several
        people converged, and the majority wording is what they converged on.
        """
        return (
            _modal(row.source_phrase for row in self.rows),
            _modal(row.corrected_target for row in self.rows),
        )

    def citations(self, limit: int = 3) -> list[str]:
        """A few anonymised snippets, deduplicated, for the review queue."""
        seen: list[str] = []
        for row in self.rows:
            snippet = (row.anonymized_snippet or "").strip()
            if snippet and snippet not in seen:
                seen.append(snippet)
            if len(seen) >= limit:
                break
        return seen


def _modal(values: Iterable[str]) -> str:
    """The most common value, ties broken by first appearance."""
    order: list[str] = []
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
        if value not in order:
            order.append(value)
    if not order:
        return ""
    return max(order, key=lambda value: (counts[value], -order.index(value)))


def cluster_corrections(
    rows: Sequence[Any],
    *,
    similarity: float,
    min_count: int,
    min_users: int,
) -> list[Cluster]:
    """Group corrections by meaning and keep only the groups worth proposing.

    Greedy single-pass assignment: each row joins the first cluster whose anchor
    it is close enough to, or starts one. Not the most accurate clustering
    available, and chosen anyway — it is one pass, it is explainable to somebody
    reviewing a proposal, and the thresholds below discard anything marginal, so
    the cost of a slightly wrong grouping is a term that does not get proposed
    rather than one that gets proposed wrongly.

    Rows are only ever compared within one language pair. A correction from an
    en→vi translation says nothing about en→ja.

    Rows with no vector fall back to grouping by exact normalised text, so a
    conversation where embedding failed still contributes rather than being
    silently dropped.

    Args:
        rows: Consented `correction_log` rows.
        similarity: Cosine similarity two corrections must reach to be counted
            as the same one.
        min_count: Corrections a cluster needs before it may be proposed.
        min_users: Different people a cluster needs. This is the threshold that
            separates a house style from one person's preference.

    Returns:
        Qualifying clusters, largest first.
    """
    by_pair: dict[tuple[str, str], list[Cluster]] = {}

    for row in rows:
        pair = (row.source_language, row.target_language)
        clusters = by_pair.setdefault(pair, [])
        vector = list(row.embedding) if row.embedding is not None else None

        placed = False
        for cluster in clusters:
            if _belongs(cluster, row, vector, similarity):
                cluster.rows.append(row)
                placed = True
                break
        if not placed:
            clusters.append(Cluster(rows=[row], anchor=vector))

    qualifying = [
        cluster
        for clusters in by_pair.values()
        for cluster in clusters
        if cluster.occurrence_count >= min_count
        and cluster.distinct_user_count >= min_users
    ]
    return sorted(
        qualifying,
        key=lambda cluster: (-cluster.occurrence_count, cluster.modal_pair()),
    )


def _belongs(
    cluster: Cluster,
    row: Any,
    vector: list[float] | None,
    similarity: float,
) -> bool:
    """Whether a correction means the same as the ones already in a cluster."""
    if vector is not None and cluster.anchor is not None:
        return cosine(cluster.anchor, vector) >= similarity
    # One of the two has no vector, so meaning cannot be compared. Exact text is
    # the only honest fallback, and it is strictly narrower than the vector
    # path — it never merges things the vector path would have kept apart.
    return normalize(row.corrected_target) == normalize(
        cluster.rows[0].corrected_target
    )


def is_already_known(
    cluster: Cluster,
    known: Sequence[tuple[str, Any]],
    *,
    similarity: float,
) -> bool:
    """Whether a cluster repeats something already active or already refused.

    Two very different situations, deliberately answered by one check. An
    administrator who has said no to a term must not be asked again next week
    about a differently-worded version of it, or the queue fills with things
    they have already dismissed and stops being read. And a term already in the
    glossary needs no proposal at all.

    Args:
        cluster: Candidate.
        known: `(normalised_term, embedding_or_None)` for every active entry
            and every rejected proposal in this language pair.
        similarity: Threshold for the vector comparison.

    Returns:
        True when the cluster should not be proposed.
    """
    _, human_phrase = cluster.modal_pair()
    normalized = normalize(human_phrase)
    for term, embedding in known:
        if term == normalized:
            return True
        if (
            cluster.anchor is not None
            and embedding is not None
            and cosine(cluster.anchor, list(embedding)) >= similarity
        ):
            return True
    return False
