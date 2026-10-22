"""患者様の 2 つの住所 (保険証の住所・実際の訪問先) の保存のきまり.

正典: ``docs/plans/patient-two-addresses-design-2026-10-22.md`` §4-1。

* ``insurance_address`` = 保険証の住所 (レセプト・カイポケと同じ・座標なし)。
* ``address`` / ``lat`` / ``lng`` = 実際の訪問先 (QR・移動・ならしが読む)。
* ``visit_same_as_insurance`` = 訪問先は保険証の住所と同じか。

作成・更新 (PATCH)・Excel 取込・スマホの患者追加申請・エージェント経由は、すべて
:func:`normalize_patient_addresses` を通す (保存後の値で判定する)。

座標 (2026-10-22 レビュー): きまりの結果、訪問先 ``address`` が保存済みの値から変わったのに
要求に新しい lat/lng が無い (来ていない・保存済みと同じ) ときは、サーバーで位置を求める
(:func:`geocode_visit_address`・控え優先 → Google)。求められなければ 422 / 行エラー。
``address`` が空になるときは lat/lng も空にする。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient
from app.services.geocoding.cached import geocode_with_cache
from app.services.geocoding.client import GeocodingServiceError
from app.services.geocoding.hash import normalize_address

VISIT_GEOCODE_FAILED_MESSAGE = (
    "訪問先の住所から位置が見つかりませんでした。緯度・経度を入れてください。"
)


class VisitGeocodeError(Exception):
    """訪問先の住所から位置を求められなかった (該当なし・キー未設定・通信・上限)."""

    def __init__(self) -> None:
        super().__init__(VISIT_GEOCODE_FAILED_MESSAGE)


@dataclass(frozen=True)
class PatientAddresses:
    insurance_address: str | None
    address: str | None
    visit_same_as_insurance: bool


def _clean(value: str | None) -> str | None:
    """trim し、空文字は None にする."""
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def normalize_patient_addresses(
    *,
    insurance_address: str | None,
    address: str | None,
    visit_same_as_insurance: bool | None,
) -> PatientAddresses:
    """保存のきまり (§4-1) を当てた 3 つの値を返す (純関数).

    1. 保険証の住所があり「同じ」→ 訪問先 = 保険証の住所。
    2. 「別」で訪問先が空 → 「同じ」に戻し、1 と同じにする。
    3. 「別」で訪問先がある → そのまま (保険証の住所が変わっても訪問先は動かない)。
    4. 保険証の住所が空 → 推測で埋めない。訪問先があれば「別」(訪問先を消さないため)、
       両方空なら「同じ」のまま。
    """
    insurance = _clean(insurance_address)
    visit = _clean(address)
    same = True if visit_same_as_insurance is None else bool(visit_same_as_insurance)
    if insurance is None:
        return PatientAddresses(None, visit, visit is None)
    if not same and visit is None:
        same = True
    if same:
        visit = insurance
    return PatientAddresses(insurance, visit, same)


def apply_patient_address_rules(patient: Patient) -> None:
    """ORM の患者に保存のきまりを当てる (保存の直前に呼ぶ)."""
    result = normalize_patient_addresses(
        insurance_address=patient.insurance_address,
        address=patient.address,
        visit_same_as_insurance=patient.visit_same_as_insurance,
    )
    patient.insurance_address = result.insurance_address
    patient.address = result.address
    patient.visit_same_as_insurance = result.visit_same_as_insurance


def same_address_text(a: str | None, b: str | None) -> bool:
    """2 つの住所が同じ文字か (NFKC・空白をそろえて比べる。どちらかが空なら False)."""
    na = normalize_address(a) if a else ""
    nb = normalize_address(b) if b else ""
    return bool(na) and na == nb


def _float_or_none(value: object) -> float | None:
    return None if value is None else float(value)  # type: ignore[arg-type]


def visit_coords_needed(
    *,
    new_address: str | None,
    old_address: str | None,
    requested_lat: object,
    requested_lng: object,
    old_lat: object,
    old_lng: object,
) -> bool:
    """訪問先の位置をサーバーで求めるか.

    訪問先が空でなく、保存済み (新規は空) から変わり、要求に新しい lat/lng が無い
    (どちらか来ていない・保存済みと同じ) とき True。
    """
    if not _clean(new_address) or same_address_text(new_address, old_address):
        return False
    req = (_float_or_none(requested_lat), _float_or_none(requested_lng))
    if None in req:
        return True
    return req == (_float_or_none(old_lat), _float_or_none(old_lng))


async def geocode_visit_address(db: AsyncSession, address: str) -> tuple[float, float]:
    """訪問先の住所の位置 (``/geocode`` と同じ控え優先). 求められなければ VisitGeocodeError."""
    try:
        hit = await geocode_with_cache(db, address)
    except (ValueError, GeocodingServiceError) as exc:
        raise VisitGeocodeError() from exc
    if hit is None:
        raise VisitGeocodeError()
    return hit.lat, hit.lng
