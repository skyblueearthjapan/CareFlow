"""QR 訪問チェックイン Phase 5-6: GPS 座標の保持パージ (APPI / 2 年).

``visit_checkins`` の ``scanned_at`` が保持上限 (= 2 年、設計 §5-6 ユーザー判断
2026-06-30) を超えた行の ``lat`` / ``lng`` / ``accuracy_m`` を NULL 化する。
派生値の ``distance_m`` / ``match_status`` は **監査のため残す** (証跡保持)。

VPS cron 日次 03:00 想定で管理 API ``POST /api/v1/admin/checkin/purge-gps`` から
``run_gps_purge`` を実行する。冪等 (既に NULL の行は WHERE で除外され no-op)。
advisory lock で多重実行を排他し、自前で commit する (``services/geocoding/audit.py``
の実績パターンに倣う)。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.visit_checkin import VisitCheckin
from app.utils.db import try_advisory_xact_lock

logger = logging.getLogger(__name__)

# 保持上限 (2 年)。法定最低に整合 (設計 §5-6)。
DEFAULT_RETENTION_DAYS: Final = 730

# 保持の下限ガード (1 年)。誤設定で短すぎる retention を渡すと監査証跡を破壊する
# ため、これ未満は拒否する (security L-1)。
MIN_RETENTION_DAYS: Final = 365

# pg_try_advisory_xact_lock 用キー (bigint 範囲内)。purge-gps 専用。
# "CHKGPURG" 相当の 64-bit 整数。
PURGE_GPS_LOCK_KEY: Final = 0x43484B47_50555247


async def run_gps_purge(
    db: AsyncSession,
    *,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    now: datetime | None = None,
) -> dict[str, int | bool]:
    """保持上限を超えた checkin の lat/lng/accuracy_m を NULL 化する.

    返却 = ``{"locked", "purged"}``。``purged`` が NULL 化した行数。
    ``retention_days`` が下限 (= ``MIN_RETENTION_DAYS``) 未満なら ``ValueError``。
    """
    if retention_days < MIN_RETENTION_DAYS:
        raise ValueError(
            f"retention_days must be >= {MIN_RETENTION_DAYS} (got {retention_days})"
        )
    if not await try_advisory_xact_lock(db, PURGE_GPS_LOCK_KEY):
        logger.warning("purge_gps: another job holds advisory lock, skipping")
        return {"locked": True, "purged": 0}

    if now is None:
        now = datetime.now(UTC)
    cutoff = now - timedelta(days=retention_days)

    stmt = (
        update(VisitCheckin)
        .where(
            VisitCheckin.scanned_at < cutoff,
            # 既に全て NULL の行は対象外 (冪等・無駄な書き込み回避)。
            or_(
                VisitCheckin.lat.is_not(None),
                VisitCheckin.lng.is_not(None),
                VisitCheckin.accuracy_m.is_not(None),
            ),
        )
        .values(lat=None, lng=None, accuracy_m=None)
        .execution_options(synchronize_session=False)
    )
    result = await db.execute(stmt)
    purged = int(result.rowcount or 0)
    await db.commit()
    logger.info(
        "purge_gps: nulled lat/lng/accuracy for %d checkins (cutoff=%s)", purged, cutoff
    )
    return {"locked": False, "purged": purged}
