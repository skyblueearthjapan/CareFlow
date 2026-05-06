"""Patient fixed-visit pattern endpoints (W9-BE1).

GET    /patients/{patient_id}/fixed-visits[?mode=normal|special]
PUT    /patients/{patient_id}/fixed-visits   body: PatientFixedVisitsBulkPut
DELETE /patients/{patient_id}/fixed-visits?mode=normal|special

RBAC:
  GET    — admin / manager / staff (staff は担当患者のみ)
  PUT    — admin / manager のみ
  DELETE — admin / manager のみ

UNIQUE (patient_id, mode, weekday) 違反は 409 Conflict。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.user import User
from app.models.visit import Visit
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.schemas.v2.patient_fixed_visit import (
    PatientFixedVisitMode,
    PatientFixedVisitsBulkPut,
    PatientFixedVisitV2Read,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _ensure_patient_exists(db: AsyncSession, patient_id: UUID) -> Patient:
    patient = await db.scalar(
        select(Patient).where(
            Patient.id == patient_id,
            Patient.deleted_at.is_(None),
        )
    )
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")
    return patient


async def _staff_owns_patient(db: AsyncSession, *, staff_id: UUID, patient_id: UUID) -> bool:
    """staff_id が patient_id の担当者であるかを判定する (W7-BE1 パターン流用).

    判定条件 (どちらか一方でも該当すれば True):
      1. visits.primary_staff_id = staff_id の visit が当該患者に存在
      2. visit_staff_assignments 経由で staff_id が当該患者の visit に
         アサインされている

    soft-delete された visit (deleted_at IS NOT NULL) は除外する。
    """
    stmt_primary = (
        select(Visit.id)
        .where(
            Visit.patient_id == patient_id,
            Visit.primary_staff_id == staff_id,
            Visit.deleted_at.is_(None),
        )
        .limit(1)
    )
    if await db.scalar(stmt_primary) is not None:
        return True

    stmt_assign = (
        select(VisitStaffAssignment.staff_id)
        .join(Visit, Visit.id == VisitStaffAssignment.visit_id)
        .where(
            Visit.patient_id == patient_id,
            VisitStaffAssignment.staff_id == staff_id,
            Visit.deleted_at.is_(None),
        )
        .limit(1)
    )
    return await db.scalar(stmt_assign) is not None


async def _check_read_access(db: AsyncSession, user: User, patient_id: UUID) -> None:
    """admin/manager は全患者可; staff は担当患者のみ可 (範囲外は 403)."""
    if user.role in {"admin", "manager"}:
        return
    if user.role == "staff":
        if user.staff_id is None or not await _staff_owns_patient(
            db, staff_id=user.staff_id, patient_id=patient_id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Staff is not assigned to this patient",
            )
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")


async def _commit_or_409(db: AsyncSession) -> None:
    """Commit し、UNIQUE 違反を 409 に変換する."""
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        msg = str(exc).lower()
        if "unique" in msg or "duplicate" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Conflict: duplicate (patient_id, mode, weekday)",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Validation error",
        ) from exc


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/{patient_id}/fixed-visits",
    response_model=list[PatientFixedVisitV2Read],
    summary="患者の固定訪問パターン一覧取得",
)
async def get_fixed_visits(
    patient_id: UUID,
    db: DbDep,
    user: CurrentActiveUser,
    mode: PatientFixedVisitMode | None = Query(
        default=None, description="フィルタ: normal / special。未指定は両方。"
    ),
) -> list[PatientFixedVisitV2Read]:
    await _ensure_patient_exists(db, patient_id)
    await _check_read_access(db, user, patient_id)

    stmt = (
        select(PatientFixedVisit)
        .where(PatientFixedVisit.patient_id == patient_id)
        .order_by(PatientFixedVisit.mode, PatientFixedVisit.weekday)
    )
    if mode is not None:
        stmt = stmt.where(PatientFixedVisit.mode == mode)

    rows = (await db.scalars(stmt)).all()
    return [PatientFixedVisitV2Read.model_validate(r) for r in rows]


@router.put(
    "/{patient_id}/fixed-visits",
    response_model=list[PatientFixedVisitV2Read],
    summary="患者の固定訪問パターン一括上書き (admin/manager のみ)",
)
async def put_fixed_visits(
    patient_id: UUID,
    body: PatientFixedVisitsBulkPut,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> list[PatientFixedVisitV2Read]:
    await _ensure_patient_exists(db, patient_id)

    # 1 TX: 当該 (patient_id, mode) を DELETE → INSERT
    await db.execute(
        delete(PatientFixedVisit).where(
            PatientFixedVisit.patient_id == patient_id,
            PatientFixedVisit.mode == body.mode,
        )
    )
    for item in body.items:
        db.add(
            PatientFixedVisit(
                patient_id=patient_id,
                mode=body.mode,
                weekday=item.weekday,
                start_time=item.start_time,
                duration_min=item.duration_min,
            )
        )
    await _commit_or_409(db)

    rows = (
        await db.scalars(
            select(PatientFixedVisit)
            .where(
                PatientFixedVisit.patient_id == patient_id,
                PatientFixedVisit.mode == body.mode,
            )
            .order_by(PatientFixedVisit.weekday)
        )
    ).all()
    return [PatientFixedVisitV2Read.model_validate(r) for r in rows]


@router.delete(
    "/{patient_id}/fixed-visits",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="患者の固定訪問パターン削除 (mode 指定必須; admin/manager のみ)",
)
async def delete_fixed_visits(
    patient_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
    mode: PatientFixedVisitMode = Query(..., description="削除する mode: normal または special"),
) -> None:
    await _ensure_patient_exists(db, patient_id)

    await db.execute(
        delete(PatientFixedVisit).where(
            PatientFixedVisit.patient_id == patient_id,
            PatientFixedVisit.mode == mode,
        )
    )
    await db.commit()
