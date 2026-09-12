"""Run the Assistant Agent for an explicit ``@assistant`` mention."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from weakref import WeakValueDictionary

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.database import get_async_session_maker
from src.database.models import AssistantJob, Message
from src.schemas.chat import MessageReceivedEvent, RealtimeMessage
from src.schemas.intelligence import ActionProposalResponse
from src.services.assistant.agent_consent import has_consent
from src.services.assistant.assistant_agent import AssistantAgentService
from src.services.intelligence.action_proposals import (
    ActionProposalAmbiguousTargetError,
    ActionProposalError,
    ActionProposalService,
)
from src.services.intelligence.conversation_intelligence import ConversationIntelligenceService
from src.services.messaging.chat import ChatService

logger = logging.getLogger(__name__)
_TASKS: set[asyncio.Task[Any]] = set()
_EXPLICIT_CALENDAR_REQUEST = re.compile(
    r"\b(?:calendar|appointment|meeting)\b|(?:lịch|lịch hẹn|cuộc họp|họp)",
    re.IGNORECASE,
)
_CALENDAR_AMENDMENT = re.compile(
    r"(?:đổi|dời|chuyển|sửa|cập nhật|change|move|reschedule)\b.*(?:giờ|lúc|ngày|mai|today|tomorrow|time|date)|(?:giờ|lúc|ngày|mai|today|tomorrow)\b.*(?:đổi|dời|chuyển|sửa|cập nhật|change|move|reschedule)",
    re.IGNORECASE,
)
CALENDAR_CONTEXT_SETTLE_SECONDS = 3
ASSISTANT_JOB_LOCK_TIMEOUT = timedelta(minutes=5)
ASSISTANT_JOB_MAX_ATTEMPTS = 5
_CALENDAR_LOCKS: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


def _calendar_lock(conversation_id: str) -> asyncio.Lock:
    """Create locks lazily so they always belong to the active event loop."""
    lock = _CALENDAR_LOCKS.get(conversation_id)
    if lock is None:
        lock = asyncio.Lock()
        _CALENDAR_LOCKS[conversation_id] = lock
    return lock


def schedule_assistant_mention(
    *,
    message_id: str,
    conversation_id: str,
    requester_id: str,
    request_text: str,
    publisher: Any,
    trusted_timezone: str | None = None,
) -> None:
    """Start explicit assistant work after the triggering message is delivered.

    The extraction agent may call an LLM, so it must never hold up the chat
    WebSocket. Its output remains a pending proposal: the user still confirms
    or rejects it through the existing human-in-the-loop endpoints.
    """
    task = asyncio.create_task(
        _persist_and_process(
            message_id=message_id,
            conversation_id=conversation_id,
            requester_id=requester_id,
            request_text=request_text,
            publisher=publisher,
            trusted_timezone=trusted_timezone,
        )
    )
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    task.add_done_callback(_log_failure)


async def _persist_and_process(
    *,
    message_id: str,
    conversation_id: str,
    requester_id: str,
    request_text: str,
    publisher: Any,
    trusted_timezone: str | None = None,
) -> None:
    """Persist the work before invoking providers, then process the durable row."""
    maker = get_async_session_maker()
    try:
        async with maker() as session:
            job = await session.scalar(select(AssistantJob).where(AssistantJob.message_id == message_id))
            if job is None:
                job = AssistantJob(
                    message_id=message_id,
                    conversation_id=conversation_id,
                    requester_id=requester_id,
                    request_text=request_text,
                    trusted_timezone=trusted_timezone,
                )
                session.add(job)
                await session.commit()
                await session.refresh(job)
            job_id = job.id
    except IntegrityError:
        async with maker() as session:
            job_id = await session.scalar(select(AssistantJob.id).where(AssistantJob.message_id == message_id))
        if job_id is not None:
            await _run_durable_job(job_id, publisher)
            return
        logger.warning("Assistant job insert conflicted but no durable row was found")
        await _process(
            message_id=message_id,
            conversation_id=conversation_id,
            requester_id=requester_id,
            request_text=request_text,
            publisher=publisher,
            trusted_timezone=trusted_timezone,
        )
        return
    except Exception:
        logger.warning("Could not persist Assistant job; using in-process fallback", exc_info=True)
        await _process(
            message_id=message_id,
            conversation_id=conversation_id,
            requester_id=requester_id,
            request_text=request_text,
            publisher=publisher,
            trusted_timezone=trusted_timezone,
        )
        return
    await _run_durable_job(job_id, publisher)


async def _run_durable_job(job_id: str, publisher: Any) -> None:
    """Claim and finish one job; interrupted rows are recoverable on startup."""
    maker = get_async_session_maker()
    while True:
        now = datetime.now(UTC)
        async with maker() as session:
            job = await session.scalar(
                select(AssistantJob).where(AssistantJob.id == job_id).with_for_update(skip_locked=True)
            )
            if job is None or job.status in {"completed", "failed"}:
                return
            if job.status == "processing" and job.locked_at is not None:
                locked_at = job.locked_at.replace(tzinfo=UTC) if job.locked_at.tzinfo is None else job.locked_at
                ready_at = locked_at + ASSISTANT_JOB_LOCK_TIMEOUT
                if ready_at > now:
                    wait_seconds = (ready_at - now).total_seconds()
                    payload = None
                else:
                    wait_seconds = 0.0
                    payload = job
            elif job.available_at is not None:
                available_at = (
                    job.available_at.replace(tzinfo=UTC) if job.available_at.tzinfo is None else job.available_at
                )
                if available_at > now:
                    wait_seconds = (available_at - now).total_seconds()
                    payload = None
                else:
                    wait_seconds = 0.0
                    payload = job
            else:
                wait_seconds = 0.0
                payload = job

            if payload is not None:
                job.status = "processing"
                job.locked_at = now
                job.attempts += 1
                await session.commit()
                arguments = {
                    "message_id": job.message_id,
                    "conversation_id": job.conversation_id,
                    "requester_id": job.requester_id,
                    "request_text": job.request_text,
                    "publisher": publisher,
                    "trusted_timezone": job.trusted_timezone,
                }
            else:
                arguments = None

        if arguments is None:
            await asyncio.sleep(min(wait_seconds, 30.0))
            continue
        try:
            processed = await _process(**arguments)
            if not processed:
                raise RuntimeError("assistant_processing_failed")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            terminal = False
            async with maker() as session:
                failed = await session.get(AssistantJob, job_id)
                if failed is not None:
                    failed.last_error = type(exc).__name__
                    failed.locked_at = None
                    if failed.attempts >= ASSISTANT_JOB_MAX_ATTEMPTS:
                        failed.status = "failed"
                        terminal = True
                    else:
                        failed.status = "pending"
                        failed.available_at = datetime.now(UTC) + timedelta(seconds=min(2**failed.attempts, 30))
                    await session.commit()
            if terminal:
                return
            continue
        async with maker() as session:
            completed = await session.get(AssistantJob, job_id)
            if completed is not None:
                completed.status = "completed"
                completed.completed_at = datetime.now(UTC)
                completed.locked_at = None
                completed.last_error = None
                await session.commit()
        return


async def recover_assistant_jobs(*, publisher: Any) -> int:
    """Resume pending and interrupted Assistant work after process startup."""
    maker = get_async_session_maker()
    async with maker() as session:
        job_ids = list(
            (
                await session.scalars(
                    select(AssistantJob.id)
                    .where(AssistantJob.status.in_(("pending", "processing")))
                    .order_by(AssistantJob.created_at.asc())
                    .limit(200)
                )
            ).all()
        )
    for job_id in job_ids:
        task = asyncio.create_task(_run_durable_job(job_id, publisher))
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)
        task.add_done_callback(_log_failure)
    return len(job_ids)


async def stop_assistant_jobs() -> None:
    """Cancel local runners; their database rows remain recoverable."""
    tasks = tuple(_TASKS)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def schedule_assistant_voice_transcript(
    *,
    message_id: str,
    conversation_id: str,
    requester_id: str,
    publisher: Any,
    trusted_timezone: str | None = None,
) -> None:
    """Continue an Assistant voice turn after its transcript is durable.

    Voice messages have no mention token to parse at upload time.  This worker
    therefore accepts only the caller's dedicated Assistant thread, waits for
    STT to persist the canonical text, then creates the normal acknowledgement
    and queues the existing Assistant workflow with that transcript.
    """
    task = asyncio.create_task(
        _process_voice_transcript(
            message_id=message_id,
            conversation_id=conversation_id,
            requester_id=requester_id,
            publisher=publisher,
            trusted_timezone=trusted_timezone,
        ),
        name=f"assistant-voice:{message_id}",
    )
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    task.add_done_callback(_log_failure)


def _log_failure(task: asyncio.Task[Any]) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.warning("Assistant mention processing failed", exc_info=task.exception())


async def _process(
    *,
    message_id: str,
    conversation_id: str,
    requester_id: str,
    request_text: str,
    publisher: Any,
    trusted_timezone: str | None = None,
) -> bool:
    """Serialize calendar turns from one conversation in message-arrival order."""
    is_calendar = bool(_EXPLICIT_CALENDAR_REQUEST.search(request_text) or _CALENDAR_AMENDMENT.search(request_text))
    arguments = {
        "message_id": message_id,
        "conversation_id": conversation_id,
        "requester_id": requester_id,
        "request_text": request_text,
        "publisher": publisher,
        "trusted_timezone": trusted_timezone,
    }
    if is_calendar:
        async with _calendar_lock(conversation_id):
            return await _process_once(**arguments)
    return await _process_once(**arguments)


async def _process_once(
    *,
    message_id: str,
    conversation_id: str,
    requester_id: str,
    request_text: str,
    publisher: Any,
    trusted_timezone: str | None = None,
) -> bool:
    """Run the assistant graph and notify only the account that invoked it.

    The graph parks at `human_confirm` rather than finishing, which is what this
    worker wants: the proposals are persisted and the person is told about them,
    and nothing reaches a calendar until they answer.
    """
    proposal_events: list[dict[str, Any]] = []
    no_new_calendar_notice: dict[str, Any] | None = None
    calendar_resolution_notice: str | None = None
    is_calendar_request = bool(_EXPLICIT_CALENDAR_REQUEST.search(request_text))
    is_calendar_amendment = bool(_CALENDAR_AMENDMENT.search(request_text))
    if is_calendar_request or is_calendar_amendment:
        # People commonly send a request and its time/note as two bubbles. Let
        # the short burst settle before reading nearby messages; the worker is
        # detached, so this never delays chat delivery.
        await asyncio.sleep(CALENDAR_CONTEXT_SETTLE_SECONDS)
    async with get_async_session_maker()() as session:
        # The graph checks this too, in `check_consent`. Asking here as well
        # keeps a normal "not permitted" state out of the done-callback, where
        # it would be logged as a failure.
        if not await has_consent(session, requester_id, "read_conversations"):
            return True

        # A clearly stated calendar request already supplies the exact message
        # to inspect.  Extract it directly instead of waiting for the general
        # planner to choose the same tool.  The result is still a pending
        # proposal and still requires the person's approval; this only removes
        # an unnecessary planning round from the interactive path.
        if is_calendar_request or is_calendar_amendment:
            try:
                extraction_args: dict[str, Any] = {
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    "user_id": requester_id,
                    "db": session,
                }
                if trusted_timezone is not None:
                    extraction_args["trusted_timezone"] = trusted_timezone
                if is_calendar_amendment:
                    extraction_args["update_latest_appointment"] = True
                extracted = await ConversationIntelligenceService().extract_actions_from_message(
                    **extraction_args,
                )
                proposal_events.extend(proposal.model_dump(mode="json") for proposal in extracted)
            except ActionProposalAmbiguousTargetError:
                calendar_resolution_notice = (
                    "Bạn đang có nhiều đề xuất lịch chưa duyệt. Hãy nói rõ tên lịch cần đổi, "
                    "ví dụ: ‘Đổi lịch Họp dự án sang 10 giờ tối mai’."
                )
            except Exception:
                logger.warning("Assistant calendar proposal extraction failed", exc_info=True)

        if not proposal_events and calendar_resolution_notice is None:
            try:
                result = await AssistantAgentService(session).run(
                    conversation_id=conversation_id,
                    user_id=requester_id,
                    request_text=request_text,
                    source_message_id=message_id,
                )
            except Exception:
                logger.warning("Assistant run failed", exc_info=True)
                return False

        if not proposal_events and calendar_resolution_notice is None:
            # Tool results intentionally begin as a compact `{id, title}` summary
            # for the planner. The browser cannot render a review card from that:
            # it needs the status, source message, time and location. Reload the
            # persisted row in this worker before publishing the realtime event.
            # This also makes the database the single source of truth when a retry
            # returns an already-existing proposal.
            proposals = ActionProposalService(session)
            for compact in result.proposals:
                proposal_id = compact.get("id") if isinstance(compact, dict) else None
                if not isinstance(proposal_id, str):
                    continue
                try:
                    proposal = await proposals.get_proposal(proposal_id)
                    if proposal.owner_user_id != requester_id:
                        logger.warning("Assistant returned a proposal owned by another user")
                        continue
                    proposal_events.append(ActionProposalResponse.model_validate(proposal).model_dump(mode="json"))
                except (ActionProposalError, AttributeError):
                    # The worker's unit seam uses a sentinel session rather than a
                    # database. A compact event remains useful to non-UI consumers;
                    # production always takes the hydrated path above.
                    proposal_events.append(compact)
                except Exception:
                    logger.warning("Could not hydrate assistant proposal %s", proposal_id, exc_info=True)

        if (is_calendar_request or is_calendar_amendment) and not proposal_events:
            # The immediate reply is intentionally optimistic because extracting
            # a proposal can take a moment.  Close that loop with a durable,
            # specific outcome when this request created nothing new (for
            # example, an identical appointment was already proposed).
            try:
                if calendar_resolution_notice is not None:
                    notice_text = calendar_resolution_notice
                elif is_calendar_amendment:
                    notice_text = (
                        "Không tìm thấy đề xuất lịch nào để cập nhật. Hãy gửi lại yêu cầu có ngày và giờ cụ thể."
                    )
                else:
                    notice_text = (
                        "Không có lịch hẹn mới để đề xuất. Yêu cầu này có thể đã được "
                        "xử lý trước đó; hãy thay đổi thời gian hoặc nội dung nếu bạn muốn tạo lịch khác."
                    )
                notice_result = await ChatService(session).create_assistant_notice(
                    conversation_id=conversation_id,
                    user_id=requester_id,
                    text=notice_text,
                    idempotency_key=f"assistant:calendar-no-new-proposal:{message_id}",
                    source_language="vi",
                )
                notice = RealtimeMessage.model_validate(notice_result.message)
                notice.assistant_generated = True
                no_new_calendar_notice = MessageReceivedEvent(message=notice).model_dump(mode="json")
            except Exception:
                logger.warning("Could not post empty calendar-request notice", exc_info=True)

    for proposal in proposal_events:
        try:
            await publisher.send_to_user(
                requester_id,
                {"type": "action_proposal_created", "proposal": proposal},
            )
        except Exception:
            logger.warning("Assistant proposal event publish failed", exc_info=True)

    if no_new_calendar_notice is not None:
        try:
            await publisher.send_to_user(requester_id, no_new_calendar_notice)
        except Exception:
            logger.warning("Assistant empty calendar-request notice publish failed", exc_info=True)
    return True


async def _process_voice_transcript(
    *,
    message_id: str,
    conversation_id: str,
    requester_id: str,
    publisher: Any,
    trusted_timezone: str | None = None,
) -> None:
    """Create the normal Assistant acknowledgement for one transcribed voice turn."""
    async with get_async_session_maker()() as session:
        service = ChatService(session)
        if not await service.is_assistant_conversation_for_user(
            conversation_id=conversation_id,
            user_id=requester_id,
        ):
            return
        if not await has_consent(session, requester_id, "read_conversations"):
            return

        message = await session.get(Message, message_id)
        if (
            message is None
            or message.conversation_id != conversation_id
            or message.sender_id != requester_id
            or message.message_type != "voice"
            or message.transcription_status != "completed"
            or not message.original_text.strip()
            or message.deleted_at is not None
        ):
            return

        request_text = message.original_text
        reply_result = await service.create_assistant_reply(trigger_message=message)
        assistant_message = RealtimeMessage.model_validate(reply_result.message)
        assistant_message.assistant_generated = True

    await publisher.send_to_users(
        reply_result.recipient_ids,
        MessageReceivedEvent(message=assistant_message).model_dump(mode="json"),
    )
    schedule_assistant_mention(
        message_id=message_id,
        conversation_id=conversation_id,
        requester_id=requester_id,
        request_text=request_text,
        publisher=publisher,
        trusted_timezone=trusted_timezone or message.client_timezone,
    )
