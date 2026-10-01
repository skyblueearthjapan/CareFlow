"""打刻履歴 API (``/api/v1/visit-history``) — 読み取り専用.

正典設計書: ``docs/plans/visit-history-design-2026-09-30.md`` §3 と
``docs/plans/actual-time-adjust-design-2026-09-30.md`` §6-3 (実績の時刻を合わせる。
合わせる操作そのものは ``PUT /visits/{id}/actual-time``)。

  * ``GET /visit-history``         一覧 (絞り込み・並び替え・ページング・集計帯)
  * ``GET /visit-history/export``  Excel (.xlsx・4 シート)
  * ``GET /visit-history/report``  A4 縦の印刷 HTML

3 本とも同じ共通クエリ (``from`` / ``to`` / 絞り込み / ``sort``) を受け、同じ行
(``services/checkin/history.py``) を使う。

可視性: **staff は自分の分だけ** (自分が担当集合に入る訪問・主担当が空で自分が
コース担当の訪問・自分が打刻した訪問)。``staff_id`` を指定しても自分に固定する。
staff 未紐付けの staff は空。admin は全件 (``/visit-recordings`` の一覧と同じ流儀)。

氏名と訪問時刻を返すので 3 本とも ``Cache-Control: no-store``。Excel と A4 は氏名と
時刻の一括出力なので、``audit_logs`` に読み取りを明示記録する
(``/visit-recordings`` の report と同じ流儀)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import DbDep, require_role
from app.models.audit_log import AuditLog
from app.models.user import User, normalize_user_role
from app.schemas.visit_history import VisitHistoryList
from app.services.checkin.adjust import can_adjust_actual_time
from app.services.checkin.history import (
    JST,
    HistoryRow,
    filter_rows,
    group_counts,
    load_history_rows,
    sort_rows,
    summarize,
    without_future,
)
from app.services.checkin.history_report_html import render_history_report_html
from app.services.checkin.history_xlsx import build_history_xlsx
from app.services.checkin.judge import load_thresholds

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/visit-history", tags=["visit-history"])

#: 期間の上限 (両端を含む日数)。3 か月分を 1 回で引ける幅。
MAX_RANGE_DAYS = 92

EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_NO_STORE: dict[str, str] = {"Cache-Control": "no-store"}


@dataclass
class _HistoryQuery:
    """共通クエリを解決した結果 (絞り込み・並び替え済みの行)。"""

    date_from: date
    date_to: date
    rows: list[HistoryRow]
    #: A4 の 1 枚目に出す「何で絞ったか」(絞っていなければ None)。
    scope_note: str | None
    #: 並び (``groups`` の単位)。
    sort: str
    #: 出力した人 (Excel / A4 の監査記録用)。
    user: User


async def _history_query(
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin", "staff"))],
    from_: Annotated[date, Query(alias="from")],
    to: Annotated[date, Query()],
    patient_id: Annotated[UUID | None, Query()] = None,
    office_id: Annotated[UUID | None, Query()] = None,
    staff_id: Annotated[UUID | None, Query()] = None,
    state: Annotated[
        Literal["in", "nodep", "none", "adjusted", "special", "late"] | None, Query()
    ] = None,
    # 2 文字未満は絞り込みとして無意味なので無視する (422 にはしない)。
    q: Annotated[str | None, Query(max_length=100)] = None,
    sort: Annotated[Literal["date", "staff", "patient"], Query()] = "date",
) -> _HistoryQuery:
    if from_ > to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="from は to 以前の日付にしてください",
        )
    if (to - from_).days + 1 > MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"期間は {MAX_RANGE_DAYS} 日以内で指定してください",
        )

    scope_staff_id: UUID | None = None
    is_admin = normalize_user_role(user.role) == "admin"
    if not is_admin:
        # staff は指定に関わらず自分の分だけ (staff_id は無視ではなく強制上書き)。
        if user.staff_id is None:
            return _HistoryQuery(from_, to, [], "自分の担当・打刻分", sort, user)
        scope_staff_id = user.staff_id
        staff_id = None

    now = datetime.now(UTC)
    rows = filter_rows(
        await load_history_rows(db, from_, to, now=now, viewer_is_admin=is_admin),
        scope_staff_id=scope_staff_id,
        patient_id=patient_id,
        office_id=office_id,
        staff_id=staff_id,
        state=state,
        q=q,
    )
    if scope_staff_id is not None:
        scope_note: str | None = "自分の担当・打刻分"
    elif any(x is not None for x in (patient_id, office_id, staff_id, state)) or (
        q is not None and len(q.strip()) >= 2
    ):
        scope_note = "画面の条件で絞り込んだ結果"
    else:
        scope_note = None
    # 実績の時刻を合わせられるか (``PUT /visits/{id}/actual-time`` と同じ権限の規則)。
    # staff の行は可視範囲 (= 自分が関わった訪問) で絞ってあるので ``related`` は常に真。
    # 到着の読み取りが無い行は、合わせる対象が無いので偽。
    today = now.astimezone(JST).date()
    window_days = (await load_thresholds(db))["staff_adjust_window_days"]
    for row in rows:
        row.adjust_allowed = can_adjust_actual_time(
            is_admin=is_admin,
            visit_date=row.visit_date,
            deleted=row.is_deleted,
            related=True,
            today=today,
            has_arrival_read=row.has_arrival_read,
            window_days=window_days,
        )
    return _HistoryQuery(from_, to, sort_rows(rows, sort), scope_note, sort, user)


HistoryQueryDep = Annotated[_HistoryQuery, Depends(_history_query)]


async def _audit_output(
    db: AsyncSession,
    request: Request,
    query: _HistoryQuery,
    *,
    action: str,
    suffix: str,
    row_count: int,
) -> None:
    """Excel / A4 の出力を ``audit_logs`` に明示記録する。

    監査ミドルウェアは GET を記録しない。この 2 本は氏名と訪問時刻を期間まるごと
    持ち出すので、例外として自分で 1 行書く (``/visit-recordings`` の ``_audit_read``
    と同じ流儀)。記録に失敗しても **出力は止めない** — 監査の都合で請求前の確認作業を
    落とす方が害が大きい。画面の一覧 (``GET /visit-history``) は記録しない。
    """
    forwarded = request.headers.get("x-forwarded-for")
    ip_address = (
        forwarded.split(",", 1)[0].strip()[:64]
        if forwarded
        else (request.client.host[:64] if request.client else None)
    )
    period = f"{query.date_from.isoformat()}_{query.date_to.isoformat()}"
    db.add(
        AuditLog(
            actor_user_id=query.user.id,
            role=normalize_user_role(query.user.role),
            action=action,
            target_table="visit_history",
            target_id=period,
            after={
                "from": query.date_from.isoformat(),
                "to": query.date_to.isoformat(),
                "rows": row_count,
                "scope": query.scope_note,
            },
            method="GET",
            path=f"/api/v1/visit-history{suffix}",
            status_code=200,
            ip_address=ip_address,
            user_agent=(request.headers.get("user-agent") or "")[:255] or None,
        )
    )
    try:
        await db.commit()
    except Exception:  # noqa: BLE001 — 監査の失敗で出力を落とさない
        logger.exception("visit-history: %s audit insert failed (swallowed)", action)
        await db.rollback()


@router.get(
    "",
    response_model=VisitHistoryList,
    summary="打刻履歴の一覧 (staff は自分の分のみ・BE 絞り込み + ページング + 集計帯)",
)
async def list_visit_history(
    query: HistoryQueryDep,
    response: Response,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    response.headers["Cache-Control"] = _NO_STORE["Cache-Control"]
    return {
        "items": query.rows[offset : offset + limit],
        "total": len(query.rows),
        # ページングする前の絞り込み結果全体から数える。
        "summary": summarize(query.rows),
        "groups": group_counts(query.rows, query.sort),
    }


@router.get(
    "/export",
    response_class=Response,
    summary="打刻履歴の Excel 出力 (.xlsx・4 シート)",
)
async def export_visit_history(query: HistoryQueryDep, request: Request, db: DbDep) -> Response:
    rows = without_future(query.rows)
    content = build_history_xlsx(rows, date_from=query.date_from, date_to=query.date_to)
    await _audit_output(
        db, request, query, action="export_read", suffix="/export", row_count=len(rows)
    )
    filename = f"visit-history_{query.date_from.isoformat()}_{query.date_to.isoformat()}.xlsx"
    return Response(
        content=content,
        media_type=EXCEL_MIME,
        headers={"Content-Disposition": f'attachment; filename="{filename}"', **_NO_STORE},
    )


@router.get(
    "/report",
    response_class=HTMLResponse,
    summary="打刻履歴の印刷レポート (A4 縦 HTML)",
)
async def get_visit_history_report(
    query: HistoryQueryDep,
    request: Request,
    db: DbDep,
    group: Annotated[Literal["staff", "date", "patient"], Query()] = "staff",
    include_none: Annotated[bool, Query()] = False,
    page_break: Annotated[bool, Query()] = False,
) -> HTMLResponse:
    # 明細の並びは ``group`` が決める (グループ内は常に日付順)。``sort`` は使わない。
    rows = sort_rows(without_future(query.rows), "date")
    html_doc = render_history_report_html(
        rows,
        date_from=query.date_from,
        date_to=query.date_to,
        group=group,
        include_none=include_none,
        page_break=page_break,
        generated_at=datetime.now(UTC),
        scope_note=query.scope_note,
    )
    await _audit_output(
        db, request, query, action="report_read", suffix="/report", row_count=len(rows)
    )
    return HTMLResponse(html_doc, headers=_NO_STORE)
