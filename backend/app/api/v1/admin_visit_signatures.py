"""Admin-only — サインの画像の保持期間パージ (定期 cron 用).

正典設計書: ``docs/plans/signature-checkin-design-2026-10-06.md`` §5-1 Q5。

  * ``POST /api/v1/admin/visit-signatures/purge-images`` — 保持日数
    (``VISIT_SIGNATURE_RETENTION_DAYS``・既定 1825 日 = 5 年) を超えたサインの
    **画像ファイルだけ** を消す。記録の行 (と退出の打刻) は残し、``image_path=NULL`` +
    ``image_deleted_at`` で「消した」と分かる形にする。

経過日数は ``created_at`` (サーバーが受け取った時刻) で数える。``admin_visit_recordings``
の ``purge-audio`` と同型: advisory lock で多重実行を排他し、冪等 (二度目は 0 件)。

保持日数の下限は 365 日。誤設定で短すぎる値が入っていたら、証拠を焼く前に 422 で止める。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import DbDep, require_role
from app.models.user import User
from app.models.visit_signature import VisitSignature
from app.utils.db import try_advisory_xact_lock

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/visit-signatures", tags=["admin", "visit-signatures"])

#: 保持日数の下限 (証拠の画像なので、誤設定で短く消さない)。
MIN_RETENTION_DAYS: Final = 365
#: pg_try_advisory_xact_lock 用キー ("VSIGPURG" 相当)。サインの画像のパージ専用。
PURGE_SIGNATURE_LOCK_KEY: Final = 0x56534947_50555247


class PurgeSignatureImagesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locked: bool = Field(description="他のジョブが advisory lock を保持していれば True (= no-op).")
    purged: int = Field(description="画像ファイルを削除した visit_signatures 行数.")


async def run_signature_purge(
    db: AsyncSession, *, retention_days: int, now: datetime | None = None
) -> dict[str, int | bool]:
    """保持日数を超えたサインの画像を消し、行には痕跡だけ残す (冪等・内部で commit)。

    Raises:
        ValueError: ``retention_days`` が下限未満 (= 設定ミス)。
    """
    if retention_days < MIN_RETENTION_DAYS:
        raise ValueError(
            f"VISIT_SIGNATURE_RETENTION_DAYS must be >= {MIN_RETENTION_DAYS} (got {retention_days})"
        )
    if not await try_advisory_xact_lock(db, PURGE_SIGNATURE_LOCK_KEY):
        logger.warning("purge_signatures: another job holds advisory lock, skipping")
        return {"locked": True, "purged": 0}
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=retention_days)
    rows = (
        await db.scalars(
            select(VisitSignature).where(
                VisitSignature.created_at < cutoff,
                VisitSignature.image_path.is_not(None),
            )
        )
    ).all()
    # ファイルを先に消し、消せた行だけ「消した」にする。消せなかった行は印を付けずに
    # 残し、次の実行でまた消しに行く (証拠のファイルが残ったまま「消した」と記録しない)。
    purged = 0
    for row in rows:
        path = Path(row.image_path) if row.image_path else None
        try:
            if path is not None:
                path.unlink(missing_ok=True)
        except OSError:
            logger.error("purge_signatures: failed to unlink %s (left for next run)", path)
            continue
        row.image_path = None
        row.image_bytes = None
        row.image_deleted_at = now
        purged += 1
    await db.commit()
    logger.info("purge_signatures: removed %d images (cutoff=%s)", purged, cutoff)
    return {"locked": False, "purged": purged}


@router.post(
    "/purge-images",
    summary="サインの画像のパージ (定期 cron 日次推奨・保持日数超の画像ファイルだけ削除)",
    response_model=PurgeSignatureImagesResponse,
)
async def purge_signature_images(
    db: DbDep,
    _admin: Annotated[User, Depends(require_role("admin"))],
) -> PurgeSignatureImagesResponse:
    settings = get_settings()
    try:
        result = await run_signature_purge(
            db, retention_days=settings.visit_signature_retention_days
        )
    except ValueError as exc:
        await db.rollback()
        logger.error("purge_signatures rejected: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None
    except Exception:  # noqa: BLE001 — cron 向けに失敗を構造化応答へ集約する
        await db.rollback()
        logger.exception("purge_signatures failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="purge-images job failed",
        ) from None
    return PurgeSignatureImagesResponse(**result)
