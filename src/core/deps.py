"""FastAPI dependencies for authentication."""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import decode_token
from src.database import get_db
from src.database.models import User

# HTTP Bearer token scheme
bearer_scheme = HTTPBearer(auto_error=False)


async def get_user_by_token(token: str, db: AsyncSession) -> User | None:
    """Resolve a database user from a validated access token.

    The helper is transport-neutral so HTTP dependencies and WebSocket handlers
    use the same token validation and user lookup path.
    """
    payload = decode_token(token)
    if payload is None:
        return None

    user_id = payload.get("sub")
    if not isinstance(user_id, str) or not user_id:
        return None

    result = await db.execute(
        select(User).where(User.id == user_id, User.deleted_at.is_(None), User.suspended_at.is_(None))
    )
    return result.scalar_one_or_none()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Get the current authenticated user from JWT token.

    This dependency extracts the user ID from the JWT token in the Authorization
    header and fetches the user from the database.

    Args:
        credentials: The Bearer token credentials
        db: Database session

    Returns:
        The authenticated User

    Raises:
        HTTPException: If token is missing or invalid
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None:
        raise credentials_exception

    user = await get_user_by_token(credentials.credentials, db)

    if user is None:
        raise credentials_exception

    return user


async def get_admin_user(current_user: User = Depends(get_current_user)) -> User:
    """Require the authenticated account to have the administrator role."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user
