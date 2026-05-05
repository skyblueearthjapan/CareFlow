"""Integrations / 連携センター endpoints — Phase 5-1 Wave 2-B + Wave 4-A.

Exposes Kaipoke fetch/push job management plus admin-only views over the
geocoding cache and the AI interpret audit log. Wave 4-A adds the actual
relay endpoints to the existing kaipoke-api (Flask + Playwright) so the
連携センター画面 can drive expand/export/diff/apply jobs end-to-end.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core.deps import DbDep, require_role
from app.models.ai_interpret_log import AiInterpretLog
from app.models.correction_sheet import CorrectionSheet, CorrectionSheetItem
from app.models.geocoding_cache import GeocodingCache
from app.models.kaipoke_job import KaipokeJob, KaipokeJobItem
from app.models.user import User
from app.schemas._pagination import Paginated
from app.schemas.integrations import (
    AiInterpretLogRead,
    CorrectionBulkSelect,
    CorrectionItemRead,
    CorrectionItemUpdate,
    CorrectionSheetRead,
    DiffAccepted,
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
        select(KaipokeJob)
        .where(KaipokeJob.id == job_id)
        .options(selectinload(KaipokeJob.items))
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
        select(KaipokeJob)
        .where(KaipokeJob.id == job.id)
        .options(selectinload(KaipokeJob.items))
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
    now = datetime.now(timezone.utc)
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
        existing = await db.scalar(
            select(KaipokeJob.status).where(KaipokeJob.id == job_id)
        )
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Not found"
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel job in status '{existing}'",
        )

    await _commit_or_409(db)

    refreshed = await db.scalar(
        select(KaipokeJob)
        .where(KaipokeJob.id == job_id)
        .options(selectinload(KaipokeJob.items))
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

    stmt = (
        base.order_by(GeocodingCache.looked_up_at.desc())
        .limit(limit)
        .offset(offset)
    )
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
    job.started_at = started_at or datetime.now(timezone.utc)
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

    try:
        upstream = await kaipoke.expand({"month": payload.month, "dryRun": payload.dry_run})
    except KaipokeBusyError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="kaipoke busy") from exc
    except KaipokeApiError as exc:
        job.status = "failed"
        job.completed_at = datetime.now(timezone.utc)
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

    try:
        upstream = await kaipoke.export({"month": payload.month, "format": payload.format})
    except KaipokeBusyError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="kaipoke busy") from exc
    except KaipokeApiError as exc:
        job.status = "failed"
        job.completed_at = datetime.now(timezone.utc)
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
        job.completed_at = datetime.now(timezone.utc)
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
    job.completed_at = datetime.now(timezone.utc)
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

    selected = [it for it in sheet.items if it.include]
    if not selected:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No items selected (include=true)",
        )

    job = KaipokeJob(
        job_type="push",
        week_start=date.fromisoformat(f"{sheet.target_month}-01"),
        params={"op": "apply", "sheet_id": str(sheet.id), "dry_run": payload.dry_run},
        status="pending",
        created_by_user_id=user.id,
    )
    db.add(job)
    await db.flush()

    body = {
        "sheetId": str(sheet.id),
        "month": sheet.target_month,
        "dryRun": payload.dry_run,
        "items": [
            {
                "id": str(it.id),
                "action": it.action,
                "patient_id": str(it.patient_id) if it.patient_id else None,
                "visit_id": str(it.visit_id) if it.visit_id else None,
                "before": it.before,
                "after": it.after,
            }
            for it in selected
        ],
    }

    try:
        upstream = await kaipoke.apply(body)
    except KaipokeBusyError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="kaipoke busy") from exc
    except KaipokeApiError as exc:
        job.status = "failed"
        job.completed_at = datetime.now(timezone.utc)
        job.result_summary = {"error": str(exc), "body": exc.body}
        await _commit_or_409(db)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    await _persist_job_after_kaipoke_call(db, job=job, kaipoke_response=upstream)
    if not payload.dry_run:
        sheet.status = "applied"
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
        select(KaipokeJob)
        .where(KaipokeJob.id == job_id)
        .options(selectinload(KaipokeJob.items))
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

    upstream_id = (job.result_summary or {}).get("kaipoke_job_id")
    if upstream_id:
        try:
            await kaipoke.stop(str(upstream_id))
        except KaipokeApiError:
            # Best-effort: mark cancelled locally even if upstream stop call failed.
            pass

    job.status = "cancelled"
    job.completed_at = datetime.now(timezone.utc)
    await _commit_or_409(db)

    refreshed = await db.scalar(
        select(KaipokeJob)
        .where(KaipokeJob.id == job_id)
        .options(selectinload(KaipokeJob.items))
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
            base.order_by(CorrectionSheetItem.created_at.asc())
            .limit(limit)
            .offset(offset)
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
    item = await db.scalar(
        select(CorrectionSheetItem).where(CorrectionSheetItem.id == item_id)
    )
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


# --- AI interpret logs (admin only) ---------------------------------------


@router.get(
    "/ai/logs",
    response_model=Paginated[AiInterpretLogRead],
    summary="List AI interpret logs (admin)",
)
async def list_ai_interpret_logs(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    since: Annotated[datetime | None, Query(description="created_at >= since")] = None,
    until: Annotated[datetime | None, Query(description="created_at <  until")] = None,
    model: Annotated[str | None, Query(description="Exact model id filter")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Paginated[AiInterpretLogRead]:
    conditions = []
    if since is not None:
        conditions.append(AiInterpretLog.created_at >= since)
    if until is not None:
        conditions.append(AiInterpretLog.created_at < until)
    if model is not None:
        conditions.append(AiInterpretLog.model == model)

    base = select(AiInterpretLog)
    count_stmt = select(func.count()).select_from(AiInterpretLog)
    if conditions:
        base = base.where(and_(*conditions))
        count_stmt = count_stmt.where(and_(*conditions))

    stmt = (
        base.order_by(AiInterpretLog.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.scalars(stmt)).all()
    total = (await db.scalar(count_stmt)) or 0
    return Paginated[AiInterpretLogRead](
        items=[AiInterpretLogRead.model_validate(r, from_attributes=True) for r in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )
