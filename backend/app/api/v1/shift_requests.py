"""Shift-request (シフト希望提出) endpoints — Wave 4-D.

Two URL groups share this router:

  * ``/staff/{staff_id}/shift-requests``  — list / create (per staff)
  * ``/shift-requests/{id}/status``       — review (admin/manager)

RBAC:
  * staff role can list/create only on their own ``staff_id``.
  * admin/manager can list/read every staff and update status.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.models.shift_request import ShiftRequest
from app.models.staff import Staff
from app.models.user import User
from app.schemas.shift_request import (
    ShiftRequestCreate,
    ShiftRequestRead,
    ShiftRequestStatusUpdate,
)

# Two routers — one mounted under /staff (sub-resource), one under
# /shift-requests for the cross-staff status update. Both export a
# `router` so the v1 aggregator can include them with the right prefix.
router = APIRouter()
status_router = APIRouter()


def _check_staff_access(user: User, staff_id: UUID) -> None:
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


@router.get(
    "/{staff_id}/shift-requests",
    response_model=list[ShiftRequestRead],
    summary="List shift requests for a staff member",
)
async def list_shift_requests(
    staff_id: UUID,
    db: DbDep,
    user: CurrentActiveUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ShiftRequest]:
    _check_staff_access(user, staff_id)
    await _ensure_staff_exists(db, staff_id)
    stmt = (
        select(ShiftRequest)
        .where(ShiftRequest.staff_id == staff_id)
        .order_by(ShiftRequest.target_month.desc(), ShiftRequest.submitted_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.scalars(stmt)).all()
    return list(rows)


@router.post(
    "/{staff_id}/shift-requests",
    response_model=ShiftRequestRead,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a shift request (staff: self only)",
)
async def create_shift_request(
    staff_id: UUID,
    payload: ShiftRequestCreate,
    db: DbDep,
    user: CurrentActiveUser,
) -> ShiftRequest:
    _check_staff_access(user, staff_id)
    await _ensure_staff_exists(db, staff_id)
    row = ShiftRequest(
        staff_id=staff_id,
        target_month=payload.target_month,
        content=payload.content,
        submitted_at=datetime.now(timezone.utc),
        status="submitted",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@status_router.patch(
    "/{request_id}/status",
    response_model=ShiftRequestRead,
    summary="Update shift-request status (admin/manager)",
)
async def update_shift_request_status(
    request_id: UUID,
    payload: ShiftRequestStatusUpdate,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> ShiftRequest:
    row = await db.scalar(
        select(ShiftRequest).where(ShiftRequest.id == request_id)
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    row.status = payload.status
    if payload.status == "reviewed":
        row.reviewed_by = user.id
        row.reviewed_at = datetime.now(timezone.utc)
    else:
        # Re-opening to "submitted" clears the review trail.
        row.reviewed_by = None
        row.reviewed_at = None
    await db.commit()
    await db.refresh(row)
    return row
