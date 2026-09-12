"""Tests for Conversation Intelligence reliability foundation (Batch H / B-09)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

import src.services.intelligence.conversation_intelligence as conversation_intelligence_module
from src.agents.conversation_intelligence.errors import (
    IntelligenceError,
    IntelligenceErrorCode,
)
from src.agents.conversation_intelligence.observability import log_intelligence_event
from src.agents.conversation_intelligence.parsing import (
    clean_json_text,
    invoke_with_repair,
    parse_structured_json,
)
from src.services.intelligence.conversation_intelligence import ConversationIntelligenceService


class SamplePayload(BaseModel):
    title: str
    count: int = Field(ge=0)
    tags: list[str] = Field(default_factory=list)


@pytest.mark.asyncio
async def test_recent_action_scan_limits_scope_to_selected_conversation_human_messages(monkeypatch):
    """The date-window scan delegates each eligible source row to B-04.

    Persistence and ownership remain in ``extract_actions_from_message``; this
    test protects the important contract that the bulk endpoint never bypasses
    that path or reads an unbounded cross-conversation history.
    """
    consent = AsyncMock()
    monkeypatch.setattr(conversation_intelligence_module, "require_consent", consent)

    class Scalars:
        def all(self):
            return ["message-1", "message-2"]

    class Db:
        async def get(self, _model, identifier):
            return SimpleNamespace(id=identifier) if identifier == "conversation-1" else None

        async def scalar(self, _statement):
            return "user-1"

        async def scalars(self, _statement):
            return Scalars()

    service = ConversationIntelligenceService()
    service.extract_actions_from_message = AsyncMock(side_effect=[["proposal-1"], ["proposal-2"]])

    result = await service.extract_actions_from_recent_messages(
        conversation_id="conversation-1",
        user_id="user-1",
        days=7,
        db=Db(),
    )

    assert result == ["proposal-1", "proposal-2"]
    consent.assert_awaited_once_with(service.extract_actions_from_message.call_args.kwargs["db"], "user-1", "read_conversations")
    assert [call.kwargs["message_id"] for call in service.extract_actions_from_message.await_args_list] == ["message-1", "message-2"]


@pytest.mark.asyncio
async def test_recent_action_scan_keeps_other_results_when_one_message_times_out(monkeypatch):
    """A slow historical message must not fail the entire user-initiated scan."""
    monkeypatch.setattr(conversation_intelligence_module, "require_consent", AsyncMock())

    class Scalars:
        def all(self):
            return ["message-1", "message-2"]

    class Db:
        async def get(self, _model, identifier):
            return SimpleNamespace(id=identifier) if identifier == "conversation-1" else None

        async def scalar(self, _statement):
            return "user-1"

        async def scalars(self, _statement):
            return Scalars()

    service = ConversationIntelligenceService()
    service.extract_actions_from_message = AsyncMock(
        side_effect=[
            ["proposal-1"],
            IntelligenceError(IntelligenceErrorCode.TIMEOUT, "Timed out", operation="extract_actions"),
        ]
    )

    result = await service.extract_actions_from_recent_messages(
        conversation_id="conversation-1",
        user_id="user-1",
        days=7,
        db=Db(),
    )

    assert result == ["proposal-1"]


def test_clean_json_text_pure_json():
    raw = '{"title": "Test", "count": 5, "tags": ["a", "b"]}'
    assert clean_json_text(raw) == raw


def test_clean_json_text_fenced_json():
    raw = """Here is the result:
```json
{
  "title": "Fenced",
  "count": 10,
  "tags": []
}
```
Hope that helps!"""
    expected = '{\n  "title": "Fenced",\n  "count": 10,\n  "tags": []\n}'
    assert clean_json_text(raw) == expected


def test_clean_json_text_unfenced_with_filler():
    raw = 'Sure! Here is the JSON: {"title": "Filler", "count": 2, "tags": ["x"]} Let me know if you need more.'
    assert clean_json_text(raw) == '{"title": "Filler", "count": 2, "tags": ["x"]}'


def test_parse_structured_json_valid():
    raw = '{"title": "Valid", "count": 3, "tags": ["alpha"]}'
    parsed = parse_structured_json(raw, SamplePayload)
    assert parsed.title == "Valid"
    assert parsed.count == 3
    assert parsed.tags == ["alpha"]


def test_parse_structured_json_invalid_syntax():
    raw = '{"title": "Unclosed", "count":'
    with pytest.raises(IntelligenceError) as exc_info:
        parse_structured_json(raw, SamplePayload)
    assert exc_info.value.code == IntelligenceErrorCode.INVALID_OUTPUT
    assert "Failed to decode JSON" in exc_info.value.message


def test_parse_structured_json_schema_mismatch():
    raw = '{"title": "WrongType", "count": -5}'  # count must be >= 0
    with pytest.raises(IntelligenceError) as exc_info:
        parse_structured_json(raw, SamplePayload)
    assert exc_info.value.code == IntelligenceErrorCode.INVALID_OUTPUT
    assert "did not conform to schema" in exc_info.value.message


@pytest.mark.asyncio
async def test_invoke_with_repair_success_first_try():
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=MagicMock(content='{"title": "FirstTry", "count": 1, "tags": ["ok"]}')
    )

    result = await invoke_with_repair(
        llm=mock_llm,
        messages=[HumanMessage(content="test")],
        schema=SamplePayload,
        operation="test_op",
    )
    assert result.title == "FirstTry"
    assert mock_llm.ainvoke.call_count == 1


@pytest.mark.asyncio
async def test_invoke_with_repair_repaired_on_second_try():
    mock_llm = MagicMock()
    # First response is invalid JSON, second response is valid JSON
    mock_llm.ainvoke = AsyncMock(
        side_effect=[
            MagicMock(content="I cannot provide JSON directly, here is plain text"),
            MagicMock(content='{"title": "Repaired", "count": 7, "tags": ["fixed"]}'),
        ]
    )

    result = await invoke_with_repair(
        llm=mock_llm,
        messages=[HumanMessage(content="test")],
        schema=SamplePayload,
        operation="test_op",
    )
    assert result.title == "Repaired"
    assert result.count == 7
    assert mock_llm.ainvoke.call_count == 2


@pytest.mark.asyncio
async def test_invoke_with_repair_fails_after_one_retry():
    mock_llm = MagicMock()
    # Both responses are invalid
    mock_llm.ainvoke = AsyncMock(
        side_effect=[
            MagicMock(content="Invalid 1"),
            MagicMock(content="Invalid 2"),
        ]
    )

    with pytest.raises(IntelligenceError) as exc_info:
        await invoke_with_repair(
            llm=mock_llm,
            messages=[HumanMessage(content="test")],
            schema=SamplePayload,
            operation="test_op",
        )
    assert exc_info.value.code == IntelligenceErrorCode.INVALID_OUTPUT
    assert mock_llm.ainvoke.call_count == 2


@pytest.mark.asyncio
async def test_invoke_with_repair_provider_error_no_retry():
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=RuntimeError("Provider 500 Connection Refused"))

    with pytest.raises(IntelligenceError) as exc_info:
        await invoke_with_repair(
            llm=mock_llm,
            messages=[HumanMessage(content="test")],
            schema=SamplePayload,
            operation="test_op",
        )
    assert exc_info.value.code == IntelligenceErrorCode.PROVIDER_UNAVAILABLE
    # Must NOT storm the provider with retries on network/provider error
    assert mock_llm.ainvoke.call_count == 1


@pytest.mark.asyncio
async def test_invoke_with_repair_timeout_no_retry():
    mock_llm = MagicMock()

    async def slow_call(*args, **kwargs):
        await asyncio.sleep(0.5)
        return MagicMock(content="{}")

    mock_llm.ainvoke = AsyncMock(side_effect=slow_call)

    with pytest.raises(IntelligenceError) as exc_info:
        await invoke_with_repair(
            llm=mock_llm,
            messages=[HumanMessage(content="test")],
            schema=SamplePayload,
            operation="test_op",
            timeout=0.05,
        )
    assert exc_info.value.code == IntelligenceErrorCode.TIMEOUT
    assert mock_llm.ainvoke.call_count == 1


def test_logging_failure_never_crashes():
    # Calling log_intelligence_event with bad types or throwing logger must not raise
    with patch("src.agents.conversation_intelligence.observability.logger.info", side_effect=Exception("Disk full")):
        # Should execute silently without exception
        log_intelligence_event(
            operation="summary",
            status="success",
            latency_ms=120.0,
            conversation_id="conv-123",
        )


def test_error_to_dict_safe_no_sensitive_leak():
    err = IntelligenceError(
        code=IntelligenceErrorCode.INVALID_OUTPUT,
        message="Schema validation error",
        operation="extract_actions",
        details={"raw_secret_key": "should_be_contained"},
    )
    safe_dict = err.to_dict()
    assert safe_dict["error_code"] == "intelligence_invalid_output"
    assert safe_dict["operation"] == "extract_actions"
    assert safe_dict["message"] == "Schema validation error"
    assert "raw_secret_key" not in safe_dict


@pytest.mark.asyncio
async def test_conversation_intelligence_service_invoke():
    service = ConversationIntelligenceService()
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=MagicMock(content='{"title": "ServiceTest", "count": 9, "tags": []}')
    )

    with patch.object(service, "get_model", return_value=mock_llm):
        res = await service.invoke_structured(
            messages=[HumanMessage(content="Hello")],
            schema=SamplePayload,
            operation="service_test",
        )
        assert res.title == "ServiceTest"
        assert res.count == 9
