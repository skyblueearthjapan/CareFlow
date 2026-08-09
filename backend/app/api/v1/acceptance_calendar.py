"""Acceptance calendar (受入カレンダー) endpoints — W15-BE1.

GET /api/v1/acceptance-calendar?office_id=...   — 全エントリ取得 (admin/manager/staff)
PUT /api/v1/acceptance-calendar                  — bulk upsert (admin/manager)

PUT は冪等: 当該 office_id の既存エントリを全削除 → 一括 INSERT。
同一 (weekday, time_slot) を複数含むリクエストは 422。
"""

from __future__ import annotations

import uuid
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import DbDep, require_role
from app.models.acceptance_calendar import AcceptanceCalendar
from app.models.office import Office
from app.models.user import User
from app.schemas.v2.acceptance import (
    AcceptanceCalendarBulkUpsert,
    AcceptanceCalendarRead,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _ensure_office_exists(db: AsyncSession, office_id: UUID) -> Office:
    """office_id 不正は 422 (FK違反相当)."""
    office = await db.scalar(
        select(Office).where(Office.id == office_id, Office.deleted_at.is_(None))
    )
    if office is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"office_id {office_id} not found",
        )
    return office


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=list[AcceptanceCalendarRead],
    summary="List acceptance calendar entries by office (admin/manager/staff)",
)
async def list_acceptance_calendar(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "staff"))],
    office_id: Annotated[UUID, Query(description="拠点 ID (必須)")],
) -> list[AcceptanceCalendarRead]:
    """指定拠点の全エントリを weekday + time_slot 昇順で返す."""
    rows = (
        await db.scalars(
            select(AcceptanceCalendar)
            .where(AcceptanceCalendar.office_id == office_id)
            .order_by(
                AcceptanceCalendar.weekday.asc(),
                AcceptanceCalendar.time_slot.asc(),
            )
        )
    ).all()
    return [AcceptanceCalendarRead.model_validate(r) for r in rows]


@router.put(
    "",
    response_model=list[AcceptanceCalendarRead],
    summary="Bulk upsert acceptance calendar (admin/manager)",
)
async def upsert_acceptance_calendar(
    payload: AcceptanceCalendarBulkUpsert,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> list[AcceptanceCalendarRead]:
    """1 TX で当該 office_id の既存エントリを削除 → entries を INSERT.

    バリデーション:
      - office_id 不正 → 422
      - 同一 (weekday, time_slot) 重複 → 422
    """
    await _ensure_office_exists(db, payload.office_id)

    # 重複 (weekday, time_slot) チェック
    seen: set[tuple[int, str]] = set()
    for entry in payload.entries:
        key = (entry.weekday, entry.time_slot.isoformat())
        if key in seen:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Duplicate (weekday={entry.weekday}, time_slot={entry.time_slot}) in request"
                ),
            )
        seen.add(key)

    # 1 TX: 既存全削除 → INSERT
    await db.execute(
        delete(AcceptanceCalendar).where(AcceptanceCalendar.office_id == payload.office_id)
    )

    for entry in payload.entries:
        db.add(
            AcceptanceCalendar(
                id=uuid.uuid4(),
                office_id=payload.office_id,
                weekday=entry.weekday,
                time_slot=entry.time_slot,
                status=entry.status,
                notes=entry.notes,
            )
        )

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        msg = str(exc).lower()
        if "unique" in msg or "duplicate" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Conflict: duplicate (office_id, weekday, time_slot)",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Validation error",
        ) from exc

    rows = (
        await db.scalars(
            select(AcceptanceCalendar)
            .where(AcceptanceCalendar.office_id == payload.office_id)
            .order_by(
                AcceptanceCalendar.weekday.asc(),
                AcceptanceCalendar.time_slot.asc(),
            )
        )
    ).all()
    return [AcceptanceCalendarRead.model_validate(r) for r in rows]
