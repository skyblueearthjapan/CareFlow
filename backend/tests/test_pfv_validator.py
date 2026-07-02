"""P0-2 再検証カーネル (pfv_validator) の単体テスト.

設計書 docs/plans/p0-2-apply-safety-net-design.md §7 のケースを網羅:
    - V2 pinned: 保持=OK / 変更=422(error) / 削除=422(error) / 非pinned変更=OK
    - V3 患者間衝突: 衝突あり / なし / 同住所ペア許容 / 90分占有
    - V4 H10 昼休み重複
    - V5 コース容量 (人数 6 / 総所要 480 分)

validate_pfv_changes は read-only なので直接呼んで PfvValidationResult を検証する.
"""

from __future__ import annotations

from datetime import time

import pytest

from app.models import Office, Patient, PatientFixedVisit
from app.schemas.v2.patient_fixed_visit import PatientFixedVisitV2Base
from app.services.scheduling.config import DEFAULT_SCHEDULING_CONFIG
from app.services.scheduling.pfv_validator import (
    CODE_CAPACITY,
    CODE_LUNCH,
    CODE_MOVABILITY_CORRECTED,
    CODE_PATIENT_CONFLICT,
    CODE_PINNED,
    validate_pfv_changes,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

CFG = DEFAULT_SCHEDULING_CONFIG

# 同住所 (identical) と、遠距離 (>10km) / 近距離 (~0.5km) の座標.
_ADDR_A = (35.600, 140.100)
_ADDR_FAR = (35.700, 140.200)
_ADDR_NEAR = (35.605, 140.100)  # A から ~0.5km (別バケット, 移動は小)


async def _make_office(db, code: str) -> Office:
    o = Office(code=code, name=code)
    db.add(o)
    await db.commit()
    await db.refresh(o)
    return o


async def _make_patient(
    db,
    *,
    code: str,
    office_id=None,
    coords: tuple[float, float] | None = None,
) -> Patient:
    lat, lng = coords if coords is not None else (None, None)
    p = Patient(
        code=code,
        name=f"患者{code}",
        special_week_active=[],
        primary_office_id=office_id,
        status="active",
        lat=lat,
        lng=lng,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _add_pfv(
    db,
    *,
    patient: Patient,
    weekday: int,
    start: time,
    duration: int,
    course_template_id=None,
    slot_index: int = 0,
    is_pinned: bool = False,
    mode: str = "normal",
) -> PatientFixedVisit:
    row = PatientFixedVisit(
        patient_id=patient.id,
        mode=mode,
        weekday=weekday,
        start_time=start,
        duration_min=duration,
        course_template_id=course_template_id,
        slot_index=slot_index,
        is_pinned=is_pinned,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


def _item(
    weekday: int,
    start: time,
    duration: int = 30,
    *,
    course_template_id=None,
    slot_index: int = 0,
    is_pinned: bool = False,
    movability: str = "unknown",
) -> PatientFixedVisitV2Base:
    return PatientFixedVisitV2Base(
        weekday=weekday,
        start_time=start,
        duration_min=duration,
        course_template_id=course_template_id,
        slot_index=slot_index,
        is_pinned=is_pinned,
        movability=movability,
    )


# ---------------------------------------------------------------------------
# V2: pinned 保護 (同一性規約)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pinned_preserved_is_ok(db) -> None:
    """既存 pinned 行が body に完全一致で含まれる → error なし (保持)."""
    p = await _make_patient(db, code="PIN-1")
    await _add_pfv(db, patient=p, weekday=0, start=time(9, 0), duration=30, is_pinned=True)

    # 完全一致で保持 (is_pinned=True 込み).
    items = [_item(0, time(9, 0), 30, is_pinned=True)]
    result = await validate_pfv_changes(db, p.id, items, "normal", config=CFG)

    assert not result.has_errors
    assert all(w.code != CODE_PINNED for w in result.warnings)


@pytest.mark.asyncio
async def test_pinned_changed_is_error(db) -> None:
    """既存 pinned 行の属性 (start_time) を変更 → 422 (error)."""
    p = await _make_patient(db, code="PIN-2")
    await _add_pfv(db, patient=p, weekday=0, start=time(9, 0), duration=30, is_pinned=True)

    # 同 weekday/slot だが start_time が異なる = 変更.
    items = [_item(0, time(10, 0), 30, is_pinned=True)]
    result = await validate_pfv_changes(db, p.id, items, "normal", config=CFG)

    assert result.has_errors
    assert any(w.code == CODE_PINNED and w.severity == "error" for w in result.warnings)


@pytest.mark.asyncio
async def test_pinned_deleted_is_error(db) -> None:
    """既存 pinned 行が body から消える (削除) → 422 (error)."""
    p = await _make_patient(db, code="PIN-3")
    await _add_pfv(db, patient=p, weekday=0, start=time(9, 0), duration=30, is_pinned=True)

    result = await validate_pfv_changes(db, p.id, [], "normal", config=CFG)

    assert result.has_errors
    assert any(w.code == CODE_PINNED for w in result.errors)


@pytest.mark.asyncio
async def test_pinned_flag_only_diff_is_error(db) -> None:
    """既存 pinned 行を is_pinned=False で送る (フラグのみ相違) → 変更扱いで error.

    (安全弁は今回不要 = FE が is_pinned を運搬しているため厳格規約を適用.)
    """
    p = await _make_patient(db, code="PIN-4")
    await _add_pfv(db, patient=p, weekday=0, start=time(9, 0), duration=30, is_pinned=True)

    items = [_item(0, time(9, 0), 30, is_pinned=False)]
    result = await validate_pfv_changes(db, p.id, items, "normal", config=CFG)

    assert result.has_errors


@pytest.mark.asyncio
async def test_non_pinned_change_is_ok(db) -> None:
    """非 pinned 行の変更は自由 (error なし)."""
    p = await _make_patient(db, code="PIN-5")
    await _add_pfv(db, patient=p, weekday=0, start=time(9, 0), duration=30, is_pinned=False)

    items = [_item(0, time(11, 0), 60, is_pinned=False)]
    result = await validate_pfv_changes(db, p.id, items, "normal", config=CFG)

    assert not result.has_errors


# ---------------------------------------------------------------------------
# V3: 患者間時間衝突
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conflict_overlapping_time(db) -> None:
    """同コース同曜日・異住所・同時刻 → 衝突 warning."""
    office = await _make_office(db, "OF-C1")
    ct_id = None
    target = await _make_patient(db, code="C1-T", office_id=office.id, coords=_ADDR_A)
    other = await _make_patient(db, code="C1-O", office_id=office.id, coords=_ADDR_FAR)
    await _add_pfv(
        db, patient=other, weekday=0, start=time(10, 0), duration=30, course_template_id=ct_id
    )

    items = [_item(0, time(10, 0), 30, course_template_id=ct_id)]
    result = await validate_pfv_changes(db, target.id, items, "normal", config=CFG)

    assert not result.has_errors
    assert any(w.code == CODE_PATIENT_CONFLICT for w in result.warnings)


@pytest.mark.asyncio
async def test_no_conflict_disjoint_time(db) -> None:
    """同コースでも十分離れた時刻 (移動込みで収まる) → 衝突なし."""
    office = await _make_office(db, "OF-C2")
    target = await _make_patient(db, code="C2-T", office_id=office.id, coords=_ADDR_A)
    other = await _make_patient(db, code="C2-O", office_id=office.id, coords=_ADDR_FAR)
    # target 09:30-10:00, other 14:00 → 大きく離れる.
    await _add_pfv(db, patient=other, weekday=0, start=time(14, 0), duration=30)

    items = [_item(0, time(9, 30), 30)]
    result = await validate_pfv_changes(db, target.id, items, "normal", config=CFG)

    assert not result.has_errors
    assert all(w.code != CODE_PATIENT_CONFLICT for w in result.warnings)


@pytest.mark.asyncio
async def test_same_address_pair_no_conflict(db) -> None:
    """同住所・同時刻の相手は同時刻ペアとして許容 → 衝突なし."""
    office = await _make_office(db, "OF-C3")
    target = await _make_patient(db, code="C3-T", office_id=office.id, coords=_ADDR_A)
    other = await _make_patient(db, code="C3-O", office_id=office.id, coords=_ADDR_A)
    await _add_pfv(db, patient=other, weekday=0, start=time(10, 0), duration=30)

    items = [_item(0, time(10, 0), 30)]
    result = await validate_pfv_changes(db, target.id, items, "normal", config=CFG)

    assert all(w.code != CODE_PATIENT_CONFLICT for w in result.warnings)


@pytest.mark.asyncio
async def test_single_visit_no_conflict_at_1100(db) -> None:
    """単独 (ペアなし) の 10:00-10:30 相手なら 11:00 は移動込みで収まる (基準ケース)."""
    office = await _make_office(db, "OF-C4")
    target = await _make_patient(db, code="C4-T", office_id=office.id, coords=_ADDR_NEAR)
    other = await _make_patient(db, code="C4-O", office_id=office.id, coords=_ADDR_A)
    await _add_pfv(db, patient=other, weekday=0, start=time(10, 0), duration=30)

    items = [_item(0, time(11, 0), 30)]
    result = await validate_pfv_changes(db, target.id, items, "normal", config=CFG)

    assert all(w.code != CODE_PATIENT_CONFLICT for w in result.warnings)


@pytest.mark.asyncio
async def test_90min_pair_occupancy_causes_conflict(db) -> None:
    """同住所ペア (10:00) は 90 分占有 (→11:30) を持つため、11:00 の異住所枠は衝突.

    単独 (test_single_visit_no_conflict_at_1100) では衝突しない距離・時刻でも、
    ペア占有の底上げで衝突が発火することを確認 (90 分占有の再現).
    """
    office = await _make_office(db, "OF-C5")
    target = await _make_patient(db, code="C5-T", office_id=office.id, coords=_ADDR_NEAR)
    pair_a = await _make_patient(db, code="C5-A", office_id=office.id, coords=_ADDR_A)
    pair_b = await _make_patient(db, code="C5-B", office_id=office.id, coords=_ADDR_A)
    # 同住所・同時刻ペア (10:00).
    await _add_pfv(db, patient=pair_a, weekday=0, start=time(10, 0), duration=30)
    await _add_pfv(db, patient=pair_b, weekday=0, start=time(10, 0), duration=30)

    items = [_item(0, time(11, 0), 30)]
    result = await validate_pfv_changes(db, target.id, items, "normal", config=CFG)

    assert any(w.code == CODE_PATIENT_CONFLICT for w in result.warnings)


# ---------------------------------------------------------------------------
# V4: H10 昼休み重複
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lunch_break_overlap_warning(db) -> None:
    """昼休み (11:30-13:30) を避けられない枠 → lunch 警告."""
    p = await _make_patient(db, code="LUN-1")
    # 11:45-13:15: start<12:00 かつ end>13:00 → 物理的に lunch を取れない.
    items = [_item(0, time(11, 45), 90)]
    result = await validate_pfv_changes(db, p.id, items, "normal", config=CFG)

    assert not result.has_errors
    assert any(w.code == CODE_LUNCH for w in result.warnings)


@pytest.mark.asyncio
async def test_no_lunch_warning_for_morning_slot(db) -> None:
    """午前中に収まる枠は lunch 警告なし."""
    p = await _make_patient(db, code="LUN-2")
    items = [_item(0, time(9, 0), 30)]
    result = await validate_pfv_changes(db, p.id, items, "normal", config=CFG)

    assert all(w.code != CODE_LUNCH for w in result.warnings)


# ---------------------------------------------------------------------------
# V5: コース容量
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capacity_patient_count_exceeded(db) -> None:
    """同コースの人数が上限 6 名を超える → 容量警告 (人数)."""
    office = await _make_office(db, "OF-CAP1")
    target = await _make_patient(db, code="CAP1-T", office_id=office.id, coords=_ADDR_A)
    # 同住所の他患者 6 名 (target と合わせて 7 名) を別々の時刻で配置 (移動衝突を避ける).
    for i in range(6):
        other = await _make_patient(db, code=f"CAP1-O{i}", office_id=office.id, coords=_ADDR_A)
        await _add_pfv(db, patient=other, weekday=0, start=time(9, 30 + i), duration=15)

    items = [_item(0, time(11, 0), 15)]
    result = await validate_pfv_changes(db, target.id, items, "normal", config=CFG)

    assert any(w.code == CODE_CAPACITY for w in result.warnings)


@pytest.mark.asyncio
async def test_capacity_minutes_exceeded(db) -> None:
    """同コースの総所要が 480 分を超える → 容量警告 (総所要)."""
    office = await _make_office(db, "OF-CAP2")
    target = await _make_patient(db, code="CAP2-T", office_id=office.id, coords=_ADDR_A)
    o1 = await _make_patient(db, code="CAP2-O1", office_id=office.id, coords=_ADDR_A)
    o2 = await _make_patient(db, code="CAP2-O2", office_id=office.id, coords=_ADDR_A)
    # 同住所 3 名 × 200 分 → 総所要 600 分 (>480).
    await _add_pfv(db, patient=o1, weekday=0, start=time(9, 35), duration=200)
    await _add_pfv(db, patient=o2, weekday=0, start=time(9, 40), duration=200)

    items = [_item(0, time(9, 30), 200)]
    result = await validate_pfv_changes(db, target.id, items, "normal", config=CFG)

    assert any(
        w.code == CODE_CAPACITY and "分" in w.message for w in result.warnings
    )


@pytest.mark.asyncio
async def test_no_capacity_warning_when_no_office(db) -> None:
    """拠点 / 座標が無い患者は V3/V5 を評価しない (V4 のみ)."""
    p = await _make_patient(db, code="CAP3")  # office/coords なし
    items = [_item(0, time(9, 0), 30)]
    result = await validate_pfv_changes(db, p.id, items, "normal", config=CFG)

    assert not result.has_errors
    assert result.warnings == []


# ---------------------------------------------------------------------------
# V6: pinned 行の movability 矯正 (is_pinned=True ⇒ movability='locked')
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v6_pinned_movability_corrected_to_locked(db) -> None:
    """is_pinned=True かつ movability='unknown' → corrected_items で 'locked' + warning."""
    p = await _make_patient(db, code="MV-V6-1")
    items = [_item(0, time(9, 0), 30, is_pinned=True, movability="unknown")]
    result = await validate_pfv_changes(db, p.id, items, "normal", config=CFG)

    # 矯正 warning が出る (error ではない = 続行可).
    assert not result.has_errors
    assert any(
        w.code == CODE_MOVABILITY_CORRECTED and w.severity == "warning"
        for w in result.warnings
    )
    # corrected_items の movability が 'locked' に矯正されている.
    assert len(result.corrected_items) == 1
    assert result.corrected_items[0].movability == "locked"
    # 元 items は汚さない (副作用なし).
    assert items[0].movability == "unknown"


@pytest.mark.asyncio
async def test_v6_no_correction_when_already_locked(db) -> None:
    """is_pinned=True かつ movability='locked' は矯正不要 (warning なし)."""
    p = await _make_patient(db, code="MV-V6-2")
    items = [_item(0, time(9, 0), 30, is_pinned=True, movability="locked")]
    result = await validate_pfv_changes(db, p.id, items, "normal", config=CFG)

    assert all(w.code != CODE_MOVABILITY_CORRECTED for w in result.warnings)
    assert result.corrected_items[0].movability == "locked"


@pytest.mark.asyncio
async def test_v6_no_correction_for_non_pinned(db) -> None:
    """非 pinned 行は movability を矯正しない (time_flexible をそのまま保持)."""
    p = await _make_patient(db, code="MV-V6-3")
    items = [_item(0, time(9, 0), 30, is_pinned=False, movability="time_flexible")]
    result = await validate_pfv_changes(db, p.id, items, "normal", config=CFG)

    assert all(w.code != CODE_MOVABILITY_CORRECTED for w in result.warnings)
    assert result.corrected_items[0].movability == "time_flexible"
