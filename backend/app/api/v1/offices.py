"""Office CRUD endpoints (Phase 2 domain router).

Staff role is read-only (GET list/detail). Mutations are admin/manager;
soft-delete is admin only.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.deps import DbDep, require_role
from app.models.office import Office
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


@router.get("", response_model=list[OfficeRead], summary="List offices")
async def list_offices(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager", "staff"))],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Office]:
    stmt = (
        select(Office)
        .where(Office.deleted_at.is_(None))
        .order_by(Office.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.scalars(stmt)).all()
    return list(rows)


@router.get("/{office_id}", response_model=OfficeRead, summary="Get office by id")
async def get_office(
    office_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager", "staff"))],
) -> Office:
    office = await db.scalar(
        select(Office).where(Office.id == office_id, Office.deleted_at.is_(None))
    )
    if office is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return office


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
) -> Office:
    office = Office(**payload.model_dump())
    db.add(office)
    await _commit_or_409(db)
    await db.refresh(office)
    return office


@router.patch("/{office_id}", response_model=OfficeRead, summary="Update office")
async def update_office(
    office_id: UUID,
    payload: OfficeUpdate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> Office:
    office = await db.scalar(
        select(Office).where(Office.id == office_id, Office.deleted_at.is_(None))
    )
    if office is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(office, field, value)
    await _commit_or_409(db)
    await db.refresh(office)
    return office


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
