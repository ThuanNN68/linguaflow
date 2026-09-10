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
    return entered


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
