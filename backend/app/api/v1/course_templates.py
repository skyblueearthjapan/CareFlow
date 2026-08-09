"""Course template (永続コーステンプレート) endpoints — W15-BE1.

GET    /api/v1/course-templates?office_id=...   — 一覧 (admin/manager/staff)
POST   /api/v1/course-templates                  — 新規 (admin/manager)
PATCH  /api/v1/course-templates/{id}             — 更新 (admin/manager)
DELETE /api/v1/course-templates/{id}             — 論理削除 (admin only)

UNIQUE 制約: (office_id, label) — 重複 → 409 Conflict
"""

from __future__ import annotations

import uuid
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import DbDep, require_role
from app.models.course_template import CourseTemplate
from app.models.office import Office
from app.models.user import User
from app.schemas.v2.course_template import (
    CourseTemplateCreate,
    CourseTemplateRead,
    CourseTemplateUpdate,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _commit_or_409(db: AsyncSession) -> None:
    """Commit. UNIQUE 違反は 409、それ以外の整合性エラーは 422 に変換."""
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        msg = str(exc).lower()
        if "unique" in msg or "duplicate" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Conflict: duplicate (office_id, label)",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Validation error: invalid foreign key or check constraint",
        ) from exc


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


async def _get_template_or_404(db: AsyncSession, template_id: UUID) -> CourseTemplate:
    """論理削除されていないテンプレートを取得、なければ 404."""
    tpl = await db.scalar(
        select(CourseTemplate).where(
            CourseTemplate.id == template_id,
            CourseTemplate.deleted_at.is_(None),
        )
    )
    if tpl is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="CourseTemplate not found",
        )
    return tpl


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=list[CourseTemplateRead],
    summary="List course templates by office (admin/manager/staff)",
)
async def list_course_templates(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "staff"))],
    office_id: Annotated[UUID, Query(description="拠点 ID (必須)")],
) -> list[CourseTemplateRead]:
    """指定拠点の全テンプレート (deleted_at IS NULL) を label 昇順で返す."""
    rows = (
        await db.scalars(
            select(CourseTemplate)
            .where(
                CourseTemplate.office_id == office_id,
                CourseTemplate.deleted_at.is_(None),
            )
            .order_by(CourseTemplate.label.asc(), CourseTemplate.created_at.asc())
        )
    ).all()
    return [CourseTemplateRead.model_validate(r) for r in rows]


@router.post(
    "",
    response_model=CourseTemplateRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create course template (admin/manager)",
)
async def create_course_template(
    payload: CourseTemplateCreate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> CourseTemplateRead:
    """新規テンプレート作成. UNIQUE (office_id, label) 違反は 409."""
    await _ensure_office_exists(db, payload.office_id)

    tpl = CourseTemplate(
        id=uuid.uuid4(),
        office_id=payload.office_id,
        label=payload.label,
        capacity_mon=payload.capacity_mon,
        capacity_tue=payload.capacity_tue,
        capacity_wed=payload.capacity_wed,
        capacity_thu=payload.capacity_thu,
        capacity_fri=payload.capacity_fri,
        capacity_sat=payload.capacity_sat,
        capacity_sun=payload.capacity_sun,
        notes=payload.notes,
    )
    db.add(tpl)
    await _commit_or_409(db)
    await db.refresh(tpl)
    return CourseTemplateRead.model_validate(tpl)


@router.patch(
    "/{template_id}",
    response_model=CourseTemplateRead,
    summary="Update course template (admin/manager)",
)
async def update_course_template(
    template_id: UUID,
    payload: CourseTemplateUpdate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> CourseTemplateRead:
    """部分更新. office_id は変更不可."""
    tpl = await _get_template_or_404(db, template_id)

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(tpl, field, value)

    await _commit_or_409(db)
    await db.refresh(tpl)
    return CourseTemplateRead.model_validate(tpl)


@router.delete(
    "/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete course template (admin only)",
)
async def delete_course_template(
    template_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> None:
    """論理削除. 既に削除済みは 404."""
    tpl = await _get_template_or_404(db, template_id)
    tpl.deleted_at = sa_func.now()
    await db.commit()
    return None
