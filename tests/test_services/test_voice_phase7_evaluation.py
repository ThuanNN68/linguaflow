"""Deterministic Phase 7 evaluation of voice transcript and translation fidelity.

These tests prove the application boundaries with controlled fixtures. They do
not measure live STT provider quality and deliberately spend no provider or
LLM quota.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass

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
from src.services.language import translation
from src.services.language.customization import DatabaseCustomizationProvider
from src.services.language.glossary import normalize_term
from src.services.language.translation import schedule_translations
from src.services.messaging import message_postprocessing
from src.services.messaging.attachment_storage import StoredAttachment
from src.services.messaging.context_provider import DatabaseContextProvider
from src.services.voice import transcription as transcription_service
from src.services.voice import voice_transcription
from src.services.voice.transcription import TranscriptionResult, TranscriptionService
from tests import conftest as test_support

FIDELITY_TRANSCRIPTS = (
    "Chào anh. Em đã kiểm tra xe và sẽ gọi lại sau khi có kết quả.",
    "Lịch hẹn là 14 giờ 35 ngày 29/08/2026; tổng tiền 1.250.000 đồng.",
    "Không thay lốp và đừng xóa lỗi trước khi kỹ thuật viên đọc mã.",
    "Chị Nguyễn Thùy Linh đã chốt VinFast VF 8 Eco màu Deep Ocean.",
    "Kiểm tra mô-tơ điện, bộ inverter, pin cao áp và hệ thống ADAS giúp anh.",
    "Xe chạy hơi cà giựt á, nhưng đừng lo, để em check lại rồi báo nha.",
    "Please update firmware trước 5 PM, rồi gửi diagnostic report qua email.",
    "Ừm, em... em đã kiểm tra VF 9—à không, VF 8; đừng, đừng xóa mã lỗi nhé.",
    "Mục một: giữ nguyên phụ tùng OEM.\nMục hai: gọi khách trước khi sửa; OK?",
    (
        "Sáng nay khách báo xe rung khi tăng tốc, nhưng không rung lúc chạy đều. "
        "Kỹ thuật viên cần đo điện áp pin 12V, đọc mã lỗi P0A80, chụp lại màn hình "
        "và gọi cho anh Minh trước 16 giờ. Nếu chưa xác định được nguyên nhân thì "
        "không thay linh kiện, không xóa mã lỗi và hẹn khách quay lại vào thứ Hai."
    ),
)


@dataclass
class _FixtureStorage:
    transcript: str

    async def read(self, attachment: Attachment) -> StoredAttachment:
        return StoredAttachment(
            data=b"phase-7-nonsensitive-audio-fixture",
            filename="phase7.ogg",
            content_type="audio/ogg; codecs=vorbis",
        )


class _FixtureProvider:
    def __init__(self, transcript: str) -> None:
        self.transcript = transcript
        self.calls: list[tuple[bytes, str, str]] = []

    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str,
        content_type: str,
        language_hint: str | None = None,
    ) -> TranscriptionResult:
        self.calls.append((audio_bytes, filename, content_type))
        return TranscriptionResult(
            text=self.transcript,
            detected_language="vi",
            model="deterministic-phase7-fixture",
            latency_ms=1,
        )


def _attachment() -> Attachment:
    return Attachment(
        id="phase7-audio",
        conversation_id="phase7-conversation",
        uploader_id="phase7-user",
        filename="phase7.ogg",
        content_type="audio/ogg",
        size=36,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("transcript", FIDELITY_TRANSCRIPTS)
async def test_stt_boundary_preserves_every_deterministic_fidelity_fixture(transcript):
    provider = _FixtureProvider(transcript)
    service = TranscriptionService(
        provider=provider,
        storage=_FixtureStorage(transcript),
        max_audio_size_bytes=1024,
    )

    result = await service.transcribe_attachment(_attachment())

    assert result.text == transcript
    assert result.detected_language == "vi"
    assert provider.calls == [(b"phase-7-nonsensitive-audio-fixture", "phase7.ogg", "audio/ogg")]


def test_voice_path_has_no_summary_or_voice_specific_translation_seam():
    voice_sources = "\n".join(
        (
            inspect.getsource(voice_transcription),
            inspect.getsource(message_postprocessing),
        )
    ).lower()
    provider_source = inspect.getsource(transcription_service.GeminiTranscriptionProvider).lower()

    assert "summar" not in voice_sources
    assert "translate_voice" not in voice_sources
    assert "audio/translations" not in provider_source
    assert "gemini-3.5-transcribe-live" not in provider_source
    assert '"type": "verbatim"' in provider_source
    # The language codes name the language being *spoken*, as a hint fed from
    # the caller. They must never become a fixed target language, which is the
    # shape that would quietly turn transcription into translation — the same
    # thing `audio/translations` is barred for above.
    assert '"language_codes": [language_hint] if language_hint else []' in provider_source


class _RecordingPublisher:
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
    message_type: str = "text",
    transcription_status: str | None = None,
) -> Message:
    message = Message(
        client_message_id=client_message_id,
        conversation_id=conversation_id,
        sender_id=sender_id,
        original_text=original_text,
        message_type=message_type,
        transcription_status=transcription_status,
        source_language="vi",
    )
    session.add(message)
    await session.commit()
    await session.refresh(message)
    return message


@pytest.mark.asyncio
async def test_text_and_completed_voice_use_identical_translation_inputs_and_outputs(
    test_db,
    test_user,
    test_user_two,
    conversation_factory,
):
    """Compare ordinary text and completed voice with the exact same canonical text."""
    transcript = "Khách nói hôm qua đã chốt VF 8 rồi, chiều nay gửi hợp đồng nhé."
    prior_context = "VF 8 ở đây là mẫu xe của khách Nguyễn An, mã hồ sơ A-017."
    text_conversation = await conversation_factory(
        test_user_two,
        [test_user],
        conversation_type="group",
        title="Phase 7 text control",
    )
    voice_conversation = await conversation_factory(
        test_user_two,
        [test_user],
        conversation_type="group",
        title="Phase 7 voice evaluation",
    )

    for prefix, conversation in (
        ("text", text_conversation),
        ("voice", voice_conversation),
    ):
        await _persist_message(
            test_db,
            conversation_id=conversation.id,
            sender_id=test_user.id,
            client_message_id=f"phase7-{prefix}-context",
            original_text=prior_context,
        )
        test_db.add_all(
            [
                ConversationProfile(
                    conversation_id=conversation.id,
                    domain="automotive",
                    audience="client",
                ),
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
            ]
        )
    test_db.add_all(
        [
            UserSettings(user_id=test_user.id, translation_tone="formal"),
            GlossaryEntry(
                source_term="VF 8",
                source_term_normalized=normalize_term("VF 8"),
                target_term="VF 8",
                source_language="vi",
                target_language="en",
                domain="automotive",
                audience="client",
                status="active",
            ),
        ]
    )
    await test_db.commit()

    text_message = await _persist_message(
        test_db,
        conversation_id=text_conversation.id,
        sender_id=test_user_two.id,
        client_message_id="phase7-text-input",
        original_text=transcript,
    )
    voice_message = await _persist_message(
        test_db,
        conversation_id=voice_conversation.id,
        sender_id=test_user_two.id,
        client_message_id="phase7-voice-input",
        original_text=transcript,
        message_type="voice",
        transcription_status="completed",
    )
    test_db.add(
        Attachment(
            id="PHASE7_AUDIO_METADATA_MUST_NOT_REACH_TRANSLATION.ogg",
            conversation_id=voice_conversation.id,
            message_id=voice_message.id,
            uploader_id=test_user_two.id,
            filename="PHASE7_AUDIO_FILENAME_MUST_NOT_REACH_TRANSLATION.ogg",
            content_type="audio/ogg; codecs=vorbis",
            size=1234,
        )
    )
    await test_db.commit()

    captured: list[dict] = []

    def graph_factory(session, message_id):
        class ExistingTranslationGraph:
            async def ainvoke(self, state, config=None):
                context = await DatabaseContextProvider(
                    session,
                    before_message_id=message_id,
                ).get_recent_messages(state["conversation_id"])
                customization = await DatabaseCustomizationProvider(session).get_customization(
                    state["conversation_id"],
                    original_text=state["original_text"],
                    source_language=state["source_language"],
                    target_language=state["target_language"],
                )
                captured.append(
                    {
                        "state": dict(state),
                        "context": context,
                        "domain": customization.domain,
                        "audience": customization.audience,
                        "glossary": [(term.source_term, term.target_term) for term in customization.glossary_terms],
                    }
                )
                return {
                    **state,
                    "source_language": "vi",
                    "translated_text": f"DETERMINISTIC::{state['original_text']}",
                    "model": "existing-translation-graph-phase7-fake",
                    "latency_ms": 1,
                    "is_fallback": False,
                }

        return ExistingTranslationGraph()

    publisher = _RecordingPublisher()
    for message in (text_message, voice_message):
        schedule_translations(
            message=message,
            publisher=publisher,
            session_factory=test_support.test_async_session_maker,
            graph_factory=graph_factory,
        )
    await asyncio.gather(*tuple(translation._BACKGROUND_TASKS))

    by_message = {call["state"]["message_id"]: call for call in captured if call["state"]["target_language"] == "en"}
    text_call = by_message[text_message.id]
    voice_call = by_message[voice_message.id]
    compared_state_keys = (
        "original_text",
        "source_language",
        "target_language",
        "honorific_profile",
        "sender_honorific_profile",
        "translation_tone",
    )
    assert {key: text_call["state"][key] for key in compared_state_keys} == {
        key: voice_call["state"][key] for key in compared_state_keys
    }
    assert voice_call["state"]["original_text"] == transcript
    assert text_call["context"] == voice_call["context"]
    assert [line.split(": ", 1)[1] for line in voice_call["context"]] == [prior_context]
    assert text_call["domain"] == voice_call["domain"] == "automotive"
    assert text_call["audience"] == voice_call["audience"] == "client"
    assert text_call["glossary"] == voice_call["glossary"] == [("VF 8", "VF 8")]
    assert "PHASE7_AUDIO" not in repr(voice_call)
    assert "audio/ogg" not in repr(voice_call)

    async with test_support.test_async_session_maker() as session:
        persisted = (
            await session.scalars(
                select(TranslationResult).where(TranslationResult.message_id.in_((text_message.id, voice_message.id)))
            )
        ).all()
    assert len(persisted) == 2
    assert {row.translated_text for row in persisted} == {f"DETERMINISTIC::{transcript}"}
    assert {payload["message_id"] for _, payload in publisher.sent} == {text_message.id, voice_message.id}
    assert all(payload["type"] == "translation_completed" for _, payload in publisher.sent)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prior_type", "current_type"),
    (
        ("text", "text"),
        ("text", "voice"),
        ("voice", "text"),
        ("voice", "voice"),
    ),
)
async def test_context_matrix_uses_only_canonical_completed_text(
    test_db,
    test_user,
    test_user_two,
    conversation_factory,
    prior_type,
    current_type,
):
    conversation = await conversation_factory(
        test_user,
        [test_user_two],
        conversation_type="direct",
    )
    prefix = f"phase7-{prior_type}-to-{current_type}"
    prior_text = f"CANONICAL_{prior_type.upper()}_CONTEXT"
    prior = await _persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        client_message_id=f"{prefix}-prior",
        original_text=prior_text,
        message_type=prior_type,
        transcription_status="completed" if prior_type == "voice" else None,
    )
    if prior_type == "voice":
        test_db.add(
            Attachment(
                id=f"{prefix}-audio",
                conversation_id=conversation.id,
                message_id=prior.id,
                uploader_id=test_user.id,
                filename="AUDIO_FILENAME_MUST_NOT_ENTER_CONTEXT.webm",
                content_type="audio/webm; codecs=opus",
                size=321,
            )
        )
    await _persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        client_message_id=f"{prefix}-pending",
        original_text="",
        message_type="voice",
        transcription_status="pending",
    )
    await _persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user.id,
        client_message_id=f"{prefix}-failed",
        original_text="",
        message_type="voice",
        transcription_status="failed",
    )
    current = await _persist_message(
        test_db,
        conversation_id=conversation.id,
        sender_id=test_user_two.id,
        client_message_id=f"{prefix}-current",
        original_text="CURRENT_MESSAGE_MUST_BE_EXCLUDED",
        message_type=current_type,
        transcription_status="completed" if current_type == "voice" else None,
    )

    context = await DatabaseContextProvider(
        test_db,
        before_message_id=current.id,
    ).get_recent_messages(conversation.id)

    assert [line.split(": ", 1)[1] for line in context] == [prior_text]
    serialized = repr(context)
    assert "CURRENT_MESSAGE_MUST_BE_EXCLUDED" not in serialized
    assert "AUDIO_FILENAME_MUST_NOT_ENTER_CONTEXT" not in serialized
    assert "audio/webm" not in serialized
    assert not hasattr(Attachment, "provider_file_uri")
