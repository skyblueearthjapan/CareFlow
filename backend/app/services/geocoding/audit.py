"""Phase G-5: lat/lng 整合性 自動モニタリング (Task #144).

全 alive 患者 (= ``patients.deleted_at IS NULL``) の ``address`` を Google Maps
Geocoding API で再評価し、 現在の ``(lat, lng)`` から ``MIN_DRIFT_KM`` (= 0.1km
= 100m) 以上ずれていれば自動で UPDATE する.

設計判断 (Codex + Opus クロスレビュー、 G-5):

  * **エンドポイント方式** (= ``POST /api/v1/admin/geocoding/audit``) を採用.
    APScheduler を backend に組み込むと uvicorn worker / container の二重実行
    リスクがあるため、 host VPS 側 cron が叩く形にする.
  * **PostgreSQL advisory lock** (``pg_try_advisory_xact_lock(ADVISORY_LOCK_KEY)``)
    で並列実行を排他する. 取れなかったら 「既に他のジョブが走っている」 とし
    ``locked=True`` を返して即時 return. transaction 単位の lock なので commit /
    rollback で自動解放され、 lock leak のリスクが無い.
  * **dry-run** (= ``dry_run=True``) で actual UPDATE を skip し、 差分プレビュー
    のみ返す.
  * **confidence ガード**: Google geocoding の精度が ``GEOMETRIC_CENTER`` /
    ``APPROXIMATE`` の場合は信頼性が低いため update しない (= ``confidence
    locations only`` フラグで wave). 今回は MVP として ``GeocodeResult`` の
    ``status`` (= "OK") のみ trust し、 確信度フィルタは TODO で残す.
  * **audit_logs**: ``action="auto_geocode_correction"``, ``target_table="patients"``,
    ``target_id=<patient.code>``, ``before={"lat": ..., "lng": ...}``,
    ``after={"lat": ..., "lng": ..., "distance_km": ..., "formatted_address": ...}``.
  * **rate limit**: 各 Geocoding 呼び出しの間に 50ms sleep を入れる (= 約 20
    QPS). Google の制限は 50 QPS (= secondary, 標準). 86 件 × 50ms = 4.3 秒.
  * **timeout**: 1 回の audit job は ``TIMEOUT_SECONDS=300`` (5 分) で打ち切る.

呼び出し方:
  POST /api/v1/admin/geocoding/audit
  Body: {"dry_run": false}   # default false
  Response:
    {
      "locked": false,
      "checked": 86,
      "corrected": 3,
      "skipped_unchanged": 80,
      "errors": 0,
      "details": [
        {"code": "P003", "name": "井川 裕太", "drift_km": 7.32,
         "before": {"lat": 35.68, "lng": 139.77},
         "after": {"lat": 35.65, "lng": 140.11}},
        ...
      ]
    }
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, Final
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.patient import Patient
from app.services.allocation.utils import haversine_km
from app.services.geocoding.client import (
    GeocodingQuotaExceeded,
    GeocodingServiceError,
    geocode_address,
)

logger = logging.getLogger(__name__)

# 修正閾値: 100m. 1km 以下は GPS / geocoding 精度誤差として無視する選択肢もあるが、
# 数百 m ずれていると visit allocation の移動距離計算に影響するため厳しめ.
MIN_DRIFT_KM: Final = 0.1

# 1 回の geocoding 間 sleep (= 50ms = 約 20 QPS). Google API は 50 QPS が安全圏.
RATE_LIMIT_SLEEP_SECONDS: Final = 0.05

# Audit job 全体の timeout (= 5 分). 86 件 × 50ms = 4.3 秒 + Google レイテンシ.
# 万一遅延しても 5 分で打ち切る.
TIMEOUT_SECONDS: Final = 300.0

# pg_try_advisory_xact_lock 用. PostgreSQL bigint (= signed 64-bit) 範囲内の
# 整数を 1 つ予約する. 値は "GCOD_AUDI" ASCII を 64-bit に詰めたもの
# (= 5,134,422,566,117,853,257 = 約 5.1 * 10^18、 bigint max = 9.2 * 10^18 内).
# transaction 単位の lock なので commit / rollback で自動解放される.
ADVISORY_LOCK_KEY: Final = 0x47434F44_41554449  # "GCOD_AUDI" を 64-bit に


class AuditResult(dict):
    """Type-friendly TypedDict ライク wrapper (BaseModel 化は API 層で)."""


async def _try_advisory_lock(db: AsyncSession) -> bool:
    """pg_try_advisory_xact_lock を取得 (= transaction-level lock).

    transaction が終了 (commit / rollback) すると自動的に解放されるため、
    手動 release 不要 + lock leak のリスクが無い.

    PostgreSQL 以外 (= sqlite test 環境) では常に True を返す (= 重複実行は
    本番でのみ排他、 test では 1 process なので不要).

    Returns:
        True if lock acquired (or non-PG dialect), False if another job holds it.
    """
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return True
    row = (
        await db.execute(text(f"SELECT pg_try_advisory_xact_lock({ADVISORY_LOCK_KEY})"))
    ).scalar()
    return bool(row)


async def run_lat_lng_audit(
    db: AsyncSession,
    *,
    actor_user_id: UUID | None = None,
    dry_run: bool = False,
    limit_patients: int | None = None,
) -> dict[str, Any]:
    """全 alive 患者の lat/lng 整合性を audit して必要なら自動補正する.

    Args:
        db: AsyncSession (lock 取得 + commit する).
        actor_user_id: audit_logs に actor として記録する user id. None = 自動 (NULL).
        dry_run: True なら UPDATE せず差分のみ集計.
        limit_patients: テスト用に対象患者数を絞る (None = 無制限).

    Returns:
        AuditResult dict (locked / checked / corrected / skipped / errors / details).
    """
    # 並列実行排他
    if not await _try_advisory_lock(db):
        logger.warning("lat_lng_audit: another job holds advisory lock, skipping")
        return {
            "locked": True,
            "checked": 0,
            "corrected": 0,
            "skipped_unchanged": 0,
            "errors": 0,
            "details": [],
        }

    started_at = datetime.now(UTC)
    checked = 0
    corrected = 0
    skipped = 0
    errors = 0
    details: list[dict[str, Any]] = []

    try:
        q = select(Patient).where(Patient.deleted_at.is_(None)).order_by(Patient.code)
        if limit_patients is not None:
            q = q.limit(limit_patients)
        patients = (await db.scalars(q)).all()
        logger.info(f"lat_lng_audit: starting (dry_run={dry_run}, target={len(patients)})")

        for p in patients:
            # 全体 timeout
            elapsed = (datetime.now(UTC) - started_at).total_seconds()
            if elapsed > TIMEOUT_SECONDS:
                logger.warning(
                    f"lat_lng_audit: timeout after {elapsed:.1f}s, "
                    f"processed {checked}/{len(patients)}"
                )
                break

            if not p.address or not p.address.strip():
                continue
            checked += 1

            try:
                result = await geocode_address(p.address)
            except (GeocodingQuotaExceeded, GeocodingServiceError) as e:
                logger.warning(f"lat_lng_audit: geocode failed for {p.code}: {e}")
                errors += 1
                # quota は短いバックオフで一旦継続を試みる
                await asyncio.sleep(0.2)
                continue
            except Exception as e:  # noqa: BLE001
                logger.exception(f"lat_lng_audit: unexpected error for {p.code}: {e}")
                errors += 1
                continue

            if result is None:
                # ZERO_RESULTS — 住所が解決できない. 元の lat/lng を保持.
                continue

            new_lat = round(result.lat, 7)
            new_lng = round(result.lng, 7)
            old_lat = float(p.lat) if p.lat is not None else None
            old_lng = float(p.lng) if p.lng is not None else None

            if old_lat is None or old_lng is None:
                # 旧 lat/lng が NULL なら無条件で set (= 修正対象).
                drift_km = float("inf")
            else:
                drift_km = haversine_km(old_lat, old_lng, new_lat, new_lng)

            if drift_km < MIN_DRIFT_KM:
                skipped += 1
            else:
                # 補正対象
                corrected += 1
                detail = {
                    "code": p.code,
                    "name": p.name,
                    "drift_km": round(drift_km, 3) if drift_km != float("inf") else None,
                    "before": {"lat": old_lat, "lng": old_lng},
                    "after": {
                        "lat": new_lat,
                        "lng": new_lng,
                        "formatted_address": result.formatted_address,
                    },
                }
                details.append(detail)

                if not dry_run:
                    p.lat = new_lat
                    p.lng = new_lng
                    # audit_log に記録
                    db.add(
                        AuditLog(
                            actor_user_id=actor_user_id,
                            action="auto_geocode_correction",
                            target_table="patients",
                            target_id=p.code or str(p.id),
                            before={"lat": old_lat, "lng": old_lng},
                            after={
                                "lat": new_lat,
                                "lng": new_lng,
                                "drift_km": detail["drift_km"],
                                "formatted_address": result.formatted_address,
                            },
                        )
                    )

            await asyncio.sleep(RATE_LIMIT_SLEEP_SECONDS)

        try:
            if not dry_run and corrected > 0:
                await db.commit()
                logger.info(f"lat_lng_audit: committed {corrected} corrections")
            else:
                # dry-run or 0 件 — rollback (advisory_xact_lock も同時解放される)
                await db.rollback()
                tail = "(no commit)" if dry_run else "(no changes)"
                logger.info(f"lat_lng_audit: dry_run={dry_run}, corrected={corrected} {tail}")
        except Exception:
            # commit 失敗時は明示 rollback (= advisory_xact_lock も解放される)
            await db.rollback()
            raise

    finally:
        # advisory_xact_lock は transaction の commit/rollback で自動解放されるため
        # 明示 unlock 不要. (旧コードの finally release は lock leak リスクがあったため撤去)
        pass

    return {
        "locked": False,
        "checked": checked,
        "corrected": corrected,
        "skipped_unchanged": skipped,
        "errors": errors,
        "details": details,
    }
