"""患者様の 2 つの住所 (保険証の住所・実際の訪問先) の保存のきまり.

正典: ``docs/plans/patient-two-addresses-design-2026-10-22.md`` §4-1。

* ``insurance_address`` = 保険証の住所 (レセプト・カイポケと同じ・座標なし)。
* ``address`` / ``lat`` / ``lng`` = 実際の訪問先 (QR・移動・ならしが読む)。
* ``visit_same_as_insurance`` = 訪問先は保険証の住所と同じか。

作成・更新 (PATCH)・Excel 取込・スマホの患者追加申請・エージェント経由は、すべて
:func:`normalize_patient_addresses` を通す (保存後の値で判定する)。座標は触らない
(要求に来た値を使う。サーバーで geocode し直さない)。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.patient import Patient


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
