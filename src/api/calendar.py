"""Calendar HTTP endpoints."""

import logging
from datetime import datetime, timedelta

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.deps import get_current_user
from src.database import get_db
from src.database.models import (
    User,
)
from src.schemas.calendar import (
    CalendarEventCreateRequest,
    CalendarEventResponse,
    CalendarEventUpdateRequest,
    ReminderResponse,
)
from src.services.calendar.calendar import (
    CalendarEventNotFoundError,
    CalendarService,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/me/calendar/events", response_model=list[CalendarEventResponse])
async def list_calendar_events(
    starts_after: datetime | None = None,
    starts_before: datetime | None = None,
    include_cancelled: bool = False,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[CalendarEventResponse]:
    """Return the caller's calendar over a range (`CONTRACT.md` §3.16)."""
    events = await CalendarService(db).list_events(
        user_id=current_user.id,
        starts_after=starts_after,
        starts_before=starts_before,
        include_cancelled=include_cancelled,
    )
    return [CalendarEventResponse.model_validate(event) for event in events]


@router.post(
    "/me/calendar/events",
    response_model=CalendarEventResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_calendar_event(
    request: CalendarEventCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CalendarEventResponse:
    """Add an entry the caller typed themselves."""
    lead = timedelta(minutes=request.reminder_minutes_before) if request.reminder_minutes_before is not None else None
    scheduled = await CalendarService(db).create_event(
        user_id=current_user.id,
        title=request.title,
        starts_at=request.starts_at,
        ends_at=request.ends_at,
        details=request.details,
        location=request.location,
        all_day=request.all_day,
        timezone=request.timezone,
        source="manual",
        reminder_lead=lead,
    )
    return CalendarEventResponse.model_validate(scheduled.event)


@router.patch("/me/calendar/events/{event_id}", response_model=CalendarEventResponse)
async def update_calendar_event(
    event_id: str,
    request: CalendarEventUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CalendarEventResponse:
    """Change an entry the caller owns and did not import from Google."""
    try:
        event = await CalendarService(db).update_event(
            user_id=current_user.id,
            event_id=event_id,
            changes=request.model_dump(exclude_unset=True),
        )
    except CalendarEventNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Calendar entry was not found") from exc
    return CalendarEventResponse.model_validate(event)


@router.delete("/me/calendar/events/{event_id}", response_model=CalendarEventResponse)
async def cancel_calendar_event(
    event_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CalendarEventResponse:
    """Withdraw an entry. The row stays; a reminder may already have fired."""
    try:
        event = await CalendarService(db).cancel_event(user_id=current_user.id, event_id=event_id)
    except CalendarEventNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Calendar entry was not found") from exc
    return CalendarEventResponse.model_validate(event)


@router.get("/me/reminders", response_model=list[ReminderResponse])
async def list_reminders(
    include_delivered: bool = False,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ReminderResponse]:
    """Return the caller's pending nudges, soonest first."""
    reminders = await CalendarService(db).list_reminders(user_id=current_user.id, include_delivered=include_delivered)
    return [ReminderResponse.model_validate(reminder) for reminder in reminders]


@router.post("/me/reminders/{reminder_id}/dismiss", response_model=ReminderResponse)
async def dismiss_reminder(
    reminder_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReminderResponse:
    """Mark one nudge as dealt with."""
    try:
        reminder = await CalendarService(db).dismiss_reminder(user_id=current_user.id, reminder_id=reminder_id)
    except CalendarEventNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reminder was not found") from exc
    return ReminderResponse.model_validate(reminder)
