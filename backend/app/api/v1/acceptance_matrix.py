"""受け入れ枠マトリックス (acceptance matrix) endpoints — P1 / P4.

GET    /api/v1/acceptance-matrix                — 拠点×曜日×時間帯 ○△× 自動算出 (read-only)
PUT    /api/v1/acceptance-matrix/week-override  — 週別手動上書き 1 セルを upsert (admin/manager)
DELETE /api/v1/acceptance-matrix/week-override  — 週別手動上書き 1 セルを解除 (admin/manager)

自動算出値 (``auto_status``) に、常設手動 (``acceptance_calendar``) と週別手動
(``acceptance_calendar_week``) を重ねた ``effective_status`` (= 週別 ?? 常設 ?? 自動)
を返す。GET は read-only。週別上書きの編集は PUT/DELETE で行う。

ロジック本体は ``app.services.scheduling.acceptance_matrix_service``。
"""

from __future__ import annotations

import uuid
from datetime import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.core.deps import DbDep, require_role
from app.models.acceptance_calendar_week import AcceptanceCalendarWeek
from app.models.office import Office
from app.models.user import User
from app.schemas.v2.acceptance_matrix import (
    AcceptanceMatrixResponse,
    WeekOverrideRead,
    WeekOverrideUpsert,
)
from app.services.scheduling.acceptance_matrix_service import compute_acceptance_matrix

router = APIRouter()


@router.get(
    "",
    response_model=AcceptanceMatrixResponse,
    summary="受け入れ枠マトリックス (拠点×曜日×時間帯 ○△× 自動算出, read-only)",
)
async def get_acceptance_matrix(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager", "staff"))],
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
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
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
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
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
