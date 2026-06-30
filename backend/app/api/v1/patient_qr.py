"""患者 QR 発行 / 再発行 endpoints (Phase 5-1).

GET  /patients/{id}/qr            未発行なら遅延生成し ``{token, version}`` を返す
POST /patients/{id}/qr/regenerate 旧トークンを失効退避し新トークンを発行する

いずれも admin / manager のみ (``require_role``)。患者宅に固定掲示する QR の
識別子 (``token_urlsafe(16)``) を管理する。URL (``/q/{token}``) はフロントが
``window.location.origin`` を前置して組み立てる。

再発行は旧トークンを ``revoked_qr_tokens`` に退避してから新トークンに置き換え、
``qr_version`` を +1 する。judge は失効トークンでの打刻に 410 Gone を返す。
回転は ``audit_logs`` に明示記録する (``scheduling_settings`` の作法に倣う)。
"""

from __future__ import annotations

import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import DbDep, require_role
from app.models.audit_log import AuditLog
from app.models.patient import Patient
from app.models.revoked_qr_token import RevokedQrToken
from app.models.user import User
from app.schemas.patient_qr import PatientQrRead

router = APIRouter()

# 採番した token が既存値 (patients / revoked) と衝突したときの再試行上限。
# token_urlsafe(16) の衝突確率は天文学的に低いが、_commit_or_409 と同様に防御する。
_QR_TOKEN_RETRY_MAX = 5


async def _generate_unique_token(db: AsyncSession) -> str:
    """既存の patients.qr_token / revoked_qr_tokens と衝突しない新トークンを返す.

    DB の UNIQUE 制約が最終防壁だが、judge の引き当て (patients 優先 → revoked) が
    曖昧にならないよう、採番時点でも両表に存在しないトークンを選ぶ。
    """
    for _ in range(_QR_TOKEN_RETRY_MAX):
        token = secrets.token_urlsafe(16)
        in_patients = await db.scalar(select(Patient.id).where(Patient.qr_token == token))
        in_revoked = await db.scalar(
            select(RevokedQrToken.id).where(RevokedQrToken.token == token)
        )
        if in_patients is None and in_revoked is None:
            return token
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Conflict: failed to allocate a unique QR token",
    )


async def _get_patient_or_404(db: AsyncSession, patient_id: UUID) -> Patient:
    patient = await db.scalar(
        select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
    )
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return patient


@router.get(
    "/{patient_id}/qr",
    response_model=PatientQrRead,
    summary="Phase 5-1: 患者 QR トークン取得 (未発行なら遅延生成, admin/manager)",
)
async def get_patient_qr(
    patient_id: UUID,
    db: DbDep,
    _actor: Annotated[User, Depends(require_role("admin", "manager"))],
) -> PatientQrRead:
    """患者の QR トークンを返す。``qr_token`` が NULL なら遅延生成して保存する。"""
    patient = await _get_patient_or_404(db, patient_id)

    if patient.qr_token is None:
        # 遅延発行: UNIQUE 衝突 (並行発行) 時は採番からやり直す。
        last_exc: IntegrityError | None = None
        for _ in range(_QR_TOKEN_RETRY_MAX):
            patient.qr_token = await _generate_unique_token(db)
            try:
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                last_exc = exc
                patient = await _get_patient_or_404(db, patient_id)
                # 並行リクエストが先に発行済みなら、その値を採用して返す。
                if patient.qr_token is not None:
                    break
                continue
            await db.refresh(patient)
            break
        else:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Conflict: failed to allocate a unique QR token",
            ) from last_exc

    return PatientQrRead(token=patient.qr_token, version=patient.qr_version)


@router.post(
    "/{patient_id}/qr/regenerate",
    response_model=PatientQrRead,
    summary="Phase 5-1: 患者 QR 再発行 (旧トークン失効 + version+1, admin/manager)",
)
async def regenerate_patient_qr(
    patient_id: UUID,
    db: DbDep,
    actor: Annotated[User, Depends(require_role("admin", "manager"))],
) -> PatientQrRead:
    """旧トークンを ``revoked_qr_tokens`` に退避し、新トークンを発行する。

    * 旧 ``qr_token`` が非 NULL なら失効履歴に退避する (judge が 410 を返すため)。
    * 新トークンを採番し ``qr_version`` を +1 する。
    * 回転を ``audit_logs`` に記録する。
    """
    patient = await _get_patient_or_404(db, patient_id)

    old_token = patient.qr_token
    old_version = patient.qr_version

    new_token = await _generate_unique_token(db)
    if old_token is not None:
        db.add(RevokedQrToken(patient_id=patient.id, token=old_token))
    patient.qr_token = new_token
    patient.qr_version = old_version + 1

    db.add(
        AuditLog(
            actor_user_id=actor.id,
            action="patient_qr_regenerate",
            target_table="patients",
            target_id=str(patient.id),
            before={"qr_token": old_token, "qr_version": old_version},
            after={"qr_token": new_token, "qr_version": patient.qr_version},
        )
    )

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conflict: QR token collision during regenerate",
        ) from exc
    await db.refresh(patient)

    return PatientQrRead(token=patient.qr_token, version=patient.qr_version)
