"""Patient CRUD endpoints (v2 schema, W1-BE1; W7-BE1 RBAC hardening).

設計仕様書 v0.9 §4.1 / API 契約 v0.1 §1 に対応する v2 endpoints。

* リクエスト/レスポンスは ``app.schemas.v2.patient`` の型を使用 (re-export 経由)。
* RBAC (W7-BE1 で強化, Codex Must-fix #1):
    - GET (list)           — admin / manager は全件、staff は自分の担当患者のみ
    - GET (detail)         — admin / manager は任意、staff は担当患者のみ (範囲外は 404)
    - POST / PATCH         — admin / manager
    - DELETE (soft delete) — admin only

  Staff の「担当患者」は以下のいずれかを満たす患者:
    1. ``visits.primary_staff_id`` / ``secondary_staff_id`` / ``mentor_staff_id``
       が当該 staff の (v1 互換)
    2. ``visit_staff_assignments`` テーブルに当該 staff の行がある visit
       (v2 正規; W2-BE4 で導入)
  ``visits.deleted_at IS NULL`` のものに限定する。
* 旧フィールド (``age``, ``ng_time_start``, ``ng_time_end``, ``required_staff_count``,
  ``area``, ``ng_staff_ids``, ``preferred_staff_ids``, ``specified_type``,
  ``continuous_request``) は v2 schema が ``extra='ignore'`` で受理するため、
  旧クライアントからの送信は静かに無視される。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.models.patient import Patient
from app.models.user import User
from app.models.visit import Visit
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.schemas.patient import PatientCreate, PatientRead, PatientUpdate

router = APIRouter()


async def _commit_or_409(db) -> None:
    """Commit and translate IntegrityError into a stable HTTP response.

    `unique`/`duplicate` -> 409 Conflict; other FK/check errors -> 422.
    """
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


def _model_dump_for_orm(payload: PatientCreate | PatientUpdate, *, partial: bool) -> dict:
    """Serialise a v2 schema into a dict suitable for SQLAlchemy assignment.

    * Pydantic validators may emit ``WeeklyPatternV2`` instances; the ORM
      JSONB columns expect ``dict``. ``model_dump(mode='json')`` ensures
      everything goes through Pydantic JSON serialisation, which yields
      a plain dict tree we can hand off to JSONB.
    * ``primary_office_id`` (UUID) is preserved as a UUID object — JSON
      mode would coerce it to string, which the ORM column would then
      reject. We revert just that field manually.
    """
    data = payload.model_dump(
        mode="json",
        exclude_unset=partial,
        exclude_none=False,
    )
    # UUID columns: model_dump(mode='json') stringifies. Map back to UUID
    # so SQLAlchemy receives the right type.
    if "primary_office_id" in data and data["primary_office_id"] is not None:
        try:
            data["primary_office_id"] = UUID(str(data["primary_office_id"]))
        except (ValueError, TypeError):
            data["primary_office_id"] = None
    # ``special_week_active`` is a list[dict] (already JSON-mode-friendly).
    return data


def _staff_patient_ids_subquery(staff_id: UUID):
    """Return a scalar subquery yielding patient_ids visible to the given staff.

    A patient is visible to staff if they have any (non-deleted) visit where the
    staff is primary/secondary/mentor (v1 互換) **or** the staff has a row in
    ``visit_staff_assignments`` for that visit (v2 正規; §4.5).
    """
    assigned_visit_ids = select(VisitStaffAssignment.visit_id).where(
        VisitStaffAssignment.staff_id == staff_id
    )
    return (
        select(Visit.patient_id)
        .where(
            Visit.deleted_at.is_(None),
            or_(
                Visit.primary_staff_id == staff_id,
                Visit.secondary_staff_id == staff_id,
                Visit.mentor_staff_id == staff_id,
                Visit.id.in_(assigned_visit_ids),
            ),
        )
        .distinct()
    )


@router.get("", response_model=list[PatientRead], summary="List patients")
async def list_patients(
    db: DbDep,
    user: CurrentActiveUser,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Patient]:
    if user.role not in {"admin", "manager", "staff"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")

    # 登録ナンバー (code) 昇順で常に固定表示。code 未設定は末尾、同コードは登録順で安定化。
    stmt = (
        select(Patient)
        .where(Patient.deleted_at.is_(None))
        .order_by(Patient.code.asc().nulls_last(), Patient.created_at.asc())
    )
    if user.role == "staff":
        # Staff sees only patients they are assigned to via visits.
        # If the staff has no linked staff_id, return an empty page (defensive).
        if user.staff_id is None:
            return []
        stmt = stmt.where(Patient.id.in_(_staff_patient_ids_subquery(user.staff_id)))
    stmt = stmt.limit(limit).offset(offset)
    rows = (await db.scalars(stmt)).all()
    return list(rows)


@router.get("/{patient_id}", response_model=PatientRead, summary="Get patient by id")
async def get_patient(
    patient_id: UUID,
    db: DbDep,
    user: CurrentActiveUser,
) -> Patient:
    if user.role not in {"admin", "manager", "staff"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")

    patient = await db.scalar(
        select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
    )
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if user.role == "staff":
        # Staff: only own patients. Return 404 (not 403) to avoid leaking
        # the existence of patients outside their scope.
        if user.staff_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        in_scope = await db.scalar(
            select(Patient.id)
            .where(
                Patient.id == patient_id,
                Patient.id.in_(_staff_patient_ids_subquery(user.staff_id)),
            )
            .limit(1)
        )
        if in_scope is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return patient


@router.post(
    "",
    response_model=PatientRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create patient",
)
async def create_patient(
    payload: PatientCreate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> Patient:
    data = _model_dump_for_orm(payload, partial=False)
    patient = Patient(**data)
    db.add(patient)
    await _commit_or_409(db)
    await db.refresh(patient)
    return patient


@router.patch("/{patient_id}", response_model=PatientRead, summary="Update patient")
async def update_patient(
    patient_id: UUID,
    payload: PatientUpdate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> Patient:
    patient = await db.scalar(
        select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
    )
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    data = _model_dump_for_orm(payload, partial=True)
    for field, value in data.items():
        setattr(patient, field, value)
    await _commit_or_409(db)
    await db.refresh(patient)
    return patient


@router.delete(
    "/{patient_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete patient (admin only)",
)
async def delete_patient(
    patient_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> None:
    patient = await db.scalar(
        select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
    )
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    patient.deleted_at = func.now()
    await db.commit()
    return None
