"""Administrative view of the glossary, its review queue, and reader feedback.

A separate router rather than another block in `routes.py`, for the reason
`metrics.py` gives: several branches edit that file at once, and a new module
cannot conflict with them.

Every endpoint here is gated on `get_admin_user`. The gate is the whole
authorization model — `users.role` compared to the string `"admin"` — so it has
to be on each one rather than assumed from the path prefix.

What an administrator can see is bounded by design, not by omission. Glossary
records contain anonymised snippets only. The feedback review queue is an
explicit quality-control exception: it carries an original, machine translation
and reader correction, but never the sender, reader, conversation or message
identifier that could join it back to a chat.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.customization import Customization
from src.agents.graph import build_translation_graph
from src.agents.observability import build_runnable_config
from src.config import get_settings
from src.core.deps import get_admin_user
from src.core.security import create_refresh_token, hash_refresh_token
from src.database import get_db
from src.database.models import (
    GLOSSARY_PROPOSAL_STATUSES,
    AuditLog,
    CorrectionLog,
    Feedback,
    GlossaryEntry,
    GlossaryProposal,
    GlossaryProposalCitation,
    Message,
    PasswordResetToken,
    RefreshSession,
    TranslationEdit,
    TranslationResult,
    User,
)
from src.schemas.admin_translation import (
    AdminTranslationRequest,
    AdminTranslationResponse,
)
from src.schemas.feedback import (
    FeedbackOverviewResponse,
    FeedbackReviewEntry,
    FeedbackVoteSummary,
    SharedCorrection,
)
from src.schemas.glossary import (
    GlossaryApprovalRequest,
    GlossaryCitationSummary,
    GlossaryEntryRequest,
    GlossaryEntryResponse,
    GlossaryEntryUpdateRequest,
    GlossaryProposalCreateRequest,
    GlossaryProposalResponse,
    GlossaryRejectionRequest,
    GlossarySimilarEntry,
)
from src.services.delivery.webhooks import schedule_webhook_event
from src.services.identity.audit import record_audit_event
from src.services.identity.email import EmailDeliveryError, send_password_reset_email
from src.services.language.glossary import lookup_terms, normalize_term
from src.services.shared.embeddings import embed_with_model

logger = logging.getLogger(__name__)

router = APIRouter()

# One page of a review queue. A reviewer works through a handful at a time; a
# larger page just means more of them go unread.
MAX_PAGE = 100


class AdminUserResponse(BaseModel):
    id: str
    email: str
    username: str | None
    display_name: str | None
    role: str
    suspended_at: datetime | None
    suspension_reason: str | None
    created_at: datetime


class AdminSuspendUserRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@router.get("/admin/users", response_model=list[AdminUserResponse])
async def list_users(
    query: str = Query(default="", max_length=100),
    limit: int = Query(default=50, ge=1, le=100),
    _admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> list[AdminUserResponse]:
    statement = select(User).where(User.deleted_at.is_(None)).order_by(User.created_at.desc()).limit(limit)
    if query.strip():
        term = f"%{query.strip()}%"
        statement = statement.where(
            User.email.ilike(term) | User.username.ilike(term) | User.display_name.ilike(term)
        )
    users = (await db.scalars(statement)).all()
    return [AdminUserResponse.model_validate(user, from_attributes=True) for user in users]


@router.post("/admin/users/{user_id}/suspend", response_model=AdminUserResponse)
async def suspend_user(
    user_id: str,
    request: AdminSuspendUserRequest,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> AdminUserResponse:
    if user_id == admin.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Administrators cannot suspend themselves")
    user = await db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User was not found")
    user.suspended_at = datetime.now(UTC)
    user.suspension_reason = request.reason.strip()
    await db.execute(
        update(RefreshSession).where(RefreshSession.user_id == user.id, RefreshSession.revoked_at.is_(None)).values(revoked_at=datetime.now(UTC))
    )
    await record_audit_event(db, actor_id=admin.id, action="user.suspended", resource_type="user", resource_id=user.id)
    await db.commit()
    schedule_webhook_event("user.suspended", {"user_id": user.id, "reason": user.suspension_reason})
    return AdminUserResponse.model_validate(user, from_attributes=True)


@router.post("/admin/users/{user_id}/unsuspend", response_model=AdminUserResponse)
async def unsuspend_user(
    user_id: str,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> AdminUserResponse:
    user = await db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User was not found")
    user.suspended_at = None
    user.suspension_reason = None
    await record_audit_event(db, actor_id=admin.id, action="user.unsuspended", resource_type="user", resource_id=user.id)
    await db.commit()
    schedule_webhook_event("user.unsuspended", {"user_id": user.id})
    return AdminUserResponse.model_validate(user, from_attributes=True)


@router.post("/admin/users/{user_id}/password-reset", status_code=status.HTTP_202_ACCEPTED)
async def send_admin_password_reset(
    user_id: str,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    user = await db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User was not found")
    settings = get_settings()
    now = datetime.now(UTC)
    raw_token = create_refresh_token()
    await db.execute(
        update(PasswordResetToken).where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None)).values(used_at=now)
    )
    db.add(PasswordResetToken(user_id=user.id, token_hash=hash_refresh_token(raw_token), expires_at=now + timedelta(minutes=settings.password_reset_expire_minutes)))
    await record_audit_event(db, actor_id=admin.id, action="user.password_reset_requested", resource_type="user", resource_id=user.id)
    await db.commit()
    reset_url = f"{settings.frontend_url}/reset-password?{urlencode({'token': raw_token})}"
    try:
        await send_password_reset_email(to_email=user.email, reset_url=reset_url, expires_in_minutes=settings.password_reset_expire_minutes, language=user.interface_language)
    except EmailDeliveryError:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Unable to send password-reset email") from None
    return {"message": "Password-reset email sent"}


@router.get("/admin/audit-logs")
async def list_audit_logs(
    limit: int = Query(default=50, ge=1, le=100),
    _admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, object]]:
    """Return recent non-content security events to an administrator only."""
    rows = (await db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit))).all()
    return [
        {
            "id": row.id,
            "actor_id": row.actor_id,
            "action": row.action,
            "resource_type": row.resource_type,
            "resource_id": row.resource_id,
            "metadata": json.loads(row.metadata_json),
            "created_at": row.created_at,
        }
        for row in rows
    ]


class _AdminTranslationCustomizationProvider:
    """Apply an explicitly selected admin scope without inventing a conversation."""

    def __init__(self, db: AsyncSession, *, domain: str, audience: str) -> None:
        self._db = db
        self._domain = domain
        self._audience = audience

    async def get_customization(
        self,
        _conversation_id: str,
        *,
        original_text: str,
        source_language: str,
        target_language: str,
    ) -> Customization:
        terms = await lookup_terms(
            self._db,
            text=original_text,
            source_language=source_language,
            target_language=target_language,
            domain=self._domain,
            audience=self._audience,
        )
        return Customization(
            domain=self._domain,
            audience=self._audience,
            glossary_terms=terms,
        )


@router.post(
    "/admin/translate",
    response_model=AdminTranslationResponse,
)
async def run_admin_translation(
    payload: AdminTranslationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_admin_user),
) -> AdminTranslationResponse:
    """Run the real production translation graph synchronously for an admin.

    This endpoint deliberately does not create a chat message or translation
    row: the admin test bench must not leak synthetic conversations into user
    history. Provider telemetry is returned directly to the test UI instead.
    """
    request_id = str(uuid.uuid4())
    graph = build_translation_graph(
        customization_provider=_AdminTranslationCustomizationProvider(
            db,
            domain=payload.domain,
            audience=payload.audience,
        )
    )
    state = {
        "conversation_id": f"admin-test:{current_user.id}",
        "message_id": request_id,
        "sender_id": current_user.id,
        "original_text": payload.original_text,
        "source_language": payload.source_language,
        "target_language": payload.target_language,
        "honorific_profile": "peer",
        "sender_honorific_profile": "peer",
        "translation_tone": payload.translation_tone,
    }
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(
            graph.ainvoke(
                state,
                config=build_runnable_config(
                    conversation_id=state["conversation_id"],
                    message_id=request_id,
                    target_language=payload.target_language,
                    attempt_id=request_id,
                ),
            ),
            timeout=get_settings().translation_timeout_seconds,
        )
    except TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Translation agent timed out",
        ) from exc
    except Exception as exc:
        logger.exception("Admin translation agent failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Translation agent could not complete the request",
        ) from exc

    telemetry = result.get("telemetry") or {}
    matched_terms = [
        term.source_term for term in (result.get("glossary_terms") or [])
    ]
    return AdminTranslationResponse(
        original_text=payload.original_text,
        translated_text=str(result.get("translated_text") or payload.original_text),
        source_language=str(result.get("source_language") or payload.source_language),
        target_language=payload.target_language,
        model=str(result.get("model") or telemetry.get("model_served") or "passthrough"),
        latency_ms=max(
            int(result.get("latency_ms") or 0),
            int((time.perf_counter() - started) * 1000),
        ),
        input_tokens=int(telemetry.get("input_tokens") or 0),
        output_tokens=int(telemetry.get("output_tokens") or 0),
        is_fallback=bool(result.get("is_fallback")),
        fallback_reason=str(telemetry.get("fallback_reason") or ""),
        matched_glossary_terms=matched_terms,
    )


async def _citations(db: AsyncSession, proposal_id: str) -> list[GlossaryCitationSummary]:
    """The anonymised fragments attached to one proposal."""
    rows = (
        await db.scalars(
            select(GlossaryProposalCitation)
            .where(GlossaryProposalCitation.proposal_id == proposal_id)
            .order_by(GlossaryProposalCitation.observed_at)
        )
    ).all()
    return [GlossaryCitationSummary.model_validate(row) for row in rows]


async def _load_proposal(db: AsyncSession, proposal_id: str) -> GlossaryProposal:
    """Fetch a proposal or answer 404."""
    proposal = await db.scalar(
        select(GlossaryProposal).where(GlossaryProposal.id == proposal_id)
    )
    if proposal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Proposal was not found",
        )
    return proposal


def _require_pending(proposal: GlossaryProposal) -> None:
    """Refuse to decide the same proposal twice.

    409 rather than 200: two administrators working the queue at once would
    otherwise both believe they were the one who approved it, and the second
    decision would silently overwrite the first — including its reason.
    """
    if proposal.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This proposal has already been {proposal.status}",
        )


async def _similar_entries(
    db: AsyncSession, proposal: GlossaryProposal
) -> tuple[list[GlossarySimilarEntry], bool]:
    """What the glossary already says about this proposal's source term.

    Matched on the normalised source term and the language pair, not on
    embeddings. That is a deliberate choice rather than a shortcut: measured on
    this project's own terms, the available embedding models score a true
    variant and an unrelated word within 0.002 of each other, so a similarity
    threshold here would fill a reviewer's screen with confident noise (ADR-26).
    Normalised equality is narrower and it is *right*, which is what a screen
    that exists to prevent a wrong approval needs.

    Returns:
        The entries found, and whether any of them is active with a different
        target — the case where the machine is already translating this term to
        somebody's satisfaction and the proposal is asking to overturn it.
    """
    rows = (
        await db.scalars(
            select(GlossaryEntry).where(
                GlossaryEntry.source_term_normalized == proposal.source_term_normalized,
                GlossaryEntry.source_language == proposal.source_language,
                GlossaryEntry.target_language == proposal.target_language,
            )
        )
    ).all()

    conflicts = any(
        entry.status == "active" and entry.target_term != proposal.target_term
        for entry in rows
    )
    return [GlossarySimilarEntry.model_validate(entry) for entry in rows], conflicts


async def _proposal_response(
    db: AsyncSession, proposal: GlossaryProposal
) -> GlossaryProposalResponse:
    """Render one proposal with the evidence a reviewer decides on.

    Both endpoints that return a proposal go through here. They used to build
    the response separately from `model_fields`, which meant adding a field to
    the DTO silently broke whichever copy was not edited — it did, the first
    time this grew.
    """
    similar, conflicts = await _similar_entries(db, proposal)
    carried = {
        field
        for field in GlossaryProposalResponse.model_fields
        if field not in ("citations", "similar_entries", "conflicts_with_active")
    }
    return GlossaryProposalResponse(
        **{field: getattr(proposal, field) for field in carried},
        citations=await _citations(db, proposal.id),
        similar_entries=similar,
        conflicts_with_active=conflicts,
    )


@router.get(
    "/admin/glossary/proposals",
    response_model=list[GlossaryProposalResponse],
)
async def list_glossary_proposals(
    status_filter: str = Query(
        default="pending",
        alias="status",
        description="pending, approved or rejected. Defaults to pending.",
    ),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_admin_user),
) -> list[GlossaryProposalResponse]:
    """List proposals awaiting a decision, newest first.

    Newest first because a term the team has just started arguing about is more
    useful to settle than one from six weeks ago.
    """
    if status_filter not in GLOSSARY_PROPOSAL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown status: {status_filter}",
        )

    proposals = (
        await db.scalars(
            select(GlossaryProposal)
            .where(GlossaryProposal.status == status_filter)
            .order_by(GlossaryProposal.created_at.desc())
            .limit(limit)
        )
    ).all()

    return [await _proposal_response(db, proposal) for proposal in proposals]


@router.post(
    "/admin/glossary/proposals",
    response_model=GlossaryProposalResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_glossary_proposal(
    payload: GlossaryProposalCreateRequest,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_admin_user),
) -> GlossaryProposalResponse:
    """Persist an administrator's direct proposal for the review queue.

    The review queue must never be a frontend-only list: the returned proposal
    is a committed row, with its embedding generated before it can be shown.
    """
    vector, vector_model = await embed_with_model(payload.source_term)
    proposal = GlossaryProposal(
        source_term=payload.source_term,
        source_term_normalized=normalize_term(payload.source_term),
        target_term=payload.target_term,
        source_language=payload.source_language,
        target_language=payload.target_language,
        domain=payload.domain,
        audience=payload.audience,
        keep_verbatim=payload.keep_verbatim,
        status="pending",
        occurrence_count=1,
        distinct_user_count=1,
        rationale=payload.rationale,
        embedding=vector,
        embedding_model=vector_model,
    )
    db.add(proposal)
    await db.commit()
    await db.refresh(proposal)
    return await _proposal_response(db, proposal)


@router.post(
    "/admin/glossary/proposals/{proposal_id}/approve",
    response_model=GlossaryEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def approve_glossary_proposal(
    proposal_id: str,
    payload: GlossaryApprovalRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_admin_user),
) -> GlossaryEntryResponse:
    """Turn a proposal into an entry the translation engine is bound by.

    The proposal is marked `approved` and kept, not deleted: it is the record of
    where the entry came from, and it is also what stops the miner proposing the
    same term again next week.

    The entry is embedded here rather than in the background. Without a vector
    it is still found by exact match, but the variants people actually type —
    "stg" for "staging environment" — would go on being missed until somebody
    noticed why, and an administrator has just been told the term is now in use.
    """
    proposal = await _load_proposal(db, proposal_id)
    _require_pending(proposal)

    source_term = payload.source_term or proposal.source_term
    target_term = payload.target_term or proposal.target_term
    domain = proposal.domain if payload.domain is None else payload.domain
    audience = proposal.audience if payload.audience is None else payload.audience
    keep_verbatim = (
        proposal.keep_verbatim if payload.keep_verbatim is None else payload.keep_verbatim
    )

    vector, vector_model = await embed_with_model(source_term)
    entry = GlossaryEntry(
        source_term=source_term,
        source_term_normalized=normalize_term(source_term),
        target_term=target_term,
        source_language=proposal.source_language,
        target_language=proposal.target_language,
        domain=domain,
        audience=audience,
        keep_verbatim=keep_verbatim,
        status="active",
        approved_by=current_user.id,
        embedding=vector,
        embedding_model=vector_model,
    )
    db.add(entry)

    proposal.status = "approved"
    proposal.reviewed_by = current_user.id
    proposal.reviewed_at = _now()

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        # The unique constraint on (term, languages, domain, audience). Two
        # reviewers approving near-identical proposals land here, and so does one
        # reviewer approving a term already added by hand.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A glossary entry for this term and scope already exists",
        ) from exc

    await db.refresh(entry)
    return GlossaryEntryResponse.model_validate(entry)


@router.post(
    "/admin/glossary/proposals/{proposal_id}/reject",
    response_model=GlossaryProposalResponse,
)
async def reject_glossary_proposal(
    proposal_id: str,
    payload: GlossaryRejectionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_admin_user),
) -> GlossaryProposalResponse:
    """Refuse a proposal, and record why.

    The row stays forever, with its embedding. That is what lets the miner
    recognise the same term next week in different words and not ask again —
    which is the difference between a queue somebody works and one they stop
    opening (ADR-28).
    """
    proposal = await _load_proposal(db, proposal_id)
    _require_pending(proposal)

    proposal.status = "rejected"
    proposal.reject_reason = payload.reason
    proposal.reviewed_by = current_user.id
    proposal.reviewed_at = _now()
    await db.commit()
    await db.refresh(proposal)

    return await _proposal_response(db, proposal)


@router.get("/admin/glossary", response_model=list[GlossaryEntryResponse])
async def list_glossary_entries(
    include_retired: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=MAX_PAGE * 5),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_admin_user),
) -> list[GlossaryEntryResponse]:
    """List the entries currently in force."""
    query = select(GlossaryEntry).order_by(
        GlossaryEntry.source_language,
        GlossaryEntry.target_language,
        GlossaryEntry.source_term_normalized,
    )
    if not include_retired:
        query = query.where(GlossaryEntry.status == "active")

    entries = (await db.scalars(query.limit(limit))).all()
    return [GlossaryEntryResponse.model_validate(entry) for entry in entries]


@router.post(
    "/admin/glossary",
    response_model=GlossaryEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_glossary_entry(
    payload: GlossaryEntryRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_admin_user),
) -> GlossaryEntryResponse:
    """Add a term by hand, without waiting for anybody to correct it first.

    The mining pipeline needs several people to disagree with the machine before
    it proposes anything, which is right for discovering a house style and
    useless for a term the team already knows it wants.
    """
    vector, vector_model = await embed_with_model(payload.source_term)
    entry = GlossaryEntry(
        source_term=payload.source_term,
        source_term_normalized=normalize_term(payload.source_term),
        target_term=payload.target_term,
        source_language=payload.source_language,
        target_language=payload.target_language,
        domain=payload.domain,
        audience=payload.audience,
        keep_verbatim=payload.keep_verbatim,
        status="active",
        approved_by=current_user.id,
        embedding=vector,
        embedding_model=vector_model,
    )
    db.add(entry)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A glossary entry for this term and scope already exists",
        ) from exc

    await db.refresh(entry)
    return GlossaryEntryResponse.model_validate(entry)


async def _load_entry(db: AsyncSession, entry_id: str) -> GlossaryEntry:
    """Fetch an entry or answer 404."""
    entry = await db.scalar(select(GlossaryEntry).where(GlossaryEntry.id == entry_id))
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Glossary entry was not found",
        )
    return entry


@router.patch(
    "/admin/glossary/{entry_id}",
    response_model=GlossaryEntryResponse,
)
async def update_glossary_entry(
    entry_id: str,
    payload: GlossaryEntryUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_admin_user),
) -> GlossaryEntryResponse:
    """Correct an entry that is already in force.

    Only the fields sent are changed; the language pair is not one of them,
    because a term whose pair is wrong is a different entry rather than a
    mistyped one, and the embedding and the unique constraint are both scoped
    to the pair.

    The source term is re-embedded when it changes, for the same reason
    approval embeds in the first place: without a fresh vector the semantic
    stage would go on matching the old wording, silently, and the only symptom
    would be a term that stops being found.
    """
    entry = await _load_entry(db, entry_id)

    if payload.source_term is not None and payload.source_term != entry.source_term:
        entry.source_term = payload.source_term
        entry.source_term_normalized = normalize_term(payload.source_term)
        entry.embedding, entry.embedding_model = await embed_with_model(
            payload.source_term
        )
    if payload.target_term is not None:
        entry.target_term = payload.target_term
    if payload.domain is not None:
        entry.domain = payload.domain
    if payload.audience is not None:
        entry.audience = payload.audience
    if payload.keep_verbatim is not None:
        entry.keep_verbatim = payload.keep_verbatim

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        # An edit can collide with an existing row the same way an insert can:
        # the scope it is being moved to may already be taken.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A glossary entry for this term and scope already exists",
        ) from exc

    await db.refresh(entry)
    return GlossaryEntryResponse.model_validate(entry)


@router.post(
    "/admin/glossary/{entry_id}/restore",
    response_model=GlossaryEntryResponse,
)
async def restore_glossary_entry(
    entry_id: str,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_admin_user),
) -> GlossaryEntryResponse:
    """Put a retired entry back into use.

    The counterpart of retirement, and the reason retirement can be undone at
    all: nothing was deleted, so restoring is a status change rather than a
    re-creation — the entry keeps its id, its embedding and the date it was
    first approved.
    """
    entry = await _load_entry(db, entry_id)

    entry.status = "active"
    await db.commit()
    await db.refresh(entry)
    return GlossaryEntryResponse.model_validate(entry)


@router.delete(
    "/admin/glossary/{entry_id}",
    response_model=GlossaryEntryResponse,
)
async def retire_glossary_entry(
    entry_id: str,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_admin_user),
) -> GlossaryEntryResponse:
    """Take an entry out of use without deleting it.

    DELETE by verb, retirement by effect, and deliberately so: a translation
    delivered last month was shaped by this term, and removing the row would
    erase the only explanation for the wording a reader is looking at. Retiring
    it stops it shaping anything new, and `POST .../restore` undoes it.
    """
    entry = await _load_entry(db, entry_id)

    entry.status = "retired"
    await db.commit()
    await db.refresh(entry)
    return GlossaryEntryResponse.model_validate(entry)


@router.delete(
    "/admin/glossary/{entry_id}/permanent",
    response_model=GlossaryEntryResponse,
)
async def delete_glossary_entry(
    entry_id: str,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_admin_user),
) -> GlossaryEntryResponse:
    """Remove a retired entry from the table for good.

    Retirement, not deletion, is still the way a term stops being used: a
    translation delivered last month was shaped by whatever was active then,
    and the row is the only explanation for wording somebody may still be
    reading. This exists for the other case — a term typed in by mistake, which
    explains nothing because it never shaped anything anyone saw.

    Only a retired entry can be deleted, which is what keeps the two apart. An
    active entry is in use by definition, so the answer to "delete this" is
    always "retire it first and see".

    The response is the row as it was, because after this call there is nowhere
    left to look it up.
    """
    entry = await _load_entry(db, entry_id)

    if entry.status != "retired":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only a retired entry can be deleted. Retire it first.",
        )

    # Read the response before the row goes, not after.
    response = GlossaryEntryResponse.model_validate(entry)
    await db.delete(entry)
    await db.commit()
    return response


@router.get("/admin/feedback", response_model=FeedbackOverviewResponse)
async def read_feedback_overview(
    limit: int = Query(default=50, ge=1, le=MAX_PAGE),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_admin_user),
) -> FeedbackOverviewResponse:
    """What readers said about translations, including an anonymous review queue.

    Votes are a histogram over `feedbacks.rating`; the interface sends 5 for a
    thumb up and 1 for a thumb down, so those two buckets are named. Anything
    else stays visible in `ratings` rather than being given a bucket of its
    own: there is no third answer to offer, and a "neither" count that is
    always zero reads as an opinion nobody holds.

    `review_entries` combines the raw feedback row and append-only wording
    edits. It intentionally omits every identity and conversation field while
    retaining the three texts an administrator needs to judge a translation:
    source, machine rendering and the reader's replacement wording.

    The existing `shared_corrections` remains the opt-in, anonymised feed used
    by glossary mining. It is not replaced by the review queue: the two serve
    different purposes and have different retention/privacy constraints.
    """
    counts = (
        await db.execute(
            select(Feedback.rating, func.count()).group_by(Feedback.rating)
        )
    ).all()
    ratings = {int(rating): int(count) for rating, count in counts}
    up = ratings.get(5, 0)
    down = ratings.get(1, 0)
    total = sum(ratings.values())
    votes = FeedbackVoteSummary(
        up=up,
        down=down,
        total=total,
        up_rate=round(up / total, 4) if total else 0.0,
        ratings=ratings,
    )

    shared_total = int(
        await db.scalar(
            select(func.count())
            .select_from(CorrectionLog)
            .where(CorrectionLog.consent_to_share.is_(True))
        )
        or 0
    )
    withheld_total = int(
        await db.scalar(
            select(func.count())
            .select_from(CorrectionLog)
            .where(CorrectionLog.consent_to_share.is_(False))
        )
        or 0
    )
    edited_translations = int(
        await db.scalar(
            select(func.count(func.distinct(TranslationEdit.translation_id)))
        )
        or 0
    )

    rows = (
        await db.scalars(
            select(CorrectionLog)
            .where(CorrectionLog.consent_to_share.is_(True))
            .order_by(CorrectionLog.observed_at.desc())
            .limit(limit)
        )
    ).all()

    feedback_rows = (
        await db.execute(
            select(Feedback, TranslationResult, Message)
            .join(TranslationResult, Feedback.translation_id == TranslationResult.id)
            .join(Message, TranslationResult.message_id == Message.id)
            .order_by(Feedback.created_at.desc())
            .limit(limit)
        )
    ).all()
    # Reader edits are append-only so that the product can audit a correction,
    # but repeating every revision in this overview made a single translation
    # look like many independent quality signals.  The review page shows the
    # latest wording per translated message; the full revision trail remains
    # in its own persistence boundary.
    latest_edit_rank = (
        select(
            TranslationEdit.id.label("edit_id"),
            func.row_number()
            .over(
                partition_by=TranslationEdit.translation_id,
                order_by=(TranslationEdit.created_at.desc(), TranslationEdit.id.desc()),
            )
            .label("rank"),
        )
        .subquery()
    )
    edit_rows = (
        await db.execute(
            select(TranslationEdit, TranslationResult, Message)
            .join(latest_edit_rank, TranslationEdit.id == latest_edit_rank.c.edit_id)
            .join(TranslationResult, TranslationEdit.translation_id == TranslationResult.id)
            .join(Message, TranslationResult.message_id == Message.id)
            .where(latest_edit_rank.c.rank == 1)
            .order_by(TranslationEdit.created_at.desc())
            .limit(limit)
        )
    ).all()

    review_entries = [
        FeedbackReviewEntry(
            entry_type="vote",
            original_text=message.original_text,
            translated_text=translation.translated_text,
            source_language=message.source_language,
            target_language=translation.target_language,
            model=translation.model,
            vote="up" if feedback.rating == 5 else "down" if feedback.rating == 1 else "other",
            rating=feedback.rating,
            user_correction=feedback.correction,
            created_at=feedback.created_at,
        )
        for feedback, translation, message in feedback_rows
    ]
    review_entries.extend(
        FeedbackReviewEntry(
            entry_type="edit",
            original_text=message.original_text,
            translated_text=translation.translated_text,
            source_language=message.source_language,
            target_language=translation.target_language,
            model=translation.model,
            user_correction=edit.edited_text,
            created_at=edit.created_at,
        )
        for edit, translation, message in edit_rows
    )

    def review_entry_time(entry: FeedbackReviewEntry) -> datetime:
        """Normalise SQLite's naive server defaults before chronological sorting."""
        return entry.created_at.replace(tzinfo=UTC) if entry.created_at.tzinfo is None else entry.created_at

    review_entries.sort(key=review_entry_time, reverse=True)

    # This dashboard intentionally hides people and conversations.  Without
    # those identifiers, repeated tests of the same sentence appeared as a
    # wall of indistinguishable rows.  Collapse matching anonymous evidence
    # while retaining its frequency, and keep the newest timestamp as the
    # representative row.
    grouped_entries: dict[tuple[object, ...], FeedbackReviewEntry] = {}
    for entry in review_entries:
        key = (
            entry.entry_type,
            entry.original_text,
            entry.translated_text,
            entry.source_language,
            entry.target_language,
            entry.model,
            entry.vote,
            entry.rating,
            entry.user_correction,
        )
        existing = grouped_entries.get(key)
        if existing is None:
            grouped_entries[key] = entry
        else:
            existing.occurrence_count += 1
    review_entries = list(grouped_entries.values())

    return FeedbackOverviewResponse(
        votes=votes,
        edited_translations=edited_translations,
        review_entries=review_entries[:limit],
        shared_corrections=[SharedCorrection.model_validate(row) for row in rows],
        shared_total=shared_total,
        withheld_total=withheld_total,
    )


def _now():
    """The current instant, as the database columns store it."""
    from datetime import UTC, datetime

    return datetime.now(UTC)
