"""Wire shapes for the administrator's translation-feedback review (F-05).

The review queue is deliberately anonymous: it exposes no sender, reader,
conversation or message identifier. An administrator can compare the source,
machine rendering, vote and reader correction needed to investigate translation
quality, but cannot connect the row to a person or chat thread.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FeedbackVoteSummary(BaseModel):
    """How readers rated the translations they were shown.

    Two named buckets, not three. The interface offers a thumb up and a thumb
    down and nothing between them, so a "neither" count was always zero and
    read on the screen as a third opinion nobody was holding.

    `ratings` is the raw 1-to-5 histogram the `feedbacks` table stores, kept
    beside the two buckets so a value outside them stays visible instead of
    vanishing — that is the signal that the interface has changed.
    """

    up: int = 0
    down: int = 0
    total: int = 0
    # Share of votes that were positive, 0 when nobody has voted. Rounded to
    # four places: this is a proportion for a screen, not an accounting figure.
    up_rate: float = 0.0
    ratings: dict[int, int] = Field(default_factory=dict)


class SharedCorrection(BaseModel):
    """One correction a reader allowed to be used for the shared glossary.

    Exactly the columns the term miner reads, which is the point: this screen
    exists so an administrator can see what the miner is working from before a
    proposal appears, rather than only the proposals it eventually produces.
    """

    model_config = ConfigDict(from_attributes=True)

    source_phrase: str
    corrected_target: str
    source_language: str
    target_language: str
    domain: str
    audience: str
    # Both prepared at edit time with names and numbers removed, and both are
    # the only message-derived text on this screen — a term pair with no usage
    # around it cannot be judged. `original_snippet` previews the sender's own
    # wording (in `source_language`); `anonymized_snippet` is the window around
    # the corrected phrase in the machine's rendering (in `target_language`).
    original_snippet: str
    anonymized_snippet: str
    observed_at: datetime


class FeedbackReviewEntry(BaseModel):
    """One anonymous vote or wording edit shown in the admin review table.

    Feedback and edits are persisted separately: a vote replaces a reader's
    prior vote while an edit is append-only. The UI receives one chronological
    list so it can show both without pretending an edit is a down-vote.
    """

    entry_type: Literal["vote", "edit"]
    original_text: str
    translated_text: str
    source_language: str
    target_language: str
    model: str
    vote: Literal["up", "down", "other"] | None = None
    rating: int | None = None
    user_correction: str | None = None
    created_at: datetime
    # Identical anonymous signals are folded into one review row.  This makes
    # repeated test messages readable without discarding the volume of evidence.
    occurrence_count: int = 1


class FeedbackOverviewResponse(BaseModel):
    """Everything the feedback tab shows in one request.

    One response rather than three endpoints: the three parts are read together
    and are cheap, and splitting them would put three spinners on one screen.
    """

    votes: FeedbackVoteSummary
    # Distinct translations that have at least one reader edit.  This is kept
    # separate from the visible review rows because the latter is capped by
    # ``limit`` and must not be used as a dashboard total.
    edited_translations: int = 0
    # Latest anonymous individual signals, newest first. This is the
    # operational quality-review table; it intentionally contains no account,
    # conversation or message identifiers.
    review_entries: list[FeedbackReviewEntry] = Field(default_factory=list)
    # Consented corrections, newest first, capped by the request's `limit`.
    shared_corrections: list[SharedCorrection] = Field(default_factory=list)
    # How many consented corrections exist in total, which is not the length of
    # the list above once the cap bites.
    shared_total: int = 0
    # Corrections whose author did not consent. A count and nothing else: the
    # rows exist, and the number is what makes it visible that they are being
    # left out rather than silently missing.
    withheld_total: int = 0
