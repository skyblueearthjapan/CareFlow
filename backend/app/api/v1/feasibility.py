"""実現性チェック API — 週の予定の移動・重なり・バッファ・同住所ルールを機械判定 (read-only).

PO 要望 (2026-08-31): 盤面の「実現性チェック」ボタンから A4 レポートを出す。
判定は ``services/scheduling/feasibility_check`` (らく助の設定値・定数と同じ前提)。
DB への書込は無い。
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse

from app.core.deps import DbDep, require_role
from app.models.user import User
from app.schemas.v2.feasibility import FeasibilityMarksRead, FeasibilityReportRead
from app.services.scheduling.feasibility_alerts import MAX_MARK_DAYS, build_marks
from app.services.scheduling.feasibility_check import build_feasibility_report, report_to_dict
from app.services.scheduling.feasibility_report_html import render_feasibility_html

router = APIRouter()


@router.get(
    "/v2/feasibility-report",
    summary="週の実現性チェック (移動/重なり/バッファ/同住所ルール) — read-only (admin)",
    response_model=FeasibilityReportRead,
    responses={200: {"content": {"text/html": {}}}},
)
async def get_feasibility_report(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    iso_year: Annotated[int, Query(ge=2020, le=2100)],
    iso_week: Annotated[int, Query(ge=1, le=53)],
    office_id: Annotated[uuid.UUID | None, Query()] = None,
    days: Annotated[int, Query(ge=1, le=7, description="月曜から何日分 (既定 6 = 月〜土)")] = 6,
    fmt: Annotated[Literal["json", "html"], Query(alias="format")] = "json",
    include_html: Annotated[bool, Query(description="json 応答に印刷用 HTML を同梱する")] = True,
):
    try:
        report = await build_feasibility_report(
            db, iso_year=iso_year, iso_week=iso_week, office_id=office_id, days=days
        )
    except ValueError as exc:  # date.fromisocalendar の範囲外
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"invalid ISO week: {exc}"
        ) from exc
    if fmt == "html":
        return HTMLResponse(render_feasibility_html(report))
    payload = report_to_dict(report)
    if include_html:
        payload["html"] = render_feasibility_html(report)
    return FeasibilityReportRead.model_validate(payload)


@router.get(
    "/v2/feasibility-marks",
    summary="期間の実現性の印 (訪問 ID ごと・赤/黄と理由) と一覧 — read-only (admin)",
    response_model=FeasibilityMarksRead,
)
async def get_feasibility_marks(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    date_from: Annotated[date, Query()],
    date_to: Annotated[date, Query()],
    office_id: Annotated[uuid.UUID | None, Query()] = None,
) -> FeasibilityMarksRead:
    """スケジュール画面は 1 週・訪問モニターは 1 日をまとめて 1 回で取る (設計 §3-1 (B)).

    参考 (要注意(実走行)・同住所ペア:同時刻でない・昼休み・座標なし) は返さない。
    印は保存せず毎回計算する (§3-2)。
    """
    if date_to < date_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="date_to は date_from 以降を指定してください",
        )
    if (date_to - date_from).days + 1 > MAX_MARK_DAYS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"期間は {MAX_MARK_DAYS} 日までです",
        )
    payload = await build_marks(db, date_from=date_from, date_to=date_to, office_id=office_id)
    payload["date_from"] = date_from.isoformat()
    payload["date_to"] = date_to.isoformat()
    return FeasibilityMarksRead.model_validate(payload)
