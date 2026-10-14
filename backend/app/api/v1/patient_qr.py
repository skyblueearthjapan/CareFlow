"""患者 QR 発行 / 再発行 endpoints (Phase 5-1).

GET  /patients/{id}/qr            未発行なら遅延生成し ``{token, version}`` を返す
GET  /patients/{id}/qr/status     「QRの管理」の表示 (版・発行日・最後に読めた日・猶予)
POST /patients/{id}/qr/regenerate 旧トークンを失効退避し新トークンを発行する

いずれも admin のみ (``require_role("admin")``)。患者宅に固定掲示する QR の
識別子 (``token_urlsafe(16)``) を管理する。URL (``/q/{token}``) はフロントが
``window.location.origin`` を前置して組み立てる。

再発行は旧トークンを ``revoked_qr_tokens`` に退避してから新トークンに置き換え、
``qr_version`` を +1 する。judge は失効トークンでの打刻に 410 Gone を返す。
回転は ``audit_logs`` に明示記録する (``scheduling_settings`` の作法に倣う)。

再発行の理由 (``docs/plans/qr-manage-reissue-guard-2026-10-14.md`` §4-2):
``damaged`` (汚れた・読めない) は旧トークンを 7 日間の猶予つきで退避し、その間は
古いシールでも打刻できる。``lost`` / ``misuse`` と本文なし (古い画面) は即時に無効。
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import DbDep, require_role
from app.models.audit_log import AuditLog
from app.models.patient import Patient
from app.models.revoked_qr_token import RevokedQrToken
from app.models.staff import Staff
from app.models.user import User
from app.models.visit_checkin import VisitCheckin
from app.schemas.patient_qr import (
    PatientQrGrace,
    PatientQrRead,
    PatientQrRegenerate,
    PatientQrStatus,
)
from app.services.checkin.actuals import as_utc
from app.services.checkin.judge import expire_grace_tokens

router = APIRouter()

# 採番した token が既存値 (patients / revoked) と衝突したときの再試行上限。
# token_urlsafe(16) の衝突確率は天文学的に低いが、_commit_or_409 と同様に防御する。
_QR_TOKEN_RETRY_MAX = 5

# 理由が「汚れた・読めない」のとき、古いシールを使える日数 (PO 2026-10-14)。
_DAMAGED_GRACE = timedelta(days=7)


async def _generate_unique_token(db: AsyncSession) -> str:
    """既存の patients.qr_token / revoked_qr_tokens と衝突しない新トークンを返す.

    DB の UNIQUE 制約が最終防壁だが、judge の引き当て (patients 優先 → revoked) が
    曖昧にならないよう、採番時点でも両表に存在しないトークンを選ぶ。
    """
    for _ in range(_QR_TOKEN_RETRY_MAX):
        token = secrets.token_urlsafe(16)
        in_patients = await db.scalar(select(Patient.id).where(Patient.qr_token == token))
        in_revoked = await db.scalar(select(RevokedQrToken.id).where(RevokedQrToken.token == token))
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
    _actor: Annotated[User, Depends(require_role("admin"))],
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
    actor: Annotated[User, Depends(require_role("admin"))],
    body: Annotated[PatientQrRegenerate | None, Body()] = None,
) -> PatientQrRead:
    """旧トークンを ``revoked_qr_tokens`` に退避し、新トークンを発行する。

    * 旧 ``qr_token`` が非 NULL なら失効履歴に退避する (judge が 410 を返すため)。
      理由が ``damaged`` なら ``valid_until = now + 7 日`` (猶予中は古いシールでも
      打刻できる)。``lost`` / ``misuse`` と本文なしは ``valid_until = NULL`` (即時無効)。
    * 新トークンを採番し ``qr_version`` を +1 する。
    * 回転を ``audit_logs`` に記録する (after に理由)。
    """
    patient = await _get_patient_or_404(db, patient_id)

    old_token = patient.qr_token
    old_version = patient.qr_version
    reason = body.reason if body is not None else None
    now = datetime.now(UTC)

    new_token = await _generate_unique_token(db)
    if old_token is not None:
        db.add(
            RevokedQrToken(
                patient_id=patient.id,
                token=old_token,
                # 版の並び (status の猶予の版) を時刻で決めるため、アプリの時計で入れる。
                revoked_at=now,
                valid_until=now + _DAMAGED_GRACE if reason == "damaged" else None,
                reason=reason,
            )
        )
    if reason != "damaged":
        # 即時無効の理由 (なくした・他人に使われた恐れ・本文なし) では、前の再発行で
        # 猶予中の古いシールも今の時刻で打ち切る (古いシールはどれも使えなくする)。
        await expire_grace_tokens(db, patient, now)
    patient.qr_token = new_token
    patient.qr_version = old_version + 1

    db.add(
        AuditLog(
            actor_user_id=actor.id,
            action="patient_qr_regenerate",
            target_table="patients",
            target_id=str(patient.id),
            before={"qr_token": old_token, "qr_version": old_version},
            after={"qr_token": new_token, "qr_version": patient.qr_version, "reason": reason},
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


@router.get(
    "/{patient_id}/qr/status",
    response_model=PatientQrStatus,
    summary="QRの管理: 今の版・発行日・最後に読めた日・猶予中の古い QR (admin)",
)
async def get_patient_qr_status(
    patient_id: UUID,
    db: DbDep,
    _actor: Annotated[User, Depends(require_role("admin"))],
) -> PatientQrStatus:
    """「QRの管理」ダイアログの表示に使う値を返す。

    * ``issued_at`` = 直近の ``revoked_qr_tokens.revoked_at`` (無ければ null = 初回発行)。
    * 最後に読めた = ``visit_checkins`` の ``checkin_source='qr'`` の最新 1 件と職員名。
    * ``grace`` = ``valid_until > now`` の古い QR。版は再発行の新しい順に
      「今の版 - 1, - 2, …」(再発行は必ず旧トークンを退避して版を +1 するため)。
    """
    patient = await _get_patient_or_404(db, patient_id)
    now = datetime.now(UTC)

    revoked_rows = (
        await db.scalars(
            select(RevokedQrToken)
            .where(RevokedQrToken.patient_id == patient.id)
            .order_by(RevokedQrToken.revoked_at.desc())
        )
    ).all()
    grace = [
        PatientQrGrace(version=patient.qr_version - 1 - index, valid_until=row.valid_until)
        for index, row in enumerate(revoked_rows)
        if row.valid_until is not None and as_utc(row.valid_until) > now
    ]

    last_scan = (
        await db.execute(
            select(VisitCheckin.scanned_at, Staff.name)
            .outerjoin(Staff, Staff.id == VisitCheckin.staff_id)
            .where(
                VisitCheckin.patient_id == patient.id,
                VisitCheckin.checkin_source == "qr",
            )
            .order_by(VisitCheckin.scanned_at.desc())
            .limit(1)
        )
    ).first()

    return PatientQrStatus(
        version=patient.qr_version,
        issued_at=revoked_rows[0].revoked_at if revoked_rows else None,
        last_scanned_at=last_scan[0] if last_scan is not None else None,
        last_scanned_staff_name=last_scan[1] if last_scan is not None else None,
        grace=grace,
    )
