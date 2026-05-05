"""Schedule endpoints (W3-BE-FIX).

設計仕様書 ``docs/plans/v2-allocation-redesign.md`` v0.9 §3.6.2 (Layer 1
時間配置) と API 契約 ``docs/plans/v2-api-contracts.md`` §6.1 に対応する
HTTP 層。本ファイルでは ``POST /api/v1/schedule/fix`` のみ実装する。

将来的に W4-BE7 で ``POST /api/v1/schedule/generate-week`` を同じ router
に追加予定。

## RBAC (API 契約 §6.1)

- POST /schedule/fix — admin / manager のみ (staff は 403)

## トランザクション

サービス層 (``ScheduleFixService``) は ``await db.flush()`` のみを呼び、
``commit()`` / ``rollback()`` を呼ばない。本 HTTP 層が ``try/except`` で
全 patient の更新を 1 トランザクションに包み、例外時に rollback する
(API 契約 §6.1: "全件 1 トランザクション")。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.core.deps import DbDep, require_role
from app.models.user import User
from app.services.schedule_fix_service import (
    FixedVisit,
    ScheduleFixError,
    ScheduleFixService,
    WeeklyPatternChange,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response schemas (HTTP 層に閉じる)
# ---------------------------------------------------------------------------


class ScheduleFixRequest(BaseModel):
    """``POST /api/v1/schedule/fix`` のリクエストボディ.

    ``layout`` 内の各 FixedVisit は (patient_id × weekday × start_time)
    の単位。同一患者で複数曜日 / 複数時刻スロットを送る場合は要素を増やす。
    """

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(ge=2000, le=2100)
    iso_week: int = Field(ge=1, le=53)
    layout: list[FixedVisit] = Field(default_factory=list)


class ScheduleFixResponse(BaseModel):
    """``POST /api/v1/schedule/fix`` のレスポンス."""

    model_config = ConfigDict(extra="forbid")

    patients_updated: int
    weekly_pattern_changes: list[WeeklyPatternChange]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/fix",
    response_model=ScheduleFixResponse,
    status_code=status.HTTP_200_OK,
    summary="Fix week layout into patients.weekly_pattern (W3-BE-FIX)",
)
async def fix_schedule(
    payload: ScheduleFixRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> ScheduleFixResponse:
    """週レイアウトを各患者の ``weekly_pattern`` に保存する.

    全件 1 トランザクション。1 件でも失敗すれば全 rollback。
    """
    service = ScheduleFixService()
    try:
        result = await service.fix_week_layout(
            db,
            iso_year=payload.iso_year,
            iso_week=payload.iso_week,
            layout=payload.layout,
        )
        await db.commit()
    except ScheduleFixError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    except Exception:
        # 想定外の例外でも必ず rollback する (受入基準 3: トランザクショナル)
        await db.rollback()
        raise

    return ScheduleFixResponse(
        patients_updated=result.patients_updated,
        weekly_pattern_changes=result.weekly_pattern_changes,
    )


__all__ = ["router"]
