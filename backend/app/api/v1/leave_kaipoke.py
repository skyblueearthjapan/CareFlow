"""休みのカイポケとの照合と送信 (段階 L #9・#10・管理者のみ).

正典: ``docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md`` §7-4-3・§7-4-4。

POST /api/v1/leave/compare/start          — カイポケと比べる (材料の取り直しのジョブ・202)
GET  /api/v1/leave/compare/latest?month=  — その月の最新の照合の結果と動いているジョブ
GET  /api/v1/leave/compare/{job_id}       — 照合のジョブの進み具合と、そのジョブの結果
POST /api/v1/leave/compare/apply          — 差分を「合わせる」(1 件でも、まとめてでも)
POST /api/v1/leave/outbound/start         — 未送信の休みをカイポケへ送る (ジョブ・202)
GET  /api/v1/leave/outbound/preview?month= — 送る前の一覧 (送れない理由・要対応)
GET  /api/v1/leave/outbound/{job_id}      — 送信のジョブの進み具合と 1 件ずつの結果

ジョブは段階 0 (``app/services/kaipoke/sources.py``) の ``KaipokeJob``。RPA を使う他の
ジョブが動いていれば 409。実体は応答の後に自前のセッションで走る (``BackgroundTasks``)。
画面は状態を数秒おきに読む (Cloudflare の 100 秒の制限を避ける)。
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.deps import DbDep, require_role
from app.models.kaipoke_job import KaipokeJob
from app.models.leave import LeaveCompareRun, LeaveMonth
from app.models.user import User
from app.services import leave_compare
from app.services.kaipoke import leave_outbound
from app.services.kaipoke.sources import (
    OP_LEAVE_COMPARE,
    OP_LEAVE_OUTBOUND,
    STALE_JOB_MINUTES,
    RpaJobConflictError,
    evening_warning,
)
from app.services.kaipoke_client import KaipokeClient, get_kaipoke_client
from app.services.staff_leave import StaffLeaveError

router = APIRouter()

_MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
_ACTIVE: tuple[str, ...] = ("pending", "running")
#: 月のジョブを探すときに見る直近のジョブの数。
_RECENT_JOBS = 50

MonthQuery = Annotated[str, Query(pattern=_MONTH_PATTERN, description="YYYY-MM")]


def _kaipoke_dep() -> KaipokeClient:
    """RPA のクライアント (試験は ``kaipoke_client.set_test_client`` で差し替える)。"""
    return get_kaipoke_client()


class MonthRequest(BaseModel):
    month: str = Field(pattern=_MONTH_PATTERN, description="YYYY-MM")


class CompareActionRequest(BaseModel):
    staff_id: uuid.UUID
    date: date
    action: Literal["add_to_rakusuke", "match_kaipoke_kind", "cancel_rakusuke", "choose_kind"]
    content_hash: str | None = None
    chosen_kind: Literal["not_leave", "off", "am_off", "pm_off", "paid_off"] | None = None


class CompareApplyRequest(BaseModel):
    items: list[CompareActionRequest] = Field(min_length=1, max_length=500)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _iso(value: datetime | None) -> str | None:
    aware = _aware(value)
    return aware.isoformat() if aware is not None else None


def _job_view(job: KaipokeJob) -> dict[str, Any]:
    """ジョブの進み具合 (画面に出すもの)。"""
    summary = job.result_summary or {}
    touched = _aware(job.updated_at or job.created_at)
    stale = (
        job.status in _ACTIVE
        and touched is not None
        and touched < datetime.now(UTC) - timedelta(minutes=STALE_JOB_MINUTES)
    )
    return {
        "id": str(job.id),
        "op": (job.params or {}).get("op"),
        "status": job.status,
        "month": (job.params or {}).get("month"),
        "done": int(summary.get("done") or 0),
        "total": summary.get("total"),
        "current": summary.get("current"),
        "results": list(summary.get("results") or []),
        "failed": list(summary.get("failed") or []),
        "error": summary.get("error"),
        "stale": stale,
        "started_at": _iso(job.started_at),
        "completed_at": _iso(job.completed_at),
        "updated_at": _iso(touched),
    }


def _run_view(run: LeaveCompareRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "month": run.month.isoformat(),
        "kaipoke_job_id": str(run.kaipoke_job_id) if run.kaipoke_job_id else None,
        "fetched_at": run.fetched_at,
        "diffs": list(run.diffs or []),
        "auto_updates": list(run.auto_updates or []),
        "diff_count": run.diff_count,
        "planned_send_count": run.planned_send_count,
        "unconfirmed_days": run.unconfirmed_days,
        "created_at": _iso(run.created_at),
    }


async def _job_of(db: DbDep, job_id: uuid.UUID, op: str) -> KaipokeJob:
    job = await db.get(KaipokeJob, job_id, populate_existing=True)
    if job is None or (job.params or {}).get("op") != op:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    return job


async def _latest_job(db: DbDep, op: str, month: str) -> KaipokeJob | None:
    jobs = (
        await db.scalars(
            select(KaipokeJob).order_by(KaipokeJob.created_at.desc()).limit(_RECENT_JOBS)
        )
    ).all()
    for job in jobs:
        params = job.params or {}
        if params.get("op") == op and params.get("month") == month:
            return job
    return None


# --- カイポケと比べる ---------------------------------------------------------------


@router.post(
    "/compare/start",
    status_code=status.HTTP_202_ACCEPTED,
    summary="休み: カイポケと比べる (材料の取り直しのジョブを立ててすぐ返す・admin)",
)
async def start_compare(
    payload: MonthRequest,
    background: BackgroundTasks,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin"))],
    kaipoke: Annotated[KaipokeClient, Depends(_kaipoke_dep)],
) -> dict[str, Any]:
    try:
        job, plan = await leave_compare.start_leave_compare(
            db, month=payload.month, user_id=user.id
        )
    except RpaJobConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    background.add_task(
        leave_compare.run_leave_compare_job, job_id=job.id, plan=plan, client=kaipoke
    )
    return {
        "job_id": str(job.id),
        "status": job.status,
        "month": payload.month,
        "total": len(plan),
        "evening_warning": evening_warning(),
    }


# ⚠ 登録の順: ``/compare/latest`` は ``/compare/{job_id}`` より先 (後にすると "latest" が
# job_id として読まれ、UUID でないため 422 になる)。
@router.get("/compare/latest", summary="休み: その月の最新の照合の結果 (admin)")
async def latest_compare(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    month: MonthQuery,
) -> dict[str, Any]:
    first = leave_compare.month_first_day(month)
    leave_month = await db.get(LeaveMonth, first)
    run: LeaveCompareRun | None = None
    if leave_month is not None and leave_month.last_compare_run_id is not None:
        run = await db.get(LeaveCompareRun, leave_month.last_compare_run_id)
    job = await _latest_job(db, OP_LEAVE_COMPARE, month)
    compared_at = _aware(leave_month.last_compared_at) if leave_month else None
    changed_at = _aware(leave_month.last_leave_changed_at) if leave_month else None
    return {
        "month": month,
        "run": _run_view(run) if run is not None else None,
        "job": _job_view(job) if job is not None else None,
        "last_compared_at": _iso(compared_at),
        "last_leave_changed_at": _iso(changed_at),
        # 照合の後に休みを足した・変えた・消した (比べ直しが要る)。
        "changed_since_compare": bool(
            compared_at is not None and changed_at is not None and changed_at > compared_at
        ),
        "evening_warning": evening_warning(),
    }


@router.get("/compare/{job_id}", summary="休み: 照合のジョブの進み具合と結果 (admin)")
async def get_compare(
    job_id: uuid.UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> dict[str, Any]:
    """``state``: ``fetching`` (取り直し中)・``comparing`` (差分を作成中)・``done``・
    ``compare_failed`` (差分を作れなかった・``compare_error``)・``failed``・``cancelled``。"""
    job = await _job_of(db, job_id, OP_LEAVE_COMPARE)
    run = await db.scalar(
        select(LeaveCompareRun)
        .where(LeaveCompareRun.kaipoke_job_id == job.id)
        .order_by(LeaveCompareRun.created_at.desc())
        .limit(1)
    )
    compare_error = (job.result_summary or {}).get("compare_error")
    if job.status in _ACTIVE:
        state = "fetching"
    elif job.status != "completed":
        state = job.status
    elif run is not None:
        state = "done"
    elif compare_error:
        state = "compare_failed"
    else:
        state = "comparing"
    return {
        "job": _job_view(job),
        "state": state,
        "run": _run_view(run) if run is not None else None,
        "compare_error": compare_error,
    }


@router.post("/compare/apply", summary="休み: 照合の差分を「合わせる」(admin)")
async def apply_compare(
    payload: CompareApplyRequest,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin"))],
) -> dict[str, Any]:
    items = [
        leave_compare.CompareAction(
            staff_id=item.staff_id,
            day=item.date,
            action=item.action,
            content_hash=item.content_hash,
            chosen_kind=item.chosen_kind,
        )
        for item in payload.items
    ]
    try:
        result = await leave_compare.apply_compare_actions(db, items=items, actor=user)
    except StaffLeaveError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return result.as_dict()


# --- カイポケへ送る ---------------------------------------------------------------


@router.post(
    "/outbound/start",
    status_code=status.HTTP_202_ACCEPTED,
    summary="休み: 未送信の休みをカイポケへ送る (ジョブを立ててすぐ返す・admin)",
)
async def start_outbound(
    payload: MonthRequest,
    background: BackgroundTasks,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin"))],
    kaipoke: Annotated[KaipokeClient, Depends(_kaipoke_dep)],
) -> dict[str, Any]:
    """照合がまだの月・送る休みが無いときは 422 (理由)。他のジョブが動いていれば 409。"""
    try:
        started = await leave_outbound.start_leave_outbound(
            db, month=payload.month, user_id=user.id
        )
    except RpaJobConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if started.job is None:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=started.reason)
    background.add_task(
        leave_outbound.run_leave_outbound_job, job_id=started.job.id, client=kaipoke
    )
    return {
        "job_id": str(started.job.id),
        "status": started.job.status,
        "month": payload.month,
        "count": started.count,
        "total": started.weeks,
        "needs_action": [n.as_dict() for n in started.plan.needs_action],
        "evening_warning": evening_warning(),
    }


# ⚠ 登録の順: ``/outbound/preview`` は ``/outbound/{job_id}`` より先。
@router.get("/outbound/preview", summary="休み: カイポケへ送る前の一覧 (admin)")
async def preview_outbound(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    month: MonthQuery,
) -> dict[str, Any]:
    plan = await leave_outbound.build_leave_send_plan(db, month)
    return {**plan.as_dict(), "evening_warning": evening_warning()}


@router.get("/outbound/{job_id}", summary="休み: 送信のジョブの進み具合と結果 (admin)")
async def get_outbound(
    job_id: uuid.UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> dict[str, Any]:
    job = await _job_of(db, job_id, OP_LEAVE_OUTBOUND)
    items = list((job.result_summary or {}).get("items") or [])
    return {
        "job": _job_view(job),
        "count": len((job.params or {}).get("items") or []),
        "items": items,
        "sent_count": sum(1 for i in items if i.get("outcome") == leave_outbound.OUTCOME_SENT),
        "failed_count": sum(1 for i in items if i.get("outcome") == leave_outbound.OUTCOME_FAILED),
        "skipped_count": sum(
            1 for i in items if i.get("outcome") == leave_outbound.OUTCOME_SKIPPED
        ),
        "needs_action": list((job.params or {}).get("needs_action") or []),
    }
