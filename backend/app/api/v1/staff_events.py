"""Staff event (研修・イベント) endpoints."""

from __future__ import annotations

from datetime import date, datetime, time
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


def _combine(d: date, hhmm: str) -> datetime:
    """Combine YYYY-MM-DD + HH:MM into a naive datetime (UTC-anchored).

    The DB column is `DateTime(timezone=True)` but we have no tz from the
    Frontend; treating the value as naive matches what the rest of the
    backend does for wall-clock-only inputs.
    """
    h, m = hhmm.split(":")[:2]
    return datetime.combine(d, time(int(h), int(m)))


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
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[StaffEvent]:
    _check_read_access(user, staff_id)
    await _ensure_staff_exists(db, staff_id)

    stmt = select(StaffEvent).where(StaffEvent.staff_id == staff_id)
    if date_from is not None:
        stmt = stmt.where(StaffEvent.ends_at >= datetime.combine(date_from, time.min))
    if date_to is not None:
        stmt = stmt.where(StaffEvent.starts_at <= datetime.combine(date_to, time.max))
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
    row = StaffEvent(
        staff_id=staff_id,
        event_type=payload.type,  # already normalised to canonical English
        starts_at=_combine(payload.date, payload.start_time),
        ends_at=_combine(payload.date, payload.end_time),
        title=payload.title,
        note=payload.note,
    )
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

    data = payload.model_dump(exclude_unset=True)

    # Compute new wall-clock anchor (date + times) when any of those three
    # fields is supplied. We re-derive both starts_at/ends_at consistently
    # so partial updates can not leave the row in a torn state.
    if any(k in data for k in ("date", "start_time", "end_time")):
        # Ignore explicit `None` values for these (treated as "no change").
        new_date = data.pop("date", None) or row.starts_at.date()
        cur_start_hhmm = row.starts_at.strftime("%H:%M")
        cur_end_hhmm = row.ends_at.strftime("%H:%M")
        new_start = data.pop("start_time", None) or cur_start_hhmm
        new_end = data.pop("end_time", None) or cur_end_hhmm
        row.starts_at = _combine(new_date, new_start)
        row.ends_at = _combine(new_date, new_end)
    else:
        # Drop any explicit-None entries so we don't accidentally null the
        # column.
        for k in ("date", "start_time", "end_time"):
            data.pop(k, None)

    if "type" in data:
        row.event_type = data.pop("type")

    for k, v in data.items():
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
