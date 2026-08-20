"""Staff weekly-override endpoints (今後の休み・時間変更 セクション).

The model keys overrides by (staff_id, iso_year, iso_week, weekday). The
HTTP API exposes a friendlier `date` field which is converted to the iso
triple on write and back to a date on read.
"""

from __future__ import annotations

from datetime import date, time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError

from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.models.staff import Staff, StaffWeeklyOverride
from app.models.user import User, normalize_user_role
from app.schemas.staff_overrides import (
    OverrideCreate,
    OverrideRead,
    OverrideUpdate,
    WeekOverrideRead,
    _db_type_to_label,
)

router = APIRouter()


@router.get(
    "/overrides-week",
    response_model=list[WeekOverrideRead],
    summary="1 週の全スタッフ休み/時間変更を一覧 (週空間 A1: 職員スケジュール盤面用)",
)
async def list_week_overrides(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    iso_year: Annotated[int, Query(ge=2000, le=2100)],
    iso_week: Annotated[int, Query(ge=1, le=53)],
) -> list[WeekOverrideRead]:
    """職員スケジュール盤面 (staff×weekday) にセル単位で引ける形で返す.

    weekly-space-design.md §4-2: コースを貼るスタッフのその日が休みかどうかを
    盤面表示 + ドロップ時警告に使う (警告のみ・ブロックしない)。
    RBAC: admin のみ (他人の休みの横断一覧のため)。閲覧側 FE は canEdit の
    ときだけ取得する。
    """
    try:
        date.fromisocalendar(iso_year, iso_week, 1)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid ISO week: year={iso_year} week={iso_week}",
        ) from exc

    rows = (
        await db.scalars(
            select(StaffWeeklyOverride)
            .join(Staff, Staff.id == StaffWeeklyOverride.staff_id)
            .where(
                StaffWeeklyOverride.iso_year == iso_year,
                StaffWeeklyOverride.iso_week == iso_week,
                Staff.deleted_at.is_(None),
            )
            .order_by(StaffWeeklyOverride.weekday)
        )
    ).all()

    out: list[WeekOverrideRead] = []
    for row in rows:
        out.append(
            WeekOverrideRead(
                id=row.id,
                staff_id=row.staff_id,
                date=date.fromisocalendar(row.iso_year, row.iso_week, row.weekday + 1),
                weekday=row.weekday,
                type=_db_type_to_label(row.override_type),  # type: ignore[arg-type]
                start_time=row.start_time.strftime("%H:%M") if row.start_time else None,
                end_time=row.end_time.strftime("%H:%M") if row.end_time else None,
                note=row.reason,
            )
        )
    return out


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


def _parse_hhmm(value: str | None) -> time | None:
    if value is None:
        return None
    h, m = value.split(":")
    return time(int(h), int(m))


def _date_to_iso(d: date) -> tuple[int, int, int]:
    iso = d.isocalendar()
    return iso.year, iso.week, d.weekday()


async def _commit_or_409(db) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        msg = str(exc).lower()
        if "unique" in msg or "duplicate" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Conflict: override already exists for that date",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Validation error",
        ) from exc


@router.get(
    "/{staff_id}/overrides",
    response_model=list[OverrideRead],
    summary="List per-week overrides in a date range",
)
async def list_overrides(
    staff_id: UUID,
    db: DbDep,
    user: CurrentActiveUser,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[StaffWeeklyOverride]:
    _check_read_access(user, staff_id)
    await _ensure_staff_exists(db, staff_id)

    stmt = select(StaffWeeklyOverride).where(StaffWeeklyOverride.staff_id == staff_id)

    if date_from is not None:
        y, w, _ = _date_to_iso(date_from)
        stmt = stmt.where(
            or_(
                StaffWeeklyOverride.iso_year > y,
                and_(
                    StaffWeeklyOverride.iso_year == y,
                    StaffWeeklyOverride.iso_week >= w,
                ),
            )
        )
    if date_to is not None:
        y, w, _ = _date_to_iso(date_to)
        stmt = stmt.where(
            or_(
                StaffWeeklyOverride.iso_year < y,
                and_(
                    StaffWeeklyOverride.iso_year == y,
                    StaffWeeklyOverride.iso_week <= w,
                ),
            )
        )

    stmt = stmt.order_by(
        StaffWeeklyOverride.iso_year,
        StaffWeeklyOverride.iso_week,
        StaffWeeklyOverride.weekday,
    ).limit(limit)
    rows = (await db.scalars(stmt)).all()
    return list(rows)


@router.post(
    "/{staff_id}/overrides",
    response_model=OverrideRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add an override for a single date (admin/manager)",
)
async def create_override(
    staff_id: UUID,
    payload: OverrideCreate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> StaffWeeklyOverride:
    await _ensure_staff_exists(db, staff_id)
    iso_year, iso_week, weekday = _date_to_iso(payload.date)

    row = StaffWeeklyOverride(
        staff_id=staff_id,
        iso_year=iso_year,
        iso_week=iso_week,
        weekday=weekday,
        override_type=payload.type,  # already normalised to DB code
        start_time=_parse_hhmm(payload.start_time),
        end_time=_parse_hhmm(payload.end_time),
        reason=payload.note,
    )
    db.add(row)
    await _commit_or_409(db)
    await db.refresh(row)
    return row


@router.patch(
    "/{staff_id}/overrides/{override_id}",
    response_model=OverrideRead,
    summary="Update an existing override (admin/manager)",
)
async def update_override(
    staff_id: UUID,
    override_id: UUID,
    payload: OverrideUpdate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> StaffWeeklyOverride:
    row = await db.scalar(
        select(StaffWeeklyOverride).where(
            StaffWeeklyOverride.id == override_id,
            StaffWeeklyOverride.staff_id == staff_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    data = payload.model_dump(exclude_unset=True)
    if "date" in data and data["date"] is not None:
        iso_year, iso_week, weekday = _date_to_iso(data.pop("date"))
        row.iso_year = iso_year
        row.iso_week = iso_week
        row.weekday = weekday
    elif "date" in data:
        data.pop("date")  # explicit None — ignored

    if "start_time" in data:
        row.start_time = _parse_hhmm(data.pop("start_time"))
    if "end_time" in data:
        row.end_time = _parse_hhmm(data.pop("end_time"))

    # Frontend → DB column name mapping (the schema validator already
    # normalised `type` to its canonical English code).
    _api_to_db = {"type": "override_type", "note": "reason"}
    for k, v in data.items():
        setattr(row, _api_to_db.get(k, k), v)

    # Cross-field validation after merge.
    if row.start_time is not None and row.end_time is not None and row.start_time >= row.end_time:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_time must be < end_time",
        )

    await _commit_or_409(db)
    await db.refresh(row)
    return row


@router.delete(
    "/{staff_id}/overrides/{override_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an override (admin/manager)",
)
async def delete_override(
    staff_id: UUID,
    override_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> None:
    row = await db.scalar(
        select(StaffWeeklyOverride).where(
            StaffWeeklyOverride.id == override_id,
            StaffWeeklyOverride.staff_id == staff_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    # スタッフ本人へ取消通知 (staff-shift-confirmation-design.md §2-b・同一 TX)。
    # 削除前に ISO 三つ組から日付を逆算する (_date_to_iso の逆変換)。
    from app.services.leave_notify import notify_leave_cancelled

    target = date.fromisocalendar(row.iso_year, row.iso_week, row.weekday + 1)
    await notify_leave_cancelled(
        db,
        staff_id=row.staff_id,
        target=target,
        type_label=_db_type_to_label(row.override_type),
    )

    await db.delete(row)
    await db.commit()
    return None
