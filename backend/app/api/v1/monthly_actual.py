"""月の実績 (訪問記録の「月の実績」タブ・管理者のみ).

正典: ``docs/plans/kaipoke-actual-write-design-2026-11-01.md`` §5-3。らく助の QR 実績 (正) と
カイポケの実績を突き合わせ、選んだ行だけ RPA でカイポケの実績の時刻を書き換え、確かめ、
月の一覧 (Excel) を出す。判定の決まりは ``app.services.monthly_actual.judge``。

POST   /api/v1/monthly-actual/scheduled/run      — 予約の時刻を過ぎた反映を始める (VPS の cron 用)
GET    /api/v1/monthly-actual/{month}            — 判定の一覧と件数 (新しい実績があれば判定し直す)
POST   /api/v1/monthly-actual/{month}/refresh    — カイポケの実績を取り直して判定し直す (202)
POST   /api/v1/monthly-actual/{month}/judge      — 保存済みの実績で判定し直す (RPA を呼ばない)
PATCH  /api/v1/monthly-actual/{month}/rows/{id}  — 反映する／しない (``/rows/{id}`` でも同じ)
POST   /api/v1/monthly-actual/{month}/apply      — 選んだ行を RPA へ (dry_run 既定 true・202)
GET    /api/v1/monthly-actual/{month}/apply/status — 反映の進み具合と行ごとの結果
POST   /api/v1/monthly-actual/{month}/schedule   — 夜に流す予約 (既定は今夜 22:00 JST)
DELETE /api/v1/monthly-actual/{month}/schedule   — 予約の取り消し
GET    /api/v1/monthly-actual/{month}/export.xlsx — 月の一覧 (4 シート)
GET    /api/v1/monthly-actual/{month}/staff-summary — 看護師別の月の実績 (JSON)
GET    /api/v1/monthly-actual/{month}/report     — 看護師別の月の実績 (A4 縦の印刷 HTML)

看護師別はらく助の実績だけから数える (カイポケの実績・作業が無い月でも出せる)。数え方は
``app.services.monthly_actual.staff_report`` (ダッシュボードのスタッフ別の実績と同じ入口)。

ジョブは段階 0 の ``KaipokeJob`` (他の RPA のジョブが動いていれば 409)。実体は応答の後に
自前のセッションで走る (``BackgroundTasks``)。画面は状態を数秒おきに読む。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Response, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.core.deps import DbDep, require_role
from app.models.business_profile import BusinessProfile
from app.models.kaipoke_job import KaipokeJob
from app.models.user import User
from app.services.kaipoke.sources import RpaJobConflictError, evening_warning
from app.services.kaipoke_client import KaipokeApiError, KaipokeClient, get_kaipoke_client
from app.services.monthly_actual import service as svc
from app.services.monthly_actual.export_xlsx import build_monthly_actual_xlsx
from app.services.monthly_actual.staff_report import build_month_staff_report
from app.services.monthly_actual.staff_report_html import render_month_staff_report_html

router = APIRouter(prefix="/monthly-actual", tags=["monthly-actual"])

_MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
MonthPath = Annotated[str, Path(pattern=_MONTH_PATTERN, description="YYYY-MM")]
AdminDep = Annotated[User, Depends(require_role("admin"))]
_NO_STORE = {"Cache-Control": "no-store"}


def _kaipoke_dep() -> KaipokeClient:
    """RPA のクライアント (試験は ``kaipoke_client.set_test_client`` で差し替える)。"""
    return get_kaipoke_client()


KaipokeDep = Annotated[KaipokeClient, Depends(_kaipoke_dep)]


class RowPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected: bool


class ApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: 既定は試し (登録の直前まで進めて閉じる)。
    dry_run: bool = True
    #: 送る行 (省略 = 選んだ行すべて)。
    row_ids: list[uuid.UUID] | None = Field(default=None, max_length=3000)
    #: 前に失敗した行だけ流し直す。
    only_failed: bool = False


class ScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: 既定は試し (反映の API とそろえる)。本番の予約は画面が明示で false を送る。
    dry_run: bool = True
    #: 流す時刻 (省略 = 今夜 22:00 JST。過ぎていれば翌日の 22:00)。
    at: datetime | None = None


def _raise(exc: svc.MonthlyActualError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


async def _ensure_rpa_idle(kaipoke: KaipokeClient) -> None:
    """RPA が塞がっていれば 409 (予実比較の取り直しと同じ止め)。"""
    try:
        raw = await kaipoke.status()
    except KaipokeApiError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    if bool((raw.get("current_task") or {}).get("running")):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="kaipoke busy")


async def _month_view(db: DbDep, month: str) -> dict[str, Any]:
    run = await svc.get_run(db, month)
    snap = await svc.latest_actual_snapshot(db, month)
    rows = await svc.load_rows(db, run.id) if run is not None else []
    return {
        "month": month,
        "is_past_month": svc.is_past_month(month),
        "kaipoke": (
            {
                "snapshot_id": str(snap.id),
                "fetched_at": snap.fetched_at.isoformat() if snap.fetched_at else None,
                "row_count": snap.row_count,
                "source_op": snap.source_op,
            }
            if snap is not None
            else None
        ),
        "run": await svc.run_view(db, run) if run is not None else None,
        "counts": (run.counts if run is not None and run.counts else svc.rules.count_statuses([])),
        "selected_count": len(svc.choose_rows(rows)),
        "apply_counts": svc.apply_counts(rows),
        "care_record_counts": svc.care_record_counts(rows),
        "rows": [svc.row_view(r) for r in rows],
        "evening_warning": evening_warning(),
    }


async def _station_name(db: DbDep) -> str | None:
    profile = await db.scalar(select(BusinessProfile).where(BusinessProfile.is_singleton.is_(True)))
    return profile.station_name if profile is not None else None


# ⚠ 登録の順: ``/scheduled/run``・``/rows/{row_id}`` は ``/{month}/...`` より先。
@router.post(
    "/scheduled/run",
    summary="月の実績: 予約の時刻を過ぎた反映を始める (VPS の cron 用・10 分おき推奨・admin)",
)
async def run_scheduled(
    background: BackgroundTasks,
    db: DbDep,
    _admin: AdminDep,
    kaipoke: KaipokeDep,
) -> dict[str, Any]:
    tick = await svc.run_due_schedules(db)
    for started in tick.started:
        background.add_task(svc.run_apply_job, job_id=uuid.UUID(started["job_id"]), client=kaipoke)
    return tick.as_dict()


@router.patch("/rows/{row_id}", summary="月の実績: 反映する／しない (月を付けない形・admin)")
async def patch_row_by_id(
    row_id: uuid.UUID,
    payload: RowPatch,
    db: DbDep,
    user: AdminDep,
) -> dict[str, Any]:
    """``PATCH /{month}/rows/{row_id}`` と同じ。月は行の作業から決める。"""
    try:
        row = await svc.set_selected(
            db, month=None, row_id=row_id, selected=payload.selected, user_id=user.id
        )
    except svc.MonthlyActualError as exc:
        await db.rollback()
        _raise(exc)
    await db.commit()
    return svc.row_view(row)


@router.get("/{month}", summary="月の実績: 判定の一覧と件数 (admin)")
async def get_month(month: MonthPath, db: DbDep, _admin: AdminDep) -> dict[str, Any]:
    """カイポケの実績が判定より新しければ (連携の予実比較で取り直したときなど) 判定し直す。

    反映の途中は判定し直さない (送った行の結果を消さないため)。
    """
    run = await svc.get_run(db, month)
    snap = await svc.latest_actual_snapshot(db, month)
    applying = run is not None and await svc.active_job(db, run.apply_job_id) is not None
    if snap is not None and not applying and (run is None or run.snapshot_id != snap.id):
        await svc.judge_month(db, month, snapshot=snap)
        await db.commit()
    return await _month_view(db, month)


@router.post(
    "/{month}/refresh",
    status_code=status.HTTP_202_ACCEPTED,
    summary="月の実績: カイポケの実績を取り直して判定し直す (ジョブ・admin)",
)
async def refresh_month(
    month: MonthPath,
    background: BackgroundTasks,
    db: DbDep,
    user: AdminDep,
    kaipoke: KaipokeDep,
) -> dict[str, Any]:
    await _ensure_rpa_idle(kaipoke)
    try:
        job = await svc.start_refresh(db, month=month, user_id=user.id)
    except svc.MonthlyActualError as exc:
        _raise(exc)
    except RpaJobConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    background.add_task(svc.run_refresh_job, job_id=job.id, client=kaipoke)
    return {
        "job_id": str(job.id),
        "status": job.status,
        "month": month,
        "evening_warning": evening_warning(),
    }


@router.post("/{month}/judge", summary="月の実績: 保存済みの実績で判定し直す (admin)")
async def rejudge_month(month: MonthPath, db: DbDep, _admin: AdminDep) -> dict[str, Any]:
    """打刻履歴で時刻を合わせた後などに。RPA は呼ばない。反映の途中は 409。"""
    run = await svc.get_run(db, month)
    if run is not None and await svc.active_job(db, run.apply_job_id) is not None:
        raise HTTPException(
            status_code=409, detail="反映の途中です。終わってから判定し直してください"
        )
    try:
        await svc.judge_month(db, month)
    except svc.MonthlyActualError as exc:
        await db.rollback()
        _raise(exc)
    await db.commit()
    return await _month_view(db, month)


@router.patch("/{month}/rows/{row_id}", summary="月の実績: 反映する／しない (admin)")
async def patch_row(
    month: MonthPath,
    row_id: uuid.UUID,
    payload: RowPatch,
    db: DbDep,
    user: AdminDep,
) -> dict[str, Any]:
    try:
        row = await svc.set_selected(
            db, month=month, row_id=row_id, selected=payload.selected, user_id=user.id
        )
    except svc.MonthlyActualError as exc:
        await db.rollback()
        _raise(exc)
    await db.commit()
    return svc.row_view(row)


@router.post(
    "/{month}/apply",
    status_code=status.HTTP_202_ACCEPTED,
    summary="月の実績: 選んだ行の実績の時刻をカイポケへ書き換える (dry_run 既定・ジョブ・admin)",
)
async def apply_month(
    month: MonthPath,
    payload: ApplyRequest,
    background: BackgroundTasks,
    db: DbDep,
    user: AdminDep,
    kaipoke: KaipokeDep,
) -> dict[str, Any]:
    """過去の月だけ。反映の途中・送る行が無い・同じカイポケの行を 2 件が指す → 409 / 422。"""
    await _ensure_rpa_idle(kaipoke)
    try:
        job = await svc.start_apply(
            db,
            month=month,
            user_id=user.id,
            dry_run=payload.dry_run,
            row_ids=payload.row_ids,
            only_failed=payload.only_failed,
        )
    except svc.MonthlyActualError as exc:
        await db.rollback()
        _raise(exc)
    except RpaJobConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    background.add_task(svc.run_apply_job, job_id=job.id, client=kaipoke)
    return {
        "job_id": str(job.id),
        "status": job.status,
        "month": month,
        "dry_run": payload.dry_run,
        "total": len((job.params or {}).get("row_ids") or []),
        "evening_warning": evening_warning(),
    }


@router.get("/{month}/apply/status", summary="月の実績: 反映の進み具合と行ごとの結果 (admin)")
async def apply_status(
    month: MonthPath, db: DbDep, _admin: AdminDep, kaipoke: KaipokeDep
) -> dict[str, Any]:
    """反映が動いていれば RPA の結果を取りに行き、行ごとに書いてから返す。"""
    run = await svc.get_run(db, month)
    if run is None:
        raise HTTPException(status_code=404, detail="この月の作業がありません")
    await svc.poll_apply_status(db, kaipoke, run)
    run = await svc.get_run(db, month)
    assert run is not None
    job = (
        await db.get(KaipokeJob, run.apply_job_id, populate_existing=True)
        if run.apply_job_id is not None
        else None
    )
    rows = await svc.load_rows(db, run.id)
    row_ids = set((job.params or {}).get("row_ids") or []) if job is not None else set()
    sent = [r for r in rows if str(r.id) in row_ids]
    counts, care_counts = svc.job_counts(job, sent)
    return {
        "month": month,
        "state": run.state,
        "job": svc.job_view(job),
        "running": job is not None and job.status in ("pending", "running"),
        "counts": counts,
        "care_record_counts": care_counts,
        "last_apply": run.last_apply,
        "verify_result": run.verify_result,
        "rows": [svc.row_view(r) for r in sent],
    }


@router.post("/{month}/schedule", summary="月の実績: 夜に流す予約 (既定は今夜 22:00 JST・admin)")
async def schedule_month(
    month: MonthPath, payload: ScheduleRequest, db: DbDep, user: AdminDep
) -> dict[str, Any]:
    """予約の時刻を過ぎると、VPS の cron (``POST /monthly-actual/scheduled/run``) が反映を始める。"""
    try:
        run = await svc.schedule_apply(
            db, month=month, user_id=user.id, dry_run=payload.dry_run, at=payload.at
        )
    except svc.MonthlyActualError as exc:
        await db.rollback()
        _raise(exc)
    await db.commit()
    return await svc.run_view(db, run)


@router.delete("/{month}/schedule", summary="月の実績: 予約の取り消し (admin)")
async def cancel_schedule(month: MonthPath, db: DbDep, _admin: AdminDep) -> dict[str, Any]:
    try:
        run = await svc.cancel_schedule(db, month=month)
    except svc.MonthlyActualError as exc:
        await db.rollback()
        _raise(exc)
    await db.commit()
    return await svc.run_view(db, run)


@router.get(
    "/{month}/export.xlsx",
    summary="月の実績: 月の一覧 (Excel 4 シート・admin)",
    response_class=Response,
)
async def export_month(month: MonthPath, db: DbDep, _admin: AdminDep) -> Response:
    run = await svc.get_run(db, month)
    if run is None:
        raise HTTPException(status_code=404, detail="この月の作業がありません")
    rows = await svc.load_rows(db, run.id)
    body = build_monthly_actual_xlsx(
        rows,
        month=month,
        station_name=await _station_name(db),
        kaipoke_fetched_at=run.kaipoke_fetched_at,
        generated_on=datetime.now(UTC).astimezone(svc.JST).date(),
        staff_report=await build_month_staff_report(db, month),
    )
    filename = f"monthly-actual_{month}.xlsx"
    label = quote(f"{int(month[:4])}年{int(month[5:])}月 訪問の実績.xlsx")
    return Response(
        content=body,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{label}",
            **_NO_STORE,
        },
    )


@router.get("/{month}/staff-summary", summary="月の実績: 看護師別の月の実績 (JSON・admin)")
async def staff_summary(month: MonthPath, db: DbDep, _admin: AdminDep) -> Response:
    report = await build_month_staff_report(db, month)
    return JSONResponse(report.as_summary(), headers=_NO_STORE)


@router.get(
    "/{month}/report",
    response_class=HTMLResponse,
    summary="月の実績: 看護師別の月の実績 (A4 縦の印刷 HTML・admin)",
)
async def staff_report(month: MonthPath, db: DbDep, _admin: AdminDep) -> HTMLResponse:
    report = await build_month_staff_report(db, month)
    html_doc = render_month_staff_report_html(
        report, station_name=await _station_name(db), generated_at=datetime.now(UTC)
    )
    return HTMLResponse(html_doc, headers=_NO_STORE)
