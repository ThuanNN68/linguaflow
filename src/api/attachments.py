"""Attachments HTTP endpoints."""

import logging
import mimetypes
import re
import uuid
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.core.deps import get_current_user
from src.core.rate_limit import llm_limit
from src.database import get_db
from src.database.models import (
    Attachment,
    ConversationMember,
    Message,
    User,
)
from src.schemas.chat import (
    AttachmentPresignResponse,
    AttachmentResponse,
    TTSResponse,
)
from src.services.messaging.attachment_storage import (
    AttachmentStorage,
    AttachmentStorageError,
    AttachmentStorageNotFoundError,
)
from src.services.messaging.chat import (
    ChatService,
    ConversationMembershipError,
    ConversationNotFoundError,
    ConversationValidationError,
)
from src.services.voice.tts import TTSConfigurationError, TTSService, TTSSynthesisError

logger = logging.getLogger(__name__)

router = APIRouter()
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._ -]+")
_MAX_FILENAME_LENGTH = 120


async def _authorized_attachment(
    *, conversation_id: str, attachment_id: str, user_id: str, db: AsyncSession
) -> Attachment:
    service = ChatService(db)
    try:
        await service.get_message_history(user_id=user_id, conversation_id=conversation_id, limit=1)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation was not found") from exc
    except ConversationMembershipError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You are not a member of this conversation") from exc
    except ConversationValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    attachment = await db.scalar(
        select(Attachment).outerjoin(Message, Message.id == Attachment.message_id).where(
            Attachment.id == attachment_id,
            Attachment.conversation_id == conversation_id,
            or_(
                Attachment.message_id.is_(None),
                and_(Message.deleted_at.is_(None), or_(Message.visible_to_user_id.is_(None), Message.visible_to_user_id == user_id)),
            ),
        )
    )
    if attachment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment was not found")
    return attachment


@router.post(
    "/conversations/{conversation_id}/attachments/{attachment_id}/presign",
    response_model=AttachmentPresignResponse,
)
async def presign_conversation_attachment(
    conversation_id: str,
    attachment_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AttachmentPresignResponse:
    """Authorize first, then issue a short-lived URL for private S3 storage."""
    attachment = await _authorized_attachment(
        conversation_id=conversation_id, attachment_id=attachment_id, user_id=current_user.id, db=db
    )
    try:
        signed = await AttachmentStorage().presign(attachment)
    except AttachmentStorageError:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Unable to create attachment URL") from None
    if signed is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Direct attachment URLs are unavailable")
    return AttachmentPresignResponse(url=signed.url, expires_in_seconds=signed.expires_in_seconds)


def _safe_filename(filename: str | None) -> str:
    """Keep a human-readable filename while preventing path traversal."""
    name = Path(filename or "attachment").name
    name = _SAFE_FILENAME.sub("_", name).strip(" ._")
    return (name or "attachment")[:_MAX_FILENAME_LENGTH]


@router.post(
    "/conversations/{conversation_id}/attachments",
    response_model=AttachmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_conversation_attachment(
    conversation_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AttachmentResponse:
    """Upload one file for a conversation the current user belongs to.

    Files are stored outside public static paths. A download requires the same
    conversation-membership check, so knowing a URL never grants access.
    """
    service = ChatService(db)
    try:
        await service.get_message_history(
            user_id=current_user.id,
            conversation_id=conversation_id,
            limit=1,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation was not found") from exc
    except ConversationMembershipError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="You are not a member of this conversation"
        ) from exc
    except ConversationValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    settings = get_settings()
    attachment_id = f"{uuid.uuid4().hex}_{_safe_filename(file.filename)}"
    written = 0
    chunks: list[bytes] = []
    try:
        while chunk := await file.read(1024 * 1024):
            written += len(chunk)
            if written > settings.max_upload_size_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Attachment exceeds the 20 MB limit",
                )
            chunks.append(chunk)
    finally:
        await file.close()

    content_type = file.content_type or mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream"
    payload = b"".join(chunks)
    try:
        await AttachmentStorage(settings).write(
            conversation_id=conversation_id,
            attachment_id=attachment_id,
            data=payload,
            content_type=content_type,
        )
    except AttachmentStorageError:
        logger.warning("Attachment storage upload failed for %s", attachment_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to store attachment",
        ) from None

    # Persisted so a message can claim it later; until then the row is an
    # unattached upload, which is a normal state (docs/api/contract.md §3.7).
    attachment = Attachment(
        id=attachment_id,
        conversation_id=conversation_id,
        uploader_id=current_user.id,
        filename=_safe_filename(file.filename),
        content_type=content_type,
        size=written,
    )
    db.add(attachment)
    await db.commit()

    return AttachmentResponse.model_validate(attachment)


@router.get(
    "/conversations/{conversation_id}/attachments",
    response_model=list[AttachmentResponse],
)
async def list_conversation_attachments(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[AttachmentResponse]:
    """List files that are carried by visible messages in a conversation.

    Uploaded-but-unsent files are intentionally omitted. This keeps the
    conversation details aligned with what members can actually see in chat.
    """
    service = ChatService(db)
    try:
        await service.get_message_history(
            user_id=current_user.id,
            conversation_id=conversation_id,
            limit=1,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation was not found") from exc
    except ConversationMembershipError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="You are not a member of this conversation"
        ) from exc
    except ConversationValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    attachments = (
        await db.scalars(
            select(Attachment)
            .join(Message, Message.id == Attachment.message_id)
            .where(
                Attachment.conversation_id == conversation_id,
                Attachment.message_id.is_not(None),
                Message.deleted_at.is_(None),
                or_(
                    Message.visible_to_user_id.is_(None),
                    Message.visible_to_user_id == current_user.id,
                ),
            )
            .order_by(Attachment.created_at.desc(), Attachment.id.desc())
        )
    ).all()
    return [AttachmentResponse.model_validate(attachment) for attachment in attachments]


@router.get("/conversations/{conversation_id}/attachments/{attachment_id}")
async def download_conversation_attachment(
    conversation_id: str,
    attachment_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Download an attachment after verifying conversation membership."""
    service = ChatService(db)
    try:
        await service.get_message_history(user_id=current_user.id, conversation_id=conversation_id, limit=1)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation was not found") from exc
    except ConversationMembershipError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="You are not a member of this conversation"
        ) from exc
    except ConversationValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # OUTER join, not inner. The visibility filter below is right and has to
    # stay — an attachment carried by a private assistant reply belongs to its
    # one reader (ADR-31) — but an inner join also drops every attachment whose
    # `message_id` is still NULL, and that is every attachment between being
    # uploaded and being sent. The product uploads first and attaches on send,
    # so an inner join makes a file undownloadable during exactly the window the
    # composer needs it.
    #
    # Membership is already enforced above, by `get_message_history`, which is
    # what protects an attachment that no message carries yet.
    attachment = await db.scalar(
        select(Attachment)
        .outerjoin(Message, Message.id == Attachment.message_id)
        .where(
            Attachment.id == attachment_id,
            Attachment.conversation_id == conversation_id,
            or_(
                # Uploaded, not yet sent: no message to judge visibility by.
                Attachment.message_id.is_(None),
                and_(
                    Message.conversation_id == conversation_id,
                    Message.deleted_at.is_(None),
                    or_(
                        Message.visible_to_user_id.is_(None),
                        Message.visible_to_user_id == current_user.id,
                    ),
                ),
            ),
        )
    )
    if attachment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment was not found")

    try:
        stored = await AttachmentStorage().download(attachment)
    except AttachmentStorageNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Attachment was not found",
        ) from None
    except AttachmentStorageError:
        logger.warning("Attachment storage download failed for %s", attachment_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to retrieve attachment",
        ) from None

    if stored.local_path is not None:
        return FileResponse(
            stored.local_path,
            filename=stored.filename,
            media_type=stored.content_type,
        )
    return Response(
        content=stored.data or b"",
        media_type=stored.content_type,
        headers={"Content-Disposition": f'attachment; filename="{stored.filename}"'},
    )


@router.get("/messages/{message_id}/tts", response_model=TTSResponse)
async def get_message_tts(
    message_id: str,
    target_language: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TTSResponse:
    """Get text and metadata to pass to the client TTS engine."""
    message = await db.scalar(
        select(Message)
        .join(
            ConversationMember,
            ConversationMember.conversation_id == Message.conversation_id,
        )
        .where(
            Message.id == message_id,
            Message.deleted_at.is_(None),
            ConversationMember.user_id == current_user.id,
            or_(
                Message.visible_to_user_id.is_(None),
                Message.visible_to_user_id == current_user.id,
            ),
        )
    )
    if message is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found or you don't have access",
        )

    if target_language:
        from src.database.models import TranslationResult

        translation = await db.scalar(
            select(TranslationResult)
            .where(TranslationResult.message_id == message_id, TranslationResult.target_language == target_language)
            .limit(1)
        )
        if translation:
            return TTSResponse(
                text=translation.translated_text,
                language=target_language,
                source="translation",
            )
        else:
            return TTSResponse(
                text=message.original_text,
                language=message.source_language,
                source="original",
            )

    return TTSResponse(
        text=message.original_text,
        language=message.source_language,
        source="original",
    )


@router.get("/messages/{message_id}/tts/audio")
@llm_limit
async def get_message_tts_audio(
    message_id: str,
    request: Request,
    response: Response,
    target_language: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Synthesize an authorized message when browser-native speech is unavailable."""
    del request, response
    resolved = await get_message_tts(
        message_id=message_id,
        target_language=target_language,
        current_user=current_user,
        db=db,
    )
    try:
        speech = await TTSService().synthesize(resolved.text, resolved.language)
    except TTSConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except TTSSynthesisError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Text-to-speech provider is unavailable",
        ) from exc
    return Response(
        content=speech.data,
        media_type=speech.content_type,
        headers={"Cache-Control": "private, no-store"},
    )
