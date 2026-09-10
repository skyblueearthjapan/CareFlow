"""月次 予実比較のバックグラウンド実行 (取得 → 突合 → ジョブ決着).

同期 export を 2 本直列に回すため実行は ~100s。フロント→バックエンドには
Cloudflare の ~100s 制限があり、リクエスト内でやり切ると 524 で切れる。
そこで API は ``KaipokeJob`` を ``running`` で立ててすぐ 202 を返し、実体はここで
走らせる (旧GAS 由来の「起動 → status ポーリング」パターン)。

## セッションの扱い

**リクエストの ``AsyncSession`` は使わない**。FastAPI は yield 依存 (DbDep) を
応答送出の前に閉じるため、応答後に触ると閉じたセッションを使うことになる。
``get_session_factory()`` から自前のセッションを開き、その中で決着させる。

起動に ``BackgroundTasks`` を使う (``asyncio.create_task`` ではない) のも同じ理由で、
「リクエストのセッションが閉じてから」実行順が保証されるため。create_task だと
両セッションが同時に生きる瞬間ができ、コネクションを共有する構成では
片方の後始末がもう片方のトランザクションを巻き込む。

## 失敗しても必ず決着させる

例外で黙って死ぬと、ジョブが ``running`` のまま残り「実行中」の表示が消えない。
どの経路で落ちてもジョブを ``failed`` にし、``result_summary.error`` に日本語の
理由を残す (どの区分=予定/実績 で落ちたかも ``division`` に入れる)。
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.db.session import get_session_factory
from app.models.kaipoke_job import KaipokeJob
from app.services.kaipoke.plan_actual_compare import build_plan_actual_report
from app.services.kaipoke.plan_actual_fetch import fetch_month_snapshots
from app.services.kaipoke_client import KaipokeApiError, KaipokeBusyError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.services.kaipoke_client import KaipokeClient

logger = logging.getLogger(__name__)

#: ``KaipokeJob.params["op"]`` の値。二重起動チェック / 先取りクローズ除外に使う。
OP = "plan-actual-compare"

_BUSY_MESSAGE = (
    "カイポケが別の処理を実行中のため中断しました。"
    "実行中の処理が終わってから、もう一度お試しください。"
)


async def _settle_failed(
    db: AsyncSession,
    job_id: uuid.UUID,
    *,
    month: str,
    error: str,
    division: str | None,
) -> None:
    """ジョブを failed で決着させる (直前の失敗トランザクションは捨ててから書く)。"""
    await db.rollback()
    job = await db.get(KaipokeJob, job_id)
    if job is None:  # pragma: no cover — 取得直後に消えるのは想定外
        logger.warning("plan-actual-compare job %s vanished before failure was recorded", job_id)
        return
    job.status = "failed"
    job.completed_at = datetime.now(UTC)
    job.result_summary = {"month": month, "error": error, "division": division}
    await db.commit()


def build_result_summary(
    *,
    month: str,
    office_id: uuid.UUID | None,
    plan_snapshot_id: uuid.UUID,
    actual_snapshot_id: uuid.UUID,
    report: Any,
) -> dict[str, Any]:
    """完了時の ``result_summary`` (csv_content は入れない — 正典はスナップショット)。"""
    return {
        "month": month,
        "office_id": str(office_id) if office_id else None,
        "plan_snapshot_id": str(plan_snapshot_id),
        "actual_snapshot_id": str(actual_snapshot_id),
        "plan_rows": report.plan_rows,
        "actual_rows": report.actual_rows,
        "events_skipped": report.events_skipped,
        "malformed_rows": report.malformed_rows,
        "counts": report.counts,
        "by_staff": [
            {"staff": s.staff, "counts": s.counts, "duplicates": s.duplicates, "total": s.total}
            for s in report.by_staff
        ],
    }


async def run_plan_actual_job(
    *,
    job_id: uuid.UUID,
    month: str,
    office_id: uuid.UUID | None,
    client: KaipokeClient,
    credentials: dict[str, str] | None = None,
) -> None:
    """予定/実績を取得して突合し、``job_id`` のジョブを completed/failed で決着させる。

    自前のセッションで動く (リクエストのセッションは応答時点で閉じている)。
    例外は握り潰さずログに出したうえで、必ずジョブを failed にしてから返る。
    テストからは ``await run_plan_actual_job(...)`` で直接叩ける。
    """
    factory = get_session_factory()
    async with factory() as db:
        try:
            plan_snap, actual_snap = await fetch_month_snapshots(
                db,
                office_id=office_id,
                month=month,
                client=client,
                credentials=credentials,
            )
            report = build_plan_actual_report(
                month=month,
                plan_csv_text=plan_snap.csv_text,
                actual_csv_text=actual_snap.csv_text,
                plan_fetched_at=plan_snap.fetched_at,
                actual_fetched_at=actual_snap.fetched_at,
            )
            summary = build_result_summary(
                month=month,
                office_id=office_id,
                plan_snapshot_id=plan_snap.id,
                actual_snapshot_id=actual_snap.id,
                report=report,
            )
            job = await db.get(KaipokeJob, job_id)
            if job is None:  # pragma: no cover — 取得直後に消えるのは想定外
                logger.warning("plan-actual-compare job %s vanished before completion", job_id)
                await db.rollback()
                return
            job.status = "completed"
            job.completed_at = datetime.now(UTC)
            job.result_summary = summary
            await db.commit()
        except KaipokeBusyError:
            # 途中で誰かが apply などを走らせた → 片肺のスナップショットは残さない。
            logger.warning("plan-actual-compare job %s aborted: kaipoke busy", job_id)
            await _settle_failed(db, job_id, month=month, error=_BUSY_MESSAGE, division=None)
        except KaipokeApiError as exc:
            # KaipokeExportError も派生 (予定/実績 どちらで落ちたかを持つ)。
            logger.warning("plan-actual-compare job %s failed: %s", job_id, exc)
            await _settle_failed(
                db,
                job_id,
                month=month,
                error=str(exc),
                division=getattr(exc, "division", None),
            )
        except Exception as exc:  # noqa: BLE001 — 黙って死なせない (running が残る)
            logger.exception("plan-actual-compare job %s crashed", job_id)
            await _settle_failed(
                db,
                job_id,
                month=month,
                error=f"予実比較の実行に失敗しました: {exc}",
                division=None,
            )
