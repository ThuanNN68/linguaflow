"""The personal calendar, and the reminders owed against it.

This is where a confirmed proposal stops being a dead end. Before it,
`status = "confirmed"` was a terminal database state with no consumer: the
human approved something and nothing happened.

Entries are owner-scoped, always, on read as well as write — a calendar is the
most personal surface in the product, and a query missing its `user_id` filter
would be a silent disclosure rather than an error.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import ActionProposal, CalendarEvent, Reminder

logger = logging.getLogger(__name__)

# How long before an event the default nudge fires. Fifteen minutes is enough to
# walk to a meeting and short enough that it is still about this event rather
# than a summary of the day.
DEFAULT_REMINDER_LEAD = timedelta(minutes=15)

# What a task with a due time but no duration occupies on the grid. It has to be
# something: a zero-length block is invisible on a day view.
DEFAULT_EVENT_DURATION = timedelta(minutes=30)


class CalendarError(Exception):
    """Base error for calendar operations."""


class CalendarEventNotFoundError(CalendarError):
    """No such entry, or it belongs to somebody else.

    One error for both, deliberately. Distinguishing them would confirm that an
    id exists, which is the only fact the caller did not already supply.
    """


@dataclass(frozen=True, slots=True)
class ScheduledEvent:
    """An entry and the reminders created with it."""

    event: CalendarEvent
    reminders: tuple[Reminder, ...]


class CalendarService:
    """Owner-scoped persistence for calendar entries and their reminders."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_events(
        self,
        *,
        user_id: str,
        starts_after: datetime | None = None,
        starts_before: datetime | None = None,
        include_cancelled: bool = False,
    ) -> list[CalendarEvent]:
        """Return one person's entries, oldest first, within an optional range."""
        statement = select(CalendarEvent).where(CalendarEvent.user_id == user_id)
        if not include_cancelled:
            statement = statement.where(CalendarEvent.status == "active")
        if starts_after is not None:
            statement = statement.where(CalendarEvent.starts_at >= starts_after)
        if starts_before is not None:
            statement = statement.where(CalendarEvent.starts_at < starts_before)
        rows = await self._db.scalars(
            statement.order_by(CalendarEvent.starts_at, CalendarEvent.id)
        )
        return list(rows.all())

    async def get_event(self, *, user_id: str, event_id: str) -> CalendarEvent:
        """Load one entry the caller owns, or raise."""
        event = await self._db.get(CalendarEvent, event_id)
        if event is None or event.user_id != user_id:
            raise CalendarEventNotFoundError(event_id)
        return event

    async def create_event(
        self,
        *,
        user_id: str,
        title: str,
        starts_at: datetime,
        ends_at: datetime | None = None,
        details: str | None = None,
        location: str | None = None,
        all_day: bool = False,
        timezone: str | None = None,
        source: str = "manual",
        action_proposal_id: str | None = None,
        reminder_lead: timedelta | None = DEFAULT_REMINDER_LEAD,
        commit: bool = True,
    ) -> ScheduledEvent:
        """Put an entry on the calendar, with its default reminder.

        Args:
            reminder_lead: How far ahead to nudge, or ``None`` for no reminder.
                A lead that lands in the past is dropped rather than fired
                immediately — a notification about something already under way
                is noise, and for a task entered after the fact it would fire
                the instant it was saved.
            commit: ``False`` to join the caller's transaction. Used by
                `confirm_proposal`, where the status change and the calendar
                entry have to land together or not at all.

        Returns:
            The stored entry and whatever reminders were created with it.
        """
        if ends_at is None and not all_day:
            ends_at = starts_at + DEFAULT_EVENT_DURATION

        event = CalendarEvent(
            user_id=user_id,
            action_proposal_id=action_proposal_id,
            source=source,
            title=title[:255],
            details=details,
            location=location,
            starts_at=starts_at,
            ends_at=ends_at,
            all_day=all_day,
            timezone=timezone,
            status="active",
        )
        self._db.add(event)
        await self._db.flush()

        reminders: list[Reminder] = []
        if reminder_lead is not None:
            remind_at = starts_at - reminder_lead
            if remind_at > datetime.now(UTC):
                reminder = Reminder(
                    user_id=user_id, calendar_event_id=event.id, remind_at=remind_at
                )
                self._db.add(reminder)
                reminders.append(reminder)

        if commit:
            await self._db.commit()
            await self._db.refresh(event)
        return ScheduledEvent(event=event, reminders=tuple(reminders))

    async def update_event(
        self,
        *,
        user_id: str,
        event_id: str,
        changes: dict[str, object],
    ) -> CalendarEvent:
        """Apply explicit fields to one entry the caller owns."""
        event = await self.get_event(user_id=user_id, event_id=event_id)
        for name, value in changes.items():
            setattr(event, name, value)
        await self._db.commit()
        await self._db.refresh(event)
        return event

    async def cancel_event(self, *, user_id: str, event_id: str) -> CalendarEvent:
        """Withdraw an entry without deleting the row.

        A reminder may already have fired for it, and a task approved last week
        explains a calendar the user is looking at now.
        """
        event = await self.get_event(user_id=user_id, event_id=event_id)
        event.status = "cancelled"
        # Undelivered nudges for a cancelled event are pointless; delivered ones
        # already happened and are left alone.
        await self._db.execute(
            update(Reminder)
            .where(
                Reminder.calendar_event_id == event.id,
                Reminder.delivered_at.is_(None),
            )
            .values(dismissed_at=datetime.now(UTC), delivered_at=datetime.now(UTC))
        )
        await self._db.commit()
        await self._db.refresh(event)
        return event

    async def schedule_from_proposal(
        self,
        proposal: ActionProposal,
        *,
        commit: bool = False,
        reminder_lead: timedelta | None = DEFAULT_REMINDER_LEAD,
    ) -> ScheduledEvent | None:
        """Turn a just-confirmed proposal into a calendar entry.

        Returns ``None`` when the proposal carries no time at all. A confirmed
        task with no date is a real thing — "I'll review the doc" with nothing
        said about when — and it belongs in the task inbox rather than on a grid
        at an invented hour.

        Called inside `confirm_proposal`'s transaction so that a confirmation
        cannot succeed while leaving the calendar empty.

        Args:
            reminder_lead: How far ahead to nudge. The person approving chooses
                this, because the message the proposal came from never says it —
                somebody writes "review at ten on Wednesday", not how much
                warning they want. ``None`` creates no reminder.
        """
        starts_at = proposal.scheduled_start_at or proposal.scheduled_time or proposal.due_at
        if starts_at is None:
            return None

        return await self.create_event(
            user_id=proposal.owner_user_id,
            title=proposal.title,
            starts_at=starts_at,
            ends_at=proposal.scheduled_end_at,
            details=proposal.details,
            location=proposal.location,
            timezone=proposal.resolved_timezone,
            source="assistant",
            action_proposal_id=proposal.id,
            reminder_lead=reminder_lead,
            commit=commit,
        )

    async def list_reminders(
        self, *, user_id: str, include_delivered: bool = False
    ) -> list[Reminder]:
        """Return one person's pending nudges, soonest first."""
        statement = select(Reminder).where(
            Reminder.user_id == user_id, Reminder.dismissed_at.is_(None)
        )
        if not include_delivered:
            statement = statement.where(Reminder.delivered_at.is_(None))
        rows = await self._db.scalars(statement.order_by(Reminder.remind_at))
        return list(rows.all())

    async def dismiss_reminder(self, *, user_id: str, reminder_id: str) -> Reminder:
        """Mark one nudge as dealt with."""
        reminder = await self._db.get(Reminder, reminder_id)
        if reminder is None or reminder.user_id != user_id:
            raise CalendarEventNotFoundError(reminder_id)
        reminder.dismissed_at = datetime.now(UTC)
        await self._db.commit()
        await self._db.refresh(reminder)
        return reminder
