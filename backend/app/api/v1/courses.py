"""Course CRUD endpoints (W2-BE4 / API 契約 §4).

`docs/plans/v2-allocation-redesign.md` v0.9 §4.5 に対応する Course
(コース) のリソース API。本チケットでは **CRUD のみ** を実装する。

- generate / fix / assign-staff の各エンドポイントは Wave 4 で追加予定
  (W4-BE8 / W4-BE9)。本ファイルは将来そこへ拡張される。

## RBAC (API 契約 §4)

- GET (list / detail) — admin / manager
- POST / PATCH         — admin / manager
- DELETE (soft delete) — admin only

## UNIQUE 制約

(iso_year, iso_week, weekday, code) UNIQUE — DB 側で担保。
重複した POST / PATCH は 409 Conflict を返す。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.deps import DbDep, require_role
from app.models.course import Course
from app.models.user import User
from app.schemas.course import CourseCreate, CourseRead, CourseUpdate
from app.schemas.v2.enums import CourseStatus

router = APIRouter()


async def _commit_or_409(db) -> None:
    """Translate IntegrityError into 409 / 422 (see patients.py / offices.py)."""
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        msg = str(exc).lower()
        if "unique" in msg or "duplicate" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Conflict: duplicate course (iso_year, iso_week, weekday, code)",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Validation error: invalid foreign key or check constraint",
        ) from exc


def _to_read(course: Course) -> CourseRead:
    return CourseRead.model_validate(course, from_attributes=True)


@router.get("", response_model=list[CourseRead], summary="List courses")
async def list_courses(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
    iso_year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
    iso_week: Annotated[int | None, Query(ge=1, le=53)] = None,
    weekday: Annotated[int | None, Query(ge=0, le=6)] = None,
    course_status: Annotated[CourseStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[CourseRead]:
    stmt = (
        select(Course)
        .where(Course.deleted_at.is_(None))
        .order_by(Course.iso_year, Course.iso_week, Course.weekday, Course.code)
        .limit(limit)
        .offset(offset)
    )
    if iso_year is not None:
        stmt = stmt.where(Course.iso_year == iso_year)
    if iso_week is not None:
        stmt = stmt.where(Course.iso_week == iso_week)
    if weekday is not None:
        stmt = stmt.where(Course.weekday == weekday)
    if course_status is not None:
        stmt = stmt.where(Course.course_status == course_status.value)

    rows = (await db.scalars(stmt)).all()
    return [_to_read(c) for c in rows]


@router.get("/{course_id}", response_model=CourseRead, summary="Get course by id")
async def get_course(
    course_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> CourseRead:
    course = await db.scalar(
        select(Course).where(Course.id == course_id, Course.deleted_at.is_(None))
    )
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return _to_read(course)


@router.post(
    "",
    response_model=CourseRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create course",
)
async def create_course(
    payload: CourseCreate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> CourseRead:
    # CourseCreate (=CourseV2Create) は Pydantic 側でフィールド型を担保しているため、
    # そのまま ORM へ流し込む。CourseStatus enum -> str.
    data = payload.model_dump(mode="json")
    # CourseStatus は v2 schema では enum (StrEnum). model_dump(mode='json') で str に。
    course = Course(**data)
    db.add(course)
    await _commit_or_409(db)
    await db.refresh(course)
    return _to_read(course)


@router.patch("/{course_id}", response_model=CourseRead, summary="Update course")
async def update_course(
    course_id: UUID,
    payload: CourseUpdate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> CourseRead:
    course = await db.scalar(
        select(Course).where(Course.id == course_id, Course.deleted_at.is_(None))
    )
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    update_data = payload.model_dump(mode="json", exclude_unset=True)
    for field, value in update_data.items():
        setattr(course, field, value)

    await _commit_or_409(db)
    await db.refresh(course)
    return _to_read(course)


@router.delete(
    "/{course_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete course (admin only)",
)
async def delete_course(
    course_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> None:
    course = await db.scalar(
        select(Course).where(Course.id == course_id, Course.deleted_at.is_(None))
    )
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    course.deleted_at = func.now()
    await db.commit()
    return None
