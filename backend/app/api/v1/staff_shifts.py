"""Staff weekly fixed-shift endpoints.

GET  /api/v1/staff/{staff_id}/shifts  → 7 weekday rows (always returns 7,
backfilling missing weekdays as is_on=True / no times).
PUT  /api/v1/staff/{staff_id}/shifts  → bulk replace (transactional).
"""

from __future__ import annotations

from datetime import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select

from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.models.staff import Staff, StaffShift
from app.models.user import User
from app.schemas.staff_shifts import (
    ShiftsBulkUpdate,
    ShiftsResponse,
    StaffShiftItem,
)

router = APIRouter()


def _check_read_access(user: User, staff_id: UUID) -> None:
    """Mirror the `staff.py` IDOR pattern — staff role sees only its own row."""
    if user.role not in {"admin", "manager", "staff"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role"
        )
    if user.role == "staff" and user.staff_id != staff_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


async def _ensure_staff_exists(db, staff_id: UUID) -> Staff:
    staff = await db.scalar(
        select(Staff).where(Staff.id == staff_id, Staff.deleted_at.is_(None))
    )
    if staff is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return staff


def _row_to_item(row: StaffShift) -> StaffShiftItem:
    return StaffShiftItem(
        weekday=row.weekday,
        is_on=row.is_on,
        start_time=row.start_time.strftime("%H:%M") if row.start_time else None,
        end_time=row.end_time.strftime("%H:%M") if row.end_time else None,
    )


def _parse_hhmm(value: str | None) -> time | None:
    if value is None:
        return None
    h, m = value.split(":")
    return time(int(h), int(m))


@router.get(
    "/{staff_id}/shifts",
    response_model=ShiftsResponse,
    summary="Get the staff's fixed weekly shift (7 weekdays)",
)
async def get_shifts(
    staff_id: UUID,
    db: DbDep,
    user: CurrentActiveUser,
) -> ShiftsResponse:
    _check_read_access(user, staff_id)
    await _ensure_staff_exists(db, staff_id)

    rows = (
        await db.scalars(
            select(StaffShift)
            .where(StaffShift.staff_id == staff_id)
            .order_by(StaffShift.weekday)
        )
    ).all()
    by_wd = {r.weekday: _row_to_item(r) for r in rows}
    # Backfill missing weekdays so frontend always receives 7 entries.
    full = [
        by_wd.get(wd, StaffShiftItem(weekday=wd, is_on=True))
        for wd in range(7)
    ]
    return ShiftsResponse(shifts=full)


@router.put(
    "/{staff_id}/shifts",
    response_model=ShiftsResponse,
    summary="Bulk replace the staff's fixed weekly shift (admin/manager)",
)
async def put_shifts(
    staff_id: UUID,
    payload: ShiftsBulkUpdate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> ShiftsResponse:
    await _ensure_staff_exists(db, staff_id)

    # Replace atomically: delete + insert in one transaction.
    await db.execute(delete(StaffShift).where(StaffShift.staff_id == staff_id))
    for item in payload.shifts:
        db.add(
            StaffShift(
                staff_id=staff_id,
                weekday=item.weekday,
                is_on=item.is_on,
                start_time=_parse_hhmm(item.start_time),
                end_time=_parse_hhmm(item.end_time),
            )
        )
    await db.commit()

    rows = (
        await db.scalars(
            select(StaffShift)
            .where(StaffShift.staff_id == staff_id)
            .order_by(StaffShift.weekday)
        )
    ).all()
    return ShiftsResponse(shifts=[_row_to_item(r) for r in rows])
