"""Chat HTTP endpoints."""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.websocket import get_connection_manager
from src.core.deps import get_current_user
from src.core.rate_limit import llm_limit
from src.database import get_db
from src.database.models import (
    Attachment,
    CallSession,
    Conversation,
    ConversationMember,
    Feedback,
    Message,
    TranslationResult,
    User,
)
from src.schemas.chat import (
    AttachmentResponse,
    CallEvent,
    CallResponse,
    CallStartRequest,
    ConversationCreateRequest,
    ConversationMemberLeftEvent,
    ConversationMemberSummary,
    ConversationPreferencesUpdate,
    ConversationResponse,
    EditMessageRequest,
    FeedbackRequest,
    FeedbackResponse,
    GroupMembersRequest,
    GroupRoleRequest,
    GroupTransferOwnerRequest,
    GroupUpdateRequest,
    MessageDeletedEvent,
    MessageHistoryResponse,
    MessageReactionSummary,
    MessageReactionsUpdatedEvent,
    MessageReadEvent,
    MessageResponse,
    MessageSearchResponse,
    MessageSearchResult,
    MessageUpdatedEvent,
    ReactionStateResponse,
    ReactionUpdateRequest,
    ReadReceiptResponse,
    SavedMessagesResponse,
    SavedMessageStateResponse,
    TranslationEditRequest,
    TranslationEditResponse,
    TranslationEditSummary,
    TranslationRetryResponse,
    TranslationSummary,
    VoiceTranscriptionRetryResponse,
)
from src.services.delivery.webhooks import schedule_webhook_event
from src.services.identity.audit import record_audit_event
from src.services.identity.profiles import (
    profile_for,
    resolve_profiles,
    resolve_profiles_for_conversations,
    select_for_reader,
)
from src.services.identity.user_settings import get_or_create_user_settings
from src.services.language.correction_log import schedule_correction_record
from src.services.language.customization import resolve_conversation_profile
from src.services.language.translation import schedule_translation_retry, schedule_translations
from src.services.messaging.blocking import (
    DirectMessagingBlockedError,
)
from src.services.messaging.chat import (
    ChatService,
    ChatServiceError,
    ConversationMembershipError,
    ConversationNotFoundError,
    ConversationValidationError,
    MessageAlreadyDeletedError,
    MessageNotFoundError,
    MessageOwnershipError,
    ReferencedUsersNotFoundError,
    TranslationNotFoundError,
    VoiceTranscriptionRetryAttachmentError,
    VoiceTranscriptionRetryStateError,
    decode_message_cursor,
    encode_message_cursor,
    message_mentions,
)
from src.services.messaging.connection_manager import ConnectionManager
from src.services.messaging.rtc import (
    CallJoin,
    CallNotFoundError,
    CallService,
    CallStateError,
    RTCProviderUnavailableError,
    get_rtc_provider,
)
from src.services.voice.voice_transcription import schedule_voice_transcription

logger = logging.getLogger(__name__)

router = APIRouter()


async def _conversation_response(
    service: ChatService,
    conversation: Conversation,
    last_message: tuple[str, datetime, str | None, str | None] | None = None,
    manager: ConnectionManager | None = None,
    unread_count: int = 0,
    members: Sequence[User] | None = None,
    profiles: dict[str, str] | None = None,
    preference: ConversationMember | None = None,
) -> ConversationResponse:
    """Build the minimal conversation representation for an authorized user.

    Args:
        service: Open chat service.
        conversation: Conversation being rendered.
        last_message: Preview text and time, already resolved for the calling
            account. A conversation with no messages passes None.
        manager: Live connection registry, for `online_member_ids`.
        unread_count: Messages this reader has not seen (§3.8).
        members: Already-loaded members. The list endpoint passes them in from
            one batched query; a single-conversation caller lets this load them.
        profiles: Each member's standing in *this* conversation, already
            resolved. Passed in for the same reason `members` is: the list
            endpoint resolves every conversation in one query, and looking them
            up here would put an N+1 back into it.

    Returns:
        The conversation as the REST contract defines it (docs/api/contract.md §3.5).
    """
    if members is None:
        members = await service.get_conversation_members(conversation_id=conversation.id)
    profiles = profiles or {}
    role_rows = await service._db.execute(
        select(ConversationMember.user_id, ConversationMember.role).where(
            ConversationMember.conversation_id == conversation.id
        )
    )
    group_roles = dict(role_rows.all())
    return ConversationResponse(
        id=conversation.id,
        type=conversation.type,
        title=conversation.title,
        description=conversation.description,
        created_by=conversation.created_by,
        created_at=conversation.created_at,
        member_ids=[member.id for member in members],
        # Built field by field rather than validated from the ORM row: a
        # standing belongs to a member *within a conversation*, so it is not on
        # the User object and `from_attributes` has nowhere to read it from.
        members=[
            ConversationMemberSummary(
                id=member.id,
                email=member.email,
                username=member.username,
                display_name=member.display_name,
                preferred_language=member.preferred_language,
                group_role=group_roles.get(member.id, "member"),
                honorific_profile=profile_for(profiles, member.id),
            )
            for member in members
        ],
        last_message=last_message[0] if last_message else None,
        last_message_at=last_message[1] if last_message else None,
        last_message_type=last_message[2] if last_message else None,
        last_message_transcription_status=last_message[3] if last_message else None,
        online_member_ids=list(manager.online_user_ids(member.id for member in members)) if manager else [],
        unread_count=unread_count,
        is_pinned=preference.is_pinned if preference else False,
        pinned_at=preference.pinned_at if preference else None,
        is_muted=preference.is_muted if preference else False,
    )


@router.post(
    "/conversations",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    request: ConversationCreateRequest,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationResponse:
    """Create the minimal direct or group conversation required for chat.

    Asking twice for the same direct conversation is answered with `200` and the
    conversation that already exists, not a second one (docs/api/contract.md §3.5).
    """
    service = ChatService(db)
    try:
        result = await service.create_conversation(
            creator_id=current_user.id,
            conversation_type=request.type,
            member_ids=request.member_ids,
            title=request.title,
        )
    except ReferencedUsersNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="One or more referenced users do not exist",
        ) from exc
    except ConversationValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except DirectMessagingBlockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Direct messaging is unavailable",
        ) from exc

    if not result.created:
        response.status_code = status.HTTP_200_OK

    return await _conversation_response(
        service,
        result.conversation,
        profiles=await resolve_profiles(db, result.conversation.id),
        preference=await db.get(ConversationMember, (result.conversation.id, current_user.id)),
    )


@router.post("/assistant/conversation", response_model=ConversationResponse)
async def get_or_create_assistant_conversation(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationResponse:
    """Open the caller's durable private thread with the Assistant."""
    service = ChatService(db)
    result = await service.get_or_create_assistant_conversation(user_id=current_user.id)
    return await _conversation_response(
        service,
        result.conversation,
        profiles=await resolve_profiles(db, result.conversation.id),
    )


async def _group_access(
    db: AsyncSession, conversation_id: str, user_id: str
) -> tuple[Conversation, ConversationMember]:
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None or conversation.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Group was not found")
    if conversation.type != "group":
        raise HTTPException(status_code=400, detail="This operation is only available for groups")
    membership = await db.get(ConversationMember, (conversation_id, user_id))
    if membership is None:
        raise HTTPException(status_code=403, detail="You are not a member of this group")
    return conversation, membership


@router.post("/conversations/{conversation_id}/members", status_code=204)
async def add_group_members(
    conversation_id: str,
    payload: GroupMembersRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    _, actor = await _group_access(db, conversation_id, current_user.id)
    if actor.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Only group administrators can add members")
    found = set((await db.scalars(select(User.id).where(User.id.in_(payload.user_ids)))).all())
    if found != set(payload.user_ids):
        raise HTTPException(status_code=404, detail="One or more users were not found")
    existing = set(
        (
            await db.scalars(
                select(ConversationMember.user_id).where(
                    ConversationMember.conversation_id == conversation_id,
                    ConversationMember.user_id.in_(payload.user_ids),
                )
            )
        ).all()
    )
    db.add_all(
        ConversationMember(conversation_id=conversation_id, user_id=user_id, role="member")
        for user_id in found - existing
    )
    await db.commit()


@router.patch("/conversations/{conversation_id}", status_code=204)
async def update_group_details(
    conversation_id: str,
    payload: GroupUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    conversation, membership = await _group_access(db, conversation_id, current_user.id)
    if membership.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Only group administrators can edit group information")
    conversation.title = payload.title
    conversation.description = payload.description
    await db.commit()


@router.delete("/conversations/{conversation_id}/members/{user_id}", status_code=204)
async def remove_group_member(
    conversation_id: str,
    user_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    _, actor = await _group_access(db, conversation_id, current_user.id)
    target = await db.get(ConversationMember, (conversation_id, user_id))
    if actor.role not in {"owner", "admin"} or target is None:
        raise HTTPException(status_code=403 if target else 404, detail="Member cannot be removed")
    if target.role == "owner" or (actor.role == "admin" and target.role == "admin"):
        raise HTTPException(status_code=403, detail="You cannot remove this group administrator")
    await db.delete(target)
    await db.commit()


@router.patch("/conversations/{conversation_id}/members/{user_id}/role", status_code=204)
async def update_group_role(
    conversation_id: str,
    user_id: str,
    payload: GroupRoleRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    _, actor = await _group_access(db, conversation_id, current_user.id)
    target = await db.get(ConversationMember, (conversation_id, user_id))
    if actor.role != "owner" or target is None or target.role == "owner":
        raise HTTPException(status_code=403, detail="Only the owner can change this role")
    previous_role = target.role
    target.role = payload.role
    await record_audit_event(
        db, actor_id=current_user.id, action="conversation.member_role_changed",
        resource_type="conversation_member", resource_id=f"{conversation_id}:{user_id}",
        metadata={"from_role": previous_role, "to_role": payload.role},
    )
    await db.commit()


@router.post("/conversations/{conversation_id}/leave", status_code=204)
async def leave_group(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    manager: ConnectionManager = Depends(get_connection_manager),
) -> None:
    conversation, membership = await _group_access(db, conversation_id, current_user.id)
    member_ids = await ChatService(db).get_conversation_member_ids(conversation_id=conversation_id)
    if membership.role == "owner" and len(member_ids) > 1:
        raise HTTPException(status_code=409, detail="Transfer ownership before leaving the group")
    if len(member_ids) == 1:
        await db.delete(conversation)
    else:
        await db.delete(membership)
    await db.commit()
    await manager.send_to_users(
        (member_id for member_id in member_ids if member_id != current_user.id),
        ConversationMemberLeftEvent(conversation_id=conversation_id, user_id=current_user.id).model_dump(mode="json"),
    )


@router.post("/conversations/{conversation_id}/owner", status_code=204)
async def transfer_group_owner(
    conversation_id: str,
    payload: GroupTransferOwnerRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    conversation, actor = await _group_access(db, conversation_id, current_user.id)
    target = await db.get(ConversationMember, (conversation_id, payload.user_id))
    if actor.role != "owner" or target is None or target.user_id == actor.user_id:
        raise HTTPException(status_code=403, detail="Ownership can only be transferred to another group member")
    actor.role = "admin"
    target.role = "owner"
    conversation.created_by = target.user_id
    await record_audit_event(
        db, actor_id=current_user.id, action="conversation.ownership_transferred",
        resource_type="conversation", resource_id=conversation_id, metadata={"new_owner_id": target.user_id},
    )
    await db.commit()


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_group(
    conversation_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    conversation, membership = await _group_access(db, conversation_id, current_user.id)
    if membership.role != "owner":
        raise HTTPException(status_code=403, detail="Only the group owner can delete the group")
    conversation.deleted_at = datetime.now(UTC)
    await db.commit()


@router.patch(
    "/conversations/{conversation_id}/preferences",
    response_model=ConversationResponse,
)
async def update_conversation_preferences(
    conversation_id: str,
    payload: ConversationPreferencesUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationResponse:
    service = ChatService(db)
    try:
        preference = await service.update_conversation_preferences(
            user_id=current_user.id,
            conversation_id=conversation_id,
            is_pinned=payload.is_pinned,
            is_muted=payload.is_muted,
        )
        conversation = await db.get(Conversation, conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(conversation_id)
    except ChatServiceError as exc:
        raise _message_error(exc) from exc
    return await _conversation_response(
        service,
        conversation,
        profiles=await resolve_profiles(db, conversation_id),
        preference=preference,
    )


@router.get("/conversations", response_model=list[ConversationResponse])
async def list_conversations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    manager: ConnectionManager = Depends(get_connection_manager),
) -> list[ConversationResponse]:
    """List conversations that contain the authenticated user."""
    # `get_or_create_user_settings` may commit when it creates the first row.
    # Keep request identity and display settings as plain values before any
    # operation that can alter ORM expiration state; accessing an expired async
    # attribute later would attempt implicit I/O and raise MissingGreenlet.
    user_id = current_user.id
    reader_language = current_user.preferred_language
    service = ChatService(db)
    conversations = await service.list_conversations(user_id=user_id)
    conversation_ids = [conversation.id for conversation in conversations]
    settings = await get_or_create_user_settings(db, user_id)
    profiles = await resolve_profiles_for_conversations(db, conversation_ids)
    last_messages = await service.get_last_messages(
        conversation_ids=conversation_ids,
        reader_language=reader_language,
        reader_id=user_id,
        # The caller's own standing per conversation, so the sidebar preview
        # picks the same translation the conversation itself will show.
        reader_profiles={
            conversation_id: profile_for(members, user_id) for conversation_id, members in profiles.items()
        },
        reader_tones={conversation_id: settings.translation_tone for conversation_id in conversation_ids},
    )
    unread = await service.get_unread_counts(
        user_id=user_id,
        conversation_ids=conversation_ids,
    )
    members = await service.get_members_by_conversation(conversation_ids=conversation_ids)
    preferences = await service.get_member_preferences_for_conversations(
        user_id=user_id,
        conversation_ids=conversation_ids,
    )
    return [
        await _conversation_response(
            service,
            conversation,
            last_messages.get(conversation.id),
            manager,
            unread.get(conversation.id, 0),
            members.get(conversation.id, []),
            profiles.get(conversation.id, {}),
            preferences.get(conversation.id),
        )
        for conversation in conversations
    ]


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=MessageHistoryResponse | list[MessageResponse],
)
async def get_conversation_messages(
    conversation_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    before: str | None = Query(default=None, description="Opaque composite message cursor"),
    offset: int = Query(default=0, ge=0, le=10_000),
    paginated: bool = Query(default=False, description="Return the cursor page envelope"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageHistoryResponse | list[MessageResponse]:
    """Return a deterministic cursor page for an authorized member.

    ``offset`` remains available for older clients, but cannot be combined with
    ``before``. New clients should follow ``next_cursor`` because offset scans
    grow linearly as a conversation becomes older.
    """
    service = ChatService(db)
    try:
        before_created_at, before_id = decode_message_cursor(before) if before else (None, None)
        use_page_envelope = paginated or before is not None
        messages = await service.get_message_history(
            user_id=current_user.id,
            conversation_id=conversation_id,
            limit=limit + 1 if use_page_envelope else limit,
            before_created_at=before_created_at,
            before_id=before_id,
            offset=offset,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation was not found",
        ) from exc
    except ConversationMembershipError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this conversation",
        ) from exc
    except ConversationValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    has_more = use_page_envelope and len(messages) > limit
    page = messages[-limit:] if has_more else messages
    live_message_ids = [message.id for message in page if message.deleted_at is None]
    translations = await _translations_by_message(
        db,
        live_message_ids,
        reader_id=current_user.id,
        conversation_id=conversation_id,
    )
    attachments = await service.get_attachments_by_message(message_ids=live_message_ids)
    saved_message_ids = await service.saved_message_ids(user_id=current_user.id, message_ids=live_message_ids)
    reactions_by_message = await service.reactions_by_message(message_ids=live_message_ids)
    items = [
        _message_response(
            message,
            translations=translations.get(message.id, []),
            attachment=attachments.get(message.id),
            is_saved=message.id in saved_message_ids,
            reactions=reactions_by_message.get(message.id, []),
        )
        for message in page
    ]
    if not use_page_envelope:
        return items
    return MessageHistoryResponse(
        items=items,
        has_more=has_more,
        next_cursor=(encode_message_cursor(page[0].created_at, page[0].id) if has_more and page else None),
    )


def _message_response(
    message: Message,
    *,
    translations: list[TranslationSummary],
    attachment: Attachment | None,
    is_saved: bool,
    reactions: list[tuple[str, int, list[str]]],
) -> MessageResponse:
    """Serialize a message consistently for history and search results."""
    return MessageResponse(
        id=message.id,
        client_message_id=message.client_message_id,
        conversation_id=message.conversation_id,
        sender_id=message.sender_id,
        original_text="" if message.deleted_at else message.original_text,
        message_type=message.message_type,
        transcription_status=message.transcription_status,
        source_language=message.source_language,
        mentions=message_mentions(message),
        assistant_generated=message.assistant_generated,
        translations=translations,
        created_at=message.created_at,
        edited_at=message.edited_at,
        deleted_at=message.deleted_at,
        attachment=AttachmentResponse.model_validate(attachment) if attachment else None,
        reply_to_message_id=message.reply_to_message_id,
        forwarded_from_message_id=message.forwarded_from_message_id,
        is_saved=is_saved,
        reactions=_reaction_summaries(reactions),
    )


def _search_snippet(text: str, query: str, *, radius: int = 72) -> str:
    """Return a short, stable excerpt around the case-insensitive match."""
    index = text.lower().find(query.lower())
    if index < 0:
        return text[: radius * 2]
    start = max(0, index - radius)
    end = min(len(text), index + len(query) + radius)
    return f"{'…' if start else ''}{text[start:end]}{'…' if end < len(text) else ''}"


@router.get(
    "/conversations/{conversation_id}/messages/search",
    response_model=MessageSearchResponse,
)
async def search_conversation_messages(
    conversation_id: str,
    q: str = Query(min_length=1, max_length=255),
    limit: int = Query(default=20, ge=1, le=100),
    before_created_at: datetime | None = None,
    before_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageSearchResponse:
    """Search a conversation without exposing another reader's translation.

    Search can match a stored translated rendering, but the response is built
    from the same reader-specific selection as history.  A manager's rendering
    can therefore never be returned to a peer merely because both rows contain
    a similar word.
    """
    service = ChatService(db)
    lowered_query = q.strip().lower()
    preferred_language = current_user.preferred_language
    visible_items: list[MessageSearchResult] = []

    current_before_created_at = before_created_at
    current_before_id = before_id
    batch_size = min(max(limit * 2, 50), 100)
    has_more = False

    while len(visible_items) <= limit:
        try:
            candidates = await service.search_messages(
                user_id=current_user.id,
                conversation_id=conversation_id,
                query=q,
                reader_language=preferred_language,
                limit=batch_size,
                before_created_at=current_before_created_at,
                before_id=current_before_id,
            )
        except ChatServiceError as exc:
            raise _message_error(exc) from exc

        if not candidates:
            break

        candidate_has_more = len(candidates) > batch_size
        batch_candidates = candidates[:batch_size]
        message_ids = [message.id for message in batch_candidates]

        translations = await _translations_by_message(
            db,
            message_ids,
            reader_id=current_user.id,
            conversation_id=conversation_id,
        )
        attachments = await service.get_attachments_by_message(message_ids=message_ids)
        saved_message_ids = await service.saved_message_ids(user_id=current_user.id, message_ids=message_ids)
        reactions_by_message = await service.reactions_by_message(message_ids=message_ids)

        for message in batch_candidates:
            original_match = lowered_query in message.original_text.lower()
            readable_translation = next(
                (
                    row
                    for row in translations.get(message.id, [])
                    if row.target_language == preferred_language and lowered_query in row.translated_text.lower()
                ),
                None,
            )
            if not original_match and readable_translation is None:
                continue
            matched_text = message.original_text if original_match else readable_translation.translated_text
            visible_items.append(
                MessageSearchResult(
                    message=_message_response(
                        message,
                        translations=translations.get(message.id, []),
                        attachment=attachments.get(message.id),
                        is_saved=message.id in saved_message_ids,
                        reactions=reactions_by_message.get(message.id, []),
                    ),
                    matched_in="original" if original_match else "translation",
                    snippet=_search_snippet(matched_text, q.strip()),
                )
            )

        if len(visible_items) > limit:
            has_more = True
            break

        if not candidate_has_more:
            has_more = False
            break

        last_candidate = batch_candidates[-1]
        current_before_created_at = last_candidate.created_at
        current_before_id = last_candidate.id

    page = visible_items[:limit]
    last = page[-1].message if page else None
    return MessageSearchResponse(
        items=page,
        has_more=has_more,
        next_before_created_at=last.created_at if has_more and last else None,
        next_before_id=last.id if has_more and last else None,
    )


@router.get(
    "/saved-messages",
    response_model=SavedMessagesResponse,
)
async def list_saved_messages(
    limit: int = Query(default=20, ge=1, le=100),
    before_created_at: datetime | None = None,
    before_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SavedMessagesResponse:
    """List bookmark history ordered by bookmark creation time."""
    service = ChatService(db)
    try:
        rows = await service.list_saved_messages(
            user_id=current_user.id,
            limit=limit,
            before_created_at=before_created_at,
            before_id=before_id,
        )
    except ConversationValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    has_more = len(rows) > limit
    page_rows = rows[:limit]

    messages = [msg for msg, _ in page_rows]
    saved_entries = [saved for _, saved in page_rows]
    message_ids = [msg.id for msg in messages]

    conversations_by_message = {msg.id: msg.conversation_id for msg in messages}
    translations_by_msg: dict[str, list[TranslationSummary]] = {}
    for conv_id in set(conversations_by_message.values()):
        c_msg_ids = [m.id for m in messages if m.conversation_id == conv_id]
        t = await _translations_by_message(
            db,
            c_msg_ids,
            reader_id=current_user.id,
            conversation_id=conv_id,
        )
        translations_by_msg.update(t)

    attachments = await service.get_attachments_by_message(message_ids=message_ids)
    reactions_by_message = await service.reactions_by_message(message_ids=message_ids)

    items = [
        _message_response(
            message,
            translations=translations_by_msg.get(message.id, []),
            attachment=attachments.get(message.id),
            is_saved=True,
            reactions=reactions_by_message.get(message.id, []),
        )
        for message in messages
    ]

    last_saved = saved_entries[-1] if saved_entries else None
    return SavedMessagesResponse(
        items=items,
        has_more=has_more,
        next_before_created_at=last_saved.created_at if has_more and last_saved else None,
        next_before_id=last_saved.id if has_more and last_saved else None,
    )


@router.put(
    "/conversations/{conversation_id}/messages/{message_id}/saved",
    response_model=SavedMessageStateResponse,
)
async def save_message(
    conversation_id: str,
    message_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SavedMessageStateResponse:
    service = ChatService(db)
    try:
        is_saved = await service.set_saved_message(
            user_id=current_user.id,
            conversation_id=conversation_id,
            message_id=message_id,
            is_saved=True,
        )
    except ChatServiceError as exc:
        raise _message_error(exc) from exc
    return SavedMessageStateResponse(message_id=message_id, is_saved=is_saved)


@router.delete(
    "/conversations/{conversation_id}/messages/{message_id}/saved",
    response_model=SavedMessageStateResponse,
)
async def unsave_message(
    conversation_id: str,
    message_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SavedMessageStateResponse:
    service = ChatService(db)
    try:
        is_saved = await service.set_saved_message(
            user_id=current_user.id,
            conversation_id=conversation_id,
            message_id=message_id,
            is_saved=False,
        )
    except ChatServiceError as exc:
        raise _message_error(exc) from exc
    return SavedMessageStateResponse(message_id=message_id, is_saved=is_saved)


def _reaction_summaries(rows: list[tuple[str, int, list[str]]]) -> list[MessageReactionSummary]:
    return [MessageReactionSummary(emoji=emoji, count=count, user_ids=user_ids) for emoji, count, user_ids in rows]


@router.put(
    "/conversations/{conversation_id}/messages/{message_id}/reactions",
    response_model=ReactionStateResponse,
)
async def add_message_reaction(
    conversation_id: str,
    message_id: str,
    payload: ReactionUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    manager: ConnectionManager = Depends(get_connection_manager),
) -> ReactionStateResponse:
    service = ChatService(db)
    try:
        reactions = await service.update_reaction(
            user_id=current_user.id,
            conversation_id=conversation_id,
            message_id=message_id,
            emoji=payload.emoji,
            add=True,
        )
        member_ids = await service.get_conversation_member_ids(conversation_id=conversation_id)
    except ChatServiceError as exc:
        raise _message_error(exc) from exc
    response = ReactionStateResponse(message_id=message_id, reactions=_reaction_summaries(reactions))
    await manager.send_to_users(
        member_ids,
        MessageReactionsUpdatedEvent(
            conversation_id=conversation_id, message_id=message_id, reactions=response.reactions
        ).model_dump(mode="json"),
    )
    return response


@router.delete(
    "/conversations/{conversation_id}/messages/{message_id}/reactions",
    response_model=ReactionStateResponse,
)
async def remove_message_reaction(
    conversation_id: str,
    message_id: str,
    payload: ReactionUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    manager: ConnectionManager = Depends(get_connection_manager),
) -> ReactionStateResponse:
    service = ChatService(db)
    try:
        reactions = await service.update_reaction(
            user_id=current_user.id,
            conversation_id=conversation_id,
            message_id=message_id,
            emoji=payload.emoji,
            add=False,
        )
        member_ids = await service.get_conversation_member_ids(conversation_id=conversation_id)
    except ChatServiceError as exc:
        raise _message_error(exc) from exc
    response = ReactionStateResponse(message_id=message_id, reactions=_reaction_summaries(reactions))
    await manager.send_to_users(
        member_ids,
        MessageReactionsUpdatedEvent(
            conversation_id=conversation_id, message_id=message_id, reactions=response.reactions
        ).model_dump(mode="json"),
    )
    return response


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/translate",
    response_model=TranslationRetryResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
@llm_limit
async def retry_message_translation(
    conversation_id: str,
    message_id: str,
    request: Request,
    response: Response,
    review_recipient: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    manager: ConnectionManager = Depends(get_connection_manager),
) -> TranslationRetryResponse:
    """Request a fresh translation for the calling member only.

    This is an explicit retry, not an implicit cache refresh.  The scheduler
    bypasses its phrase cache and updates the caller's persisted language /
    standing / tone bucket in place before sending the normal realtime event.
    """
    service = ChatService(db)
    try:
        message = await service.get_message_for_member(
            user_id=current_user.id,
            conversation_id=conversation_id,
            message_id=message_id,
        )
        if message.deleted_at is not None:
            raise MessageAlreadyDeletedError(message_id)
    except ChatServiceError as exc:
        raise _message_error(exc) from exc

    reader_id = current_user.id
    viewer_ids = (current_user.id,)
    if review_recipient:
        conversation = await db.get(Conversation, conversation_id)
        if conversation is None or conversation.type != "direct" or message.sender_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Recipient translation can only be retried for your own direct message",
            )
        member_ids = tuple(
            member_id
            for member_id in await service.get_conversation_member_ids(conversation_id=conversation_id)
            if member_id != current_user.id
        )
        if len(member_ids) != 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The direct conversation no longer has exactly one recipient",
            )
        reader_id = member_ids[0]
        viewer_ids = (current_user.id, reader_id)

    schedule_translation_retry(
        message=message,
        reader_id=reader_id,
        viewer_ids=viewer_ids,
        publisher=manager,
    )
    return TranslationRetryResponse(message_id=message_id)


@router.post(
    "/messages/{message_id}/transcription/retry",
    response_model=VoiceTranscriptionRetryResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_voice_transcription(
    message_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    manager: ConnectionManager = Depends(get_connection_manager),
) -> VoiceTranscriptionRetryResponse:
    """Atomically retry STT for the same failed voice message and audio."""
    try:
        result = await ChatService(db).retry_voice_transcription(
            user_id=current_user.id,
            message_id=message_id,
        )
    except ChatServiceError as exc:
        raise _message_error(exc) from exc

    # The service commit is authoritative. Only its guarded transition winner
    # reaches this call, so concurrent requests cannot launch another STT task.
    schedule_voice_transcription(
        message_id=result.message_id,
        conversation_id=result.conversation_id,
        publisher=manager,
    )
    return VoiceTranscriptionRetryResponse(
        message_id=result.message_id,
        conversation_id=result.conversation_id,
    )


def _message_error(exc: ChatServiceError) -> HTTPException:
    """Map a message-mutation failure onto its documented status (§3.6)."""
    if isinstance(exc, MessageNotFoundError | ConversationNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, MessageOwnershipError | ConversationMembershipError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(
        exc,
        MessageAlreadyDeletedError
        | VoiceTranscriptionRetryAttachmentError
        | VoiceTranscriptionRetryStateError,
    ):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _call_response(call: CallSession, join: CallJoin | None = None) -> CallResponse:
    """Serialize public call state without leaking a provider credential."""
    return CallResponse(
        call_id=call.id,
        conversation_id=call.conversation_id,
        caller_id=call.caller_id,
        callee_id=call.callee_id,
        call_type=call.call_type,
        status=call.status,
        room_url=join.room_url if join else None,
        join_token=join.join_token if join else None,
        created_at=call.created_at,
        answered_at=call.answered_at,
        ended_at=call.ended_at,
    )


def _call_event(call: CallSession, event_type: str) -> dict[str, object]:
    """Build the safe WebSocket notification consumed by the call UI."""
    return CallEvent(
        type=event_type,  # type: ignore[arg-type]
        call_id=call.id,
        conversation_id=call.conversation_id,
        caller_id=call.caller_id,
        callee_id=call.callee_id,
        call_type=call.call_type,
        status=call.status,
    ).model_dump(mode="json")


def _call_error(exc: Exception) -> HTTPException:
    if isinstance(exc, CallNotFoundError | ConversationNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ConversationMembershipError | DirectMessagingBlockedError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, RTCProviderUnavailableError):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.post(
    "/conversations/{conversation_id}/calls",
    response_model=CallResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_conversation_call(
    conversation_id: str,
    payload: CallStartRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    manager: ConnectionManager = Depends(get_connection_manager),
) -> CallResponse:
    """Begin a direct call and notify only the authenticated callee."""
    try:
        provider = get_rtc_provider()
        service = CallService(db, provider)
        join = await service.start_call(
            caller_id=current_user.id,
            conversation_id=conversation_id,
            call_type=payload.call_type,
        )
    except (
        CallNotFoundError,
        CallStateError,
        ConversationNotFoundError,
        ConversationMembershipError,
        DirectMessagingBlockedError,
        RTCProviderUnavailableError,
    ) as exc:
        raise _call_error(exc) from exc

    await manager.send_to_user(join.session.callee_id, _call_event(join.session, "call_incoming"))
    return _call_response(join.session)


@router.post("/calls/{call_id}/accept", response_model=CallResponse)
async def accept_incoming_call(
    call_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    manager: ConnectionManager = Depends(get_connection_manager),
) -> CallResponse:
    """Accept a ringing call and issue the callee's short-lived join token."""
    try:
        provider = get_rtc_provider()
        service = CallService(db, provider)
        join = await service.accept_call(call_id=call_id, user_id=current_user.id)
    except (CallNotFoundError, CallStateError, RTCProviderUnavailableError) as exc:
        # A failed token issue transitions the durable call row to `failed`.
        # Tell the caller immediately instead of leaving their ring UI stuck.
        if isinstance(exc, RTCProviderUnavailableError):
            try:
                service = CallService(db)
                failed_call = await service.get_call(call_id=call_id, user_id=current_user.id)
                await manager.send_to_user(failed_call.caller_id, _call_event(failed_call, "call_failed"))
            except Exception:
                pass
        raise _call_error(exc) from exc

    await manager.send_to_user(join.session.caller_id, _call_event(join.session, "call_accepted"))
    return _call_response(join.session, join)


@router.get("/calls/{call_id}/join", response_model=CallResponse)
async def join_accepted_call(
    call_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CallResponse:
    """Issue this authenticated participant's Daily token after acceptance."""
    try:
        provider = get_rtc_provider()
        service = CallService(db, provider)
        join = await service.join_call(call_id=call_id, user_id=current_user.id)
    except (CallNotFoundError, CallStateError, RTCProviderUnavailableError) as exc:
        raise _call_error(exc) from exc
    return _call_response(join.session, join)


@router.post("/calls/{call_id}/reject", response_model=CallResponse)
async def reject_incoming_call(
    call_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    manager: ConnectionManager = Depends(get_connection_manager),
) -> CallResponse:
    """Reject a ringing call and notify its caller."""
    try:
        try:
            provider = get_rtc_provider()
        except RTCProviderUnavailableError:
            provider = None
        service = CallService(db, provider)
        call = await service.reject_call(call_id=call_id, user_id=current_user.id)
    except (CallNotFoundError, CallStateError) as exc:
        raise _call_error(exc) from exc
    await manager.send_to_user(call.caller_id, _call_event(call, "call_rejected"))
    return _call_response(call)


@router.post("/calls/{call_id}/end", response_model=CallResponse)
async def end_active_call(
    call_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    manager: ConnectionManager = Depends(get_connection_manager),
) -> CallResponse:
    """End an active/ringing call for both participants."""
    try:
        try:
            provider = get_rtc_provider()
        except RTCProviderUnavailableError:
            provider = None
        service = CallService(db, provider)
        call = await service.end_call(call_id=call_id, user_id=current_user.id)
    except CallNotFoundError as exc:
        raise _call_error(exc) from exc
    other_id = call.callee_id if current_user.id == call.caller_id else call.caller_id
    await manager.send_to_user(other_id, _call_event(call, "call_ended"))
    return _call_response(call)


@router.post(
    "/conversations/{conversation_id}/read",
    response_model=ReadReceiptResponse,
)
async def mark_conversation_read(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    manager: ConnectionManager = Depends(get_connection_manager),
) -> ReadReceiptResponse:
    """Mark everything in a conversation as read by the caller (§3.8).

    Other members are told so their own copy of the thread can show the message
    as seen rather than merely delivered.
    """
    service = ChatService(db)
    try:
        read_at = await service.mark_conversation_read(
            user_id=current_user.id,
            conversation_id=conversation_id,
        )
        member_ids = await service.get_conversation_member_ids(
            conversation_id=conversation_id,
        )
    except ChatServiceError as exc:
        raise _message_error(exc) from exc

    reader_settings = await get_or_create_user_settings(db, current_user.id)
    if reader_settings.read_receipts:
        await manager.send_to_users(
            tuple(member_id for member_id in member_ids if member_id != current_user.id),
            MessageReadEvent(
                conversation_id=conversation_id,
                user_id=current_user.id,
                read_at=read_at,
            ).model_dump(mode="json"),
        )
    return ReadReceiptResponse()


@router.patch(
    "/conversations/{conversation_id}/messages/{message_id}",
    response_model=MessageResponse,
)
async def edit_message(
    conversation_id: str,
    message_id: str,
    payload: EditMessageRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    manager: ConnectionManager = Depends(get_connection_manager),
) -> MessageResponse:
    """Replace the text of a message the caller sent (F-06).

    The previous translations are discarded and the message is translated again
    in the background, because a translation of retracted text is worse than no
    translation at all.
    """
    service = ChatService(db)
    try:
        message, recipient_ids = await service.edit_message(
            user_id=current_user.id,
            conversation_id=conversation_id,
            message_id=message_id,
            text=payload.text,
        )
    except ChatServiceError as exc:
        raise _message_error(exc) from exc

    await record_audit_event(
        db, actor_id=current_user.id, action="message.deleted", resource_type="message",
        resource_id=message.id, metadata={"conversation_id": conversation_id},
    )
    await db.commit()

    await manager.send_to_users(
        recipient_ids,
        MessageUpdatedEvent(
            message_id=message.id,
            conversation_id=message.conversation_id,
            original_text=message.original_text,
            edited_at=message.edited_at,
        ).model_dump(mode="json"),
    )
    if message.visible_to_user_id is None:
        schedule_translations(message=message, publisher=manager)

    return MessageResponse(
        id=message.id,
        client_message_id=message.client_message_id,
        conversation_id=message.conversation_id,
        sender_id=message.sender_id,
        original_text=message.original_text,
        message_type=message.message_type,
        transcription_status=message.transcription_status,
        source_language=message.source_language,
        mentions=message_mentions(message),
        assistant_generated=message.assistant_generated,
        translations=[],
        created_at=message.created_at,
        edited_at=message.edited_at,
        deleted_at=message.deleted_at,
    )


@router.delete(
    "/conversations/{conversation_id}/messages/{message_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_message(
    conversation_id: str,
    message_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    manager: ConnectionManager = Depends(get_connection_manager),
) -> None:
    """Withdraw a message the caller sent, keeping its row (F-06)."""
    service = ChatService(db)
    try:
        message, recipient_ids = await service.delete_message(
            user_id=current_user.id,
            conversation_id=conversation_id,
            message_id=message_id,
        )
    except ChatServiceError as exc:
        raise _message_error(exc) from exc

    await manager.send_to_users(
        recipient_ids,
        MessageDeletedEvent(
            message_id=message.id,
            conversation_id=message.conversation_id,
            deleted_at=message.deleted_at,
        ).model_dump(mode="json"),
    )
    schedule_webhook_event("message.deleted", {
        "message_id": message.id,
        "conversation_id": message.conversation_id,
        "actor_id": current_user.id,
    })


@router.post(
    "/translations/{translation_id}/feedback",
    response_model=FeedbackResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_translation_feedback(
    translation_id: str,
    payload: FeedbackRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FeedbackResponse:
    """Record the authenticated reader's rating and optional correction (F-05).

    Re-submitting replaces that reader's previous verdict on the same
    translation rather than adding a second row.
    """
    service = ChatService(db)
    try:
        feedback = await service.submit_translation_feedback(
            user_id=current_user.id,
            translation_id=translation_id,
            rating=payload.rating,
            correction=payload.correction,
        )
    except TranslationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Translation was not found",
        ) from exc
    except ConversationMembershipError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this conversation",
        ) from exc
    except ConversationValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return FeedbackResponse(feedback_id=feedback.id)


@router.post(
    "/translations/{translation_id}/edits",
    response_model=TranslationEditResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_translation_edit(
    translation_id: str,
    payload: TranslationEditRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TranslationEditResponse:
    """Store the caller's own wording for a translation (docs/api/contract.md §3.10).

    Each call appends, so editing again keeps the earlier attempt. The text is
    private to its author among conversation members and no WebSocket event
    follows: nobody else's screen changes because of it. Administrators may
    read an anonymous source/machine/edit comparison in the quality-review
    queue, but never the editor or conversation that produced it.

    With `consent_to_share`, and only with it, a second and much narrower record
    is written in the background: the term the machine used, the term this
    reader used instead, and a few words around it with identifiers removed.
    That record — not this one — is what the glossary is mined from (ADR-28).
    """
    service = ChatService(db)
    try:
        edit, translation, message = await service.submit_translation_edit(
            user_id=current_user.id,
            translation_id=translation_id,
            edited_text=payload.edited_text,
        )
    except TranslationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Translation was not found",
        ) from exc
    except ConversationMembershipError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this conversation",
        ) from exc
    except ConversationValidationError as exc:
        # A withdrawn message, which is a conflict with the message's state
        # rather than a malformed request — same code §3.6 uses for editing one.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    # After the edit is safely stored, and detached from it: this is a
    # by-product, and nothing about mining a glossary may put a reader's own
    # correction at risk. `schedule_correction_record` checks consent itself, so
    # no future call site can forget to.
    profile = await resolve_conversation_profile(db, translation.message_id)
    schedule_correction_record(
        machine_text=translation.translated_text,
        human_text=edit.edited_text,
        original_text=message.original_text,
        source_language=profile.source_language,
        target_language=translation.target_language,
        domain=profile.domain,
        audience=profile.audience,
        user_id=current_user.id,
        translation_id=translation.id,
        consent_to_share=payload.consent_to_share,
    )

    return TranslationEditResponse(
        edit_id=edit.id,
        translation_id=translation.id,
        message_id=translation.message_id,
        target_language=translation.target_language,
        honorific_profile=translation.honorific_profile,
        edited_text=edit.edited_text,
        edited_at=edit.created_at,
    )


async def _translations_by_message(
    db: AsyncSession,
    message_ids: list[str],
    reader_id: str,
    conversation_id: str,
) -> dict[str, list[TranslationSummary]]:
    """Load every translation for a page of messages in one query.

    History is how a client recovers translations it missed while disconnected,
    so this is what keeps a socket dropping mid-translation from losing data.
    Each summary also carries `reader_id`'s own feedback, which is what lets a
    rating button still look rated after a reload.

    Since `honorific_profile` joined the unique key, one message can hold
    several translations into the same language, differing only in how they
    address the reader. This collapses each language back to one row, so the
    array stays the shape docs/api/contract.md §3.2 describes and the client is
    never handed two candidates with nothing to choose between them.

    Which row wins is decided by `select_for_reader`, using the standing of
    somebody who actually reads that language: the caller's own for the caller's
    language, and otherwise the first member who reads it. That second half is
    not a nicety — in a `direct` conversation the sender is shown the
    translation the *other* person reads, so the rating and edit controls can
    sit under their own message (§4.4 rule 3, ADR-19), and picking that row with
    the sender's standing would show them a register meant for nobody.

    Args:
        db: Open session.
        message_ids: Messages whose translations are being rendered.
        reader_id: Account whose feedback is attached; never another member's.
        conversation_id: Conversation the messages belong to, needed because a
            standing is only defined inside one.

    Returns:
        Translations grouped by message id, one per target language, in no
        guaranteed order.
    """
    if not message_ids:
        return {}

    # Ordered newest first so the latest translation candidate (from retry/Translate Again)
    # is picked by select_for_reader.
    candidates = list(
        (
            await db.scalars(
                select(TranslationResult)
                .where(TranslationResult.message_id.in_(message_ids))
                .order_by(
                    TranslationResult.version.desc(),
                    TranslationResult.created_at.desc(),
                    TranslationResult.id.desc(),
                )
            )
        ).all()
    )

    profiles = await resolve_profiles(db, conversation_id)
    members = await ChatService(db).get_conversation_members(conversation_id=conversation_id)
    reader_language = next(
        (member.preferred_language for member in members if member.id == reader_id),
        "",
    )
    reader_settings = await get_or_create_user_settings(db, reader_id)
    # Sorted so that a language read by several members always resolves through
    # the same one of them, however the database happened to return the rows.
    readers_by_language: dict[str, str] = {}
    for member in sorted(members, key=lambda member: member.id):
        readers_by_language.setdefault(member.preferred_language, member.id)

    def standing_for(language: str) -> str:
        """Whose standing decides the wording a given language is rendered in."""
        if language == reader_language:
            return profile_for(profiles, reader_id)
        return profile_for(profiles, readers_by_language.get(language, ""))

    rows = []
    by_message: dict[str, list[TranslationResult]] = {}
    for row in candidates:
        by_message.setdefault(row.message_id, []).append(row)
    for message_rows in by_message.values():
        for language in dict.fromkeys(row.target_language for row in message_rows):
            chosen = select_for_reader(
                message_rows,
                target_language=language,
                honorific_profile=standing_for(language),
                translation_tone=reader_settings.translation_tone,
            )
            if chosen is not None:
                rows.append(chosen)

    my_feedback = {
        feedback.translation_id: feedback
        for feedback in (
            await db.scalars(
                select(Feedback).where(
                    Feedback.user_id == reader_id,
                    Feedback.translation_id.in_([row.id for row in rows]),
                )
            )
        ).all()
    }

    # The caller's own edits only. Loaded here rather than joined above so the
    # privacy rule is visible in one place: this dict is keyed by translation
    # and filtered by reader, and nothing else ever reaches these rows (§3.10).
    my_edits = await ChatService(db).latest_translation_edits(
        translation_ids=[row.id for row in rows],
        editor_id=reader_id,
    )

    grouped: dict[str, list[TranslationSummary]] = {}
    for row in rows:
        feedback = my_feedback.get(row.id)
        edit = my_edits.get(row.id)
        grouped.setdefault(row.message_id, []).append(
            # Built field by field rather than validated from the ORM object:
            # the row's primary key is `id`, and the client needs it under the
            # name `translation_id` so F-05 can attach feedback to it
            # (docs/api/contract.md section 4.4). `from_attributes` cannot rename.
            TranslationSummary(
                translation_id=row.id,
                target_language=row.target_language,
                honorific_profile=row.honorific_profile,
                translation_tone=row.translation_tone,
                translated_text=row.translated_text,
                model=row.model,
                latency_ms=row.latency_ms,
                is_fallback=row.is_fallback,
                my_rating=feedback.rating if feedback else None,
                my_correction=feedback.correction if feedback else None,
                my_edit=TranslationEditSummary(
                    edit_id=edit.id,
                    edited_text=edit.edited_text,
                    edited_at=edit.created_at,
                )
                if edit
                else None,
            )
        )
    return grouped
