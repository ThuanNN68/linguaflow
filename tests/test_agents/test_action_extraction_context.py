"""Prompt-level contracts for nearby context in appointment extraction."""

from src.agents.conversation_intelligence.prompts import build_action_extraction_user_prompt


def test_action_extraction_prompt_marks_nearby_messages_as_completion_context() -> None:
    """Nearby messages may complete the target request, not become independent actions."""
    prompt = build_action_extraction_user_prompt(
        message_text="Tạo lịch họp kế hoạch dự án.",
        sender_id="member-1",
        sender_name="Member",
        members_context="- Member (@member) [id: member-1]",
        reference_timestamp="2026-09-12T10:00:00+00:00",
        nearby_context="- Member: Ngày mai lúc 22:00, nhớ bật camera.",
    )

    assert "<target_message>" in prompt
    assert "<nearby_context>" in prompt
    assert "Ngày mai lúc 22:00, nhớ bật camera." in prompt
    assert "do not create an action from context" in prompt
    assert "alone, and do not copy unrelated details" in prompt
    assert "complete missing fields" in prompt


def test_action_extraction_prompt_omits_context_section_when_none_is_available() -> None:
    prompt = build_action_extraction_user_prompt(
        message_text="Tạo lịch họp kế hoạch dự án.",
        sender_id="member-1",
        sender_name="Member",
        members_context="- Member (@member) [id: member-1]",
        reference_timestamp="2026-09-12T10:00:00+00:00",
    )

    assert "<nearby_context>" not in prompt
