"""Tests for candidate action extraction REST API (B-04 / Batch J2)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import create_access_token, get_password_hash
from src.database.models import ActionProposal, Conversation, ConversationMember, Message, User
from src.services.assistant.agent_consent import set_consents
from src.services.intelligence.conversation_intelligence import ConversationIntelligenceService


@pytest_asyncio.fixture
async def extraction_setup(test_db: AsyncSession):
    """Create test conversation with members and messages for extraction tests."""
    alice = User(
        email="alice_extract@example.com",
        username="alice_ex",
        display_name="Alice Extract",
        password_hash=get_password_hash("pass123"),
        role="member",
        preferred_language="en",
    )
    bob = User(
        email="bob_extract@example.com",
        username="bob_ex",
        display_name="Bob Extract",
        password_hash=get_password_hash("pass123"),
        role="member",
        preferred_language="vi",
    )
    outsider = User(
        email="eve_extract@example.com",
        username="eve_ex",
        display_name="Eve Extract",
        password_hash=get_password_hash("pass123"),
        role="member",
        preferred_language="fr",
    )
    test_db.add_all([alice, bob, outsider])
    await test_db.flush()

    conv = Conversation(type="direct", created_by=alice.id)
    test_db.add(conv)
    await test_db.flush()

    test_db.add_all([
        ConversationMember(conversation_id=conv.id, user_id=alice.id),
        ConversationMember(conversation_id=conv.id, user_id=bob.id),
    ])

    m_task = Message(
        conversation_id=conv.id,
        sender_id=alice.id,
        client_message_id="c-msg-task",
        original_text="Bob, please deploy the backend server by tomorrow at 5 PM.",
        source_language="en",
    )
    m_appt = Message(
        conversation_id=conv.id,
        sender_id=bob.id,
        client_message_id="c-msg-appt",
        original_text="Let's have a sprint demo meeting on Friday at 10 AM.",
        source_language="en",
    )
    m_casual = Message(
        conversation_id=conv.id,
        sender_id=alice.id,
        client_message_id="c-msg-casual",
        original_text="Good morning! How are you doing today?",
        source_language="en",
    )
    test_db.add_all([m_task, m_appt, m_casual])
    await test_db.commit()

    # Granted to all three, the outsider included: these tests measure
    # membership, ownership and extraction quality. Without consent the 403 for
    # a non-member would be indistinguishable from a 403 for a missing
    # permission, and several would pass for the wrong reason (ADR-30).
    for account in (alice, bob, outsider):
        await set_consents(test_db, account.id, {"read_conversations": True})

    return {
        "alice": alice,
        "bob": bob,
        "outsider": outsider,
        "conv": conv,
        "m_task": m_task,
        "m_appt": m_appt,
        "m_casual": m_casual,
    }


@pytest.mark.asyncio
async def test_extract_actions_task_success(client: AsyncClient, test_db: AsyncSession, extraction_setup):
    bob = extraction_setup["bob"]
    conv = extraction_setup["conv"]
    m_task = extraction_setup["m_task"]
    # Bob is the explicit assignee.  Alice cannot create Bob's private task
    # from her own owner-scoped extraction request.
    token = create_access_token(subject=bob.id)

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=MagicMock(
            content=f'''{{
  "candidates": [
    {{
      "owner_user_id": "{bob.id}",
      "action_type": "task",
      "title": "Deploy the backend server",
      "details": "Deploy backend server by tomorrow at 5 PM",
      "scheduled_time": "2026-08-22T17:00:00Z",
      "confidence_score": 0.95,
      "clarification_prompt": null
    }}
  ]
}}'''
        )
    )

    with patch("src.agents.conversation_intelligence.action_graph.get_llm", return_value=mock_llm):
        response = await client.post(
            f"/api/v1/conversations/{conv.id}/messages/{m_task.id}/extract-actions",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    proposals = response.json()
    assert len(proposals) == 1
    p = proposals[0]
    assert p["action_type"] == "task"
    assert p["owner_user_id"] == bob.id
    assert p["status"] == "pending_confirmation"
    assert "backend" in p["title"].lower()
    assert p["source_message_id"] == m_task.id
    assert p["conversation_id"] == conv.id

    # Verify database persistence
    db_props = await test_db.execute(select(ActionProposal).where(ActionProposal.id == p["id"]))
    saved = db_props.scalar_one_or_none()
    assert saved is not None
    assert saved.status == "pending_confirmation"


@pytest.mark.asyncio
async def test_extract_actions_appointment_success(client: AsyncClient, extraction_setup):
    bob = extraction_setup["bob"]
    conv = extraction_setup["conv"]
    m_appt = extraction_setup["m_appt"]
    token = create_access_token(subject=bob.id)

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=MagicMock(
            content=f'''{{
  "candidates": [
    {{
      "owner_user_id": "{bob.id}",
      "action_type": "appointment",
      "title": "Sprint demo meeting",
      "details": "Demo meeting on Friday",
      "scheduled_time": "2026-08-22T10:00:00Z",
      "relationship": "requester_appointment",
      "confidence_score": 0.9,
      "clarification_prompt": null
    }}
  ]
}}'''
        )
    )

    with patch("src.agents.conversation_intelligence.action_graph.get_llm", return_value=mock_llm):
        response = await client.post(
            f"/api/v1/conversations/{conv.id}/messages/{m_appt.id}/extract-actions",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    proposals = response.json()
    assert len(proposals) == 1
    assert proposals[0]["action_type"] == "appointment"
    assert proposals[0]["scheduled_time"] is not None


@pytest.mark.asyncio
async def test_extract_actions_non_actionable_returns_empty(client: AsyncClient, extraction_setup):
    alice = extraction_setup["alice"]
    conv = extraction_setup["conv"]
    m_casual = extraction_setup["m_casual"]
    token = create_access_token(subject=alice.id)

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=MagicMock(
            content='''{
  "candidates": []
}'''
        )
    )

    with patch("src.agents.conversation_intelligence.action_graph.get_llm", return_value=mock_llm):
        response = await client.post(
            f"/api/v1/conversations/{conv.id}/messages/{m_casual.id}/extract-actions",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_recent_empty_scan_posts_a_private_durable_assistant_notice(
    client: AsyncClient,
    test_db: AsyncSession,
    extraction_setup,
    monkeypatch,
):
    alice = extraction_setup["alice"]
    conversation = extraction_setup["conv"]
    alice_id = alice.id
    conversation_id = conversation.id
    alice.interface_language = "vi"
    await test_db.commit()

    async def no_actions(self, conversation_id, user_id, days, db, hours=None):
        return []

    monkeypatch.setattr(
        ConversationIntelligenceService,
        "extract_actions_from_recent_messages",
        no_actions,
    )
    response = await client.post(
        f"/api/v1/conversations/{conversation_id}/extract-actions/recent?days=7",
        headers={"Authorization": f"Bearer {create_access_token(subject=alice_id)}"},
    )

    assert response.status_code == 200
    assert response.json() == []
    test_db.expire_all()
    notice = await test_db.scalar(
        select(Message).where(
            Message.conversation_id == conversation_id,
            Message.client_message_id.like("assistant:appointment-scan-empty:%"),
        )
    )
    assert notice is not None
    assert notice.assistant_generated is True
    assert notice.visibility == "private"
    assert notice.visible_to_user_id == alice_id
    assert "không tìm thấy lịch hẹn hoặc cuộc họp" in notice.original_text


@pytest.mark.asyncio
async def test_extract_actions_deleted_message_returns_empty_without_llm(client: AsyncClient, test_db: AsyncSession, extraction_setup):
    alice = extraction_setup["alice"]
    conv = extraction_setup["conv"]
    m_task = extraction_setup["m_task"]

    m_task.deleted_at = datetime.now(UTC)
    m_task.original_text = ""
    await test_db.commit()

    token = create_access_token(subject=alice.id)

    with patch("src.agents.conversation_intelligence.action_graph.get_llm") as mock_get_llm:
        response = await client.post(
            f"/api/v1/conversations/{conv.id}/messages/{m_task.id}/extract-actions",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert not mock_get_llm.called

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_extract_actions_non_member_403(client: AsyncClient, extraction_setup):
    outsider = extraction_setup["outsider"]
    conv = extraction_setup["conv"]
    m_task = extraction_setup["m_task"]
    token = create_access_token(subject=outsider.id)

    response = await client.post(
        f"/api/v1/conversations/{conv.id}/messages/{m_task.id}/extract-actions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_extract_actions_idempotent_reinvocation(client: AsyncClient, test_db: AsyncSession, extraction_setup):
    bob = extraction_setup["bob"]
    conv = extraction_setup["conv"]
    m_task = extraction_setup["m_task"]
    token = create_access_token(subject=bob.id)

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=MagicMock(
            content=f'''{{
  "candidates": [
    {{
      "owner_user_id": "{bob.id}",
      "action_type": "task",
      "title": "Idempotent Task Test",
      "confidence_score": 1.0
    }}
  ]
}}'''
        )
    )

    with patch("src.agents.conversation_intelligence.action_graph.get_llm", return_value=mock_llm):
        # Call 1
        res1 = await client.post(
            f"/api/v1/conversations/{conv.id}/messages/{m_task.id}/extract-actions",
            headers={"Authorization": f"Bearer {token}"},
        )
        # Call 2
        res2 = await client.post(
            f"/api/v1/conversations/{conv.id}/messages/{m_task.id}/extract-actions",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert res1.status_code == 200
    assert res2.status_code == 200
    p1 = res1.json()[0]
    p2 = res2.json()[0]
    assert p1["id"] == p2["id"]

    # Verify only 1 record exists in DB
    all_props = await test_db.execute(
        select(ActionProposal).where(ActionProposal.source_message_id == m_task.id)
    )
    assert len(all_props.scalars().all()) == 1


@pytest.mark.asyncio
async def test_extract_actions_provider_error_503(client: AsyncClient, extraction_setup):
    alice = extraction_setup["alice"]
    conv = extraction_setup["conv"]
    m_task = extraction_setup["m_task"]
    token = create_access_token(subject=alice.id)

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=RuntimeError("Provider 500 error"))

    with patch("src.agents.conversation_intelligence.action_graph.get_llm", return_value=mock_llm):
        response = await client.post(
            f"/api/v1/conversations/{conv.id}/messages/{m_task.id}/extract-actions",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Tomorrow I'll send the report.", "Ok, mình sẽ gửi report."])
async def test_other_members_self_commitment_never_becomes_requester_task(
    client: AsyncClient, test_db: AsyncSession, extraction_setup, text: str
):
    alice = extraction_setup["alice"]
    bob = extraction_setup["bob"]
    conv = extraction_setup["conv"]
    message = Message(
        conversation_id=conv.id,
        sender_id=bob.id,
        client_message_id=f"cross-user-{abs(hash(text))}",
        original_text=text,
        source_language="en",
    )
    test_db.add(message)
    await test_db.commit()
    llm = MagicMock()
    llm.ainvoke = AsyncMock(
        return_value=MagicMock(
            content='{"candidates":[{"action_type":"task","title":"Send report","relationship":"other_participant_self_commitment"}]}'
        )
    )
    with patch("src.agents.conversation_intelligence.action_graph.get_llm", return_value=llm):
        response = await client.post(
            f"/api/v1/conversations/{conv.id}/messages/{message.id}/extract-actions",
            headers={"Authorization": f"Bearer {create_access_token(subject=alice.id)}"},
        )
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_requester_assignment_and_self_commitment_are_eligible(client: AsyncClient, test_db: AsyncSession, extraction_setup):
    alice = extraction_setup["alice"]
    bob = extraction_setup["bob"]
    conv = extraction_setup["conv"]
    assigned = Message(
        conversation_id=conv.id,
        sender_id=bob.id,
        client_message_id="alice-assigned-report",
        original_text="Alice, please send the report tomorrow.",
        source_language="en",
    )
    self_commitment = Message(
        conversation_id=conv.id,
        sender_id=alice.id,
        client_message_id="alice-self-report",
        original_text="I'll send the report tomorrow.",
        source_language="en",
    )
    test_db.add_all([assigned, self_commitment])
    await test_db.commit()
    llm = MagicMock()
    llm.ainvoke = AsyncMock(
        return_value=MagicMock(
            content='{"candidates":[{"action_type":"task","title":"Send report","relationship":"requester_assigned_action"}]}'
        )
    )
    with patch("src.agents.conversation_intelligence.action_graph.get_llm", return_value=llm):
        for message in (assigned, self_commitment):
            response = await client.post(
                f"/api/v1/conversations/{conv.id}/messages/{message.id}/extract-actions",
                headers={"Authorization": f"Bearer {create_access_token(subject=alice.id)}"},
            )
            assert response.status_code == 200
            assert response.json()[0]["owner_user_id"] == alice.id
