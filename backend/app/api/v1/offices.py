"""Office CRUD endpoints (Phase 2 domain router).

Staff role is read-only (GET list/detail). Mutations are admin/manager;
soft-delete is admin only.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core.deps import DbDep, require_role
from app.models.office import Office, OfficeCity
from app.models.user import User
from app.schemas.office import OfficeCreate, OfficeRead, OfficeUpdate

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


def _to_read(office: Office) -> OfficeRead:
    """Construct OfficeRead and inject allowed_cities from M2M relationship."""
    data = OfficeRead.model_validate(office, from_attributes=True).model_dump()
    data["allowed_cities"] = [oc.city_id for oc in office.cities]
    return OfficeRead.model_validate(data)


@router.get("", response_model=list[OfficeRead], summary="List offices")
async def list_offices(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager", "staff"))],
    q: Annotated[str | None, Query(description="Substring filter on name/code")] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[OfficeRead]:
    stmt = (
        select(Office)
        .where(Office.deleted_at.is_(None))
        .options(selectinload(Office.cities))
        .order_by(Office.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Office.name.ilike(like), Office.code.ilike(like)))
    rows = (await db.scalars(stmt)).all()
    return [_to_read(o) for o in rows]


@router.get("/{office_id}", response_model=OfficeRead, summary="Get office by id")
async def get_office(
    office_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager", "staff"))],
) -> OfficeRead:
    office = await db.scalar(
        select(Office)
        .where(Office.id == office_id, Office.deleted_at.is_(None))
        .options(selectinload(Office.cities))
    )
    if office is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return _to_read(office)


async def _replace_office_cities(db, office_id: UUID, city_ids: list[UUID]) -> None:
    """Delete existing M2M rows then re-add the given city ids."""
    await db.execute(delete(OfficeCity).where(OfficeCity.office_id == office_id))
    for city_id in city_ids:
        db.add(OfficeCity(office_id=office_id, city_id=city_id))


@router.post(
    "",
    response_model=OfficeRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create office",
)
async def create_office(
    payload: OfficeCreate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> OfficeRead:
    data = payload.model_dump(exclude={"allowed_cities"})
    office = Office(**data)
    db.add(office)
    await db.flush()  # populate office.id

    if payload.allowed_cities:
        await _replace_office_cities(db, office.id, payload.allowed_cities)

    await _commit_or_409(db)

    refreshed = await db.scalar(
        select(Office)
        .where(Office.id == office.id)
        .options(selectinload(Office.cities))
    )
    assert refreshed is not None
    return _to_read(refreshed)


@router.patch("/{office_id}", response_model=OfficeRead, summary="Update office")
async def update_office(
    office_id: UUID,
    payload: OfficeUpdate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> OfficeRead:
    office = await db.scalar(
        select(Office)
        .where(Office.id == office_id, Office.deleted_at.is_(None))
        .options(selectinload(Office.cities))
    )
    if office is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    update_data = payload.model_dump(exclude_unset=True)
    allowed_cities = update_data.pop("allowed_cities", None)
    for field, value in update_data.items():
        setattr(office, field, value)

    if allowed_cities is not None:
        await _replace_office_cities(db, office.id, allowed_cities)

    await _commit_or_409(db)

    refreshed = await db.scalar(
        select(Office)
        .where(Office.id == office.id)
        .options(selectinload(Office.cities))
    )
    assert refreshed is not None
    return _to_read(refreshed)


@router.delete(
    "/{office_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete office (admin only)",
)
async def delete_office(
    office_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> None:
    office = await db.scalar(
        select(Office).where(Office.id == office_id, Office.deleted_at.is_(None))
    )
    if office is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    office.deleted_at = func.now()
    await db.commit()
    return None
