"""Startup recovery reschedules durable pending voice messages."""

import pytest

from src.services.voice import voice_transcription


class Result:
    def all(self):
        return [("message-1", "conversation-1"), ("message-2", "conversation-2")]


class Session:
    async def execute(self, _statement):
        return Result()

    async def rollback(self):
        return None


class SessionContext:
    async def __aenter__(self):
        return Session()

    async def __aexit__(self, *_args):
        return None


@pytest.mark.asyncio
async def test_startup_recovery_schedules_every_pending_row_after_scan(monkeypatch):
    scheduled = []

    def factory():
        return SessionContext()

    def record(**kwargs):
        scheduled.append(kwargs)

    monkeypatch.setattr(voice_transcription, "schedule_voice_transcription", record)
    publisher = object()

    count = await voice_transcription.recover_pending_voice_transcriptions(
        publisher=publisher,
        session_factory=factory,
    )

    assert count == 2
    assert [(item["message_id"], item["conversation_id"]) for item in scheduled] == [
        ("message-1", "conversation-1"),
        ("message-2", "conversation-2"),
    ]
    assert all(item["publisher"] is publisher for item in scheduled)
