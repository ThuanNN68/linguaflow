"""Tests for Mandatory HITL confirmation, rejection, listing endpoints, and stale transitions (B-05 / Batch K)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.security import create_access_token, get_password_hash
from src.database.models import ActionProposal, Conversation, ConversationMember, Message, User
from src.services.intelligence.action_proposals import ActionProposalService, compute_proposal_idempotency_key
from src.services.messaging.chat import ChatService


@pytest_asyncio.fixture
async def hitl_setup(test_db: AsyncSession):
    """Create test conversation, users, messages, and proposals for HITL confirmation testing."""
    owner = User(
        email="owner_hitl@example.com",
        username="owner_hitl",
        display_name="Owner User",
        password_hash=get_password_hash("pass123"),
        role="member",
        preferred_language="en",
    )
    colleague = User(
        email="colleague_hitl@example.com",
        username="colleague_hitl",
        display_name="Colleague User",
        password_hash=get_password_hash("pass123"),
        role="member",
        preferred_language="vi",
    )
    outsider = User(
        email="outsider_hitl@example.com",
        username="outsider_hitl",
        display_name="Outsider User",
        password_hash=get_password_hash("pass123"),
        role="member",
        preferred_language="ja",
    )
    test_db.add_all([owner, colleague, outsider])
    await test_db.flush()

    conv = Conversation(type="direct", created_by=owner.id)
    test_db.add(conv)
    await test_db.flush()

    test_db.add_all([
        ConversationMember(conversation_id=conv.id, user_id=owner.id),
        ConversationMember(conversation_id=conv.id, user_id=colleague.id),
    ])

    msg = Message(
        conversation_id=conv.id,
        sender_id=colleague.id,
        client_message_id="c-msg-hitl",
        original_text="Owner, please submit the compliance report tomorrow by 4 PM.",
        source_language="en",
    )
    test_db.add(msg)
    await test_db.flush()

    # Create pending-confirmation proposal owned by owner.
    key1 = compute_proposal_idempotency_key(
        owner_user_id=owner.id,
        source_message_id=msg.id,
        action_type="task",
        title="Submit compliance report",
        scheduled_time=datetime(2026, 8, 22, 16, 0, 0, tzinfo=UTC),
    )
    p_pending = ActionProposal(
        conversation_id=conv.id,
        source_message_id=msg.id,
        owner_user_id=owner.id,
        action_type="task",
        status="pending_confirmation",
        title="Submit compliance report",
        details="Submit compliance report tomorrow by 4 PM",
        scheduled_time=datetime(2026, 8, 22, 16, 0, 0, tzinfo=UTC),
        confidence_score=0.9,
        idempotency_key=key1,
    )

    # Create already confirmed proposal
    key2 = compute_proposal_idempotency_key(
        owner_user_id=owner.id,
        source_message_id=msg.id,
        action_type="appointment",
        title="Pre-meeting Sync",
        scheduled_time=datetime(2026, 8, 22, 10, 0, 0, tzinfo=UTC),
    )
    p_confirmed = ActionProposal(
        conversation_id=conv.id,
        source_message_id=msg.id,
        owner_user_id=owner.id,
        action_type="appointment",
        status="confirmed",
        title="Pre-meeting Sync",
        scheduled_time=datetime(2026, 8, 22, 10, 0, 0, tzinfo=UTC),
        confidence_score=1.0,
        idempotency_key=key2,
        confirmed_at=datetime.now(UTC),
    )

    test_db.add_all([p_pending, p_confirmed])
    await test_db.commit()

    return {
        "owner": owner,
        "colleague": colleague,
        "outsider": outsider,
        "conv": conv,
        "msg": msg,
        "p_pending": p_pending,
        "p_confirmed": p_confirmed,
        "p_pending_id": p_pending.id,
        "p_confirmed_id": p_confirmed.id,
        "msg_id": msg.id,
        "conv_id": conv.id,
    }


@pytest.mark.asyncio
async def test_list_proposals_endpoint_all_and_filtered(client: AsyncClient, hitl_setup):
    owner = hitl_setup["owner"]
    token = create_access_token(subject=owner.id)

    # List all
    res_all = await client.get(
        "/api/v1/me/action-proposals",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_all.status_code == 200
    assert len(res_all.json()) == 2

    # Filter status=pending_confirmation
    res_pending = await client.get(
        "/api/v1/me/action-proposals?status=pending_confirmation",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_pending.status_code == 200
    assert len(res_pending.json()) == 1
    assert res_pending.json()[0]["status"] == "pending_confirmation"

    # Filter status=confirmed
    res_confirmed = await client.get(
        "/api/v1/me/action-proposals?status=confirmed",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_confirmed.status_code == 200
    assert len(res_confirmed.json()) == 1
    assert res_confirmed.json()[0]["status"] == "confirmed"


@pytest.mark.asyncio
async def test_list_proposals_includes_sender_and_group_source_context(
    client: AsyncClient, test_db: AsyncSession, hitl_setup
):
    conversation = hitl_setup["conv"]
    conversation.type = "group"
    conversation.title = "Nhóm tuân thủ"
    await test_db.commit()

    token = create_access_token(subject=hitl_setup["owner"].id)
    response = await client.get(
        "/api/v1/me/action-proposals",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    proposal = response.json()[0]
    assert proposal["source_sender_name"] == "Colleague User"
    assert proposal["source_conversation_type"] == "group"
    assert proposal["source_conversation_name"] == "Nhóm tuân thủ"


@pytest.mark.asyncio
async def test_list_proposals_endpoint_is_owner_scoped(client: AsyncClient, hitl_setup):
    outsider = hitl_setup["outsider"]
    token = create_access_token(subject=outsider.id)

    res = await client.get(
        "/api/v1/me/action-proposals",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    assert res.json() == []


@pytest.mark.asyncio
async def test_confirm_proposal_owner_success_200(client: AsyncClient, test_db: AsyncSession, hitl_setup):
    owner = hitl_setup["owner"]
    p_pending_id = hitl_setup["p_pending_id"]
    token = create_access_token(subject=owner.id)

    res = await client.post(
        f"/api/v1/action-proposals/{p_pending_id}/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "confirmed"
    assert data["confirmed_at"] is not None

    # Reload from DB
    test_db.expire_all()
    prop = await ActionProposalService(test_db).get_proposal(p_pending_id)
    assert prop.status == "confirmed"


@pytest.mark.asyncio
async def test_confirmation_applies_allowed_corrections_in_same_transition(client: AsyncClient, test_db: AsyncSession, hitl_setup):
    owner = hitl_setup["owner"]
    owner_id = owner.id
    proposal_id = hitl_setup["p_pending_id"]
    response = await client.post(
        f"/api/v1/action-proposals/{proposal_id}/confirm",
        json={
            "title": "Corrected compliance report",
            "location": "Finance portal",
            "scheduled_start_at": "2026-08-22T16:30:00Z",
        },
        headers={"Authorization": f"Bearer {create_access_token(subject=owner_id)}"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "confirmed"
    assert payload["title"] == "Corrected compliance report"
    assert payload["location"] == "Finance portal"
    assert payload["scheduled_start_at"] == "2026-08-22T16:30:00Z"
    test_db.expire_all()
    proposal = await ActionProposalService(test_db).get_proposal(proposal_id)
    assert proposal.confirmed_by_user_id == owner_id


@pytest.mark.asyncio
async def test_confirm_proposal_non_owner_403(client: AsyncClient, hitl_setup):
    colleague = hitl_setup["colleague"]
    p_pending_id = hitl_setup["p_pending_id"]
    token = create_access_token(subject=colleague.id)

    res = await client.post(
        f"/api/v1/action-proposals/{p_pending_id}/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_confirm_proposal_nonexistent_404(client: AsyncClient, hitl_setup):
    owner = hitl_setup["owner"]
    token = create_access_token(subject=owner.id)

    res = await client.post(
        "/api/v1/action-proposals/00000000-0000-0000-0000-000000000000/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_confirm_proposal_terminal_state_409(client: AsyncClient, hitl_setup):
    owner = hitl_setup["owner"]
    p_confirmed_id = hitl_setup["p_confirmed_id"]
    token = create_access_token(subject=owner.id)

    res = await client.post(
        f"/api/v1/action-proposals/{p_confirmed_id}/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_reject_proposal_owner_success_200(client: AsyncClient, test_db: AsyncSession, hitl_setup):
    owner = hitl_setup["owner"]
    p_pending_id = hitl_setup["p_pending_id"]
    token = create_access_token(subject=owner.id)

    res = await client.post(
        f"/api/v1/action-proposals/{p_pending_id}/reject",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "rejected"
    assert data["rejected_at"] is not None

    test_db.expire_all()
    prop = await ActionProposalService(test_db).get_proposal(p_pending_id)
    assert prop.status == "rejected"


@pytest.mark.asyncio
async def test_reject_proposal_non_owner_403(client: AsyncClient, hitl_setup):
    colleague = hitl_setup["colleague"]
    p_pending_id = hitl_setup["p_pending_id"]
    token = create_access_token(subject=colleague.id)

    res = await client.post(
        f"/api/v1/action-proposals/{p_pending_id}/reject",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_reject_proposal_terminal_state_409(client: AsyncClient, hitl_setup):
    owner = hitl_setup["owner"]
    p_confirmed_id = hitl_setup["p_confirmed_id"]
    token = create_access_token(subject=owner.id)

    res = await client.post(
        f"/api/v1/action-proposals/{p_confirmed_id}/reject",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_rejected_and_stale_proposals_cannot_be_confirmed(client: AsyncClient, test_db: AsyncSession, hitl_setup):
    owner = hitl_setup["owner"]
    proposal_id = hitl_setup["p_pending_id"]
    token = create_access_token(subject=owner.id)
    rejected = await client.post(
        f"/api/v1/action-proposals/{proposal_id}/reject",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert rejected.status_code == 200
    assert (
        await client.post(
            f"/api/v1/action-proposals/{proposal_id}/confirm",
            headers={"Authorization": f"Bearer {token}"},
        )
    ).status_code == 409

    stale = hitl_setup["p_confirmed"]
    stale_id = stale.id
    stale.status = "stale"
    stale.confirmed_at = None
    await test_db.commit()
    assert (
        await client.post(
            f"/api/v1/action-proposals/{stale_id}/confirm",
            headers={"Authorization": f"Bearer {token}"},
        )
    ).status_code == 409


@pytest.mark.asyncio
async def test_edit_message_stale_auto_transition(client: AsyncClient, test_db: AsyncSession, hitl_setup):
    colleague = hitl_setup["colleague"]
    conv_id = hitl_setup["conv_id"]
    msg_id = hitl_setup["msg_id"]
    p_pending_id = hitl_setup["p_pending_id"]
    p_confirmed_id = hitl_setup["p_confirmed_id"]
    token = create_access_token(subject=colleague.id)

    # Edit the source message
    res = await client.patch(
        f"/api/v1/conversations/{conv_id}/messages/{msg_id}",
        json={"text": "Nevermind, no report is needed."},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200

    # Verify p_pending became stale
    test_db.expire_all()
    reloaded_pending = await ActionProposalService(test_db).get_proposal(p_pending_id)
    assert reloaded_pending.status == "stale"
    assert reloaded_pending.stale_at is not None

    # Verify p_confirmed stayed confirmed
    reloaded_confirmed = await ActionProposalService(test_db).get_proposal(p_confirmed_id)
    assert reloaded_confirmed.status == "confirmed"


@pytest.mark.asyncio
async def test_delete_message_stale_auto_transition(client: AsyncClient, test_db: AsyncSession, hitl_setup):
    colleague = hitl_setup["colleague"]
    conv_id = hitl_setup["conv_id"]
    msg_id = hitl_setup["msg_id"]
    p_pending_id = hitl_setup["p_pending_id"]
    token = create_access_token(subject=colleague.id)

    # Delete the source message
    res = await client.delete(
        f"/api/v1/conversations/{conv_id}/messages/{msg_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 204

    # Verify p_pending became stale
    test_db.expire_all()
    reloaded_pending = await ActionProposalService(test_db).get_proposal(p_pending_id)
    assert reloaded_pending.status == "stale"
    assert reloaded_pending.stale_at is not None


@pytest.mark.asyncio
async def test_edit_staleness_failure_rolls_back_message_mutation(test_db: AsyncSession, hitl_setup, monkeypatch):
    """The source text and proposal invalidation share one transaction."""
    colleague = hitl_setup["colleague"]
    message = hitl_setup["msg"]
    message_id = message.id
    original_text = message.original_text
    original_execute = test_db.execute

    async def fail_stale_update(statement, *args, **kwargs):
        table = getattr(getattr(statement, "table", None), "name", None)
        if table == "action_proposals":
            raise RuntimeError("simulated proposal invalidation failure")
        return await original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(test_db, "execute", fail_stale_update)
    with pytest.raises(RuntimeError, match="invalidation"):
        await ChatService(test_db).edit_message(
            user_id=colleague.id,
            conversation_id=hitl_setup["conv_id"],
        message_id=message_id,
            text="This edit must roll back.",
        )
    await test_db.rollback()
    monkeypatch.setattr(test_db, "execute", original_execute)
    test_db.expire_all()
    reloaded = await test_db.get(Message, message_id)
    assert reloaded is not None
    assert reloaded.original_text == original_text


@pytest.mark.asyncio
async def test_concurrent_confirms_have_exactly_one_transition(test_db: AsyncSession, hitl_setup):
    """Conditional UPDATE gives the losing PostgreSQL transaction a stable conflict."""
    owner = hitl_setup["owner"]
    owner_id = owner.id
    proposal_id = hitl_setup["p_pending_id"]
    maker = async_sessionmaker(test_db.bind, class_=AsyncSession, expire_on_commit=False)

    async def confirm_once():
        async with maker() as session:
            return await ActionProposalService(session).confirm_proposal(proposal_id, owner_id)

    results = await asyncio.gather(confirm_once(), confirm_once(), return_exceptions=True)
    assert sum(getattr(result, "status", None) == "confirmed" for result in results) == 1
    assert sum(isinstance(result, Exception) for result in results) == 1
    test_db.expire_all()
    proposal = await ActionProposalService(test_db).get_proposal(proposal_id)
    assert proposal.status == "confirmed"
    assert proposal.confirmed_by_user_id == owner_id


@pytest.mark.asyncio
async def test_confirming_uses_the_lead_time_the_approver_chose(
    client: AsyncClient, test_db: AsyncSession, hitl_setup
):
    """Approval is where a person supplies what the message never contained.

    Somebody writes "review thiết kế 10h sáng thứ Tư"; nobody writes how much
    warning they want. So the lead time is asked for at the gate rather than
    guessed from the text — which is also the only place a human is looking at
    the proposal and able to answer.
    """
    from src.database.models import CalendarEvent, Reminder

    owner = hitl_setup["owner"]
    proposal_id = hitl_setup["p_pending_id"]

    response = await client.post(
        f"/api/v1/action-proposals/{proposal_id}/confirm",
        json={
            "scheduled_start_at": "2099-09-10T09:00:00Z",
            "reminder_minutes_before": 120,
        },
        headers={"Authorization": f"Bearer {create_access_token(subject=owner.id)}"},
    )
    assert response.status_code == 200

    test_db.expire_all()
    event = await test_db.scalar(
        select(CalendarEvent).where(CalendarEvent.action_proposal_id == proposal_id)
    )
    assert event is not None
    reminder = await test_db.scalar(
        select(Reminder).where(Reminder.calendar_event_id == event.id)
    )
    assert reminder is not None
    assert event.starts_at - reminder.remind_at == timedelta(minutes=120)


@pytest.mark.asyncio
async def test_confirming_with_a_null_lead_time_creates_no_reminder(
    client: AsyncClient, test_db: AsyncSession, hitl_setup
):
    """`null` is a real answer — "do not nudge me" — not a missing value.

    Same meaning the field already has on `POST /me/calendar/events`, because a
    product where the reminder field means one thing on one screen and another
    on the next is one nobody can hold in their head.
    """
    from src.database.models import CalendarEvent, Reminder

    owner = hitl_setup["owner"]
    proposal_id = hitl_setup["p_pending_id"]

    response = await client.post(
        f"/api/v1/action-proposals/{proposal_id}/confirm",
        json={
            "scheduled_start_at": "2099-09-10T09:00:00Z",
            "reminder_minutes_before": None,
        },
        headers={"Authorization": f"Bearer {create_access_token(subject=owner.id)}"},
    )
    assert response.status_code == 200

    test_db.expire_all()
    event = await test_db.scalar(
        select(CalendarEvent).where(CalendarEvent.action_proposal_id == proposal_id)
    )
    assert event is not None
    assert (
        await test_db.scalar(
            select(Reminder).where(Reminder.calendar_event_id == event.id)
        )
    ) is None


@pytest.mark.asyncio
async def test_confirming_without_naming_a_lead_time_keeps_the_default(
    client: AsyncClient, test_db: AsyncSession, hitl_setup
):
    from src.database.models import CalendarEvent, Reminder
    from src.services.calendar.calendar import DEFAULT_REMINDER_LEAD

    owner = hitl_setup["owner"]
    proposal_id = hitl_setup["p_pending_id"]

    response = await client.post(
        f"/api/v1/action-proposals/{proposal_id}/confirm",
        json={"scheduled_start_at": "2099-09-10T09:00:00Z"},
        headers={"Authorization": f"Bearer {create_access_token(subject=owner.id)}"},
    )
    assert response.status_code == 200

    test_db.expire_all()
    event = await test_db.scalar(
        select(CalendarEvent).where(CalendarEvent.action_proposal_id == proposal_id)
    )
    reminder = await test_db.scalar(
        select(Reminder).where(Reminder.calendar_event_id == event.id)
    )
    assert reminder is not None
    assert event.starts_at - reminder.remind_at == DEFAULT_REMINDER_LEAD


@pytest.mark.asyncio
async def test_the_lead_time_is_not_written_onto_the_proposal_row(
    client: AsyncClient, test_db: AsyncSession, hitl_setup
):
    """It shapes the calendar entry, not the proposal.

    Left inside `corrections` it would be dropped by the allowlist in
    `confirm_proposal` and the person's choice would vanish with no error — the
    reason it travels as its own argument.
    """
    owner = hitl_setup["owner"]
    proposal_id = hitl_setup["p_pending_id"]

    response = await client.post(
        f"/api/v1/action-proposals/{proposal_id}/confirm",
        json={
            "scheduled_start_at": "2099-09-10T09:00:00Z",
            "reminder_minutes_before": 45,
        },
        headers={"Authorization": f"Bearer {create_access_token(subject=owner.id)}"},
    )

    assert response.status_code == 200
    assert "reminder_minutes_before" not in response.json()
