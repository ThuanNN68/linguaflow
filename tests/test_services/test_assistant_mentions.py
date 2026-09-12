"""Tests for the detached worker behind an ``@assistant`` mention.

What matters here is the plumbing, not the agent: its own session, its own
failure containment, and a notification that reaches exactly one account. The
graph's routing and its human gate are covered in
`tests/test_agents/test_assistant_graph.py`.

Fixtures live in this file rather than tests/conftest.py, which is shared across
all feature areas.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.assistant import assistant_mentions
from src.services.assistant.assistant_agent import AssistantRunResult


@pytest.fixture
def session_context(monkeypatch):
    """Give the worker a sentinel session and record that it opened its own."""
    entered: list[object] = []

    @asynccontextmanager
    async def factory():
        sentinel = object()
        entered.append(sentinel)
        yield sentinel

    monkeypatch.setattr(assistant_mentions, "get_async_session_maker", lambda: factory)
    monkeypatch.setattr(assistant_mentions.asyncio, "sleep", AsyncMock())
    return entered


@pytest.mark.asyncio
async def test_assistant_work_is_persisted_before_provider_processing(monkeypatch) -> None:
    """A process restart after enqueue can recover the request from PostgreSQL."""
    saved = []

    class Session:
        async def scalar(self, _statement):
            return None

        def add(self, job):
            saved.append(job)

        async def commit(self):
            return None

        async def refresh(self, job):
            job.id = "job-1"

    @asynccontextmanager
    async def factory():
        yield Session()

    run_job = AsyncMock()
    monkeypatch.setattr(assistant_mentions, "get_async_session_maker", lambda: factory)
    monkeypatch.setattr(assistant_mentions, "_run_durable_job", run_job)

    publisher = object()
    await assistant_mentions._persist_and_process(
        message_id="message-1",
        conversation_id="conversation-1",
        requester_id="user-1",
        request_text="Tạo lịch họp lúc 10 giờ tối mai",
        publisher=publisher,
        trusted_timezone="Asia/Ho_Chi_Minh",
    )

    assert len(saved) == 1
    assert saved[0].message_id == "message-1"
    assert saved[0].trusted_timezone == "Asia/Ho_Chi_Minh"
    run_job.assert_awaited_once_with("job-1", publisher)


@pytest.mark.asyncio
async def test_explicit_assistant_mention_runs_agent_and_notifies_requester(
    monkeypatch, session_context
) -> None:
    captured: list[tuple[str, dict]] = []

    class Publisher:
        async def send_to_user(self, user_id, event):
            captured.append((user_id, event))

    run = AsyncMock(
        return_value=AssistantRunResult(
            reply="Mình tìm thấy 1 việc",
            proposals=[{"id": "proposal-1"}],
            thread_id="t-1",
        )
    )
    monkeypatch.setattr(assistant_mentions, "has_consent", AsyncMock(return_value=True))
    monkeypatch.setattr(assistant_mentions.AssistantAgentService, "run", run)

    await assistant_mentions._process(
        message_id="message-1",
        conversation_id="conversation-1",
        requester_id="user-1",
        request_text="@assistant nhắc mình việc này",
        publisher=Publisher(),
    )

    assert len(session_context) == 1
    assert run.await_args.kwargs == {
        "conversation_id": "conversation-1",
        "user_id": "user-1",
        "request_text": "@assistant nhắc mình việc này",
        "source_message_id": "message-1",
    }
    assert captured == [
        ("user-1", {"type": "action_proposal_created", "proposal": {"id": "proposal-1"}})
    ]


@pytest.mark.asyncio
async def test_explicit_calendar_mention_extracts_a_proposal_without_waiting_for_planner(
    monkeypatch, session_context
) -> None:
    """A direct calendar request takes the short deterministic proposal path."""
    captured: list[tuple[str, dict]] = []

    class Publisher:
        async def send_to_user(self, user_id, event):
            captured.append((user_id, event))

    proposal = type(
        "Proposal",
        (), {"model_dump": lambda self, **_: {"id": "calendar-1", "title": "Project meeting"}},
    )()
    extract = AsyncMock(return_value=[proposal])
    run = AsyncMock()
    monkeypatch.setattr(assistant_mentions, "has_consent", AsyncMock(return_value=True))
    monkeypatch.setattr(
        assistant_mentions.ConversationIntelligenceService,
        "extract_actions_from_message",
        extract,
    )
    monkeypatch.setattr(assistant_mentions.AssistantAgentService, "run", run)

    await assistant_mentions._process(
        message_id="message-1",
        conversation_id="conversation-1",
        requester_id="user-1",
        request_text="Tạo lịch họp lúc 10 giờ tối mai",
        publisher=Publisher(),
    )

    assert extract.await_args.kwargs == {
        "conversation_id": "conversation-1",
        "message_id": "message-1",
        "user_id": "user-1",
        "db": session_context[0],
    }
    assistant_mentions.asyncio.sleep.assert_awaited_once_with(
        assistant_mentions.CALENDAR_CONTEXT_SETTLE_SECONDS
    )
    run.assert_not_awaited()
    assert captured == [
        ("user-1", {"type": "action_proposal_created", "proposal": {"id": "calendar-1", "title": "Project meeting"}})
    ]


@pytest.mark.asyncio
async def test_explicit_calendar_mention_uses_the_requesters_browser_timezone(
    monkeypatch, session_context
) -> None:
    """Relative dates can be resolved before the review step with explicit user input."""
    extract = AsyncMock(return_value=[])
    run = AsyncMock(return_value=AssistantRunResult(reply="", proposals=[]))
    monkeypatch.setattr(assistant_mentions, "has_consent", AsyncMock(return_value=True))
    monkeypatch.setattr(
        assistant_mentions.ConversationIntelligenceService,
        "extract_actions_from_message",
        extract,
    )
    monkeypatch.setattr(assistant_mentions.AssistantAgentService, "run", run)

    await assistant_mentions._process(
        message_id="message-1",
        conversation_id="conversation-1",
        requester_id="user-1",
        request_text="Tạo lịch họp lúc 10 giờ tối mai",
        publisher=object(),
        trusted_timezone="Asia/Ho_Chi_Minh",
    )

    assert extract.await_args.kwargs["trusted_timezone"] == "Asia/Ho_Chi_Minh"


@pytest.mark.asyncio
async def test_calendar_amendment_updates_the_latest_undecided_proposal(
    monkeypatch, session_context
) -> None:
    """A newer explicit time corrects the existing card instead of creating another."""
    extract = AsyncMock(return_value=[])
    monkeypatch.setattr(assistant_mentions, "has_consent", AsyncMock(return_value=True))
    monkeypatch.setattr(
        assistant_mentions.ConversationIntelligenceService,
        "extract_actions_from_message",
        extract,
    )
    monkeypatch.setattr(
        assistant_mentions.AssistantAgentService,
        "run",
        AsyncMock(return_value=AssistantRunResult(reply="", proposals=[])),
    )
    monkeypatch.setattr(assistant_mentions.ChatService, "create_assistant_notice", AsyncMock())

    await assistant_mentions._process(
        message_id="message-2",
        conversation_id="conversation-1",
        requester_id="user-1",
        request_text="Đổi lịch họp sang 10 giờ tối mai",
        publisher=object(),
    )

    assert extract.await_args.kwargs["update_latest_appointment"] is True


@pytest.mark.asyncio
async def test_calendar_request_without_a_new_proposal_explains_the_outcome(
    monkeypatch, session_context
) -> None:
    """A duplicate or unextractable appointment must not leave a vague status reply."""
    captured: list[tuple[str, dict]] = []

    class Publisher:
        async def send_to_user(self, user_id, event):
            captured.append((user_id, event))

    notice_message = {
        "id": "notice-1",
        "conversation_id": "conversation-1",
        "sender_id": "user-1",
        "original_text": "Không có lịch hẹn mới để đề xuất.",
        "message_type": "text",
        "created_at": datetime.now(UTC).isoformat(),
        "visibility": "private",
    }
    monkeypatch.setattr(assistant_mentions, "has_consent", AsyncMock(return_value=True))
    monkeypatch.setattr(
        assistant_mentions.ConversationIntelligenceService,
        "extract_actions_from_message",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        assistant_mentions.AssistantAgentService,
        "run",
        AsyncMock(return_value=AssistantRunResult(reply="", proposals=[])),
    )
    create_notice = AsyncMock(return_value=SimpleNamespace(message=notice_message))
    monkeypatch.setattr(assistant_mentions.ChatService, "create_assistant_notice", create_notice)

    await assistant_mentions._process(
        message_id="message-1",
        conversation_id="conversation-1",
        requester_id="user-1",
        request_text="Tạo lịch họp lúc 10 giờ tối mai",
        publisher=Publisher(),
    )

    assert create_notice.await_args.kwargs["idempotency_key"] == "assistant:calendar-no-new-proposal:message-1"
    assert captured[0][0] == "user-1"
    assert captured[0][1]["type"] == "message_received"
    assert "Không có lịch hẹn mới" in captured[0][1]["message"]["original_text"]


@pytest.mark.asyncio
async def test_assistant_mention_without_read_consent_publishes_nothing(
    monkeypatch, session_context
) -> None:
    """Tagging the assistant is an ordinary message until the user permits it."""
    captured: list[tuple[str, dict]] = []

    class Publisher:
        async def send_to_user(self, user_id, event):
            captured.append((user_id, event))

    run = AsyncMock()
    monkeypatch.setattr(assistant_mentions, "has_consent", AsyncMock(return_value=False))
    monkeypatch.setattr(assistant_mentions.AssistantAgentService, "run", run)

    await assistant_mentions._process(
        message_id="message-1",
        conversation_id="conversation-1",
        requester_id="user-1",
        request_text="@assistant tóm tắt giúp mình",
        publisher=Publisher(),
    )

    run.assert_not_awaited()
    assert captured == []


@pytest.mark.asyncio
async def test_a_failing_agent_run_never_escapes_into_the_send_path(
    monkeypatch, session_context
) -> None:
    """This runs detached from a delivered message; it may not raise onward."""

    class Publisher:
        async def send_to_user(self, user_id, event):
            raise AssertionError("nothing should be published after a failed run")

    monkeypatch.setattr(assistant_mentions, "has_consent", AsyncMock(return_value=True))
    monkeypatch.setattr(
        assistant_mentions.AssistantAgentService,
        "run",
        AsyncMock(side_effect=RuntimeError("provider down")),
    )

    await assistant_mentions._process(
        message_id="message-1",
        conversation_id="conversation-1",
        requester_id="user-1",
        request_text="@assistant giúp mình",
        publisher=Publisher(),
    )


@pytest.mark.asyncio
async def test_an_offline_socket_does_not_lose_the_persisted_proposals(
    monkeypatch, session_context
) -> None:
    """The rows are already written; a failed notification is not a failed run."""

    class Publisher:
        async def send_to_user(self, user_id, event):
            raise RuntimeError("offline websocket")

    monkeypatch.setattr(assistant_mentions, "has_consent", AsyncMock(return_value=True))
    monkeypatch.setattr(
        assistant_mentions.AssistantAgentService,
        "run",
        AsyncMock(return_value=AssistantRunResult(reply="", proposals=[{"id": "p-1"}])),
    )

    await assistant_mentions._process(
        message_id="message-1",
        conversation_id="conversation-1",
        requester_id="user-1",
        request_text="@assistant nhắc mình",
        publisher=Publisher(),
    )
