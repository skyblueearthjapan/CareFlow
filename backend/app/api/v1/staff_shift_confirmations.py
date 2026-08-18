"""Staff shift confirmation endpoints (月次出勤カレンダー確定).

正典 = docs/plans/staff-shift-confirmation-design.md §2-a。

    GET  /api/v1/staff/{staff_id}/shift-confirmations?from=&to=  — admin or 本人
    POST /api/v1/staff/{staff_id}/shift-confirmations            — admin のみ

POST は upsert (同一 staff×month は confirmed_at/confirmed_by を更新) +
スタッフ本人への確定通知 (再確定 = 再通知) を同一 TX で行う。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.models.staff import Staff, StaffShiftConfirmation
from app.models.user import User, normalize_user_role
from app.schemas.staff_shift_confirmation import (
    ShiftConfirmationCreate,
    ShiftConfirmationRead,
)
from app.services.leave_notify import notify_shift_confirmed

router = APIRouter()


def _check_read_access(user: User, staff_id: UUID) -> None:
    """admin は全員、staff は本人のみ (staff_overrides と同じ流儀)."""
    if normalize_user_role(user.role) not in {"admin", "staff"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
    if user.role == "staff" and user.staff_id != staff_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


async def _ensure_staff_exists(db, staff_id: UUID) -> Staff:
    staff = await db.scalar(select(Staff).where(Staff.id == staff_id, Staff.deleted_at.is_(None)))
    if staff is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return staff


@router.get(
    "/{staff_id}/shift-confirmations",
    response_model=list[ShiftConfirmationRead],
    summary="List monthly shift confirmations (admin or the staff themselves)",
)
async def list_shift_confirmations(
    staff_id: UUID,
    db: DbDep,
    user: CurrentActiveUser,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: Annotated[date | None, Query()] = None,
) -> list[ShiftConfirmationRead]:
    _check_read_access(user, staff_id)
    await _ensure_staff_exists(db, staff_id)

    stmt = select(StaffShiftConfirmation).where(StaffShiftConfirmation.staff_id == staff_id)
    if from_ is not None:
        stmt = stmt.where(StaffShiftConfirmation.month >= from_.replace(day=1))
    if to is not None:
        stmt = stmt.where(StaffShiftConfirmation.month <= to)
    stmt = stmt.order_by(StaffShiftConfirmation.month)
    rows = (await db.scalars(stmt)).all()
    return [ShiftConfirmationRead.model_validate(r, from_attributes=True) for r in rows]


@router.post(
    "/{staff_id}/shift-confirmations",
    response_model=ShiftConfirmationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Confirm a month's shift calendar and notify the staff (admin)",
)
async def confirm_shift_month(
    staff_id: UUID,
    payload: ShiftConfirmationCreate,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin"))],
) -> ShiftConfirmationRead:
    """月次確定の upsert + 本人通知 (同一 TX)。再確定 = 更新 + 再通知。"""
    await _ensure_staff_exists(db, staff_id)

    now = datetime.now(UTC)
    row = await db.scalar(
        select(StaffShiftConfirmation).where(
            StaffShiftConfirmation.staff_id == staff_id,
            StaffShiftConfirmation.month == payload.month,
        )
    )
    if row is None:
        row = StaffShiftConfirmation(
            staff_id=staff_id,
            month=payload.month,
            confirmed_by=user.id,
            confirmed_at=now,
        )
        db.add(row)
    else:
        row.confirmed_by = user.id
        row.confirmed_at = now

    await notify_shift_confirmed(db, staff_id=staff_id, month=payload.month)

    await db.commit()
    await db.refresh(row)
    return ShiftConfirmationRead.model_validate(row, from_attributes=True)
