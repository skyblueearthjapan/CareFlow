"""Staff event-default endpoints (毎週の固定イベント・朝会など).

正典 = docs/plans/kaipoke-event-two-way-design.md §3-②。

    GET    /api/v1/staff/{staff_id}/event-defaults          — admin or 本人
    POST   /api/v1/staff/{staff_id}/event-defaults          — admin
    PATCH  /api/v1/staff/{staff_id}/event-defaults/{id}     — admin
    DELETE /api/v1/staff/{staff_id}/event-defaults/{id}     — admin
    POST   /api/v1/staff-event-defaults/bulk                — admin (一括登録)

定義の変更は「次の週展開から」効く (既に展開済みの週の staff_events は
触らない — 週単位の調整はイベント側で行う)。

一括登録 (``bulk_router``) は staff-event-history-design.md §2 Phase 3。
スタッフ×曜日の全組を 1 TX で作る **汎用** API で、朝会専用の分岐・定数は
持たない (PO Q5: 朝会はデータであってコードではない)。
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
    EventDefaultBulkCreate,
    EventDefaultBulkResult,
    EventDefaultCreate,
    EventDefaultRead,
    EventDefaultUpdate,
)

router = APIRouter()
# /api/v1/staff-event-defaults/* (staff スコープを持たない一括操作用)。
# `router` は prefix="/staff" で登録されるため別ルータに分ける。
bulk_router = APIRouter()


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


@bulk_router.post(
    "/bulk",
    response_model=EventDefaultBulkResult,
    status_code=status.HTTP_200_OK,
    summary="毎週の固定イベントを一括登録 (スタッフ × 曜日の全組・admin)",
)
async def bulk_create_event_defaults(
    payload: EventDefaultBulkCreate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> EventDefaultBulkResult:
    """``staff_ids × weekdays`` の全組を 1 トランザクションで作成する.

    正典 = docs/plans/staff-event-history-design.md §2 Phase 3 /
    docs/mockups/event-defaults-bulk-mock.html 変更A。

    - **全か無か**: 1 名でも存在しない / active でないスタッフが混ざっていたら
      422 で全体を棄却する (半分だけ登録された状態を作らない)。
    - **重複はスキップ**: 同一の (staff, weekday, 開始, 終了, タイトル) の既定が
      既にあれば作らない。何度押しても増えない (プレビューの「既に同じ登録が
      ある分は自動でスキップ」の実装)。
    - 返却は実数 ``{created, skipped}`` (created + skipped = 名数 × 曜日数)。

    朝会などの特定イベント名に対する分岐は持たない (汎用 API)。
    """
    start_time = _parse_hhmm(payload.start_time)
    end_time = _parse_hhmm(payload.end_time)
    title = payload.title.strip()
    note = (payload.note or "").strip() or None

    # ---- スタッフ検証 (存在 + active)。1 件でも欠ければ全体を棄却 ----
    found = {
        s.id: s
        for s in (
            await db.scalars(
                select(Staff).where(
                    Staff.id.in_(payload.staff_ids),
                    Staff.deleted_at.is_(None),
                )
            )
        ).all()
    }
    invalid = [sid for sid in payload.staff_ids if sid not in found]
    inactive = [s.name for sid, s in found.items() if s.status != "active"]
    if invalid or inactive:
        detail = "対象スタッフに登録できない方が含まれています"
        if inactive:
            detail += f"（休職・退職: {'・'.join(sorted(inactive))}さん）"
        if invalid:
            detail += f"（見つからないスタッフ {len(invalid)}名）"
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)

    # ---- 既存の同一内容 (重複スキップの判定材料) を 1 クエリでロード ----
    existing_keys = {
        (r.staff_id, r.weekday, r.start_time, r.end_time, (r.title or "").strip())
        for r in (
            await db.scalars(
                select(StaffEventDefault).where(
                    StaffEventDefault.staff_id.in_(payload.staff_ids),
                    StaffEventDefault.weekday.in_(payload.weekdays),
                )
            )
        ).all()
    }

    created = 0
    skipped = 0
    for staff_id in payload.staff_ids:
        for weekday in payload.weekdays:
            key = (staff_id, weekday, start_time, end_time, title)
            if key in existing_keys:
                skipped += 1
                continue
            db.add(
                StaffEventDefault(
                    staff_id=staff_id,
                    weekday=weekday,
                    start_time=start_time,
                    end_time=end_time,
                    title=title,
                    blocking=payload.blocking,
                    note=note,
                )
            )
            existing_keys.add(key)  # 同一 payload 内の重複も 1 件に畳む
            created += 1

    await db.commit()
    return EventDefaultBulkResult(created=created, skipped=skipped)


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
