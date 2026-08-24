"""Staff event (研修・イベント) endpoints."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.models.staff import Staff, StaffEvent, StaffEventDefault
from app.models.user import User, normalize_user_role
from app.schemas.staff_events import (
    EventCancelWeekRequest,
    EventCreate,
    EventRead,
    EventUpdate,
)

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
    if normalize_user_role(user.role) not in {"admin", "staff"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
    if user.role == "staff" and user.staff_id != staff_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


async def _ensure_staff_exists(db, staff_id: UUID) -> Staff:
    staff = await db.scalar(select(Staff).where(Staff.id == staff_id, Staff.deleted_at.is_(None)))
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
    summary="List events for a staff (range / search / source / order filters)",
)
async def list_events(
    staff_id: UUID,
    db: DbDep,
    user: CurrentActiveUser,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query(max_length=100)] = None,
    source: Annotated[str | None, Query(max_length=16)] = None,
    event_type: Annotated[str | None, Query(alias="type", max_length=16)] = None,
    order: Annotated[Literal["asc", "desc"], Query()] = "asc",
    hide_regular: Annotated[bool, Query()] = False,
) -> list[StaffEvent]:
    """イベント一覧 (staff-event-history-design.md §2 Phase 1).

    既定の挙動 (パラメータ無し) は従来どおり **starts_at 昇順・全件**。追加の
    絞り込みは全て任意で、後方互換を壊さない。

    - ``q``            : title / note の部分一致 (ILIKE ``%q%``)
    - ``source``       : 'manual' | 'kaipoke' | 'fixed' の完全一致
    - ``type``         : ``event_type`` の完全一致 ('event' | 'training' ほか)
    - ``order``        : starts_at の並び順 ('asc' 既定 / 'desc' = 過去タブの遡り)
    - ``hide_regular`` : 定例 (朝会など) を隠す。判定は **データ駆動** —
      ``source='fixed'`` か、``staff_event_defaults`` に登録済みのタイトルと
      一致する行を除外する。特定タイトルをコードに書かない (PO決定 Q5:
      朝会はデータであってコードではない)。
    """
    _check_read_access(user, staff_id)
    await _ensure_staff_exists(db, staff_id)

    stmt = select(StaffEvent).where(StaffEvent.staff_id == staff_id)
    if date_from is not None:
        stmt = stmt.where(StaffEvent.ends_at >= datetime.combine(date_from, time.min))
    if date_to is not None:
        stmt = stmt.where(StaffEvent.starts_at <= datetime.combine(date_to, time.max))

    if q and q.strip():
        # LIKE ワイルドカード (% / _) は文字として検索させる (レビュー指摘)。
        q_esc = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        needle = f"%{q_esc}%"
        stmt = stmt.where(
            or_(
                StaffEvent.title.ilike(needle, escape="\\"),
                StaffEvent.note.ilike(needle, escape="\\"),
            )
        )
    if source:
        stmt = stmt.where(StaffEvent.source == source)
    if event_type:
        stmt = stmt.where(StaffEvent.event_type == event_type)

    if hide_regular:
        regular_titles = select(StaffEventDefault.title).distinct().scalar_subquery()
        stmt = stmt.where(StaffEvent.source != "fixed")
        stmt = stmt.where(
            or_(
                StaffEvent.title.is_(None),
                StaffEvent.title.not_in(regular_titles),
            )
        )

    ordering = StaffEvent.starts_at.desc() if order == "desc" else StaffEvent.starts_at.asc()
    stmt = stmt.order_by(ordering).offset(offset).limit(limit)
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
    _user: Annotated[User, Depends(require_role("admin"))],
) -> StaffEvent:
    await _ensure_staff_exists(db, staff_id)
    row = StaffEvent(
        staff_id=staff_id,
        event_type=payload.type,  # already normalised to canonical English
        starts_at=_combine(payload.date, payload.start_time),
        ends_at=_combine(payload.date, payload.end_time),
        title=payload.title,
        note=payload.note,
        # 🔒 は作成時にも受け取る (ひな形からの引き継ぎ)。省略時 False。
        blocking=payload.blocking,
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
    _user: Annotated[User, Depends(require_role("admin"))],
) -> StaffEvent:
    """Update event (Wave 39: ``new_staff_id`` で別 staff への付け替えに対応).

    URL の ``staff_id`` は「現在の所有者」, body の ``new_staff_id`` (任意) で
    付け替え先を指定する. 同一トランザクション内で date/start_time/end_time
    と一緒に更新可能. 衝突チェック (= new_staff_id の同時間帯に他 event 重複)
    は本 API では弾かない (FE 側で rollback 判定する W39 案 K の方針と整合).
    """
    row = await db.scalar(
        select(StaffEvent).where(
            StaffEvent.id == event_id,
            StaffEvent.staff_id == staff_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    data = payload.model_dump(exclude_unset=True)

    # Wave 39: new_staff_id が指定されたら付け替え先 staff の存在を確認する.
    new_staff_id = data.pop("new_staff_id", None)
    if new_staff_id is not None and new_staff_id != row.staff_id:
        await _ensure_staff_exists(db, new_staff_id)
        row.staff_id = new_staff_id

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

    # blocking は NOT NULL — 明示 null は「変更なし」として落とす.
    if data.get("blocking", ...) is None:
        data.pop("blocking")

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


@router.post(
    "/{staff_id}/events/{event_id}/cancel-week",
    response_model=EventRead,
    summary="今週の運転席: イベントを今週だけ外す / 戻す (admin)",
)
async def cancel_event_week(
    staff_id: UUID,
    event_id: UUID,
    payload: EventCancelWeekRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> StaffEvent:
    """``staff_events.cancelled_at`` の掛け外し (week-cockpit-design.md §2-3).

    source は不問 (fixed / manual / kaipoke いずれも外せる)。**行は消さない**:
    固定イベントの展開 (``expand_staff_event_defaults``) は冪等キー
    (source='fixed' × external_id) の一致で skip するため、行が残っている限り
    次の週生成でも復活しない。DELETE との違いはここにある。

    取消印が立った行は ``events_outbound.build_outbound_plan`` (送信)・Layer3 の
    重なり/blocking 判定・提案エンジンのイベント収集から外れる。一方
    ``GET /staff/{id}/events`` は cancelled 行も返し続ける (FE が打消線で描く)。

    冪等: 既に同じ状態なら何も変えずに現在の行を返す。
    """
    row = await db.scalar(
        select(StaffEvent).where(
            StaffEvent.id == event_id,
            StaffEvent.staff_id == staff_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if payload.cancel:
        if row.cancelled_at is None:
            row.cancelled_at = datetime.now(UTC)
    else:
        row.cancelled_at = None
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
    _user: Annotated[User, Depends(require_role("admin"))],
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
