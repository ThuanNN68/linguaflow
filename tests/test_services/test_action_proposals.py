"""Tests for Action Proposal persistence, idempotency, and state machine (Batch J1 / B-04/B-05)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import get_password_hash
from src.database.models import ActionProposal, Conversation, ConversationMember, Message, User
from src.schemas.intelligence import ActionCandidateDTO
from src.services.intelligence.action_proposals import (
    ActionProposalNotFoundError,
    ActionProposalOwnershipError,
    ActionProposalService,
    ActionProposalStatusError,
    compute_proposal_idempotency_key,
    normalize_action_time,
)


@pytest_asyncio.fixture
async def proposal_setup(test_db: AsyncSession):
    """Fixture providing users, a conversation, and a message for action proposal testing."""
    owner = User(
        email="owner_prop@example.com",
        username="owner_prop",
        display_name="Owner User",
        password_hash=get_password_hash("pass123"),
        role="member",
        preferred_language="en",
    )
    other = User(
        email="other_prop@example.com",
        username="other_prop",
        display_name="Other User",
        password_hash=get_password_hash("pass123"),
        role="member",
        preferred_language="vi",
    )
    test_db.add_all([owner, other])
    await test_db.flush()

    conv = Conversation(type="direct", created_by=owner.id)
    test_db.add(conv)
    await test_db.flush()

    test_db.add_all([
        ConversationMember(conversation_id=conv.id, user_id=owner.id),
        ConversationMember(conversation_id=conv.id, user_id=other.id),
    ])

    msg = Message(
        conversation_id=conv.id,
        sender_id=other.id,
        client_message_id="c-msg-prop",
        original_text="Can you review the PR by tomorrow 3 PM?",
        source_language="en",
    )
    test_db.add(msg)
    await test_db.commit()

    return {
        "owner": owner,
        "other": other,
        "conv": conv,
        "msg": msg,
    }


def test_compute_proposal_idempotency_key_deterministic():
    t1 = datetime(2026, 8, 22, 15, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 8, 22, 15, 0, 0, tzinfo=UTC)

    key1 = compute_proposal_idempotency_key("u1", "m1", "task", "Review PR", t1)
    key2 = compute_proposal_idempotency_key("u1", "m1", "task", "  review pr  ", t2)
    assert key1 == key2

    key_diff_type = compute_proposal_idempotency_key("u1", "m1", "appointment", "Review PR", t1)
    assert key1 != key_diff_type


@pytest.mark.asyncio
async def test_create_proposals_from_candidates_and_idempotency(test_db: AsyncSession, proposal_setup):
    service = ActionProposalService(test_db)
    owner = proposal_setup["owner"]
    conv = proposal_setup["conv"]
    msg = proposal_setup["msg"]

    candidates = [
        ActionCandidateDTO(
            owner_user_id=owner.id,
            action_type="task",
            title="Review PR",
            details="Review the pull request before sprint release",
            scheduled_time=datetime(2026, 8, 22, 15, 0, 0, tzinfo=UTC),
            confidence_score=0.95,
        )
    ]

    # First creation
    proposals1 = await service.create_proposals_from_candidates(
        conversation_id=conv.id,
        source_message_id=msg.id,
        candidates=candidates, owner_user_id=owner.id, source_mode="on_demand",
    )
    assert len(proposals1) == 1
    p1 = proposals1[0]
    assert p1.status == "pending_confirmation"
    assert p1.owner_user_id == owner.id
    assert p1.title == "Review PR"

    # Second creation (idempotent deduplication)
    proposals2 = await service.create_proposals_from_candidates(
        conversation_id=conv.id,
        source_message_id=msg.id,
        candidates=candidates, owner_user_id=owner.id, source_mode="on_demand",
    )
    assert len(proposals2) == 1
    assert proposals2[0].id == p1.id


@pytest.mark.asyncio
async def test_get_proposal_and_not_found(test_db: AsyncSession, proposal_setup):
    service = ActionProposalService(test_db)
    with pytest.raises(ActionProposalNotFoundError):
        await service.get_proposal("00000000-0000-0000-0000-000000000000")


@pytest.mark.asyncio
async def test_list_proposals_is_owner_scoped(test_db: AsyncSession, proposal_setup):
    service = ActionProposalService(test_db)
    owner = proposal_setup["owner"]
    res = await service.list_for_owner(owner.id)
    assert isinstance(res, list)
    assert await service.list_for_owner("non-member-id") == []


@pytest.mark.asyncio
async def test_confirm_proposal_lifecycle_and_invariants(test_db: AsyncSession, proposal_setup):
    service = ActionProposalService(test_db)
    owner = proposal_setup["owner"]
    other = proposal_setup["other"]
    conv = proposal_setup["conv"]
    msg = proposal_setup["msg"]

    proposals = await service.create_proposals_from_candidates(
        conversation_id=conv.id,
        source_message_id=msg.id,
        candidates=[
            ActionCandidateDTO(
                owner_user_id=owner.id,
                action_type="appointment",
                title="Sprint Demo Meeting",
                details="Review the final design",
                location="Room C401",
                scheduled_time=datetime(2026, 8, 22, 10, 0, 0, tzinfo=UTC),
                scheduled_end_time=datetime(2026, 8, 22, 11, 30, 0, tzinfo=UTC),
            )
        ], owner_user_id=owner.id, source_mode="on_demand",
    )
    p = proposals[0]
    assert p.scheduled_end_at == datetime(2026, 8, 22, 11, 30, 0, tzinfo=UTC)
    assert p.location == "Room C401"
    assert p.details == "Review the final design"

    # Non-owner cannot confirm
    with pytest.raises(ActionProposalOwnershipError):
        await service.confirm_proposal(p.id, user_id=other.id)

    # Owner confirms
    confirmed = await service.confirm_proposal(p.id, user_id=owner.id)
    assert confirmed.status == "confirmed"
    assert confirmed.confirmed_at is not None

    # Cannot transition from terminal state
    with pytest.raises(ActionProposalStatusError):
        await service.confirm_proposal(p.id, user_id=owner.id)

    with pytest.raises(ActionProposalStatusError):
        await service.reject_proposal(p.id, user_id=owner.id)


@pytest.mark.asyncio
async def test_reject_proposal_lifecycle_and_invariants(test_db: AsyncSession, proposal_setup):
    service = ActionProposalService(test_db)
    owner = proposal_setup["owner"]
    other = proposal_setup["other"]
    conv = proposal_setup["conv"]
    msg = proposal_setup["msg"]

    proposals = await service.create_proposals_from_candidates(
        conversation_id=conv.id,
        source_message_id=msg.id,
        candidates=[
            ActionCandidateDTO(
                owner_user_id=owner.id,
                action_type="task",
                title="Rejectable Task",
            )
        ], owner_user_id=owner.id, source_mode="on_demand",
    )
    p = proposals[0]

    # Non-owner cannot reject
    with pytest.raises(ActionProposalOwnershipError):
        await service.reject_proposal(p.id, user_id=other.id)

    # Owner rejects
    rejected = await service.reject_proposal(p.id, user_id=owner.id)
    assert rejected.status == "rejected"
    assert rejected.rejected_at is not None

    # Cannot transition from terminal state
    with pytest.raises(ActionProposalStatusError):
        await service.reject_proposal(p.id, user_id=owner.id)


@pytest.mark.asyncio
async def test_mark_proposals_stale_for_message(test_db: AsyncSession, proposal_setup):
    service = ActionProposalService(test_db)
    owner = proposal_setup["owner"]
    conv = proposal_setup["conv"]
    msg = proposal_setup["msg"]

    proposals = await service.create_proposals_from_candidates(
        conversation_id=conv.id,
        source_message_id=msg.id,
        candidates=[
            ActionCandidateDTO(
                owner_user_id=owner.id,
                action_type="task",
                title="Task To Become Stale",
            ),
            ActionCandidateDTO(
                owner_user_id=owner.id,
                action_type="appointment",
                title="Meeting Already Confirmed",
            ),
        ], owner_user_id=owner.id, source_mode="on_demand",
    )
    p_pending = proposals[0]
    p_confirmed = proposals[1]

    # Confirm the second proposal
    await service.confirm_proposal(p_confirmed.id, user_id=owner.id)

    # Message is edited / deleted -> mark pending stale
    stale_count = await service.mark_proposals_stale_for_message(msg.id)
    assert stale_count == 1

    reloaded_pending = await service.get_proposal(p_pending.id)
    assert reloaded_pending.status == "stale"
    assert reloaded_pending.stale_at is not None

    reloaded_confirmed = await service.get_proposal(p_confirmed.id)
    assert reloaded_confirmed.status == "confirmed"


def test_temporal_normalizer_requires_trusted_timezone_for_relative_time():
    reference = datetime(2026, 8, 21, 8, 0, tzinfo=UTC)
    unresolved = normalize_action_time(
        raw_time_expression="mai 9h",
        reference_timestamp=reference,
        trusted_timezone=None,
        candidate_datetime=datetime(2026, 8, 22, 2, 0, tzinfo=UTC),
    )
    assert unresolved.scheduled_start_at is None
    assert set(unresolved.missing_fields) >= {"timezone", "time"}

    resolved = normalize_action_time(
        raw_time_expression="mai 9h",
        reference_timestamp=reference,
        trusted_timezone="Asia/Ho_Chi_Minh",
    )
    assert resolved.scheduled_start_at == datetime(2026, 8, 22, 2, 0, tzinfo=UTC)
    assert resolved.resolved_timezone == "Asia/Ho_Chi_Minh"
    assert resolved.missing_fields == ()

    explicit = normalize_action_time(
        raw_time_expression="2026-08-22T09:00:00+07:00",
        reference_timestamp=reference,
        trusted_timezone=None,
    )
    assert explicit.scheduled_start_at == datetime(2026, 8, 22, 2, 0, tzinfo=UTC)


def test_temporal_normalizer_accepts_local_vietnamese_clarification_with_timezone():
    resolved = normalize_action_time(
        raw_time_expression=None,
        reference_timestamp=datetime(2026, 8, 28, 8, 0, tzinfo=UTC),
        trusted_timezone="Asia/Ho_Chi_Minh",
        clarification_answer="09:00 ngày 29/08/2026",
        existing_missing_fields=["time"],
    )

    assert resolved.scheduled_start_at == datetime(2026, 8, 29, 2, 0, tzinfo=UTC)
    assert resolved.resolved_timezone == "Asia/Ho_Chi_Minh"
    assert resolved.missing_fields == ()


@pytest.mark.asyncio
async def test_clarification_resolves_only_temporal_ambiguity(test_db: AsyncSession, proposal_setup):
    service = ActionProposalService(test_db)
    owner = proposal_setup["owner"]
    other = proposal_setup["other"]
    conv = proposal_setup["conv"]
    msg = proposal_setup["msg"]
    msg.created_at = datetime(2026, 8, 21, 8, 0, tzinfo=UTC)
    proposal = ActionProposal(
        conversation_id=conv.id,
        source_message_id=msg.id,
        owner_user_id=owner.id,
        source_mode="on_demand",
        action_type="task",
        status="needs_clarification",
        title="Send report",
        raw_time_expression="mai 9h",
        missing_fields='["timezone", "time"]',
        confidence_score=0.9,
        idempotency_key="clarify-temporal-1",  # gitleaks:allow
    )
    test_db.add(proposal)
    await test_db.commit()
    with pytest.raises(ActionProposalOwnershipError):
        await service.clarify(proposal.id, other.id, "Asia/Ho_Chi_Minh", None)

    still_ambiguous = await service.clarify(proposal.id, owner.id, "tomorrow", None)
    assert still_ambiguous.status == "needs_clarification"
    assert still_ambiguous.scheduled_start_at is None
    assert still_ambiguous.clarification_rounds == 1

    clarified = await service.clarify(proposal.id, owner.id, "Asia/Ho_Chi_Minh", None)
    assert clarified.status == "pending_confirmation"
    assert clarified.clarification_rounds == 2
    assert clarified.scheduled_start_at == datetime(2026, 8, 22, 2, 0, tzinfo=UTC)
    assert clarified.resolved_timezone == "Asia/Ho_Chi_Minh"
    assert clarified.confirmed_at is None

    with pytest.raises(ActionProposalStatusError):
        await service.clarify(proposal.id, owner.id, "Asia/Ho_Chi_Minh", None)


@pytest.mark.asyncio
async def test_duplicate_candidate_does_not_rollback_earlier_candidate(test_db: AsyncSession, proposal_setup):
    service = ActionProposalService(test_db)
    owner = proposal_setup["owner"]
    conv = proposal_setup["conv"]
    msg = proposal_setup["msg"]
    duplicate = ActionCandidateDTO(action_type="task", title="Existing duplicate")
    await service.create_proposals_from_candidates(
        conv.id, msg.id, [duplicate], owner_user_id=owner.id, source_mode="on_demand"
    )
    result = await service.create_proposals_from_candidates(
        conv.id,
        msg.id,
        [ActionCandidateDTO(action_type="task", title="Earlier unique"), duplicate],
        owner_user_id=owner.id,
        source_mode="on_demand",
    )
    assert {proposal.title for proposal in result} == {"Earlier unique", "Existing duplicate"}
    assert await test_db.scalar(
        select(ActionProposal.id).where(ActionProposal.title == "Earlier unique")
    ) is not None


@pytest.mark.asyncio
async def test_needs_clarification_can_confirm_only_with_complete_same_request_correction(
    test_db: AsyncSession, proposal_setup
):
    service = ActionProposalService(test_db)
    owner = proposal_setup["owner"]
    conv = proposal_setup["conv"]
    msg = proposal_setup["msg"]
    proposal = ActionProposal(
        conversation_id=conv.id,
        source_message_id=msg.id,
        owner_user_id=owner.id,
        source_mode="on_demand",
        action_type="appointment",
        status="needs_clarification",
        title="Planning meeting",
        missing_fields='["location"]',
        confidence_score=0.8,
        idempotency_key="confirm-needs-location",
    )
    test_db.add(proposal)
    await test_db.commit()

    with pytest.raises(ActionProposalStatusError):
        await service.confirm_proposal(proposal.id, owner.id)
    with pytest.raises(ActionProposalStatusError):
        await service.confirm_proposal(proposal.id, owner.id, {"details": "Not location"})

    confirmed = await service.confirm_proposal(proposal.id, owner.id, {"location": "Room A-301"})
    assert confirmed.status == "confirmed"
    assert confirmed.location == "Room A-301"
    assert confirmed.missing_fields == "[]"
    assert confirmed.confirmed_by_user_id == owner.id
    assert confirmed.confirmed_at is not None


@pytest.mark.asyncio
async def test_non_temporal_clarification_resolves_only_existing_missing_field(test_db: AsyncSession, proposal_setup):
    service = ActionProposalService(test_db)
    owner = proposal_setup["owner"]
    conv = proposal_setup["conv"]
    msg = proposal_setup["msg"]
    location_only = ActionProposal(
        conversation_id=conv.id,
        source_message_id=msg.id,
        owner_user_id=owner.id,
        source_mode="on_demand",
        action_type="appointment",
        status="needs_clarification",
        title="Architecture review",
        missing_fields='["location"]',
        confidence_score=0.8,
        idempotency_key="clarify-location-only",
    )
    multiple = ActionProposal(
        conversation_id=conv.id,
        source_message_id=msg.id,
        owner_user_id=owner.id,
        source_mode="on_demand",
        action_type="appointment",
        status="needs_clarification",
        title="Timezone and room",
        raw_time_expression="mai 9h",
        missing_fields='["location", "timezone"]',
        confidence_score=0.8,
        idempotency_key="clarify-location-and-timezone",
    )
    test_db.add_all([location_only, multiple])
    await test_db.commit()

    resolved = await service.clarify(location_only.id, owner.id, "Room A-301", None)
    assert resolved.status == "pending_confirmation"
    assert resolved.location == "Room A-301"
    assert resolved.title == "Architecture review"
    assert resolved.missing_fields == "[]"

    partial = await service.clarify(multiple.id, owner.id, "Room A-301", None)
    assert partial.status == "needs_clarification"
    assert partial.location == "Room A-301"
    assert "timezone" in partial.missing_fields
    assert partial.title == "Timezone and room"
    assert partial.clarification_rounds == 1


@pytest.mark.asyncio
async def test_raw_relative_time_is_part_of_idempotency_identity(test_db: AsyncSession, proposal_setup):
    service = ActionProposalService(test_db)
    owner = proposal_setup["owner"]
    conv = proposal_setup["conv"]
    msg = proposal_setup["msg"]
    candidates = [
        ActionCandidateDTO(action_type="task", title="Send report", raw_time_expression="mai 9h"),
        ActionCandidateDTO(action_type="task", title="Send report", raw_time_expression="mai 10h"),
    ]
    proposals = await service.create_proposals_from_candidates(
        conv.id, msg.id, candidates, owner_user_id=owner.id, source_mode="on_demand"
    )
    assert len({proposal.id for proposal in proposals}) == 2
    assert proposals[0].idempotency_key != proposals[1].idempotency_key
    assert compute_proposal_idempotency_key(
        owner.id, msg.id, "task", "Send report", None, "on_demand", "mai 9h"
    ) == compute_proposal_idempotency_key(
        owner.id, msg.id, "task", " send report ", None, "on_demand", "MAI   9H"
    )


@pytest.mark.asyncio
async def test_needs_clarification_temporal_correction_confirms_atomically(test_db: AsyncSession, proposal_setup):
    service = ActionProposalService(test_db)
    owner = proposal_setup["owner"]
    conv = proposal_setup["conv"]
    msg = proposal_setup["msg"]
    msg.created_at = datetime(2026, 8, 21, 8, 0, tzinfo=UTC)
    proposal = ActionProposal(
        conversation_id=conv.id,
        source_message_id=msg.id,
        owner_user_id=owner.id,
        source_mode="on_demand",
        action_type="task",
        status="needs_clarification",
        title="Send report",
        raw_time_expression="mai 9h",
        missing_fields='["timezone", "time"]',
        confidence_score=0.8,
        idempotency_key="confirm-needs-timezone",
    )
    test_db.add(proposal)
    await test_db.commit()

    confirmed = await service.confirm_proposal(
        proposal.id, owner.id, {"timezone": "Asia/Ho_Chi_Minh"}
    )
    assert confirmed.status == "confirmed"
    assert confirmed.missing_fields == "[]"
    assert confirmed.scheduled_start_at == datetime(2026, 8, 22, 2, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_explicit_owner_datetime_resolves_only_time_missing_field(test_db: AsyncSession, proposal_setup):
    service = ActionProposalService(test_db)
    owner = proposal_setup["owner"]
    conv = proposal_setup["conv"]
    msg = proposal_setup["msg"]
    time_only = ActionProposal(
        conversation_id=conv.id,
        source_message_id=msg.id,
        owner_user_id=owner.id,
        source_mode="on_demand",
        action_type="appointment",
        status="needs_clarification",
        title="Review meeting",
        missing_fields='["time"]',
        confidence_score=0.8,
        idempotency_key="explicit-time-only",
    )
    time_and_location = ActionProposal(
        conversation_id=conv.id,
        source_message_id=msg.id,
        owner_user_id=owner.id,
        source_mode="on_demand",
        action_type="appointment",
        status="needs_clarification",
        title="Review meeting elsewhere",
        missing_fields='["time", "location"]',
        confidence_score=0.8,
        idempotency_key="explicit-time-and-location",
    )
    test_db.add_all([time_only, time_and_location])
    await test_db.commit()

    confirmed = await service.confirm_proposal(
        time_only.id, owner.id, {"scheduled_start_at": "2026-08-22T09:00:00+07:00"}
    )
    assert confirmed.status == "confirmed"
    assert confirmed.missing_fields == "[]"
    assert confirmed.scheduled_start_at == datetime(2026, 8, 22, 2, 0, tzinfo=UTC)

    with pytest.raises(ActionProposalStatusError):
        await service.confirm_proposal(
            time_and_location.id,
            owner.id,
            {"scheduled_start_at": "2026-08-22T09:00:00+07:00"},
        )
    unchanged = await service.get_proposal(time_and_location.id)
    assert unchanged.status == "needs_clarification"
    assert unchanged.missing_fields == '["time", "location"]'


@pytest.mark.asyncio
async def test_clarification_parser_failure_leaves_row_unchanged(
    test_db: AsyncSession, proposal_setup, monkeypatch
):
    service = ActionProposalService(test_db)
    owner = proposal_setup["owner"]
    conv = proposal_setup["conv"]
    msg = proposal_setup["msg"]
    proposal = ActionProposal(
        conversation_id=conv.id,
        source_message_id=msg.id,
        owner_user_id=owner.id,
        source_mode="on_demand",
        action_type="task",
        status="needs_clarification",
        title="Send report",
        raw_time_expression="mai 9h",
        missing_fields='["timezone", "time"]',
        confidence_score=0.8,
        idempotency_key="clarify-parser-failure",
    )
    test_db.add(proposal)
    await test_db.commit()
    proposal_id = proposal.id

    def fail_normalization(**_kwargs):
        raise ValueError("invalid structured clarification")

    monkeypatch.setattr("src.services.intelligence.action_proposals.normalize_action_time", fail_normalization)
    with pytest.raises(ValueError, match="structured clarification"):
        await service.clarify(proposal_id, owner.id, "Asia/Ho_Chi_Minh", None)
    test_db.expire_all()
    unchanged = await service.get_proposal(proposal_id)
    assert unchanged.status == "needs_clarification"
    assert unchanged.clarification_rounds == 0
    assert unchanged.missing_fields == '["timezone", "time"]'
