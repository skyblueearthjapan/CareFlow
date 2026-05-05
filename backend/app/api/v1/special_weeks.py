"""SpecialWeek CRUD endpoints (Wave 3-E).

Header (`SpecialWeek`) + child items (`SpecialWeekItem`) are managed
together: create / update accept the items array on the same payload and
fully replace the child rows on update. A separate per-item endpoint is
intentionally omitted to keep the contract small — clients always submit
the full set.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core.deps import DbDep, require_role
from app.models.special_week import SpecialWeek, SpecialWeekItem
from app.models.user import User
from app.schemas.special_week import (
    SpecialWeekCreate,
    SpecialWeekItemCreate,
    SpecialWeekRead,
    SpecialWeekUpdate,
)

router = APIRouter()


async def _commit_or_409(db) -> None:
    """Commit and translate IntegrityError into 409/422 (see patients.py)."""
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


def _build_items(
    special_week_id: UUID, items: list[SpecialWeekItemCreate]
) -> list[SpecialWeekItem]:
    """Materialise child rows from create payloads."""
    return [
        SpecialWeekItem(
            special_week_id=special_week_id,
            **item.model_dump(),
        )
        for item in items
    ]


@router.get(
    "", response_model=list[SpecialWeekRead], summary="List special weeks"
)
async def list_special_weeks(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
    patient_id: Annotated[UUID | None, Query()] = None,
    status_filter: Annotated[
        str | None, Query(alias="status", description="draft / applied / cancelled")
    ] = None,
    week_from: Annotated[date | None, Query()] = None,
    week_to: Annotated[date | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[SpecialWeek]:
    stmt = (
        select(SpecialWeek)
        .options(selectinload(SpecialWeek.items))
        .order_by(SpecialWeek.week_start.desc(), SpecialWeek.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if patient_id is not None:
        stmt = stmt.where(SpecialWeek.patient_id == patient_id)
    if status_filter:
        stmt = stmt.where(SpecialWeek.status == status_filter)
    if week_from is not None:
        stmt = stmt.where(SpecialWeek.week_end >= week_from)
    if week_to is not None:
        stmt = stmt.where(SpecialWeek.week_start <= week_to)
    rows = (await db.scalars(stmt)).unique().all()
    return list(rows)


@router.get(
    "/{special_week_id}",
    response_model=SpecialWeekRead,
    summary="Get special week by id",
)
async def get_special_week(
    special_week_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> SpecialWeek:
    sw = await db.scalar(
        select(SpecialWeek)
        .where(SpecialWeek.id == special_week_id)
        .options(selectinload(SpecialWeek.items))
    )
    if sw is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return sw


@router.post(
    "",
    response_model=SpecialWeekRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create special week (header + items)",
)
async def create_special_week(
    payload: SpecialWeekCreate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> SpecialWeek:
    header_data = payload.model_dump(exclude={"items"})
    sw = SpecialWeek(**header_data)
    db.add(sw)
    await db.flush()  # populate sw.id

    for item in _build_items(sw.id, payload.items):
        db.add(item)

    await _commit_or_409(db)

    refreshed = await db.scalar(
        select(SpecialWeek)
        .where(SpecialWeek.id == sw.id)
        .options(selectinload(SpecialWeek.items))
    )
    assert refreshed is not None
    return refreshed


@router.patch(
    "/{special_week_id}",
    response_model=SpecialWeekRead,
    summary="Update special week (header; items fully replaced if provided)",
)
async def update_special_week(
    special_week_id: UUID,
    payload: SpecialWeekUpdate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> SpecialWeek:
    sw = await db.scalar(
        select(SpecialWeek)
        .where(SpecialWeek.id == special_week_id)
        .options(selectinload(SpecialWeek.items))
    )
    if sw is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    update_data = payload.model_dump(exclude_unset=True)
    items = update_data.pop("items", None)
    for field, value in update_data.items():
        setattr(sw, field, value)

    if items is not None:
        # Full replacement: drop existing children, insert the new set.
        await db.execute(
            delete(SpecialWeekItem).where(
                SpecialWeekItem.special_week_id == special_week_id
            )
        )
        for item in _build_items(sw.id, [SpecialWeekItemCreate(**i) for i in items]):
            db.add(item)

    await _commit_or_409(db)

    refreshed = await db.scalar(
        select(SpecialWeek)
        .where(SpecialWeek.id == sw.id)
        .options(selectinload(SpecialWeek.items))
    )
    assert refreshed is not None
    return refreshed


@router.delete(
    "/{special_week_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete special week (admin only) — cascades to items",
)
async def delete_special_week(
    special_week_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> None:
    sw = await db.scalar(
        select(SpecialWeek).where(SpecialWeek.id == special_week_id)
    )
    if sw is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await db.delete(sw)
    await db.commit()
    return None
