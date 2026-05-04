"""Patient CRUD endpoints (Phase 2 domain router)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select

from app.core.deps import DbDep, require_role
from app.models.patient import Patient
from app.models.user import User
from app.schemas.patient import PatientCreate, PatientRead, PatientUpdate

router = APIRouter()


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
        select(Patient).where(
            Patient.id == patient_id, Patient.deleted_at.is_(None)
        )
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
    patient = Patient(**payload.model_dump())
    db.add(patient)
    await db.commit()
    await db.refresh(patient)
    return patient


@router.patch(
    "/{patient_id}", response_model=PatientRead, summary="Update patient"
)
async def update_patient(
    patient_id: UUID,
    payload: PatientUpdate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "manager"))],
) -> Patient:
    patient = await db.scalar(
        select(Patient).where(
            Patient.id == patient_id, Patient.deleted_at.is_(None)
        )
    )
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(patient, field, value)
    await db.commit()
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
        select(Patient).where(
            Patient.id == patient_id, Patient.deleted_at.is_(None)
        )
    )
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    patient.deleted_at = func.now()
    await db.commit()
    return None
