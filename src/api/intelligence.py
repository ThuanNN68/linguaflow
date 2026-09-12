"""Intelligence HTTP endpoints."""

import logging
import uuid
from collections.abc import Sequence

from fastapi import (
    APIRouter,
    Body,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.conversation_intelligence.errors import (
    IntelligenceError,
    IntelligenceErrorCode,
)
from src.api.websocket import get_connection_manager
from src.core.deps import get_current_user
from src.core.rate_limit import llm_limit
from src.database import get_db
from src.database.models import (
    ActionProposal,
    Conversation,
    Message,
    User,
)
from src.schemas.chat import MessageReceivedEvent, RealtimeMessage
from src.schemas.intelligence import (
    ActionProposalResponse,
    ClarifyProposalRequest,
    ConfirmProposalRequest,
    ConversationSummaryRequest,
    ConversationSummaryResponse,
)
from src.services.intelligence.action_proposals import (
    ActionProposalNotFoundError,
    ActionProposalOwnershipError,
    ActionProposalService,
    ActionProposalStatusError,
)
from src.services.intelligence.conversation_intelligence import ConversationIntelligenceService
from src.services.messaging.chat import (
    ChatService,
    ConversationMembershipError,
    ConversationNotFoundError,
    ConversationValidationError,
    MessageNotFoundError,
)
from src.services.messaging.connection_manager import ConnectionManager

logger = logging.getLogger(__name__)

router = APIRouter()


def _empty_appointment_scan_message(*, language: str, days: int, hours: int | None) -> str:
    """Return a concise, localized assistant result for an empty scan."""
    if hours is not None:
        window_en = f"the last {hours} hour{'s' if hours != 1 else ''}"
        window_vi = f"{hours} giờ gần đây"
    else:
        window_en = f"the last {days} day{'s' if days != 1 else ''}"
        window_vi = f"{days} ngày gần đây"
    if language == "vi":
        return (
            f"Mình đã kiểm tra tối đa 100 tin nhắn trong {window_vi} nhưng không tìm thấy "
            "lịch hẹn hoặc cuộc họp nào cần tạo."
        )
    return (
        f"I checked up to 100 messages from {window_en}, but did not find any "
        "appointments or meetings to create."
    )


@router.post(
    "/conversations/{conversation_id}/summary",
    response_model=ConversationSummaryResponse,
)
@llm_limit
async def summarize_conversation(
    conversation_id: str,
    request: Request,
    response: Response,
    payload: ConversationSummaryRequest = Body(default_factory=ConversationSummaryRequest),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationSummaryResponse:
    """Generate an on-demand grounded conversation summary for authorized members (B-03)."""
    service = ConversationIntelligenceService()
    try:
        return await service.summarize_conversation(
            conversation_id=conversation_id,
            user_id=current_user.id,
            db=db,
            message_limit=payload.message_limit,
            target_language=payload.target_language,
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
    except IntelligenceError as exc:
        if exc.code == IntelligenceErrorCode.TIMEOUT:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="Conversation summarization timed out",
            ) from exc
        if exc.code == IntelligenceErrorCode.PROVIDER_UNAVAILABLE:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AI provider is currently unavailable",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=exc.message,
        ) from exc


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/extract-actions",
    response_model=list[ActionProposalResponse],
)
@llm_limit
async def extract_actions_from_message_endpoint(
    conversation_id: str,
    message_id: str,
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ActionProposalResponse]:
    """Extract candidate actions/appointments from a specific message (B-04)."""
    service = ConversationIntelligenceService()
    try:
        return await service.extract_actions_from_message(
            conversation_id=conversation_id,
            message_id=message_id,
            user_id=current_user.id,
            db=db,
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
    except MessageNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message was not found",
        ) from exc
    except IntelligenceError as exc:
        if exc.code == IntelligenceErrorCode.TIMEOUT:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="Action extraction timed out",
            ) from exc
        if exc.code == IntelligenceErrorCode.PROVIDER_UNAVAILABLE:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AI provider is currently unavailable",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=exc.message,
        ) from exc


@router.post(
    "/conversations/{conversation_id}/extract-actions/recent",
    response_model=list[ActionProposalResponse],
)
@llm_limit
async def extract_actions_from_recent_messages_endpoint(
    conversation_id: str,
    request: Request,
    response: Response,
    days: int = Query(default=7, ge=1, le=30),
    hours: int | None = Query(default=None, ge=1, le=720),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    manager: ConnectionManager = Depends(get_connection_manager),
) -> list[ActionProposalResponse]:
    """Scan a conversation's human messages from the selected time window.

    ``days`` remains the backwards-compatible default. ``hours`` supports
    short scans; either range is limited to the equivalent of 30 days.
    """
    service = ConversationIntelligenceService()
    try:
        proposals = await service.extract_actions_from_recent_messages(
            conversation_id=conversation_id,
            user_id=current_user.id,
            days=days,
            hours=hours,
            db=db,
        )
        if not proposals:
            notice_result = await ChatService(db).create_assistant_notice(
                conversation_id=conversation_id,
                user_id=current_user.id,
                text=_empty_appointment_scan_message(
                    language=current_user.interface_language,
                    days=days,
                    hours=hours,
                ),
                idempotency_key=f"assistant:appointment-scan-empty:{uuid.uuid4().hex}",
                source_language=current_user.interface_language,
            )
            notice = RealtimeMessage.model_validate(notice_result.message)
            notice.assistant_generated = True
            await manager.send_to_user(
                current_user.id,
                MessageReceivedEvent(message=notice).model_dump(mode="json"),
            )
        return proposals
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation was not found") from exc
    except ConversationMembershipError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="You are not a member of this conversation"
        ) from exc
    except IntelligenceError as exc:
        if exc.code == IntelligenceErrorCode.TIMEOUT:
            raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Action scan timed out") from exc
        if exc.code == IntelligenceErrorCode.PROVIDER_UNAVAILABLE:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="AI provider is currently unavailable"
            ) from exc
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message) from exc


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/detect-commitments",
    response_model=list[ActionProposalResponse],
)
@llm_limit
async def detect_message_self_commitments(
    conversation_id: str,
    message_id: str,
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ActionProposalResponse]:
    """Detect proactive first-person self-commitments from a message (B-10)."""
    service = ConversationIntelligenceService()
    try:
        return await service.detect_self_commitments_from_message(
            conversation_id=conversation_id,
            message_id=message_id,
            user_id=current_user.id,
            db=db,
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
    except MessageNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message was not found",
        ) from exc
    except IntelligenceError as exc:
        if exc.code == IntelligenceErrorCode.TIMEOUT:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="Commitment detection timed out",
            ) from exc
        if exc.code == IntelligenceErrorCode.PROVIDER_UNAVAILABLE:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AI provider is currently unavailable",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=exc.message,
        ) from exc


async def _proposal_responses_with_source_context(
    db: AsyncSession, proposals: Sequence[ActionProposal]
) -> list[ActionProposalResponse]:
    """Add safe, human-readable source context to task-inbox responses."""
    if not proposals:
        return []

    from sqlalchemy.orm import aliased

    time_source = aliased(Message)
    details_source = aliased(Message)
    rows = await db.execute(
        select(
            ActionProposal.id,
            User.display_name,
            User.username,
            User.email,
            Conversation.title,
            Conversation.type,
            time_source.created_at.label("time_source_at"),
            details_source.created_at.label("details_source_at"),
        )
        .join(Message, Message.id == ActionProposal.source_message_id)
        .join(User, User.id == Message.sender_id)
        .join(Conversation, Conversation.id == ActionProposal.conversation_id)
        .outerjoin(time_source, time_source.id == ActionProposal.time_source_message_id)
        .outerjoin(details_source, details_source.id == ActionProposal.details_source_message_id)
        .where(ActionProposal.id.in_([proposal.id for proposal in proposals]))
    )
    context = {
        row.id: {
            "source_sender_name": row.display_name or row.username or row.email,
            "source_conversation_name": row.title,
            "source_conversation_type": row.type,
            "time_source_at": row.time_source_at,
            "details_source_at": row.details_source_at,
        }
        for row in rows
    }
    return [
        ActionProposalResponse.model_validate(proposal).model_copy(update=context.get(proposal.id, {}))
        for proposal in proposals
    ]


@router.get(
    "/me/action-proposals",
    response_model=list[ActionProposalResponse],
)
async def list_conversation_proposals(
    conversation_id: str | None = Query(default=None),
    status_filter: str | None = Query(
        default=None,
        alias="status",
        description="Filter by status: needs_clarification, pending_confirmation, confirmed, rejected, stale",
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ActionProposalResponse]:
    """List only the authenticated owner's proposals (B-05)."""
    service = ActionProposalService(db)
    try:
        proposals = await service.list_for_owner(current_user.id, status_filter, conversation_id)
        return await _proposal_responses_with_source_context(db, proposals)
    except ConversationMembershipError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this conversation",
        ) from exc
    except ConversationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation was not found",
        ) from exc


@router.post(
    "/action-proposals/{proposal_id}/dismiss",
    response_model=ActionProposalResponse,
)
async def dismiss_action_proposal(
    proposal_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ActionProposalResponse:
    """Clear one proposal out of the caller's task inbox.

    Hides the row. It does not cancel anything: a proposal that was approved has
    already put an event on the calendar, and that event and its reminders are
    untouched.
    """
    try:
        dismissed = await ActionProposalService(db).dismiss(proposal_id=proposal_id, user_id=current_user.id)
    except ActionProposalNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action proposal was not found") from exc
    except ActionProposalOwnershipError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action proposal belongs to someone else",
        ) from exc
    return ActionProposalResponse.model_validate(dismissed)


@router.post("/me/action-proposals/dismiss-decided")
async def dismiss_decided_action_proposals(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """Clear every already-decided proposal at once ("xoá tất cả").

    Deliberately leaves anything still awaiting a decision: sweeping those away
    would drop a question the assistant is waiting on, and nobody would learn it
    had been asked. Calendars are untouched, as with a single dismissal.
    """
    return {"dismissed": await ActionProposalService(db).dismiss_all_decided(user_id=current_user.id)}


@router.post(
    "/action-proposals/{proposal_id}/confirm",
    response_model=ActionProposalResponse,
)
async def confirm_action_proposal(
    proposal_id: str,
    payload: ConfirmProposalRequest = Body(default_factory=ConfirmProposalRequest),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ActionProposalResponse:
    """Explicitly confirm an action proposal by its assigned owner (B-05)."""
    service = ActionProposalService(db)
    try:
        # The lead time is not a correction to the proposal — it shapes the
        # calendar entry that confirming creates — so it travels as its own
        # argument. Left in `corrections` it would be filtered out silently by
        # the allowlist there and the person's choice would vanish.
        corrections = payload.model_dump(exclude_none=True)
        corrections.pop("reminder_minutes_before", None)
        confirmed = await service.confirm_proposal(
            proposal_id=proposal_id,
            user_id=current_user.id,
            corrections=corrections,
            reminder_minutes_before=payload.reminder_minutes_before,
        )
        return ActionProposalResponse.model_validate(confirmed)
    except ActionProposalNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Action proposal was not found",
        ) from exc
    except ActionProposalOwnershipError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the assigned owner can confirm this proposal",
        ) from exc
    except ActionProposalStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.post(
    "/action-proposals/{proposal_id}/reject",
    response_model=ActionProposalResponse,
)
async def reject_action_proposal(
    proposal_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ActionProposalResponse:
    """Explicitly reject an action proposal by its assigned owner (B-05)."""
    service = ActionProposalService(db)
    try:
        rejected = await service.reject_proposal(proposal_id=proposal_id, user_id=current_user.id)
        return ActionProposalResponse.model_validate(rejected)
    except ActionProposalNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Action proposal was not found",
        ) from exc
    except ActionProposalOwnershipError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only the assigned owner can reject this proposal"
        ) from exc
    except ActionProposalStatusError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.delete("/action-proposals/{proposal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_terminal_action_proposal(
    proposal_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Delete an inbox item only after it has been rejected or become stale."""
    service = ActionProposalService(db)
    try:
        await service.delete_terminal_proposal(proposal_id, current_user.id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except ActionProposalNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action proposal was not found") from exc
    except ActionProposalOwnershipError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only the assigned owner can delete this proposal"
        ) from exc
    except ActionProposalStatusError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/action-proposals/{proposal_id}/clarify", response_model=ActionProposalResponse)
async def clarify_action_proposal(
    proposal_id: str,
    payload: ClarifyProposalRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ActionProposalResponse:
    service = ActionProposalService(db)
    try:
        proposal = await service.clarify(proposal_id, current_user.id, payload.answer, payload.timezone)
        return ActionProposalResponse.model_validate(proposal)
    except ActionProposalNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action proposal was not found") from exc
    except ActionProposalOwnershipError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only the assigned owner can clarify this proposal"
        ) from exc
    except ActionProposalStatusError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
