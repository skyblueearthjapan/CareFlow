"""同行スタッフ割当エンドポイント (W10-BE1).

GET    /api/v1/staff/{staff_id}/companion-assignments
       -> list[StaffCompanionAssignmentV2Read]
       RBAC: admin/manager/staff (staff は自分のみ)

PUT    /api/v1/staff/{staff_id}/companion-assignments
       Body: StaffCompanionAssignmentsBulkPut
       -> list[StaffCompanionAssignmentV2Read]
       1 TX で当該 trainee_staff_id の既存全削除 → items を INSERT
       RBAC: admin/manager のみ
       - trainee のスタッフが is_trainee=True でないと 409
       - companion_staff_id が role IN ('manager','staff') かつ status='active'
         かつ deleted_at IS NULL でないと 422

DELETE /api/v1/staff/{staff_id}/companion-assignments
       -> 204
       全削除 (新人 flag OFF 時に呼ぶ想定)
       RBAC: admin/manager のみ

GET /api/v1/staff/companion-candidates?exclude_id={trainee_staff_id}
       -> list[StaffV2Read]
       RBAC: admin/manager のみ
       role IN ('manager','staff'), status='active', deleted_at IS NULL, id != exclude_id
"""

from __future__ import annotations

import uuid
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.models.staff import Staff
from app.models.staff_companion_assignment import StaffCompanionAssignment
from app.models.user import User
from app.schemas.staff import StaffRead
from app.schemas.v2.staff_companion_assignment import (
    StaffCompanionAssignmentsBulkPut,
    StaffCompanionAssignmentV2Read,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_staff_or_404(db: AsyncSession, staff_id: UUID) -> Staff:
    """staff を取得し存在しなければ 404."""
    staff = await db.scalar(select(Staff).where(Staff.id == staff_id, Staff.deleted_at.is_(None)))
    if staff is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")
    return staff


def _check_read_access(user: User, staff_id: UUID) -> None:
    """admin/manager は全スタッフ可; staff は自分のみ。"""
    if user.role in {"admin", "manager"}:
        return
    if user.role == "staff":
        if user.staff_id != staff_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Staff can only view their own companion assignments",
            )
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")


async def _validate_companion(db: AsyncSession, companion_staff_id: UUID) -> None:
    """companion が role in ('manager','staff') かつ active かつ未削除かを検証."""
    companion = await db.scalar(
        select(Staff).where(
            Staff.id == companion_staff_id,
            Staff.role.in_(["manager", "staff"]),
            Staff.status == "active",
            Staff.deleted_at.is_(None),
        )
    )
    if companion is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"companion_staff_id {companion_staff_id} is not a valid active "
                "manager/staff member"
            ),
        )


# ---------------------------------------------------------------------------
# Companion-candidates helper (collection-level; before /{staff_id})
# ---------------------------------------------------------------------------


@router.get(
    "/companion-candidates",
    response_model=list[StaffRead],
    summary="同行スタッフ候補一覧 (admin/manager のみ)",
)
async def list_companion_candidates(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
    exclude_id: UUID | None = Query(
        default=None,
        description="除外する staff_id (通常は trainee 自身の ID)",
    ),
) -> list[Staff]:
    """role IN ('manager','staff'), status='active', deleted_at IS NULL の一覧.

    exclude_id を指定すると該当スタッフを除外する (trainee 自身の除外用)。
    """
    stmt = select(Staff).where(
        Staff.role.in_(["manager", "staff"]),
        Staff.status == "active",
        Staff.deleted_at.is_(None),
    )
    if exclude_id is not None:
        stmt = stmt.where(Staff.id != exclude_id)
    stmt = stmt.order_by(Staff.code.asc().nulls_last(), Staff.created_at.asc())

    rows = (await db.scalars(stmt)).all()
    return list(rows)


# ---------------------------------------------------------------------------
# Sub-resource endpoints: /staff/{staff_id}/companion-assignments
# ---------------------------------------------------------------------------


@router.get(
    "/{staff_id}/companion-assignments",
    response_model=list[StaffCompanionAssignmentV2Read],
    summary="同行スタッフ割当一覧取得",
)
async def get_companion_assignments(
    staff_id: UUID,
    db: DbDep,
    user: CurrentActiveUser,
) -> list[StaffCompanionAssignmentV2Read]:
    """trainee_staff_id = staff_id の同行スタッフ割当を全取得する."""
    _check_read_access(user, staff_id)
    await _get_staff_or_404(db, staff_id)

    rows = (
        await db.scalars(
            select(StaffCompanionAssignment)
            .where(StaffCompanionAssignment.trainee_staff_id == staff_id)
            .order_by(
                StaffCompanionAssignment.weekday,
                StaffCompanionAssignment.part,
            )
        )
    ).all()

    return [StaffCompanionAssignmentV2Read.model_validate(r) for r in rows]


@router.put(
    "/{staff_id}/companion-assignments",
    response_model=list[StaffCompanionAssignmentV2Read],
    summary="同行スタッフ割当一括上書き (admin/manager のみ)",
)
async def put_companion_assignments(
    staff_id: UUID,
    body: StaffCompanionAssignmentsBulkPut,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> list[StaffCompanionAssignmentV2Read]:
    """1 TX で当該 trainee_staff_id の既存全削除 → items を INSERT.

    バリデーション:
      - trainee スタッフが is_trainee=True でないと 409
      - 同一 (weekday, part) 重複 → 422
      - companion_staff_id が trainee 自身 → 422
      - companion が active な manager/staff でないと 422
    """
    trainee = await _get_staff_or_404(db, staff_id)

    # trainee flag チェック
    if not trainee.is_trainee:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Staff {staff_id} is not marked as is_trainee=true",
        )

    # 同一 (weekday, part) 重複チェック
    seen: set[tuple[int, str]] = set()
    for item in body.items:
        key = (item.weekday, item.part)
        if key in seen:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Duplicate (weekday={item.weekday}, part={item.part!r}) in request",
            )
        seen.add(key)

    # companion 自身チェック + 有効性チェック
    for item in body.items:
        if item.companion_staff_id == staff_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="companion_staff_id must not be the same as the trainee (staff_id)",
            )
        await _validate_companion(db, item.companion_staff_id)

    # 1 TX: 既存全削除 → INSERT
    await db.execute(
        delete(StaffCompanionAssignment).where(
            StaffCompanionAssignment.trainee_staff_id == staff_id
        )
    )

    for item in body.items:
        db.add(
            StaffCompanionAssignment(
                id=uuid.uuid4(),
                trainee_staff_id=staff_id,
                weekday=item.weekday,
                part=item.part,
                companion_staff_id=item.companion_staff_id,
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
                detail="Conflict: duplicate (trainee_staff_id, weekday, part)",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Validation error",
        ) from exc

    rows = (
        await db.scalars(
            select(StaffCompanionAssignment)
            .where(StaffCompanionAssignment.trainee_staff_id == staff_id)
            .order_by(
                StaffCompanionAssignment.weekday,
                StaffCompanionAssignment.part,
            )
        )
    ).all()

    return [StaffCompanionAssignmentV2Read.model_validate(r) for r in rows]


@router.delete(
    "/{staff_id}/companion-assignments",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="同行スタッフ割当全削除 (admin/manager のみ)",
)
async def delete_companion_assignments(
    staff_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> None:
    """trainee_staff_id = staff_id の全割当を削除する (冪等)."""
    # 404 チェックはしない (冪等削除)
    await db.execute(
        delete(StaffCompanionAssignment).where(
            StaffCompanionAssignment.trainee_staff_id == staff_id
        )
    )
    await db.commit()
