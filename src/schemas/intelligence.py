"""Pydantic schemas for Conversation Intelligence operations (B-03, B-04, B-05, B-08, B-10)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.schemas.auth import normalize_language
from src.schemas.chat import UtcDatetime

# ---------------------------------------------------------------------------
# B-03 Conversation Summary Schemas
# ---------------------------------------------------------------------------


class ConversationSummaryPayload(BaseModel):
    """Internal LLM payload for conversation summary."""

    model_config = ConfigDict(extra="ignore")

    summary: str = Field(description="Comprehensive summary of the conversation")
    key_points: list[str] = Field(default_factory=list, description="Key discussion topics")
    decisions: list[str] = Field(default_factory=list, description="Explicit decisions or agreements")
    open_items: list[str] = Field(default_factory=list, description="Unresolved questions or pending tasks")


class ConversationSummaryRequest(BaseModel):
    """Request payload for on-demand conversation summary."""

    model_config = ConfigDict(extra="forbid")

    # Raised from 50/100 when summarisation gained a map-reduce stage
    # (ADR-38). The old ceiling was set by what one prompt could hold; above
    # LONG_CONVERSATION_THRESHOLD the work is now split into batches, so the
    # limit is about cost and latency rather than about the context window.
    message_limit: int = Field(default=200, ge=1, le=2000)
    target_language: str | None = Field(
        default=None,
        description="Target ISO 639-1 language code (e.g. 'vi', 'en'). Defaults to user's preferred language.",
    )

    @field_validator("target_language")
    @classmethod
    def validate_target_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_language(value)


class ConversationSummaryResponse(BaseModel):
    """Response payload for on-demand conversation summary."""

    summary: str
    key_points: list[str]
    decisions: list[str]
    open_items: list[str]
    message_count: int
    target_language: str
    window_start_at: UtcDatetime | None = None
    window_end_at: UtcDatetime | None = None


# ---------------------------------------------------------------------------
# B-04, B-05, B-08, B-10 Action Proposal Schemas
# ---------------------------------------------------------------------------

ActionProposalType = Literal["task", "appointment"]
ActionProposalStatus = Literal["needs_clarification", "pending_confirmation", "confirmed", "rejected", "stale"]
CandidateRelationship = Literal[
    "requester_self_commitment",
    "requester_assigned_action",
    "requester_appointment",
    "other_participant_self_commitment",
    "other_or_unknown",
]


class ActionCandidateDTO(BaseModel):
    """Extracted action candidate prior to or during persistence."""

    model_config = ConfigDict(extra="ignore")

    # This is legacy model output only. Persistence always receives the owner
    # from the authenticated/server-side workflow and never trusts this value.
    owner_user_id: str | None = Field(default=None, description="Untrusted model attribution hint")
    action_type: ActionProposalType = Field(description="'task' or 'appointment'")
    title: str = Field(description="Action title or summary", max_length=255)
    details: str | None = Field(default=None, description="Additional context or notes")
    location: str | None = Field(default=None, max_length=200, description="Meeting location or call link")
    scheduled_time: UtcDatetime | None = Field(default=None, description="Due date or appointment start time")
    scheduled_end_time: UtcDatetime | None = Field(
        default=None,
        description="Appointment end time when it is explicitly stated in the message",
    )
    confidence_score: float = Field(default=1.0, ge=0.0, le=1.0, description="Extraction confidence")
    clarification_prompt: str | None = Field(default=None, description="Question asked if details are ambiguous")
    raw_time_expression: str | None = None
    resolved_timezone: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    relationship: CandidateRelationship = "other_or_unknown"
    location: str | None = None


class ActionExtractionPayload(BaseModel):
    """Internal LLM structured extraction output."""

    model_config = ConfigDict(extra="ignore")

    candidates: list[ActionCandidateDTO] = Field(default_factory=list)


class ActionProposalResponse(BaseModel):
    """Public representation of an ActionProposal."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    source_message_id: str
    owner_user_id: str
    action_type: ActionProposalType
    status: ActionProposalStatus
    title: str
    details: str | None = None
    scheduled_time: UtcDatetime | None = None
    confidence_score: float = 1.0
    clarification_prompt: str | None = None
    clarification_question: str | None = None
    source_mode: Literal["on_demand", "proactive"]
    created_by_user_id: str | None = None
    # Display-only provenance. These values are derived from the source message
    # and conversation; they are never accepted from the client.
    source_sender_name: str | None = None
    source_conversation_name: str | None = None
    source_conversation_type: Literal["direct", "group"] | None = None
    raw_time_expression: str | None = None
    scheduled_start_at: UtcDatetime | None = None
    scheduled_end_at: UtcDatetime | None = None
    due_at: UtcDatetime | None = None
    location: str | None = None
    resolved_timezone: str | None = None
    missing_fields: str = "[]"
    clarification_rounds: int = 0
    confirmed_by_user_id: str | None = None
    idempotency_key: str
    created_at: UtcDatetime
    updated_at: UtcDatetime
    confirmed_at: UtcDatetime | None = None
    rejected_at: UtcDatetime | None = None
    stale_at: UtcDatetime | None = None


# Alias DTO for service layers
ActionProposalDTO = ActionProposalResponse


class ClarifyProposalRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=2000)
    timezone: str | None = Field(default=None, max_length=64)


class ConfirmProposalRequest(BaseModel):
    # Approving is where a person adds the detail the message never contained.
    # The extractor reads what was said — "review thiết kế 10h sáng thứ Tư" — and
    # nobody says how long beforehand they want to be nudged, so this is asked
    # at approval rather than guessed from the text.
    #
    # Same name and same semantics as `CalendarEventCreateRequest`: omitted
    # means fifteen minutes, `null` means no reminder at all. Deliberately not a
    # second convention — a product where the reminder field means one thing on
    # one screen and another on the next is one nobody can hold in their head.
    reminder_minutes_before: int | None = Field(default=15, ge=0, le=10080)
    title: str | None = Field(default=None, max_length=255)
    details: str | None = None
    location: str | None = None
    scheduled_start_at: UtcDatetime | None = None
    scheduled_end_at: UtcDatetime | None = None
    due_at: UtcDatetime | None = None
    resolved_timezone: str | None = Field(default=None, max_length=64)
    timezone: str | None = Field(default=None, max_length=64)
