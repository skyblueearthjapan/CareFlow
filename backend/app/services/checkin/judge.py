"""チェックイン判定 (サーバ責務) — 設計 §2.

クライアントの lat/lng を信用せず、患者登録座標との距離をサーバが Haversine で
計算し、その時点のしきい値で ``match_status`` を確定・保存する (使用しきい値も
``threshold_snapshot`` にスナップショット = 遡及変更しない)。

判定は **位置のみ** (match / review / mismatch / no_gps)。遅延・未訪問の時間
判定は Phase 3 モニターが集計時に合成する (過去 checkin の位置判定は不変)。

「記録は止めない」方針: 不一致でも HTTPException にせず行を残す。エラーで弾く
のは ① 別患者 QR (409) ② 無効 QR (404) ③ visit ガード違反 (当日外 / 削除 /
取消 = 409) のみ。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.checkin_settings import CheckinSettings
from app.models.patient import Patient
from app.models.revoked_qr_token import RevokedQrToken
from app.models.visit import VISIT_STATUS_CANCELLED, Visit
from app.models.visit_checkin import VisitCheckin
from app.schemas.visit_checkin import CheckinCreate
from app.utils.geo import haversine_m

# 「当日」判定は JST (Asia/Tokyo) で行う。scanned_at は timestamptz (UTC) なので
# JST に変換してから naive な visit_date と比較する (設計 R3)。
JST = ZoneInfo("Asia/Tokyo")

# checkin_settings 行が無い / 列が NULL のときの既定しきい値 (設計 §1-C)。
DEFAULT_THRESHOLDS: dict[str, int] = {
    "match_m": 100,
    "review_m": 300,
    "accuracy_m": 50,
    "no_show_grace_min": 20,
    "late_min": 15,
    # 退出忘れ (長時間 inprogress) しきい値 (分)。Phase 3 までは monitor の定数
    # MAX_INPROGRESS_MIN にハードコードされていたが Phase 4 で設定化した。
    "max_inprogress_min": 240,
}


async def load_thresholds(db: AsyncSession) -> dict[str, int]:
    """checkin_settings シングルトンを読み、NULL 列は既定にフォールバックする."""
    thresholds = dict(DEFAULT_THRESHOLDS)
    row = await db.scalar(select(CheckinSettings).where(CheckinSettings.is_singleton.is_(True)))
    if row is not None:
        for key in thresholds:
            value = getattr(row, key, None)
            if value is not None:
                thresholds[key] = int(value)
    return thresholds


def compute_distance_m(patient: Patient, lat: float | None, lng: float | None) -> float | None:
    """端末 GPS と患者登録座標の距離 (m). どちらか欠ければ None."""
    if lat is None or lng is None or patient.lat is None or patient.lng is None:
        return None
    return round(haversine_m(float(patient.lat), float(patient.lng), float(lat), float(lng)), 1)


def compute_match_status(
    distance_m: float | None,
    accuracy_m: float | None,
    thresholds: dict[str, int],
) -> str:
    """位置のみの match_status を確定する (設計 §2-5 の全分岐)."""
    match_m = thresholds["match_m"]
    review_m = thresholds["review_m"]
    accuracy_tol = thresholds["accuracy_m"]

    # 設計判断: accuracy_m が None (端末が精度を報告しない) は「許容内」として
    # 扱う。すなわち下流の精度ガード (`accuracy_m is not None and ...`) は素通り
    # し、距離だけで match/review/mismatch を確定する。未報告を no_gps/review に
    # 落とすと、精度を出さない正常端末まで一律で要確認になり実用に耐えないため。
    # 精度を悪用したバイパス (負値等) は schema 側 (accuracy>=0) で塞ぐ。

    # GPS 無し or 患者座標無し → 距離不明。
    if distance_m is None:
        return "no_gps"
    # 精度が要確認しきい値より粗い = 測位不能。距離で断定しない。
    if accuracy_m is not None and accuracy_m > review_m:
        return "no_gps"
    if distance_m <= match_m:
        # 近距離でも精度が許容を超えていれば測位不良として要確認。
        if accuracy_m is not None and accuracy_m > accuracy_tol:
            return "review"
        return "match"
    if distance_m <= review_m:
        return "review"
    # 精度が粗い場合は前段の no_gps で吸収済み = ここは明確な遠隔。
    return "mismatch"


async def _lookup_active_patient_by_token(db: AsyncSession, qr_token: str) -> Patient | None:
    """QR トークンに一致する active・未削除の患者を引く (無ければ None)."""
    return await db.scalar(
        select(Patient).where(
            Patient.qr_token == qr_token,
            Patient.deleted_at.is_(None),
            Patient.status == "active",
        )
    )


async def _lookup_revoked_token(db: AsyncSession, qr_token: str) -> RevokedQrToken | None:
    """再発行で失効した旧トークンの履歴行を引く (無ければ None)."""
    return await db.scalar(select(RevokedQrToken).where(RevokedQrToken.token == qr_token))


async def resolve_qr_patient(db: AsyncSession, qr_token: str) -> Patient:
    """QR トークン**単独** (visit 文脈なし) から患者を解決する。

    汎用カメラのディープリンク (`GET /visits/resolve-qr/{token}`) 用。
    `_resolve_patient` と同じルールだが、visit が無いため失効 QR (410) の案内は
    患者名を出さない汎用文に固定する (情報露出の最小化)。
    未知トークンは 404 (checkin API と同水準の存在情報として許容)。
    """
    patient = await _lookup_active_patient_by_token(db, qr_token)
    if patient is not None:
        return patient
    if await _lookup_revoked_token(db, qr_token) is not None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="このQRは更新されています。正しいQRをご利用ください",
        )
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="QR token not found",
    )


async def _resolve_patient(
    db: AsyncSession, visit: Visit, qr_token: str | None
) -> tuple[Patient, str]:
    """QR トークンから患者を解決する。無し = manual で visit.patient を採用 (R1)."""
    if not qr_token:
        return visit.patient, "manual"
    patient = await _lookup_active_patient_by_token(db, qr_token)
    if patient is None:
        # 未知トークン: 再発行で失効した旧 QR (= ローテ済) なら 410 Gone を返し、
        # 旧ステッカーで打刻したスタッフに「QR が更新された」と気づかせる。
        # 失効履歴にも無い完全な未知トークンは従来どおり 404。
        revoked = await _lookup_revoked_token(db, qr_token)
        if revoked is not None:
            # 氏名スコープ: 旧トークンが「この visit の患者」のものなら氏名入りで
            # 案内する。担当外患者の旧トークン (= 別患者のステッカー誤読) では
            # 氏名を漏らさず汎用文に留める (情報露出の最小化)。
            if revoked.patient_id == visit.patient_id:
                revoked_patient = await db.scalar(
                    select(Patient).where(
                        Patient.id == revoked.patient_id,
                        Patient.deleted_at.is_(None),
                    )
                )
                patient_name = revoked_patient.name if revoked_patient is not None else "利用者"
                detail = f"このQRは更新されています。{patient_name}の新しいQRをご利用ください"
            else:
                detail = "このQRは更新されています。正しいQRをご利用ください"
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail=detail,
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="QR token not found",
        )
    if patient.id != visit.patient_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="QR does not match this visit's patient",
        )
    return patient, "qr"


# QR capability 分岐 (``api/v1/visits.py`` §4-2) 用の公開別名。担当外の可視性判定を
# 打刻の QR 照合と**同じ関数**で行い、未知 404 / 失効 410 / 別患者 409 の意味論が
# 2 経路でズレないようにする。
resolve_patient_for_visit = _resolve_patient


def _guard_visit(visit: Visit, now: datetime) -> None:
    """visit が打刻可能か (削除 / 取消 / 当日) を検証する (設計 §2-3, R4)."""
    if visit.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Visit is deleted",
        )
    if visit.status == VISIT_STATUS_CANCELLED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Visit is cancelled",
        )
    today_jst = now.astimezone(JST).date()
    if visit.visit_date != today_jst:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Visit is not scheduled for today (JST)",
        )


async def judge_checkin(
    db: AsyncSession,
    visit: Visit,
    staff_id: UUID,
    payload: CheckinCreate,
    kind: str,
    *,
    now: datetime | None = None,
) -> VisitCheckin:
    """打刻 1 件を判定し ``VisitCheckin`` を session に追加して返す (未 commit).

    可視性 (担当か) は呼び出し側 (API) が事前に検証している前提。本関数は QR 照合・
    visit ガード・距離 / match_status 確定・スナップショット保存を担う。
    """
    if now is None:
        now = datetime.now(UTC)

    patient, source = await _resolve_patient(db, visit, payload.qr_token)
    _guard_visit(visit, now)

    thresholds = await load_thresholds(db)
    distance_m = compute_distance_m(patient, payload.lat, payload.lng)
    match_status = compute_match_status(distance_m, payload.accuracy, thresholds)

    checkin = VisitCheckin(
        visit_id=visit.id,
        patient_id=patient.id,
        staff_id=staff_id,
        kind=kind,
        scanned_at=now,
        device_time=payload.device_time,
        lat=payload.lat,
        lng=payload.lng,
        accuracy_m=payload.accuracy,
        distance_m=distance_m,
        match_status=match_status,
        threshold_snapshot={"v": 1, **thresholds},
        reason=payload.reason,
        is_override=payload.is_override,
        checkin_source=source,
    )
    db.add(checkin)
    return checkin
