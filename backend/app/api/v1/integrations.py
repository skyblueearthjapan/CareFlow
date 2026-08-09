"""Integrations / 連携センター endpoints — Phase 5-1 Wave 2-B + Wave 4-A.

Exposes Kaipoke fetch/push job management plus admin-only views over the
geocoding cache. Wave 4-A adds the actual
relay endpoints to the existing kaipoke-api (Flask + Playwright) so the
連携センター画面 can drive expand/export/diff/apply jobs end-to-end.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.deps import DbDep, require_role
from app.models.correction_sheet import CorrectionSheet, CorrectionSheetItem
from app.models.geocoding_cache import GeocodingCache
from app.models.kaipoke_job import KaipokeJob, KaipokeJobItem
from app.models.user import User
from app.schemas._pagination import Paginated
from app.schemas.integrations import (
    CorrectionBulkSelect,
    CorrectionItemRead,
    CorrectionItemUpdate,
    CorrectionSheetRead,
    DiffAccepted,
    EventsInboundApplyItemRead,
    EventsInboundApplyRequest,
    EventsInboundApplyResult,
    EventsInboundChange,
    EventsInboundPreviewRead,
    EventsInboundPreviewRequest,
    EventsInboundUnmatchedRead,
    ExpandStatusRead,
    GeneratedCsvRead,
    GeocodingCacheRead,
    InboundApplyRequest,
    InboundApplyResult,
    InboundEligibilityRead,
    InboundItemResultRead,
    InboundSnapshotListRead,
    InboundSnapshotRead,
    IntegrationApplyRequest,
    IntegrationDiffRequest,
    IntegrationExpandRequest,
    IntegrationExportRequest,
    JobAccepted,
    JobItemPatch,
    KaipokeCredentialsRead,
    KaipokeCredentialsUpdate,
    KaipokeJobCreate,
    KaipokeJobRead,
    KaipokeLoginTestResult,
    KaipokeStatusRead,
    LiveSnapshotRead,
    ReplaceInboundRequest,
    ReplaceInboundResult,
    ReplaceInboundSkipRead,
    ReplaceInboundTraineeSoloRead,
    SmartInboundApplyRequest,
    SmartInboundApplyResult,
    SmartInboundPreviewRead,
    SmartInboundPreviewRequest,
    SnapshotRestoreResultRead,
    WeekScheduleRead,
    WeekScheduleRow,
)
from app.services.kaipoke_client import (
    KaipokeApiError,
    KaipokeBusyError,
    KaipokeClient,
    get_kaipoke_client,
)

router = APIRouter()


async def _commit_or_409(db) -> None:
    """Commit and translate IntegrityError into 409/422 (W1-C pattern)."""
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        msg = str(exc).lower()
        if "unique" in msg or "duplicate" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Conflict: duplicate value",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Validation error: invalid foreign key",
        ) from exc


# --- Kaipoke jobs ----------------------------------------------------------


@router.get(
    "/kaipoke/jobs",
    response_model=Paginated[KaipokeJobRead],
    summary="List Kaipoke jobs",
)
async def list_kaipoke_jobs(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    week_start: Annotated[date | None, Query(description="Filter by week_start")] = None,
    job_status: Annotated[str | None, Query(alias="status")] = None,
    job_type: Annotated[str | None, Query(alias="type")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Paginated[KaipokeJobRead]:
    conditions = []
    if week_start is not None:
        conditions.append(KaipokeJob.week_start == week_start)
    if job_status is not None:
        conditions.append(KaipokeJob.status == job_status)
    if job_type is not None:
        conditions.append(KaipokeJob.job_type == job_type)

    base = select(KaipokeJob)
    count_stmt = select(func.count()).select_from(KaipokeJob)
    if conditions:
        base = base.where(and_(*conditions))
        count_stmt = count_stmt.where(and_(*conditions))

    stmt = (
        base.options(selectinload(KaipokeJob.items))
        .order_by(KaipokeJob.created_at.desc())
        .limit(limit)
        .offset(offset)
    )

    rows = (await db.scalars(stmt)).all()
    total = (await db.scalar(count_stmt)) or 0
    return Paginated[KaipokeJobRead](
        items=[KaipokeJobRead.model_validate(j, from_attributes=True) for j in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.get(
    "/kaipoke/jobs/{job_id}",
    response_model=KaipokeJobRead,
    summary="Get Kaipoke job detail (with items)",
)
async def get_kaipoke_job(
    job_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> KaipokeJobRead:
    job = await db.scalar(
        select(KaipokeJob).where(KaipokeJob.id == job_id).options(selectinload(KaipokeJob.items))
    )
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return KaipokeJobRead.model_validate(job, from_attributes=True)


@router.post(
    "/kaipoke/jobs",
    response_model=KaipokeJobRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new Kaipoke job (admin)",
)
async def create_kaipoke_job(
    payload: KaipokeJobCreate,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin"))],
) -> KaipokeJobRead:
    job = KaipokeJob(
        job_type=payload.job_type,
        week_start=payload.week_start,
        params=payload.params,
        status="pending",
        created_by_user_id=user.id,
    )
    db.add(job)
    await db.flush()

    await _commit_or_409(db)

    refreshed = await db.scalar(
        select(KaipokeJob).where(KaipokeJob.id == job.id).options(selectinload(KaipokeJob.items))
    )
    assert refreshed is not None
    return KaipokeJobRead.model_validate(refreshed, from_attributes=True)


@router.post(
    "/kaipoke/jobs/{job_id}/cancel",
    response_model=KaipokeJobRead,
    summary="Cancel a pending/running Kaipoke job (admin)",
)
async def cancel_kaipoke_job(
    job_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> KaipokeJobRead:
    # Atomic state-transition: only flip pending/running -> cancelled and rely
    # on rowcount to decide between 404 (no row at all) and 409 (already in a
    # terminal state). This avoids the SELECT-then-UPDATE race with the
    # background worker that promotes running -> completed.
    now = datetime.now(UTC)
    stmt = (
        update(KaipokeJob)
        .where(
            KaipokeJob.id == job_id,
            KaipokeJob.status.in_(("pending", "running")),
        )
        .values(
            status="cancelled",
            completed_at=func.coalesce(KaipokeJob.completed_at, now),
        )
        .execution_options(synchronize_session=False)
    )
    result = await db.execute(stmt)
    if result.rowcount == 0:
        # Distinguish 404 from 409: was the row missing or simply terminal?
        existing = await db.scalar(select(KaipokeJob.status).where(KaipokeJob.id == job_id))
        if existing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel job in status '{existing}'",
        )

    await _commit_or_409(db)

    refreshed = await db.scalar(
        select(KaipokeJob).where(KaipokeJob.id == job_id).options(selectinload(KaipokeJob.items))
    )
    assert refreshed is not None
    return KaipokeJobRead.model_validate(refreshed, from_attributes=True)


# --- Geocoding cache (admin only) -----------------------------------------


@router.get(
    "/geocoding/cache",
    response_model=Paginated[GeocodingCacheRead],
    summary="List geocoding cache entries (admin)",
)
async def list_geocoding_cache(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    q: Annotated[str | None, Query(description="Substring filter on address")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Paginated[GeocodingCacheRead]:
    base = select(GeocodingCache)
    count_stmt = select(func.count()).select_from(GeocodingCache)
    if q:
        base = base.where(GeocodingCache.address.ilike(f"%{q}%"))
        count_stmt = count_stmt.where(GeocodingCache.address.ilike(f"%{q}%"))

    stmt = base.order_by(GeocodingCache.looked_up_at.desc()).limit(limit).offset(offset)
    rows = (await db.scalars(stmt)).all()
    total = (await db.scalar(count_stmt)) or 0
    return Paginated[GeocodingCacheRead](
        items=[GeocodingCacheRead.model_validate(r, from_attributes=True) for r in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


# --- Wave 4-A: kaipoke relay -----------------------------------------------


def _kaipoke_dep() -> KaipokeClient:
    """FastAPI dependency wrapper around `get_kaipoke_client()`.

    Pulled out so that tests can override via `app.dependency_overrides`.
    """
    return get_kaipoke_client()


async def _kaipoke_credentials(db) -> dict[str, str] | None:
    """アプリ内設定のカイポケ認証情報 (C-1)。未設定は None (RPA は env フォールバック)。"""
    from app.services.kaipoke.credentials import get_credentials_payload

    return await get_credentials_payload(db)


def _attach_credentials(body: dict[str, Any], creds: dict[str, str] | None) -> None:
    """RPA への HTTP body にのみ認証情報を同梱する。

    **KaipokeJob.params には絶対に入れない** (DB に平文が残るため)。
    audit ミドルウェアは password キーを redact 済み。
    """
    if creds:
        body["credentials"] = creds


def _month_to_week_start(month: str) -> date:
    """Map "YYYY-MM" -> first day of that month (used as KaipokeJob.week_start)."""
    return date.fromisoformat(f"{month}-01")


async def _persist_job_after_kaipoke_call(
    db,
    *,
    job: KaipokeJob,
    kaipoke_response: dict[str, Any],
    started_at: datetime | None = None,
) -> None:
    """Mark a job as running and store the upstream jobId/payload."""
    job.status = "running"
    job.started_at = started_at or datetime.now(UTC)
    summary = dict(job.result_summary or {})
    upstream_id = (
        kaipoke_response.get("jobId")
        or kaipoke_response.get("job_id")
        or kaipoke_response.get("id")
    )
    if upstream_id:
        summary["kaipoke_job_id"] = str(upstream_id)
    summary["kaipoke_response"] = kaipoke_response
    job.result_summary = summary
    await _commit_or_409(db)


@router.get(
    "/status",
    response_model=KaipokeStatusRead,
    summary="Get live kaipoke status + last DB job (admin)",
)
async def get_integration_status(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    kaipoke: Annotated[KaipokeClient, Depends(_kaipoke_dep)],
) -> KaipokeStatusRead:
    raw: dict[str, Any] = {}
    reachable = True
    err: str | None = None
    try:
        raw = await kaipoke.status()
    except KaipokeApiError as exc:
        reachable = False
        err = str(exc)

    last = await db.scalar(
        select(KaipokeJob)
        .where(KaipokeJob.status == "running")
        .options(selectinload(KaipokeJob.items))
        .order_by(KaipokeJob.started_at.desc().nullslast())
        .limit(1)
    )
    if last is None:
        last = await db.scalar(
            select(KaipokeJob)
            .options(selectinload(KaipokeJob.items))
            .order_by(KaipokeJob.created_at.desc())
            .limit(1)
        )

    last_sync_at = None
    raw_last = raw.get("lastSyncAt") or raw.get("last_sync_at")
    if isinstance(raw_last, str):
        try:
            last_sync_at = datetime.fromisoformat(raw_last.replace("Z", "+00:00"))
        except ValueError:
            last_sync_at = None
    login_remain = raw.get("loginRemainSec") or raw.get("login_remain_sec")

    return KaipokeStatusRead(
        kaipoke=raw,
        login_remain_sec=int(login_remain) if isinstance(login_remain, (int, float)) else None,
        last_sync_at=last_sync_at,
        running_job=KaipokeJobRead.model_validate(last, from_attributes=True) if last else None,
        reachable=reachable,
        error=err,
    )


_ACTIVE_JOB_STATUSES = ("pending", "running")


async def _notify_apply_result(db, job: KaipokeJob) -> None:
    """apply(本番反映)が失敗/スキップを含んで決着したら管理者へ通知を残す。

    非同期の apply は完了時に実行者が別画面にいる可能性が高い。トースト(揮発)では
    見逃すため、既存の通知基盤(ベル+未読バッジ)に恒久的な通知を1件作る。
    reference_id=job.id で冪等 (reconcile が複数回走っても1件)。成功のみの時は作らない。
    """
    from app.services.checkin.notify import _active_admin_manager_users, _create_idempotent

    result = (job.result_summary or {}).get("result") or {}
    failed = int(result.get("failed") or 0)
    skipped = int(result.get("skipped") or 0)
    is_failed = job.status == "failed"
    if not (is_failed or failed > 0 or skipped > 0):
        return  # 全件成功 → 通知不要 (要対応がある時だけ通知する)

    month = (job.params or {}).get("month") or ""
    if is_failed:
        title = "カイポケ反映が失敗しました"
        body = f"{month} の反映がエラーで終了しました。カイポケ連携画面で確認してください。"
    else:
        parts = []
        if failed > 0:
            parts.append(f"失敗{failed}件")
        if skipped > 0:
            parts.append(f"スキップ{skipped}件")
        title = f"カイポケ反映に要確認（{'・'.join(parts)}）"
        body = (
            f"{month} の反映は完了しましたが {('・'.join(parts))} があります。"
            "未登録のまま放置しないよう、カイポケ連携画面で内訳を確認してください。"
        )

    users = await _active_admin_manager_users(db)
    await _create_idempotent(
        db,
        users=users,
        type_="kaipoke_apply_result",
        reference_type="kaipoke_apply",
        reference_id=job.id,
        title=title,
        body=body,
    )


async def _reconcile_latest_job(
    db, *, kaipoke_idle: bool, result_payload: dict[str, Any] | None
) -> KaipokeJob | None:
    """Close the newest still-open job once kaipoke reports idle.

    kaipoke-api is single-slot and has no per-job callback, so the DB job stays
    `running` until a poll observes the worker gone idle. This lazily settles it
    to completed/failed using the worker's last result summary. Idempotent: only
    pending/running rows are touched, and a no-op returns the row unchanged.
    """
    job = await db.scalar(
        select(KaipokeJob)
        .options(selectinload(KaipokeJob.items))
        .order_by(KaipokeJob.created_at.desc())
        .limit(1)
    )
    if job is None or job.status not in _ACTIVE_JOB_STATUSES:
        return job
    if not kaipoke_idle:
        return job

    payload = result_payload or {}
    if payload.get("status") == "error" or payload.get("error"):
        job.status = "failed"
        job.result_summary = {**(job.result_summary or {}), "error": payload.get("error")}
    elif not payload:
        # Worker idle but no result surfaced (result lost from the ring buffer,
        # or the op — e.g. expand — has no /result endpoint). Mark completed but
        # flag it so the UI/audit can tell "confirmed done" from "assumed done".
        job.status = "completed"
        job.result_summary = {**(job.result_summary or {}), "result_unknown": True}
    else:
        job.status = "completed"
        result = payload.get("result")
        if isinstance(result, dict):
            trimmed = {k: v for k, v in result.items() if k != "csv_content"}
            job.result_summary = {**(job.result_summary or {}), "result": trimmed}
    job.completed_at = datetime.now(UTC)

    # apply(実書込)ジョブが決着したら CorrectionSheet の状態も同期させる:
    # applying → completed で applied / failed で failed。早計な applied を避け、
    # 実際にカイポケ処理が終わってから確定する。
    params = job.params or {}
    if params.get("op") == "apply" and not params.get("dry_run"):
        if params.get("sheet_id"):
            sheet = await db.scalar(
                select(CorrectionSheet).where(
                    CorrectionSheet.id == _safe_uuid(params.get("sheet_id"))
                )
            )
            if sheet is not None and sheet.status == "applying":
                sheet.status = "applied" if job.status == "completed" else "failed"
        # 失敗/スキップがあれば実行管理者(admin/manager)へ通知を残す。反映後の
        # 失敗/スキップを見逃して放置しないための恒久的な気づき (トーストと違い消えない)。
        await _notify_apply_result(db, job)

    await _commit_or_409(db)

    # commit() expires the instance; re-select with items eagerly loaded so the
    # caller can model_validate() without triggering a lazy load outside the
    # async greenlet (would raise MissingGreenlet during serialization).
    return await db.scalar(
        select(KaipokeJob).where(KaipokeJob.id == job.id).options(selectinload(KaipokeJob.items))
    )


@router.get(
    "/live",
    response_model=LiveSnapshotRead,
    summary="Live single-slot worker snapshot for the monitor UI (admin)",
)
async def get_live_snapshot(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    kaipoke: Annotated[KaipokeClient, Depends(_kaipoke_dep)],
    tail: Annotated[int, Query(ge=1, le=1000)] = 60,
) -> LiveSnapshotRead:
    settings = get_settings()

    try:
        raw = await kaipoke.status()
    except KaipokeApiError as exc:
        return LiveSnapshotRead(reachable=False, error=str(exc), monitor_url=None)

    task = raw.get("current_task") or {}
    command = task.get("command")
    running = bool(task.get("running"))

    phase = processed = total = current_name = None
    success = failed = skipped = None
    result_payload: dict[str, Any] | None = None

    # Pull richer progress/result for the op-specific poll endpoints.
    try:
        if command == "apply" or (not running):
            ap = await kaipoke.apply_result()
            if ap.get("status") == "running":
                prog = ap.get("progress") or {}
                phase = prog.get("phase")
                processed = prog.get("processed")
                total = prog.get("total")
                current_name = prog.get("current_name")
                success, failed, skipped = (
                    prog.get("success"),
                    prog.get("failed"),
                    prog.get("skipped"),
                )
            elif ap.get("status") in {"completed", "error"}:
                result_payload = ap
                res = ap.get("result") or {}
                success, failed, skipped = (
                    res.get("success"),
                    res.get("failed"),
                    res.get("skipped"),
                )
        if command == "export" or (result_payload is None and not running):
            ep = await kaipoke.export_result()
            if ep.get("status") in {"completed", "error"} and result_payload is None:
                result_payload = ep
    except KaipokeApiError:
        pass

    log_lines: list[str] = []
    try:
        log_resp = await kaipoke.logs(tail=tail)
        raw_lines = log_resp.get("lines")
        if isinstance(raw_lines, list):
            log_lines = [str(x) for x in raw_lines]
    except KaipokeApiError:
        pass

    latest = await _reconcile_latest_job(
        db, kaipoke_idle=not running, result_payload=result_payload
    )

    return LiveSnapshotRead(
        reachable=True,
        running=running,
        command=command,
        phase=phase,
        processed=processed,
        total=total,
        current_name=current_name,
        success=success,
        failed=failed,
        skipped=skipped,
        logs=log_lines,
        monitor_url=settings.kaipoke_novnc_url,
        latest_job=(
            KaipokeJobRead.model_validate(latest, from_attributes=True) if latest else None
        ),
        error=None,
    )


@router.get(
    "/monitor-url",
    response_model=dict,
    summary="noVNC live monitor URL (admin)",
)
async def get_monitor_url(
    _user: Annotated[User, Depends(require_role("admin"))],
) -> dict[str, str]:
    return {"url": get_settings().kaipoke_novnc_url}


@router.post(
    "/reconcile-jobs",
    response_model=dict,
    summary="実行中ジョブの決着を確定し失敗/スキップを通知 (定期 cron 用・admin)",
)
async def reconcile_jobs(
    db: DbDep,
    _admin: Annotated[User, Depends(require_role("admin"))],
    kaipoke: Annotated[KaipokeClient, Depends(_kaipoke_dep)],
) -> dict[str, Any]:
    """kaipoke が idle なら未決着ジョブを completed/failed へ確定し、apply の
    失敗/スキップを管理者へ通知する (VPS cron 用)。

    連携画面を開いていなくても通知が確実に作られるよう、/live ポーリングに
    依存しない経路として cron から数分毎に叩く。冪等 (何度呼んでも二重通知しない)。
    """
    try:
        raw = await kaipoke.status()
    except KaipokeApiError as exc:
        return {"reachable": False, "settled": False, "error": str(exc)}

    task = raw.get("current_task") or {}
    running = bool(task.get("running"))
    result_payload: dict[str, Any] | None = None
    if not running:
        try:
            ap = await kaipoke.apply_result()
            if ap.get("status") in {"completed", "error"}:
                result_payload = ap
            else:
                ep = await kaipoke.export_result()
                if ep.get("status") in {"completed", "error"}:
                    result_payload = ep
        except KaipokeApiError:
            pass

    job = await _reconcile_latest_job(db, kaipoke_idle=not running, result_payload=result_payload)
    settled = job is not None and job.status not in _ACTIVE_JOB_STATUSES
    return {"reachable": True, "running": running, "settled": settled}


@router.get(
    "/generated-csv",
    response_model=GeneratedCsvRead,
    summary="CareFlow visits から生成したカイポケ18列CSV (admin)",
)
async def get_generated_csv(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    month: Annotated[str, Query(pattern=r"^\d{4}-\d{2}$")],
    office_id: Annotated[UUID | None, Query()] = None,
) -> GeneratedCsvRead:
    """対象月の確定 visits を18列CSV へ生成して返す (差分適用の最適化CSV側)。

    read-only (DB 書込なし)。utf-8-sig で返し UI プレビュー/DL に使う (実 apply 時の
    kaipoke 転記は別途 cp932)。将来のローカル差分の入力にもなる。
    """
    from app.services.kaipoke.csv_builder import BuildOptions, build_month_csv

    year, mon = int(month[:4]), int(month[5:7])
    opts = BuildOptions(year=year, month=mon, office_id=office_id)
    data = await build_month_csv(db, opts, encoding="utf-8-sig")
    text = data.decode("utf-8-sig")
    # 行数 = ヘッダー除く (空末尾行を除去)。
    row_count = max(0, len([ln for ln in text.splitlines() if ln.strip()]) - 1)
    return GeneratedCsvRead(month=month, row_count=row_count, csv_content=text)


@router.get(
    "/week-schedule",
    response_model=WeekScheduleRead,
    summary="対象週の CareFlow スケジュール (週ビュー表示用・admin)",
)
async def get_week_schedule(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    week_start: Annotated[str, Query(pattern=r"^\d{4}-\d{2}-\d{2}$", alias="weekStart")],
    week_end: Annotated[str, Query(pattern=r"^\d{4}-\d{2}-\d{2}$", alias="weekEnd")],
    office_id: Annotated[UUID | None, Query(alias="officeId")] = None,
) -> WeekScheduleRead:
    """対象週の確定 visits を週ビュー用の構造化データで返す (read-only)。

    現場が見慣れたコース別表示 (行=コース × 列=曜日) のため、visit.course_id →
    courses.code (A/B/..) と office 名を join して各行に付与する。
    """
    from app.models.course import Course
    from app.models.office import Office
    from app.models.staff import Staff
    from app.models.visit import Visit
    from app.models.visit_staff_assignment import VisitStaffAssignment

    ws = date.fromisoformat(week_start)
    we = date.fromisoformat(week_end)

    stmt = (
        select(Visit)
        .where(
            Visit.deleted_at.is_(None),
            Visit.visit_date >= ws,
            Visit.visit_date <= we,
            Visit.status != "cancelled",
        )
        .options(selectinload(Visit.patient))
        .order_by(Visit.visit_date, Visit.start_time)
    )
    visits = list((await db.scalars(stmt)).all())

    visit_ids = [v.id for v in visits]

    # 担当スタッフの正典は visit_staff_assignments + courses.assigned_staff_id。
    # visits.primary_staff_id は「レガシー互換」欄で、自動割当の一部経路や一斉未割当で
    # 未同期のことがある (実データ W28 は primary_staff_id がほぼ NULL・割当は
    # visit_staff_assignments 側に存在)。本体スケジュール画面と同じソースで解決し、
    # primary_staff_id は最後のフォールバックにする。
    assignments_by_visit: dict = {}
    if visit_ids:
        for vsa in (
            await db.scalars(
                select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id.in_(visit_ids))
            )
        ).all():
            assignments_by_visit.setdefault(vsa.visit_id, []).append(vsa.staff_id)

    course_ids = {v.course_id for v in visits if v.course_id}
    course_map: dict = {}
    if course_ids:
        course_map = {
            c.id: c
            for c in (await db.scalars(select(Course).where(Course.id.in_(course_ids)))).all()
        }

    # 全経路のスタッフ ID をまとめて一括ロード (割当 / コース担当 / レガシー欄)。
    staff_ids: set = set()
    for v in visits:
        staff_ids |= set(assignments_by_visit.get(v.id, []))
        if v.primary_staff_id:
            staff_ids.add(v.primary_staff_id)
        if v.secondary_staff_id:
            staff_ids.add(v.secondary_staff_id)
    for c in course_map.values():
        if c.assigned_staff_id:
            staff_ids.add(c.assigned_staff_id)
    staff_map = {}
    if staff_ids:
        staff_map = {
            s.id: s for s in (await db.scalars(select(Staff).where(Staff.id.in_(staff_ids)))).all()
        }

    office_ids = {c.office_id for c in course_map.values() if c.office_id}
    office_ids |= {v.patient.primary_office_id for v in visits if v.patient}
    office_ids.discard(None)
    office_map: dict = {}
    if office_ids:
        office_map = {
            o.id: o
            for o in (await db.scalars(select(Office).where(Office.id.in_(office_ids)))).all()
        }

    def _name(sid) -> str:
        s = staff_map.get(sid)
        return s.name if s else ""

    def _resolve_staff(v, course) -> tuple[str, str]:
        """visit の担当を (staff1, staff2) で解決。

        優先順: ① visit_staff_assignments (正典) ② courses.assigned_staff_id
        ③ visits.primary/secondary_staff_id (レガシー)。staff1 はコース担当を先頭に。
        """
        assigned = list(assignments_by_visit.get(v.id, []))
        course_staff = course.assigned_staff_id if course else None
        ordered: list = []
        # コース担当を先頭 (本体スケジュールのコース担当表示と一致させる)。
        if course_staff is not None:
            ordered.append(course_staff)
        for sid in assigned:
            if sid not in ordered:
                ordered.append(sid)
        # 割当もコース担当も無ければレガシー欄にフォールバック。
        if not ordered:
            for sid in (v.primary_staff_id, v.secondary_staff_id):
                if sid is not None and sid not in ordered:
                    ordered.append(sid)
        return (
            _name(ordered[0]) if len(ordered) >= 1 else "",
            _name(ordered[1]) if len(ordered) >= 2 else "",
        )

    rows: list[WeekScheduleRow] = []
    for v in visits:
        patient = v.patient
        # primary_staff_id が未同期でも予定自体は表示する (担当は正典ソースで解決)。
        if patient is None:
            continue
        if office_id is not None and patient.primary_office_id != office_id:
            continue
        course = course_map.get(v.course_id)
        course_code = course.code if course else ""
        office = (
            office_map.get(course.office_id)
            if course
            else office_map.get(patient.primary_office_id)
        )
        office_name = office.name if office else ""
        staff1, staff2 = _resolve_staff(v, course)
        rows.append(
            WeekScheduleRow(
                visit_date=v.visit_date.isoformat(),
                weekday=v.visit_date.weekday(),
                start_time=f"{v.start_time.hour:02d}:{v.start_time.minute:02d}",
                end_time=f"{v.end_time.hour:02d}:{v.end_time.minute:02d}",
                patient_name=patient.name,
                patient_sex=patient.sex,
                staff1=staff1,
                staff2=staff2,
                course_code=course_code,
                office_name=office_name,
            )
        )
    return WeekScheduleRead(week_start=week_start, week_end=week_end, rows=rows)


@router.get(
    "/expand-status",
    response_model=ExpandStatusRead,
    summary="対象月の展開状況 (展開は月1回・2回目ブロック判定用・admin)",
)
async def get_expand_status(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    month: Annotated[str, Query(pattern=r"^\d{4}-\d{2}$")],
) -> ExpandStatusRead:
    """CareFlow の実行履歴 (KaipokeJob) から、その月が展開済みかを判定する。

    展開は月1回・2回目は既存を上書きするため、UI 側で再展開をブロックする判断に使う。
    完了済みの expand ジョブ (params.op=expand, params.month=month) があれば展開済み。
    """
    # pending も含める: 展開ジョブは作成直後 pending で flush される。二重展開を
    # 防ぐため、投入直後でも「展開済み」扱いにする（安全側）。
    job = await db.scalar(
        select(KaipokeJob)
        .where(
            KaipokeJob.params["op"].astext == "expand",
            KaipokeJob.params["month"].astext == month,
            KaipokeJob.status.in_(("completed", "running", "pending")),
        )
        .order_by(KaipokeJob.created_at.desc())
        .limit(1)
    )
    return ExpandStatusRead(
        month=month,
        expanded=job is not None,
        expanded_at=(job.completed_at or job.started_at) if job else None,
        job_id=job.id if job else None,
    )


@router.post(
    "/expand",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger a kaipoke expand job (admin)",
)
async def trigger_expand(
    payload: IntegrationExpandRequest,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin"))],
    kaipoke: Annotated[KaipokeClient, Depends(_kaipoke_dep)],
) -> JobAccepted:
    job = KaipokeJob(
        job_type="fetch",
        week_start=_month_to_week_start(payload.month),
        params={"op": "expand", "month": payload.month, "dry_run": payload.dry_run},
        status="pending",
        created_by_user_id=user.id,
    )
    db.add(job)
    await db.flush()

    # /api/expand は同期で 15-20 分ブロックし、frontend→backend は Cloudflare の
    # ~100s 制限もある。短い timeout で投げ、Timeout(504) は「起動した」とみなして
    # 202 running を返す (kaipoke は走り続け、ライブモニターが完了を reconcile する)。
    # 旧GAS の「524→status ポーリング」パターンの移植。
    body: dict[str, Any] = {"month": payload.month, "dryRun": payload.dry_run}
    _attach_credentials(body, await _kaipoke_credentials(db))
    try:
        upstream = await kaipoke.expand(body, timeout=25.0)
    except KaipokeBusyError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="kaipoke busy") from exc
    except KaipokeApiError as exc:
        if exc.status_code == 504:
            # タイムアウト = 展開はバックグラウンドで継続中。エラーにせず running 扱い。
            job.status = "running"
            job.started_at = datetime.now(UTC)
            job.result_summary = {"note": "expand running (timeout tolerated)"}
            await _commit_or_409(db)
            return JobAccepted(job_id=job.id, kaipoke_job_id=None, status="running")
        job.status = "failed"
        job.completed_at = datetime.now(UTC)
        job.result_summary = {"error": str(exc), "body": exc.body}
        await _commit_or_409(db)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    await _persist_job_after_kaipoke_call(db, job=job, kaipoke_response=upstream)
    return JobAccepted(
        job_id=job.id,
        kaipoke_job_id=str(upstream.get("jobId") or upstream.get("job_id") or "") or None,
        status="running",
    )


@router.post(
    "/export",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger a kaipoke export job (admin)",
)
async def trigger_export(
    payload: IntegrationExportRequest,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin"))],
    kaipoke: Annotated[KaipokeClient, Depends(_kaipoke_dep)],
) -> JobAccepted:
    job = KaipokeJob(
        job_type="fetch",
        week_start=_month_to_week_start(payload.month),
        params={"op": "export", "month": payload.month, "format": payload.format},
        status="pending",
        created_by_user_id=user.id,
    )
    db.add(job)
    await db.flush()

    # async=true: kaipoke returns immediately and runs export in a background
    # thread (a sync export blocks ~50s, over the client's 30s timeout). The
    # UI then polls GET /integrations/live for progress + completion.
    export_body: dict[str, Any] = {"month": payload.month, "format": payload.format, "async": True}
    _attach_credentials(export_body, await _kaipoke_credentials(db))
    try:
        upstream = await kaipoke.export(export_body)
    except KaipokeBusyError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="kaipoke busy") from exc
    except KaipokeApiError as exc:
        job.status = "failed"
        job.completed_at = datetime.now(UTC)
        job.result_summary = {"error": str(exc), "body": exc.body}
        await _commit_or_409(db)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    await _persist_job_after_kaipoke_call(db, job=job, kaipoke_response=upstream)
    return JobAccepted(
        job_id=job.id,
        kaipoke_job_id=str(upstream.get("jobId") or upstream.get("job_id") or "") or None,
        status="running",
    )


@router.post(
    "/diff",
    response_model=DiffAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger kaipoke diff and persist a CorrectionSheet (admin)",
)
async def trigger_diff(
    payload: IntegrationDiffRequest,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin"))],
    kaipoke: Annotated[KaipokeClient, Depends(_kaipoke_dep)],
) -> DiffAccepted:
    job = KaipokeJob(
        job_type="fetch",
        week_start=_month_to_week_start(payload.month),
        params={"op": "diff", "month": payload.month},
        status="pending",
        created_by_user_id=user.id,
    )
    db.add(job)
    await db.flush()

    diff_body: dict[str, Any] = {"month": payload.month}
    _attach_credentials(diff_body, await _kaipoke_credentials(db))
    try:
        upstream = await kaipoke.diff(diff_body)
    except KaipokeBusyError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="kaipoke busy") from exc
    except KaipokeApiError as exc:
        job.status = "failed"
        job.completed_at = datetime.now(UTC)
        job.result_summary = {"error": str(exc), "body": exc.body}
        await _commit_or_409(db)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    raw_items = upstream.get("items") or upstream.get("corrections") or []
    if not isinstance(raw_items, list):
        raw_items = []

    sheet = CorrectionSheet(
        target_month=payload.month,
        status="ready",
        created_by_user_id=user.id,
    )
    db.add(sheet)
    await db.flush()

    summary, items_to_add = _build_correction_items(sheet.id, raw_items)
    db.add_all(items_to_add)

    job.status = "completed"
    job.completed_at = datetime.now(UTC)
    job.result_summary = {
        "sheet_id": str(sheet.id),
        "summary": summary,
        "kaipoke_response": upstream,
    }
    await _commit_or_409(db)

    return DiffAccepted(job_id=job.id, sheet_id=sheet.id, summary=summary)


def _build_correction_items(
    sheet_id: UUID,
    raw_items: list[dict[str, Any]],
) -> tuple[dict[str, int], list[CorrectionSheetItem]]:
    """Transform kaipoke /diff items into CorrectionSheetItem rows.

    Implements the *delete+add → companion_change* coalescing rule: if the
    same patient_id has a `delete` and an `add` whose dates are within ±1
    day of each other, fold both rows into a single `companion_change`.
    """
    from collections import defaultdict

    summary: dict[str, int] = defaultdict(int)
    items: list[CorrectionSheetItem] = []

    # Bucket by patient_id so we can detect the delete/add coalesce window.
    by_patient: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
    for r in raw_items:
        if not isinstance(r, dict):
            continue
        by_patient[str(r.get("patient_id")) if r.get("patient_id") else None].append(r)

    consumed: set[int] = set()  # indices already merged
    for pid, group in by_patient.items():
        for i, row in enumerate(group):
            if id(row) in consumed:
                continue
            action = row.get("action")
            row_date = _safe_iso_date(row.get("date") or (row.get("after") or {}).get("date"))
            if action == "delete" and pid:
                # Look for a matching add within ±1 day.
                match = None
                for j, other in enumerate(group):
                    if j == i or id(other) in consumed:
                        continue
                    if other.get("action") != "add":
                        continue
                    other_date = _safe_iso_date(
                        other.get("date") or (other.get("after") or {}).get("date")
                    )
                    if row_date and other_date and abs((other_date - row_date).days) <= 1:
                        match = other
                        break
                if match is not None:
                    items.append(
                        CorrectionSheetItem(
                            sheet_id=sheet_id,
                            patient_id=_safe_uuid(pid),
                            visit_id=_safe_uuid(row.get("visit_id")),
                            action="companion_change",
                            before=row.get("before") or row,
                            after=match.get("after") or match,
                            include=True,
                        )
                    )
                    consumed.add(id(row))
                    consumed.add(id(match))
                    summary["companion_change"] += 1
                    continue
            # Default: pass through verbatim.
            act_str = str(action or "update")
            items.append(
                CorrectionSheetItem(
                    sheet_id=sheet_id,
                    patient_id=_safe_uuid(pid),
                    visit_id=_safe_uuid(row.get("visit_id")),
                    action=act_str,
                    before=row.get("before"),
                    after=row.get("after"),
                    include=True,
                )
            )
            consumed.add(id(row))
            summary[act_str] += 1

    summary["total"] = len(items)
    return dict(summary), items


def _safe_iso_date(v: Any) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _safe_uuid(v: Any) -> UUID | None:
    if not v:
        return None
    try:
        return UUID(str(v))
    except (TypeError, ValueError):
        return None


@router.post(
    "/diff-local",
    response_model=DiffAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="CareFlow visits vs kaipoke 現況のローカル差分→CorrectionSheet (admin)",
)
async def trigger_diff_local(
    payload: IntegrationDiffRequest,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin"))],
    kaipoke: Annotated[KaipokeClient, Depends(_kaipoke_dep)],
) -> DiffAccepted:
    """差分の「正」を CareFlow に一本化する統合エンドポイント (K-2b)。

    現況(kaipoke同期export) と 最適化(CareFlow visits生成) を CareFlow 内で突合し、
    Correction を CorrectionSheet 化する。利用者名は name_match で patient へ解決
    (未解決は patient_id=None のまま可視化)。同期 export のため ~50s かかる。
    """
    from collections import defaultdict

    from app.models.patient import Patient
    from app.services.kaipoke.local_diff import build_local_diff, correction_before_after
    from app.services.kaipoke.name_match import build_name_index, match_name

    job = KaipokeJob(
        job_type="fetch",
        week_start=_month_to_week_start(payload.month),
        params={"op": "diff-local", "month": payload.month},
        status="pending",
        created_by_user_id=user.id,
    )
    db.add(job)
    await db.flush()

    try:
        corrections, meta = await build_local_diff(
            db,
            month=payload.month,
            kaipoke=kaipoke,
            office_id=payload.office_id,
            week_start=payload.week_start,
            week_end=payload.week_end,
            credentials=await _kaipoke_credentials(db),
        )
    except KaipokeBusyError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="kaipoke busy") from exc
    except KaipokeApiError as exc:
        job.status = "failed"
        job.completed_at = datetime.now(UTC)
        job.result_summary = {"error": str(exc), "body": exc.body}
        await _commit_or_409(db)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except Exception as exc:
        # CSV生成/差分中の予期しない例外もジョブに監査記録してから 500。
        await db.rollback()
        db.add(job)
        job.status = "failed"
        job.completed_at = datetime.now(UTC)
        job.result_summary = {"error": f"local diff failed: {exc}"}
        await _commit_or_409(db)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="local diff failed"
        ) from exc

    # 利用者名 → patient_id の索引 (active のみ)。
    patients = (await db.scalars(select(Patient).where(Patient.deleted_at.is_(None)))).all()
    pindex = build_name_index({str(p.id): p.name for p in patients})

    # apply実績ゲート (逆反映) の判定材料として週レンジをシートに刻む。
    sheet_week_end = payload.week_end or (
        payload.week_start + timedelta(days=6) if payload.week_start else None
    )
    sheet = CorrectionSheet(
        target_month=payload.month,
        status="ready",
        direction="outbound",
        week_start=payload.week_start,
        week_end=sheet_week_end,
        created_by_user_id=user.id,
    )
    db.add(sheet)
    await db.flush()

    summary: dict[str, int] = defaultdict(int)
    items: list[CorrectionSheetItem] = []
    unresolved = 0
    for c in corrections:
        pid_str = match_name(c.user_name, pindex)
        pid = UUID(pid_str) if pid_str else None
        if pid is None:
            unresolved += 1
        before, after = correction_before_after(c)
        items.append(
            CorrectionSheetItem(
                sheet_id=sheet.id,
                patient_id=pid,
                visit_id=None,
                action=c.action,
                before=before,
                after=after,
                include=True,
            )
        )
        summary[c.action] += 1
    summary["total"] = len(items)
    summary["unresolved_patient"] = unresolved
    db.add_all(items)

    job.status = "completed"
    job.completed_at = datetime.now(UTC)
    job.result_summary = {"sheet_id": str(sheet.id), "summary": dict(summary), **meta}
    await _commit_or_409(db)

    return DiffAccepted(job_id=job.id, sheet_id=sheet.id, summary=dict(summary))


@router.post(
    "/apply",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Apply selected CorrectionItems back to kaipoke (admin)",
)
async def trigger_apply(
    payload: IntegrationApplyRequest,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin"))],
    kaipoke: Annotated[KaipokeClient, Depends(_kaipoke_dep)],
) -> JobAccepted:
    sheet = await db.scalar(
        select(CorrectionSheet)
        .where(CorrectionSheet.id == payload.sheet_id)
        .options(selectinload(CorrectionSheet.items))
    )
    if sheet is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sheet not found")
    if sheet.direction == "inbound":
        # 逆反映シートをカイポケへ押すと取り込み内容が往復して壊れる。専用APIへ。
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="inbound sheet must be applied via /integrations/apply-inbound",
        )

    # 二重書込ガード (不可逆): 適用済みは常に拒否、適用中は実書込を拒否。
    # dry-run(状態変更なし)は applying でなければ許可。
    if sheet.status == "applied":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="sheet already applied")
    if sheet.status == "applying" and not payload.dry_run:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="apply already in progress"
        )

    selected = [it for it in sheet.items if it.include]
    if not selected:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No items selected (include=true)",
        )

    from app.services.kaipoke.local_diff import item_to_kaipoke_correction

    job = KaipokeJob(
        job_type="push",
        week_start=sheet.week_start or date.fromisoformat(f"{sheet.target_month}-01"),
        params={
            "op": "apply",
            "sheet_id": str(sheet.id),
            "dry_run": payload.dry_run,
            # apply実績ゲート (逆反映・real_apply_record) の判定キー。
            "week_start": sheet.week_start.isoformat() if sheet.week_start else None,
        },
        status="pending",
        created_by_user_id=user.id,
    )
    db.add(job)
    await db.flush()

    # CorrectionSheetItem(before/after dict) → カイポケ /api/apply の平坦 Correction 形式。
    # カイポケ側は Correction(**item) で復元するためキーを厳密一致させる (item_to_kaipoke_correction)。
    correction_data = [
        item_to_kaipoke_correction(it.action, it.before, it.after) for it in selected
    ]
    # 安全: 職員1が空(未割当)の修正数を数え、監査に残す (カイポケでは '-' 登録になる)。
    unassigned = sum(1 for c in correction_data if not c["staff1_to"] and c["action"] != "delete")

    body = {
        "correction_data": correction_data,
        "month": sheet.target_month,
        "dry_run": payload.dry_run,
        "headed": True,
    }
    _attach_credentials(body, await _kaipoke_credentials(db))

    try:
        upstream = await kaipoke.apply(body)
    except KaipokeBusyError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="kaipoke busy") from exc
    except KaipokeApiError as exc:
        job.status = "failed"
        job.completed_at = datetime.now(UTC)
        job.result_summary = {"error": str(exc), "body": exc.body}
        await _commit_or_409(db)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    await _persist_job_after_kaipoke_call(db, job=job, kaipoke_response=upstream)
    job.result_summary = {
        **(job.result_summary or {}),
        "correction_count": len(correction_data),
        "unassigned_staff": unassigned,
        "dry_run": payload.dry_run,
    }
    if not payload.dry_run:
        # 非同期のため投入時は中間状態。実際の完了は _reconcile_latest_job が
        # job 完了を観測して applied/failed へ遷移させる (早計な applied を避ける)。
        sheet.status = "applying"
    await _commit_or_409(db)

    return JobAccepted(
        job_id=job.id,
        kaipoke_job_id=str(upstream.get("jobId") or upstream.get("job_id") or "") or None,
        status="running",
    )


# --- 接続設定: カイポケ ログイン情報 (C-1・汎用化) ---------------------------
# docs/plans/kaipoke-credentials-config-design.md — ログイン情報をアプリ内設定に。


@router.get(
    "/credentials",
    response_model=KaipokeCredentialsRead,
    summary="カイポケ ログイン情報の設定状態 (パスワード非返却・admin)",
)
async def get_kaipoke_credentials(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> KaipokeCredentialsRead:
    from app.services.kaipoke.credentials import read_credential

    row = await read_credential(db)
    if row is None:
        return KaipokeCredentialsRead(configured=False)
    return KaipokeCredentialsRead(
        configured=True,
        corp_id=row.corp_id,
        user_id=row.user_id,
        updated_at=row.updated_at,
    )


@router.put(
    "/credentials",
    response_model=KaipokeCredentialsRead,
    summary="カイポケ ログイン情報を暗号化保存 (admin)",
)
async def put_kaipoke_credentials(
    payload: KaipokeCredentialsUpdate,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin"))],
) -> KaipokeCredentialsRead:
    from app.services.kaipoke.credentials import read_credential, upsert_credential

    await upsert_credential(
        db,
        corp_id=payload.corp_id.strip(),
        user_id=payload.user_id.strip(),
        password=payload.password,
        updated_by_user_id=user.id,
    )
    await _commit_or_409(db)
    # commit 後の期限切れ属性 (server onupdate の updated_at) への同期アクセスは
    # MissingGreenlet を起こすため re-select する (K-1a の既出パターン)。
    row = await read_credential(db)
    return KaipokeCredentialsRead(
        configured=row is not None,
        corp_id=row.corp_id if row else None,
        user_id=row.user_id if row else None,
        updated_at=row.updated_at if row else None,
    )


@router.post(
    "/credentials/test",
    response_model=KaipokeLoginTestResult,
    summary="保存済みログイン情報でカイポケへ実ログインを試す (同期 ~60s・admin)",
)
async def test_kaipoke_credentials(
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin"))],
    kaipoke: Annotated[KaipokeClient, Depends(_kaipoke_dep)],
) -> KaipokeLoginTestResult:
    """RPA の単一スロットを短時間占有して実ログインを1回試す (noVNC で目視可)。"""
    creds = await _kaipoke_credentials(db)
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="ログイン情報が未設定です (先に保存してください)",
        )
    job = KaipokeJob(
        job_type="fetch",
        week_start=datetime.now(UTC).date(),
        params={"op": "login-test"},
        status="pending",
        created_by_user_id=user.id,
    )
    db.add(job)
    await db.flush()
    try:
        upstream = await kaipoke.login_test({"credentials": creds}, timeout=120.0)
    except KaipokeBusyError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="kaipoke busy") from exc
    except KaipokeApiError as exc:
        job.status = "failed"
        job.completed_at = datetime.now(UTC)
        job.result_summary = {"error": str(exc), "body": exc.body}
        await _commit_or_409(db)
        return KaipokeLoginTestResult(ok=False, message=f"接続テスト失敗: {exc}")

    ok = bool(upstream.get("ok") or upstream.get("status") == "ok")
    message = str(upstream.get("message") or ("ログイン成功" if ok else "ログイン失敗"))
    job.status = "completed" if ok else "failed"
    job.completed_at = datetime.now(UTC)
    job.result_summary = {"ok": ok, "message": message}
    await _commit_or_409(db)
    return KaipokeLoginTestResult(ok=ok, message=message)


# --- 逆反映: カイポケ → CareFlow (R-1/R-2) ----------------------------------
# docs/plans/kaipoke-reverse-sync-design.md — 「週のバトンリレー」の取り込み側。


async def _build_inbound_sheet(
    db,
    *,
    corrections,
    month: str,
    week_start: date,
    week_end: date,
    user_id: UUID | None,
):
    """inbound Correction 群を名寄せ解決して CorrectionSheet + items に永続化する。

    trigger_diff_inbound と smart-inbound-preview (2026-07-26) の共通部。
    Returns: (sheet, summary dict)。commit は呼び出し側の責務。
    """
    from collections import defaultdict

    from app.models.patient import Patient
    from app.services.kaipoke.inbound import (
        day_to_date,
        load_staff_name_index,
        load_week_visit_index,
        parse_hhmm,
    )
    from app.services.kaipoke.local_diff import correction_before_after
    from app.services.kaipoke.name_match import build_name_index, match_name

    # 利用者名 → patient_id、担当名 → staff_id、(patient, date, start) → visit_id の解決。
    patients = (await db.scalars(select(Patient).where(Patient.deleted_at.is_(None)))).all()
    pindex = build_name_index({str(p.id): p.name for p in patients})
    sindex, _smap = await load_staff_name_index(db)
    visit_index = await load_week_visit_index(db, week_start, week_end)

    sheet = CorrectionSheet(
        target_month=month,
        status="ready",
        direction="inbound",
        week_start=week_start,
        week_end=week_end,
        created_by_user_id=user_id,
    )
    db.add(sheet)
    await db.flush()

    summary: dict[str, int] = defaultdict(int)
    items: list[CorrectionSheetItem] = []
    unresolved = 0
    for c in corrections:
        pid_str = match_name(c.user_name, pindex)
        pid = UUID(pid_str) if pid_str else None
        if pid is None:
            unresolved += 1
        before, after = correction_before_after(c)

        # before 側 (CareFlow の現在地) から対象 visit を解決する。
        visit_id = None
        if pid is not None and c.action != "add":
            try:
                day = int(str(before.get("date")))
            except (TypeError, ValueError):
                day = -1
            target_date = day_to_date(day, week_start, week_end) if day > 0 else None
            start = parse_hhmm(str(before.get("start_time") or ""))
            if target_date is not None and start is not None:
                v = visit_index.get((pid, target_date, start))
                visit_id = v.id if v is not None else None

        # 既定 include (設計 §8): キャンセル/変更 = 対象 visit まで特定できたもの。
        # add = 患者と担当が名寄せ解決できたもの (コースは臨時新設で常に解決可能)。
        # 未解決は OFF で可視化 (人が判断)。
        if c.action == "add":
            include = pid is not None and bool(
                match_name(str((after or {}).get("staff1") or ""), sindex)
            )
        else:
            include = c.action in ("delete", "edit", "date_change") and visit_id is not None
        items.append(
            CorrectionSheetItem(
                sheet_id=sheet.id,
                patient_id=pid,
                visit_id=visit_id,
                action=c.action,
                before=before,
                after=after,
                include=include,
            )
        )
        summary[c.action] += 1
    summary["total"] = len(items)
    summary["unresolved_patient"] = unresolved
    summary["auto_selected"] = sum(1 for it in items if it.include)
    db.add_all(items)
    return sheet, summary


@router.get(
    "/inbound-eligibility",
    response_model=InboundEligibilityRead,
    summary="対象週が取り込み可能か (時間ゲート or apply実績) を判定 (admin)",
)
async def inbound_eligibility(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    week_start: date = Query(..., alias="weekStart"),
) -> InboundEligibilityRead:
    """過去週・今週は無条件で開放 / 未来週は実apply記録が必要 (2026-07-26 PO確定)。"""
    from app.services.kaipoke.inbound import inbound_week_eligible

    eligible, job = await inbound_week_eligible(db, week_start)
    return InboundEligibilityRead(
        week_start=week_start,
        eligible=eligible,
        last_applied_at=job.completed_at if job else None,
    )


@router.get(
    "/inbound-snapshots",
    response_model=InboundSnapshotListRead,
    summary="取り込み前スナップショットの一覧 (週単位・admin)",
)
async def list_inbound_snapshots(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    week_start: date = Query(..., alias="weekStart"),
) -> InboundSnapshotListRead:
    """対象週の「取り込み前に戻す」候補 (直近 5 世代・新しい順)。"""
    from app.models.inbound_snapshot import InboundSnapshot

    rows = (
        await db.scalars(
            select(InboundSnapshot)
            .where(InboundSnapshot.week_start == week_start)
            .order_by(InboundSnapshot.created_at.desc(), InboundSnapshot.id.desc())
        )
    ).all()
    return InboundSnapshotListRead(snapshots=[InboundSnapshotRead.model_validate(r) for r in rows])


@router.post(
    "/inbound-snapshots/{snapshot_id}/restore",
    response_model=SnapshotRestoreResultRead,
    summary="取り込み前の盤面へ復元する (admin)",
)
async def restore_inbound_snapshot(
    snapshot_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> SnapshotRestoreResultRead:
    """週を白紙化してスナップショットの盤面を書き戻す (PO 決定 2026-08-09)。

    「間違えて取り込んでも、取り込む前に戻せる」の実行部。
    打刻ガード: 打刻の付いた週は 422 (実績の紐付け先を消さない)。
    """
    from app.models.inbound_snapshot import InboundSnapshot
    from app.services.kaipoke.inbound_snapshot import (
        SnapshotRestoreBlockedError,
        restore_snapshot,
    )

    snap = await db.get(InboundSnapshot, snapshot_id)
    if snap is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="snapshot not found")
    try:
        result = await restore_snapshot(db, snap, now=datetime.now(UTC))
    except SnapshotRestoreBlockedError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    await _commit_or_409(db)
    return SnapshotRestoreResultRead(
        wiped=result.wiped,
        restored=result.restored,
        courses_restored=result.courses_restored,
        courses_removed=result.courses_removed,
    )


@router.post(
    "/diff-inbound",
    response_model=DiffAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="カイポケ現況 → CareFlow の逆向き差分を計算 (read-only・admin)",
)
async def trigger_diff_inbound(
    payload: IntegrationDiffRequest,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin"))],
    kaipoke: Annotated[KaipokeClient, Depends(_kaipoke_dep)],
) -> DiffAccepted:
    """提供中の週にカイポケ側で入った直し込みを差分として可視化する (書込なし)。

    diff-local の逆向き: before=CareFlow / after=カイポケ現況。
    apply実績ゲートを通った週のみ許可。visit_id まで解決して inbound シートに永続化。
    """

    from app.services.kaipoke.inbound import (
        inbound_week_eligible,
    )
    from app.services.kaipoke.local_diff import build_local_diff

    if payload.week_start is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="weekStart is required for inbound diff",
        )
    week_start = payload.week_start
    week_end = payload.week_end or (week_start + timedelta(days=6))

    # 取り込みゲート (2026-07-26 改訂): 過去週・今週は無条件開放。未来週のみ
    # 実apply記録が必要 (計画中の週を取り込む「週全滅事故」の防止)。
    eligible, _record = await inbound_week_eligible(db, week_start)
    if not eligible:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "未来の週はまだ取り込めません (計画中の週を消してしまう事故防止のため、"
                "先に④反映を実行した週のみ取り込めます)"
            ),
        )

    job = KaipokeJob(
        job_type="fetch",
        week_start=week_start,
        params={
            "op": "diff-inbound",
            "month": payload.month,
            "week_start": week_start.isoformat(),
        },
        status="pending",
        created_by_user_id=user.id,
    )
    db.add(job)
    await db.flush()

    try:
        corrections, meta = await build_local_diff(
            db,
            month=payload.month,
            kaipoke=kaipoke,
            office_id=payload.office_id,
            week_start=week_start,
            week_end=week_end,
            direction="inbound",
            credentials=await _kaipoke_credentials(db),
        )
    except KaipokeBusyError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="kaipoke busy") from exc
    except KaipokeApiError as exc:
        job.status = "failed"
        job.completed_at = datetime.now(UTC)
        job.result_summary = {"error": str(exc), "body": exc.body}
        await _commit_or_409(db)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except Exception as exc:
        await db.rollback()
        db.add(job)
        job.status = "failed"
        job.completed_at = datetime.now(UTC)
        job.result_summary = {"error": f"inbound diff failed: {exc}"}
        await _commit_or_409(db)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="inbound diff failed"
        ) from exc

    sheet, summary = await _build_inbound_sheet(
        db,
        corrections=corrections,
        month=payload.month,
        week_start=week_start,
        week_end=week_end,
        user_id=user.id,
    )

    job.status = "completed"
    job.completed_at = datetime.now(UTC)
    job.result_summary = {"sheet_id": str(sheet.id), "summary": dict(summary), **meta}
    await _commit_or_409(db)

    return DiffAccepted(job_id=job.id, sheet_id=sheet.id, summary=dict(summary))


@router.post(
    "/apply-inbound",
    response_model=InboundApplyResult,
    summary="inbound シートを CareFlow visits へ適用 (キャンセル/時刻変更・admin)",
)
async def trigger_apply_inbound(
    payload: InboundApplyRequest,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin"))],
) -> InboundApplyResult:
    """カイポケ側の直し込みを CareFlow の予定表へ書き写す (同期・ローカル・RPA不使用)。

    dry_run=True (既定) は一切書き込まず予定される結果だけ返す。
    実適用はキャンセル (status='cancelled') と時刻/日付変更 (source='manual_week') のみ。
    days 指定で曜日チップの複数選択 (指定日以外は対象外)。
    """
    from app.services.kaipoke.inbound import apply_inbound_items

    sheet = await db.scalar(
        select(CorrectionSheet)
        .where(CorrectionSheet.id == payload.sheet_id)
        .options(selectinload(CorrectionSheet.items))
        # applied チェックの TOCTOU (同時2リクエストの二重適用) を行ロックで防ぐ。
        .with_for_update(of=CorrectionSheet)
    )
    if sheet is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sheet not found")
    if sheet.direction != "inbound":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="outbound sheet must be applied via /integrations/apply",
        )
    if sheet.status == "applied":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="sheet already applied")
    if sheet.week_start is None or sheet.week_end is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="sheet has no week range"
        )

    selected = [it for it in sheet.items if it.include]
    if not selected:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No items selected (include=true)",
        )

    now = datetime.now(UTC)
    if not payload.dry_run:
        # 取り込み前スナップショット (PO 決定 2026-08-09: 「取り込み前に戻す」用)。
        # 取り込みと同一トランザクション = 適用が失敗すれば残らない。
        from app.services.kaipoke.inbound_snapshot import snapshot_week

        await snapshot_week(db, sheet.week_start, kind="diff", user_id=user.id)
    summary = await apply_inbound_items(
        db,
        items=selected,
        week_start=sheet.week_start,
        week_end=sheet.week_end,
        days=payload.days,
        dry_run=payload.dry_run,
        now=now,
    )

    job_id: UUID | None = None
    if payload.dry_run:
        # dry-run は mutate しない (apply_inbound_items も no-write)。明示 rollback。
        await db.rollback()
    else:
        # 同期・ローカル適用のためジョブは即 completed で監査記録する。
        job = KaipokeJob(
            job_type="fetch",
            week_start=sheet.week_start,
            params={
                "op": "apply-inbound",
                "sheet_id": str(sheet.id),
                "week_start": sheet.week_start.isoformat(),
                "days": [d.isoformat() for d in payload.days] if payload.days else None,
            },
            status="completed",
            started_at=now,
            completed_at=now,
            created_by_user_id=user.id,
        )
        job.result_summary = {k: v for k, v in summary.as_dict().items() if k != "results"}
        db.add(job)
        sheet.status = "applied"
        # 失敗を含む決着は恒久通知も残す (実行者以外の管理者への周知・監査)。
        # 同期実行のため実行者は画面で結果を見るが、outbound apply と同じ基盤に揃える。
        if summary.failed > 0:
            from app.services.checkin.notify import (
                _active_admin_manager_users,
                _create_idempotent,
            )

            users = await _active_admin_manager_users(db)
            await _create_idempotent(
                db,
                users=users,
                type_="kaipoke_import_result",
                reference_type="kaipoke_import",
                reference_id=job.id,
                title=f"カイポケ取り込みに要確認（失敗{summary.failed}件）",
                body=(
                    f"{sheet.target_month} 週 {sheet.week_start.isoformat()} の取り込みで "
                    f"失敗{summary.failed}件があります。カイポケ連携画面で内訳を確認してください。"
                ),
            )
        await _commit_or_409(db)
        job_id = job.id

    return InboundApplyResult(
        job_id=job_id,
        dry_run=payload.dry_run,
        cancelled=summary.cancelled,
        updated=summary.updated,
        added=summary.added,
        skipped=summary.skipped,
        failed=summary.failed,
        results=[InboundItemResultRead(**r.__dict__) for r in summary.results],
    )


@router.get(
    "/jobs/{job_id}",
    response_model=KaipokeJobRead,
    summary="Get a Kaipoke job (alias of /kaipoke/jobs/{id}) (admin)",
)
async def get_integration_job(
    job_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> KaipokeJobRead:
    job = await db.scalar(
        select(KaipokeJob).where(KaipokeJob.id == job_id).options(selectinload(KaipokeJob.items))
    )
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return KaipokeJobRead.model_validate(job, from_attributes=True)


@router.post(
    "/jobs/{job_id}/stop",
    response_model=KaipokeJobRead,
    summary="Stop a running kaipoke job (admin)",
)
async def stop_integration_job(
    job_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    kaipoke: Annotated[KaipokeClient, Depends(_kaipoke_dep)],
) -> KaipokeJobRead:
    job = await db.scalar(select(KaipokeJob).where(KaipokeJob.id == job_id))
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if job.status not in {"pending", "running"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot stop job in status '{job.status}'",
        )

    # kaipoke-api is single-slot: /api/stop halts the one running task (no id).
    # Best-effort — we still mark the DB job cancelled even if the call fails.
    try:
        await kaipoke.stop()
    except KaipokeApiError:
        pass

    job.status = "cancelled"
    job.completed_at = datetime.now(UTC)
    await _commit_or_409(db)

    refreshed = await db.scalar(
        select(KaipokeJob).where(KaipokeJob.id == job_id).options(selectinload(KaipokeJob.items))
    )
    assert refreshed is not None
    return KaipokeJobRead.model_validate(refreshed, from_attributes=True)


@router.get(
    "/jobs",
    response_model=Paginated[KaipokeJobRead],
    summary="List recent integration jobs (admin)",
)
async def list_integration_jobs(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Paginated[KaipokeJobRead]:
    rows = (
        await db.scalars(
            select(KaipokeJob)
            .options(selectinload(KaipokeJob.items))
            .order_by(KaipokeJob.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    total = (await db.scalar(select(func.count()).select_from(KaipokeJob))) or 0
    return Paginated[KaipokeJobRead](
        items=[KaipokeJobRead.model_validate(r, from_attributes=True) for r in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.patch(
    "/job-items/{item_id}",
    response_model=dict,
    summary="Patch a KaipokeJobItem (manuallyHandled / comment) (admin)",
)
async def patch_job_item(
    item_id: UUID,
    payload: JobItemPatch,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> dict[str, Any]:
    item = await db.scalar(select(KaipokeJobItem).where(KaipokeJobItem.id == item_id))
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    content = dict(item.content or {})
    if payload.manually_handled is not None:
        content["manually_handled"] = payload.manually_handled
    if payload.comment is not None:
        content["comment"] = payload.comment
    item.content = content
    await _commit_or_409(db)
    return {
        "id": str(item.id),
        "content": item.content,
        "status": item.status,
    }


# --- Wave 4-A: correction sheets / items (Phase C) -------------------------


@router.get(
    "/correction-sheets/latest",
    response_model=CorrectionSheetRead,
    summary="Get the latest correction sheet for a month (admin)",
)
async def get_latest_correction_sheet(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    month: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
) -> CorrectionSheetRead:
    stmt = select(CorrectionSheet).options(selectinload(CorrectionSheet.items))
    if month:
        stmt = stmt.where(CorrectionSheet.target_month == month)
    stmt = stmt.order_by(CorrectionSheet.created_at.desc()).limit(1)
    sheet = await db.scalar(stmt)
    if sheet is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No sheet found")
    return CorrectionSheetRead.model_validate(sheet, from_attributes=True)


@router.get(
    "/correction-sheets/{sheet_id}/items",
    response_model=Paginated[CorrectionItemRead],
    summary="List correction items in a sheet (admin)",
)
async def list_correction_items(
    sheet_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    type: Annotated[str | None, Query(description="Filter by action")] = None,
    include: Annotated[bool | None, Query(description="Filter by include flag")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Paginated[CorrectionItemRead]:
    sheet = await db.scalar(select(CorrectionSheet).where(CorrectionSheet.id == sheet_id))
    if sheet is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sheet not found")

    conditions = [CorrectionSheetItem.sheet_id == sheet_id]
    if type is not None:
        conditions.append(CorrectionSheetItem.action == type)
    if include is not None:
        conditions.append(CorrectionSheetItem.include == include)

    base = select(CorrectionSheetItem).where(and_(*conditions))
    count_stmt = select(func.count()).select_from(CorrectionSheetItem).where(and_(*conditions))
    rows = (
        await db.scalars(
            base.order_by(CorrectionSheetItem.created_at.asc()).limit(limit).offset(offset)
        )
    ).all()
    total = (await db.scalar(count_stmt)) or 0
    return Paginated[CorrectionItemRead](
        items=[CorrectionItemRead.model_validate(r, from_attributes=True) for r in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.patch(
    "/correction-items/{item_id}",
    response_model=CorrectionItemRead,
    summary="Update a single correction item (admin)",
)
async def update_correction_item(
    item_id: UUID,
    payload: CorrectionItemUpdate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> CorrectionItemRead:
    item = await db.scalar(select(CorrectionSheetItem).where(CorrectionSheetItem.id == item_id))
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if payload.include is not None:
        item.include = payload.include
    if payload.comment is not None:
        item.comment = payload.comment
    await _commit_or_409(db)
    return CorrectionItemRead.model_validate(item, from_attributes=True)


@router.post(
    "/correction-sheets/{sheet_id}/items/bulk",
    response_model=dict,
    summary="Bulk patch correction items (admin)",
)
async def bulk_update_correction_items(
    sheet_id: UUID,
    payload: CorrectionBulkSelect,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> dict[str, Any]:
    sheet = await db.scalar(select(CorrectionSheet).where(CorrectionSheet.id == sheet_id))
    if sheet is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sheet not found")
    if not payload.ids:
        return {"updated": 0}

    values: dict[str, Any] = {}
    if payload.patch.include is not None:
        values["include"] = payload.patch.include
    if payload.patch.comment is not None:
        values["comment"] = payload.patch.comment
    if not values:
        return {"updated": 0}

    stmt = (
        update(CorrectionSheetItem)
        .where(
            CorrectionSheetItem.sheet_id == sheet_id,
            CorrectionSheetItem.id.in_(payload.ids),
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    res = await db.execute(stmt)
    await _commit_or_409(db)
    return {"updated": int(res.rowcount or 0)}


# --- イベント取り込み (個別業務・kaipoke-event-inbound-design.md E-1) --------


_EVENTS_GATE_DETAIL = (
    "未来の週はまだ取り込めません（計画中の週を消してしまう事故防止のため、"
    "先に④反映を実行した週のみ取り込めます）"
)


def _hhmm(t) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


@router.post(
    "/events-inbound-preview",
    response_model=EventsInboundPreviewRead,
    summary="Fetch kaipoke individual tasks and build an events diff plan (admin)",
)
async def events_inbound_preview(
    payload: EventsInboundPreviewRequest,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin"))],
    kaipoke: Annotated[KaipokeClient, Depends(_kaipoke_dep)],
) -> EventsInboundPreviewRead:
    """カイポケ個別業務(イベント)を取得して staff_events との差分計画を返す。

    read-only (staff_events への書込なし・シート永続化なし。KaipokeJob の監査
    記録のみ作成)。RPA 同期取得のため ~60-90s かかる。
    取り込みゲート = 訪問取り込みと同一 (対象週に実apply 記録が必要)。
    """
    from app.services.kaipoke.events_inbound import (
        EventsFetchError,
        build_events_plan,
        fetch_week_tasks,
    )
    from app.services.kaipoke.inbound import inbound_week_eligible

    if payload.week_start.weekday() != 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="weekStart は月曜日を指定してください",
        )
    eligible, _record = await inbound_week_eligible(db, payload.week_start)
    if not eligible:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_EVENTS_GATE_DETAIL,
        )

    credentials = await _kaipoke_credentials(db)
    now = datetime.now(UTC)
    job = KaipokeJob(
        job_type="fetch",
        week_start=payload.week_start,
        params={"op": "events-preview", "week_start": payload.week_start.isoformat()},
        status="running",
        started_at=now,
        created_by_user_id=user.id,
    )
    db.add(job)
    await db.flush()

    try:
        result = await fetch_week_tasks(kaipoke, payload.week_start, credentials)
    except KaipokeBusyError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="kaipoke busy") from exc
    except (KaipokeApiError, EventsFetchError) as exc:
        job.status = "failed"
        job.completed_at = datetime.now(UTC)
        job.result_summary = {"error": str(exc)}
        await _commit_or_409(db)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    plan = await build_events_plan(db, week_start=payload.week_start, tasks=result["tasks"])

    adds = sum(1 for c in plan.changes if c.action == "add")
    updates = sum(1 for c in plan.changes if c.action == "update")
    deletes = sum(1 for c in plan.changes if c.action == "delete")
    job.status = "completed"
    job.completed_at = datetime.now(UTC)
    job.result_summary = {
        "fetched": plan.fetched_total,
        "adds": adds,
        "updates": updates,
        "deletes": deletes,
        "unmatched": sum(plan.unmatched.values()),
        "sunday_skipped": plan.sunday_skipped,
    }
    await _commit_or_409(db)

    return EventsInboundPreviewRead(
        week_start=plan.week_start,
        week_end=plan.week_end,
        fetched_total=plan.fetched_total,
        sunday_skipped=plan.sunday_skipped,
        memo_count=plan.memo_count,
        adds=adds,
        updates=updates,
        deletes=deletes,
        changes=[
            EventsInboundChange(
                action=c.action,
                external_id=c.external_id,
                staff_id=c.staff_id,
                staff_name=c.staff_name,
                target_date=c.date,
                start=_hhmm(c.start),
                end=_hhmm(c.end),
                title=c.title,
                is_memo=c.is_memo,
                before_start=_hhmm(c.before_start) if c.before_start else None,
                before_end=_hhmm(c.before_end) if c.before_end else None,
                before_title=c.before_title,
            )
            for c in plan.changes
        ],
        unmatched=[
            EventsInboundUnmatchedRead(staff_name=name, count=count)
            for name, count in sorted(plan.unmatched.items())
        ],
    )


@router.post(
    "/events-inbound-apply",
    response_model=EventsInboundApplyResult,
    summary="Apply an events diff plan to staff_events (admin)",
)
async def events_inbound_apply(
    payload: EventsInboundApplyRequest,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin"))],
) -> EventsInboundApplyResult:
    """プレビューの changes をエコーバックで受けて staff_events へ適用する。

    dry_run 既定 true (無書込・明示 rollback)。upsert 意味論のため stale 耐性あり。
    source='kaipoke' の行のみ管理 (手動イベントには触れない)。
    """
    from datetime import time as _time

    from app.services.kaipoke.events_inbound import (
        EventChange,
        apply_events_changes,
    )
    from app.services.kaipoke.inbound import inbound_week_eligible

    if payload.week_start.weekday() != 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="weekStart は月曜日を指定してください",
        )
    eligible, _record = await inbound_week_eligible(db, payload.week_start)
    if not eligible:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_EVENTS_GATE_DETAIL,
        )
    if not payload.changes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="適用対象の変更がありません（先にプレビューを実行してください）",
        )

    def _parse(hhmm: str) -> _time:
        hh, mm = hhmm.split(":")
        return _time(int(hh), int(mm))

    changes = [
        EventChange(
            action=c.action,
            external_id=c.external_id,
            staff_id=c.staff_id,
            staff_name=c.staff_name,
            date=c.target_date,
            start=_parse(c.start),
            end=_parse(c.end),
            title=c.title[:255],
        )
        for c in payload.changes
    ]

    now = datetime.now(UTC)
    summary = await apply_events_changes(
        db,
        week_start=payload.week_start,
        changes=changes,
        dry_run=payload.dry_run,
        now=now,
    )

    job_id: UUID | None = None
    if payload.dry_run:
        # dry-run は mutate しない (apply_events_changes も no-write)。明示 rollback。
        await db.rollback()
    else:
        job = KaipokeJob(
            job_type="fetch",
            week_start=payload.week_start,
            params={
                "op": "apply-events",
                "week_start": payload.week_start.isoformat(),
                "changes": len(payload.changes),
            },
            status="completed",
            started_at=now,
            completed_at=now,
            created_by_user_id=user.id,
        )
        job.result_summary = summary.as_dict()
        db.add(job)
        await db.flush()
        if summary.failed > 0:
            from app.services.checkin.notify import (
                _active_admin_manager_users,
                _create_idempotent,
            )

            users = await _active_admin_manager_users(db)
            await _create_idempotent(
                db,
                users=users,
                type_="kaipoke_import_result",
                reference_type="kaipoke_import",
                reference_id=job.id,
                title=f"イベント取り込みに要確認（失敗{summary.failed}件）",
                body=(
                    f"週 {payload.week_start.isoformat()} のイベント取り込みで "
                    f"失敗{summary.failed}件があります。カイポケ連携画面で内訳を確認してください。"
                ),
            )
        await _commit_or_409(db)
        job_id = job.id

    return EventsInboundApplyResult(
        job_id=job_id,
        dry_run=payload.dry_run,
        added=summary.added,
        updated=summary.updated,
        deleted=summary.deleted,
        skipped=summary.skipped,
        failed=summary.failed,
        results=[
            EventsInboundApplyItemRead(
                action=r.action,
                external_id=r.external_id,
                staff_name=r.staff_name,
                target_date=r.date,
                title=r.title,
                outcome=r.outcome,
                detail=r.detail,
            )
            for r in summary.results
        ],
    )


# --- 置換取り込み (週白紙化→カイポケ全挿入・2026-07-26 PO確定) --------------


@router.post(
    "/replace-inbound",
    response_model=ReplaceInboundResult,
    summary="Replace the week's visits with the kaipoke schedule (admin)",
)
async def replace_inbound(
    payload: ReplaceInboundRequest,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin"))],
    kaipoke: Annotated[KaipokeClient, Depends(_kaipoke_dep)],
) -> ReplaceInboundResult:
    """対象週のらく助訪問を白紙化し、カイポケ現況で丸ごと書き直す (全置換)。

    「カイポケは請求と紐づく最終的な正・らく助が受け入れる」(PO確定 2026-07-26)。
    差分突合をしないため名寄せ差 (氏名の空白違い) や同時刻衝突が構造的に発生しない。
    一度も同期していない週の初回整列・未打刻週のズレ一括解消に使う。

    安全装置: ゲート=差分取り込みと共有 / 実績(打刻)ガード=1件でもあれば422 /
    dry_run既定 / 白紙化と挿入は同一トランザクション / UI は
    「らく助側のこの週の情報はすべて削除される可能性がございます」を明記した
    確認ダイアログを必須とする。
    """
    from app.services.diff.engine import parse_csv_from_content
    from app.services.kaipoke.inbound import inbound_week_eligible
    from app.services.kaipoke.local_diff import export_current_week_csv
    from app.services.kaipoke.replace_inbound import (
        ReplaceBlockedError,
        replace_week_from_kaipoke,
    )

    if payload.week_start.weekday() != 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="weekStart は月曜日を指定してください",
        )
    eligible, _record = await inbound_week_eligible(db, payload.week_start)
    if not eligible:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_EVENTS_GATE_DETAIL,
        )

    credentials = await _kaipoke_credentials(db)
    now = datetime.now(UTC)
    job = KaipokeJob(
        job_type="fetch",
        week_start=payload.week_start,
        params={
            "op": "replace-inbound",
            "week_start": payload.week_start.isoformat(),
            "dry_run": payload.dry_run,
        },
        status="running",
        started_at=now,
        created_by_user_id=user.id,
    )
    db.add(job)
    await db.flush()

    try:
        csv_content = await export_current_week_csv(
            kaipoke=kaipoke, week_start=payload.week_start, credentials=credentials
        )
    except KaipokeBusyError as exc:
        # busy = 単一スロットの一時的な競合 (リトライで解消) → 監査記録は残さない。
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="kaipoke busy") from exc
    except KaipokeApiError as exc:
        # API エラー = 恒常的な障害の可能性 → failed ジョブとして記録し調査可能にする。
        job.status = "failed"
        job.completed_at = datetime.now(UTC)
        job.result_summary = {"error": str(exc)}
        await _commit_or_409(db)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    entries = parse_csv_from_content(csv_content, "kaipoke")
    if not entries:
        # 空CSVでの白紙化は「週全滅」そのもの — 誤操作/取得失敗の疑いが濃いため拒否。
        job.status = "failed"
        job.completed_at = datetime.now(UTC)
        job.result_summary = {"error": "empty kaipoke csv"}
        await _commit_or_409(db)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "カイポケの現況が0件でした。カイポケにこの週のスケジュールが"
                "入力されているか確認してください（0件での置換は安全のため拒否します）"
            ),
        )

    if not payload.dry_run:
        # 取り込み前スナップショット (PO 決定 2026-08-09: 「取り込み前に戻す」用)。
        from app.services.kaipoke.inbound_snapshot import snapshot_week

        await snapshot_week(db, payload.week_start, kind="replace", user_id=user.id)
    try:
        result = await replace_week_from_kaipoke(
            db,
            week_start=payload.week_start,
            entries=entries,
            dry_run=payload.dry_run,
            now=now,
        )
    except ReplaceBlockedError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    job.status = "completed"
    job.completed_at = datetime.now(UTC)
    job.result_summary = {
        "dry_run": payload.dry_run,
        "wiped": result.wiped,
        "inserted": result.inserted,
        "skipped": len(result.skipped),
        "sunday_skipped": result.sunday_skipped,
        "temp_courses": result.temp_courses,
        "courses_reassigned": result.courses_reassigned,
        "courses_created": result.courses_created,
    }

    job_id: UUID | None = None
    if payload.dry_run:
        # dry-run は visits を mutate しない (job 記録も残さない)。明示 rollback。
        await db.rollback()
    else:
        if result.skipped or result.trainee_solo:
            from app.services.checkin.notify import (
                _active_admin_manager_users,
                _create_idempotent,
            )

            users = await _active_admin_manager_users(db)
            body_parts: list[str] = []
            if result.skipped:
                body_parts.append(
                    f"挿入できなかったカイポケ行が {len(result.skipped)} 件あります。"
                )
            if result.trainee_solo:
                solo = "・".join(
                    f"{name}（{count}件）" for name, count in sorted(result.trainee_solo.items())
                )
                body_parts.append(
                    f"⚠ 新人の単独訪問を取り込みました: {solo} — "
                    "実態に合わせて新人フラグの見直しを検討してください。"
                )
            await _create_idempotent(
                db,
                users=users,
                type_="kaipoke_import_result",
                reference_type="kaipoke_import",
                reference_id=job.id,
                title=(
                    f"置換取り込みの要確認（対象外 {len(result.skipped)} 件"
                    + (
                        f"・新人単独 {sum(result.trainee_solo.values())} 件"
                        if result.trainee_solo
                        else ""
                    )
                    + "）"
                ),
                body=(
                    f"週 {payload.week_start.isoformat()} の置換取り込み: "
                    + " ".join(body_parts)
                    + " 連携画面で内訳を確認してください。"
                ),
            )
        await _commit_or_409(db)
        job_id = job.id

    return ReplaceInboundResult(
        job_id=job_id,
        week_start=result.week_start,
        week_end=result.week_end,
        dry_run=payload.dry_run,
        wiped=result.wiped,
        inserted=result.inserted,
        sunday_skipped=result.sunday_skipped,
        temp_courses=result.temp_courses,
        courses_reassigned=result.courses_reassigned,
        courses_created=result.courses_created,
        skipped=[
            ReplaceInboundSkipRead(
                reason=s.reason,
                user_name=s.user_name,
                staff_name=s.staff_name,
                target_date=s.date,
                start=s.start,
            )
            for s in result.skipped
        ],
        trainee_solo=[
            ReplaceInboundTraineeSoloRead(staff_name=name, count=count)
            for name, count in sorted(result.trainee_solo.items())
        ],
    )


# --- smart-inbound (日単位ハイブリッド自動判別・2026-07-26 PO確定) ------------
# 「打刻あり日 = 差分 (実績の紐付けを守って直す) / なし日 = 置換 (カイポケで書き直す)」
# をシステムが自動判別し、作業者はモードを選ばない (handoff 2026-07-26 §6-b)。
# export は preview / apply それぞれ1回だけ実行し、差分計算と置換計画の両方に渡す。


def _replace_result_read(result, job_id) -> ReplaceInboundResult:
    """ReplaceResult (service) → ReplaceInboundResult (schema)。"""
    return ReplaceInboundResult(
        job_id=job_id,
        week_start=result.week_start,
        week_end=result.week_end,
        dry_run=result.dry_run,
        wiped=result.wiped,
        inserted=result.inserted,
        sunday_skipped=result.sunday_skipped,
        temp_courses=result.temp_courses,
        courses_reassigned=result.courses_reassigned,
        courses_created=result.courses_created,
        skipped=[
            ReplaceInboundSkipRead(
                reason=s.reason,
                user_name=s.user_name,
                staff_name=s.staff_name,
                target_date=s.date,
                start=s.start,
            )
            for s in result.skipped
        ],
        trainee_solo=[
            ReplaceInboundTraineeSoloRead(staff_name=name, count=count)
            for name, count in sorted(result.trainee_solo.items())
        ],
    )


async def _smart_classify(db, week_start: date) -> tuple[list[date], list[date]]:
    """週の各日を 打刻あり(差分担当) / なし(置換担当) に分類する。"""
    from app.services.kaipoke.replace_inbound import week_checkin_days

    protected = await week_checkin_days(db, week_start)
    week_days = [week_start + timedelta(days=i) for i in range(6)]  # 月〜土
    return (
        sorted(d for d in week_days if d in protected),
        sorted(d for d in week_days if d not in protected),
    )


@router.post(
    "/smart-inbound-preview",
    response_model=SmartInboundPreviewRead,
    summary="日単位ハイブリッド取り込みの統合プレビュー (admin)",
)
async def smart_inbound_preview(
    payload: SmartInboundPreviewRequest,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin"))],
    kaipoke: Annotated[KaipokeClient, Depends(_kaipoke_dep)],
) -> SmartInboundPreviewRead:
    """打刻あり日=差分シート作成・なし日=置換dry-run、を1回のexportで実行する。"""
    from app.services.diff.engine import parse_csv_from_content
    from app.services.kaipoke.inbound import inbound_week_eligible
    from app.services.kaipoke.local_diff import build_local_diff, export_current_week_csv
    from app.services.kaipoke.replace_inbound import replace_week_from_kaipoke

    week_start = payload.week_start
    if week_start.weekday() != 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="weekStart は月曜日を指定してください",
        )
    eligible, _record = await inbound_week_eligible(db, week_start)
    if not eligible:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_EVENTS_GATE_DETAIL,
        )

    protected_days, replace_days = await _smart_classify(db, week_start)
    credentials = await _kaipoke_credentials(db)
    now = datetime.now(UTC)
    job = KaipokeJob(
        job_type="fetch",
        week_start=week_start,
        params={
            "op": "smart-preview",
            "week_start": week_start.isoformat(),
            "protected_days": [d.isoformat() for d in protected_days],
        },
        status="running",
        started_at=now,
        created_by_user_id=user.id,
    )
    db.add(job)
    await db.flush()

    try:
        csv_content = await export_current_week_csv(
            kaipoke=kaipoke, week_start=week_start, credentials=credentials
        )
    except KaipokeBusyError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="kaipoke busy") from exc
    except KaipokeApiError as exc:
        job.status = "failed"
        job.completed_at = datetime.now(UTC)
        job.result_summary = {"error": str(exc)}
        await _commit_or_409(db)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    entries = parse_csv_from_content(csv_content, "kaipoke")
    if not entries:
        job.status = "failed"
        job.completed_at = datetime.now(UTC)
        job.result_summary = {"error": "empty kaipoke csv"}
        await _commit_or_409(db)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "カイポケの現況が0件でした。カイポケにこの週のスケジュールが"
                "入力されているか確認してください（0件での取り込みは安全のため拒否します）"
            ),
        )

    # 差分パート (打刻あり日) — シートは週全体で作り、適用時に日で絞る
    sheet = None
    diff_summary: dict[str, int] = {}
    if protected_days:
        month = f"{week_start.year:04d}-{week_start.month:02d}"
        corrections, _meta = await build_local_diff(
            db,
            month=month,
            kaipoke=kaipoke,
            week_start=week_start,
            week_end=week_start + timedelta(days=6),
            direction="inbound",
            current_csv=csv_content,
        )
        sheet, diff_summary = await _build_inbound_sheet(
            db,
            corrections=corrections,
            month=month,
            week_start=week_start,
            week_end=week_start + timedelta(days=6),
            user_id=user.id,
        )

    # 置換パート (打刻なし日) — dry-run で計画のみ
    replace_read = None
    if replace_days:
        plan = await replace_week_from_kaipoke(
            db,
            week_start=week_start,
            entries=entries,
            dry_run=True,
            now=now,
            target_days=set(replace_days),
        )
        replace_read = _replace_result_read(plan, None)

    job.status = "completed"
    job.completed_at = datetime.now(UTC)
    job.result_summary = {
        "protected_days": len(protected_days),
        "replace_days": len(replace_days),
        "diff_summary": diff_summary,
        "replace_wiped": replace_read.wiped if replace_read else 0,
        "replace_inserted": replace_read.inserted if replace_read else 0,
    }
    await _commit_or_409(db)

    return SmartInboundPreviewRead(
        week_start=week_start,
        week_end=week_start + timedelta(days=5),
        protected_days=protected_days,
        replace_days=replace_days,
        sheet_id=sheet.id if sheet is not None else None,
        diff_summary=diff_summary,
        replace=replace_read,
    )


@router.post(
    "/smart-inbound-apply",
    response_model=SmartInboundApplyResult,
    summary="日単位ハイブリッド取り込みの適用 (admin)",
)
async def smart_inbound_apply(
    payload: SmartInboundApplyRequest,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin"))],
    kaipoke: Annotated[KaipokeClient, Depends(_kaipoke_dep)],
) -> SmartInboundApplyResult:
    """打刻あり日=差分シート適用・なし日=置換、を単一トランザクションで実行する。

    実行時に再分類・再取得する (プレビュー後に打刻やカイポケ入力が進んでも安全)。
    dry_run=True は一切書き込まない。
    """
    from app.services.diff.engine import parse_csv_from_content
    from app.services.kaipoke.inbound import apply_inbound_items, inbound_week_eligible
    from app.services.kaipoke.local_diff import export_current_week_csv
    from app.services.kaipoke.replace_inbound import (
        ReplaceBlockedError,
        replace_week_from_kaipoke,
    )

    week_start = payload.week_start
    if week_start.weekday() != 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="weekStart は月曜日を指定してください",
        )
    eligible, _record = await inbound_week_eligible(db, week_start)
    if not eligible:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_EVENTS_GATE_DETAIL,
        )

    # 実行時に再分類 (プレビュー後の打刻進行を安全側で反映)
    protected_days, replace_days = await _smart_classify(db, week_start)
    now = datetime.now(UTC)

    if not payload.dry_run:
        # 取り込み前スナップショット (PO 決定 2026-08-09: 「取り込み前に戻す」用)。
        # 差分・置換のどちらの変異よりも前 = 取り込み前の盤面が丸ごと残る。
        from app.services.kaipoke.inbound_snapshot import snapshot_week

        await snapshot_week(db, week_start, kind="smart", user_id=user.id)

    # 置換対象日があるときのみ再取得 (差分のみの週は RPA 不要)
    entries = None
    if replace_days:
        credentials = await _kaipoke_credentials(db)
        try:
            csv_content = await export_current_week_csv(
                kaipoke=kaipoke, week_start=week_start, credentials=credentials
            )
        except KaipokeBusyError as exc:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="kaipoke busy"
            ) from exc
        except KaipokeApiError as exc:
            await db.rollback()
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        entries = parse_csv_from_content(csv_content, "kaipoke")
        if not entries:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "カイポケの現況が0件でした。カイポケにこの週のスケジュールが"
                    "入力されているか確認してください（0件での置換は安全のため拒否します）"
                ),
            )

    # 差分パート (打刻あり日・シートの include 項目を日で絞って適用)
    diff_result = None
    if payload.sheet_id is not None and protected_days:
        sheet = await db.scalar(
            select(CorrectionSheet)
            .where(CorrectionSheet.id == payload.sheet_id)
            .options(selectinload(CorrectionSheet.items))
            .with_for_update(of=CorrectionSheet)
        )
        if sheet is None or sheet.direction != "inbound":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="差分シートが見つかりません（プレビューを取り直してください）",
            )
        if sheet.status == "applied":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="sheet already applied"
            )
        selected = [it for it in sheet.items if it.include]
        if selected:
            summary = await apply_inbound_items(
                db,
                items=selected,
                week_start=week_start,
                week_end=week_start + timedelta(days=6),
                days=protected_days,
                dry_run=payload.dry_run,
                now=now,
            )
            if not payload.dry_run:
                sheet.status = "applied"
            diff_result = InboundApplyResult(
                job_id=None,
                dry_run=payload.dry_run,
                cancelled=summary.cancelled,
                updated=summary.updated,
                added=summary.added,
                skipped=summary.skipped,
                failed=summary.failed,
                results=[
                    InboundItemResultRead(
                        item_id=r.item_id,
                        action=r.action,
                        outcome=r.outcome,  # type: ignore[arg-type]
                        detail=r.detail,
                        patient_name=r.patient_name,
                        date=r.date,
                    )
                    for r in summary.results
                ],
            )

    # 置換パート (打刻なし日)
    replace_read = None
    if replace_days and entries is not None:
        try:
            plan = await replace_week_from_kaipoke(
                db,
                week_start=week_start,
                entries=entries,
                dry_run=payload.dry_run,
                now=now,
                target_days=set(replace_days),
            )
        except ReplaceBlockedError as exc:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        replace_read = _replace_result_read(plan, None)

    job_id: UUID | None = None
    if payload.dry_run:
        await db.rollback()
    else:
        job = KaipokeJob(
            job_type="fetch",
            week_start=week_start,
            params={
                "op": "smart-apply",
                "week_start": week_start.isoformat(),
                "protected_days": [d.isoformat() for d in protected_days],
                "replace_days": [d.isoformat() for d in replace_days],
            },
            status="completed",
            started_at=now,
            completed_at=datetime.now(UTC),
            created_by_user_id=user.id,
        )
        job.result_summary = {
            "diff": diff_result.model_dump(
                include={"cancelled", "updated", "added", "skipped", "failed"}
            )
            if diff_result
            else None,
            "replace": replace_read.model_dump(
                include={
                    "wiped",
                    "inserted",
                    "temp_courses",
                    "courses_reassigned",
                    "courses_created",
                }
            )
            if replace_read
            else None,
        }
        db.add(job)
        await db.flush()
        # 要確認 (対象外/新人単独/差分失敗) は管理者へ恒久通知
        n_skipped = len(replace_read.skipped) if replace_read else 0
        n_trainee = sum(t.count for t in replace_read.trainee_solo) if replace_read else 0
        n_failed = diff_result.failed if diff_result else 0
        if n_skipped or n_trainee or n_failed:
            from app.services.checkin.notify import (
                _active_admin_manager_users,
                _create_idempotent,
            )

            users = await _active_admin_manager_users(db)
            await _create_idempotent(
                db,
                users=users,
                type_="kaipoke_import_result",
                reference_type="kaipoke_import",
                reference_id=job.id,
                title=(
                    f"取り込みの要確認（対象外{n_skipped}・新人単独{n_trainee}・失敗{n_failed}）"
                ),
                body=(
                    f"週 {week_start.isoformat()} のハイブリッド取り込みに要確認項目があります。"
                    "連携画面で内訳を確認してください。"
                ),
            )
        await _commit_or_409(db)
        job_id = job.id
        if replace_read is not None:
            replace_read = replace_read.model_copy(update={"job_id": job_id})

    return SmartInboundApplyResult(
        week_start=week_start,
        protected_days=protected_days,
        replace_days=replace_days,
        dry_run=payload.dry_run,
        diff=diff_result,
        replace=replace_read,
    )
