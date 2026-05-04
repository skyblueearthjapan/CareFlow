"""Shared FastAPI dependencies: DB session and auth user resolver."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import JWTError, decode_token
from app.db.session import get_session_factory
from app.models.user import User

settings = get_settings()
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.api_v1_prefix}/auth/login",
    auto_error=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """Yield an async DB session bound to the request lifecycle."""
    factory = get_session_factory()
    async with factory() as session:
        yield session


DbDep = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    db: DbDep,
    token: Annotated[str | None, Depends(oauth2_scheme)],
) -> User:
    """Resolve the current authenticated user from a Bearer JWT.

    Raises 401 if missing/invalid; 401 if user disappeared.
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_token(token)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if payload.get("type") not in {"access", None}:
        # Refresh tokens must not be used for normal endpoints.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Wrong token type",
            headers={"WWW-Authenticate": "Bearer"},
        )

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token claims",
        )

    try:
        user_id = UUID(sub)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token subject",
        ) from exc

    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_active_user(user: CurrentUser) -> User:
    """Resolve the current user and reject soft-deleted accounts.

    Phase 2 introduces soft-delete on most resources; user soft-delete is
    represented by a non-null `deleted_at`. Today the User model has no
    deleted_at column yet, so this defensively checks `getattr` and falls
    through when absent. When the column lands the guard takes effect
    without further wiring.
    """
    deleted_at = getattr(user, "deleted_at", None)
    if deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is deactivated",
        )
    return user


CurrentActiveUser = Annotated[User, Depends(get_current_active_user)]


def require_role(*roles: str):
    """Build a dependency that allows only the given roles (admin/manager/staff)."""

    async def _checker(user: CurrentUser) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role",
            )
        return user

    return _checker
