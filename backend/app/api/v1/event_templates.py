"""イベントひな形 (event_templates) endpoints — Phase 2.

正典 = docs/plans/staff-event-history-design.md §2 Phase 2。

    GET    /api/v1/event-templates                      — 全ロール (閲覧)
    GET    /api/v1/event-templates/history-suggestions   — 全ロール (閲覧)
    POST   /api/v1/event-templates                      — admin
    PATCH  /api/v1/event-templates/{template_id}        — admin
    DELETE /api/v1/event-templates/{template_id}        — admin (物理削除)
    PUT    /api/v1/event-templates/reorder              — admin

スコープは 2 つだけ: **共通** (staff_id IS NULL) と **個人** (staff_id = そのスタッフ)。
一覧は「共通は常に返す + staff_id 指定時はその個人ぶんも返す」フラット配列で、
FE は ``is_shared`` でセクション分けする。

設計原則 (PO 2026-08-24): 朝会などの定例をコードで個別定義しない。
history-suggestions の「定例除外」は ``staff_event_defaults`` のタイトル
(テーブル駆動) と source='fixed' で判定する — タイトルのハードコードは禁止。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select

from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.models.staff import EventTemplate, Staff, StaffEvent, StaffEventDefault
from app.models.user import User
from app.schemas.event_templates import (
    EventTemplateCreate,
    EventTemplateRead,
    EventTemplateUpdate,
    HistorySuggestionItem,
    ReorderRequest,
    format_hhmm,
    parse_hhmm,
)

router = APIRouter()

HISTORY_SUGGESTION_LIMIT = 30


def _to_read(row: EventTemplate) -> EventTemplateRead:
    return EventTemplateRead(
        id=row.id,
        staff_id=row.staff_id,
        title=row.title,
        event_type=row.event_type,
        start_time=format_hhmm(row.start_time),
        end_time=format_hhmm(row.end_time),
        blocking=row.blocking,
        note=row.note,
        sort_order=row.sort_order,
        is_active=row.is_active,
        is_shared=row.staff_id is None,
    )


async def _ensure_staff_exists(db, staff_id: UUID) -> None:
    staff = await db.scalar(select(Staff).where(Staff.id == staff_id, Staff.deleted_at.is_(None)))
    if staff is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _scope_clause(staff_id: UUID | None):
    """スコープ 1 つぶんの where 句 (共通 or 指定スタッフ個人)."""
    if staff_id is None:
        return EventTemplate.staff_id.is_(None)
    return EventTemplate.staff_id == staff_id


def _months_ago(today: date, months: int) -> date:
    """today から months ヶ月前の日付 (日は月末クランプ)."""
    total = today.year * 12 + (today.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    # 月末クランプ (3/31 の 1 ヶ月前 = 2/28 など)。
    day = today.day
    while day > 1:
        try:
            return date(year, month, day)
        except ValueError:
            day -= 1
    return date(year, month, 1)


# ---------------------------------------------------------------------------
# 読み取り (全ロール)
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=list[EventTemplateRead],
    summary="List event templates (shared + optional per-staff)",
)
async def list_event_templates(
    db: DbDep,
    _user: CurrentActiveUser,
    staff_id: Annotated[UUID | None, Query(description="指定すると個人ひな形も含める")] = None,
    include_inactive: Annotated[bool, Query(description="無効化したひな形も含める")] = False,
) -> list[EventTemplateRead]:
    stmt = select(EventTemplate)
    if staff_id is None:
        stmt = stmt.where(EventTemplate.staff_id.is_(None))
    else:
        stmt = stmt.where(
            (EventTemplate.staff_id.is_(None)) | (EventTemplate.staff_id == staff_id)
        )
    if not include_inactive:
        stmt = stmt.where(EventTemplate.is_active.is_(True))
    stmt = stmt.order_by(EventTemplate.sort_order, EventTemplate.created_at)
    rows = (await db.scalars(stmt)).all()
    return [_to_read(r) for r in rows]


@router.get(
    "/history-suggestions",
    response_model=list[HistorySuggestionItem],
    summary="Suggest templates from past staff events (aggregated by title)",
)
async def history_suggestions(
    db: DbDep,
    _user: CurrentActiveUser,
    staff_id: Annotated[UUID | None, Query(description="指定するとそのスタッフの履歴のみ")] = None,
    months: Annotated[int, Query(ge=1, le=36, description="遡る月数")] = 6,
) -> list[HistorySuggestionItem]:
    cutoff = datetime.combine(_months_ago(date.today(), months), datetime.min.time())

    # 除外 (b): 定例イベントのタイトル群 (テーブル駆動・ハードコード禁止)。
    default_titles = {
        (t or "").strip()
        for t in (await db.scalars(select(StaffEventDefault.title).distinct())).all()
        if (t or "").strip()
    }
    # 除外 (c): 同スコープに既にあるひな形のタイトル (無効化ぶんも含めて重複を防ぐ)。
    existing_stmt = select(EventTemplate.title)
    if staff_id is None:
        existing_stmt = existing_stmt.where(EventTemplate.staff_id.is_(None))
    else:
        existing_stmt = existing_stmt.where(
            (EventTemplate.staff_id.is_(None)) | (EventTemplate.staff_id == staff_id)
        )
    existing_titles = {
        (t or "").strip() for t in (await db.scalars(existing_stmt)).all() if (t or "").strip()
    }

    stmt = select(
        StaffEvent.title,
        StaffEvent.starts_at,
        StaffEvent.ends_at,
        StaffEvent.event_type,
    ).where(
        StaffEvent.starts_at >= cutoff,
        StaffEvent.title.is_not(None),
        StaffEvent.source != "fixed",  # 除外 (a)
    )
    if staff_id is not None:
        stmt = stmt.where(StaffEvent.staff_id == staff_id)

    agg: dict[str, dict] = {}
    for title, starts_at, ends_at, event_type in (await db.execute(stmt)).all():
        name = (title or "").strip()
        if not name or name in default_titles or name in existing_titles:
            continue
        entry = agg.get(name)
        if entry is None:
            agg[name] = {
                "count": 1,
                "last_at": starts_at,
                "last_start": starts_at,
                "last_end": ends_at,
                "event_type": event_type,
            }
            continue
        entry["count"] += 1
        if starts_at > entry["last_at"]:
            entry["last_at"] = starts_at
            entry["last_start"] = starts_at
            entry["last_end"] = ends_at
            entry["event_type"] = event_type

    ordered = sorted(agg.items(), key=lambda kv: (-kv[1]["count"], kv[0]))
    return [
        HistorySuggestionItem(
            title=name,
            count=e["count"],
            last_date=e["last_at"].date(),
            last_start_time=format_hhmm(e["last_start"].time()),
            last_end_time=format_hhmm(e["last_end"].time()) if e["last_end"] else None,
            event_type=e["event_type"],
        )
        for name, e in ordered[:HISTORY_SUGGESTION_LIMIT]
    ]


# ---------------------------------------------------------------------------
# 書き込み (admin)
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=EventTemplateRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an event template (admin)",
)
async def create_event_template(
    payload: EventTemplateCreate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> EventTemplateRead:
    if payload.staff_id is not None:
        await _ensure_staff_exists(db, payload.staff_id)

    sort_order = payload.sort_order
    if sort_order is None:
        current_max = await db.scalar(
            select(func.max(EventTemplate.sort_order)).where(_scope_clause(payload.staff_id))
        )
        sort_order = 0 if current_max is None else int(current_max) + 1

    row = EventTemplate(
        staff_id=payload.staff_id,
        title=payload.title,
        event_type=payload.event_type,
        start_time=parse_hhmm(payload.start_time) if payload.start_time else None,
        end_time=parse_hhmm(payload.end_time) if payload.end_time else None,
        blocking=payload.blocking,
        note=payload.note or None,
        sort_order=sort_order,
        is_active=payload.is_active,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _to_read(row)


@router.put(
    "/reorder",
    response_model=list[EventTemplateRead],
    summary="Reorder templates within one scope (admin)",
)
async def reorder_event_templates(
    payload: ReorderRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> list[EventTemplateRead]:
    if len(set(payload.ordered_ids)) != len(payload.ordered_ids):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="ordered_ids に重複があります",
        )

    rows = (
        await db.scalars(select(EventTemplate).where(EventTemplate.id.in_(payload.ordered_ids)))
    ).all()
    by_id = {r.id: r for r in rows}
    for tid in payload.ordered_ids:
        row = by_id.get(tid)
        if row is None or row.staff_id != payload.staff_id:
            # 存在しない ID / スコープ不一致はどちらも「並べ替えの前提が壊れている」。
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="対象のひな形が見つからないか、スコープが一致しません",
            )

    for index, tid in enumerate(payload.ordered_ids):
        by_id[tid].sort_order = index
    await db.commit()

    refreshed = (
        await db.scalars(
            select(EventTemplate)
            .where(_scope_clause(payload.staff_id))
            .order_by(EventTemplate.sort_order, EventTemplate.created_at)
        )
    ).all()
    return [_to_read(r) for r in refreshed]


@router.patch(
    "/{template_id}",
    response_model=EventTemplateRead,
    summary="Update an event template (admin)",
)
async def update_event_template(
    template_id: UUID,
    payload: EventTemplateUpdate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> EventTemplateRead:
    row = await db.scalar(select(EventTemplate).where(EventTemplate.id == template_id))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    fields = payload.model_fields_set
    if payload.title is not None:
        row.title = payload.title
    if payload.event_type is not None:
        row.event_type = payload.event_type
    if "start_time" in fields:
        # スキーマ側で「両方 or どちらも」を保証済み。
        row.start_time = parse_hhmm(payload.start_time) if payload.start_time else None
        row.end_time = parse_hhmm(payload.end_time) if payload.end_time else None
    if payload.blocking is not None:
        row.blocking = payload.blocking
    if "note" in fields:
        row.note = payload.note or None
    if payload.sort_order is not None:
        row.sort_order = payload.sort_order
    if payload.is_active is not None:
        row.is_active = payload.is_active

    await db.commit()
    await db.refresh(row)
    return _to_read(row)


@router.delete(
    "/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an event template (admin, hard delete)",
)
async def delete_event_template(
    template_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> None:
    row = await db.scalar(select(EventTemplate).where(EventTemplate.id == template_id))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await db.delete(row)
    await db.commit()
