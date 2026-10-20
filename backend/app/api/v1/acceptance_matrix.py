"""受け入れ枠マトリックス (acceptance matrix) endpoints — P1 / P4 / P5.

GET    /api/v1/acceptance-matrix                    — 拠点×曜日×時間帯 ○△× 自動算出 (read-only)
GET    /api/v1/acceptance-matrix/strict             — 数週続けて空いているか (職員×日×時台・read-only)
POST   /api/v1/acceptance-matrix/strict/refresh     — カイポケの材料を取り直す (admin・202)
GET    /api/v1/acceptance-matrix/strict/refresh/latest   — 動いている取り直しのジョブ
GET    /api/v1/acceptance-matrix/strict/refresh/{job_id} — 取り直しの進み具合
PUT    /api/v1/acceptance-matrix/week-override      — 週別手動上書き 1 セルを upsert (admin/manager)
DELETE /api/v1/acceptance-matrix/week-override      — 週別手動上書き 1 セルを解除 (admin/manager)
PUT    /api/v1/acceptance-matrix/standing-override  — 常設(毎週)上書き 1 セルを upsert (admin/manager)
DELETE /api/v1/acceptance-matrix/standing-override  — 常設(毎週)上書き 1 セルを解除 (admin/manager)

自動算出値 (``auto_status``) に、常設手動 (``acceptance_calendar``) と週別手動
(``acceptance_calendar_week``) を重ねた ``effective_status`` (= 週別 ?? 常設 ?? 自動)
を返す。GET は read-only。週別=その週だけ、常設=毎週(解除するまで)。常設は
``office_id`` 省略で全拠点一括も可。コメントは notes で保存する。

ロジック本体は ``app.services.scheduling.acceptance_matrix_service``。
``/strict`` (設計書 ``docs/plans/acceptance-strict-design-2026-10-20.md`` §4) の本体は
``acceptance_strict`` (判定)・``acceptance_strict_data`` (材料)・``acceptance_strict_refresh``
(取り直しの範囲。仕組みは段階 0 の ``services/kaipoke/sources.py``)。
"""

from __future__ import annotations

import uuid
from datetime import time
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import DbDep, require_role
from app.models.acceptance_calendar import AcceptanceCalendar
from app.models.acceptance_calendar_week import AcceptanceCalendarWeek
from app.models.kaipoke_job import KaipokeJob
from app.models.office import Office
from app.models.user import User, normalize_user_role
from app.schemas.v2.acceptance_matrix import (
    AcceptanceMatrixResponse,
    StandingOverrideUpsert,
    WeekOverrideRead,
    WeekOverrideUpsert,
)
from app.services.kaipoke.sources import OP_ACCEPTANCE_REFRESH, RpaJobConflictError
from app.services.kaipoke_client import KaipokeClient, get_kaipoke_client
from app.services.scheduling.acceptance_matrix_service import compute_acceptance_matrix
from app.services.scheduling.acceptance_strict_data import (
    OfficeNotFoundError,
    build_strict_payload,
    job_view,
    jst_today,
    latest_refresh_job,
    round_for_staff,
)
from app.services.scheduling.acceptance_strict_refresh import run_refresh_job, start_refresh

router = APIRouter()


def _kaipoke_dep() -> KaipokeClient:
    """RPA のクライアント (試験は ``kaipoke_client.set_test_client`` で差し替える)。"""
    return get_kaipoke_client()


# ---------------------------------------------------------------------------
# 数週続けて空いているか (strict) — 設計書 acceptance-strict-design-2026-10-20.md §4
# ---------------------------------------------------------------------------


@router.get(
    "/strict",
    summary="受け入れ枠: 職員×日×時台の「入れる」(直近 8 週・休みの扱いの両方・read-only)",
)
async def get_strict_acceptance(
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin", "staff"))],
    office_id: Annotated[
        UUID, Query(description="拠点 ID (担い手の候補を絞る。訪問の行は絞らない)")
    ],
    plan_only: Annotated[
        bool,
        Query(description="開発用: すべての日をカイポケの予定で見る (本番では使えない)"),
    ] = False,
) -> dict[str, Any]:
    """担い手・見る週・休みの扱いは画面が集計する。DB に書かない。

    職員の権限の応答では休みの理由を「休み」「半休」に丸める。
    """
    if plan_only and get_settings().is_production:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="plan_only は開発用です")
    try:
        payload = await build_strict_payload(db, office_id, plan_only=plan_only)
    except OfficeNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="office not found"
        ) from exc
    if normalize_user_role(user.role) != "admin":
        return round_for_staff(payload)
    return payload


class StrictRefreshRequest(BaseModel):
    """取り直しの範囲。``recent`` = ふだん (日で決めた部分)、``all`` = 8 週ぶんすべて。"""

    scope: Literal["recent", "all"] = "recent"


@router.post(
    "/strict/refresh",
    status_code=status.HTTP_202_ACCEPTED,
    summary="受け入れ枠: カイポケの材料を取り直す (admin・ジョブを立ててすぐ返す)",
)
async def refresh_strict_sources(
    payload: StrictRefreshRequest,
    background: BackgroundTasks,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin"))],
    kaipoke: Annotated[KaipokeClient, Depends(_kaipoke_dep)],
) -> dict[str, Any]:
    """取る月・週を決め、段階 0 の仕組みでジョブを立てる。RPA を使う他のジョブがあれば 409。

    実体は応答の後に自前のセッションで走る (``BackgroundTasks``・予実比較と同じ理由)。
    """
    try:
        job, targets = await start_refresh(
            db, scope=payload.scope, today=jst_today(), user_id=user.id
        )
    except RpaJobConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    background.add_task(run_refresh_job, job_id=job.id, targets=targets, client=kaipoke)
    return {"job_id": str(job.id), "status": job.status, "total": len(targets)}


# ⚠ 登録の順: ``/strict/refresh/latest`` は ``/strict/refresh/{job_id}`` より先
# (後にすると "latest" が job_id として読まれ、UUID でないため 422 になる)。
@router.get(
    "/strict/refresh/latest",
    summary="受け入れ枠: 動いている取り直しのジョブ (無ければ直近の 1 本)",
)
async def latest_strict_refresh(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "staff"))],
) -> dict[str, Any]:
    job = await latest_refresh_job(db, active_only=True) or await latest_refresh_job(db)
    return {"job": job_view(job) if job else None}


@router.get(
    "/strict/refresh/{job_id}",
    summary="受け入れ枠: 取り直しの進み具合",
)
async def get_strict_refresh(
    job_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "staff"))],
) -> dict[str, Any]:
    job = await db.get(KaipokeJob, job_id, populate_existing=True)
    if job is None or (job.params or {}).get("op") != OP_ACCEPTANCE_REFRESH:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    return job_view(job)


@router.get(
    "",
    response_model=AcceptanceMatrixResponse,
    summary="受け入れ枠マトリックス (拠点×曜日×時間帯 ○△× 自動算出, read-only)",
)
async def get_acceptance_matrix(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "staff"))],
    iso_year: Annotated[int, Query(ge=2020, le=2100, description="ISO 年")],
    iso_week: Annotated[int, Query(ge=1, le=53, description="ISO 週")],
    office_id: Annotated[UUID | None, Query(description="拠点 ID (未指定なら全拠点)")] = None,
    service_minutes: Annotated[
        int, Query(ge=1, le=480, description="判定に用いる標準訪問の長さ (分)")
    ] = 60,
) -> AcceptanceMatrixResponse:
    """指定週・拠点の受け入れ枠マトリックスを返す (DB 書込なし)."""
    data = await compute_acceptance_matrix(
        db,
        iso_year=iso_year,
        iso_week=iso_week,
        office_ids=[office_id] if office_id is not None else None,
        service_minutes=service_minutes,
    )
    return AcceptanceMatrixResponse.model_validate(data)


@router.put(
    "/week-override",
    response_model=WeekOverrideRead,
    summary="週別手動上書き 1 セルを upsert (admin/manager)",
)
async def upsert_week_override(
    payload: WeekOverrideUpsert,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> WeekOverrideRead:
    """(office_id, iso_year, iso_week, weekday, time_slot) の週別上書きを upsert する.

    office_id 不正は 404。同一キーへの同時 PUT は UNIQUE 競合を捕捉して update に
    切替える (race-safe)。
    """
    office = await db.get(Office, payload.office_id)
    if office is None or office.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="office not found")

    def _key_filter():
        return (
            AcceptanceCalendarWeek.office_id == payload.office_id,
            AcceptanceCalendarWeek.iso_year == payload.iso_year,
            AcceptanceCalendarWeek.iso_week == payload.iso_week,
            AcceptanceCalendarWeek.weekday == payload.weekday,
            AcceptanceCalendarWeek.time_slot == payload.time_slot,
        )

    existing = await db.scalar(select(AcceptanceCalendarWeek).where(*_key_filter()))
    if existing is not None:
        existing.status = payload.status
        existing.notes = payload.notes
        await db.commit()
        await db.refresh(existing)
        return WeekOverrideRead.model_validate(existing)

    row = AcceptanceCalendarWeek(
        id=uuid.uuid4(),
        office_id=payload.office_id,
        iso_year=payload.iso_year,
        iso_week=payload.iso_week,
        weekday=payload.weekday,
        time_slot=payload.time_slot,
        status=payload.status,
        notes=payload.notes,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        # 同時 PUT 競合 (UNIQUE 違反) → 既存行を読み直して update に切替える.
        await db.rollback()
        existing = await db.scalar(select(AcceptanceCalendarWeek).where(*_key_filter()))
        if existing is None:
            raise
        existing.status = payload.status
        existing.notes = payload.notes
        await db.commit()
        await db.refresh(existing)
        return WeekOverrideRead.model_validate(existing)
    await db.refresh(row)
    return WeekOverrideRead.model_validate(row)


@router.delete(
    "/week-override",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="週別手動上書き 1 セルを解除 (admin/manager)",
)
async def delete_week_override(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    office_id: Annotated[UUID, Query()],
    iso_year: Annotated[int, Query(ge=2020, le=2100)],
    iso_week: Annotated[int, Query(ge=1, le=53)],
    weekday: Annotated[int, Query(ge=0, le=6)],
    time_slot: Annotated[time, Query(description="時間帯の開始時刻 (例 10:00:00)")],
) -> None:
    """週別上書きを解除する (常設 or 自動算出に戻す). 冪等 (無くても 204)."""
    await db.execute(
        delete(AcceptanceCalendarWeek).where(
            AcceptanceCalendarWeek.office_id == office_id,
            AcceptanceCalendarWeek.iso_year == iso_year,
            AcceptanceCalendarWeek.iso_week == iso_week,
            AcceptanceCalendarWeek.weekday == weekday,
            AcceptanceCalendarWeek.time_slot == time_slot,
        )
    )
    await db.commit()
    return None


# ---------------------------------------------------------------------------
# 常設(毎週)上書き — acceptance_calendar (毎週共通・全週一律). office_id 省略=全拠点.
# ---------------------------------------------------------------------------


async def _resolve_standing_office_ids(db: AsyncSession, office_id: UUID | None) -> list[UUID]:
    """office_id 指定なら存在チェックして単一、None なら全 (未削除) 拠点を返す."""
    if office_id is not None:
        office = await db.get(Office, office_id)
        if office is None or office.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="office not found")
        return [office_id]
    rows = await db.scalars(select(Office.id).where(Office.deleted_at.is_(None)))
    return list(rows.all())


@router.put(
    "/standing-override",
    summary="常設(毎週)上書き 1 セルを upsert (admin/manager, office_id省略=全拠点)",
)
async def upsert_standing_override(
    payload: StandingOverrideUpsert,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> dict[str, int]:
    """(office_id?, weekday, time_slot) の常設上書きを upsert する (毎週共通).

    ``office_id`` 省略時は全 (未削除) 拠点に一括適用 (例: 全社会議)。同一キーへの
    同時 PUT は UNIQUE 競合を捕捉して update に切替える (race-safe)。
    """
    office_ids = await _resolve_standing_office_ids(db, payload.office_id)

    async def _apply() -> None:
        for oid in office_ids:
            existing = await db.scalar(
                select(AcceptanceCalendar).where(
                    AcceptanceCalendar.office_id == oid,
                    AcceptanceCalendar.weekday == payload.weekday,
                    AcceptanceCalendar.time_slot == payload.time_slot,
                )
            )
            if existing is not None:
                existing.status = payload.status
                existing.notes = payload.notes
            else:
                db.add(
                    AcceptanceCalendar(
                        id=uuid.uuid4(),
                        office_id=oid,
                        weekday=payload.weekday,
                        time_slot=payload.time_slot,
                        status=payload.status,
                        notes=payload.notes,
                    )
                )

    # 同時 PUT による UNIQUE 競合は rollback 後に再試行 (2 回目以降は既存行を
    # update に切替えるため通常 1 回で収束). 最大 3 回で打ち切り 409.
    for _attempt in range(3):
        await _apply()
        try:
            await db.commit()
            break
        except IntegrityError:
            await db.rollback()
    else:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="concurrent conflict, please retry"
        )
    return {"applied_office_count": len(office_ids)}


@router.delete(
    "/standing-override",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="常設(毎週)上書き 1 セルを解除 (admin/manager, office_id省略=全拠点)",
)
async def delete_standing_override(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    weekday: Annotated[int, Query(ge=0, le=6)],
    time_slot: Annotated[time, Query(description="時間帯の開始時刻 (例 10:00:00)")],
    office_id: Annotated[UUID | None, Query(description="省略=全拠点まとめて解除")] = None,
) -> None:
    """常設上書きを解除する (週別 or 自動算出に戻す). 冪等 (無くても 204).

    office_id 省略時は全 (未削除) 拠点をまとめて解除する (削除済み拠点の行は触らない)。
    """
    target_ids = await _resolve_standing_office_ids(db, office_id)
    await db.execute(
        delete(AcceptanceCalendar).where(
            AcceptanceCalendar.weekday == weekday,
            AcceptanceCalendar.time_slot == time_slot,
            AcceptanceCalendar.office_id.in_(target_ids),
        )
    )
    await db.commit()
    return None
