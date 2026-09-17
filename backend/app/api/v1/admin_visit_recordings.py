"""Admin-only endpoints — 音声記録の保持期間パージ (定期 cron 用).

正典設計書: ``docs/plans/visit-voice-record-design-2026-09-17.md`` §2-6 / §10-3。

  * ``POST /api/v1/admin/visit-recordings/purge-audio`` — 保持日数
    (``VISIT_AUDIO_RETENTION_DAYS``) を超えた録音の **音声ファイルだけ** を消す。
    経過日数は **``created_at`` (サーバー受領時刻)** で数える (``recorded_at`` は
    端末の自己申告なので、時計が狂った端末が保持期間を飛び越えてしまう)。

``admin_checkin.purge-gps`` と同型: advisory lock で多重実行を排他し、冪等
(既に消えている行は WHERE で外れるので二度目は 0 件)。**文字起こし・要約は
消さない** — 消すのは音声バイナリだけで、行は ``audio_path=NULL`` +
``audio_deleted_at`` を立てて「パージ済み」と分かる形で残す。

保持日数の下限は 30 日 (設計 §10-1)。誤設定で短すぎる値を渡されたら記録を
焼く前に落とす (``purge.py`` の ``MIN_RETENTION_DAYS`` と同じ思想)。
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
from app.models.visit_recording import VisitRecording
from app.services.voice.jobs import reap_stale_jobs
from app.utils.db import try_advisory_xact_lock

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/visit-recordings", tags=["admin", "visit-recordings"])

# 保持の下限ガード (設計 §10-1「90（下限 30）」)。
MIN_RETENTION_DAYS: Final = 30

# pg_try_advisory_xact_lock 用キー ("VRECPURG" 相当)。音声パージ専用。
PURGE_AUDIO_LOCK_KEY: Final = 0x56524543_50555247


class PurgeAudioResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locked: bool = Field(description="他のジョブが advisory lock を保持していれば True (= no-op).")
    purged: int = Field(description="音声ファイルを削除した visit_recordings 行数.")


async def _run_audio_purge(
    db: AsyncSession, *, retention_days: int, now: datetime | None = None
) -> dict[str, int | bool]:
    """保持日数を超えた録音の音声を unlink し、行には痕跡だけ残す (冪等).

    Raises:
        ValueError: ``retention_days`` が下限未満 (= 設定ミス)。
    """
    if retention_days < MIN_RETENTION_DAYS:
        raise ValueError(
            f"VISIT_AUDIO_RETENTION_DAYS must be >= {MIN_RETENTION_DAYS} (got {retention_days})"
        )
    if not await try_advisory_xact_lock(db, PURGE_AUDIO_LOCK_KEY):
        logger.warning("purge_audio: another job holds advisory lock, skipping")
        return {"locked": True, "purged": 0}

    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=retention_days)

    # **基準は created_at (サーバー受領時刻)**。``recorded_at`` は端末の自己申告で、
    # 時計が狂った 1 台が「2 年前」を名乗れば、受け取った翌日にその音声が消える。
    # 保持期間は「預かってから何日」であって「端末が何と言ったか」ではない。
    rows = (
        await db.scalars(
            select(VisitRecording).where(
                VisitRecording.created_at < cutoff,
                # 既に音声が無い行は対象外 (冪等・無駄な書き込み回避)。
                VisitRecording.audio_path.is_not(None),
            )
        )
    ).all()

    paths: list[Path] = []
    for row in rows:
        if row.audio_path:
            paths.append(Path(row.audio_path))
        row.audio_path = None
        row.audio_bytes = None
        row.audio_deleted_at = now
    await db.commit()

    # 実ファイルの削除は commit の **後**。ここで失敗しても DB は巻き戻さない
    # (行が「パージ済み」になっている方が運用上正しい / 孤児は再実行では消えない
    # ので、残った場合は運用で掃除する)。
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - best effort
            logger.warning("purge_audio: failed to unlink %s", path)

    logger.info("purge_audio: unlinked audio for %d recordings (cutoff=%s)", len(rows), cutoff)
    return {"locked": False, "purged": len(rows)}


@router.post(
    "/purge-audio",
    summary="音声パージ (定期 cron 日次推奨、保持日数超の音声ファイルのみ削除)",
    response_model=PurgeAudioResponse,
)
async def purge_audio(
    db: DbDep,
    _admin: Annotated[User, Depends(require_role("admin"))],
) -> PurgeAudioResponse:
    """保持日数を超えた録音の音声を削除する (文字起こし・要約は残す).

    ``_run_audio_purge`` は **内部で commit する**。失敗時は rollback して 500 を
    返す (cron が再試行できるよう構造化エラー応答)。ついでに ``transcribing``
    のまま残った残骸ジョブも掃除する (設計 §10-4)。
    """
    settings = get_settings()
    try:
        await reap_stale_jobs(db)
        result = await _run_audio_purge(db, retention_days=settings.visit_audio_retention_days)
    except ValueError as exc:
        await db.rollback()
        logger.error("purge_audio rejected: %s", exc)
        # 422 (= 入力が不正) であって 500 ではない。ジョブは 1 バイトも消して
        # おらず、サーバーも壊れていない — 直すのは設定値の側。cron の失敗通知で
        # 500 (要調査) と区別が付くようにする。
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None
    except Exception:  # noqa: BLE001 — cron 向けに失敗を構造化応答へ集約する
        await db.rollback()
        logger.exception("purge_audio failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="purge-audio job failed",
        ) from None
    return PurgeAudioResponse(**result)
