"""Admin-only endpoints — QR チェックイン定期ジョブ (Phase 5-2 / 5-6).

VPS 側 cron が叩く想定 (``docs/runbook/lat_lng_audit_cron.md`` と同じ認証方式 =
admin role の Bearer token):

  * ``POST /api/v1/admin/checkin/check-missing`` — 当日の未訪問 visit を admin/manager
    へ冪等通知 (5 分毎想定)。
  * ``POST /api/v1/admin/checkin/purge-gps`` — 2 年超の checkin の lat/lng/accuracy を
    NULL 化 (日次想定)。

いずれも advisory lock で多重実行を排他し冪等 (重複起動しても二重生成 / 二重更新
しない)。
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.core.deps import DbDep, require_role
from app.models.user import User
from app.services.checkin.notify import run_check_missing
from app.services.checkin.purge import run_gps_purge

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/checkin", tags=["admin", "checkin"])


class CheckMissingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locked: bool = Field(description="他のジョブが advisory lock を保持していれば True (= no-op).")
    scanned: int = Field(description="走査した当日 visit 数.")
    missing: int = Field(description="未訪問と判定した visit 数.")
    created: int = Field(description="新規生成した通知行数 (既通知は除外).")


class PurgeGpsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locked: bool = Field(description="他のジョブが advisory lock を保持していれば True (= no-op).")
    purged: int = Field(description="lat/lng/accuracy_m を NULL 化した checkin 行数.")


@router.post(
    "/check-missing",
    summary="未訪問通知 (定期 cron 5 分毎推奨、未訪問 visit を admin/manager へ冪等通知)",
    response_model=CheckMissingResponse,
)
async def check_missing(
    db: DbDep,
    _admin: Annotated[User, Depends(require_role("admin"))],
) -> CheckMissingResponse:
    """当日の未訪問 visit を走査し admin/manager へ冪等通知する (VPS cron 用).

    ``run_check_missing`` は **内部で commit する** (advisory lock を commit で
    解放するため)。失敗時は rollback して 500 を返す (cron が再試行できるよう
    構造化エラー応答)。
    """
    try:
        result = await run_check_missing(db)
    except Exception:  # noqa: BLE001 — cron 向けに失敗を構造化応答へ集約する
        await db.rollback()
        logger.exception("check_missing failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="check-missing job failed",
        ) from None
    return CheckMissingResponse(**result)


@router.post(
    "/purge-gps",
    summary="GPS パージ (定期 cron 日次推奨、2 年超の lat/lng/accuracy を NULL 化)",
    response_model=PurgeGpsResponse,
)
async def purge_gps(
    db: DbDep,
    _admin: Annotated[User, Depends(require_role("admin"))],
) -> PurgeGpsResponse:
    """2 年超の checkin の lat/lng/accuracy_m を NULL 化する (distance/status は残す).

    ``run_gps_purge`` は **内部で commit する**。失敗時は rollback して 500 を返す。
    """
    try:
        result = await run_gps_purge(db)
    except Exception:  # noqa: BLE001 — cron 向けに失敗を構造化応答へ集約する
        await db.rollback()
        logger.exception("purge_gps failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="purge-gps job failed",
        ) from None
    return PurgeGpsResponse(**result)
