"""NG スタッフ (患者×スタッフ割当禁止) CRUD endpoints.

正典設計書: ``docs/plans/patient-ng-staff-design.md`` §4-1.
実装の手本は ``patient_same_address_links.py`` (同じ「行が存在する = 制約あり」方式).

GET    /patients/{patient_id}/ng-staff              → 一覧
PUT    /patients/{patient_id}/ng-staff/{staff_id}   → upsert {note}
DELETE /patients/{patient_id}/ng-staff/{staff_id}   → 削除
GET    /staff/{staff_id}/ng-patients                → 逆引き一覧 (``staff_router``)

RBAC:
  GET             — admin / staff (閲覧は全ロール開放.
                    RBAC UI 統一「全ロール同一表示・操作は権限どおり」の原則。
                    先例 = monitor / nearby / checkin-settings)
  PUT / DELETE    — admin のみ

ルータ構成
----------
本モジュールは prefix の異なる 2 本のルータを公開する:

* ``router``       — ``/patients`` 配下 (CRUD 本体)
* ``staff_router`` — ``/staff`` 配下 (逆引き 1 本のみ)

逆引きは同じ ``patient_ng_staff`` テーブルが正典なので実装をここに同居させ、
登録側 (``api/v1/__init__.py``) で prefix を分けている (先例 =
``shift_requests.status_router``). ``staff.py`` へは置かない — NG の知識を
スタッフ CRUD に染み出させないため.

設計メモ
--------
* ``staff_name`` は正典 (patient_ng_staff) に持たず、staff テーブルを JOIN して
  都度解決する (新人同行の教訓: 表示名を別テーブルへ二重化しない).
* 退職済み (``staff.deleted_at IS NOT NULL``) スタッフの **新規指定は 422**。
  既存行は残し ``staff_is_deleted=true`` で返す (= 表示側で「(退職)」注記).
* 患者/スタッフの soft delete では FK CASCADE が発火しないため、NG 行の掃除は
  ``patients.delete_patient`` / ``staff.delete_staff`` 側で明示 DELETE している。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.core.deps import DbDep, require_role
from app.models.patient import Patient
from app.models.patient_ng_staff import PatientNgStaff
from app.models.staff import Staff
from app.models.user import User
from app.schemas.patient_ng_staff import (
    PatientNgStaffRead,
    PatientNgStaffUpsert,
    StaffNgPatientRead,
)

router = APIRouter()
# 逆引き専用ルータ. ``/staff`` prefix で登録する (module docstring「ルータ構成」参照).
staff_router = APIRouter()


async def _require_active_patient(db, patient_id: UUID) -> Patient:
    """未削除の患者を取得. 無ければ 404."""
    patient = await db.scalar(
        select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
    )
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")
    return patient


@router.get(
    "/{patient_id}/ng-staff",
    response_model=list[PatientNgStaffRead],
    summary="NG スタッフ一覧 (閲覧は全ロール)",
)
async def list_patient_ng_staff(
    patient_id: UUID,
    db: DbDep,
    _actor: Annotated[User, Depends(require_role("admin", "staff"))],
) -> list[PatientNgStaffRead]:
    """当該患者の NG スタッフを名前解決付きで返す.

    退職済みスタッフの行も返し ``staff_is_deleted=true`` を立てる
    (= 行は消さず、表示側で「(退職)」注記する運用).
    """
    await _require_active_patient(db, patient_id)

    rows = (
        await db.execute(
            select(PatientNgStaff, Staff.name, Staff.deleted_at)
            .join(Staff, Staff.id == PatientNgStaff.staff_id)
            .where(PatientNgStaff.patient_id == patient_id)
            .order_by(Staff.name, PatientNgStaff.staff_id)
        )
    ).all()

    return [
        PatientNgStaffRead(
            staff_id=ng.staff_id,
            staff_name=staff_name,
            staff_is_deleted=staff_deleted_at is not None,
            note=ng.note,
            decided_by_user_id=ng.decided_by_user_id,
            created_at=ng.created_at,
        )
        for ng, staff_name, staff_deleted_at in rows
    ]


@router.put(
    "/{patient_id}/ng-staff/{staff_id}",
    response_model=PatientNgStaffRead,
    summary="NG スタッフ upsert (admin only)",
)
async def upsert_patient_ng_staff(
    patient_id: UUID,
    staff_id: UUID,
    payload: PatientNgStaffUpsert,
    db: DbDep,
    actor: Annotated[User, Depends(require_role("admin"))],
) -> PatientNgStaffRead:
    """NG スタッフを登録 / 理由メモを更新する (冪等).

    * 患者・スタッフが存在しなければ 404.
    * 退職済みスタッフの **新規** 指定は 422 (既存行の note 更新は許可).
    * ``decided_by_user_id`` は毎回操作ユーザーで上書きする (= 最終設定者を保持).
    """
    await _require_active_patient(db, patient_id)

    # 退職者も対象にするため deleted_at で絞らない (存在判定のみ).
    staff = await db.scalar(select(Staff).where(Staff.id == staff_id))
    if staff is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")

    existing = await db.scalar(
        select(PatientNgStaff).where(
            PatientNgStaff.patient_id == patient_id,
            PatientNgStaff.staff_id == staff_id,
        )
    )

    if existing is None and staff.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="削除済みスタッフを NG に新規指定することはできません",
        )

    if existing is None:
        existing = PatientNgStaff(
            patient_id=patient_id,
            staff_id=staff_id,
            note=payload.note,
            decided_by_user_id=actor.id,
        )
        db.add(existing)
    else:
        existing.note = payload.note
        existing.decided_by_user_id = actor.id

    await db.commit()
    await db.refresh(existing)

    return PatientNgStaffRead(
        staff_id=existing.staff_id,
        staff_name=staff.name,
        staff_is_deleted=staff.deleted_at is not None,
        note=existing.note,
        decided_by_user_id=existing.decided_by_user_id,
        created_at=existing.created_at,
    )


@router.delete(
    "/{patient_id}/ng-staff/{staff_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="NG スタッフ解除 (admin only)",
)
async def delete_patient_ng_staff(
    patient_id: UUID,
    staff_id: UUID,
    db: DbDep,
    _actor: Annotated[User, Depends(require_role("admin"))],
) -> None:
    """NG 指定を解除する. 行が無ければ 404."""
    await _require_active_patient(db, patient_id)

    existing = await db.scalar(
        select(PatientNgStaff).where(
            PatientNgStaff.patient_id == patient_id,
            PatientNgStaff.staff_id == staff_id,
        )
    )
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    await db.delete(existing)
    await db.commit()
    return None


# ---------------------------------------------------------------------------
# 逆引き: このスタッフを NG 指定している患者 (設計書 §8-2 Phase 2)
# ---------------------------------------------------------------------------


@staff_router.get(
    "/{staff_id}/ng-patients",
    response_model=list[StaffNgPatientRead],
    summary="このスタッフを NG 指定している患者一覧 (閲覧は全ロール)",
)
async def list_staff_ng_patients(
    staff_id: UUID,
    db: DbDep,
    _actor: Annotated[User, Depends(require_role("admin", "staff"))],
) -> list[StaffNgPatientRead]:
    """スタッフ詳細の閲覧サマリ用の逆引き.

    * 削除済み患者 (``patients.deleted_at IS NOT NULL``) は除外する.
    * 患者氏名は JOIN で都度解決する (1 クエリ・N+1 なし).
    * 未登録 / 削除済みスタッフは 404 (``GET /staff/{id}`` と同じ判定).
    """
    staff = await db.scalar(select(Staff).where(Staff.id == staff_id, Staff.deleted_at.is_(None)))
    if staff is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")

    rows = (
        await db.execute(
            select(PatientNgStaff, Patient.name)
            .join(Patient, Patient.id == PatientNgStaff.patient_id)
            .where(
                PatientNgStaff.staff_id == staff_id,
                Patient.deleted_at.is_(None),
            )
            .order_by(Patient.name, PatientNgStaff.patient_id)
        )
    ).all()

    return [
        StaffNgPatientRead(
            patient_id=ng.patient_id,
            patient_name=patient_name,
            note=ng.note,
            created_at=ng.created_at,
        )
        for ng, patient_name in rows
    ]
