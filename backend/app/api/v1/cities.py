"""City CRUD endpoints (Phase 2 domain router).

Staff role is read-only (GET list/detail). Mutations are admin/manager;
soft-delete is admin only.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.core.deps import DbDep, require_role
from app.models.city import City
from app.models.user import User
from app.schemas.city import CityCreate, CityRead, CityUpdate

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


@router.get("", response_model=list[CityRead], summary="List cities")
async def list_cities(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "staff"))],
    q: Annotated[str | None, Query(description="Substring filter on name/prefecture")] = None,
    prefecture: Annotated[str | None, Query(description="Exact prefecture filter")] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[City]:
    stmt = (
        select(City)
        .where(City.deleted_at.is_(None))
        .order_by(City.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if prefecture:
        stmt = stmt.where(City.prefecture == prefecture)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(City.name.ilike(like), City.prefecture.ilike(like)))
    rows = (await db.scalars(stmt)).all()
    return list(rows)


@router.get("/{city_id}", response_model=CityRead, summary="Get city by id")
async def get_city(
    city_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "staff"))],
) -> City:
    city = await db.scalar(select(City).where(City.id == city_id, City.deleted_at.is_(None)))
    if city is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return city


@router.post(
    "",
    response_model=CityRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create city",
)
async def create_city(
    payload: CityCreate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> City:
    city = City(**payload.model_dump())
    db.add(city)
    await _commit_or_409(db)
    await db.refresh(city)
    return city


@router.patch("/{city_id}", response_model=CityRead, summary="Update city")
async def update_city(
    city_id: UUID,
    payload: CityUpdate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> City:
    city = await db.scalar(select(City).where(City.id == city_id, City.deleted_at.is_(None)))
    if city is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(city, field, value)
    await _commit_or_409(db)
    await db.refresh(city)
    return city


@router.delete(
    "/{city_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete city (admin only)",
)
async def delete_city(
    city_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> None:
    city = await db.scalar(select(City).where(City.id == city_id, City.deleted_at.is_(None)))
    if city is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    city.deleted_at = func.now()
    await db.commit()
    return None
