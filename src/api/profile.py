"""Profile HTTP endpoints."""

import logging

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.deps import get_current_user
from src.core.security import verify_password
from src.database import get_db
from src.database.models import (
    AGENT_CONSENT_POLICY_VERSION,
    BlockedUser,
    User,
)
from src.schemas.agent_consent import (
    AgentConsentEntry,
    AgentConsentsResponse,
    AgentConsentsUpdate,
)
from src.schemas.auth import (
    SUPPORTED_LANGUAGES,
    AccountDeletionRequest,
    UpdateInterfaceLanguageRequest,
    UpdateLanguageRequest,
    UserProfileUpdate,
    UserResponse,
    UserSettingsResponse,
    UserSettingsUpdate,
)
from src.services.assistant.agent_consent import get_consents, set_consents
from src.services.identity.account_erasure import AccountErasureError, erase_account
from src.services.identity.audit import record_audit_event
from src.services.identity.user_settings import get_or_create_user_settings, update_user_settings
from src.services.messaging.blocking import (
    block_user,
    list_blocked_user_ids,
    unblock_user,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/auth/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: User = Depends(get_current_user),
) -> UserResponse:
    """Get the current authenticated user's information.

    Args:
        current_user: The authenticated user from JWT token

    Returns:
        User information (excludes password hash)
    """
    return UserResponse.model_validate(current_user)


@router.patch("/auth/me", response_model=UserResponse)
async def update_current_user_profile(
    request: UserProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Persist the current user's editable profile fields only."""
    for field, value in request.model_dump(exclude_unset=True).items():
        setattr(current_user, field, value)
    await db.commit()
    await db.refresh(current_user)
    return UserResponse.model_validate(current_user)


@router.get("/auth/me/settings", response_model=UserSettingsResponse)
async def get_current_user_settings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserSettingsResponse:
    return UserSettingsResponse.model_validate(await get_or_create_user_settings(db, current_user.id))


@router.patch("/auth/me/settings", response_model=UserSettingsResponse)
async def patch_current_user_settings(
    request: UserSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserSettingsResponse:
    settings = await update_user_settings(db, current_user.id, request.model_dump(exclude_unset=True))
    return UserSettingsResponse.model_validate(settings)


@router.get("/auth/me/agent-consents", response_model=AgentConsentsResponse)
async def get_current_user_agent_consents(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AgentConsentsResponse:
    """Return every assistant permission for the caller (`CONTRACT.md` §3.15)."""
    return AgentConsentsResponse(
        policy_version=AGENT_CONSENT_POLICY_VERSION,
        consents=[AgentConsentEntry.model_validate(row) for row in await get_consents(db, current_user.id)],
    )


@router.put("/auth/me/agent-consents", response_model=AgentConsentsResponse)
async def put_current_user_agent_consents(
    request: AgentConsentsUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AgentConsentsResponse:
    """Grant or revoke the named scopes and return the full current set."""
    before = {row.scope: row.is_granted for row in await get_consents(db, current_user.id)}
    consents = await set_consents(db, current_user.id, request.consents)
    for scope, granted in request.consents.items():
        if before.get(scope, False) != granted:
            await record_audit_event(
                db,
                actor_id=current_user.id,
                action="assistant.consent_granted" if granted else "assistant.consent_revoked",
                resource_type="agent_consent",
                resource_id=scope,
                metadata={"scope": scope},
            )
    await db.commit()
    return AgentConsentsResponse(
        policy_version=AGENT_CONSENT_POLICY_VERSION,
        consents=[AgentConsentEntry.model_validate(row) for row in consents],
    )


@router.delete("/auth/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_current_account(
    request: AccountDeletionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Irreversibly erase direct account data after explicit confirmation."""
    if current_user.password_hash and not verify_password(request.current_password or "", current_user.password_hash):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Current password is required")
    try:
        await erase_account(db, current_user)
    except AccountErasureError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.put("/auth/me/language", response_model=UserResponse)
async def update_preferred_language(
    request: UpdateLanguageRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Update the current user's preferred language.

    Args:
        request: New language preference
        current_user: The authenticated user from JWT token
        db: Database session

    Returns:
        Updated user information
    """
    current_user.preferred_language = request.preferred_language
    await db.commit()
    await db.refresh(current_user)

    return UserResponse.model_validate(current_user)


@router.put("/auth/me/interface-language", response_model=UserResponse)
async def update_interface_language(
    request: UpdateInterfaceLanguageRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Update the language the interface is drawn in (docs/api/contract.md §1.2).

    Deliberately does not touch `preferred_language`. The two are set together
    only once, when the account is created; from then on someone reading
    Japanese messages behind a Vietnamese menu is a choice the product allows.

    Args:
        request: New interface language.
        current_user: The authenticated user from JWT token.
        db: Database session.

    Returns:
        Updated user information.
    """
    current_user.interface_language = request.interface_language
    await db.commit()
    await db.refresh(current_user)

    return UserResponse.model_validate(current_user)


@router.get("/languages", response_model=list[str])
async def list_supported_languages() -> list[str]:
    """Return the language codes the system accepts.

    Serving the allowlist is what lets the frontend stop keeping its own copy;
    `SUPPORTED_LANGUAGES` stays the single source of truth (CONTRACT section 1).
    """
    return sorted(SUPPORTED_LANGUAGES)


@router.get("/users", response_model=list[UserResponse])
async def find_users(
    q: str = Query(..., min_length=2, max_length=255),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[UserResponse]:
    """Find accounts to start a conversation with (docs/api/contract.md §3.1).

    Matches a case-insensitive prefix of the email, the username or the display
    name. Prefix rather than substring: matching anywhere inside the string turns
    this into a way to walk the whole user table one letter at a time, while a
    prefix still finds the person whose name or address the caller is typing.

    `func.lower` rather than `ilike`: SQLite — which the tests run on — applies
    `LIKE` case-insensitively to ASCII only, so `ilike` would agree with
    PostgreSQL on the test data and quietly disagree on real names.

    The caller is excluded: offering someone a conversation with themselves is
    the one result that is never what they meant.

    A list rather than a single object: an empty list is an unambiguous "no such
    account", where a 404 would be confused with a broken route.

    Authentication is required so it is not an anonymous enumeration oracle. It
    remains one for signed-in users, which is accepted at this stage — see the
    rate-limiting note in docs/operations/deployment.md.
    """
    # `%` and `_` are LIKE wildcards, so a query of "%" would otherwise list the
    # whole table — exactly what prefix matching is here to prevent.
    needle = q.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    prefix = f"{needle}%"
    # A display name is several words, and people search for the one they know:
    # "An" must find "Nguyễn An". Any word may start the match, but no match
    # starts inside a word, so the walk-the-table problem above stays closed.
    word_prefix = f"% {needle}%"
    users = await db.scalars(
        select(User)
        .where(
            User.id != current_user.id,
            ~select(BlockedUser.blocker_id)
            .where(
                BlockedUser.blocker_id == current_user.id,
                BlockedUser.blocked_id == User.id,
            )
            .exists(),
            ~select(BlockedUser.blocker_id)
            .where(
                BlockedUser.blocker_id == User.id,
                BlockedUser.blocked_id == current_user.id,
            )
            .exists(),
            or_(
                func.lower(User.email).like(prefix, escape="\\"),
                func.lower(User.username).like(prefix, escape="\\"),
                func.lower(User.display_name).like(prefix, escape="\\"),
                func.lower(User.display_name).like(word_prefix, escape="\\"),
            ),
        )
        .order_by(User.email)
        .limit(20)
    )
    return [UserResponse.model_validate(user) for user in users]


@router.put("/users/{user_id}/block", status_code=status.HTTP_204_NO_CONTENT)
async def block_contact(
    user_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    if await db.get(User, user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User was not found")
    try:
        await block_user(db, current_user.id, user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.delete("/users/{user_id}/block", status_code=status.HTTP_204_NO_CONTENT)
async def unblock_contact(
    user_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await unblock_user(db, current_user.id, user_id)


@router.get("/auth/me/blocked-users", response_model=list[UserResponse])
async def get_blocked_contacts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[UserResponse]:
    user_ids = await list_blocked_user_ids(db, current_user.id)
    if not user_ids:
        return []
    users = await db.scalars(select(User).where(User.id.in_(user_ids)))
    by_id = {user.id: user for user in users}
    return [UserResponse.model_validate(by_id[user_id]) for user_id in user_ids if user_id in by_id]


# ========================
# Conversation Endpoints
# ========================
