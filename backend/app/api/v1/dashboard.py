"""Dashboard aggregation endpoints (Phase 7 — W2-D).

Read-only endpoints that summarise the `visits` table for the homepage
dashboard. Staff users see their own assigned visits across all three slots
(primary, secondary/同行, mentor); admin/manager see everything.

Implementation notes
────────────────────
* Today's KPI is one indexed `WHERE visit_date = today` query plus a tiny
  Python pass to compute overlaps (cheap for the per-day cardinality we
  expect — typically < 200 rows).
* Trend uses a single `GROUP BY visit_date` query over the requested window
  and back-fills missing days with zeros so the chart is gap-free.
* The week aggregate is the ISO week containing today (Mon–Sun).
* `cancelled` visits are excluded from every aggregate (today / week / trend)
  so they neither inflate visit totals nor drag down completion rates.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import case, func, or_, select

from app.core.deps import CurrentActiveUser, DbDep
from app.models.user import normalize_user_role
from app.models.visit import (
    VISIT_STATUS_CANCELLED,
    VISIT_STATUS_COMPLETED,
    Visit,
)
from app.schemas.dashboard import (
    DashboardKpiResponse,
    DashboardTrendItem,
    DashboardTrendResponse,
)

router = APIRouter()

# All "today"/"end" date math is anchored to JST so a request issued from a
# UTC-region VPS still uses Japan local-day boundaries (the staff-facing UI
# is JST-only). Status comparisons below use the typed constants imported
# from `app.models.visit` (backed by the `VisitStatus` Literal).
JST = ZoneInfo("Asia/Tokyo")

_ALLOWED_ROLES = {"admin", "staff"}


def _require_dashboard_role(role: str) -> None:
    # アカウント権限は 2 値 (管理者/一般・2026-08-09)。旧 'manager' は別名正規化。
    if normalize_user_role(role) not in _ALLOWED_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")


def _staff_scope(stmt, role: str, staff_id: UUID | None):
    """Restrict a select() to the caller's own visits when role='staff'.

    A visit is "the caller's own" when the caller is in any of the three
    staff slots — primary, secondary (同行), or mentor — so a同行/mentor
    visit still surfaces in the dashboard KPIs of the accompanying staff.

    Returns a sentinel (None) when the user is staff without a linked
    `staff_id`, so the caller can short-circuit to empty aggregates instead
    of issuing a query that would return everything.
    """
    if role != "staff":
        return stmt
    if staff_id is None:
        return None
    return stmt.where(
        or_(
            Visit.primary_staff_id == staff_id,
            Visit.secondary_staff_id == staff_id,
            Visit.mentor_staff_id == staff_id,
        )
    )


def _iso_week_bounds(today: date) -> tuple[date, date]:
    """Return (Monday, Sunday) of the ISO week containing `today`."""
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def _count_overlaps(rows: list[tuple[UUID | None, object, object]]) -> int:
    """Count visits that overlap another visit sharing the same primary_staff.

    `rows` is a list of `(primary_staff_id, start_time, end_time)` tuples for
    a single day. Visits without a primary_staff are skipped.
    Each side of an overlapping pair is counted, matching how the UI
    surfaces "重複している予定" badges.
    """
    by_staff: dict[UUID, list[tuple[object, object]]] = {}
    for staff_id, start, end in rows:
        if staff_id is None:
            continue
        by_staff.setdefault(staff_id, []).append((start, end))

    overlapping = 0
    for slots in by_staff.values():
        slots.sort(key=lambda x: x[0])
        is_overlap = [False] * len(slots)
        for i in range(len(slots)):
            for j in range(i + 1, len(slots)):
                if slots[j][0] >= slots[i][1]:
                    break  # sorted by start; no further overlaps with i
                is_overlap[i] = True
                is_overlap[j] = True
        overlapping += sum(1 for x in is_overlap if x)
    return overlapping


@router.get(
    "/kpi",
    response_model=DashboardKpiResponse,
    summary="Today + this-week dashboard KPI",
)
async def get_kpi(db: DbDep, user: CurrentActiveUser) -> DashboardKpiResponse:
    _require_dashboard_role(user.role)

    today = datetime.now(JST).date()
    week_start, week_end = _iso_week_bounds(today)

    # ---- Today's roll-up ---------------------------------------------------
    # Cancelled visits are excluded so they neither count toward
    # `today_visits` nor inflate `today_overlapping`. Status constants come
    # from `app.models.visit` (typed by the `VisitStatus` Literal).
    today_stmt = select(
        Visit.primary_staff_id,
        Visit.start_time,
        Visit.end_time,
        Visit.status,
    ).where(
        Visit.deleted_at.is_(None),
        Visit.visit_date == today,
        Visit.status != VISIT_STATUS_CANCELLED,
    )

    today_stmt = _staff_scope(today_stmt, user.role, user.staff_id)
    if today_stmt is None:
        return DashboardKpiResponse(
            today_visits=0,
            today_completed=0,
            today_unassigned=0,
            today_overlapping=0,
            this_week_visits=0,
            this_week_completion_rate=0.0,
        )

    today_rows = (await db.execute(today_stmt)).all()
    today_visits = len(today_rows)
    today_completed = sum(1 for r in today_rows if r.status == VISIT_STATUS_COMPLETED)
    today_unassigned = sum(1 for r in today_rows if r.primary_staff_id is None)
    today_overlapping = _count_overlaps(
        [(r.primary_staff_id, r.start_time, r.end_time) for r in today_rows]
    )

    # ---- This week's roll-up ----------------------------------------------
    # Cancelled visits are excluded so the weekly total and completion rate
    # reflect operational visits only (consistent with today's KPI above).
    week_stmt = select(
        func.count(Visit.id).label("total"),
        func.sum(case((Visit.status == VISIT_STATUS_COMPLETED, 1), else_=0)).label("completed"),
    ).where(
        Visit.deleted_at.is_(None),
        Visit.visit_date >= week_start,
        Visit.visit_date <= week_end,
        Visit.status != VISIT_STATUS_CANCELLED,
    )
    week_stmt = _staff_scope(week_stmt, user.role, user.staff_id)
    # _staff_scope cannot return None here because we returned early above.
    assert week_stmt is not None
    week_row = (await db.execute(week_stmt)).one()
    week_total = int(week_row.total or 0)
    week_completed = int(week_row.completed or 0)
    week_rate = (week_completed / week_total) if week_total > 0 else 0.0

    return DashboardKpiResponse(
        today_visits=today_visits,
        today_completed=today_completed,
        today_unassigned=today_unassigned,
        today_overlapping=today_overlapping,
        this_week_visits=week_total,
        this_week_completion_rate=round(week_rate, 4),
    )


@router.get(
    "/trend",
    response_model=DashboardTrendResponse,
    summary="Daily visit trend (last N days)",
)
async def get_trend(
    db: DbDep,
    user: CurrentActiveUser,
    days: Annotated[int, Query(ge=1, le=90)] = 7,
) -> DashboardTrendResponse:
    _require_dashboard_role(user.role)

    end = datetime.now(JST).date()
    start = end - timedelta(days=days - 1)

    stmt = (
        select(
            Visit.visit_date.label("d"),
            func.count(Visit.id).label("total"),
            func.sum(case((Visit.status == VISIT_STATUS_COMPLETED, 1), else_=0)).label("completed"),
            func.sum(case((Visit.primary_staff_id.is_(None), 1), else_=0)).label("unassigned"),
        )
        .where(
            Visit.deleted_at.is_(None),
            Visit.visit_date >= start,
            Visit.visit_date <= end,
            # Cancelled visits are excluded from trend totals to match the
            # exclusion rule applied to today/week aggregates.
            Visit.status != VISIT_STATUS_CANCELLED,
        )
        .group_by(Visit.visit_date)
        .order_by(Visit.visit_date.asc())
    )

    scoped = _staff_scope(stmt, user.role, user.staff_id)
    if scoped is None:
        # Staff with no linked staff_id → empty (but well-formed) series.
        items = [
            DashboardTrendItem(date=start + timedelta(days=i), total=0, completed=0, unassigned=0)
            for i in range(days)
        ]
        return DashboardTrendResponse(items=items, days=days, start_date=start, end_date=end)

    rows = (await db.execute(scoped)).all()
    by_date = {
        r.d: (int(r.total or 0), int(r.completed or 0), int(r.unassigned or 0)) for r in rows
    }
    items: list[DashboardTrendItem] = []
    for i in range(days):
        d = start + timedelta(days=i)
        total, completed, unassigned = by_date.get(d, (0, 0, 0))
        items.append(
            DashboardTrendItem(date=d, total=total, completed=completed, unassigned=unassigned)
        )

    return DashboardTrendResponse(items=items, days=days, start_date=start, end_date=end)
