"""Admin-only endpoints — `/api/v1/admin/users` CRUD (Wave 4-F).

Replaces the Phase-1 stub. The role guard (`require_role("admin")`) is now
exercised by every method, and the audit middleware records every mutation
with the actor's user_id + a redacted body.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.core.deps import DbDep, require_role
from app.core.security import hash_password
from app.models.staff import Staff
from app.models.user import USER_ROLES, User
from app.schemas._pagination import Paginated
from app.schemas.admin_user import (
    AdminPasswordResetResponse,
    AdminUserCreate,
    AdminUserCreateResponse,
    AdminUserRead,
    AdminUserUpdate,
)

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Helpers


def _generate_temp_password() -> str:
    """16-char URL-safe random secret. Shown once to the admin."""
    return secrets.token_urlsafe(12)  # ~16 chars after base64 encoding


def _validate_role_or_400(role: str) -> None:
    if role not in USER_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"role must be one of {USER_ROLES}, got {role!r}",
        )


def _escape_like(value: str, escape_char: str = "\\") -> str:
    """Escape LIKE wildcards in a search string to prevent injection / mis-matches."""
    return (
        value.replace(escape_char, escape_char * 2)
        .replace("%", f"{escape_char}%")
        .replace("_", f"{escape_char}_")
    )


async def _ensure_staff_alive_or_422(db, staff_id: UUID) -> None:
    """Reject linkage to a non-existent or soft-deleted staff (FK does not
    detect soft-delete, so an explicit alive check is required)."""
    staff = await db.scalar(select(Staff).where(Staff.id == staff_id, Staff.deleted_at.is_(None)))
    if staff is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="staff_id does not exist or is deleted",
        )


async def _commit_or_409(db) -> None:
    """Translate UniqueViolation → 409, FK error → 422.

    The 409 detail names the offending constraint. PostgreSQL reports the index
    name (``ix_users_..._unique_alive``) while SQLite reports the column
    (``users.email`` etc.), so we match on both flavours.
    """
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        msg = str(exc).lower()
        if "unique" in msg or "duplicate" in msg:
            # Be specific: the INSERT statement always mentions all column names, so
            # we must distinguish by the constraint/index name (PG: "..._unique_alive")
            # or the "table.column" form SQLite uses ("users.staff_id" etc.).
            if "users.staff_id" in msg or "staff_id_unique" in msg:
                detail = "Conflict: this staff is already linked to another account"
            elif "users.email" in msg or "email_unique" in msg:
                detail = "Conflict: email already in use"
            elif "users.username" in msg or "username_unique" in msg:
                detail = "Conflict: username already in use"
            else:
                detail = "Conflict: email / username / staff linkage duplicated"
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Validation error: invalid foreign key",
        ) from exc


# ---------------------------------------------------------------------------
# Routes


@router.get(
    "/users",
    response_model=Paginated[AdminUserRead],
    summary="List users (admin only)",
)
async def list_users(
    db: DbDep,
    _admin: Annotated[User, Depends(require_role("admin"))],
    role: Annotated[str | None, Query(description="Filter by role")] = None,
    q: Annotated[str | None, Query(description="Substring match on email or username")] = None,
    include_deleted: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Paginated[AdminUserRead]:
    conds = []
    if not include_deleted:
        conds.append(User.deleted_at.is_(None))
    if role:
        _validate_role_or_400(role)
        conds.append(User.role == role)
    if q:
        escaped = _escape_like(q.lower())
        pattern = f"%{escaped}%"
        conds.append(
            or_(
                func.lower(User.email).like(pattern, escape="\\"),
                func.lower(User.username).like(pattern, escape="\\"),
            )
        )

    base = select(User)
    count_stmt = select(func.count()).select_from(User)
    if conds:
        base = base.where(and_(*conds))
        count_stmt = count_stmt.where(and_(*conds))

    rows = (
        await db.scalars(base.order_by(User.created_at.desc()).limit(limit).offset(offset))
    ).all()
    total = (await db.scalar(count_stmt)) or 0
    return Paginated[AdminUserRead](
        items=[AdminUserRead.model_validate(r) for r in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.post(
    "/users",
    response_model=AdminUserCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create user + mint a one-shot temporary password",
)
async def create_user(
    payload: AdminUserCreate,
    db: DbDep,
    _admin: Annotated[User, Depends(require_role("admin"))],
) -> AdminUserCreateResponse:
    _validate_role_or_400(payload.role)
    # At least one login identifier is required, otherwise the account can never
    # authenticate (email or username). staff-role accounts commonly carry only
    # a username (staff code).
    if payload.email is None and payload.username is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one of email or username is required",
        )
    # Role-specific identifier requirements (§4): admin/manager need email for
    # current email-based login flow; staff need username for code-based login (P1b).
    if payload.role in ("admin", "manager") and payload.email is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="email is required for admin/manager role",
        )
    if payload.role == "staff" and payload.username is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="username is required for staff role",
        )
    if payload.staff_id is not None:
        await _ensure_staff_alive_or_422(db, payload.staff_id)
    temp_password = _generate_temp_password()
    user = User(
        email=payload.email,
        username=payload.username,
        password_hash=hash_password(temp_password),
        role=payload.role,
        staff_id=payload.staff_id,
        must_change_password=True,
    )
    db.add(user)
    await _commit_or_409(db)
    await db.refresh(user)
    return AdminUserCreateResponse(
        user=AdminUserRead.model_validate(user),
        temp_password=temp_password,
    )


@router.patch(
    "/users/{user_id}",
    response_model=AdminUserRead,
    summary="Update user (role / staff_id / email / must_change_password)",
)
async def update_user(
    user_id: UUID,
    payload: AdminUserUpdate,
    db: DbDep,
    _admin: Annotated[User, Depends(require_role("admin"))],
) -> AdminUserRead:
    user = await db.scalar(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    data = payload.model_dump(exclude_unset=True)
    if "role" in data and data["role"] is not None:
        _validate_role_or_400(data["role"])
    if "email" in data and data["email"] is not None:
        data["email"] = str(data["email"])
    # staff soft-delete check (FK does not detect soft-delete).
    if data.get("staff_id") is not None:
        await _ensure_staff_alive_or_422(db, data["staff_id"])

    for field, value in data.items():
        setattr(user, field, value)

    # Login-possibility guard: unlinking a staff (staff_id=None) or clearing the
    # email must not leave an account with neither email nor username — that
    # would make it permanently unable to log in.
    if user.email is None and user.username is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Account must keep at least one of email or username (login would be impossible)",
        )
    # Role-specific identifier requirements — enforced ONLY when the role is being
    # changed in this PATCH, mirroring create_user so a role switch cannot leave an
    # account inconsistent with its new role. Ordinary edits that don't touch role
    # stay flexible (the login-possibility guard above is the always-on floor).
    if "role" in data:
        if user.role in ("admin", "manager") and user.email is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="email is required for admin/manager role",
            )
        if user.role == "staff" and user.username is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="username is required for staff role",
            )
    await _commit_or_409(db)
    await db.refresh(user)
    return AdminUserRead.model_validate(user)


@router.post(
    "/users/{user_id}/reset-password",
    response_model=AdminPasswordResetResponse,
    summary="Issue a fresh temporary password (forces change on next login)",
)
async def reset_password(
    user_id: UUID,
    db: DbDep,
    _admin: Annotated[User, Depends(require_role("admin"))],
) -> AdminPasswordResetResponse:
    user = await db.scalar(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    temp_password = _generate_temp_password()
    user.password_hash = hash_password(temp_password)
    user.must_change_password = True
    user.failed_login_count = 0
    user.locked_until = None
    await db.commit()
    return AdminPasswordResetResponse(user_id=user.id, temp_password=temp_password)


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete user (admin only)",
)
async def delete_user(
    user_id: UUID,
    db: DbDep,
    admin: Annotated[User, Depends(require_role("admin"))],
) -> None:
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An admin cannot soft-delete their own account",
        )
    user = await db.scalar(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    user.deleted_at = datetime.now(tz=UTC)
    await db.commit()
    return None
