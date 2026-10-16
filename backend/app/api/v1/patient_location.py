"""登録の位置のずれと、打刻の場所に合わせる endpoints (2026-10-17).

GET  /patients/{id}/location-drift   ずれ (無ければ null)
POST /patients/{id}/location/align   サーバーで計算し直して打刻の場所に合わせる
POST /patients/{id}/location/revert  直近の align の前の位置に戻す

いずれも admin のみ (``require_role("admin")``)。設計
``docs/plans/location-drift-align-2026-10-17.md`` §4。

* align は画面から座標を受け取らない (サーバーで計算し直す)。ずれが無くなっていたら 409。
* 住所の ``geocoding_cache`` (``address_hash(address)``) があれば同じ値にする
  (住所を保存し直したときに古い位置が戻らないように)。
* 合わせた / 戻した記録は ``audit_logs`` に before / after で残す
  (``patient_qr`` の作法に倣う)。revert はこの記録の before を使う。
* revert は「合わせた後に誰も触っていない」ときだけ戻す。住所か患者の位置が align の
  ときと違えば 409。cache は align のときの ``address_hash`` の行を、align が書いた値のまま
  なら戻し、変わっていれば触らない (住所を変えた後に別の住所の cache を壊さないように)。
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import DbDep, require_role
from app.models.audit_log import AuditLog
from app.models.geocoding_cache import GeocodingCache
from app.models.patient import Patient
from app.models.user import User
from app.schemas.patient_location import LocationDrift, PatientLocationRead
from app.services.checkin.location_drift import find_location_drift
from app.services.geocoding.hash import address_hash

router = APIRouter()

ACTION_ALIGN = "patient_location_align"
ACTION_REVERT = "patient_location_revert"


async def _get_patient_or_404(
    db: AsyncSession, patient_id: UUID, *, for_update: bool = False
) -> Patient:
    stmt = select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
    if for_update:
        # 合わせる / 戻すが同時に走っても、before / after が食い違わないように行を押さえる。
        stmt = stmt.with_for_update()
    patient = await db.scalar(stmt)
    if patient is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="利用者様が見つかりません"
        )
    return patient


async def _get_cache_by_hash(db: AsyncSession, hash_: str | None) -> GeocodingCache | None:
    """``address_hash`` の geocoding_cache 行 (hash が無い・未登録なら None)。"""
    if not hash_:
        return None
    return await db.scalar(select(GeocodingCache).where(GeocodingCache.address_hash == hash_))


def _point(lat, lng) -> dict[str, float | None]:
    return {
        "lat": float(lat) if lat is not None else None,
        "lng": float(lng) if lng is not None else None,
    }


def _same_point(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    """2 点が同じか (小数 7 桁で丸めて比べる・DB の Numeric(10,7) に合わせる)。"""
    if a is None or b is None:
        return a is None and b is None

    def _r(v: Any) -> float | None:
        return round(float(v), 7) if v is not None else None

    return _r(a.get("lat")) == _r(b.get("lat")) and _r(a.get("lng")) == _r(b.get("lng"))


def _location_read(patient: Patient) -> PatientLocationRead:
    return PatientLocationRead(**_point(patient.lat, patient.lng))


@router.get(
    "/{patient_id}/location-drift",
    response_model=LocationDrift | None,
    summary="登録の位置のずれ (無ければ null, admin)",
)
async def get_location_drift(
    patient_id: UUID,
    db: DbDep,
    _actor: Annotated[User, Depends(require_role("admin"))],
) -> LocationDrift | None:
    """登録座標と、職員が実際に QR を読んだ場所のまとまりとのずれを返す。"""
    patient = await _get_patient_or_404(db, patient_id)
    return await find_location_drift(db, patient)


@router.post(
    "/{patient_id}/location/align",
    response_model=PatientLocationRead,
    summary="登録の位置を打刻の場所に合わせる (admin)",
)
async def align_patient_location(
    patient_id: UUID,
    db: DbDep,
    actor: Annotated[User, Depends(require_role("admin"))],
) -> PatientLocationRead:
    """サーバーでずれを計算し直し、提案の位置に合わせる (cache も)。ずれが無ければ 409。

    revert が同じ cache 行を引けるよう、使った ``address_hash`` を記録に残す。
    """
    patient = await _get_patient_or_404(db, patient_id, for_update=True)
    drift = await find_location_drift(db, patient)
    if drift is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="登録の位置のずれは見つかりませんでした",
        )

    hash_ = address_hash(patient.address) if patient.address else None
    cache = await _get_cache_by_hash(db, hash_)
    suggested = _point(drift.suggested_lat, drift.suggested_lng)
    before = {
        **_point(patient.lat, patient.lng),
        "address_hash": hash_,
        "cache": _point(cache.lat, cache.lng) if cache is not None else None,
    }
    patient.lat = drift.suggested_lat
    patient.lng = drift.suggested_lng
    if cache is not None:
        cache.lat = drift.suggested_lat
        cache.lng = drift.suggested_lng

    db.add(
        AuditLog(
            actor_user_id=actor.id,
            action=ACTION_ALIGN,
            target_table="patients",
            target_id=str(patient.id),
            before=before,
            after={
                **suggested,
                "address_hash": hash_,
                "cache": dict(suggested) if cache is not None else None,
                "distance_m": drift.distance_m,
                "scans": drift.scans,
                "days": drift.days,
            },
        )
    )
    await db.commit()
    await db.refresh(patient)
    return _location_read(patient)


@router.post(
    "/{patient_id}/location/revert",
    response_model=PatientLocationRead,
    summary="直近に合わせた登録の位置を元に戻す (admin)",
)
async def revert_patient_location(
    patient_id: UUID,
    db: DbDep,
    actor: Annotated[User, Depends(require_role("admin"))],
) -> PatientLocationRead:
    """直近の align の before に戻す (cache も)。戻した記録も残す。

    * 直近の記録が無い = 404「元に戻せる記録がありません」、直近が revert = 404
      「続けて元に戻すことはできません」。
    * 住所が align のときと違う、または患者の位置が align の after と違う = 409。
    * cache は align のときの ``address_hash`` の行が align の書いた値のままなら戻す。
      変わっていれば触らず、記録に ``"cache": "unchanged"`` を残す。
    """
    patient = await _get_patient_or_404(db, patient_id, for_update=True)
    last = await db.scalar(
        select(AuditLog)
        .where(
            AuditLog.target_table == "patients",
            AuditLog.target_id == str(patient.id),
            AuditLog.action.in_([ACTION_ALIGN, ACTION_REVERT]),
        )
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(1)
    )
    if last is not None and last.action == ACTION_REVERT:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="続けて元に戻すことはできません",
        )
    if last is None or last.before is None or last.after is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="元に戻せる記録がありません",
        )
    current_hash = address_hash(patient.address) if patient.address else None
    if current_hash != last.after.get("address_hash") or not _same_point(
        _point(patient.lat, patient.lng), last.after
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="合わせた後に位置が変わっているため、元に戻せません",
        )

    restored_cache: dict[str, Any] | str | None = None
    before_cache = last.before.get("cache")
    after_cache = last.after.get("cache")
    if before_cache is not None and after_cache is not None:
        cache = await _get_cache_by_hash(db, last.after.get("address_hash"))
        if cache is not None and _same_point(_point(cache.lat, cache.lng), after_cache):
            cache.lat = before_cache["lat"]
            cache.lng = before_cache["lng"]
            restored_cache = before_cache
        else:
            restored_cache = "unchanged"

    before = _point(patient.lat, patient.lng)
    patient.lat = last.before.get("lat")
    patient.lng = last.before.get("lng")

    db.add(
        AuditLog(
            actor_user_id=actor.id,
            action=ACTION_REVERT,
            target_table="patients",
            target_id=str(patient.id),
            before=before,
            after={
                **_point(patient.lat, patient.lng),
                "address_hash": last.after.get("address_hash"),
                "cache": restored_cache,
                "reverted_audit_id": last.id,
            },
        )
    )
    await db.commit()
    await db.refresh(patient)
    return _location_read(patient)
