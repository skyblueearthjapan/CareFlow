"""受け入れ枠マトリックス (acceptance matrix) endpoint — P1.

GET /api/v1/acceptance-matrix
    ?iso_year=2026&iso_week=26[&office_id=...][&service_minutes=60]

拠点 × 曜日 × 時間帯の受け入れ可否 (○△×) を当週の実 Visit から自動算出して返す
**read-only** API。自動算出値 (``auto_status``) に既存 ``acceptance_calendar``
(常設手動上書き) を重ねた ``effective_status`` を返す。DB 書込なし。

ロジック本体は ``app.services.scheduling.acceptance_matrix_service``。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbDep, require_role
from app.models.user import User
from app.schemas.v2.acceptance_matrix import AcceptanceMatrixResponse
from app.services.scheduling.acceptance_matrix_service import compute_acceptance_matrix

router = APIRouter()


@router.get(
    "",
    response_model=AcceptanceMatrixResponse,
    summary="受け入れ枠マトリックス (拠点×曜日×時間帯 ○△× 自動算出, read-only)",
)
async def get_acceptance_matrix(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager", "staff"))],
    iso_year: Annotated[int, Query(ge=2020, le=2100, description="ISO 年")],
    iso_week: Annotated[int, Query(ge=1, le=53, description="ISO 週")],
    office_id: Annotated[UUID | None, Query(description="拠点 ID (未指定なら全拠点)")] = None,
    service_minutes: Annotated[
        int, Query(ge=1, le=480, description="判定に用いる標準訪問の長さ (分)")
    ] = 60,
) -> AcceptanceMatrixResponse:
    """指定週・拠点の受け入れ枠マトリックスを返す (DB 書込なし)."""
    data = await compute_acceptance_matrix(
        db,
        iso_year=iso_year,
        iso_week=iso_week,
        office_ids=[office_id] if office_id is not None else None,
        service_minutes=service_minutes,
    )
    return AcceptanceMatrixResponse.model_validate(data)
