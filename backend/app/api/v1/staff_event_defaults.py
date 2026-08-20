"""Staff event-default endpoints (毎週の固定イベント・朝会など).

正典 = docs/plans/kaipoke-event-two-way-design.md §3-②。

    GET    /api/v1/staff/{staff_id}/event-defaults          — admin or 本人
    POST   /api/v1/staff/{staff_id}/event-defaults          — admin
    PATCH  /api/v1/staff/{staff_id}/event-defaults/{id}     — admin
    DELETE /api/v1/staff/{staff_id}/event-defaults/{id}     — admin

定義の変更は「次の週展開から」効く (既に展開済みの週の staff_events は
触らない — 週単位の調整はイベント側で行う)。
"""

from __future__ import annotations

from datetime import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.models.staff import Staff, StaffEventDefault
from app.models.user import User, normalize_user_role
from app.schemas.staff_event_default import (
    EventDefaultCreate,
    EventDefaultRead,
    EventDefaultUpdate,
)

router = APIRouter()


def _check_read_access(user: User, staff_id: UUID) -> None:
    if normalize_user_role(user.role) not in {"admin", "staff"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
    if user.role == "staff" and user.staff_id != staff_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


async def _ensure_staff_exists(db, staff_id: UUID) -> Staff:
    staff = await db.scalar(select(Staff).where(Staff.id == staff_id, Staff.deleted_at.is_(None)))
    if staff is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return staff


def _parse_hhmm(v: str) -> time:
    h, m = v.split(":")
    return time(int(h), int(m))


def _to_read(row: StaffEventDefault) -> EventDefaultRead:
    return EventDefaultRead(
        id=row.id,
        staff_id=row.staff_id,
        weekday=row.weekday,
        weekday_label=EventDefaultRead.weekday_to_label(row.weekday),
        start_time=f"{row.start_time.hour:02d}:{row.start_time.minute:02d}",
        end_time=f"{row.end_time.hour:02d}:{row.end_time.minute:02d}",
        title=row.title,
        blocking=row.blocking,
        note=row.note,
    )


@router.get(
    "/{staff_id}/event-defaults",
    response_model=list[EventDefaultRead],
    summary="List weekly fixed events (admin or the staff themselves)",
)
async def list_event_defaults(
    staff_id: UUID,
    db: DbDep,
    user: CurrentActiveUser,
) -> list[EventDefaultRead]:
    _check_read_access(user, staff_id)
    await _ensure_staff_exists(db, staff_id)
    rows = (
        await db.scalars(
            select(StaffEventDefault)
            .where(StaffEventDefault.staff_id == staff_id)
            .order_by(StaffEventDefault.weekday, StaffEventDefault.start_time)
        )
    ).all()
    return [_to_read(r) for r in rows]


@router.post(
    "/{staff_id}/event-defaults",
    response_model=EventDefaultRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a weekly fixed event (admin)",
)
async def create_event_default(
    staff_id: UUID,
    payload: EventDefaultCreate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> EventDefaultRead:
    await _ensure_staff_exists(db, staff_id)
    row = StaffEventDefault(
        staff_id=staff_id,
        weekday=payload.weekday,
        start_time=_parse_hhmm(payload.start_time),
        end_time=_parse_hhmm(payload.end_time),
        title=payload.title,
        blocking=payload.blocking,
        note=payload.note,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _to_read(row)


@router.patch(
    "/{staff_id}/event-defaults/{default_id}",
    response_model=EventDefaultRead,
    summary="Update a weekly fixed event (admin)",
)
async def update_event_default(
    staff_id: UUID,
    default_id: UUID,
    payload: EventDefaultUpdate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> EventDefaultRead:
    row = await db.scalar(
        select(StaffEventDefault).where(
            StaffEventDefault.id == default_id,
            StaffEventDefault.staff_id == staff_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if payload.weekday is not None:
        row.weekday = payload.weekday
    if payload.start_time is not None:
        row.start_time = _parse_hhmm(payload.start_time)
    if payload.end_time is not None:
        row.end_time = _parse_hhmm(payload.end_time)
    if payload.title is not None:
        row.title = payload.title.strip()
    if payload.blocking is not None:
        row.blocking = payload.blocking
    if payload.note is not None:
        row.note = payload.note or None
    if row.start_time > row.end_time:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="終了時刻は開始時刻以降にしてください",
        )
    await db.commit()
    await db.refresh(row)
    return _to_read(row)


@router.delete(
    "/{staff_id}/event-defaults/{default_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a weekly fixed event (admin)",
)
async def delete_event_default(
    staff_id: UUID,
    default_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> None:
    row = await db.scalar(
        select(StaffEventDefault).where(
            StaffEventDefault.id == default_id,
            StaffEventDefault.staff_id == staff_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await db.delete(row)
    await db.commit()
