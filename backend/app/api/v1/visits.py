"""Visit CRUD endpoints (Phase 2 domain router).

Staff role users may only see visits where they are assigned as primary,
secondary, or mentor staff; admin/manager see everything.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select

from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.models.user import User
from app.models.visit import Visit
from app.schemas.visit import VisitCreate, VisitRead, VisitUpdate

router = APIRouter()


def _staff_visibility_filter(staff_id: UUID):
    return or_(
        Visit.primary_staff_id == staff_id,
        Visit.secondary_staff_id == staff_id,
        Visit.mentor_staff_id == staff_id,
    )


@router.get("", response_model=list[VisitRead], summary="List visits")
async def list_visits(
    db: DbDep,
    user: CurrentActiveUser,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Visit]:
    if user.role not in {"admin", "manager", "staff"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")

    stmt = select(Visit).where(Visit.deleted_at.is_(None))
    if user.role == "staff":
        if user.staff_id is None:
            return []
        stmt = stmt.where(_staff_visibility_filter(user.staff_id))
    stmt = stmt.order_by(Visit.visit_date.desc(), Visit.start_time.desc()).limit(limit).offset(offset)
    rows = (await db.scalars(stmt)).all()
    return list(rows)


@router.get("/{visit_id}", response_model=VisitRead, summary="Get visit by id")
async def get_visit(
    visit_id: UUID,
    db: DbDep,
    user: CurrentActiveUser,
) -> Visit:
    if user.role not in {"admin", "manager", "staff"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")

    visit = await db.scalar(
        select(Visit).where(Visit.id == visit_id, Visit.deleted_at.is_(None))
    )
    if visit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if user.role == "staff":
        if user.staff_id is None or user.staff_id not in {
            visit.primary_staff_id,
            visit.secondary_staff_id,
            visit.mentor_staff_id,
        }:
            # Hide existence from non-assigned staff.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return visit


@router.post(
    "",
    response_model=VisitRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create visit",
)
async def create_visit(
    payload: VisitCreate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> Visit:
    visit = Visit(**payload.model_dump())
    db.add(visit)
    await db.commit()
    await db.refresh(visit)
    return visit


@router.patch("/{visit_id}", response_model=VisitRead, summary="Update visit")
async def update_visit(
    visit_id: UUID,
    payload: VisitUpdate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> Visit:
    visit = await db.scalar(
        select(Visit).where(Visit.id == visit_id, Visit.deleted_at.is_(None))
    )
    if visit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(visit, field, value)
    await db.commit()
    await db.refresh(visit)
    return visit


@router.delete(
    "/{visit_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete visit (admin only)",
)
async def delete_visit(
    visit_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> None:
    visit = await db.scalar(
        select(Visit).where(Visit.id == visit_id, Visit.deleted_at.is_(None))
    )
    if visit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    visit.deleted_at = func.now()
    await db.commit()
    return None
