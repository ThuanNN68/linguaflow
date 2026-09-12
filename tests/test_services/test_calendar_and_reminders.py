"""Tests for what a confirmed proposal becomes, and for the reminder queue.

Two assertions carry most of the weight. A confirmation and its calendar entry
land in one transaction, so a successful approval can never leave the calendar
empty. And the scheduler's claim is idempotent, so running the scan twice
delivers once — the property that lets a restarted process catch up without
re-notifying everyone about everything it slept through.

Fixtures live in this file rather than tests/conftest.py, which is shared across
all feature areas.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import ActionProposal, CalendarEvent, Message, Reminder
from src.services.calendar.calendar import (
    CalendarEventNotFoundError,
    CalendarService,
)
from src.services.calendar.reminder_scheduler import scan_due_reminders
from src.services.messaging.chat import ChatService


class Recorder:
    """Stands in for `ConnectionManager` and remembers what it was sent."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def send_to_user(self, user_id: str, event: dict) -> None:
        self.events.append((user_id, event))


@pytest_asyncio.fixture
async def proposal(test_db: AsyncSession, test_user, conversation_factory) -> ActionProposal:
    """One confirmable proposal carrying a time."""
    conversation = await conversation_factory(test_user, [test_user], conversation_type="group")
    message = Message(
        client_message_id=f"m-{uuid.uuid4().hex[:8]}",
        conversation_id=conversation.id,
        sender_id=test_user.id,
        original_text="Tôi sẽ gửi báo cáo sáng mai lúc 9h",
        source_language="vi",
    )
    test_db.add(message)
    await test_db.flush()

    row = ActionProposal(
        conversation_id=conversation.id,
        source_message_id=message.id,
        owner_user_id=test_user.id,
        action_type="task",
        status="pending_confirmation",
        title="Gửi báo cáo",
        scheduled_start_at=datetime.now(UTC) + timedelta(days=1),
        resolved_timezone="Asia/Ho_Chi_Minh",
        missing_fields="[]",
        idempotency_key=uuid.uuid4().hex,
    )
    test_db.add(row)
    await test_db.commit()
    await test_db.refresh(row)
    return row


@pytest.mark.asyncio
async def test_confirming_a_timed_proposal_puts_it_on_the_calendar(test_db: AsyncSession, test_user, proposal) -> None:
    """The gap this phase closes: `confirmed` used to lead nowhere."""
    from src.services.intelligence.action_proposals import ActionProposalService

    await ActionProposalService(test_db).confirm_proposal(proposal.id, test_user.id)

    event = await test_db.scalar(select(CalendarEvent).where(CalendarEvent.action_proposal_id == proposal.id))
    assert event is not None
    assert event.title == "Gửi báo cáo"
    assert event.source == "assistant"
    assert event.user_id == test_user.id


@pytest.mark.asyncio
async def test_confirming_also_creates_the_reminder_owed_against_it(test_db: AsyncSession, test_user, proposal) -> None:
    """An entry with no nudge is a calendar the user has to remember to read."""
    from src.services.intelligence.action_proposals import ActionProposalService

    await ActionProposalService(test_db).confirm_proposal(proposal.id, test_user.id)

    reminder = await test_db.scalar(select(Reminder).where(Reminder.user_id == test_user.id))
    assert reminder is not None
    assert reminder.delivered_at is None


@pytest.mark.asyncio
async def test_a_confirmed_proposal_with_no_time_stays_off_the_calendar(
    test_db: AsyncSession, test_user, proposal
) -> None:
    """ "I'll review the doc" with no date belongs in the inbox, not on a grid."""
    from src.services.intelligence.action_proposals import ActionProposalService

    proposal.scheduled_start_at = None
    proposal.scheduled_time = None
    proposal.due_at = None
    await test_db.commit()

    await ActionProposalService(test_db).confirm_proposal(proposal.id, test_user.id)

    event = await test_db.scalar(select(CalendarEvent).where(CalendarEvent.action_proposal_id == proposal.id))
    assert event is None


@pytest.mark.asyncio
async def test_scanning_twice_delivers_a_due_reminder_only_once(test_db: AsyncSession, test_user) -> None:
    """The claim is what makes a restart safe to catch up from."""
    import tests.conftest as conftest_module

    service = CalendarService(test_db)
    scheduled = await service.create_event(
        user_id=test_user.id,
        title="Họp nhóm",
        starts_at=datetime.now(UTC) + timedelta(hours=1),
        reminder_lead=None,
    )
    overdue = Reminder(
        user_id=test_user.id,
        calendar_event_id=scheduled.event.id,
        remind_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    test_db.add(overdue)
    await test_db.commit()

    recorder = Recorder()
    first = await scan_due_reminders(recorder, session_factory=conftest_module.test_async_session_maker)
    second = await scan_due_reminders(recorder, session_factory=conftest_module.test_async_session_maker)

    assert (first, second) == (1, 0)
    assert len(recorder.events) == 1
    assert recorder.events[0][0] == test_user.id
    assert recorder.events[0][1]["type"] == "reminder_due"
    assert recorder.events[0][1]["reminder"]["title"] == "Họp nhóm"


@pytest.mark.asyncio
async def test_failed_durable_reminder_delivery_is_left_retryable(
    test_db: AsyncSession, test_user, monkeypatch
) -> None:
    """A failed Assistant-thread write must not be recorded as delivered."""
    import tests.conftest as conftest_module

    scheduled = await CalendarService(test_db).create_event(
        user_id=test_user.id,
        title="Họp cần thử lại",
        starts_at=datetime.now(UTC) + timedelta(hours=1),
        reminder_lead=None,
    )
    reminder = Reminder(
        user_id=test_user.id,
        calendar_event_id=scheduled.event.id,
        remind_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    test_db.add(reminder)
    await test_db.commit()
    reminder_id = reminder.id
    monkeypatch.setattr(ChatService, "post_assistant_notice", AsyncMock(return_value=None))

    claimed = await scan_due_reminders(
        Recorder(),
        session_factory=conftest_module.test_async_session_maker,
        now=datetime.now(UTC),
    )

    await test_db.rollback()
    stored = await test_db.get(Reminder, reminder_id)
    await test_db.refresh(stored)
    assert claimed == 1
    assert stored.delivered_at is None
    assert stored.claimed_at is None
    assert stored.attempts == 1
    assert stored.next_attempt_at is not None
    assert stored.last_error == "assistant_notice_failed"


@pytest.mark.asyncio
async def test_a_reminder_that_is_not_due_yet_is_left_alone(test_db: AsyncSession, test_user) -> None:
    """Otherwise every scan would fire everything on the calendar."""
    import tests.conftest as conftest_module

    scheduled = await CalendarService(test_db).create_event(
        user_id=test_user.id,
        title="Sau này",
        starts_at=datetime.now(UTC) + timedelta(days=3),
    )
    assert scheduled.reminders

    recorder = Recorder()
    claimed = await scan_due_reminders(recorder, session_factory=conftest_module.test_async_session_maker)

    assert claimed == 0
    assert recorder.events == []


@pytest.mark.asyncio
async def test_a_lead_time_already_in_the_past_creates_no_reminder(test_db: AsyncSession, test_user) -> None:
    """Firing the instant an entry is saved is noise, not a reminder."""
    scheduled = await CalendarService(test_db).create_event(
        user_id=test_user.id,
        title="Bắt đầu trong 5 phút",
        starts_at=datetime.now(UTC) + timedelta(minutes=5),
        reminder_lead=timedelta(minutes=15),
    )

    assert scheduled.reminders == ()


@pytest.mark.asyncio
async def test_cancelling_an_entry_silences_its_undelivered_reminders(test_db: AsyncSession, test_user) -> None:
    """A nudge about a meeting that is off is worse than no nudge."""
    import tests.conftest as conftest_module

    scheduled = await CalendarService(test_db).create_event(
        user_id=test_user.id,
        title="Sẽ huỷ",
        starts_at=datetime.now(UTC) + timedelta(hours=2),
    )
    await CalendarService(test_db).cancel_event(user_id=test_user.id, event_id=scheduled.event.id)

    recorder = Recorder()
    await scan_due_reminders(recorder, session_factory=conftest_module.test_async_session_maker)

    assert recorder.events == []


@pytest.mark.asyncio
async def test_the_calendar_never_returns_another_persons_entry(
    test_db: AsyncSession, test_user, test_user_two
) -> None:
    """A calendar is the most personal surface here; a missing filter is silent."""
    await CalendarService(test_db).create_event(
        user_id=test_user.id,
        title="Riêng tư",
        starts_at=datetime.now(UTC) + timedelta(hours=1),
    )

    events = await CalendarService(test_db).list_events(user_id=test_user_two.id)
    assert events == []


@pytest.mark.asyncio
async def test_reaching_another_persons_entry_by_id_reads_as_not_found(
    test_db: AsyncSession, test_user, test_user_two
) -> None:
    """Distinguishing "not yours" from "no such id" would confirm the id exists."""
    scheduled = await CalendarService(test_db).create_event(
        user_id=test_user.id,
        title="Riêng tư",
        starts_at=datetime.now(UTC) + timedelta(hours=1),
    )

    with pytest.raises(CalendarEventNotFoundError):
        await CalendarService(test_db).get_event(user_id=test_user_two.id, event_id=scheduled.event.id)
