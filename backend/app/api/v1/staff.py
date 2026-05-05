"""Staff CRUD endpoints (Phase 2 domain router / W1-BE2 v2 schema).

Staff role users may only see their own staff record (the one referenced by
their `users.staff_id`); admin/manager see everyone.

W1-BE2 (v2 整理 §4.2): リクエスト / レスポンスの schema (`StaffCreate` /
`StaffUpdate` / `StaffRead`) は v2 9 項目構成に縮約済み。削除カラム
(can_double_team / home_address / home_lat / home_lng / areas /
max_per_day / skill_level / assignment_volume) を payload に含めて送ると
``extra="forbid"`` により 422 で拒否される。状態 (`status`) は
``active`` / ``on_leave`` / ``retired`` の 3 値 enum で厳密化。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.models.staff import Staff
from app.models.user import User
from app.schemas.staff import StaffCreate, StaffRead, StaffUpdate

router = APIRouter()


async def _commit_or_409(db) -> None:
    """Commit and translate IntegrityError into 409/422 (see patients.py)."""
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        msg = str(exc).lower()
        if "unique" in msg or "duplicate" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Conflict: duplicate value",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Validation error: invalid foreign key",
        ) from exc


@router.get("", response_model=list[StaffRead], summary="List staff")
async def list_staff(
    db: DbDep,
    user: CurrentActiveUser,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Staff]:
    if user.role not in {"admin", "manager", "staff"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")

    stmt = select(Staff).where(Staff.deleted_at.is_(None))
    if user.role == "staff":
        # Staff can only see their own record.
        if user.staff_id is None:
            return []
        stmt = stmt.where(Staff.id == user.staff_id)
    stmt = stmt.order_by(Staff.created_at.desc()).limit(limit).offset(offset)
    rows = (await db.scalars(stmt)).all()
    return list(rows)


@router.get("/{staff_id}", response_model=StaffRead, summary="Get staff by id")
async def get_staff(
    staff_id: UUID,
    db: DbDep,
    user: CurrentActiveUser,
) -> Staff:
    if user.role not in {"admin", "manager", "staff"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")

    if user.role == "staff" and user.staff_id != staff_id:
        # Don't leak existence to non-owners.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    staff = await db.scalar(select(Staff).where(Staff.id == staff_id, Staff.deleted_at.is_(None)))
    if staff is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return staff


@router.post(
    "",
    response_model=StaffRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create staff",
)
async def create_staff(
    payload: StaffCreate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> Staff:
    staff = Staff(**payload.model_dump())
    db.add(staff)
    await _commit_or_409(db)
    await db.refresh(staff)
    return staff


@router.patch("/{staff_id}", response_model=StaffRead, summary="Update staff")
async def update_staff(
    staff_id: UUID,
    payload: StaffUpdate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> Staff:
    staff = await db.scalar(select(Staff).where(Staff.id == staff_id, Staff.deleted_at.is_(None)))
    if staff is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(staff, field, value)
    await _commit_or_409(db)
    await db.refresh(staff)
    return staff


@router.delete(
    "/{staff_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete staff (admin only)",
)
async def delete_staff(
    staff_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> None:
    staff = await db.scalar(select(Staff).where(Staff.id == staff_id, Staff.deleted_at.is_(None)))
    if staff is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    staff.deleted_at = func.now()
    await db.commit()
    return None
