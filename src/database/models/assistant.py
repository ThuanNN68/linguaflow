"""Assistant persistence models."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .constants import (
    ACTION_PROPOSAL_SOURCE_MODES,
    ACTION_PROPOSAL_STATUSES,
    ACTION_PROPOSAL_TYPES,
    AGENT_CONSENT_SCOPES,
    ASSISTANT_CHUNK_STRATEGIES,
    ASSISTANT_JOB_STATUSES,
    ASSISTANT_MEMORY_KINDS,
    ASSISTANT_OUTCOMES,
    EMBEDDING_DIM,
    _in_clause,
)


class AssistantJob(Base):
    """Durable queue entry for one private Assistant turn."""

    __tablename__ = "assistant_jobs"
    __table_args__ = (
        CheckConstraint(_in_clause("status", ASSISTANT_JOB_STATUSES), name="ck_assistant_jobs_status"),
        UniqueConstraint("message_id", name="uq_assistant_jobs_message_id"),
        Index("ix_assistant_jobs_ready", "status", "available_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    message_id: Mapped[str] = mapped_column(String(36), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    requester_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    request_text: Mapped[str] = mapped_column(Text, nullable=False)
    trusted_timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), server_default=func.now()
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        onupdate=lambda: datetime.now(UTC),
    )


class AgentConsent(Base):
    """One permission a user has granted, or refused, to the assistant.

    A table rather than five more columns on `UserSettings`, for three reasons
    that are all mechanical rather than stylistic. A permission has to record
    *when* it was given and taken back, which a boolean column cannot do. Its
    `policy_version` is per-scope, so adding a sixth permission may only re-ask
    about that one instead of invalidating the five already granted. And the
    list will keep growing, which on `UserSettings` would mean repeatedly
    altering a table every request reads.

    Absence of a row means **not granted** — the default fails closed, the same
    way `CorrectionLog.consent_to_share` defaults to false.
    """

    __tablename__ = "agent_consents"
    __table_args__ = (
        CheckConstraint(
            _in_clause("scope", AGENT_CONSENT_SCOPES),
            name="ck_agent_consents_scope",
        ),
        UniqueConstraint("user_id", "scope", name="uq_agent_consents_user_scope"),
        Index("ix_agent_consents_user_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    is_granted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # Revoking keeps the row and only clears the flag. Deleting it would make
    # "never asked" and "asked and refused" indistinguishable, and that
    # distinction is exactly what decides whether to prompt again.
    granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    policy_version: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        onupdate=lambda: datetime.now(UTC),
    )


class ActionProposal(Base):
    """An AI-proposed action extracted from a message awaiting human confirmation (B-04/B-05)."""

    __tablename__ = "action_proposals"
    __table_args__ = (
        CheckConstraint(
            _in_clause("action_type", ACTION_PROPOSAL_TYPES),
            name="ck_action_proposals_action_type",
        ),
        CheckConstraint(
            _in_clause("status", ACTION_PROPOSAL_STATUSES),
            name="ck_action_proposals_status",
        ),
        CheckConstraint(
            _in_clause("source_mode", ACTION_PROPOSAL_SOURCE_MODES), name="ck_action_proposals_source_mode"
        ),
        CheckConstraint("confidence_score >= 0 AND confidence_score <= 1", name="ck_action_proposals_confidence"),
        CheckConstraint("clarification_rounds >= 0", name="ck_action_proposals_clarification_rounds"),
        Index("ix_action_proposals_conversation_id", "conversation_id"),
        Index("ix_action_proposals_source_message_id", "source_message_id"),
        Index("ix_action_proposals_time_source_message_id", "time_source_message_id"),
        Index("ix_action_proposals_details_source_message_id", "details_source_message_id"),
        Index("ix_action_proposals_owner_status", "owner_user_id", "status"),
        Index("ix_action_proposals_status", "status"),
        UniqueConstraint("idempotency_key", name="uq_action_proposals_idempotency_key"),
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
    source_message_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Field-level provenance for values completed from nearby messages. These
    # remain nullable for proposals created before contextual extraction.
    time_source_message_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    details_source_message_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    owner_user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    source_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="on_demand", server_default="on_demand"
    )
    action_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending_confirmation",
        server_default="pending_confirmation",
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    details: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_time_expression: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scheduled_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scheduled_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    confidence_score: Mapped[float] = mapped_column(
        nullable=False,
        default=1.0,
        server_default="1.0",
    )
    clarification_prompt: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    clarification_question: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    missing_fields: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    clarification_rounds: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    idempotency_key: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    confirmed_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    rejected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    stale_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # When the owner cleared this out of their task inbox. It hides the row from
    # that list and nothing else: an approved proposal has already produced a
    # calendar event, and that event and its reminders are unaffected. Deleting
    # the row instead would take the calendar entry with it through the
    # `action_proposal_id` link, which is the opposite of what somebody tidying
    # a finished list expects to happen.
    dismissed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:
        return f"<ActionProposal(id={self.id}, type={self.action_type}, status={self.status}, title={self.title})>"


class AssistantChunk(Base):
    """A retrievable piece of a conversation, for the Assistant Agent only.

    Deliberately not `message_embeddings`, and not for tidiness. A chunk is not
    a message: it may gather six short messages that between them carry one
    fact, or split one long message into three. There is therefore no one-to-one
    relationship to hang a foreign key on, which is why `message_ids` is a JSON
    array -- the same encoding `action_proposals.missing_fields` already uses.

    The other half of the separation is the model. The assistant may embed with
    a different provider than translation (ADR-39), and vectors from two models
    occupy different spaces: comparing across them returns a confident ranking
    that means nothing, with no error anywhere. Every query against this table
    must filter `embedding_model`.

    Derived data, like `message_embeddings`: it can be dropped and rebuilt at any
    time from `messages`, and nothing outside the assistant's retrieval path may
    read it.
    """

    __tablename__ = "assistant_chunks"
    __table_args__ = (
        CheckConstraint(
            _in_clause("strategy", ASSISTANT_CHUNK_STRATEGIES),
            name="ck_assistant_chunks_strategy",
        ),
        # `strategy` is inside the key on purpose: several chunkings of one
        # conversation coexist so they can be compared over the same data.
        UniqueConstraint(
            "conversation_id",
            "strategy",
            "chunk_index",
            name="uq_assistant_chunks_conversation_strategy_index",
        ),
        # The filter that accompanies every vector scan. A vector index is only
        # used when the filter beside it is cheap, and retrieval must never be
        # able to reach into another conversation.
        Index(
            "ix_assistant_chunks_scope",
            "conversation_id",
            "strategy",
            "embedding_model",
        ),
        Index(
            "ix_assistant_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index(
            "ix_assistant_chunks_lexical",
            text("to_tsvector('simple', chunk_text)"),
            postgresql_using="gin",
        ),
        Index(
            "ix_assistant_chunks_trigram",
            "chunk_text",
            postgresql_using="gin",
            postgresql_ops={"chunk_text": "gin_trgm_ops"},
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Denormalised from `messages` so the nearest-neighbour search can be scoped
    # without a join, for the reason `message_embeddings` gives.
    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    strategy: Mapped[str] = mapped_column(String(30), nullable=False)
    # Position within this conversation *under this strategy*, from zero. What
    # makes `parent_index` and neighbour expansion addressable.
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    # JSON array of the messages this chunk covers, in order. Carried so an
    # answer can cite the messages it came from rather than the chunk, which is
    # an artefact of retrieval that means nothing to a reader.
    message_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # First and last message in the chunk. Present so "what did we decide last
    # week" can be answered without loading the messages back to find out when
    # they were sent.
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Only meaningful under `parent_child`: the index of the wider chunk this one
    # expands into before reaching the prompt. NULL everywhere else.
    parent_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    embedding_model: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"<AssistantChunk(conversation_id={self.conversation_id}, "
            f"strategy={self.strategy}, chunk_index={self.chunk_index})>"
        )


class AssistantUserMemory(Base):
    """A durable fact the assistant has learned about one person.

    Separate from `assistant_chunks` because it is not part of any conversation
    transcript: "prefers a day's notice", "owns the payments area", "has a sync
    every Monday". `conversation_id` is nullable for the same reason -- something
    learned in one group is often true everywhere.

    Superseded rather than overwritten. Replacing the row in place would make
    "never said anything about this" and "said otherwise and changed their mind"
    indistinguishable, and the second is the one that decides whether to ask.
    The same reasoning keeps revoked rows in `agent_consents`.

    Every row here lives under the `store_memory` consent. Without it nothing is
    written at all.
    """

    __tablename__ = "assistant_user_memory"
    __table_args__ = (
        CheckConstraint(
            _in_clause("kind", ASSISTANT_MEMORY_KINDS),
            name="ck_assistant_user_memory_kind",
        ),
        # Recall is always scoped to one person and skips superseded rows, so
        # both columns belong in the index and in that order.
        Index("ix_assistant_user_memory_owner", "user_id", "superseded_by"),
        Index(
            "ix_assistant_user_memory_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # NULL when the fact is not tied to one thread.
    conversation_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # This is inferred by a model, not stated by the user. A weak inference has
    # to rank below a firm one when both are recalled at once.
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5, server_default="0.5")
    # SET NULL, not CASCADE: deleting a message must not delete what was learned
    # from it, but must not leave a key pointing at nothing either.
    source_message_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    # The row that replaced this one. NULL means this is the current fact.
    superseded_by: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("assistant_user_memory.id", ondelete="SET NULL"),
        nullable=True,
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    embedding_model: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"<AssistantUserMemory(user_id={self.user_id}, kind={self.kind}, "
            f"superseded={self.superseded_by is not None})>"
        )


class AssistantAttempt(Base):
    """One row per Assistant Agent run, whatever it produced.

    The same arrangement `translation_attempts` has and for the same reason
    (ADR-16): this is a **measurement log, not application state**. Nothing
    outside `src/services/assistant/assistant_telemetry.py` and
    `scripts/maintenance/report_metrics.py` may read it, and its columns are free to change
    with what needs measuring — no contract depends on them.

    Rows are written at every exit, including `refused`, `clarified` and
    `empty`, which produce no proposal and no answer. Those are the reason the
    table exists: without them, "how often does the assistant reach the gate"
    can only be computed over the runs that reached the gate.

    No unique constraint. Asking the assistant the same thing twice is two runs
    and deserves two rows, exactly as re-running a translation does.
    """

    __tablename__ = "assistant_attempts"
    __table_args__ = (
        CheckConstraint(
            _in_clause("outcome", ASSISTANT_OUTCOMES),
            name="ck_assistant_attempts_outcome",
        ),
        # Every report groups by time, and most filter by conversation. Ordered
        # so the common query — "the last week, for this thread" — is one range
        # scan rather than a sort.
        Index("ix_assistant_attempts_created", "created_at"),
        Index("ix_assistant_attempts_conversation", "conversation_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # SET NULL rather than CASCADE, the choice note 9 already explains for
    # `translation_id`: deleting a conversation must not delete the evidence
    # that the assistant was asked something in it.
    conversation_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
    )
    user_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_message_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    # Which model actually planned. Recorded per row rather than read from
    # configuration at report time, because configuration changes and a row
    # describes the run that happened, not the deployment reading it.
    provider: Mapped[str] = mapped_column(String(30), nullable=False, default="", server_default="")
    model_configured: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    # How many times the planner was asked again. The distribution of this is
    # what says whether MAX_REPLANS is set anywhere near right: all runs at zero
    # means the loop is not earning its cost, all runs at the ceiling means it
    # is being cut off mid-thought.
    replans: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    tools_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # JSON array of tool names in call order, the encoding
    # `action_proposals.missing_fields` already uses. Kept as a list rather than
    # a count because "which tool" is the question a failure raises, and a count
    # cannot answer it.
    tools_used: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")

    proposals_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Confirmed **within this run**. A proposal approved later through the REST
    # endpoint belongs to that request, not this one — counting it here would
    # attribute an approval to a run that had already ended (ADR-32).
    proposals_executed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    memory_lines: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Of those lines, how many came from retrieval rather than from the recent
    # window. The ratio is what says whether `assistant_chunks` is doing
    # anything at all — zero everywhere means the index is empty and nobody
    # would otherwise notice (ADR-37).
    memory_recalled: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    total_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Exception type when `outcome` is `error`, or the consent scope when it is
    # `refused`. A short, groupable string rather than a message, so a report
    # can count causes instead of printing them.
    error_code: Mapped[str] = mapped_column(String(80), nullable=False, default="", server_default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"<AssistantAttempt(outcome={self.outcome}, replans={self.replans}, tools={self.tool_calls})>"
