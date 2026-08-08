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
async def test_pinned_changed_is_allowed(db) -> None:
    """統合 (PO 決定 2026-08-09): 完全固定の枠でも人手の型編集は常に可 (旧 V2 撤廃).

    完全固定の意味は「エンジンが動かさない」。人手ブロック (422) はしない。
    """
    p = await _make_patient(db, code="PIN-2")
    await _add_pfv(db, patient=p, weekday=0, start=time(9, 0), duration=30, is_pinned=True)

    items = [_item(0, time(10, 0), 30, is_pinned=True)]
    result = await validate_pfv_changes(db, p.id, items, "normal", config=CFG)

    assert not result.has_errors
    assert all(w.code != CODE_PINNED for w in result.warnings)


@pytest.mark.asyncio
async def test_pinned_deleted_is_allowed(db) -> None:
    """統合: 完全固定の枠の削除も人手なら可 (旧 V2 撤廃)."""
    p = await _make_patient(db, code="PIN-3")
    await _add_pfv(db, patient=p, weekday=0, start=time(9, 0), duration=30, is_pinned=True)

    result = await validate_pfv_changes(db, p.id, [], "normal", config=CFG)

    assert not result.has_errors


@pytest.mark.asyncio
async def test_pinned_flag_only_diff_is_allowed(db) -> None:
    """統合: フラグ差分も 422 にしない (人手の完全固定解除は PUT でも可)."""
    p = await _make_patient(db, code="PIN-4")
    await _add_pfv(db, patient=p, weekday=0, start=time(9, 0), duration=30, is_pinned=True)

    items = [_item(0, time(9, 0), 30, is_pinned=False)]
    result = await validate_pfv_changes(db, p.id, items, "normal", config=CFG)

    assert not result.has_errors


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

    assert any(w.code == CODE_CAPACITY and "分" in w.message for w in result.warnings)


@pytest.mark.asyncio
async def test_no_capacity_warning_when_no_office(db) -> None:
    """拠点 / 座標が無い患者は V3/V5 を評価しない (V4 のみ)."""
    p = await _make_patient(db, code="CAP3")  # office/coords なし
    items = [_item(0, time(9, 0), 30)]
    result = await validate_pfv_changes(db, p.id, items, "normal", config=CFG)

    assert not result.has_errors
    assert result.warnings == []


# ---------------------------------------------------------------------------
# 可動域はピン留めで上書きしない (PO 決定 2026-08-08 / 旧 V6 の廃止)
#
# 可動域は「この枠をどこまで動かしてよいか」という現場の判断の記録であり、
# ピン留めは一括で掛け外しする上乗せの錠。独立した 2 軸として扱う。
# 旧 V6 は保存のたびに pinned 行の可動域を 'locked' で上書きしていたため、
# ピン留めの掛け外しで現場の設定が消えていた。
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pinned_row_keeps_its_movability_unknown(db) -> None:
    """is_pinned=True でも movability='unknown' はそのまま保存される (旧 V6 は locked に矯正していた)."""
    p = await _make_patient(db, code="MV-V6-1")
    items = [_item(0, time(9, 0), 30, is_pinned=True, movability="unknown")]
    result = await validate_pfv_changes(db, p.id, items, "normal", config=CFG)

    assert not result.has_errors
    # 矯正 warning はもう出ない.
    assert all(w.code != CODE_MOVABILITY_CORRECTED for w in result.warnings)
    assert len(result.corrected_items) == 1
    assert result.corrected_items[0].movability == "unknown"
    assert items[0].movability == "unknown"


@pytest.mark.asyncio
async def test_pinned_row_keeps_explicit_locked(db) -> None:
    """is_pinned=True かつ明示 'locked' はそのまま (ピンを外した後も保護が残る前提)."""
    p = await _make_patient(db, code="MV-V6-2")
    items = [_item(0, time(9, 0), 30, is_pinned=True, movability="locked")]
    result = await validate_pfv_changes(db, p.id, items, "normal", config=CFG)

    assert all(w.code != CODE_MOVABILITY_CORRECTED for w in result.warnings)
    assert result.corrected_items[0].movability == "locked"


@pytest.mark.asyncio
async def test_pinned_row_keeps_time_flexible(db) -> None:
    """核心: ピン留め中でも 'time_flexible' が保持される.

    旧実装ではここが 'locked' に潰され、ピンを外すと 'unknown' に戻っていたため、
    現場が設定した「時刻変更可」が往復で消えていた。
    """
    p = await _make_patient(db, code="MV-V6-3")
    items = [_item(0, time(9, 0), 30, is_pinned=True, movability="time_flexible")]
    result = await validate_pfv_changes(db, p.id, items, "normal", config=CFG)

    assert all(w.code != CODE_MOVABILITY_CORRECTED for w in result.warnings)
    assert result.corrected_items[0].movability == "time_flexible"


@pytest.mark.asyncio
async def test_non_pinned_row_keeps_its_movability(db) -> None:
    """非 pinned 行は従来どおりそのまま保持 (regression)."""
    p = await _make_patient(db, code="MV-V6-4")
    items = [_item(0, time(9, 0), 30, is_pinned=False, movability="time_flexible")]
    result = await validate_pfv_changes(db, p.id, items, "normal", config=CFG)

    assert all(w.code != CODE_MOVABILITY_CORRECTED for w in result.warnings)
    assert result.corrected_items[0].movability == "time_flexible"


# ---------------------------------------------------------------------------
# P4-C: PATCH 解放 と V2 pinned 保護 (同一性規約) の非矛盾
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_p4c_movability_diff_does_not_trigger_pinned_error(db) -> None:
    """V2 同一性タプルは movability を含まないため、pinned 行の movability 差異は保持=OK.

    PATCH pin 経由での movability 解放 (locked→unknown) と PUT の pinned 保護が矛盾しない
    ことの確認: 既存 pinned 行 (DB 上 movability='locked') を、同一 identity で movability
    のみ異なる body で送っても CODE_PINNED error は出ない (保持扱い). V6 が locked に戻す.
    """
    p = await _make_patient(db, code="P4C-1")
    await _add_pfv(db, patient=p, weekday=0, start=time(9, 0), duration=30, is_pinned=True)

    # 同一 identity (weekday/slot/time/duration/office/course/is_pinned) だが movability だけ相違.
    items = [_item(0, time(9, 0), 30, is_pinned=True, movability="time_flexible")]
    result = await validate_pfv_changes(db, p.id, items, "normal", config=CFG)

    assert not result.has_errors
    assert all(w.code != CODE_PINNED for w in result.warnings)


@pytest.mark.asyncio
async def test_p4c_unpinned_row_movability_change_is_ok(db) -> None:
    """PATCH で解除済み (is_pinned=False) の行は V2 保護対象外 → movability 変更も自由.

    ピン解除後、当該行を non-pinned として PUT で再送しても pinned 保護は発火しない.
    """
    p = await _make_patient(db, code="P4C-2")
    # 解除済みを模す: is_pinned=False の既存行 (P4-C の PATCH 解放後の DB 状態).
    await _add_pfv(db, patient=p, weekday=0, start=time(9, 0), duration=30, is_pinned=False)

    items = [_item(0, time(9, 0), 30, is_pinned=False, movability="unknown")]
    result = await validate_pfv_changes(db, p.id, items, "normal", config=CFG)

    assert not result.has_errors
    assert all(w.code != CODE_PINNED for w in result.warnings)
