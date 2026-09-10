"""Argument schemas for the tools the Assistant Agent may call (ADR-40).

One Pydantic model per tool. They are not API schemas — nothing here is
serialised over HTTP — but they live in `src/schemas/` because that is where
this project keeps Pydantic models, and because their real job is the same one
a request schema does: reject a malformed call at the boundary rather than
halfway through the service beneath it.

The boundary matters more here than in a REST handler. These arguments come from
a language model, so every field is a place the model can be wrong, and
`extra="forbid"` is what turns "the planner hallucinated a parameter" into a
caught error instead of a silently ignored instruction. Descriptions are written
for the planner rather than for a developer: they are rendered into the prompt
that decides which tool to call.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.database.models import ASSISTANT_MEMORY_KINDS


class _ToolArguments(BaseModel):
    """Shared configuration: reject anything not declared."""

    model_config = ConfigDict(extra="forbid")


class SearchOldMessagesArguments(_ToolArguments):
    """Find earlier parts of this conversation by meaning."""

    query: str = Field(
        min_length=1,
        max_length=500,
        description=(
            "What to look for, as a self-contained question. Ask about one "
            "thing; call the tool twice for two things."
        ),
    )
    top_n: int = Field(
        default=4,
        ge=1,
        le=10,
        description="How many passages to bring back. More is not better; each one costs context.",
    )


class SummarizeConversationArguments(_ToolArguments):
    """Condense the conversation into key points, decisions and open items."""

    message_limit: int = Field(
        default=200,
        ge=1,
        le=2000,
        description="How far back to read. The whole thread is summarised in batches above 60 messages.",
    )
    target_language: str | None = Field(
        default=None,
        max_length=10,
        description="ISO 639-1 code. Omit to use the reader's own language.",
    )


class ExtractActionsArguments(_ToolArguments):
    """Propose tasks and appointments found in the triggering message."""

    # No fields. The message is the one that triggered the run, which the state
    # already carries; letting the planner name a message id would let it point
    # the extractor at something the user never mentioned.


class ListCalendarEventsArguments(_ToolArguments):
    """Read the person's own calendar over a window."""

    starts_after: datetime | None = Field(
        default=None, description="Only entries starting at or after this instant."
    )
    starts_before: datetime | None = Field(
        default=None, description="Only entries starting before this instant."
    )


class ProposeCalendarEventArguments(_ToolArguments):
    """Propose a calendar entry. It reaches the calendar only once approved."""

    title: str = Field(min_length=1, max_length=200)
    starts_at: datetime = Field(
        description="Absolute start. Never a relative phrase — resolve it first."
    )
    ends_at: datetime | None = Field(
        default=None, description="Absolute end. Omit for the default duration."
    )
    details: str | None = Field(default=None, max_length=2000)
    location: str | None = Field(default=None, max_length=200)
    # Two fields are deliberately absent, and both for the same reason: the
    # model has nothing to read them from, so offering the field would only let
    # it invent one.
    #
    # `timezone` — `normalize_action_time` treats a timezone as *authenticated
    # owner input* and refuses to take one from model output
    # (`create_proposals_from_candidates` passes `trusted_timezone=None`). A
    # field here would be accepted, validated, and then silently dropped on the
    # way to the row: the worst kind of parameter, because it looks like it
    # works. The owner supplies it at approval, where `resolved_timezone` is in
    # the correction allowlist.
    #
    # `reminder_minutes_before` — nobody writes how much warning they want in a
    # chat message. Asked at the approval step, where somebody is already
    # looking at the proposal.


class RecallUserMemoryArguments(_ToolArguments):
    """Look up what is already known about this person."""

    query: str = Field(min_length=1, max_length=300)
    top_n: int = Field(default=3, ge=1, le=10)


class SaveUserMemoryArguments(_ToolArguments):
    """Record something durable about this person."""

    # Constrained rather than merely described. A description tells the planner
    # what to send; only a type rejects what it actually sent, and the failure
    # this catches travels a long way otherwise — an unknown kind passes the
    # schema, passes the service, and surfaces as a CheckConstraint violation
    # from inside a transaction, where the message names the constraint instead
    # of the vocabulary.
    kind: Literal[*ASSISTANT_MEMORY_KINDS] = Field(
        description=f"One of: {', '.join(ASSISTANT_MEMORY_KINDS)}.",
    )
    content: str = Field(
        min_length=1,
        max_length=500,
        description=(
            "One fact, stated plainly and out of context — it will be recalled "
            "in conversations that have nothing to do with this one."
        ),
    )
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="How sure this is. A guess belongs below 0.5, not omitted.",
    )
