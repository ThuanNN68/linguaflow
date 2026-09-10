"""Phase 4 handoff tests: completed voice becomes ordinary canonical text."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from src.database.models import (
    Attachment,
    ConversationProfile,
    GlossaryEntry,
    Message,
    ParticipantProfile,
    TranslationResult,
    UserSettings,
)
from src.services.language import translation as translation_service
from src.services.language.customization import DatabaseCustomizationProvider
from src.services.language.glossary import normalize_term
from src.services.language.translation import schedule_translations
from src.services.messaging.context_provider import DatabaseContextProvider
from src.services.messaging.message_postprocessing import schedule_text_dependent_work
from tests import conftest as test_support

FULL_TRANSCRIPT = (
    "Xin hãy giữ nguyên toàn bộ nội dung này.  Mã đơn là A-017 và tổng tiền là 1.250.000 đồng.\n"
    "Đừng bỏ câu cuối: tôi sẽ giao bản đầy đủ vào thứ Sáu."
)
BASE_TIME = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)


class RecordingPublisher:
    def __init__(self) -> None:
        self.sent: list[tuple[tuple[str, ...], dict]] = []

    async def send_to_users(self, user_ids, payload) -> None:
        self.sent.append((tuple(user_ids), dict(payload)))


async def _persist_message(
    session,
    *,
    conversation_id: str,
    sender_id: str,
    client_message_id: str,
    original_text: str,
    minute: int,
    message_type: str = "text",
    transcription_status: str | None = None,
    source_language: str = "vi",
) -> Message:
    message = Message(
        client_message_id=client_message_id,
        conversation_id=conversation_id,
        sender_id=sender_id,
        original_text=original_text,
        message_type=message_type,
        transcription_status=transcription_status,
        source_language=source_language,
        created_at=BASE_TIME + timedelta(minutes=minute),
    )
    session.add(message)
    await session.commit()
    await session.refresh(message)
    return message


def _recording_schedulers():
    calls: list[tuple[str, dict]] = []

    def record(name):
        def recorder(**kwargs):
            calls.append((name, kwargs))

        return recorder

    return calls, {
        "translation_scheduler": record("translation"),
        "commitment_scheduler": record("commitment"),
        "profile_scheduler": record("profile"),
        "embedding_scheduler": record("embedding"),
    }


def test_completed_voice_schedules_the_existing_four_text_dependent_services():
    message = Message(
        id="voice-completed",
        client_message_id="voice-client",
        conversation_id="conversation-id",
        sender_id="sender-id",
        original_text=FULL_TRANSCRIPT,
        message_type="voice",
        transcription_status="completed",
        source_language="vi",
    )
    publisher = object()
    calls, schedulers = _recording_schedulers()

    schedule_text_dependent_work(
        message=message,
        publisher=publisher,
        **schedulers,
    )

    assert [name for name, _ in calls] == [
        "translation",
        "commitment",
        "profile",
        "embedding",
    ]
    assert calls[0][1] == {"message": message, "publisher": publisher}
    assert calls[1][1] == {
        "message_id": message.id,
        "conversation_id": message.conversation_id,
        "sender_id": message.sender_id,
        "publisher": publisher,
    }
    assert calls[2][1] == {"conversation_id": message.conversation_id}
    assert calls[3][1] == {
        "message_id": message.id,
        "conversation_id": message.conversation_id,
        "text": FULL_TRANSCRIPT,
        # Whose `store_memory` consent decides whether the transcript is
        # remembered. Without it `schedule_message_embedding` falls back to the
        # RAG flag alone, so an account that granted memory would be embedded
        # never and nothing would report it (ADR-30).
        "sender_id": message.sender_id,
    }


def test_pending_and_failed_voice_never_schedule_text_dependent_work():
    for status in ("pending", "failed"):
        calls, schedulers = _recording_schedulers()
        message = Message(
            id=f"voice-{status}",
            client_message_id=f"voice-client-{status}",
            conversation_id="conversation-id",
            sender_id="sender-id",
            original_text="",
            message_type="voice",
            transcription_status=status,
            source_language="vi",
        )

        schedule_text_dependent_work(
            message=message,
            publisher=object(),
            **schedulers,
        )

        assert calls == []


def test_shared_scheduler_preserves_the_normal_text_handoff():
    message = Message(
        id="text-message",
        client_message_id="text-client",
        conversation_id="conversation-id",
        sender_id="sender-id",
        original_text="Ordinary text remains unchanged.",
        message_type="text",
        transcription_status=None,
        source_language="en",
    )
    calls, schedulers = _recording_schedulers()

    schedule_text_dependent_work(
        message=message,
        publisher=object(),
        **schedulers,
    )

    assert [name for name, _ in calls] == [
        "translation",
        "commitment",
        "profile",
        "embedding",
    ]
    assert calls[0][1]["message"] is message
    assert calls[3][1]["text"] == message.original_text


@pytest.mark.asyncio
async def test_text_and_voice_share_original_text_context_without_audio_metadata(
    test_db,
    test_user,
    test_user_two,
    conversation_factory,
):
    conversation = await conversation_factory(test_user, [test_user_two])
    prior_text = await _persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        client_message_id="context-text-1",
        original_text="Atlas means the Friday release in this conversation.",
        minute=1,
        source_language="en",
    )
    first_voice = await _persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        client_message_id="context-voice-1",
        original_text="Atlas phải giữ nguyên tất cả các bước kiểm thử.",
        minute=2,
        message_type="voice",
        transcription_status="completed",
    )
    test_db.add(
        Attachment(
            id="audio-context-marker.webm",
            conversation_id=conversation.id,
            message_id=first_voice.id,
            uploader_id=test_user_two.id,
            filename="AUDIO_METADATA_MUST_NOT_REACH_CONTEXT.webm",
            content_type="audio/webm; codecs=opus",
            size=999,
        )
    )
    await test_db.commit()

    voice_context = await DatabaseContextProvider(
        test_db,
        before_message_id=first_voice.id,
    ).get_recent_messages(conversation.id)
    assert [line.split(": ", 1)[1] for line in voice_context] == [prior_text.original_text]

    await _persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        client_message_id="context-voice-pending",
        original_text="",
        minute=3,
        message_type="voice",
        transcription_status="pending",
    )
    await _persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        client_message_id="context-voice-failed",
        original_text="",
        minute=4,
        message_type="voice",
        transcription_status="failed",
    )
    later_text = await _persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        client_message_id="context-text-later",
        original_text="Does it still apply?",
        minute=5,
        source_language="en",
    )

    later_text_context = await DatabaseContextProvider(
        test_db,
        before_message_id=later_text.id,
    ).get_recent_messages(conversation.id)
    later_text_values = [line.split(": ", 1)[1] for line in later_text_context]
    assert later_text_values == [prior_text.original_text, first_voice.original_text]
    assert "AUDIO_METADATA_MUST_NOT_REACH_CONTEXT" not in repr(later_text_context)
    assert "audio/webm" not in repr(later_text_context)

    later_voice = await _persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        client_message_id="context-voice-later",
        original_text="Nó vẫn áp dụng cho bản phát hành tiếp theo phải không?",
        minute=6,
        message_type="voice",
        transcription_status="completed",
    )
    later_voice_context = await DatabaseContextProvider(
        test_db,
        before_message_id=later_voice.id,
    ).get_recent_messages(conversation.id)
    later_voice_values = [line.split(": ", 1)[1] for line in later_voice_context]
    assert first_voice.original_text in later_voice_values
    assert later_text.original_text in later_voice_values
    assert "" not in later_voice_values


@pytest.mark.asyncio
async def test_completed_voice_uses_existing_translation_context_buckets_persistence_and_event(
    test_db,
    test_user,
    test_user_two,
    conversation_factory,
):
    conversation = await conversation_factory(test_user_two, [test_user])
    prior = await _persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        client_message_id="translation-prior-text",
        original_text="Atlas refers to the full Friday deployment package.",
        minute=1,
        source_language="en",
    )
    voice = await _persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        client_message_id="translation-voice",
        original_text=FULL_TRANSCRIPT,
        minute=2,
        message_type="voice",
        transcription_status="completed",
        source_language="vi",
    )
    test_db.add_all(
        [
            ParticipantProfile(
                conversation_id=conversation.id,
                user_id=test_user.id,
                honorific_profile="client",
                inferred_by="llm",
            ),
            ParticipantProfile(
                conversation_id=conversation.id,
                user_id=test_user_two.id,
                honorific_profile="junior",
                inferred_by="llm",
            ),
            UserSettings(user_id=test_user.id, translation_tone="formal"),
        ]
    )
    await test_db.commit()

    captured: list[dict] = []

    def graph_factory(session, message_id):
        class ExistingPathGraph:
            async def ainvoke(self, state, config=None):
                context = await DatabaseContextProvider(
                    session,
                    before_message_id=message_id,
                ).get_recent_messages(state["conversation_id"])
                captured.append({"state": dict(state), "context": context})
                if state["target_language"] == "en":
                    return {
                        **state,
                        "source_language": "vi",
                        "translated_text": (
                            "Please preserve all of this content.  The order code is A-017 and the total is "
                            "1.250.000 đồng.\nDo not omit the last sentence: I will deliver the complete version "
                            "on Friday."
                        ),
                        "model": "existing-translation-graph-fake",
                        "latency_ms": 31,
                        "is_fallback": False,
                    }
                return {
                    **state,
                    "source_language": "vi",
                    "translated_text": state["original_text"],
                    "model": "passthrough",
                    "latency_ms": 1,
                    "is_fallback": False,
                }

        return ExistingPathGraph()

    publisher = RecordingPublisher()
    schedule_translations(
        message=voice,
        publisher=publisher,
        session_factory=test_support.test_async_session_maker,
        graph_factory=graph_factory,
    )
    await asyncio.gather(*tuple(translation_service._BACKGROUND_TASKS))

    english_calls = [call for call in captured if call["state"]["target_language"] == "en"]
    assert len(english_calls) == 1
    english = english_calls[0]
    assert english["state"]["original_text"] == FULL_TRANSCRIPT
    assert english["state"]["message_id"] == voice.id
    assert english["state"]["honorific_profile"] == "client"
    assert english["state"]["translation_tone"] == "formal"
    assert [line.split(": ", 1)[1] for line in english["context"]] == [prior.original_text]

    async with test_support.test_async_session_maker() as session:
        translations = (
            await session.scalars(select(TranslationResult).where(TranslationResult.message_id == voice.id))
        ).all()
        stored_voice = await session.get(Message, voice.id)
    assert len(translations) == 1
    assert translations[0].target_language == test_user.preferred_language
    assert translations[0].honorific_profile == "client"
    assert translations[0].translation_tone == "formal"
    assert stored_voice.original_text == FULL_TRANSCRIPT
    assert stored_voice.transcription_status == "completed"

    completed_events = [payload for _, payload in publisher.sent]
    assert len(completed_events) == 1
    assert completed_events[0]["type"] == "translation_completed"
    assert completed_events[0]["message_id"] == voice.id
    assert completed_events[0]["translation_id"] == translations[0].id
    assert completed_events[0]["translated_text"] == translations[0].translated_text
    assert set(publisher.sent[0][0]) == {test_user.id, test_user_two.id}
    assert not any(row.target_language == test_user_two.preferred_language for row in translations)


@pytest.mark.asyncio
async def test_voice_transcript_uses_existing_customization_and_glossary_lookup(
    test_db,
    test_user,
    test_user_two,
    conversation_factory,
):
    conversation = await conversation_factory(test_user, [test_user_two])
    voice = await _persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        client_message_id="voice-glossary",
        original_text="Please review the UI and keep every instruction.",
        minute=1,
        message_type="voice",
        transcription_status="completed",
        source_language="en",
    )
    test_db.add_all(
        [
            ConversationProfile(
                conversation_id=conversation.id,
                domain="software",
                audience="client",
            ),
            GlossaryEntry(
                source_term="UI",
                source_term_normalized=normalize_term("UI"),
                target_term="giao diện",
                source_language="en",
                target_language="vi",
                domain="software",
                audience="client",
                status="active",
            ),
        ]
    )
    await test_db.commit()

    customization = await DatabaseCustomizationProvider(test_db).get_customization(
        conversation.id,
        original_text=voice.original_text,
        source_language="en",
        target_language="vi",
    )

    assert customization.domain == "software"
    assert customization.audience == "client"
    assert [(term.source_term, term.target_term) for term in customization.glossary_terms] == [("UI", "giao diện")]


@pytest.mark.asyncio
async def test_default_translation_factory_uses_existing_context_and_customization_providers(
    test_db,
    test_user,
    test_user_two,
    conversation_factory,
    monkeypatch,
):
    conversation = await conversation_factory(test_user, [test_user_two])
    voice = await _persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        client_message_id="voice-default-graph",
        original_text=FULL_TRANSCRIPT,
        minute=1,
        message_type="voice",
        transcription_status="completed",
    )
    captured = {}
    sentinel = object()

    def build(context_provider, *, customization_provider):
        captured["context"] = context_provider
        captured["customization"] = customization_provider
        return sentinel

    monkeypatch.setattr(translation_service, "build_translation_graph", build)

    graph = translation_service._default_graph_factory(test_db, voice.id)

    assert graph is sentinel
    assert isinstance(captured["context"], DatabaseContextProvider)
    assert captured["context"]._before_message_id == voice.id
    assert isinstance(captured["customization"], DatabaseCustomizationProvider)


@pytest.mark.asyncio
async def test_existing_translation_failure_preserves_completed_voice_and_audio(
    test_db,
    test_user,
    test_user_two,
    conversation_factory,
):
    conversation = await conversation_factory(test_user, [test_user_two])
    voice = await _persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        client_message_id="voice-translation-failure",
        original_text=FULL_TRANSCRIPT,
        minute=1,
        message_type="voice",
        transcription_status="completed",
        source_language="vi",
    )
    attachment = Attachment(
        id="voice-translation-failure.webm",
        conversation_id=conversation.id,
        message_id=voice.id,
        uploader_id=test_user.id,
        filename="voice.webm",
        content_type="audio/webm",
        size=321,
    )
    test_db.add(attachment)
    await test_db.commit()

    def failing_graph_factory(_session, _message_id):
        class FailingGraph:
            async def ainvoke(self, state, config=None):
                raise RuntimeError("mock translation failure")

        return FailingGraph()

    publisher = RecordingPublisher()
    schedule_translations(
        message=voice,
        publisher=publisher,
        session_factory=test_support.test_async_session_maker,
        graph_factory=failing_graph_factory,
    )
    await asyncio.gather(*tuple(translation_service._BACKGROUND_TASKS))

    async with test_support.test_async_session_maker() as session:
        stored = await session.get(Message, voice.id)
        stored_attachment = await session.get(Attachment, attachment.id)
        translations = (
            await session.scalars(select(TranslationResult).where(TranslationResult.message_id == voice.id))
        ).all()
    assert stored.message_type == "voice"
    assert stored.transcription_status == "completed"
    assert stored.original_text == FULL_TRANSCRIPT
    assert stored_attachment.message_id == voice.id
    assert translations == []
    assert publisher.sent == []
