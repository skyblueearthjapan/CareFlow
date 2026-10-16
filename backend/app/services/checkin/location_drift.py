"""登録の位置のずれを見つける (2026-10-17).

``docs/plans/location-drift-align-2026-10-17.md`` §3。患者の登録座標が、職員が実際に
QR を読んだ場所から離れていると、モニターで毎回「不一致」「要確認」になる。打刻の
位置のまとまりから提案の位置を出し、管理者がボタンで合わせられるようにする
(全自動では合わせない)。

患者ごとに、``checkin_source='qr'``・緯度経度あり・GPS 精度 ≤ 100m (精度が無い行は
数える) の打刻を、直近 60 日・新しい順に最大 20 件集める。候補の位置 = 緯度・経度
それぞれの中央値。その点から 50m 以内の打刻を「まとまり」とし、次がすべてそろったら
「ずれ」とする。

1. まとまりが 3 件以上
2. まとまりの打刻が JST で 2 日以上に分かれている
3. まとまりが集めた打刻の 6 割以上 (よそで読んだ打刻に引っ張られないように)
4. 提案の位置 (= まとまりの中央値) が登録座標から ``match_m`` より遠い
   (登録座標が無い患者は距離なしで提案する)
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from statistics import median
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient
from app.models.visit_checkin import VisitCheckin
from app.schemas.patient_location import LocationDrift
from app.services.checkin.actuals import as_utc
from app.services.checkin.judge import JST, load_thresholds
from app.utils.geo import haversine_m

# 数える打刻の GPS 精度の上限 (m)。
MAX_ACCURACY_M = 100
# 集める期間と件数。
LOOKBACK = timedelta(days=60)
MAX_SCANS = 20
# 候補の位置からこの距離以内の打刻を「まとまり」とする (m)。
CLUSTER_RADIUS_M = 50
# まとまりの条件 (件数・日数・割合)。
MIN_CLUSTER_SCANS = 3
MIN_CLUSTER_DAYS = 2
MIN_CLUSTER_RATIO = 0.6

# 打刻 1 件 = (緯度, 経度, 受信時刻)。
_Scan = tuple[float, float, datetime]


def compute_drift(
    scans: Sequence[_Scan],
    patient_lat: float | None,
    patient_lng: float | None,
    match_m: float,
) -> LocationDrift | None:
    """集めた打刻 (新しい順・最大 20 件) からずれを判定する。ずれが無ければ None。"""
    if not scans:
        return None
    center_lat = median(s[0] for s in scans)
    center_lng = median(s[1] for s in scans)
    cluster = [
        s for s in scans if haversine_m(center_lat, center_lng, s[0], s[1]) <= CLUSTER_RADIUS_M
    ]
    if len(cluster) < MIN_CLUSTER_SCANS:
        return None
    days = len({as_utc(s[2]).astimezone(JST).date() for s in cluster})
    if days < MIN_CLUSTER_DAYS:
        return None
    if len(cluster) < MIN_CLUSTER_RATIO * len(scans):
        return None
    suggested_lat = round(median(s[0] for s in cluster), 7)
    suggested_lng = round(median(s[1] for s in cluster), 7)
    distance_m: float | None = None
    if patient_lat is not None and patient_lng is not None:
        distance_m = round(haversine_m(patient_lat, patient_lng, suggested_lat, suggested_lng), 1)
        if distance_m <= match_m:
            return None
    return LocationDrift(
        distance_m=distance_m,
        suggested_lat=suggested_lat,
        suggested_lng=suggested_lng,
        scans=len(cluster),
        days=days,
    )


def _float(value: Decimal | float | None) -> float | None:
    return float(value) if value is not None else None


async def load_location_drifts(
    db: AsyncSession,
    patients: Iterable[Patient],
    *,
    now: datetime | None = None,
    match_m: float | None = None,
) -> dict[UUID, LocationDrift]:
    """患者ごとのずれをまとめて返す (ずれのある患者だけ)。打刻は 1 回のクエリで集める。"""
    by_id = {p.id: p for p in patients}
    if not by_id:
        return {}
    if now is None:
        now = datetime.now(UTC)
    if match_m is None:
        match_m = (await load_thresholds(db))["match_m"]

    rows = (
        await db.execute(
            select(
                VisitCheckin.patient_id,
                VisitCheckin.lat,
                VisitCheckin.lng,
                VisitCheckin.scanned_at,
            )
            .where(
                VisitCheckin.patient_id.in_(list(by_id)),
                VisitCheckin.checkin_source == "qr",
                VisitCheckin.lat.is_not(None),
                VisitCheckin.lng.is_not(None),
                or_(
                    VisitCheckin.accuracy_m.is_(None),
                    VisitCheckin.accuracy_m <= MAX_ACCURACY_M,
                ),
                VisitCheckin.scanned_at >= now - LOOKBACK,
            )
            .order_by(VisitCheckin.patient_id, VisitCheckin.scanned_at.desc())
        )
    ).all()

    scans_by_patient: dict[UUID, list[_Scan]] = defaultdict(list)
    for patient_id, lat, lng, scanned_at in rows:
        bucket = scans_by_patient[patient_id]
        if len(bucket) < MAX_SCANS:
            bucket.append((float(lat), float(lng), scanned_at))

    result: dict[UUID, LocationDrift] = {}
    for patient_id, scans in scans_by_patient.items():
        patient = by_id[patient_id]
        drift = compute_drift(scans, _float(patient.lat), _float(patient.lng), match_m)
        if drift is not None:
            result[patient_id] = drift
    return result


async def find_location_drift(
    db: AsyncSession, patient: Patient, *, now: datetime | None = None
) -> LocationDrift | None:
    """1 人の患者のずれ (無ければ None)。"""
    return (await load_location_drifts(db, [patient], now=now)).get(patient.id)
