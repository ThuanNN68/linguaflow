"""Translation persistence models."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .constants import (
    ATTEMPT_OUTCOMES,
    DEFAULT_HONORIFIC_PROFILE,
    EMBEDDING_DIM,
    GLOSSARY_ENTRY_STATUSES,
    GLOSSARY_PROPOSAL_STATUSES,
    HONORIFIC_PROFILES,
    TRANSLATION_TONES,
    _in_clause,
)


class TranslationResult(Base):
    """One message rendered for one target language and one standing.

    Members who share both a target language *and* an honorific profile share a
    row, so the `translation_id` they each receive is the same — which is what
    lets F-05 attach feedback to a translation rather than to a recipient
    (docs/api/contract.md section 4.4).

    The standing is part of the key because Vietnamese, Japanese and Korean
    cannot render a sentence without choosing how the reader is addressed, and
    that choice is not the same for a manager and for a client sitting in the
    same group. Grouping by language alone would force one of them to read the
    wrong register; grouping per recipient would multiply the model calls by the
    size of the group. Four coarse standings is the middle the project settled
    on (ADR-23).

    `honorific_profile` is written once and never rewritten. Profiles are
    inferred and can change; if the read path looked rows up by the *current*
    profile, one re-inference would hide every translation already delivered.
    """

    __tablename__ = "translation_results"
    __table_args__ = (
        CheckConstraint(
            _in_clause("honorific_profile", HONORIFIC_PROFILES),
            name="ck_translation_results_honorific_profile",
        ),
        CheckConstraint(
            _in_clause("translation_tone", TRANSLATION_TONES),
            name="ck_translation_results_translation_tone",
        ),
        UniqueConstraint(
            "message_id",
            "target_language",
            "honorific_profile",
            "translation_tone",
            "version",
            name="uq_translation_results_bucket_version",
        ),
        Index(
            "ix_translation_results_lookup",
            "message_id",
            "target_language",
            "honorific_profile",
            "translation_tone",
            "version",
        ),
        Index("ix_translation_results_message_id", "message_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    message_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_language: Mapped[str] = mapped_column(String(10), nullable=False)
    # The standing this wording addresses the reader with. `peer` is the neutral
    # value and what rows written before the feature existed were backfilled to.
    honorific_profile: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=DEFAULT_HONORIFIC_PROFILE,
    )
    translation_tone: Mapped[str] = mapped_column(
        String(20), nullable=False, default="natural", server_default="natural"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    translated_text: Mapped[str] = mapped_column(Text, nullable=False)
    # Empty when tier 3 of the fallback chain returned the original untranslated
    # (ARCHITECTURE.md section 5.1).
    model: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # True whenever the text did not come from the configured LLM, including a
    # successful secondary-provider translation.
    is_fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class Feedback(Base):
    """A reader's correction of a translation (F-05).

    The table exists so `translation_results` has somewhere to point; the
    endpoint and the correction UI are Sprint 2.
    """

    __tablename__ = "feedbacks"
    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 5", name="ck_feedbacks_rating"),
        Index("ix_feedbacks_translation_id", "translation_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    translation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("translation_results.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    correction: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class TranslationEdit(Base):
    """One reader's attempt at a better wording for a translation (F-05).

    Append-only: editing again writes a new row rather than replacing the old
    one, and the row with the newest `created_at` *for that editor* is the one
    in effect. The history behind it is what a later admin feature would read
    to compare human wording against the machine's (docs/api/contract.md §3.10).

    Private to its author among conversation members. The anonymous admin
    quality-review queue may compare this wording with the source and machine
    translation, but never exposes its editor or conversation. `editor_id`
    still cascades so deleting an account removes the associated edit.
    """

    __tablename__ = "translation_edits"
    __table_args__ = (
        # Ordered the way every read filters: newest edit by one person on one
        # translation, without scanning the rest of the table.
        Index(
            "ix_translation_edits_lookup",
            "translation_id",
            "editor_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    translation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("translation_results.id", ondelete="CASCADE"),
        nullable=False,
    )
    editor_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    edited_text: Mapped[str] = mapped_column(Text, nullable=False)
    # Stamped in Python, unlike every other table here, because this is the only
    # column that has to *order* rows rather than just date them: the newest
    # edit is the one in effect. `func.now()` renders as SQLite's
    # CURRENT_TIMESTAMP, which resolves to whole seconds — two saves inside one
    # second tie, and the tie was resolved by primary key, so fixing a typo and
    # saving twice quickly could bring the older wording back.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )


class TranslationAttempt(Base):
    """One row per (message, target language, standing) tried, whatever the outcome.

    A measurement log, not application state, and the difference drives the
    schema. `translation_results` holds what readers are shown, so it is unique
    on (message_id, target_language) and carries only the fields the product
    contract exposes. This table is append-only: a retry is a second attempt and
    deserves its own row, so there is no unique constraint, and the columns are
    free to change with what the team wants to measure.

    Writes must never be allowed to fail a translation — see `record_attempt`
    in `src/services/language/translation.py`, which is the only writer.
    """

    __tablename__ = "translation_attempts"
    __table_args__ = (
        CheckConstraint(
            _in_clause("outcome", ATTEMPT_OUTCOMES),
            name="ck_translation_attempts_outcome",
        ),
        CheckConstraint(
            _in_clause("translation_tone", TRANSLATION_TONES),
            name="ck_translation_attempts_translation_tone",
        ),
        Index("ix_translation_attempts_created_at", "created_at"),
        Index("ix_translation_attempts_message_id", "message_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    message_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_language: Mapped[str] = mapped_column(String(10), nullable=False)
    # The standing this attempt translated for. Without it the table reports one
    # row per (message, language) as it always did, except there are now up to
    # four of them and nothing to tell them apart — every latency percentile
    # would quietly mix four distributions (docs/api/contract.md section 5, note 9
    # allows this table's columns to change with what is being measured).
    honorific_profile: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=DEFAULT_HONORIFIC_PROFILE,
    )
    translation_tone: Mapped[str] = mapped_column(
        String(20), nullable=False, default="natural", server_default="natural"
    )
    # What the sender's profile claimed, known before the run starts.
    source_language_declared: Mapped[str] = mapped_column(String(10), nullable=False)
    # What detection concluded. Null when detection was skipped or failed, so
    # the gap between the two columns is itself measurable (ADR-11).
    source_language_detected: Mapped[str | None] = mapped_column(String(10), nullable=True)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)

    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    model_configured: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    # What the provider reported serving, which is not always what was asked
    # for: aliases resolve, and providers reroute under load.
    model_served: Mapped[str] = mapped_column(String(100), nullable=False, default="")

    detect_method: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    llm_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    finish_reason: Mapped[str] = mapped_column(String(20), nullable=False, default="")

    detect_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    context_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    translate_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fallback_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Wall clock around the whole run, measured in the service layer. Wider than
    # `translation_results.latency_ms`, which covers model time only: this is
    # what the reader waited, and what NFR-01 is about.
    total_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    context_lines: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Machine-readable code, not prose — the log lines stay for humans.
    fallback_reason: Mapped[str] = mapped_column(String(30), nullable=False, default="")

    # SET NULL rather than CASCADE, the opposite of `feedbacks`, and on purpose:
    # deleting a translation must not delete the record that it happened.
    translation_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("translation_results.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class ConversationProfile(Base):
    """What a conversation is about and who it is with, as inferred by the LLM.

    One row per conversation. Decides two things at translation time: which
    glossary variant applies -- a dev team keeps "UI" while a client is owed
    "giao dien" -- and how formal the result should read.

    The counters are the whole mechanism. Inference does not run per message: it
    waits until there are five, repeats every twenty after that, and stops for
    good once three consecutive runs agree, at which point `locked_at` is
    stamped. That bounds the cost to a handful of calls per conversation and
    stops the audience flickering between messages, which readers would see as
    the register changing mid-thread (ADR-24).
    """

    __tablename__ = "conversation_profiles"
    __table_args__ = (
        # One profile per conversation. The read path loads a whole
        # conversation's profile in a single query on the strength of this.
        UniqueConstraint("conversation_id", name="uq_conversation_profiles_conversation"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Free text rather than an enum: the useful values are not known in advance,
    # and a wrong guess frozen into a CheckConstraint costs a migration. Empty
    # means "not inferred yet", which every read treats as "no preference".
    domain: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    audience: Mapped[str] = mapped_column(String(50), nullable=False, default="")

    # Message count when inference last ran, so the next run falls due at +20
    # messages rather than at a wall-clock interval: a quiet conversation should
    # not burn quota re-deciding something nobody added evidence for.
    message_count_at_last_run: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consecutive_stable_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Set once the profile stops being re-inferred. Null means still open.
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The model's own justification, kept so a wrong audience can be understood
    # rather than merely observed. Never shown to readers.
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    # Stamped in Python for the reason given on `TranslationEdit.created_at`:
    # CURRENT_TIMESTAMP resolves coarsely, and these rows are read by recency.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        server_default=func.now(),
    )


class ParticipantProfile(Base):
    """Where one person stands in one conversation, as inferred by the LLM.

    Keyed by (conversation, user) rather than by user: the same account is a
    `junior` to their manager and a `client` in the thread with a supplier.
    Hanging this off `users` would force those two into one value, and hanging
    it off `conversation_members` was rejected because that table is a bare
    composite key by design (docs/api/contract.md section 5, note 2).

    The value changes as evidence accumulates, so it is deliberately *not* what
    an old translation is looked up by: rows in `translation_results` keep the
    profile they were written under, and the read path falls back through
    `peer`. Without that, one re-inference would blank the translations on a
    whole thread with nothing logged to explain it.
    """

    __tablename__ = "participant_profiles"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "user_id",
            name="uq_participant_profiles_conversation_user",
        ),
        CheckConstraint(
            _in_clause("honorific_profile", HONORIFIC_PROFILES),
            name="ck_participant_profiles_honorific_profile",
        ),
        Index("ix_participant_profiles_conversation_id", "conversation_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    honorific_profile: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=DEFAULT_HONORIFIC_PROFILE,
    )
    # "llm" or "default". Without this there is no way to tell a model that
    # looked and concluded `peer` from a row nobody has looked at yet, and those
    # two deserve different answers to "should this be re-run?".
    inferred_by: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    # 0-100. A low value is a reason to look again, never a reason to block.
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        server_default=func.now(),
    )


class GlossaryEntry(Base):
    """One term the translation is required to render a fixed way.

    The point is consistency, not vocabulary: left to itself a model renders
    "staging environment" as "moi truong staging" in one message and "moi truong
    dan dung" in the next, and a reader cannot tell whether the two sentences
    are about the same thing. An entry removes that choice.

    `domain` and `audience` are what make the same source term resolve two ways:
    a row scoped to an internal audience keeps "UI" verbatim, while the row
    scoped to a client audience renders it "giao dien". Empty means "applies
    everywhere" and acts as the fallback when no scoped row matches, which is
    why both columns are part of the unique constraint rather than nullable --
    NULL would not compare equal to NULL and duplicates would slip through.
    """

    __tablename__ = "glossary_entries"
    __table_args__ = (
        UniqueConstraint(
            "source_term_normalized",
            "source_language",
            "target_language",
            "domain",
            "audience",
            name="uq_glossary_entries_term_scope",
        ),
        CheckConstraint(
            _in_clause("status", GLOSSARY_ENTRY_STATUSES),
            name="ck_glossary_entries_status",
        ),
        # The lookup filters by language pair before it does anything else.
        Index(
            "ix_glossary_entries_languages",
            "source_language",
            "target_language",
            "status",
        ),
        # Nearest-neighbour search over the source terms, so a wording the
        # glossary has never seen literally still finds the entry it means.
        # Cosine because the embedding providers return normalised vectors and
        # magnitude carries no meaning for a short term.
        Index(
            "ix_glossary_entries_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    # As a human wrote it; this is what an admin reads in the review queue.
    source_term: Mapped[str] = mapped_column(String(200), nullable=False)
    # Case-folded and whitespace-collapsed. Stored rather than computed so the
    # unique constraint and the exact-match lookup can both use an index --
    # a function over the column would need a matching expression index and
    # every caller would have to spell the normalisation identically.
    source_term_normalized: Mapped[str] = mapped_column(String(200), nullable=False)
    target_term: Mapped[str] = mapped_column(String(200), nullable=False)
    source_language: Mapped[str] = mapped_column(String(10), nullable=False)
    target_language: Mapped[str] = mapped_column(String(10), nullable=False)
    domain: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    audience: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    # True when the term is to be left in the source language. Redundant with
    # `target_term == source_term`, and kept anyway because the prompt reads
    # better when it can say "keep this untranslated" outright rather than
    # leaving the model to notice that two strings happen to match.
    keep_verbatim: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    # SET NULL, not CASCADE: an admin leaving the project must not take the
    # glossary with them.
    approved_by: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    # Which model produced `embedding`. A vector from another model sits in a
    # different space, so comparing the two returns a confident wrong answer;
    # this column is what lets the lookup skip those instead.
    embedding_model: Mapped[str] = mapped_column(String(100), nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        server_default=func.now(),
    )


class GlossaryProposal(Base):
    """A term the miner believes belongs in the glossary, awaiting review.

    Created only from corrections several people made independently, never from
    the agent's own opinion: the signal is that humans kept fixing the same
    wording, which is evidence a machine cannot manufacture (docs/architecture/assistant-admin.md,
    diagram 4).

    Rejected rows are never deleted. They carry their embedding so the miner can
    check a new candidate against everything an admin has already turned down --
    otherwise the same term comes back next week spelled slightly differently
    and the queue becomes noise nobody reads.
    """

    __tablename__ = "glossary_proposals"
    __table_args__ = (
        CheckConstraint(
            _in_clause("status", GLOSSARY_PROPOSAL_STATUSES),
            name="ck_glossary_proposals_status",
        ),
        # The review queue is "everything still pending, newest first".
        Index("ix_glossary_proposals_status_created_at", "status", "created_at"),
        Index(
            "ix_glossary_proposals_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    source_term: Mapped[str] = mapped_column(String(200), nullable=False)
    source_term_normalized: Mapped[str] = mapped_column(String(200), nullable=False)
    target_term: Mapped[str] = mapped_column(String(200), nullable=False)
    source_language: Mapped[str] = mapped_column(String(10), nullable=False)
    target_language: Mapped[str] = mapped_column(String(10), nullable=False)
    domain: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    audience: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    keep_verbatim: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")

    # How many corrections the cluster contains, and how many distinct people
    # made them. The second number is the one that matters: five corrections
    # from one person is a personal preference, not a house style.
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    distinct_user_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")

    reviewed_by: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Required when rejecting. Read by whoever wonders later why a sensible
    # looking term never made it in.
    reject_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")

    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    embedding_model: Mapped[str] = mapped_column(String(100), nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class GlossaryProposalCitation(Base):
    """An anonymised fragment showing a proposal being used in the wild.

    Admins have to be able to judge a proposed term, and a term pair on its own
    does not say enough. It also cannot cost the reader their privacy: the
    admin role is explicitly barred from reading conversation content
    (docs/architecture/assistant-admin.md, diagram 2).

    The compromise the design settled on is both halves at once -- the
    correction must carry `consent_to_share`, *and* only a few words around the
    term survive into the snippet, with identifiers stripped. Nothing here says
    who wrote it, which conversation it came from, or what was said around it.
    """

    __tablename__ = "glossary_proposal_citations"
    __table_args__ = (Index("ix_glossary_proposal_citations_proposal_id", "proposal_id"),)

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    proposal_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("glossary_proposals.id", ondelete="CASCADE"),
        nullable=False,
    )
    anonymized_snippet: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class CorrectionLog(Base):
    """The term-level signal behind a reader's edit, kept for mining.

    Separate from `translation_edits` on purpose. That table stays what ADR-19
    made it: append-only and private to its author, readable by nobody else.
    Mining it directly would quietly revoke that promise. This table holds only
    the derived pair -- what the machine wrote, what the human wrote instead --
    and only the rows whose author agreed to share (docs/architecture/assistant-admin.md 3.1,
    option A). The two live side by side so the privacy rule stays a rule.

    `consent_to_share` gates the whole row, not just the citation: counting a
    correction somebody declined to share would still be using it.
    """

    __tablename__ = "correction_log"
    __table_args__ = (
        # The miner reads one time window at a time, consented rows only.
        Index("ix_correction_log_consent_observed_at", "consent_to_share", "observed_at"),
        Index(
            "ix_correction_log_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    # The phrase as the machine rendered it, and what the reader replaced it
    # with. Extracted by rule at edit time rather than by a model: this sits on
    # the request path, and a wrong extraction here costs nothing because the
    # clustering downstream drops anything that does not repeat.
    source_phrase: Mapped[str] = mapped_column(String(200), nullable=False)
    corrected_target: Mapped[str] = mapped_column(String(200), nullable=False)
    source_language: Mapped[str] = mapped_column(String(10), nullable=False)
    target_language: Mapped[str] = mapped_column(String(10), nullable=False)
    # Copied from the conversation profile at the time of the edit, not looked
    # up later: the profile can be re-inferred, and this row is evidence about
    # the conversation as it was when somebody objected to the wording.
    domain: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    audience: Mapped[str] = mapped_column(String(50), nullable=False, default="")

    # Who corrected it, needed only to count distinct people per cluster. Never
    # reaches an admin: the review queue receives counts and snippets.
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # SET NULL rather than CASCADE, matching `translation_attempts`: deleting a
    # translation must not delete the evidence that somebody corrected it.
    translation_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("translation_results.id", ondelete="SET NULL"),
        nullable=True,
    )
    consent_to_share: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Prepared here, at the one moment the surrounding text is in hand, rather
    # than in the miner where it would need the conversation back again.
    # Anonymised the same way as `anonymized_snippet` below (names, links and
    # long digit runs stripped), but drawn from `Message.original_text` rather
    # than the machine's rendering — the sender's own wording, in whichever
    # language they wrote it, rather than the reader's reading language. An
    # admin judging a proposed term otherwise sees only one side of the
    # translation it came from (24/08).
    original_snippet: Mapped[str] = mapped_column(Text, nullable=False, default="")
    anonymized_snippet: Mapped[str] = mapped_column(Text, nullable=False, default="")

    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    embedding_model: Mapped[str] = mapped_column(String(100), nullable=False, default="")

    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class MessageEmbedding(Base):
    """A message's vector, so context can be retrieved by meaning as well as time.

    `build_context` takes the last three to five messages, which is right for
    resolving a pronoun and useless for a reference to something agreed forty
    messages ago. This table is what lets the retrieval add the nearest
    neighbours by meaning alongside the newest by clock (ADR-26).

    A separate table rather than a column on `messages`: that table is hot, it
    is named field by field in docs/api/contract.md section 5, and this is derived
    data that can be recomputed at any time -- the same split, for the same
    reasons, that ADR-16 made for `translation_attempts`.
    """

    __tablename__ = "message_embeddings"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_message_embeddings_message"),
        Index(
            "ix_message_embeddings_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    message_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Denormalised from `messages` so the nearest-neighbour search can be scoped
    # to one conversation without a join: a vector index is only used when the
    # filter it is combined with is cheap, and retrieval must never be able to
    # reach across conversations.
    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    embedding_model: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
