"""Admin-only endpoints — 音声記録の保持期間パージ (定期 cron 用) と利用状況集計.

正典設計書: ``docs/plans/visit-voice-record-design-2026-09-17.md`` §2-6 / §10-3 /
§11-3 (費用ダッシュボード)。

  * ``POST /api/v1/admin/visit-recordings/purge-audio`` — 保持日数
    (``VISIT_AUDIO_RETENTION_DAYS``) を超えた録音の **音声ファイルだけ** を消す。
    経過日数は **``created_at`` (サーバー受領時刻)** で数える (``recorded_at`` は
    端末の自己申告なので、時計が狂った端末が保持期間を飛び越えてしまう)。
  * ``GET  /api/v1/admin/visit-recordings/usage?month=YYYY-MM`` — 月次の件数・
    音声分・トークン・``cost_usd`` (read-only)。

``admin_checkin.purge-gps`` と同型: advisory lock で多重実行を排他し、冪等
(既に消えている行は WHERE で外れるので二度目は 0 件)。**文字起こし・要約は
消さない** — 消すのは音声バイナリだけで、行は ``audio_path=NULL`` +
``audio_deleted_at`` を立てて「パージ済み」と分かる形で残す。

保持日数の下限は 30 日 (設計 §10-1)。誤設定で短すぎる値を渡されたら記録を
焼く前に落とす (``purge.py`` の ``MIN_RETENTION_DAYS`` と同じ思想)。
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import DbDep, require_role
from app.models.staff import Staff
from app.models.user import User
from app.models.visit_recording import (
    RECORDING_STATUS_FAILED,
    RECORDING_STATUSES,
    VisitRecording,
)
from app.schemas.visit_recording import (
    VisitRecordingUsage,
    VisitRecordingUsageByStaff,
)
from app.services.checkin.judge import JST
from app.services.voice.jobs import reap_stale_jobs
from app.utils.db import try_advisory_xact_lock

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/visit-recordings", tags=["admin", "visit-recordings"])

# 保持の下限ガード (設計 §10-1「90（下限 30）」)。
MIN_RETENTION_DAYS: Final = 30

# pg_try_advisory_xact_lock 用キー ("VRECPURG" 相当)。音声パージ専用。
PURGE_AUDIO_LOCK_KEY: Final = 0x56524543_50555247

# 利用状況の月指定 (YYYY-MM)。
_MONTH_RE: Final = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


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


# ---------------------------------------------------------------------------
# 利用状況 (費用ダッシュボード)


def _month_bounds_utc(month: str) -> tuple[datetime, datetime]:
    """``'2026-09'`` → その月の JST 境界 ``[start, end)`` を UTC で返す.

    境界は **JST**。UTC で切ると月末の 9 時間 (JST の夕方以降) が隣の月に入り、
    現場が見ている暦と請求の月がずれる。

    Raises:
        ValueError: ``YYYY-MM`` 形式でない / 月が 01〜12 でない。
    """
    if not _MONTH_RE.match(month):
        raise ValueError("month は YYYY-MM 形式で指定してください")
    year, mon = int(month[:4]), int(month[5:7])
    start = datetime(year, mon, 1, tzinfo=JST)
    end = (
        datetime(year + 1, 1, 1, tzinfo=JST)
        if mon == 12
        else datetime(year, mon + 1, 1, tzinfo=JST)
    )
    return start.astimezone(UTC), end.astimezone(UTC)


def _as_float(value: Any) -> float:
    """``Numeric`` は Decimal で返る。JSON に出す前に float へ落とす."""
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _minutes(seconds: Any) -> float:
    """秒 → 分 (小数第 1 位)。画面は「何分録ったか」しか見ない."""
    return round(_as_float(seconds) / 60, 1)


@router.get(
    "/usage",
    response_model=VisitRecordingUsage,
    summary="音声記録の月次利用状況 (件数・分・トークン・費用・admin・read-only)",
)
async def get_visit_recording_usage(
    db: DbDep,
    _admin: Annotated[User, Depends(require_role("admin"))],
    month: Annotated[str | None, Query(description="YYYY-MM (既定 = JST の今月)")] = None,
) -> VisitRecordingUsage:
    """1 か月ぶんの利用状況を集計して返す (設計 §11-3 費用ダッシュボード).

    対象は ``deleted_at IS NULL`` の行だけ (消した録音の費用は既に払っているが、
    画面の「今月どれだけ録ったか」は生きている記録の話なので数えない)。期間は
    ``created_at`` (サーバー受領時刻) の JST 月境界 — ``recorded_at`` は端末の
    自己申告で、圏外の端末がまとめて送ると先月の分が今月の請求に化ける。
    """
    month = month or datetime.now(UTC).astimezone(JST).strftime("%Y-%m")
    try:
        start, end = _month_bounds_utc(month)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None

    in_month = (
        VisitRecording.deleted_at.is_(None),
        VisitRecording.created_at >= start,
        VisitRecording.created_at < end,
    )

    totals = (
        await db.execute(
            select(
                func.count(VisitRecording.id),
                func.coalesce(func.sum(VisitRecording.tokens_in), 0),
                func.coalesce(func.sum(VisitRecording.tokens_out), 0),
            ).where(*in_month)
        )
    ).one()

    # status 別。0 件の status も 0 で埋める (画面のバッジが月によって消えない)。
    by_status: dict[str, int] = dict.fromkeys(sorted(RECORDING_STATUSES), 0)
    for st, count in (
        await db.execute(
            select(VisitRecording.status, func.count(VisitRecording.id))
            .where(*in_month)
            .group_by(VisitRecording.status)
        )
    ).all():
        by_status[str(st)] = int(count)

    # スタッフ別。名前は outerjoin で引く (退職者の行も落とさない)。
    by_staff = [
        VisitRecordingUsageByStaff(
            staff_id=staff_id,
            staff_name=staff_name,
            recordings=int(count),
            minutes=_minutes(seconds),
            cost_usd=round(_as_float(cost), 6),
        )
        for staff_id, staff_name, count, seconds, cost in (
            await db.execute(
                select(
                    VisitRecording.staff_id,
                    Staff.name,
                    func.count(VisitRecording.id),
                    func.coalesce(func.sum(VisitRecording.duration_sec), 0),
                    func.sum(VisitRecording.cost_usd),
                )
                .outerjoin(Staff, Staff.id == VisitRecording.staff_id)
                .where(*in_month)
                .group_by(VisitRecording.staff_id, Staff.name)
                .order_by(func.count(VisitRecording.id).desc())
            )
        ).all()
    ]

    # 分と費用の **総計は内訳の合計** にする。それぞれを別々に丸めると、画面の
    # スタッフ別の行を足しても総計に 0.1 分 / 1e-6 ドル合わないことがあり、
    # 「どちらが正しいのか」を現場に考えさせてしまう (数字は必ず足して合うこと)。
    return VisitRecordingUsage(
        month=month,
        recordings=int(totals[0]),
        minutes_total=round(sum(s.minutes for s in by_staff), 1),
        tokens_in=int(totals[1]),
        tokens_out=int(totals[2]),
        cost_usd=round(sum(s.cost_usd for s in by_staff), 6),
        by_staff=by_staff,
        by_status=by_status,
        failed=by_status.get(RECORDING_STATUS_FAILED, 0),
    )
