"""Course-scoped staff companion assignments endpoints (W15-FE Phase 5 F-1).

GET    /api/v1/staff-companion-assignments?course_template_id=&iso_year=&iso_week=
       -> list[StaffCompanionAssignmentV2Read]
       RBAC: admin/manager/staff

PATCH  /api/v1/staff-companion-assignments/{assignment_id}
       Body: { "pair_role": "primary" | "support" | null }
       -> StaffCompanionAssignmentV2Read
       RBAC: admin/manager only

Note (W15 schema limitation):
    The current ``staff_companion_assignments`` table is keyed on
    ``(trainee_staff_id, weekday, part)`` and has *no* ``course_template_id``,
    ``course_id``, ``iso_year`` or ``iso_week`` column. Therefore the GET
    endpoint currently accepts those query parameters for API-contract
    compatibility with the W15 FE, but cannot filter by them.

    To preserve the existing FE behaviour (graceful empty fallback), the GET
    endpoint returns ``[]`` until the schema is extended in Phase 6+ to
    associate assignments with a course template / week.

    The PATCH endpoint is fully functional today since it operates on a
    single assignment by primary key.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.core.deps import DbDep, require_role
from app.models.staff_companion_assignment import StaffCompanionAssignment
from app.models.user import User
from app.schemas.v2.staff_companion_assignment import StaffCompanionAssignmentV2Read

router = APIRouter()


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class PairRoleUpdate(BaseModel):
    """PATCH /staff-companion-assignments/{id} request body."""

    model_config = ConfigDict(extra="forbid")

    pair_role: Literal["primary", "support"] | None = None


# ---------------------------------------------------------------------------
# GET /api/v1/staff-companion-assignments
# ---------------------------------------------------------------------------


@router.get(
    "/staff-companion-assignments",
    response_model=list[StaffCompanionAssignmentV2Read],
    summary="コーステンプレート×週の補佐スタッフ割当一覧",
)
async def list_staff_companion_assignments(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager", "staff"))],
    course_template_id: Annotated[UUID, Query(description="対象コーステンプレート ID")],
    iso_year: Annotated[int, Query(ge=2000, le=2100, description="ISO 年")],
    iso_week: Annotated[int, Query(ge=1, le=53, description="ISO 週番号")],
) -> list[StaffCompanionAssignmentV2Read]:
    """指定コーステンプレート × 週の補佐 (companion) 割当を取得する.

    TODO (Phase 6+):
        ``staff_companion_assignments`` テーブルに ``course_template_id`` /
        ``iso_year`` / ``iso_week`` 列 (または同等の関連テーブル) を追加し、
        ここで実際にフィルタする。それまでは空配列を返し、FE の graceful
        fallback に依存する。

        Query パラメータは将来の互換性のため必須化したまま受理する。
    """
    # NOTE: schema does not yet associate assignments with a course/week,
    # so we can only return empty until the schema is extended.
    # The query parameters are validated for FE-contract correctness but
    # are intentionally unused.
    _ = course_template_id, iso_year, iso_week

    # Returning [] — see module docstring for rationale.
    # Once schema is extended, replace with:
    #   stmt = select(StaffCompanionAssignment).where(
    #       StaffCompanionAssignment.course_template_id == course_template_id,
    #       StaffCompanionAssignment.iso_year == iso_year,
    #       StaffCompanionAssignment.iso_week == iso_week,
    #   )
    #   rows = (await db.scalars(stmt)).all()
    #   return [StaffCompanionAssignmentV2Read.model_validate(r) for r in rows]
    _ = db
    return []


# ---------------------------------------------------------------------------
# PATCH /api/v1/staff-companion-assignments/{assignment_id}
# ---------------------------------------------------------------------------


@router.patch(
    "/staff-companion-assignments/{assignment_id}",
    response_model=StaffCompanionAssignmentV2Read,
    summary="補佐割当の pair_role を更新 (admin/manager のみ)",
)
async def update_pair_role(
    assignment_id: UUID,
    body: PairRoleUpdate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> StaffCompanionAssignmentV2Read:
    """補佐割当の ``pair_role`` (primary/support/None) を更新する."""
    row = await db.scalar(
        select(StaffCompanionAssignment).where(StaffCompanionAssignment.id == assignment_id)
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Staff companion assignment not found",
        )
    row.pair_role = body.pair_role
    await db.commit()
    await db.refresh(row)
    return StaffCompanionAssignmentV2Read.model_validate(row)
