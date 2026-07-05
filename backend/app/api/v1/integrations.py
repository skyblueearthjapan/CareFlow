"""Integrations / 連携センター endpoints — Phase 5-1 Wave 2-B + Wave 4-A.

Exposes Kaipoke fetch/push job management plus admin-only views over the
geocoding cache. Wave 4-A adds the actual
relay endpoints to the existing kaipoke-api (Flask + Playwright) so the
連携センター画面 can drive expand/export/diff/apply jobs end-to-end.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
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
    ExpandStatusRead,
    GeneratedCsvRead,
    GeocodingCacheRead,
    IntegrationApplyRequest,
    IntegrationDiffRequest,
    IntegrationExpandRequest,
    IntegrationExportRequest,
    JobAccepted,
    JobItemPatch,
    KaipokeJobCreate,
    KaipokeJobRead,
    KaipokeStatusRead,
    LiveSnapshotRead,
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
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
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
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
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

    # 一括ロード: staff / office / course。
    staff_ids = {sid for v in visits for sid in (v.primary_staff_id, v.secondary_staff_id) if sid}
    staff_map = {}
    if staff_ids:
        staff_map = {
            s.id: s for s in (await db.scalars(select(Staff).where(Staff.id.in_(staff_ids)))).all()
        }
    course_ids = {v.course_id for v in visits if v.course_id}
    course_map: dict = {}
    if course_ids:
        course_map = {
            c.id: c
            for c in (await db.scalars(select(Course).where(Course.id.in_(course_ids)))).all()
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

    rows: list[WeekScheduleRow] = []
    for v in visits:
        patient = v.patient
        if patient is None or v.primary_staff_id is None:
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
        rows.append(
            WeekScheduleRow(
                visit_date=v.visit_date.isoformat(),
                weekday=v.visit_date.weekday(),
                start_time=f"{v.start_time.hour:02d}:{v.start_time.minute:02d}",
                end_time=f"{v.end_time.hour:02d}:{v.end_time.minute:02d}",
                patient_name=patient.name,
                staff1=_name(v.primary_staff_id),
                staff2=_name(v.secondary_staff_id),
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
    try:
        upstream = await kaipoke.expand(
            {"month": payload.month, "dryRun": payload.dry_run}, timeout=25.0
        )
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
    try:
        upstream = await kaipoke.export(
            {"month": payload.month, "format": payload.format, "async": True}
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

    try:
        upstream = await kaipoke.diff({"month": payload.month})
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

    sheet = CorrectionSheet(
        target_month=payload.month,
        status="ready",
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
        week_start=date.fromisoformat(f"{sheet.target_month}-01"),
        params={"op": "apply", "sheet_id": str(sheet.id), "dry_run": payload.dry_run},
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
