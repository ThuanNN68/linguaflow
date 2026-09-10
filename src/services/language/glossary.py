"""Finding the terms a translation is required to render a fixed way.

The glossary exists for consistency rather than vocabulary. Left alone a model
renders "staging environment" as "môi trường staging" in one message and "môi
trường dàn dựng" in the next, and a reader cannot tell whether the two sentences
are about the same thing.

Lookup is deliberately in two stages. An exact match on the normalised surface
form is free, deterministic and needs no model; only what that misses is worth
an embedding call, and only when `SEMANTIC_GLOSSARY_ENABLED` is on. The second
stage is what catches "staging env" when the entry says "staging environment" —
the variants people actually type — but it is also the stage that can be wrong,
and a wrongly matched term is *forced* into the translation. Hence a
conservative threshold and exact matches winning outright.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.customization import GlossaryTerm
from src.config import Settings, get_settings
from src.database.models import GlossaryEntry
from src.services.shared.embeddings import embed, embedding_model_name, glossary_threshold_for

logger = logging.getLogger(__name__)

# How many entries one message may pull in. A prompt carrying fifty forced
# renderings stops being a translation instruction and starts being a
# dictionary the model has to read first.
MAX_TERMS_PER_MESSAGE = 12

# Ceiling on the candidate set loaded for one language pair. A glossary large
# enough to exceed this wants an index-driven query rather than a bigger number.
MAX_CANDIDATES = 500

_WHITESPACE = re.compile(r"\s+")


def normalize_term(text: str) -> str:
    """Fold a term to the form the unique constraint and the lookup both use.

    Case-folded rather than lower-cased: `casefold` handles the pairs
    `lower` misses, and a glossary spanning several languages will meet them.
    """
    return _WHITESPACE.sub(" ", (text or "").strip()).casefold()


def normalize_scope(value: Any, allowed: tuple[str, ...]) -> str:
    """Hold a model-supplied scope to the closed vocabulary, or drop it.

    Anything off the list becomes `""`, which means "applies everywhere" — the
    fallback rank the lookup already has a rule for, and the safer of the two
    ways to be wrong.

    The vocabulary is closed because `_scope_rank` below compares scopes by
    equality. A free-text "an external client" is not a slightly worse label
    than "client"; it is a scope no glossary entry is ever filed under, so
    every scoped entry silently stops applying (ADR-24, ADR-26).
    """
    if not isinstance(value, str):
        return ""
    cleaned = value.strip().casefold()
    return cleaned if cleaned in allowed else ""


def _scope_rank(entry: GlossaryEntry, domain: str, audience: str) -> int:
    """Score how specifically an entry matches this conversation.

    Higher wins. Audience outranks subject area because it is the axis the
    feature exists for: a client is owed "giao diện" whatever the subject area
    turns out to be, while a subject area alone never changes who is reading.

    Returns -1 for an entry that does not apply at all, so callers can drop it.
    """
    if entry.domain and entry.domain != domain:
        return -1
    if entry.audience and entry.audience != audience:
        return -1
    return (2 if entry.audience else 0) + (1 if entry.domain else 0)


def contains_term(haystack: str, needle: str) -> bool:
    """Whether a normalised message contains a normalised term as a whole word.

    Substring matching would fire "UI" inside "building", which then forces a
    rendering into a word that has nothing to do with the glossary. The
    boundaries are checked against the normalised text, so punctuation around
    the term is fine and a term that is itself punctuation is not matched.
    """
    if not needle:
        return False
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None


def _cosine(left: list[float], right: list[float]) -> float:
    """Cosine similarity between two vectors of equal width."""
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def select_terms(
    entries: Iterable[Any],
    *,
    text: str,
    domain: str = "",
    audience: str = "",
) -> tuple[GlossaryTerm, ...]:
    """Choose which of the given entries this message has to honour.

    The exact-match half of the lookup, with no database and no model. Split
    out so the evaluation harness scores the same rule production runs instead
    of a second implementation that can drift from it — the mistake that makes
    an evaluation reassuring and wrong at the same time.

    Args:
        entries: Candidate entries, already filtered to one language pair.
        text: The message being translated.
        domain: Subject area of the conversation, empty when unknown.
        audience: Who the conversation is with, empty when unknown.

    Returns:
        At most `MAX_TERMS_PER_MESSAGE` terms, one per source term, longest
        source term first.
    """
    normalized_text = normalize_term(text)
    if not normalized_text:
        return ()

    best: dict[str, tuple[int, Any]] = {}
    for entry in entries:
        if not contains_term(normalized_text, entry.source_term_normalized):
            continue
        rank = _scope_rank(entry, domain, audience)
        if rank < 0:
            continue
        current = best.get(entry.source_term_normalized)
        if current is None or rank > current[0]:
            best[entry.source_term_normalized] = (rank, entry)

    return _to_terms(best)


def _to_terms(best: dict[str, tuple[int, Any]]) -> tuple[GlossaryTerm, ...]:
    """Render the chosen entries in a stable, longest-first order.

    Longest first so a multi-word term is read before the single word inside
    it, and stable so two identical messages produce two identical prompts
    rather than whatever the query plan returned.
    """
    terms = [
        GlossaryTerm(
            source_term=entry.source_term,
            target_term=entry.target_term,
            keep_verbatim=entry.keep_verbatim,
        )
        for _, entry in sorted(
            best.values(),
            key=lambda pair: (-len(pair[1].source_term), pair[1].source_term),
        )
    ]
    return tuple(terms[:MAX_TERMS_PER_MESSAGE])


async def lookup_terms(
    session: AsyncSession,
    *,
    text: str,
    source_language: str,
    target_language: str,
    domain: str = "",
    audience: str = "",
    settings: Settings | None = None,
) -> tuple[GlossaryTerm, ...]:
    """Find the glossary entries this message is required to honour.

    Args:
        session: Open session.
        text: The message being translated.
        source_language: Language the message is written in.
        target_language: Language it is being translated into.
        domain: Subject area inferred for the conversation, empty when unknown.
        audience: Who the conversation is with, empty when unknown.
        settings: Configuration to read. Defaults to the process settings.

    Returns:
        The terms to put in front of the model, at most
        `MAX_TERMS_PER_MESSAGE`, one per source term. Empty on any failure —
        the caller is a graph node, and a missing glossary costs consistency
        rather than delivery.
    """
    settings = settings or get_settings()
    normalized_text = normalize_term(text)
    if not normalized_text or not source_language or not target_language:
        return ()

    try:
        candidates = list(
            (
                await session.scalars(
                    select(GlossaryEntry)
                    .where(
                        GlossaryEntry.source_language == source_language,
                        GlossaryEntry.target_language == target_language,
                        GlossaryEntry.status == "active",
                    )
                    .limit(MAX_CANDIDATES)
                )
            ).all()
        )
    except Exception as exc:
        logger.warning("Loading glossary candidates failed: %s", exc)
        return ()

    # Best entry per source term, so a scoped row replaces the catch-all rather
    # than both being sent and the model left to choose.
    best: dict[str, tuple[int, GlossaryEntry]] = {}

    def offer(entry: GlossaryEntry, bonus: int) -> None:
        """Keep the most specific entry seen for one source term."""
        rank = _scope_rank(entry, domain, audience)
        if rank < 0:
            return
        key = entry.source_term_normalized
        current = best.get(key)
        if current is None or rank + bonus > current[0]:
            best[key] = (rank + bonus, entry)

    # Two stages, not three. A fuzzy stage for mistyped terms was built and
    # then removed: no surface measure separates a misspelt word from a
    # different word spelt almost the same — "headline" and "deadline" are one
    # edit apart — and embeddings are worse at it than string distance, because
    # a typo is not a word and its vector drifts away from the term while two
    # similar real words sit close together. Both measured; both recorded in
    # ADR-26. A mistyped term is therefore left to the translating model, which
    # reads around a slip the way a person does, instead of to a rule that
    # would force the wrong term in whenever it guessed wrong.
    for entry in candidates:
        if contains_term(normalized_text, entry.source_term_normalized):
            # An exact hit outranks any semantic one, whatever their scopes: the
            # message literally contains this term.
            offer(entry, bonus=10)


    if settings.semantic_glossary_enabled:
        await _add_semantic_matches(
            candidates=candidates,
            text=text,
            offer=offer,
            already=set(best),
            settings=settings,
        )

    return _to_terms(best)


async def _add_semantic_matches(
    *,
    candidates: list[GlossaryEntry],
    text: str,
    offer,
    already: set[str],
    settings: Settings,
) -> None:
    """Offer entries whose meaning is close to the message but whose words are not.

    Compared in Python rather than by an index query. The candidate set is
    already bounded and in memory, an HNSW index earns its keep on a table scan
    rather than on a few hundred rows, and doing it here keeps the whole
    decision — scope, exactness, threshold — in one readable place.
    """
    # Plain `embed`, not `embed_with_model`: this is the read path, and falling
    # back here would be work that cannot pay off. A vector from the local model
    # matches no Gemini-stored entry by construction, so the fallback would load
    # a half-gigabyte model onto the request path to produce a query guaranteed
    # to return nothing. Writers fall back — a vector stored under its true model
    # is useful later — readers just go quiet.
    vector = await embed(text, settings=settings)
    if vector is None:
        return

    # The threshold belongs to the space this query vector lives in, so it is
    # looked up by the model that produced it rather than read off a single
    # global constant.
    model = embedding_model_name(settings)
    threshold = glossary_threshold_for(model, settings)
    for entry in candidates:
        if entry.source_term_normalized in already:
            continue
        if not entry.embedding:
            continue
        # A vector from another model sits in a different space; comparing them
        # returns a number, and the number means nothing.
        if model and entry.embedding_model and entry.embedding_model != model:
            continue
        if _cosine(list(entry.embedding), vector) >= threshold:
            offer(entry, bonus=0)
