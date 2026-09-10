"""Calendar persistence models."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .constants import (
    CALENDAR_EVENT_SOURCES,
    CALENDAR_EVENT_STATUSES,
    _in_clause,
)


class CalendarEvent(Base):
    """One entry on a user's personal calendar (B-12).

    This is what a confirmed proposal becomes, and it is the reason
    `status = "confirmed"` stopped being a dead end. A proposal records that
    somebody said they would do something; an event records that it is on a
    calendar at a time. Keeping them apart matters because they diverge: the
    user moves an event, Google moves an event, an event is cancelled â€” none of
    which changes the fact that the commitment was made and approved.

    `action_proposal_id` uses SET NULL rather than CASCADE. Where the entry came
    from should outlive the proposal row, the same choice `translation_attempts`
    makes for `translation_id` (Â§5 note 9).
    """

    __tablename__ = "calendar_events"
    __table_args__ = (
        CheckConstraint(_in_clause("source", CALENDAR_EVENT_SOURCES), name="ck_calendar_events_source"),
        CheckConstraint(_in_clause("status", CALENDAR_EVENT_STATUSES), name="ck_calendar_events_status"),
        CheckConstraint("ends_at IS NULL OR ends_at >= starts_at", name="ck_calendar_events_ends_after_starts"),
        # The calendar page always asks for one person over one date range.
        Index("ix_calendar_events_user_starts_at", "user_id", "starts_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    action_proposal_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("action_proposals.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="manual", server_default="manual"
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    all_day: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # The zone the user meant, kept beside the instant rather than instead of
    # it: "9am tomorrow" and the UTC moment it resolved to are different facts,
    # and only the first survives them flying somewhere else.
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )
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

    def __repr__(self) -> str:
        return f"<CalendarEvent(id={self.id}, title={self.title}, starts_at={self.starts_at})>"


class Reminder(Base):
    """A single nudge owed to one user at one moment (B-13).

    A row per nudge rather than a column on the event, because an event can owe
    several â€” a day before and again ten minutes before â€” and because
    `delivered_at` is per nudge, not per event.

    The table doubles as the scheduler's queue. `scan_due_reminders` claims rows
    with `remind_at <= now() AND delivered_at IS NULL` in one conditional
    UPDATE, which makes delivery idempotent under a retry and lets a restarted
    process catch up on everything it slept through. That is why the index is on
    exactly those two columns, in that order.
    """

    __tablename__ = "reminders"
    __table_args__ = (
        Index("ix_reminders_due", "remind_at", "delivered_at"),
        Index("ix_reminders_user_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    calendar_event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("calendar_events.id", ondelete="CASCADE"), nullable=False
    )
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<Reminder(id={self.id}, remind_at={self.remind_at}, delivered={self.delivered_at is not None})>"
