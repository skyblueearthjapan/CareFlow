"""Schedule endpoints (W3-BE-FIX / W4-BE7).

設計仕様書 ``docs/plans/v2-allocation-redesign.md`` v0.9 §3.6.2 (Layer 1
時間配置) と API 契約 ``docs/plans/v2-api-contracts.md`` §6 に対応する
HTTP 層。

実装エンドポイント:
    - POST /api/v1/schedule/fix         (W3-BE-FIX): 週レイアウト確定
    - POST /api/v1/schedule/generate-week (W4-BE7): Layer 1 アルゴリズム

## RBAC (API 契約 §6)

- POST /schedule/fix          — admin / manager のみ (staff は 403)
- POST /schedule/generate-week — admin / manager のみ (staff は 403)

## トランザクション

各サービス層 (``ScheduleFixService`` / ``Layer1Expander``) は
``await db.flush()`` のみを呼び、``commit()`` / ``rollback()`` は呼ばない。
本 HTTP 層が ``try/except`` で 1 リクエストを 1 トランザクションに包み、
例外時に rollback する。
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
from app.services.scheduling import (
    Layer1Expander,
    Layer1ExpandError,
    PoolEntry,
    VisitCreated,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# /fix Request / Response schemas (HTTP 層に閉じる)
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
# /generate-week Request / Response schemas (W4-BE7)
# ---------------------------------------------------------------------------


class GenerateWeekRequest(BaseModel):
    """``POST /api/v1/schedule/generate-week`` のリクエストボディ.

    Layer 1 はステートレス (週指定のみで全 active 患者を再展開する)。
    """

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(ge=2000, le=2100)
    iso_week: int = Field(ge=1, le=53)


class GenerateWeekSummary(BaseModel):
    """Layer 1 結果のサマリ (W4-BE8 / W4-BE9 の入力要約)."""

    model_config = ConfigDict(extra="forbid")

    patients_processed: int
    visits_created: int
    pool_count: int
    special_week_applied_count: int


class GenerateWeekResponse(BaseModel):
    """``POST /api/v1/schedule/generate-week`` のレスポンス (W4-BE7)。

    出力 schema は W4-BE8 (Layer 2) が直接消費するため、フィールド名 /
    型は **後方互換** を保つこと。フィールド追加は OK、リネームは禁止。
    """

    model_config = ConfigDict(extra="forbid")

    iso_year: int
    iso_week: int
    visits_created: list[VisitCreated]
    pool: list[PoolEntry]
    summary: GenerateWeekSummary


# ---------------------------------------------------------------------------
# Endpoints
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


@router.post(
    "/generate-week",
    response_model=GenerateWeekResponse,
    status_code=status.HTTP_200_OK,
    summary="Layer 1: expand weekly_pattern → visits + pool (W4-BE7)",
)
async def generate_week(
    payload: GenerateWeekRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> GenerateWeekResponse:
    """Layer 1 アルゴリズムを実行し、当該週の visits を生成する.

    冪等: 同じ (iso_year, iso_week) で 2 回呼ぶと、当該週の自動生成
    visit (source=auto, status=planned) は削除されてから再生成される。
    completed / cancelled / source != auto の visit は保護される。
    """
    expander = Layer1Expander()
    try:
        result = await expander.expand_week(
            db, iso_year=payload.iso_year, iso_week=payload.iso_week
        )
        await db.commit()
    except Layer1ExpandError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    except Exception:
        await db.rollback()
        raise

    return GenerateWeekResponse(
        iso_year=result.iso_year,
        iso_week=result.iso_week,
        visits_created=result.visits_created,
        pool=result.pool,
        summary=GenerateWeekSummary(
            patients_processed=result.patients_processed,
            visits_created=result.visits_created_count,
            pool_count=result.pool_count,
            special_week_applied_count=result.special_week_applied_count,
        ),
    )


__all__ = ["router"]
