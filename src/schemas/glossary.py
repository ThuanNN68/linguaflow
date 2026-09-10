"""Wire shapes for the glossary and its review queue (admin only).

Naming follows the rest of `src/schemas/`: `XxxRequest` for what a client
sends, `XxxResponse` for what it gets back, `XxxSummary` for a nested piece.

What is *not* here is as deliberate as what is. A proposal carries counts and
anonymised snippets and nothing else: no author, no conversation, no message id.
An administrator is barred from reading conversation content
(docs/architecture/assistant-admin.md, diagram 2), and the way to keep that true is for the DTO to
have nowhere to put it.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.schemas.auth import SUPPORTED_LANGUAGES


class GlossaryCitationSummary(BaseModel):
    """One anonymised fragment showing a proposed term in use."""

    model_config = ConfigDict(from_attributes=True)

    anonymized_snippet: str
    observed_at: datetime


class GlossarySimilarEntry(BaseModel):
    """An entry already in the glossary that covers the same source term.

    Shown beside a proposal so a reviewer approves into a known state rather
    than a blank one. Without it the queue invites the same term to be approved
    twice under two scopes with two different renderings, and the lookup then
    picks between them by scope rank — a decision nobody made on purpose.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    source_term: str
    target_term: str
    domain: str
    audience: str
    status: str


class GlossaryProposalResponse(BaseModel):
    """A term waiting for a decision, with the evidence behind it."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    source_term: str
    target_term: str
    source_language: str
    target_language: str
    domain: str
    audience: str
    keep_verbatim: bool
    status: str
    # The pair of numbers a reviewer actually judges on. `distinct_user_count`
    # is the one that matters: five corrections from one person is a preference,
    # two from two people is a house style forming (ADR-28).
    occurrence_count: int
    distinct_user_count: int
    rationale: str
    reject_reason: str
    created_at: datetime
    citations: list[GlossaryCitationSummary] = Field(default_factory=list)
    # What the glossary already says about this term. `similar_entries` is the
    # evidence and `conflicts_with_active` is the verdict a reviewer acts on:
    # true means an entry is live *now* with a different rendering, so the
    # machine has been translating this term correctly by the glossary's current
    # lights and the proposal is asking to change the answer, not to supply a
    # missing one. Those are different decisions and the queue used to show them
    # identically.
    similar_entries: list[GlossarySimilarEntry] = Field(default_factory=list)
    conflicts_with_active: bool = False


class GlossaryEntryResponse(BaseModel):
    """An entry the translation engine is currently bound by."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    source_term: str
    target_term: str
    source_language: str
    target_language: str
    domain: str
    audience: str
    keep_verbatim: bool
    status: str
    created_at: datetime
    updated_at: datetime


def _validate_language(value: str) -> str:
    """Reject a language the rest of the product does not serve."""
    cleaned = (value or "").strip().lower()
    if cleaned not in SUPPORTED_LANGUAGES:
        raise ValueError(f"Unsupported language: {value!r}")
    return cleaned


class GlossaryEntryRequest(BaseModel):
    """A term an administrator is adding by hand."""

    model_config = ConfigDict(extra="forbid")

    source_term: str = Field(min_length=1, max_length=200)
    target_term: str = Field(min_length=1, max_length=200)
    source_language: str = Field(min_length=2, max_length=10)
    target_language: str = Field(min_length=2, max_length=10)
    # Empty means "applies everywhere" and is the fallback the lookup uses when
    # no scoped entry matches. It is a real value, not a missing one, which is
    # why both are plain strings rather than optionals: NULL would not compare
    # equal to NULL and the unique constraint would let duplicates through.
    domain: str = Field(default="", max_length=50)
    audience: str = Field(default="", max_length=50)
    keep_verbatim: bool = False

    @field_validator("source_language", "target_language")
    @classmethod
    def language_must_be_supported(cls, value: str) -> str:
        """Both ends have to be languages the product translates between."""
        return _validate_language(value)

    @field_validator("source_term", "target_term")
    @classmethod
    def term_must_not_be_blank(cls, value: str) -> str:
        """Whitespace would store as set and read as empty."""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Terms must not be blank")
        return cleaned


class GlossaryProposalCreateRequest(GlossaryEntryRequest):
    """A manually submitted glossary proposal, kept pending for review."""

    rationale: str = Field(min_length=1, max_length=1000)

    @field_validator("rationale")
    @classmethod
    def rationale_must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("A proposal needs a rationale")
        return cleaned


class GlossaryEntryUpdateRequest(BaseModel):
    """A correction to an entry that is already in force.

    Every field is optional and only what is sent is changed, so a screen that
    knows nothing about a column added later cannot blank it by omission.

    Editing exists because the alternative is worse. Without it a typo in an
    approved term is fixed by retiring the row and adding a near-identical one,
    which leaves two rows a reader has to compare to work out which one the
    machine is using — and the retired one is kept forever (§16), so the
    confusion is permanent.
    """

    model_config = ConfigDict(extra="forbid")

    source_term: str | None = Field(default=None, min_length=1, max_length=200)
    target_term: str | None = Field(default=None, min_length=1, max_length=200)
    domain: str | None = Field(default=None, max_length=50)
    audience: str | None = Field(default=None, max_length=50)
    keep_verbatim: bool | None = None

    @field_validator("source_term", "target_term")
    @classmethod
    def term_must_not_be_blank(cls, value: str | None) -> str | None:
        """Whitespace would store as set and read as empty."""
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Terms must not be blank")
        return cleaned


class GlossaryApprovalRequest(BaseModel):
    """An approval, optionally correcting the proposal on the way through.

    Every field is optional and overrides what the miner suggested. The miner's
    answer came from a model reading anonymised fragments; the administrator is
    the one who knows what the team actually says, and making them reject and
    re-add a nearly-right term is how a queue stops being worked.
    """

    model_config = ConfigDict(extra="forbid")

    source_term: str | None = Field(default=None, min_length=1, max_length=200)
    target_term: str | None = Field(default=None, min_length=1, max_length=200)
    domain: str | None = Field(default=None, max_length=50)
    audience: str | None = Field(default=None, max_length=50)
    keep_verbatim: bool | None = None


class GlossaryRejectionRequest(BaseModel):
    """A refusal, and why.

    The reason is required. A rejected proposal is kept forever and compared
    against future candidates, so months later somebody will want to know why a
    sensible-looking term never made it in — and "rejected" alone will not tell
    them (ADR-28).
    """

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def reason_must_not_be_blank(cls, value: str) -> str:
        """Whitespace is not a reason."""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("A rejection needs a reason")
        return cleaned
