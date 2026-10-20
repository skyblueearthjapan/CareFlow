"""Staff weekly-override endpoints (今後の休み・時間変更 セクション).

The model keys overrides by (staff_id, iso_year, iso_week, weekday). The
HTTP API exposes a friendlier `date` field which is converted to the iso
triple on write and back to a date on read.
"""

from __future__ import annotations

import uuid
from datetime import date, time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError

from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.core.leave import LEAVE_TYPES
from app.models.staff import Staff, StaffWeeklyOverride
from app.models.user import User, normalize_user_role
from app.schemas.staff_overrides import (
    OverrideCreate,
    OverrideRead,
    OverrideUpdate,
    WeekOverrideRead,
    _db_type_to_label,
)
from app.services.leave_notify import notify_leave_cancelled
from app.services.op_log_service import fmt_time, set_staff_override
from app.services.staff_leave import (
    LEAVE_WRITE_CONFLICT_DETAIL,
    StaffLeaveError,
    apply_staff_leave,
    cancel_staff_leave,
    write_custom_time,
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
                detail=LEAVE_WRITE_CONFLICT_DETAIL,
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


def _leave_error(exc: StaffLeaveError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


async def _load_row(db, staff_id: UUID, override_id: UUID) -> StaffWeeklyOverride:
    row = await db.scalar(
        select(StaffWeeklyOverride).where(
            StaffWeeklyOverride.id == override_id,
            StaffWeeklyOverride.staff_id == staff_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return row


async def _row_on(db, staff_id: UUID, d: date) -> StaffWeeklyOverride | None:
    iso_year, iso_week, weekday = _date_to_iso(d)
    return await db.scalar(
        select(StaffWeeklyOverride).where(
            StaffWeeklyOverride.staff_id == staff_id,
            StaffWeeklyOverride.iso_year == iso_year,
            StaffWeeklyOverride.iso_week == iso_week,
            StaffWeeklyOverride.weekday == weekday,
        )
    )


async def _write_day(
    db,
    *,
    staff_id: UUID,
    d: date,
    override_type: str,
    start_time: str | None,
    end_time: str | None,
    note: str | None,
    actor: User,
    source: str | None = None,
    pending_request_id: UUID | None = None,
    op_group_id: UUID | None = None,
) -> None:
    """1 日ぶんを書く: 休みは共通の処理 (訪問を担当なしへ・休み設計 §3-1)、時間変更は行だけ."""
    if override_type in LEAVE_TYPES:
        try:
            await apply_staff_leave(
                db,
                staff_id=staff_id,
                day=d,
                leave_type=override_type,
                source=source or "admin",
                note=note,
                actor=actor,
                op_group_id=op_group_id,
                origin="staff_detail",
                pending_request_id=pending_request_id,
            )
        except StaffLeaveError as exc:
            await db.rollback()
            raise _leave_error(exc) from exc
        except IntegrityError as exc:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=LEAVE_WRITE_CONFLICT_DETAIL
            ) from exc
        return
    try:
        # 時間変更も set_staff_off として同じ op_group に記録する (休み→時間変更の「戻る」)。
        await write_custom_time(
            db,
            staff_id=staff_id,
            day=d,
            start_time=start_time,
            end_time=end_time,
            reason=note,
            actor=actor,
            op_group_id=op_group_id,
            origin="staff_detail",
            source=source,
            pending_request_id=pending_request_id,
        )
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=LEAVE_WRITE_CONFLICT_DETAIL
        ) from exc


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
    user: Annotated[User, Depends(require_role("admin"))],
) -> StaffWeeklyOverride:
    """1 日ぶんの休み・時間変更を入れる.

    休みの種類 (休み・午前休・午後休・有給) は共通の処理 ``apply_staff_leave`` で入れる
    (今日以降なら休みの時間帯の本人の訪問を担当なしへ移す)。時間変更は行だけを書く。
    同じ日に登録があれば 409 (今までどおり・種類を変えるのは PATCH)。
    """
    await _ensure_staff_exists(db, staff_id)
    if await _row_on(db, staff_id, payload.date) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conflict: override already exists for that date",
        )
    await _write_day(
        db,
        staff_id=staff_id,
        d=payload.date,
        override_type=payload.type,  # already normalised to DB code
        start_time=payload.start_time,
        end_time=payload.end_time,
        note=payload.note,
        actor=user,
    )
    row = await _row_on(db, staff_id, payload.date)
    await _commit_or_409(db)
    if row is None:  # pragma: no cover (直前に書いた行)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await db.refresh(row)
    return row


def _hhmm_or_none(value: time | None) -> str | None:
    return fmt_time(value) if value is not None else None


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
    user: Annotated[User, Depends(require_role("admin"))],
) -> StaffWeeklyOverride:
    """休み・時間変更を変える (日付・種類・時刻・メモ).

    休みの種類へ変える・休みのまま日付を変えると共通の処理を通る。日付を変えたときは
    元の日の行を消してから新しい日に入れる (新しい日に登録があれば 409)。
    種類が変わらなければ ``source``・``pending_request_id`` を残す。
    """
    row = await _load_row(db, staff_id, override_id)
    data = payload.model_dump(exclude_unset=True)
    old_date = date.fromisocalendar(row.iso_year, row.iso_week, row.weekday + 1)
    new_date = data.get("date") or old_date
    new_type = data.get("type") or row.override_type
    new_start = data["start_time"] if "start_time" in data else _hhmm_or_none(row.start_time)
    new_end = data["end_time"] if "end_time" in data else _hhmm_or_none(row.end_time)
    new_note = data["note"] if "note" in data else row.reason
    if new_type in LEAVE_TYPES:
        new_start = new_end = None
    if new_start is not None and new_end is not None and new_start >= new_end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_time must be < end_time",
        )
    if new_type == "custom_time" and (new_start is None or new_end is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="custom_time override requires start_time and end_time",
        )

    # 種類と日付が同じ (メモ・時刻だけ違う) なら、誰が入れたか・承認元の申請を残す。
    same_slot = new_type == row.override_type and new_date == old_date
    keep_source = row.source if same_slot else None
    keep_request = row.pending_request_id if same_slot else None
    group = uuid.uuid4()
    if new_date != old_date and await _row_on(db, staff_id, new_date) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conflict: override already exists for that date",
        )
    if row.override_type in LEAVE_TYPES and (new_date != old_date or new_type not in LEAVE_TYPES):
        # 元が休み: 休み→時間変更・日付の移動は、先に休みを取り消す (同じ op_group)。
        try:
            await cancel_staff_leave(
                db,
                staff_id=staff_id,
                day=old_date,
                actor=user,
                op_group_id=group,
                origin="staff_detail",
                notify=False,
            )
        except StaffLeaveError as exc:
            await db.rollback()
            raise _leave_error(exc) from exc
    elif new_date != old_date:
        await set_staff_override(
            db,
            staff_id=staff_id,
            iso_year=row.iso_year,
            iso_week=row.iso_week,
            weekday=row.weekday,
            override_type=None,
        )
    await _write_day(
        db,
        staff_id=staff_id,
        d=new_date,
        override_type=new_type,
        start_time=new_start,
        end_time=new_end,
        note=new_note,
        actor=user,
        source=keep_source,
        pending_request_id=keep_request,
        op_group_id=group,
    )
    out = await _row_on(db, staff_id, new_date)
    await _commit_or_409(db)
    if out is None:  # pragma: no cover (直前に書いた行)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await db.refresh(out)
    return out


@router.delete(
    "/{staff_id}/overrides/{override_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an override (admin/manager)",
)
async def delete_override(
    staff_id: UUID,
    override_id: UUID,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin"))],
) -> None:
    """休み・時間変更を消す。スタッフ本人へ取消通知 (同一 TX).

    休みの種類は共通の処理 ``cancel_staff_leave`` (担当なしへ移した訪問は戻さない)。
    """
    row = await _load_row(db, staff_id, override_id)
    target = date.fromisocalendar(row.iso_year, row.iso_week, row.weekday + 1)
    if row.override_type in LEAVE_TYPES:
        try:
            await cancel_staff_leave(
                db,
                staff_id=staff_id,
                day=target,
                actor=user,
                op_group_id=None,
                origin="staff_detail",
            )
        except StaffLeaveError as exc:
            await db.rollback()
            raise _leave_error(exc) from exc
        await db.commit()
        return None

    await notify_leave_cancelled(
        db,
        staff_id=row.staff_id,
        target=target,
        type_label=_db_type_to_label(row.override_type),
    )
    await set_staff_override(
        db,
        staff_id=staff_id,
        iso_year=row.iso_year,
        iso_week=row.iso_week,
        weekday=row.weekday,
        override_type=None,
    )
    await db.commit()
    return None
