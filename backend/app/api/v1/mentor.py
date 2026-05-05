"""Mentor assignment endpoints (シンプル版).

Backed by `Staff.mentor_id` (self-referential FK). The richer historical
`mentor_assignments` table is preserved untouched for future use.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.models.staff import Staff
from app.models.user import User
from app.schemas.mentor import MentorAssign, MentorRead

router = APIRouter()


def _check_read_access(user: User, staff_id: UUID) -> None:
    if user.role not in {"admin", "manager", "staff"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role"
        )
    if user.role == "staff" and user.staff_id != staff_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


async def _get_staff_or_404(db, staff_id: UUID) -> Staff:
    staff = await db.scalar(
        select(Staff).where(Staff.id == staff_id, Staff.deleted_at.is_(None))
    )
    if staff is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return staff


def _to_read(staff: Staff, mentor: Staff | None) -> MentorRead:
    return MentorRead(
        staff_id=staff.id,
        mentor_staff_id=mentor.id if mentor is not None else None,
        mentor_staff_name=mentor.name if mentor is not None else None,
    )


@router.get(
    "/{staff_id}/mentor",
    response_model=MentorRead,
    summary="Get the staff's current mentor pointer",
)
async def get_mentor(
    staff_id: UUID,
    db: DbDep,
    user: CurrentActiveUser,
) -> MentorRead:
    _check_read_access(user, staff_id)
    staff = await _get_staff_or_404(db, staff_id)

    mentor: Staff | None = None
    if staff.mentor_id is not None:
        mentor = await db.scalar(
            select(Staff).where(
                Staff.id == staff.mentor_id, Staff.deleted_at.is_(None)
            )
        )
    return _to_read(staff, mentor)


@router.put(
    "/{staff_id}/mentor",
    response_model=MentorRead,
    summary="Set / clear the staff's mentor pointer (admin/manager)",
)
async def set_mentor(
    staff_id: UUID,
    payload: MentorAssign,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> MentorRead:
    staff = await _get_staff_or_404(db, staff_id)

    new_mentor: Staff | None = None
    if payload.mentor_staff_id is not None:
        if payload.mentor_staff_id == staff_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A staff member cannot mentor themselves",
            )
        new_mentor = await db.scalar(
            select(Staff).where(
                Staff.id == payload.mentor_staff_id,
                Staff.deleted_at.is_(None),
            )
        )
        if new_mentor is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="mentor_staff_id does not exist",
            )

    staff.mentor_id = payload.mentor_staff_id
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Validation error",
        ) from exc
    await db.refresh(staff)
    return _to_read(staff, new_mentor)
