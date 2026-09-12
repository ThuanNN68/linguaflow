"""WebSocket transport for realtime chat events."""

import asyncio
import json
import logging
import re
from collections.abc import Callable

from fastapi import APIRouter, Depends, WebSocket
from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketDisconnect

from src.config import get_settings
from src.core.deps import get_user_by_token
from src.core.rate_limit import (
    WebSocketRateLimitError,
    check_websocket_message_rate,
)
from src.database import get_async_session_maker, get_db  # noqa: F401 – get_db kept for test overrides
from src.schemas.chat import (
    AssistantConsentRequiredEvent,
    AttachmentResponse,
    AuthEvent,
    AuthOkEvent,
    ErrorEvent,
    MentionNotificationEvent,
    MentionSummary,
    MessageCreatedEvent,
    MessageReceivedEvent,
    RealtimeMessage,
    SendMessageEvent,
    SendVoiceMessageEvent,
    TypingEvent,
    TypingNotificationEvent,
)
from src.services.assistant.agent_consent import has_consent
from src.services.assistant.assistant_mentions import schedule_assistant_mention
from src.services.delivery.push import schedule_push
from src.services.delivery.webhooks import schedule_webhook_event
from src.services.intelligence.commitment_detection import schedule_commitment_detection
from src.services.intelligence.profile_inference import schedule_profile_inference
from src.services.language.translation import schedule_translations
from src.services.messaging.blocking import DirectMessagingBlockedError
from src.services.messaging.chat import (
    ChatService,
    ClientMessageIdConflictError,
    ConversationMembershipError,
    ConversationNotFoundError,
    VoiceAttachmentClaimedError,
    VoiceAttachmentConversationError,
    VoiceAttachmentNotAudioError,
    VoiceAttachmentNotFoundError,
    VoiceAttachmentOwnershipError,
    VoiceMessageIdConflictError,
    message_mentions,
)
from src.services.messaging.connection_manager import ConnectionManager
from src.services.messaging.message_memory import schedule_message_embedding
from src.services.messaging.message_postprocessing import schedule_text_dependent_work
from src.services.voice.voice_transcription import schedule_voice_transcription

logger = logging.getLogger(__name__)

AUTH_TIMEOUT_SECONDS = 10
HEARTBEAT_INTERVAL_SECONDS = 30
HEARTBEAT_TIMEOUT_SECONDS = 10

_SessionFactory = Callable[[], AsyncSession]

router = APIRouter()
connection_manager = ConnectionManager()


def get_connection_manager() -> ConnectionManager:
    """Provide the single-process connection manager for dependency overrides."""
    return connection_manager


def _origin_allowed(origin: str | None) -> bool:
    """Whether a browser at `origin` may open this socket.

    `CORSMiddleware` never runs for a WebSocket handshake, so without this check
    any page on the internet could open a socket in a signed-in visitor's browser
    and read their conversations. The same allowlist as CORS is used, so there is
    one place to add a deployed domain.

    A request with no `Origin` header is allowed: that is a non-browser client
    (the test client, `curl`, a native app), which is not what the header
    defends against — an attacker who can forge headers can forge this one too.
    """
    if origin is None:
        return True
    settings = get_settings()
    if origin in settings.cors_origin_list:
        return True
    pattern = settings.cors_origin_regex
    return bool(pattern) and re.fullmatch(pattern, origin) is not None


async def _send_event(websocket: WebSocket, event: BaseModel) -> None:
    """Serialize a typed event into a JSON-safe WebSocket payload."""
    await websocket.send_json(event.model_dump(mode="json"))


async def _send_error(websocket: WebSocket, code: str, message: str) -> None:
    """Send a structured application error without exposing internal details."""
    await _send_event(websocket, ErrorEvent(code=code, message=message))


async def _send_error_and_close(
    websocket: WebSocket,
    code: str,
    message: str,
    close_code: int,
) -> None:
    """Best-effort error response before closing an unauthenticated socket."""
    try:
        await _send_error(websocket, code, message)
        await websocket.close(code=close_code)
    except (OSError, RuntimeError, WebSocketDisconnect):
        return


async def _heartbeat(websocket: WebSocket, pong_received: asyncio.Event) -> None:
    """Require a pong after every ping and close half-open connections."""
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            pong_received.clear()
            await websocket.send_json({"type": "ping"})
            try:
                await asyncio.wait_for(
                    pong_received.wait(),
                    timeout=HEARTBEAT_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                await websocket.close(code=1001, reason="heartbeat timeout")
                return
    except (OSError, RuntimeError, WebSocketDisconnect, asyncio.CancelledError):
        return


async def _relay_typing(
    *,
    websocket: WebSocket,
    session_factory: _SessionFactory,
    manager: ConnectionManager,
    user_id: str,
    raw_event: dict,
) -> None:
    """Pass a typing notice to the rest of the conversation.

    Membership is checked on every notice rather than trusted from the client:
    without it, any authenticated socket could announce itself as typing into a
    conversation it cannot even read. The client debounces, so this is not a
    query per keystroke.
    """
    try:
        event = TypingEvent.model_validate(raw_event)
    except ValidationError:
        await _send_error(websocket, "invalid_event", "The typing event is invalid")
        return

    async with session_factory() as db:
        service = ChatService(db)
        try:
            member_ids = await service.get_conversation_member_ids(
                conversation_id=event.conversation_id,
            )
        except SQLAlchemyError:
            await db.rollback()
            return
        await db.rollback()

    if user_id not in member_ids:
        await _send_error(
            websocket,
            "not_conversation_member",
            "You are not a member of this conversation",
        )
        return

    await manager.send_to_users(
        tuple(member_id for member_id in member_ids if member_id != user_id),
        TypingNotificationEvent(
            conversation_id=event.conversation_id,
            user_id=user_id,
            is_typing=event.is_typing,
        ).model_dump(mode="json"),
    )


async def _handle_voice_message(
    *,
    websocket: WebSocket,
    session_factory: _SessionFactory,
    manager: ConnectionManager,
    user_id: str,
    raw_event: dict,
) -> None:
    """Persist/fan out pending voice, then start only detached STT work."""
    try:
        event = SendVoiceMessageEvent.model_validate(raw_event)
    except ValidationError:
        await _send_error(
            websocket,
            "invalid_event",
            "The send_voice_message event is invalid",
        )
        return

    async with session_factory() as db:
        service = ChatService(db)
        try:
            result = await service.send_voice_message(
                sender_id=user_id,
                conversation_id=event.conversation_id,
                client_message_id=event.client_message_id,
                attachment_id=event.attachment_id,
                reply_to_message_id=event.reply_to_message_id,
                client_timezone=event.client_timezone,
            )
        except ConversationNotFoundError:
            await db.rollback()
            await _send_error(websocket, "conversation_not_found", "Conversation was not found")
            return
        except ConversationMembershipError:
            await db.rollback()
            await _send_error(
                websocket,
                "not_conversation_member",
                "You are not a member of this conversation",
            )
            return
        except DirectMessagingBlockedError:
            await db.rollback()
            await _send_error(
                websocket,
                "direct_messaging_blocked",
                "Direct messaging is unavailable",
            )
            return
        except VoiceMessageIdConflictError:
            await db.rollback()
            await _send_error(
                websocket,
                "client_message_id_conflict",
                "client_message_id was already used for a different voice request",
            )
            return
        except VoiceAttachmentNotFoundError:
            await db.rollback()
            await _send_error(websocket, "voice_attachment_not_found", "Voice attachment was not found")
            return
        except VoiceAttachmentConversationError:
            await db.rollback()
            await _send_error(
                websocket,
                "voice_attachment_wrong_conversation",
                "Voice attachment belongs to a different conversation",
            )
            return
        except VoiceAttachmentOwnershipError:
            await db.rollback()
            await _send_error(
                websocket,
                "voice_attachment_not_owned",
                "Voice attachment belongs to a different uploader",
            )
            return
        except VoiceAttachmentClaimedError:
            await db.rollback()
            await _send_error(
                websocket,
                "voice_attachment_already_claimed",
                "Voice attachment is already claimed",
            )
            return
        except VoiceAttachmentNotAudioError:
            await db.rollback()
            await _send_error(
                websocket,
                "voice_attachment_not_audio",
                "Voice attachment is not supported audio",
            )
            return
        except SQLAlchemyError:
            await db.rollback()
            await _send_error(websocket, "internal_error", "Voice message could not be persisted")
            return

        realtime_message = RealtimeMessage.model_validate(result.message)
        attachments = await service.get_attachments_by_message(message_ids=[result.message.id])
        attachment = attachments.get(result.message.id)
        if attachment is None:
            # The atomic service contract says this cannot happen. Fail closed if a
            # future persistence change violates it: never fan out an unplayable
            # voice message.
            await db.rollback()
            await _send_error(websocket, "internal_error", "Voice message could not be delivered")
            return
        realtime_message.attachment = AttachmentResponse.model_validate(attachment)

    await manager.send_to_user(
        user_id,
        MessageCreatedEvent(
            client_message_id=event.client_message_id,
            message=realtime_message,
        ).model_dump(mode="json"),
    )
    if not result.created:
        return

    await manager.send_to_users(
        result.recipient_ids,
        MessageReceivedEvent(message=realtime_message).model_dump(mode="json"),
    )
    schedule_push(
        result.recipient_ids,
        title="New voice message",
        body="You received a voice message",
        url=f"/chat?conversation={result.message.conversation_id}",
    )
    schedule_webhook_event("message.created", {
        "message_id": result.message.id,
        "conversation_id": result.message.conversation_id,
        "sender_id": result.message.sender_id,
        "message_type": "voice",
    })
    schedule_voice_transcription(
        message_id=result.message.id,
        conversation_id=result.message.conversation_id,
        publisher=manager,
    )


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    manager: ConnectionManager = Depends(get_connection_manager),
) -> None:
    """Authenticate a socket, persist messages, then fan them out to members.

    Database sessions are created per-event and closed immediately after each
    operation so an idle authenticated socket does not hold a connection from
    the pool for its entire lifetime.
    """
    if not _origin_allowed(websocket.headers.get("origin")):
        # Refused before `accept()`, so the handshake fails outright rather than
        # opening a socket only to close it.
        await websocket.close(code=4403)
        return

    await websocket.accept()
    user_id: str | None = None
    ping_task: asyncio.Task | None = None
    pong_received = asyncio.Event()
    session_factory = get_async_session_maker()

    try:
        try:
            initial_event = await asyncio.wait_for(
                websocket.receive_json(),
                timeout=AUTH_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            await _send_error_and_close(
                websocket,
                "authentication_timeout",
                "Authentication was not received in time",
                4401,
            )
            return
        except json.JSONDecodeError:
            await _send_error_and_close(
                websocket,
                "authentication_failed",
                "The first event must be a valid auth event",
                4401,
            )
            return
        except WebSocketDisconnect:
            return

        if not isinstance(initial_event, dict) or initial_event.get("type") != "auth":
            await _send_error_and_close(
                websocket,
                "authentication_required",
                "The first event must be an auth event",
                4401,
            )
            return

        try:
            auth_event = AuthEvent.model_validate(initial_event)
        except ValidationError:
            await _send_error_and_close(
                websocket,
                "authentication_failed",
                "The auth event is invalid",
                4401,
            )
            return

        async with session_factory() as db:
            try:
                user = await get_user_by_token(auth_event.token, db)
            except SQLAlchemyError:
                await db.rollback()
                await _send_error_and_close(
                    websocket,
                    "internal_error",
                    "Authentication could not be completed",
                    1011,
                )
                return

            if user is None:
                await db.rollback()
                await _send_error_and_close(
                    websocket,
                    "authentication_failed",
                    "Invalid or expired token",
                    4401,
                )
                return

            user_id = user.id

        await _send_event(websocket, AuthOkEvent(user_id=user_id))
        manager.connect(user_id, websocket)
        ping_task = asyncio.create_task(
            _heartbeat(websocket, pong_received),
            name=f"ws-ping:{user_id}",
        )

        while True:
            try:
                raw_event = await websocket.receive_json()
            except WebSocketDisconnect:
                # A browser can close between reconnect attempts.  Treat that as
                # the normal end of this connection rather than letting the
                # receive loop call into a disconnected ASGI websocket.
                return
            except RuntimeError as exc:
                if "not connected" in str(exc).lower():
                    return
                raise
            except json.JSONDecodeError:
                await _send_error(websocket, "invalid_event", "Event must be valid JSON")
                continue

            if not isinstance(raw_event, dict):
                await _send_error(websocket, "invalid_event", "Unsupported event type")
                continue

            # Client heartbeat response — no processing needed.
            if raw_event.get("type") == "pong":
                pong_received.set()
                continue

            if raw_event.get("type") in {"send_message", "send_voice_message"}:
                try:
                    check_websocket_message_rate(user_id)
                except WebSocketRateLimitError as exc:
                    await _send_error(
                        websocket,
                        "rate_limited",
                        f"Too many messages. Retry after {exc.retry_after} seconds.",
                    )
                    continue

            if raw_event.get("type") == "typing":
                await _relay_typing(
                    websocket=websocket,
                    session_factory=session_factory,
                    manager=manager,
                    user_id=user_id,
                    raw_event=raw_event,
                )
                continue

            if raw_event.get("type") == "send_voice_message":
                await _handle_voice_message(
                    websocket=websocket,
                    session_factory=session_factory,
                    manager=manager,
                    user_id=user_id,
                    raw_event=raw_event,
                )
                continue

            if raw_event.get("type") != "send_message":
                await _send_error(websocket, "invalid_event", "Unsupported event type")
                continue

            try:
                event = SendMessageEvent.model_validate(raw_event)
            except ValidationError:
                await _send_error(websocket, "invalid_event", "The send_message event is invalid")
                continue

            async with session_factory() as db:
                service = ChatService(db)
                try:
                    result = await service.send_message(
                        sender_id=user_id,
                        conversation_id=event.conversation_id,
                        client_message_id=event.client_message_id,
                        text=event.text,
                        attachment_id=event.attachment_id,
                        reply_to_message_id=event.reply_to_message_id,
                        forwarded_from_message_id=event.forwarded_from_message_id,
                        mentions=[mention.model_dump() for mention in event.mentions],
                    )
                except ConversationNotFoundError:
                    await db.rollback()
                    await _send_error(websocket, "conversation_not_found", "Conversation was not found")
                    continue
                except ConversationMembershipError:
                    await db.rollback()
                    await _send_error(
                        websocket,
                        "not_conversation_member",
                        "You are not a member of this conversation",
                    )
                    continue
                except DirectMessagingBlockedError:
                    await db.rollback()
                    await _send_error(websocket, "direct_messaging_blocked", "Direct messaging is unavailable")
                    continue
                except ClientMessageIdConflictError:
                    await db.rollback()
                    await _send_error(
                        websocket,
                        "client_message_id_conflict",
                        "client_message_id was already used for different text",
                    )
                    continue
                except SQLAlchemyError:
                    await db.rollback()
                    await _send_error(websocket, "internal_error", "Message could not be persisted")
                    continue

                realtime_message = RealtimeMessage.model_validate(result.message)
                realtime_message.mentions = [
                    MentionSummary.model_validate(mention)
                    for mention in message_mentions(result.message)
                ]
                realtime_message.assistant_generated = result.message.assistant_generated
                # Attached separately: the ORM object has no `attachment` field, and
                # recipients need the file metadata without refetching history.
                attachments = await service.get_attachments_by_message(
                    message_ids=[result.message.id],
                )
                if result.message.id in attachments:
                    realtime_message.attachment = AttachmentResponse.model_validate(
                        attachments[result.message.id],
                    )
                await manager.send_to_user(
                    user_id,
                    MessageCreatedEvent(
                        client_message_id=event.client_message_id,
                        message=realtime_message,
                    ).model_dump(mode="json"),
                )
                if result.created:
                    await manager.send_to_users(
                        result.recipient_ids,
                        MessageReceivedEvent(message=realtime_message).model_dump(mode="json"),
                    )
                    schedule_push(
                        result.recipient_ids,
                        title="New message",
                        body=event.text,
                        url=f"/chat?conversation={result.message.conversation_id}",
                    )
                    schedule_webhook_event("message.created", {
                        "message_id": result.message.id,
                        "conversation_id": result.message.conversation_id,
                        "sender_id": result.message.sender_id,
                        "message_type": "text",
                    })
                    mentioned_user_ids = tuple(
                        mention.user_id
                        for mention in realtime_message.mentions
                        if mention.type == "user" and mention.user_id
                    )
                    if mentioned_user_ids:
                        await manager.send_to_users(
                            mentioned_user_ids,
                            MentionNotificationEvent(
                                message_id=result.message.id,
                                conversation_id=result.message.conversation_id,
                                sender_id=user_id,
                            ).model_dump(mode="json"),
                        )
                    # Same predicate `ChatService` used when it decided to keep this
                    # message private. The two must agree: a message hidden from the
                    # conversation but not answered would simply disappear.
                    if result.for_assistant:
                        # Assistant turns are private and require an explicit
                        # permission. Report a missing permission to the client
                        # instead of silently accepting a message that can never
                        # receive a reply.
                        if not await has_consent(db, user_id, "read_conversations"):
                            await _send_event(
                                websocket,
                                AssistantConsentRequiredEvent(
                                    client_message_id=event.client_message_id,
                                    conversation_id=event.conversation_id,
                                ),
                            )
                        else:
                            # The reply itself reads recent conversation content
                            # and sends it to an LLM, so it uses the same consent
                            # gate as action extraction.
                            assistant_result = await service.create_assistant_reply(
                                trigger_message=result.message,
                            )
                            assistant_message = RealtimeMessage.model_validate(assistant_result.message)
                            assistant_message.mentions = [
                                MentionSummary.model_validate(mention)
                                for mention in message_mentions(assistant_result.message)
                            ]
                            assistant_message.assistant_generated = True
                            await manager.send_to_users(
                                assistant_result.recipient_ids,
                                MessageReceivedEvent(message=assistant_message).model_dump(mode="json"),
                            )
                            schedule_assistant_mention(
                                message_id=result.message.id,
                                conversation_id=result.message.conversation_id,
                                requester_id=user_id,
                                request_text=result.message.original_text,
                                publisher=manager,
                                trusted_timezone=event.client_timezone,
                            )
                    # Guarded by `created`, preserving the existing exactly-once
                    # text-send seam while sharing the same post-text work with a
                    # durably completed voice transcript.
                    schedule_text_dependent_work(
                        message=result.message,
                        publisher=manager,
                        # An assistant request is already private to its author.
                        # Translating it creates no recipient value and made the
                        # assistant thread look like a translation-only feature.
                        translate=not result.for_assistant,
                        # Pass the module aliases so existing socket tests and
                        # integrations that replace these seams keep working.
                        translation_scheduler=schedule_translations,
                        commitment_scheduler=schedule_commitment_detection,
                        profile_scheduler=schedule_profile_inference,
                        embedding_scheduler=schedule_message_embedding,
                    )
    except WebSocketDisconnect:
        return
    finally:
        if ping_task is not None:
            ping_task.cancel()
        if user_id is not None:
            manager.disconnect(user_id, websocket)
