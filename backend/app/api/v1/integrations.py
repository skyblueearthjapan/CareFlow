"""Integrations / 連携センター endpoints — Phase 5-1 Wave 2-B.

Exposes Kaipoke fetch/push job management plus admin-only views over the
geocoding cache and the AI interpret audit log. The actual Playwright
execution loop lives in Phase 5-2/5-3/5-4 and is intentionally out of
scope for this wave — these endpoints just persist + list jobs.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core.deps import DbDep, require_role
from app.models.ai_interpret_log import AiInterpretLog
from app.models.geocoding_cache import GeocodingCache
from app.models.kaipoke_job import KaipokeJob
from app.models.user import User
from app.schemas.integrations import (
    AiInterpretLogRead,
    GeocodingCacheRead,
    KaipokeJobCreate,
    KaipokeJobRead,
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
    response_model=list[KaipokeJobRead],
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
) -> list[KaipokeJobRead]:
    stmt = (
        select(KaipokeJob)
        .options(selectinload(KaipokeJob.items))
        .order_by(KaipokeJob.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    conditions = []
    if week_start is not None:
        conditions.append(KaipokeJob.week_start == week_start)
    if job_status is not None:
        conditions.append(KaipokeJob.status == job_status)
    if job_type is not None:
        conditions.append(KaipokeJob.job_type == job_type)
    if conditions:
        stmt = stmt.where(and_(*conditions))

    rows = (await db.scalars(stmt)).all()
    return [KaipokeJobRead.model_validate(j, from_attributes=True) for j in rows]


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
    job = await db.scalar(
        select(KaipokeJob)
        .where(KaipokeJob.id == job_id)
        .options(selectinload(KaipokeJob.items))
    )
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if job.status not in {"pending", "running"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel job in status '{job.status}'",
        )

    job.status = "cancelled"
    if job.completed_at is None:
        job.completed_at = datetime.utcnow()

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
    response_model=list[GeocodingCacheRead],
    summary="List geocoding cache entries (admin)",
)
async def list_geocoding_cache(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    q: Annotated[str | None, Query(description="Substring filter on address")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[GeocodingCacheRead]:
    stmt = (
        select(GeocodingCache)
        .order_by(GeocodingCache.looked_up_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if q:
        stmt = stmt.where(GeocodingCache.address.ilike(f"%{q}%"))
    rows = (await db.scalars(stmt)).all()
    return [GeocodingCacheRead.model_validate(r, from_attributes=True) for r in rows]


# --- AI interpret logs (admin only) ---------------------------------------


@router.get(
    "/ai/logs",
    response_model=list[AiInterpretLogRead],
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
) -> list[AiInterpretLogRead]:
    stmt = (
        select(AiInterpretLog)
        .order_by(AiInterpretLog.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    conditions = []
    if since is not None:
        conditions.append(AiInterpretLog.created_at >= since)
    if until is not None:
        conditions.append(AiInterpretLog.created_at < until)
    if model is not None:
        conditions.append(AiInterpretLog.model == model)
    if conditions:
        stmt = stmt.where(and_(*conditions))

    rows = (await db.scalars(stmt)).all()
    return [AiInterpretLogRead.model_validate(r, from_attributes=True) for r in rows]
