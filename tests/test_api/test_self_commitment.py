"""Tests for Proactive Self-Commitment Detection API (B-10 / Batch M)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.conversation_intelligence.self_commitment import detect_self_commitments
from src.core.security import create_access_token, get_password_hash
from src.database.models import ActionProposal, Conversation, ConversationMember, Message, User
from src.services.assistant.agent_consent import set_consents


@pytest_asyncio.fixture
async def commitment_setup(test_db: AsyncSession):
    """Setup test users, conversation, and messages for self-commitment testing."""
    alice = User(
        email="alice_commit@example.com",
        username="alice_commit",
        display_name="Alice Nguyen",
        password_hash=get_password_hash("pass123"),
        role="member",
        preferred_language="vi",
    )
    bob = User(
        email="bob_commit@example.com",
        username="bob_commit",
        display_name="Bob Smith",
        password_hash=get_password_hash("pass123"),
        role="member",
        preferred_language="en",
    )
    outsider = User(
        email="outsider_commit@example.com",
        username="outsider_commit",
        display_name="Outsider Commit",
        password_hash=get_password_hash("pass123"),
        role="member",
        preferred_language="vi",
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

    m_vi = Message(
        conversation_id=conv.id,
        sender_id=alice.id,
        client_message_id="c-msg-vi-sc",
        original_text="Tôi sẽ hoàn thành slide thuyết trình trước 17h chiều mai nhé.",
        source_language="vi",
    )
    m_en = Message(
        conversation_id=conv.id,
        sender_id=bob.id,
        client_message_id="c-msg-en-sc",
        original_text="I will schedule a team demo on Friday at 10 AM UTC.",
        source_language="en",
    )
    m_casual = Message(
        conversation_id=conv.id,
        sender_id=alice.id,
        client_message_id="c-msg-casual-sc",
        original_text="Chào buổi sáng mọi người!",
        source_language="vi",
    )
    test_db.add_all([m_vi, m_en, m_casual])
    await test_db.commit()

    # Granted to all three, including the outsider: these tests are about
    # membership, ownership and detection quality. Leaving consent off would
    # make several of them pass for the wrong reason — a 403 about permissions
    # looks identical to a 403 about membership from the status code alone.
    for account in (alice, bob, outsider):
        await set_consents(test_db, account.id, {"read_conversations": True})

    return {
        "alice": alice,
        "bob": bob,
        "outsider": outsider,
        "conv_id": conv.id,
        "m_vi_id": m_vi.id,
        "m_en_id": m_en.id,
        "m_casual_id": m_casual.id,
        "m_vi": m_vi,
    }


@pytest.mark.asyncio
async def test_detect_self_commitments_vietnamese_success_200(client: AsyncClient, test_db: AsyncSession, commitment_setup):
    alice = commitment_setup["alice"]
    conv_id = commitment_setup["conv_id"]
    m_vi_id = commitment_setup["m_vi_id"]
    token = create_access_token(subject=alice.id)

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=MagicMock(
            content=f'''{{
  "candidates": [
    {{
      "owner_user_id": "{alice.id}",
      "action_type": "task",
      "title": "Hoàn thành slide thuyết trình",
      "details": "Trước 17h chiều mai",
      "scheduled_time": "2026-08-22T10:00:00Z",
      "confidence_score": 0.95,
      "clarification_prompt": null
    }}
  ]
}}'''
        )
    )

    with patch("src.agents.conversation_intelligence.self_commitment.get_llm", return_value=mock_llm):
        response = await client.post(
            f"/api/v1/conversations/{conv_id}/messages/{m_vi_id}/detect-commitments",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    prop = data[0]
    assert prop["owner_user_id"] == alice.id
    assert prop["status"] == "pending_confirmation"
    assert prop["action_type"] == "task"
    assert "slide" in prop["title"]
    assert prop["scheduled_time"] == "2026-08-22T10:00:00Z"

    # Verify DB persistence
    count = await test_db.scalar(
        select(func.count(ActionProposal.id)).where(ActionProposal.source_message_id == m_vi_id)
    )
    assert count == 1


@pytest.mark.asyncio
async def test_detect_self_commitments_english_success_200(client: AsyncClient, test_db: AsyncSession, commitment_setup):
    bob = commitment_setup["bob"]
    conv_id = commitment_setup["conv_id"]
    m_en_id = commitment_setup["m_en_id"]
    token = create_access_token(subject=bob.id)

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=MagicMock(
            content=f'''{{
  "candidates": [
    {{
      "owner_user_id": "{bob.id}",
      "action_type": "appointment",
      "title": "Team demo meeting",
      "details": "Client team demo sync",
      "scheduled_time": "2026-08-28T10:00:00Z",
      "confidence_score": 0.98,
      "clarification_prompt": null
    }}
  ]
}}'''
        )
    )

    with patch("src.agents.conversation_intelligence.self_commitment.get_llm", return_value=mock_llm):
        response = await client.post(
            f"/api/v1/conversations/{conv_id}/messages/{m_en_id}/detect-commitments",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    prop = data[0]
    assert prop["owner_user_id"] == bob.id
    assert prop["action_type"] == "appointment"
    assert prop["status"] == "pending_confirmation"


@pytest.mark.asyncio
async def test_detect_self_commitments_no_commitments_empty_list(client: AsyncClient, commitment_setup):
    alice = commitment_setup["alice"]
    conv_id = commitment_setup["conv_id"]
    m_casual_id = commitment_setup["m_casual_id"]
    token = create_access_token(subject=alice.id)

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=MagicMock(
            content='''{
  "candidates": []
}'''
        )
    )

    with patch("src.agents.conversation_intelligence.self_commitment.get_llm", return_value=mock_llm):
        response = await client.post(
            f"/api/v1/conversations/{conv_id}/messages/{m_casual_id}/detect-commitments",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data == []


@pytest.mark.asyncio
async def test_detect_self_commitments_idempotency(client: AsyncClient, test_db: AsyncSession, commitment_setup):
    alice = commitment_setup["alice"]
    conv_id = commitment_setup["conv_id"]
    m_vi_id = commitment_setup["m_vi_id"]
    token = create_access_token(subject=alice.id)

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=MagicMock(
            content=f'''{{
  "candidates": [
    {{
      "owner_user_id": "{alice.id}",
      "action_type": "task",
      "title": "Hoàn thành slide thuyết trình",
      "details": "Trước 17h chiều mai",
      "scheduled_time": "2026-08-22T10:00:00Z",
      "confidence_score": 0.95,
      "clarification_prompt": null
    }}
  ]
}}'''
        )
    )

    with patch("src.agents.conversation_intelligence.self_commitment.get_llm", return_value=mock_llm):
        res1 = await client.post(
            f"/api/v1/conversations/{conv_id}/messages/{m_vi_id}/detect-commitments",
            headers={"Authorization": f"Bearer {token}"},
        )
        res2 = await client.post(
            f"/api/v1/conversations/{conv_id}/messages/{m_vi_id}/detect-commitments",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert res1.status_code == 200
    assert res2.status_code == 200
    assert res1.json()[0]["id"] == res2.json()[0]["id"]

    test_db.expire_all()
    count = await test_db.scalar(
        select(func.count(ActionProposal.id)).where(ActionProposal.source_message_id == m_vi_id)
    )
    assert count == 1


@pytest.mark.asyncio
async def test_detect_self_commitments_non_member_403(client: AsyncClient, commitment_setup):
    outsider = commitment_setup["outsider"]
    conv_id = commitment_setup["conv_id"]
    m_vi_id = commitment_setup["m_vi_id"]
    token = create_access_token(subject=outsider.id)

    response = await client.post(
        f"/api/v1/conversations/{conv_id}/messages/{m_vi_id}/detect-commitments",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_detect_self_commitments_nonexistent_message_404(client: AsyncClient, commitment_setup):
    alice = commitment_setup["alice"]
    conv_id = commitment_setup["conv_id"]
    token = create_access_token(subject=alice.id)

    response = await client.post(
        f"/api/v1/conversations/{conv_id}/messages/00000000-0000-0000-0000-000000000000/detect-commitments",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_manual_detection_cannot_expose_another_members_private_proposal(client: AsyncClient, commitment_setup):
    alice = commitment_setup["alice"]
    conv_id = commitment_setup["conv_id"]
    bob_message_id = commitment_setup["m_en_id"]
    response = await client.post(
        f"/api/v1/conversations/{conv_id}/messages/{bob_message_id}/detect-commitments",
        headers={"Authorization": f"Bearer {create_access_token(subject=alice.id)}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "Bạn gửi report cho mình nhé?",
        "John will send the report tomorrow.",
        "Maybe we'll meet tomorrow.",
        "If I have time I'll check it.",
        "I sent it yesterday.",
    ],
)
async def test_semantic_negatives_never_invoke_detector_provider(text):
    with patch("src.agents.conversation_intelligence.self_commitment.get_llm") as get_llm:
        proposals = await detect_self_commitments(
            message_text=text,
            sender_id="sender-1",
            sender_name="Sender",
            reference_timestamp=None,
            conversation_id="conversation-1",
            message_id="message-1",
        )
    assert proposals == []
    get_llm.assert_not_called()
