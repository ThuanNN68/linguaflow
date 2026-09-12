"""Detached, race-safe voice transcription and post-transcript handoff.

Audio stops at the Phase 2 transcription service. Once the full transcript is
durable, Phase 4 hands the reloaded canonical ``Message`` to the same shared
post-text scheduler used by ordinary WebSocket messages.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.database import get_async_session_maker
from src.database.models import Attachment, ConversationMember, Message, User
from src.schemas.chat import (
    VoiceTranscriptionCompletedEvent,
    VoiceTranscriptionFailedEvent,
)
from src.services.assistant.assistant_mentions import schedule_assistant_voice_transcript
from src.services.messaging.attachment_storage import (
    AttachmentStorageError,
    AttachmentStorageNotFoundError,
)
from src.services.messaging.chat import ChatService
from src.services.messaging.message_postprocessing import schedule_text_dependent_work
from src.services.voice.transcription import (
    BlankTranscriptError,
    TranscriptionError,
    TranscriptionService,
    get_transcription_service,
)

logger = logging.getLogger(__name__)

_BACKGROUND_TASKS: set[asyncio.Task] = set()


class EventPublisher(Protocol):
    """Transport capability used after a lifecycle state is durable."""

    async def send_to_users(
        self,
        user_ids: Iterable[str],
        event: Mapping[str, Any],
    ) -> None: ...


class PostprocessingScheduler(Protocol):
    """Schedules existing text-dependent work for a persisted message."""

    def __call__(self, *, message: Message, publisher: EventPublisher) -> None: ...


def schedule_voice_transcription(
    *,
    message_id: str,
    conversation_id: str,
    publisher: EventPublisher,
    session_factory: Callable[[], AsyncSession] | None = None,
    transcription_service_factory: Callable[[], TranscriptionService] | None = None,
    postprocessing_scheduler: PostprocessingScheduler | None = None,
) -> None:
    """Launch STT with primitive IDs and sessions independent of the socket."""
    resolved_session_factory = session_factory or get_async_session_maker()
    task = asyncio.create_task(
        _run_voice_transcription_guarded(
            message_id=message_id,
            conversation_id=conversation_id,
            publisher=publisher,
            session_factory=resolved_session_factory,
            transcription_service_factory=(transcription_service_factory or get_transcription_service),
            postprocessing_scheduler=postprocessing_scheduler,
        ),
        name=f"voice-stt:{message_id}",
    )
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    task.add_done_callback(_log_task_failure)


async def recover_pending_voice_transcriptions(
    *,
    publisher: EventPublisher,
    session_factory: Callable[[], AsyncSession] | None = None,
) -> int:
    """Reschedule durable pending voice rows after an interrupted process.

    Duplicate recovery scans are safe: terminal state transitions are guarded
    by ``pending`` in the UPDATE predicate, so only one task can publish the
    completion or failure event.
    """
    factory = session_factory or get_async_session_maker()
    async with factory() as session:
        pending = tuple(
            (
                await session.execute(
                    select(Message.id, Message.conversation_id).where(
                        Message.message_type == "voice",
                        Message.transcription_status == "pending",
                        Message.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        await session.rollback()

    for message_id, conversation_id in pending:
        schedule_voice_transcription(
            message_id=message_id,
            conversation_id=conversation_id,
            publisher=publisher,
            session_factory=factory,
        )
    return len(pending)


def _log_task_failure(task: asyncio.Task) -> None:
    """Surface an unexpected task escape without logging sensitive payloads."""
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error(
            "voice_stt_task_escaped task=%s exception_type=%s",
            task.get_name(),
            type(error).__name__,
        )


async def _run_voice_transcription_guarded(
    *,
    message_id: str,
    conversation_id: str,
    publisher: EventPublisher,
    session_factory: Callable[[], AsyncSession],
    transcription_service_factory: Callable[[], TranscriptionService],
    postprocessing_scheduler: PostprocessingScheduler | None = None,
) -> None:
    """Keep an unexpected detached-task error from stranding a pending row."""
    try:
        await transcribe_voice_message(
            message_id=message_id,
            conversation_id=conversation_id,
            publisher=publisher,
            session_factory=session_factory,
            transcription_service_factory=transcription_service_factory,
            postprocessing_scheduler=postprocessing_scheduler,
        )
    except Exception as exc:  # noqa: BLE001 - never log raw provider/user content
        settings = get_settings()
        logger.error(
            "voice_stt_task_error message_id=%s conversation_id=%s provider=%s "
            "model=%s stage=orchestration failure_code=unexpected_error "
            "http_status=None retryable=True exception_type=%s",
            message_id,
            conversation_id,
            settings.stt_provider,
            settings.stt_model,
            type(exc).__name__,
        )
        try:
            await _transition_to_failed(
                message_id=message_id,
                conversation_id=conversation_id,
                retryable=True,
                publisher=publisher,
                session_factory=session_factory,
            )
        except Exception as transition_error:  # noqa: BLE001 - best effort only
            logger.error(
                "voice_stt_failure_transition_error message_id=%s conversation_id=%s exception_type=%s",
                message_id,
                conversation_id,
                type(transition_error).__name__,
            )


async def transcribe_voice_message(
    *,
    message_id: str,
    conversation_id: str,
    publisher: EventPublisher,
    session_factory: Callable[[], AsyncSession],
    transcription_service_factory: Callable[[], TranscriptionService],
    postprocessing_scheduler: PostprocessingScheduler | None = None,
) -> None:
    """Transcribe one authoritative pending voice message and transition it once."""
    started = time.perf_counter()
    settings = get_settings()
    logger.info(
        "voice_stt_started message_id=%s conversation_id=%s provider=%s model=%s",
        message_id,
        conversation_id,
        settings.stt_provider,
        settings.stt_model,
    )
    attachment: Attachment | None = None
    async with session_factory() as session:
        message = await session.scalar(
            select(Message).where(
                Message.id == message_id,
                Message.conversation_id == conversation_id,
                Message.message_type == "voice",
                Message.transcription_status == "pending",
                Message.deleted_at.is_(None),
            )
        )
        if message is None:
            return
        attachment = await session.scalar(
            select(Attachment).where(
                Attachment.message_id == message_id,
                Attachment.conversation_id == conversation_id,
            )
        )
        # Read inside this session: the transcription below runs outside it, and
        # the ORM instances above must not be touched from there.
        language_hint = await session.scalar(select(User.preferred_language).where(User.id == message.sender_id))

    if attachment is None:
        _log_voice_failure(
            message_id=message_id,
            conversation_id=conversation_id,
            failure_code="attachment_missing",
            stage="storage",
            http_status=None,
            retryable=False,
            latency_ms=_elapsed_ms(started),
        )
        await _transition_to_failed(
            message_id=message_id,
            conversation_id=conversation_id,
            retryable=False,
            publisher=publisher,
            session_factory=session_factory,
        )
        return

    try:
        service = transcription_service_factory()
        transcription = await service.transcribe_attachment(attachment, language_hint)
        transcript = transcription.text.strip()
        if not transcript:
            raise BlankTranscriptError("STT provider returned a blank transcript")
    except (AttachmentStorageError, TranscriptionError) as exc:
        failure_code, stage, http_status, retryable = _failure_metadata(exc)
        _log_voice_failure(
            message_id=message_id,
            conversation_id=conversation_id,
            failure_code=failure_code,
            stage=stage,
            http_status=http_status,
            retryable=retryable,
            latency_ms=_elapsed_ms(started),
        )
        await _transition_to_failed(
            message_id=message_id,
            conversation_id=conversation_id,
            retryable=retryable,
            publisher=publisher,
            session_factory=session_factory,
        )
        return

    completed = await _transition_to_completed(
        message_id=message_id,
        conversation_id=conversation_id,
        transcript=transcript,
        publisher=publisher,
        session_factory=session_factory,
        postprocessing_scheduler=(postprocessing_scheduler or schedule_text_dependent_work),
    )
    if completed:
        logger.info(
            "voice_stt_completed message_id=%s conversation_id=%s provider=%s "
            "model=%s provider_latency_ms=%s total_ms=%s",
            message_id,
            conversation_id,
            settings.stt_provider,
            transcription.model,
            transcription.latency_ms,
            _elapsed_ms(started),
        )


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _failure_metadata(exc: Exception) -> tuple[str, str, int | None, bool]:
    """Return safe fields only; raw provider and audio details stay discarded."""
    if isinstance(exc, TranscriptionError):
        return exc.failure_code, exc.stage, exc.http_status, exc.retryable
    if isinstance(exc, AttachmentStorageNotFoundError):
        return "attachment_missing", "storage", None, False
    if isinstance(exc, AttachmentStorageError):
        return "storage_unavailable", "storage", None, True
    return "unexpected_error", "orchestration", None, True


def _log_voice_failure(
    *,
    message_id: str,
    conversation_id: str,
    failure_code: str,
    stage: str,
    http_status: int | None,
    retryable: bool,
    latency_ms: int,
) -> None:
    settings = get_settings()
    logger.warning(
        "voice_stt_failed message_id=%s conversation_id=%s provider=%s model=%s "
        "stage=%s failure_code=%s http_status=%s retryable=%s latency_ms=%s",
        message_id,
        conversation_id,
        settings.stt_provider,
        settings.stt_model,
        stage,
        failure_code,
        http_status,
        retryable,
        latency_ms,
    )


async def _current_member_ids(
    session: AsyncSession,
    conversation_id: str,
) -> tuple[str, ...]:
    return tuple(
        (
            await session.scalars(
                select(ConversationMember.user_id).where(ConversationMember.conversation_id == conversation_id)
            )
        ).all()
    )


async def _transition_to_completed(
    *,
    message_id: str,
    conversation_id: str,
    transcript: str,
    publisher: EventPublisher,
    session_factory: Callable[[], AsyncSession],
    postprocessing_scheduler: PostprocessingScheduler,
) -> bool:
    """Commit completion, publish it, then schedule existing post-text work."""
    async with session_factory() as session:
        row = (
            await session.execute(
                update(Message)
                .where(
                    Message.id == message_id,
                    Message.conversation_id == conversation_id,
                    Message.message_type == "voice",
                    Message.transcription_status == "pending",
                    Message.deleted_at.is_(None),
                )
                .values(
                    original_text=transcript,
                    transcription_status="completed",
                )
                .returning(Message.source_language)
            )
        ).one_or_none()
        if row is None:
            await session.rollback()
            return False
        member_ids = await _current_member_ids(session, conversation_id)
        source_language = row.source_language
        await session.commit()

    await publisher.send_to_users(
        member_ids,
        VoiceTranscriptionCompletedEvent(
            message_id=message_id,
            conversation_id=conversation_id,
            original_text=transcript,
            source_language=source_language,
        ).model_dump(mode="json"),
    )

    # Reload after the completion commit and event. This persisted Message is
    # the sole source passed to translation and every other text-dependent
    # service; the transient STT result is not a parallel content channel.
    async with session_factory() as session:
        completed_message = await session.scalar(
            select(Message).where(
                Message.id == message_id,
                Message.conversation_id == conversation_id,
                Message.message_type == "voice",
                Message.transcription_status == "completed",
                Message.original_text != "",
                Message.deleted_at.is_(None),
            )
        )
        is_assistant_voice_turn = bool(completed_message) and await ChatService(
            session
        ).is_assistant_conversation_for_user(
            conversation_id=conversation_id,
            user_id=completed_message.sender_id,
        )
    if completed_message is None:
        return True
    postprocessing_scheduler(message=completed_message, publisher=publisher)
    if is_assistant_voice_turn:
        schedule_assistant_voice_transcript(
            message_id=message_id,
            conversation_id=conversation_id,
            requester_id=completed_message.sender_id,
            publisher=publisher,
            trusted_timezone=completed_message.client_timezone,
        )
    return True


async def _transition_to_failed(
    *,
    message_id: str,
    conversation_id: str,
    retryable: bool,
    publisher: EventPublisher,
    session_factory: Callable[[], AsyncSession],
) -> bool:
    """Atomically claim the pending state, commit, then publish safe failure."""
    async with session_factory() as session:
        transitioned = (
            await session.execute(
                update(Message)
                .where(
                    Message.id == message_id,
                    Message.conversation_id == conversation_id,
                    Message.message_type == "voice",
                    Message.transcription_status == "pending",
                    Message.deleted_at.is_(None),
                )
                .values(original_text="", transcription_status="failed")
                .returning(Message.id)
            )
        ).one_or_none()
        if transitioned is None:
            await session.rollback()
            return False
        member_ids = await _current_member_ids(session, conversation_id)
        await session.commit()

    await publisher.send_to_users(
        member_ids,
        VoiceTranscriptionFailedEvent(
            message_id=message_id,
            conversation_id=conversation_id,
            retryable=retryable,
        ).model_dump(mode="json"),
    )
    return True
