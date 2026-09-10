"""Tests for guarded voice transcription and the Phase 4 handoff boundary."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select, update

from src.database.models import Attachment, Message
from src.services.messaging.chat import ChatService
from src.services.voice.transcription import (
    TranscriptionProviderError,
    TranscriptionResult,
    TranscriptionTimeoutError,
)
from src.services.voice.voice_transcription import (
    _run_voice_transcription_guarded,
    transcribe_voice_message,
)
from tests import conftest as test_support

FULL_TRANSCRIPT = (
    "Xin chào.  Tôi cần giữ nguyên mọi chi tiết.\nMã là A-017, số tiền 1.250.000 đồng, và đừng bỏ câu cuối."
)


async def _pending_voice(test_db, test_user, test_user_two, conversation_factory):
    conversation = await conversation_factory(test_user, [test_user_two])
    attachment = Attachment(
        id=f"voice-{conversation.id}.webm",
        conversation_id=conversation.id,
        uploader_id=test_user.id,
        filename="recording.webm",
        content_type="audio/webm; codecs=opus",
        size=321,
    )
    test_db.add(attachment)
    await test_db.commit()
    result = await ChatService(test_db).send_voice_message(
        sender_id=test_user.id,
        conversation_id=conversation.id,
        client_message_id=f"client-{conversation.id}",
        attachment_id=attachment.id,
    )
    return conversation, attachment, result.message


class FakeTranscriptionService:
    def __init__(self, *, result=None, error=None, before_return=None) -> None:
        self.result = result or TranscriptionResult(
            text=FULL_TRANSCRIPT,
            detected_language="vi",
            model="fake-transcribe",
            latency_ms=7,
        )
        self.error = error
        self.before_return = before_return
        self.calls: list[str] = []
        self.language_hints: list[str | None] = []

    async def transcribe_attachment(
        self, attachment: Attachment, language_hint: str | None = None
    ) -> TranscriptionResult:
        self.calls.append(attachment.id)
        self.language_hints.append(language_hint)
        if self.before_return is not None:
            await self.before_return()
        if self.error is not None:
            raise self.error
        return self.result


class DurablePublisher:
    """Record events and independently observe DB state at publication time."""

    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory
        self.deliveries: list[tuple[tuple[str, ...], dict, tuple[str, str]]] = []

    async def send_to_users(self, user_ids, event) -> None:
        async with self.session_factory() as session:
            message = await session.get(Message, event["message_id"])
            durable_state = (message.transcription_status, message.original_text)
        self.deliveries.append((tuple(user_ids), dict(event), durable_state))


class TrackingSessionFactory:
    def __init__(self, base_factory) -> None:
        self.base_factory = base_factory
        self.sessions = []

    def __call__(self):
        session = self.base_factory()
        self.sessions.append(session)
        return session


class RecordingPostprocessor:
    """Observe the canonical reloaded message and event-before-work ordering."""

    def __init__(self, publisher) -> None:
        self.publisher = publisher
        self.calls: list[dict] = []

    def __call__(self, *, message, publisher) -> None:
        assert publisher is self.publisher
        self.calls.append(
            {
                "message_id": message.id,
                "message_type": message.message_type,
                "transcription_status": message.transcription_status,
                "original_text": message.original_text,
                "events_seen": [delivery[1]["type"] for delivery in publisher.deliveries],
            }
        )


@pytest.mark.asyncio
async def test_success_uses_fresh_sessions_persists_full_transcript_then_publishes(
    test_db,
    test_user,
    test_user_two,
    conversation_factory,
):
    conversation, attachment, message = await _pending_voice(test_db, test_user, test_user_two, conversation_factory)
    base_factory = test_support.test_async_session_maker
    tracking_factory = TrackingSessionFactory(base_factory)
    publisher = DurablePublisher(base_factory)
    postprocessor = RecordingPostprocessor(publisher)
    transcription = FakeTranscriptionService()

    await transcribe_voice_message(
        message_id=message.id,
        conversation_id=conversation.id,
        publisher=publisher,
        session_factory=tracking_factory,
        transcription_service_factory=lambda: transcription,
        postprocessing_scheduler=postprocessor,
    )

    assert len(tracking_factory.sessions) == 3
    assert all(session is not test_db for session in tracking_factory.sessions)
    assert transcription.calls == [attachment.id]
    assert len(publisher.deliveries) == 1
    user_ids, event, durable_state = publisher.deliveries[0]
    assert set(user_ids) == {test_user.id, test_user_two.id}
    assert event == {
        "type": "voice_transcription_completed",
        "message_id": message.id,
        "conversation_id": conversation.id,
        "original_text": FULL_TRANSCRIPT,
        "source_language": test_user.preferred_language,
        "transcription_status": "completed",
    }
    assert durable_state == ("completed", FULL_TRANSCRIPT)
    assert postprocessor.calls == [
        {
            "message_id": message.id,
            "message_type": "voice",
            "transcription_status": "completed",
            "original_text": FULL_TRANSCRIPT,
            "events_seen": ["voice_transcription_completed"],
        }
    ]

    async with base_factory() as session:
        stored = await session.get(Message, message.id)
        stored_attachment = await session.get(Attachment, attachment.id)
        assert stored.message_type == "voice"
        assert stored.original_text == FULL_TRANSCRIPT
        assert stored.transcription_status == "completed"
        assert stored_attachment.message_id == message.id
        assert (
            await session.scalar(
                select(func.count()).select_from(Message).where(Message.conversation_id == conversation.id)
            )
            == 1
        )


@pytest.mark.asyncio
async def test_controlled_stt_failure_is_durable_non_destructive_and_retry_safe(
    test_db,
    test_user,
    test_user_two,
    conversation_factory,
    caplog,
):
    conversation, attachment, message = await _pending_voice(test_db, test_user, test_user_two, conversation_factory)
    factory = test_support.test_async_session_maker
    publisher = DurablePublisher(factory)
    postprocessor = RecordingPostprocessor(publisher)
    transcription = FakeTranscriptionService(error=TranscriptionTimeoutError("raw provider timeout detail"))

    await transcribe_voice_message(
        message_id=message.id,
        conversation_id=conversation.id,
        publisher=publisher,
        session_factory=factory,
        transcription_service_factory=lambda: transcription,
        postprocessing_scheduler=postprocessor,
    )

    assert len(publisher.deliveries) == 1
    _, event, durable_state = publisher.deliveries[0]
    assert event == {
        "type": "voice_transcription_failed",
        "message_id": message.id,
        "conversation_id": conversation.id,
        "transcription_status": "failed",
        "retryable": True,
    }
    assert durable_state == ("failed", "")
    assert "raw provider timeout detail" not in caplog.text
    assert "voice_stt_failed" in caplog.text
    assert "failure_code=provider_timeout" in caplog.text
    assert "stage=provider" in caplog.text
    assert "retryable=True" in caplog.text
    assert postprocessor.calls == []
    async with factory() as session:
        stored_attachment = await session.get(Attachment, attachment.id)
        assert stored_attachment.message_id == message.id


@pytest.mark.asyncio
async def test_provider_failure_event_does_not_expose_provider_or_audio_content(
    test_db,
    test_user,
    test_user_two,
    conversation_factory,
    caplog,
):
    conversation, _, message = await _pending_voice(test_db, test_user, test_user_two, conversation_factory)
    factory = test_support.test_async_session_maker
    publisher = DurablePublisher(factory)
    postprocessor = RecordingPostprocessor(publisher)
    transcription = FakeTranscriptionService(
        error=TranscriptionProviderError("raw-provider-body secret-audio transcript-words")
    )

    await transcribe_voice_message(
        message_id=message.id,
        conversation_id=conversation.id,
        publisher=publisher,
        session_factory=factory,
        transcription_service_factory=lambda: transcription,
        postprocessing_scheduler=postprocessor,
    )

    serialized = repr(publisher.deliveries)
    assert "raw-provider-body" not in serialized
    assert "secret-audio" not in serialized
    assert "transcript-words" not in serialized
    assert "raw-provider-body" not in caplog.text
    assert postprocessor.calls == []


@pytest.mark.asyncio
async def test_permanent_provider_failure_is_not_marked_retryable(
    test_db,
    test_user,
    test_user_two,
    conversation_factory,
    caplog,
):
    conversation, _, message = await _pending_voice(
        test_db, test_user, test_user_two, conversation_factory
    )
    factory = test_support.test_async_session_maker
    publisher = DurablePublisher(factory)
    transcription = FakeTranscriptionService(
        error=TranscriptionProviderError(
            "secret provider body",
            failure_code="provider_authentication_failed",
            retryable=False,
            stage="upload",
            http_status=403,
        )
    )

    await transcribe_voice_message(
        message_id=message.id,
        conversation_id=conversation.id,
        publisher=publisher,
        session_factory=factory,
        transcription_service_factory=lambda: transcription,
        postprocessing_scheduler=RecordingPostprocessor(publisher),
    )

    assert publisher.deliveries[0][1]["retryable"] is False
    assert "failure_code=provider_authentication_failed" in caplog.text
    assert "http_status=403" in caplog.text
    assert "secret provider body" not in caplog.text


@pytest.mark.asyncio
async def test_guarded_task_turns_unexpected_error_into_safe_retryable_failure(
    test_db,
    test_user,
    test_user_two,
    conversation_factory,
    caplog,
):
    conversation, _, message = await _pending_voice(
        test_db, test_user, test_user_two, conversation_factory
    )
    factory = test_support.test_async_session_maker
    publisher = DurablePublisher(factory)
    transcription = FakeTranscriptionService(
        error=RuntimeError("secret unexpected audio/provider details")
    )

    await _run_voice_transcription_guarded(
        message_id=message.id,
        conversation_id=conversation.id,
        publisher=publisher,
        session_factory=factory,
        transcription_service_factory=lambda: transcription,
        postprocessing_scheduler=RecordingPostprocessor(publisher),
    )

    assert publisher.deliveries[0][1]["retryable"] is True
    async with factory() as session:
        stored = await session.get(Message, message.id)
        assert stored.transcription_status == "failed"
        assert stored.original_text == ""
    assert "failure_code=unexpected_error" in caplog.text
    assert "exception_type=RuntimeError" in caplog.text
    assert "secret unexpected" not in caplog.text


@pytest.mark.asyncio
async def test_stale_result_cannot_overwrite_or_publish_after_pending_state_changes(
    test_db,
    test_user,
    test_user_two,
    conversation_factory,
):
    conversation, _, message = await _pending_voice(test_db, test_user, test_user_two, conversation_factory)
    factory = test_support.test_async_session_maker
    publisher = DurablePublisher(factory)
    postprocessor = RecordingPostprocessor(publisher)

    async def mark_failed_before_provider_returns():
        async with factory() as session:
            await session.execute(
                update(Message).where(Message.id == message.id).values(transcription_status="failed", original_text="")
            )
            await session.commit()

    transcription = FakeTranscriptionService(before_return=mark_failed_before_provider_returns)

    await transcribe_voice_message(
        message_id=message.id,
        conversation_id=conversation.id,
        publisher=publisher,
        session_factory=factory,
        transcription_service_factory=lambda: transcription,
        postprocessing_scheduler=postprocessor,
    )

    assert publisher.deliveries == []
    assert postprocessor.calls == []
    async with factory() as session:
        stored = await session.get(Message, message.id)
        assert stored.transcription_status == "failed"
        assert stored.original_text == ""


@pytest.mark.asyncio
async def test_duplicate_tasks_can_publish_only_one_terminal_transition(
    test_db,
    test_user,
    test_user_two,
    conversation_factory,
):
    conversation, _, message = await _pending_voice(test_db, test_user, test_user_two, conversation_factory)
    factory = test_support.test_async_session_maker
    publisher = DurablePublisher(factory)
    postprocessor = RecordingPostprocessor(publisher)
    both_started = asyncio.Event()

    class BarrierTranscriptionService(FakeTranscriptionService):
        async def transcribe_attachment(self, attachment, language_hint=None):
            self.calls.append(attachment.id)
            if len(self.calls) == 2:
                both_started.set()
            await both_started.wait()
            return self.result

    transcription = BarrierTranscriptionService()
    kwargs = {
        "message_id": message.id,
        "conversation_id": conversation.id,
        "publisher": publisher,
        "session_factory": factory,
        "transcription_service_factory": lambda: transcription,
        "postprocessing_scheduler": postprocessor,
    }

    await asyncio.gather(
        transcribe_voice_message(**kwargs),
        transcribe_voice_message(**kwargs),
    )

    assert len(transcription.calls) == 2
    assert len(publisher.deliveries) == 1
    assert publisher.deliveries[0][1]["type"] == "voice_transcription_completed"
    assert len(postprocessor.calls) == 1


@pytest.mark.asyncio
async def test_terminal_or_deleted_voice_message_is_not_reprocessed(
    test_db,
    test_user,
    test_user_two,
    conversation_factory,
):
    conversation, _, message = await _pending_voice(test_db, test_user, test_user_two, conversation_factory)
    message.transcription_status = "completed"
    message.original_text = FULL_TRANSCRIPT
    await test_db.commit()
    factory = test_support.test_async_session_maker
    publisher = DurablePublisher(factory)
    postprocessor = RecordingPostprocessor(publisher)
    transcription = FakeTranscriptionService()

    await transcribe_voice_message(
        message_id=message.id,
        conversation_id=conversation.id,
        publisher=publisher,
        session_factory=factory,
        transcription_service_factory=lambda: transcription,
        postprocessing_scheduler=postprocessor,
    )

    assert transcription.calls == []
    assert publisher.deliveries == []
    assert postprocessor.calls == []


@pytest.mark.asyncio
async def test_retry_success_reuses_message_and_attachment_then_runs_existing_handoff(
    test_db,
    test_user,
    test_user_two,
    conversation_factory,
):
    conversation, attachment, message = await _pending_voice(test_db, test_user, test_user_two, conversation_factory)
    message.transcription_status = "failed"
    await test_db.commit()

    retry = await ChatService(test_db).retry_voice_transcription(
        user_id=test_user_two.id,
        message_id=message.id,
    )
    factory = test_support.test_async_session_maker
    publisher = DurablePublisher(factory)
    postprocessor = RecordingPostprocessor(publisher)

    await transcribe_voice_message(
        message_id=retry.message_id,
        conversation_id=retry.conversation_id,
        publisher=publisher,
        session_factory=factory,
        transcription_service_factory=FakeTranscriptionService,
        postprocessing_scheduler=postprocessor,
    )

    assert retry.message_id == message.id
    assert retry.attachment_id == attachment.id
    assert len(publisher.deliveries) == 1
    assert publisher.deliveries[0][1]["type"] == "voice_transcription_completed"
    assert len(postprocessor.calls) == 1
    assert postprocessor.calls[0]["message_id"] == message.id
    assert postprocessor.calls[0]["original_text"] == FULL_TRANSCRIPT


@pytest.mark.asyncio
async def test_retry_provider_failure_returns_same_message_to_failed_without_handoff(
    test_db,
    test_user,
    test_user_two,
    conversation_factory,
):
    conversation, _, message = await _pending_voice(test_db, test_user, test_user_two, conversation_factory)
    message.transcription_status = "failed"
    await test_db.commit()
    retry = await ChatService(test_db).retry_voice_transcription(
        user_id=test_user.id,
        message_id=message.id,
    )
    factory = test_support.test_async_session_maker
    publisher = DurablePublisher(factory)
    postprocessor = RecordingPostprocessor(publisher)

    await transcribe_voice_message(
        message_id=retry.message_id,
        conversation_id=retry.conversation_id,
        publisher=publisher,
        session_factory=factory,
        transcription_service_factory=lambda: FakeTranscriptionService(
            error=TranscriptionTimeoutError("controlled timeout")
        ),
        postprocessing_scheduler=postprocessor,
    )

    async with factory() as session:
        stored = await session.get(Message, message.id)
        assert stored.transcription_status == "failed"
        assert stored.original_text == ""
    assert len(publisher.deliveries) == 1
    assert publisher.deliveries[0][1]["type"] == "voice_transcription_failed"
    assert postprocessor.calls == []


@pytest.mark.asyncio
async def test_delete_during_stt_suppresses_late_completion_event_and_downstream_work(
    test_db,
    test_user,
    test_user_two,
    conversation_factory,
):
    conversation, _, message = await _pending_voice(test_db, test_user, test_user_two, conversation_factory)
    factory = test_support.test_async_session_maker
    publisher = DurablePublisher(factory)
    postprocessor = RecordingPostprocessor(publisher)

    async def delete_before_provider_returns():
        async with factory() as session:
            await ChatService(session).delete_message(
                user_id=test_user.id,
                conversation_id=conversation.id,
                message_id=message.id,
            )

    await transcribe_voice_message(
        message_id=message.id,
        conversation_id=conversation.id,
        publisher=publisher,
        session_factory=factory,
        transcription_service_factory=lambda: FakeTranscriptionService(before_return=delete_before_provider_returns),
        postprocessing_scheduler=postprocessor,
    )

    async with factory() as session:
        stored = await session.get(Message, message.id)
        assert stored.deleted_at is not None
        assert stored.original_text == ""
        assert stored.transcription_status == "pending"
    assert publisher.deliveries == []
    assert postprocessor.calls == []
