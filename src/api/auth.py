"""Auth HTTP endpoints."""

import logging
import math
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from sqlalchemy import case, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.core.deps import get_current_user
from src.core.security import (
    create_access_token,
    create_refresh_token,
    get_password_hash,
    hash_refresh_token,
    verify_password,
)
from src.database import get_db
from src.database.models import (
    PasswordResetToken,
    PendingRegistration,
    RefreshSession,
    User,
)
from src.schemas.auth import (
    AuthResponse,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    GoogleLinkResponse,
    GoogleLoginRequest,
    LoginRequest,
    LogoutRequest,
    PendingRegisterResponse,
    RefreshRequest,
    RegisterRequest,
    ResendRegisterOtpRequest,
    ResendRegisterOtpResponse,
    ResetPasswordRequest,
    UserResponse,
    VerifyRegisterRequest,
)
from src.services.identity.email import (
    EmailDeliveryError,
    send_password_reset_email,
    send_registration_otp_email,
)
from src.services.identity.google_auth import GoogleAuthError, GoogleUserInfo, verify_google_token

logger = logging.getLogger(__name__)

router = APIRouter()


async def _issue_auth_response(
    user: User,
    db: AsyncSession,
    *,
    remember: bool,
) -> AuthResponse:
    """Create an access token and a revocable, rotated refresh session."""
    if user.deleted_at is not None or user.suspended_at is not None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account access is suspended")
    settings = get_settings()
    refresh_token = create_refresh_token()
    refresh_days = settings.refresh_expire_days if remember else 1
    db.add(
        RefreshSession(
            user_id=user.id,
            token_hash=hash_refresh_token(refresh_token),
            expires_at=datetime.now(UTC) + timedelta(days=refresh_days),
        )
    )
    await db.commit()
    return AuthResponse(
        access_token=create_access_token(subject=user.id),
        refresh_token=refresh_token,
        user=UserResponse.model_validate(user),
    )


def _ensure_utc(dt: datetime) -> datetime:
    """Ensure a datetime object is timezone-aware with UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _generate_otp() -> str:
    """Generate a zero-padded 6-digit numeric OTP."""
    return f"{secrets.randbelow(1_000_000):06d}"


@router.post(
    "/auth/register",
    response_model=PendingRegisterResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> PendingRegisterResponse:
    """Start registration by sending an OTP; the account is created after verification."""
    duplicate = await db.execute(
        select(User).where((User.email == payload.email) | (User.username == payload.username))
    )
    existing_user = duplicate.scalar_one_or_none()
    if existing_user is not None:
        detail = (
            "Email is already registered" if existing_user.email == payload.email else "Username is already registered"
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)

    now = datetime.now(UTC)
    otp = _generate_otp()
    otp_hash = get_password_hash(otp)
    password_hash = get_password_hash(payload.password)
    display_name = (payload.display_name or payload.username).strip()
    cooldown_threshold = now - timedelta(seconds=60)
    window_threshold = now - timedelta(seconds=3600)

    # Check for existing pending registration by email or username
    pending_query = await db.execute(
        select(PendingRegistration).where(
            (PendingRegistration.email == payload.email) | (PendingRegistration.username == payload.username)
        )
    )
    existing_pending = pending_query.scalars().first()

    if existing_pending is not None:
        is_window_expired = PendingRegistration.rate_window_started_at <= window_threshold
        stmt = (
            update(PendingRegistration)
            .where(
                PendingRegistration.id == existing_pending.id,
                PendingRegistration.last_sent_at <= cooldown_threshold,
                (PendingRegistration.rate_window_started_at <= window_threshold)
                | (PendingRegistration.request_count < 5),
            )
            .values(
                email=payload.email,
                username=payload.username,
                display_name=display_name,
                password_hash=password_hash,
                preferred_language=payload.preferred_language,
                interface_language=payload.preferred_language,
                otp_hash=otp_hash,
                attempts=0,
                expires_at=now + timedelta(minutes=5),
                last_sent_at=now,
                rate_window_started_at=case(
                    (is_window_expired, now),
                    else_=PendingRegistration.rate_window_started_at,
                ),
                request_count=case(
                    (is_window_expired, 1),
                    else_=PendingRegistration.request_count + 1,
                ),
            )
            .execution_options(synchronize_session=False)
            .returning(
                PendingRegistration.id,
                PendingRegistration.email,
            )
        )
        update_res = await db.execute(stmt)
        updated_row = update_res.mappings().one_or_none()
        if updated_row is not None:
            await db.commit()
            pending_id = updated_row["id"]
        else:
            # Conditional update failed: check if due to cooldown or rate limit
            pending = await db.get(PendingRegistration, existing_pending.id)
            if pending is not None:
                last_sent = _ensure_utc(pending.last_sent_at)
                remaining_cooldown = math.ceil(60 - (now - last_sent).total_seconds())
                if remaining_cooldown > 0:
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail="Please wait before requesting another code.",
                        headers={"Retry-After": str(max(1, remaining_cooldown))},
                    )
                win_start = _ensure_utc(pending.rate_window_started_at)
                if pending.request_count >= 5 and (now - win_start).total_seconds() < 3600:
                    retry_after = math.ceil(3600 - (now - win_start).total_seconds())
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail="Too many verification requests. Please try again later.",
                        headers={"Retry-After": str(max(1, retry_after))},
                    )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Please wait before requesting another code.",
                headers={"Retry-After": "60"},
            )
    else:
        pending = PendingRegistration(
            email=payload.email,
            username=payload.username,
            display_name=display_name,
            password_hash=password_hash,
            preferred_language=payload.preferred_language,
            interface_language=payload.preferred_language,
            otp_hash=otp_hash,
            expires_at=now + timedelta(minutes=5),
            attempts=0,
            last_sent_at=now,
            rate_window_started_at=now,
            request_count=1,
        )
        db.add(pending)
        try:
            await db.commit()
            pending_id = pending.id
        except IntegrityError as exc:
            await db.rollback()
            # Concurrent insertion raced for this email/username:
            # check the newly inserted row and apply cooldown/rate limits
            raced_pending = (
                (
                    await db.execute(
                        select(PendingRegistration).where(
                            (PendingRegistration.email == payload.email)
                            | (PendingRegistration.username == payload.username)
                        )
                    )
                )
                .scalars()
                .first()
            )
            if raced_pending is not None:
                last_sent = _ensure_utc(raced_pending.last_sent_at)
                remaining = math.ceil(60 - (now - last_sent).total_seconds())
                if remaining > 0:
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail="Please wait before requesting another code.",
                        headers={"Retry-After": str(max(1, remaining))},
                    )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email or username is already registered",
            ) from exc

    # Delivery occurs strictly AFTER database commit
    try:
        await send_registration_otp_email(
            to_email=payload.email,
            otp=otp,
            language=payload.preferred_language,
        )
    except EmailDeliveryError as exc:
        logger.error("Email delivery failed for pending registration %s: %s", pending_id, type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="email_delivery_failed",
        ) from None

    return PendingRegisterResponse(
        pending_id=pending_id,
        email=payload.email,
        expires_in_seconds=300,
        cooldown_seconds=60,
        message="Verification code sent to your email",
    )


@router.post("/auth/register/resend", response_model=ResendRegisterOtpResponse)
async def resend_register_otp(
    request: ResendRegisterOtpRequest,
    db: AsyncSession = Depends(get_db),
) -> ResendRegisterOtpResponse:
    """Replace an expired or misplaced registration OTP, subject to rate limits."""
    now = datetime.now(UTC)
    otp = _generate_otp()
    otp_hash = get_password_hash(otp)
    cooldown_threshold = now - timedelta(seconds=60)
    window_threshold = now - timedelta(seconds=3600)

    is_window_expired = PendingRegistration.rate_window_started_at <= window_threshold
    stmt = (
        update(PendingRegistration)
        .where(
            PendingRegistration.id == request.pending_id,
            PendingRegistration.last_sent_at <= cooldown_threshold,
            (PendingRegistration.rate_window_started_at <= window_threshold) | (PendingRegistration.request_count < 5),
        )
        .values(
            otp_hash=otp_hash,
            attempts=0,
            expires_at=now + timedelta(minutes=5),
            last_sent_at=now,
            rate_window_started_at=case(
                (is_window_expired, now),
                else_=PendingRegistration.rate_window_started_at,
            ),
            request_count=case(
                (is_window_expired, 1),
                else_=PendingRegistration.request_count + 1,
            ),
        )
        .execution_options(synchronize_session=False)
        .returning(
            PendingRegistration.id,
            PendingRegistration.email,
            PendingRegistration.interface_language,
        )
    )
    update_res = await db.execute(stmt)
    row = update_res.mappings().one_or_none()
    if row is not None:
        await db.commit()
        # Delivery occurs strictly AFTER database commit
        try:
            await send_registration_otp_email(
                to_email=row["email"],
                otp=otp,
                language=row["interface_language"],
            )
        except EmailDeliveryError as exc:
            logger.error("Email delivery failed for pending registration %s: %s", row["id"], type(exc).__name__)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="email_delivery_failed",
            ) from None

        return ResendRegisterOtpResponse(
            pending_id=row["id"],
            expires_in_seconds=300,
            cooldown_seconds=60,
            message="New verification code sent to your email",
        )

    # If update matched 0 rows, check reasons (not found, cooldown, or rate limit)
    pending = await db.get(PendingRegistration, request.pending_id)
    if pending is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pending registration not found or already verified",
        )

    last_sent = _ensure_utc(pending.last_sent_at)
    remaining_cooldown = math.ceil(60 - (now - last_sent).total_seconds())
    if remaining_cooldown > 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Please wait before requesting another code.",
            headers={"Retry-After": str(max(1, remaining_cooldown))},
        )

    win_start = _ensure_utc(pending.rate_window_started_at)
    if pending.request_count >= 5 and (now - win_start).total_seconds() < 3600:
        retry_after = math.ceil(3600 - (now - win_start).total_seconds())
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many verification requests. Please try again later.",
            headers={"Retry-After": str(max(1, retry_after))},
        )

    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Please wait before requesting another code.",
        headers={"Retry-After": "60"},
    )


@router.post("/auth/register/verify", response_model=AuthResponse)
async def verify_register_otp(
    request: VerifyRegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    """Create the account and authenticate it after a valid, unused OTP."""
    pending = await db.get(PendingRegistration, request.pending_id)
    if pending is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification session",
        )

    now = datetime.now(UTC)
    expires_at = _ensure_utc(pending.expires_at)

    if pending.attempts >= 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum verification attempts exceeded. Please request a new code.",
        )

    if now >= expires_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification code has expired. Please request a new code.",
        )

    if not verify_password(request.otp, pending.otp_hash):
        update_stmt = (
            update(PendingRegistration)
            .where(
                PendingRegistration.id == request.pending_id,
                PendingRegistration.otp_hash == pending.otp_hash,
                PendingRegistration.expires_at > now,
                PendingRegistration.attempts < 3,
            )
            .values(attempts=PendingRegistration.attempts + 1)
            .execution_options(synchronize_session=False)
            .returning(PendingRegistration.attempts)
        )
        update_res = await db.execute(update_stmt)
        new_attempts = update_res.scalar_one_or_none()
        await db.commit()

        if new_attempts is None or new_attempts >= 3:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Maximum verification attempts exceeded. Please request a new code.",
            )
        remaining = max(0, 3 - new_attempts)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid verification code. {remaining} attempt{'s' if remaining != 1 else ''} remaining.",
        )

    # Atomic conditional DELETE ... RETURNING
    delete_stmt = (
        delete(PendingRegistration)
        .where(
            PendingRegistration.id == request.pending_id,
            PendingRegistration.otp_hash == pending.otp_hash,
            PendingRegistration.expires_at > now,
            PendingRegistration.attempts < 3,
        )
        .execution_options(synchronize_session=False)
        .returning(
            PendingRegistration.id,
            PendingRegistration.email,
            PendingRegistration.username,
            PendingRegistration.display_name,
            PendingRegistration.password_hash,
            PendingRegistration.preferred_language,
            PendingRegistration.interface_language,
        )
    )
    delete_res = await db.execute(delete_stmt)
    consumed = delete_res.mappings().one_or_none()
    if consumed is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification code already used or invalid.",
        )

    # Check for existing user conflict
    duplicate = await db.execute(
        select(User).where((User.email == consumed["email"]) | (User.username == consumed["username"]))
    )
    existing = duplicate.scalar_one_or_none()
    if existing is not None:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email or username is already registered",
        )

    # Create User and initial RefreshSession in the same single transaction
    user = User(
        email=consumed["email"],
        username=consumed["username"],
        display_name=consumed["display_name"],
        password_hash=consumed["password_hash"],
        preferred_language=consumed["preferred_language"],
        interface_language=consumed["interface_language"],
        role="member",
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email or username is already registered",
        ) from exc

    settings = get_settings()
    refresh_token = create_refresh_token()
    refresh_days = settings.refresh_expire_days
    db.add(
        RefreshSession(
            user_id=user.id,
            token_hash=hash_refresh_token(refresh_token),
            expires_at=datetime.now(UTC) + timedelta(days=refresh_days),
        )
    )
    await db.commit()

    return AuthResponse(
        access_token=create_access_token(subject=user.id),
        refresh_token=refresh_token,
        user=UserResponse.model_validate(user),
    )


@router.post("/auth/login", response_model=AuthResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    """Authenticate user and return JWT token.

    Args:
        payload: Login credentials (email and password)
        request: Raw HTTP request used by SlowAPI
        db: Database session

    Returns:
        JWT access token

    Raises:
        HTTPException: If credentials are invalid or rate limit exceeded
    """
    # Find user by email
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()

    if user is None or not user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # Verify password
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    return await _issue_auth_response(user, db, remember=payload.remember)


async def _user_by_google_sub(db: AsyncSession, google_sub: str) -> User | None:
    """Return the canonical owner of a Google subject, if one exists.

    The unique database constraint should make this a zero-or-one lookup. If a
    legacy or manually-corrupted database violates that invariant, fail closed
    rather than issuing a session for an arbitrary row.
    """
    result = await db.execute(select(User).where(User.google_sub == google_sub))
    users = result.scalars().all()
    if len(users) > 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Google account identity conflict.",
        )
    return users[0] if users else None


async def _user_by_google_email(db: AsyncSession, email: str) -> User | None:
    """Return the account for an already verified, normalized Google email.

    The current application always normalizes email before storing it, but an
    older case-sensitive unique index could contain case variants. Treat that
    as an explicit conflict instead of logging into whichever row happens to
    be returned first.
    """
    result = await db.execute(select(User).where(func.lower(User.email) == email))
    users = result.scalars().all()
    if len(users) > 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email identity conflict.",
        )
    return users[0] if users else None


async def _user_by_id(db: AsyncSession, user_id: str) -> User | None:
    """Reload a user after a SQL UPDATE that bypasses the ORM identity map."""
    result = await db.execute(select(User).where(User.id == user_id).execution_options(populate_existing=True))
    return result.scalar_one_or_none()


async def _claim_google_sub(
    db: AsyncSession,
    *,
    user_id: str,
    google_sub: str,
) -> User | None:
    """Atomically attach a Google subject only while the account is unlinked.

    The conditional UPDATE is the concurrency boundary.  A competing request
    cannot overwrite a subject that another request has just attached, even
    when the two subjects are different and therefore would not hit the unique
    constraint.  Every unsuccessful claim rolls the session back before a
    caller re-fetches canonical state.
    """
    try:
        claimed_id = (
            await db.execute(
                update(User)
                .where(User.id == user_id, User.google_sub.is_(None))
                .values(google_sub=google_sub)
                .returning(User.id)
                .execution_options(synchronize_session=False)
            )
        ).scalar_one_or_none()
        if claimed_id is None:
            await db.rollback()
            return None
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return None

    return await _user_by_id(db, user_id)


async def _reconcile_google_login_race(
    db: AsyncSession,
    google_info: GoogleUserInfo,
) -> User:
    """Re-fetch authoritative state after a failed Google link/create claim."""
    # Subject ownership always wins: it is the immutable Google identity and is
    # intentionally checked before the mutable email address.
    by_sub = await _user_by_google_sub(db, google_info.google_sub)
    if by_sub is not None:
        return by_sub

    by_email = await _user_by_google_email(db, google_info.email)
    if by_email is not None and by_email.google_sub is None:
        # A concurrent create may have made the email appear after our first
        # lookup. Claim it through the same compare-and-swap boundary.
        linked = await _claim_google_sub(
            db,
            user_id=by_email.id,
            google_sub=google_info.google_sub,
        )
        if linked is not None:
            return linked

        by_sub = await _user_by_google_sub(db, google_info.google_sub)
        if by_sub is not None:
            return by_sub
        by_email = await _user_by_google_email(db, google_info.email)

    if by_email is not None and by_email.google_sub == google_info.google_sub:
        return by_email
    if by_email is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This email is already linked to a different Google account.",
        )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Google account registration conflict.",
    )


@router.post("/auth/google/login", response_model=AuthResponse)
@router.post("/auth/google", response_model=AuthResponse)
async def google_login(
    request: GoogleLoginRequest,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    """Log in or sign up with a Google ID token as a full authentication provider."""
    try:
        google_info: GoogleUserInfo = await verify_google_token(request.credential)
    except GoogleAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    # CASE 1: Subject match takes precedence over email.
    user = await _user_by_google_sub(db, google_info.google_sub)
    if user is not None:
        return await _issue_auth_response(user, db, remember=request.remember)

    # CASE 2: The verified email may claim only an unlinked account.
    email_user = await _user_by_google_email(db, google_info.email)
    if email_user is not None:
        if email_user.google_sub is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This email is already linked to a different Google account.",
            )

        linked = await _claim_google_sub(
            db,
            user_id=email_user.id,
            google_sub=google_info.google_sub,
        )
        if linked is None:
            linked = await _reconcile_google_login_race(db, google_info)

        logger.info("Google login resolved to existing user %s", linked.id)
        return await _issue_auth_response(linked, db, remember=request.remember)

    # CASE 3: Create a password-less Google-native account. The unique database
    # constraints remain authoritative; an IntegrityError is reconciled below.
    new_user = User(
        email=google_info.email,
        google_sub=google_info.google_sub,
        password_hash=None,
        display_name=google_info.name,
        username=None,
        role="member",
        preferred_language="en",
        interface_language="en",
    )
    db.add(new_user)
    try:
        await db.commit()
        await db.refresh(new_user)
    except IntegrityError:
        await db.rollback()
        canonical = await _reconcile_google_login_race(db, google_info)
        return await _issue_auth_response(canonical, db, remember=request.remember)

    logger.info("Created new Google-native user %s", new_user.id)
    return await _issue_auth_response(new_user, db, remember=request.remember)


@router.post("/auth/me/google/link", response_model=GoogleLinkResponse)
async def link_google_account(
    request: GoogleLoginRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GoogleLinkResponse:
    """Link a Google account to the current LinguaFlow account (Batch G).

    The user must have a valid session (JWT). After linking they can log in with
    Google on any device. One Google account maps to one LinguaFlow account.
    """
    # _claim_google_sub may roll back the AsyncSession, which expires ORM
    # instances. Keep the caller identity as an immutable primitive for every
    # operation after that concurrency boundary.
    current_user_id = current_user.id

    if current_user.google_sub is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This account is already linked to a Google account.",
        )

    try:
        google_info: GoogleUserInfo = await verify_google_token(request.credential)
    except GoogleAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    linked = await _claim_google_sub(
        db,
        user_id=current_user_id,
        google_sub=google_info.google_sub,
    )
    if linked is None:
        owner = await _user_by_google_sub(db, google_info.google_sub)
        if owner is None or owner.id != current_user_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This Google account is already linked to another user.",
            )

    logger.info("Google account linked to user %s", current_user_id)
    return GoogleLinkResponse(
        google_linked=True,
        message="Google account linked successfully.",
    )


@router.delete("/auth/me/google/link", response_model=GoogleLinkResponse)
async def unlink_google_account(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GoogleLinkResponse:
    """Remove the Google link from the current LinguaFlow account (Batch G).

    If already unlinked, returns 200 with google_linked=false (idempotent).
    If google_sub exists and password_hash is NULL, returns 409 Conflict
    to prevent locking the user out of their only authentication method.
    """
    if current_user.google_sub is None:
        return GoogleLinkResponse(
            google_linked=False,
            message="Google account unlinked successfully.",
        )

    if not current_user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot unlink Google account without a password. Please set a password first.",
        )

    current_user.google_sub = None
    await db.commit()
    logger.info("Google account unlinked from user %s", current_user.id)

    return GoogleLinkResponse(
        google_linked=False,
        message="Google account unlinked successfully.",
    )


@router.post("/auth/refresh", response_model=AuthResponse)
async def refresh_session(
    request: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    """Rotate a valid refresh token and return a fresh authenticated session."""
    result = await db.execute(
        select(RefreshSession).where(RefreshSession.token_hash == hash_refresh_token(request.refresh_token))
    )
    session = result.scalar_one_or_none()
    invalid = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token")
    if session is None or session.revoked_at is not None:
        raise invalid

    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        session.revoked_at = datetime.now(UTC)
        await db.commit()
        raise invalid

    user = await db.get(User, session.user_id)
    if user is None:
        raise invalid
    session.revoked_at = datetime.now(UTC)
    return await _issue_auth_response(user, db, remember=(expires_at - datetime.now(UTC)).days > 1)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: LogoutRequest,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Revoke a browser session. The operation is idempotent."""
    result = await db.execute(
        select(RefreshSession).where(RefreshSession.token_hash == hash_refresh_token(request.refresh_token))
    )
    session = result.scalar_one_or_none()
    if session is not None and session.revoked_at is None:
        session.revoked_at = datetime.now(UTC)
        await db.commit()


@router.post("/auth/password/forgot", response_model=ForgotPasswordResponse)
async def forgot_password(
    request: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
) -> ForgotPasswordResponse:
    """Email a short-lived reset link without revealing account existence."""
    result = await db.execute(select(User).where(User.email == request.email))
    user = result.scalar_one_or_none()
    generic = "If an account exists for that email, a password reset link will be sent shortly."
    if user is None:
        return ForgotPasswordResponse(message=generic)

    settings = get_settings()
    now = datetime.now(UTC)
    raw_token = create_refresh_token()
    # Only the newest link remains usable. This limits the impact of a link
    # sitting in an old inbox while retaining the single-use token guarantee.
    await db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_refresh_token(raw_token),
            expires_at=now + timedelta(minutes=settings.password_reset_expire_minutes),
        )
    )
    await db.commit()

    reset_url = f"{settings.frontend_url}/reset-password?{urlencode({'token': raw_token})}"
    try:
        await send_password_reset_email(
            to_email=user.email,
            reset_url=reset_url,
            expires_in_minutes=settings.password_reset_expire_minutes,
            language=user.interface_language,
        )
    except EmailDeliveryError as exc:
        # Preserve the same response for existing and unknown emails. Returning
        # an SMTP error only for real accounts would reintroduce account probing.
        logger.error("Password-reset email delivery failed for user %s: %s", user.id, type(exc).__name__)

    # Non-production environments keep the opaque token available for automated
    # tests and offline work. Production never exposes it through the API response.
    return ForgotPasswordResponse(
        message=generic,
        reset_token=raw_token if settings.app_env != "production" else None,
    )


@router.post("/auth/password/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    request: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Consume a reset token, change the password, and revoke all sessions."""
    result = await db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_refresh_token(request.token))
    )
    reset = result.scalar_one_or_none()
    invalid = HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset token")
    if reset is None or reset.used_at is not None:
        raise invalid
    expires_at = reset.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        raise invalid
    user = await db.get(User, reset.user_id)
    if user is None:
        raise invalid

    now = datetime.now(UTC)
    user.password_hash = get_password_hash(request.new_password)
    reset.used_at = now
    await db.execute(
        update(RefreshSession)
        .where(RefreshSession.user_id == user.id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await db.commit()
