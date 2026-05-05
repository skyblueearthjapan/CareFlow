"""Staff event (研修・イベント) endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.models.staff import Staff, StaffEvent
from app.models.user import User
from app.schemas.staff_events import EventCreate, EventRead, EventUpdate

router = APIRouter()


def _check_read_access(user: User, staff_id: UUID) -> None:
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


async def _commit_or_422(db) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Validation error",
        ) from exc


@router.get(
    "/{staff_id}/events",
    response_model=list[EventRead],
    summary="List events for a staff (optional datetime range)",
)
async def list_events(
    staff_id: UUID,
    db: DbDep,
    user: CurrentActiveUser,
    starts_from: Annotated[datetime | None, Query(alias="from")] = None,
    starts_to: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[StaffEvent]:
    _check_read_access(user, staff_id)
    await _ensure_staff_exists(db, staff_id)

    stmt = select(StaffEvent).where(StaffEvent.staff_id == staff_id)
    if starts_from is not None:
        stmt = stmt.where(StaffEvent.ends_at >= starts_from)
    if starts_to is not None:
        stmt = stmt.where(StaffEvent.starts_at <= starts_to)
    stmt = stmt.order_by(StaffEvent.starts_at).limit(limit)
    rows = (await db.scalars(stmt)).all()
    return list(rows)


@router.post(
    "/{staff_id}/events",
    response_model=EventRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an event (admin/manager)",
)
async def create_event(
    staff_id: UUID,
    payload: EventCreate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> StaffEvent:
    await _ensure_staff_exists(db, staff_id)
    row = StaffEvent(staff_id=staff_id, **payload.model_dump())
    db.add(row)
    await _commit_or_422(db)
    await db.refresh(row)
    return row


@router.patch(
    "/{staff_id}/events/{event_id}",
    response_model=EventRead,
    summary="Update an event (admin/manager)",
)
async def update_event(
    staff_id: UUID,
    event_id: UUID,
    payload: EventUpdate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> StaffEvent:
    row = await db.scalar(
        select(StaffEvent).where(
            StaffEvent.id == event_id,
            StaffEvent.staff_id == staff_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(row, k, v)

    if row.starts_at >= row.ends_at:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="starts_at must be < ends_at",
        )
    await _commit_or_422(db)
    await db.refresh(row)
    return row


@router.delete(
    "/{staff_id}/events/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an event (admin/manager)",
)
async def delete_event(
    staff_id: UUID,
    event_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> None:
    row = await db.scalar(
        select(StaffEvent).where(
            StaffEvent.id == event_id,
            StaffEvent.staff_id == staff_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await db.delete(row)
    await db.commit()
    return None
