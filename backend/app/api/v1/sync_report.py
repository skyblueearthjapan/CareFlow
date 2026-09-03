"""連携結果レポート API — GET /integrations/kaipoke/jobs/{job_id}/report。

admin・read-only (DB 書込なし)。`format=html` で A4 縦の自己完結 HTML、
既定の `format=json` では同じ内容の構造化データ (+ `html` 同梱) を返す。
正典 = ``docs/plans/sync-result-report-design.md`` §3。

`integrations.py` とは別ファイルにしてある (レポートは read-only の派生機能で、
連携センター本体の巨大ルータと寿命が違うため)。登録は `api/v1/__init__.py` で
``prefix="/integrations"`` — 最終パスは
``/api/v1/integrations/kaipoke/jobs/{job_id}/report``。
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse

from app.core.deps import DbDep, require_role
from app.models.user import User
from app.schemas.sync_report import SyncReportRead
from app.services.kaipoke.sync_report import (
    SyncReportNotFoundError,
    SyncReportUnsupportedError,
    build_sync_report,
)
from app.services.kaipoke.sync_report_html import render_sync_report_html

router = APIRouter()


@router.get(
    "/kaipoke/jobs/{job_id}/report",
    response_model=SyncReportRead,
    responses={200: {"content": {"text/html": {}}}},
    summary="連携結果レポート (印刷用 HTML 同梱・read-only・admin)",
)
async def get_sync_report(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    job_id: UUID,
    fmt: Annotated[Literal["json", "html"], Query(alias="format")] = "json",
    include_html: Annotated[bool, Query(alias="includeHtml")] = True,
    verify: Annotated[bool, Query()] = True,
):
    """完了済みジョブ 1 件の結果を A4 縦の報告書にして返す (両方向)。

    対象外の op / 未完了ジョブは 422、存在しないジョブは 404。
    `verify=false` で末尾のカイポケ突合 (月の CSV を組み直すので重い) を省略する。
    """
    try:
        report = await build_sync_report(db, job_id, verify=verify)
    except SyncReportNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="ジョブが見つかりません"
        ) from exc
    except SyncReportUnsupportedError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    if fmt == "html":
        return HTMLResponse(render_sync_report_html(report))

    payload = report.to_dict()
    # includeHtml=false のときは組み立てもしない (数字だけ欲しい呼び出し元向け)。
    payload["html"] = render_sync_report_html(report) if include_html else None
    return SyncReportRead.model_validate(payload)
