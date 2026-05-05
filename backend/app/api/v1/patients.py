"""Patient CRUD endpoints (v2 schema, W1-BE1).

設計仕様書 v0.9 §4.1 / API 契約 v0.1 §1 に対応する v2 endpoints。

* リクエスト/レスポンスは ``app.schemas.v2.patient`` の型を使用 (re-export 経由)。
* RBAC:
    - GET (list / detail) — admin / manager / staff
    - POST / PATCH         — admin / manager
    - DELETE (soft delete) — admin only
* 旧フィールド (``age``, ``ng_time_start``, ``ng_time_end``, ``required_staff_count``,
  ``area``, ``ng_staff_ids``, ``preferred_staff_ids``, ``specified_type``,
  ``continuous_request``) は v2 schema が ``extra='ignore'`` で受理するため、
  旧クライアントからの送信は静かに無視される。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.deps import DbDep, require_role
from app.models.patient import Patient
from app.models.user import User
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


@router.get("", response_model=list[PatientRead], summary="List patients")
async def list_patients(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager", "staff"))],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Patient]:
    stmt = (
        select(Patient)
        .where(Patient.deleted_at.is_(None))
        .order_by(Patient.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.scalars(stmt)).all()
    return list(rows)


@router.get("/{patient_id}", response_model=PatientRead, summary="Get patient by id")
async def get_patient(
    patient_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager", "staff"))],
) -> Patient:
    patient = await db.scalar(
        select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
    )
    if patient is None:
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
