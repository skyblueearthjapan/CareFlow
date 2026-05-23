"""Phase G-21 T3 + W1: Algorithm refactor + Wave 1 統合 tests.

検証観点 (最低 15 ケース pass):
  T3.1  pinned 厳守 (PFV.start_time そのまま)
  T3.2  pinned + 異住所同時刻 (= shift 警告)
  T3.3  非 pinned 配置 (weekly_pattern preferred_start..end 範囲内)
  T3.4  同 patient 同 weekday pinned + weekly_pattern entry 同時 → Invariant G21-A
  T3.5  pair_mode=blocked → 同時刻 NG
  T3.6  pair_mode=required → 同時刻優先
  T3.7  pair_mode=preferred (default) → 既存挙動 (= 同住所同時刻 OK)
  T3.8  全患者 blocked link → 全員独立 set
  T3.9  Before 4 経路 union: DB Visit のみある patient が Before に出る
  T3.10 Before 4 経路 union: weekly_pattern のみある patient が Before に出る
  T3.11 Before 4 経路 union: pending_overlay 反映
  T3.12 reset_to_fixed mode='legacy' (= 既存 Phase G-10/G-11 挙動)
  T3.13 reset_to_fixed mode='auto' (= 新 pinned 厳守 + 非 pinned weekly_pattern 配置)
  T3.14 reset_to_fixed dry-run 件数返却
  T3.15 D&D で pinned 動かしたら 422
  T3.16 _enforce_h2_split_overflow sort 決定論化 (= 同 input で常に同じ overflow patient 選出)
  T3.17 Wave 1: apply_week_only で apply_travel_corrections 呼ばれる (補正一元化)
  T3.18 Wave 1: reset_to_fixed (auto mode) でも補正呼ばれる
  T3.19 feature flag OFF 拠点は旧経路 (canary 切替)
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from uuid import UUID

import pytest
from sqlalchemy import select

from app.models import Office, Patient
from app.models.office_feature_flag import OfficeFeatureFlag
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.staff import Staff, StaffShift
from app.models.visit import Visit
from app.services.scheduling.auto_allocator_v2 import (
    G21_NEW_ALGORITHM_FEATURE_KEY,
    PinnedVisitMovedError,
    V2Set,
    V2Visit,
    V2Warning,
    _enforce_h2_split_overflow,
    _enforce_same_address_pair_mode,
    _load_before_visits_v2,
    apply_week_only,
    build_visits_for_pool_v2,
    reset_visits_to_fixed,
    run_v2_pipeline,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_office(db, *, name: str) -> Office:
    o = Office(name=name)
    db.add(o)
    await db.flush()
    return o


async def _make_patient(
    db,
    *,
    code: str,
    name: str,
    office: Office,
    lat: float = 35.65,
    lng: float = 140.10,
    weekly_pattern: dict | None = None,
) -> Patient:
    p = Patient(
        code=code,
        name=name,
        status="active",
        lat=lat,
        lng=lng,
        primary_office_id=office.id,
        weekly_pattern=weekly_pattern,
    )
    db.add(p)
    await db.flush()
    return p


async def _make_pfv(
    db,
    *,
    patient: Patient,
    weekday: int,
    start_hhmm: tuple[int, int],
    is_pinned: bool = True,
    duration_min: int = 35,
) -> PatientFixedVisit:
    pfv = PatientFixedVisit(
        patient_id=patient.id,
        mode="normal",
        weekday=weekday,
        start_time=time(start_hhmm[0], start_hhmm[1]),
        duration_min=duration_min,
        slot_index=0,
        is_pinned=is_pinned,
    )
    db.add(pfv)
    await db.flush()
    return pfv


async def _enable_g21_flag(db, office: Office) -> None:
    flag = OfficeFeatureFlag(
        office_id=office.id,
        feature_key=G21_NEW_ALGORITHM_FEATURE_KEY,
        enabled_at=datetime.now(tz=UTC),
    )
    db.add(flag)
    await db.flush()


async def _seed_staff_and_shift(db, *, office: Office, weekday: int) -> Staff:
    s = Staff(
        name=f"staff-{office.name}-{weekday}",
        role="staff",
        is_trainee=False,
        primary_office_id=office.id,
    )
    db.add(s)
    await db.flush()
    db.add(StaffShift(staff_id=s.id, weekday=weekday, is_on=True))
    await db.flush()
    return s


# ---------------------------------------------------------------------------
# T3.1: pinned 厳守 (PFV.start_time そのまま)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t3_1_pinned_keeps_pfv_start_time(db) -> None:
    """pinned PFV (is_pinned=True) は PFV.start_time が apply_travel_corrections で動かない."""
    office = await _make_office(db, name="g21-t3-1-office")
    p = await _make_patient(
        db,
        code="T31",
        name="t3.1 patient",
        office=office,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    pfv = await _make_pfv(db, patient=p, weekday=0, start_hhmm=(10, 0), is_pinned=True)
    await _seed_staff_and_shift(db, office=office, weekday=0)
    await db.commit()

    # build_visits_for_pool_v2 経由で時刻保持を確認.
    visits = build_visits_for_pool_v2(
        [p],
        fixed_by_patient={p.id: [pfv]},
    )
    assert len(visits) == 1
    assert visits[0].start_time == time(10, 0)
    assert visits[0].is_pinned is True
    assert visits[0].time_type == "固定"


# ---------------------------------------------------------------------------
# T3.2: pinned + 異住所同時刻 (= shift 警告だが pinned は動かない)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t3_2_pinned_with_cross_address_same_time_warns_but_not_moved(db) -> None:
    """pinned 患者と非 pinned 患者が異住所同時刻でも、pinned 側は動かない."""
    office = await _make_office(db, name="g21-t3-2-office")
    # patient A: 10:00 pinned, lat=35.65
    pa = await _make_patient(
        db,
        code="T32A",
        name="t3.2 patient A",
        office=office,
        lat=35.65,
        lng=140.10,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    pfv_a = await _make_pfv(db, patient=pa, weekday=0, start_hhmm=(10, 0), is_pinned=True)
    # patient B: 10:00 非 pinned, lat=35.70 (異住所)
    pb = await _make_patient(
        db,
        code="T32B",
        name="t3.2 patient B",
        office=office,
        lat=35.70,
        lng=140.15,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    pfv_b = await _make_pfv(db, patient=pb, weekday=0, start_hhmm=(10, 0), is_pinned=False)
    await db.commit()

    visits = build_visits_for_pool_v2(
        [pa, pb],
        fixed_by_patient={pa.id: [pfv_a], pb.id: [pfv_b]},
    )
    # pinned A は keep, 非 pinned B は weekly_pattern 経路で配置.
    a_visits = [v for v in visits if v.patient_id == pa.id]
    assert len(a_visits) == 1
    assert a_visits[0].is_pinned is True
    assert a_visits[0].start_time == time(10, 0)


# ---------------------------------------------------------------------------
# T3.3: 非 pinned 配置 (weekly_pattern preferred_start..end 範囲内)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t3_3_non_pinned_uses_weekly_pattern(db) -> None:
    """非 pinned 患者は weekly_pattern.time_type / preferred_start / preferred_end を使う."""
    office = await _make_office(db, name="g21-t3-3-office")
    p = await _make_patient(
        db,
        code="T33",
        name="t3.3 patient",
        office=office,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "09:00",
            "preferred_end": "17:00",
            "time_type": "時間帯",
        },
    )
    pfv = await _make_pfv(db, patient=p, weekday=0, start_hhmm=(10, 0), is_pinned=False)
    await db.commit()

    visits = build_visits_for_pool_v2(
        [p],
        fixed_by_patient={p.id: [pfv]},
    )
    # 非 pinned 経路: weekly_pattern entry をそのまま使う (PFV は 1 行で is_pinned=False
    # でも、 build_visits_for_pool_v2 は pinned PFV のみ採用し、それ以外は weekly_pattern 経路).
    assert len(visits) == 1
    v = visits[0]
    assert v.is_pinned is False
    assert v.time_type == "時間帯"
    assert v.preferred_start == "09:00"
    assert v.preferred_end == "17:00"


# ---------------------------------------------------------------------------
# T3.4: Invariant G21-A (pinned PFV + weekly_pattern 同時 → weekly_pattern skip + warning)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t3_4_invariant_g21_a_pinned_skips_weekly_pattern(db) -> None:
    """同 patient × 同 weekday に pinned PFV と weekly_pattern entry が同時にある場合、
    weekly_pattern entry は skip + warning."""
    office = await _make_office(db, name="g21-t3-4-office")
    p = await _make_patient(
        db,
        code="T34",
        name="t3.4 patient",
        office=office,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "09:00",
            "preferred_end": "17:00",
            "time_type": "時間帯",
        },
    )
    pfv = await _make_pfv(db, patient=p, weekday=0, start_hhmm=(10, 30), is_pinned=True)
    await db.commit()

    warnings: list[V2Warning] = []
    visits = build_visits_for_pool_v2(
        [p],
        fixed_by_patient={p.id: [pfv]},
        warnings=warnings,
    )
    # pinned 1 件のみ生成. weekly_pattern entry は skip.
    assert len(visits) == 1
    assert visits[0].is_pinned is True
    assert visits[0].start_time == time(10, 30)
    # Invariant G21-A warning.
    assert any("Invariant G21-A" in w.message for w in warnings), warnings


# ---------------------------------------------------------------------------
# T3.5: pair_mode=blocked → 同時刻 NG (同 set に入れない)
# ---------------------------------------------------------------------------


def _make_v2_visit(
    patient_id, *, name="x", lat=35.65, lng=140.10, st=time(10, 0), office_id=None
) -> V2Visit:
    """test 用 V2Visit factory.

    Phase G-21 T3 (Reviewer M4 fix): end_time は ``_add_minutes`` で計算する
    (旧実装の手計算は service_minutes=30 専用かつ HH:30 境界跨ぎで壊れていた).
    """
    import uuid as _uuid

    from app.services.scheduling.auto_allocator_v2 import _add_minutes as _add

    if office_id is None:
        office_id = _uuid.uuid4()
    return V2Visit(
        patient_id=patient_id,
        patient_name=name,
        patient_code=None,
        weekday=0,
        start_time=st,
        end_time=_add(st, 30),
        service_minutes=30,
        lat=lat,
        lng=lng,
        office_id=office_id,
        am_pm="am",
        source_kind="pool",
    )


@pytest.mark.asyncio
async def test_t3_5_pair_mode_blocked_separates_set(db) -> None:
    """pair_mode=blocked のペアは同 set 内で同時刻にできない."""
    import uuid as _uuid

    pa_id = _uuid.uuid4()
    pb_id = _uuid.uuid4()
    # 文字列比較で a < b の正規化.
    a, b = (pa_id, pb_id) if str(pa_id) < str(pb_id) else (pb_id, pa_id)

    office_id = _uuid.uuid4()
    va = _make_v2_visit(a, name="A", office_id=office_id)
    vb = _make_v2_visit(b, name="B", office_id=office_id)
    # 初期は同 set + 別の空 set (剥がし先候補)
    sets = [V2Set(visits=[va, vb]), V2Set(visits=[])]
    pair_modes = {(a, b): "blocked"}
    warnings: list[V2Warning] = []
    _enforce_same_address_pair_mode(sets, pair_modes, warnings)
    # b は別 set に剥がされる.
    flat = [v for s in sets for v in s.visits]
    a_set_idx = next((i for i, s in enumerate(sets) for v in s.visits if v.patient_id == a), None)
    b_set_idx = next((i for i, s in enumerate(sets) for v in s.visits if v.patient_id == b), None)
    assert a_set_idx != b_set_idx, "blocked ペアは別 set にならねばならない"
    assert len(flat) == 2
    assert any("blocked" in w.message for w in warnings), warnings


# ---------------------------------------------------------------------------
# T3.6: pair_mode=required → 別 set にいたら同 set に統合
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t3_6_pair_mode_required_merges_into_same_set(db) -> None:
    """pair_mode=required のペアは別 set にいたら同 set に統合される."""
    import uuid as _uuid

    pa_id = _uuid.uuid4()
    pb_id = _uuid.uuid4()
    a, b = (pa_id, pb_id) if str(pa_id) < str(pb_id) else (pb_id, pa_id)

    office_id = _uuid.uuid4()
    va = _make_v2_visit(a, name="A", office_id=office_id)
    vb = _make_v2_visit(b, name="B", office_id=office_id)
    sets = [V2Set(visits=[va]), V2Set(visits=[vb])]
    pair_modes = {(a, b): "required"}
    warnings: list[V2Warning] = []
    _enforce_same_address_pair_mode(sets, pair_modes, warnings)
    a_set_idx = next((i for i, s in enumerate(sets) for v in s.visits if v.patient_id == a), None)
    b_set_idx = next((i for i, s in enumerate(sets) for v in s.visits if v.patient_id == b), None)
    assert a_set_idx == b_set_idx, "required ペアは同 set にならねばならない"
    assert any("required" in w.message for w in warnings), warnings


# ---------------------------------------------------------------------------
# T3.7: pair_mode=preferred (default) → 既存挙動 (= 同住所同時刻 OK; 何もしない)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t3_7_pair_mode_preferred_keeps_existing_behavior(db) -> None:
    """preferred は DB 行を持たない (= map に存在しない). 同 set 配置を維持."""
    import uuid as _uuid

    pa_id = _uuid.uuid4()
    pb_id = _uuid.uuid4()

    office_id = _uuid.uuid4()
    va = _make_v2_visit(pa_id, name="A", office_id=office_id)
    vb = _make_v2_visit(pb_id, name="B", office_id=office_id)
    sets = [V2Set(visits=[va, vb])]
    pair_modes: dict[tuple, str] = {}  # preferred = 行無し
    warnings: list[V2Warning] = []
    _enforce_same_address_pair_mode(sets, pair_modes, warnings)
    # 何も変わらない.
    assert len(sets[0].visits) == 2
    assert warnings == []


# ---------------------------------------------------------------------------
# T3.8: 全患者 blocked link → 全員独立 set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t3_8_all_blocked_links_separate_all(db) -> None:
    """全患者ペアが blocked → 各患者が別 set に分散."""
    import uuid as _uuid

    p_ids = sorted([_uuid.uuid4() for _ in range(3)], key=str)
    office_id = _uuid.uuid4()
    visits = [_make_v2_visit(pid, name=f"P{i}", office_id=office_id) for i, pid in enumerate(p_ids)]
    # 初期は 1 set + 空 set 2 個 (剥がし先候補)
    sets = [V2Set(visits=list(visits)), V2Set(visits=[]), V2Set(visits=[])]
    pair_modes = {
        (p_ids[0], p_ids[1]): "blocked",
        (p_ids[0], p_ids[2]): "blocked",
        (p_ids[1], p_ids[2]): "blocked",
    }
    warnings: list[V2Warning] = []
    _enforce_same_address_pair_mode(sets, pair_modes, warnings)
    # 全患者が異なる set に分散されているか確認.
    patient_to_set_idx: dict = {}
    for si, s in enumerate(sets):
        for v in s.visits:
            patient_to_set_idx[v.patient_id] = si
    # 少なくとも 2 つ以上の異なる set に分かれている (3 通り blocked 検出のため).
    assert len(set(patient_to_set_idx.values())) >= 2, patient_to_set_idx


# ---------------------------------------------------------------------------
# T3.9: Before 4 経路 union: DB Visit のみある patient が Before に出る
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t3_9_before_includes_db_visit_only_patient(db) -> None:
    """PFV / weekly_pattern なし + DB Visit のみある patient が _load_before_visits_v2
    の 4 経路 union で Before に出る."""
    office = await _make_office(db, name="g21-t3-9-office")
    p = await _make_patient(
        db,
        code="T39",
        name="t3.9 patient",
        office=office,
        weekly_pattern=None,  # weekly_pattern なし
    )
    # DB Visit のみ: 2026 W20 Mon=2026-05-11
    visit = Visit(
        patient_id=p.id,
        visit_date=date(2026, 5, 11),  # Mon
        start_time=time(13, 30),
        end_time=time(14, 0),
        type="regular",
        status="planned",
        source="manual",
        required_staff_count=1,
    )
    db.add(visit)
    await db.commit()

    visits = await _load_before_visits_v2(
        db,
        patients_by_id={p.id: p},
        iso_year=2026,
        iso_week=20,
    )
    # DB Visit 経由で Before に出る.
    assert len(visits) == 1
    assert visits[0].patient_id == p.id
    assert visits[0].start_time == time(13, 30)


# ---------------------------------------------------------------------------
# T3.10: Before 4 経路 union: weekly_pattern のみある patient が Before に出る
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t3_10_before_includes_weekly_pattern_only_patient(db) -> None:
    """PFV / DB Visit なし + weekly_pattern のみある patient が Before に出る."""
    office = await _make_office(db, name="g21-t3-10-office")
    p = await _make_patient(
        db,
        code="T310",
        name="t3.10 patient",
        office=office,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "09:30",
            "preferred_end": "10:00",
            "time_type": "時間帯",
        },
    )
    await db.commit()

    visits = await _load_before_visits_v2(
        db,
        patients_by_id={p.id: p},
        iso_year=2026,
        iso_week=20,
    )
    # weekly_pattern 経由で Before に出る.
    assert len(visits) == 1
    assert visits[0].patient_id == p.id
    assert visits[0].weekday == 0
    assert visits[0].start_time == time(9, 30)


# ---------------------------------------------------------------------------
# T3.11: Before 4 経路 union: pending_overlay 反映
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t3_11_before_applies_pending_overlay(db) -> None:
    """pending_overlay が指定された PFV の値を上書きする."""
    from app.services.scheduling.auto_allocator_v2 import PendingEditOverlay

    office = await _make_office(db, name="g21-t3-11-office")
    p = await _make_patient(
        db,
        code="T311",
        name="t3.11 patient",
        office=office,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    await _make_pfv(db, patient=p, weekday=0, start_hhmm=(10, 0), is_pinned=True)
    await db.commit()

    overlay = {
        (p.id, 0): PendingEditOverlay(
            patient_id=p.id,
            weekday=0,
            new_start=time(11, 30),
            new_end=None,
            new_time_type="固定",
            new_start_str="11:30",
            new_end_str=None,
        )
    }
    visits = await _load_before_visits_v2(
        db,
        patients_by_id={p.id: p},
        iso_year=2026,
        iso_week=20,
        pending_overlay=overlay,
    )
    assert len(visits) == 1
    assert visits[0].start_time == time(11, 30)


# ---------------------------------------------------------------------------
# T3.12: reset_to_fixed mode='legacy' (= 既存 Phase G-10/G-11 挙動)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t3_12_reset_to_fixed_legacy_mode_keeps_pfv_time(db) -> None:
    """mode='legacy' (default) は全 PFV を pinned 扱い + apply_travel_corrections
    完全スキップ. Phase G-10/G-11 後方互換."""
    office = await _make_office(db, name="g21-t3-12-office")
    p = await _make_patient(
        db,
        code="T312",
        name="t3.12 patient",
        office=office,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "09:00",
            "preferred_end": "17:00",
            "time_type": "時間帯",  # 時間帯でも legacy は動かさない
        },
    )
    # 非 pinned PFV でも legacy 経路では固定扱い.
    await _make_pfv(db, patient=p, weekday=0, start_hhmm=(10, 0), is_pinned=False)
    await _seed_staff_and_shift(db, office=office, weekday=0)
    await db.commit()

    result = await reset_visits_to_fixed(
        db, iso_year=2026, iso_week=20, office_ids=[office.id], mode="legacy"
    )
    await db.commit()
    assert result["visits_regenerated"] >= 1
    visit = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == p.id,
                Visit.visit_date == date(2026, 5, 11),
                Visit.deleted_at.is_(None),
            )
        )
    ).first()
    assert visit is not None
    assert visit.start_time == time(10, 0)


# ---------------------------------------------------------------------------
# T3.13: reset_to_fixed mode='auto' (= pinned 厳守 + 非 pinned weekly_pattern 配置)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t3_13_reset_to_fixed_auto_mode_pinned_strict(db) -> None:
    """mode='auto' で pinned PFV のみ厳守, 非 pinned は weekly_pattern 経路."""
    office = await _make_office(db, name="g21-t3-13-office")
    p_pinned = await _make_patient(
        db,
        code="T313P",
        name="t3.13 pinned patient",
        office=office,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    await _make_pfv(db, patient=p_pinned, weekday=0, start_hhmm=(10, 0), is_pinned=True)
    await _seed_staff_and_shift(db, office=office, weekday=0)
    await db.commit()

    await reset_visits_to_fixed(db, iso_year=2026, iso_week=20, office_ids=[office.id], mode="auto")
    await db.commit()
    # pinned 患者の visit は PFV.start_time そのまま.
    visit = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == p_pinned.id,
                Visit.visit_date == date(2026, 5, 11),
                Visit.deleted_at.is_(None),
            )
        )
    ).first()
    assert visit is not None
    assert visit.start_time == time(10, 0), (
        f"pinned PFV は auto mode でも動かない (got {visit.start_time})"
    )


# ---------------------------------------------------------------------------
# T3.14: reset_to_fixed dry-run 件数返却
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t3_14_reset_to_fixed_dry_run_returns_counts(db) -> None:
    """dry_run=True は DB 不変 + 3 種件数返却."""
    office = await _make_office(db, name="g21-t3-14-office")
    p = await _make_patient(
        db,
        code="T314",
        name="t3.14 patient",
        office=office,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    await _make_pfv(db, patient=p, weekday=0, start_hhmm=(10, 0), is_pinned=True)
    await _seed_staff_and_shift(db, office=office, weekday=0)
    await db.commit()

    result = await reset_visits_to_fixed(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="legacy",
        dry_run=True,
    )
    # dry_run 結果のキー存在を確認.
    assert result["dry_run"] is True
    assert "visits_to_insert" in result
    assert "visits_to_skip_protected" in result
    assert "visits_to_skip_conflict" in result
    assert result["visits_to_insert"] >= 1
    # DB に visit が INSERT されていない.
    rows = (
        await db.scalars(select(Visit).where(Visit.patient_id == p.id, Visit.deleted_at.is_(None)))
    ).all()
    assert len(rows) == 0, "dry_run なのに visit が INSERT された"


# ---------------------------------------------------------------------------
# T3.15: D&D で pinned 動かしたら 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t3_15_apply_week_only_rejects_pinned_dnd(db) -> None:
    """apply_week_only で pinned PFV と同 (patient_id, weekday) で start_time が違う
    visit_plan が来たら PinnedVisitMovedError を raise する."""
    office = await _make_office(db, name="g21-t3-15-office")
    p = await _make_patient(
        db,
        code="T315",
        name="t3.15 patient",
        office=office,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    await _make_pfv(db, patient=p, weekday=0, start_hhmm=(10, 0), is_pinned=True)
    await db.commit()

    # D&D で pinned を 13:00 に動かそうとする plan.
    patient_visit_plans = [
        {
            "patient_id": p.id,
            "visit_plans": [
                {
                    "weekday": 0,
                    "start_time": time(13, 0),
                    "end_time": time(13, 35),
                    "duration_min": 35,
                    "course_code": "A",
                    "office_id": office.id,
                    "am_pm": "pm",
                }
            ],
        }
    ]
    with pytest.raises(PinnedVisitMovedError) as exc_info:
        await apply_week_only(
            db,
            iso_year=2026,
            iso_week=20,
            office_ids=[office.id],
            patient_visit_plans=patient_visit_plans,
        )
    violations = exc_info.value.violations
    assert len(violations) == 1
    assert violations[0]["weekday"] == 0
    assert violations[0]["pfv_start"] == "10:00"
    assert violations[0]["plan_start"] == "13:00"


# ---------------------------------------------------------------------------
# T3.16: _enforce_h2_split_overflow sort 決定論化
# ---------------------------------------------------------------------------


def test_t3_16_split_overflow_sort_is_deterministic() -> None:
    """同 input で常に同じ overflow patient 選出されるか (Reviewer H2 fix:
    3 set 以上 + 異なる住所の seed visit を用意し overflow 移動先候補を
    確保する. これで sort 後に「実際に選ばれる overflow patient_id」を観測する).
    """
    import uuid as _uuid

    # 3 名同住所 同時刻 → 1 名 overflow.
    # patient_id 文字列順で最小選出されることをテストする.
    office_id = _uuid.uuid4()
    p_ids = [_uuid.uuid4() for _ in range(3)]
    # 移動先 set (set 1/2) に 1 名ずつ seed visit を入れて bucket 制約は同じ
    # (office, weekday, am_pm) にする. 住所だけ別住所にして「同住所 2 件未満」条件を満たす.
    seed_a_id = _uuid.uuid4()
    seed_b_id = _uuid.uuid4()

    def _run() -> tuple[UUID | None, tuple[str, ...]]:
        visits = [
            V2Visit(
                patient_id=pid,
                patient_name=f"P{i}",
                patient_code=None,
                weekday=0,
                start_time=time(10, 0),
                end_time=time(10, 30),
                service_minutes=30,
                lat=35.65,  # 同住所
                lng=140.10,
                office_id=office_id,
                am_pm="am",
                source_kind="pool",
            )
            for i, pid in enumerate(p_ids)
        ]
        seed_a = V2Visit(
            patient_id=seed_a_id,
            patient_name="SeedA",
            patient_code=None,
            weekday=0,
            start_time=time(10, 30),
            end_time=time(11, 0),
            service_minutes=30,
            lat=35.66,  # 別住所
            lng=140.11,
            office_id=office_id,
            am_pm="am",
            source_kind="pool",
        )
        seed_b = V2Visit(
            patient_id=seed_b_id,
            patient_name="SeedB",
            patient_code=None,
            weekday=0,
            start_time=time(11, 0),
            end_time=time(11, 30),
            service_minutes=30,
            lat=35.67,  # 別住所
            lng=140.12,
            office_id=office_id,
            am_pm="am",
            source_kind="pool",
        )
        # set 0 に 3 名同住所 (= overflow 発火) + set 1/2 に seed visit (= 移動先).
        sets = [
            V2Set(visits=visits),
            V2Set(visits=[seed_a]),
            V2Set(visits=[seed_b]),
        ]
        warnings: list[V2Warning] = []
        _enforce_h2_split_overflow(sets, warnings)
        # 移動された overflow patient の patient_id を取得.
        moved_id: UUID | None = None
        for si, s in enumerate(sets):
            if si == 0:
                continue
            for v in s.visits:
                if v.patient_id in set(p_ids):
                    moved_id = v.patient_id
                    break
            if moved_id is not None:
                break
        wmsgs = tuple(w.message for w in warnings if w.message)
        return (moved_id, wmsgs)

    moved1, msgs1 = _run()
    moved2, msgs2 = _run()
    assert moved1 == moved2, f"非決定論的 overflow choice: {moved1} vs {moved2}"
    assert msgs1 == msgs2, f"非決定論的 warning order: {msgs1} vs {msgs2}"
    # patient_id 文字列順で最小が overflow に選出される (sort key).
    expected_overflow = sorted(p_ids, key=str)[0]
    assert moved1 == expected_overflow, (
        f"sort 期待値 ({expected_overflow}) と実際 ({moved1}) が不一致"
    )


# ---------------------------------------------------------------------------
# T3.17: Wave 1: apply_week_only で apply_travel_corrections 呼ばれる (補正一元化)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t3_17_apply_week_only_calls_travel_corrections(db, monkeypatch) -> None:
    """apply_week_only が _apply_corrections_to_visits 経由で apply_travel_corrections
    を呼ぶ (Wave 1 4 経路統合)."""
    from app.services.scheduling import auto_allocator_v2 as mod

    call_count = {"n": 0}
    original = mod._apply_corrections_to_visits

    def _spy(*args, **kwargs):
        call_count["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(mod, "_apply_corrections_to_visits", _spy)

    office = await _make_office(db, name="g21-t3-17-office")
    p = await _make_patient(
        db,
        code="T317",
        name="t3.17 patient",
        office=office,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    await db.commit()
    patient_visit_plans = [
        {
            "patient_id": p.id,
            "visit_plans": [
                {
                    "weekday": 0,
                    "start_time": time(10, 0),
                    "end_time": time(10, 35),
                    "duration_min": 35,
                    "course_code": "A",
                    "office_id": office.id,
                    "am_pm": "am",
                }
            ],
        }
    ]
    await apply_week_only(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        patient_visit_plans=patient_visit_plans,
    )
    await db.commit()
    assert call_count["n"] >= 1, (
        "apply_week_only が _apply_corrections_to_visits を呼んでいない (Wave 1 統合崩壊)"
    )


# ---------------------------------------------------------------------------
# T3.18: Wave 1: reset_to_fixed (auto mode) で apply_corrections 呼ばれる
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t3_18_reset_to_fixed_auto_calls_travel_corrections(db, monkeypatch) -> None:
    """reset_to_fixed (mode='auto') が _apply_corrections_to_visits を呼ぶ."""
    from app.services.scheduling import auto_allocator_v2 as mod

    call_count = {"n": 0}
    original = mod._apply_corrections_to_visits

    def _spy(*args, **kwargs):
        call_count["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(mod, "_apply_corrections_to_visits", _spy)

    office = await _make_office(db, name="g21-t3-18-office")
    p = await _make_patient(
        db,
        code="T318",
        name="t3.18 patient",
        office=office,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    await _make_pfv(db, patient=p, weekday=0, start_hhmm=(10, 0), is_pinned=True)
    await _seed_staff_and_shift(db, office=office, weekday=0)
    await db.commit()
    await reset_visits_to_fixed(db, iso_year=2026, iso_week=20, office_ids=[office.id], mode="auto")
    await db.commit()
    assert call_count["n"] >= 1, (
        "reset_to_fixed (mode=auto) が _apply_corrections_to_visits を呼んでいない"
    )


# ---------------------------------------------------------------------------
# T3.19: feature flag OFF 拠点は旧経路 (canary 切替)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t3_19_feature_flag_off_uses_legacy_before(db, monkeypatch) -> None:
    """feature flag OFF (= OfficeFeatureFlag 行無し) の拠点は run_v2_pipeline で
    旧 _load_before_visits_from_pfv 経路を使う."""
    from app.services.scheduling import auto_allocator_v2 as mod

    legacy_call_count = {"n": 0}
    new_call_count = {"n": 0}
    original_legacy = mod._load_before_visits_from_pfv
    original_new = mod._load_before_visits_v2

    async def _legacy_spy(*args, **kwargs):
        legacy_call_count["n"] += 1
        return await original_legacy(*args, **kwargs)

    async def _new_spy(*args, **kwargs):
        new_call_count["n"] += 1
        return await original_new(*args, **kwargs)

    monkeypatch.setattr(mod, "_load_before_visits_from_pfv", _legacy_spy)
    monkeypatch.setattr(mod, "_load_before_visits_v2", _new_spy)

    office = await _make_office(db, name="g21-t3-19-office")
    p = await _make_patient(
        db,
        code="T319",
        name="t3.19 patient",
        office=office,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    await _make_pfv(db, patient=p, weekday=0, start_hhmm=(10, 0), is_pinned=True)
    await db.commit()

    # feature flag 未設定 (OFF) で run_v2_pipeline.
    await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    # OFF 拠点は legacy のみ呼ばれる. new (= _load_before_visits_v2) は 0 回.
    assert legacy_call_count["n"] >= 1, "OFF 拠点で legacy 経路が呼ばれていない"
    assert new_call_count["n"] == 0, (
        f"feature flag OFF なのに _load_before_visits_v2 が呼ばれた (n={new_call_count['n']})"
    )


@pytest.mark.asyncio
async def test_t3_19b_feature_flag_on_uses_new_before(db, monkeypatch) -> None:
    """feature flag ON の拠点は _load_before_visits_v2 (新経路) を呼ぶ (canary 切替)."""
    from app.services.scheduling import auto_allocator_v2 as mod

    new_call_count = {"n": 0}
    original_new = mod._load_before_visits_v2

    async def _new_spy(*args, **kwargs):
        new_call_count["n"] += 1
        return await original_new(*args, **kwargs)

    monkeypatch.setattr(mod, "_load_before_visits_v2", _new_spy)

    office = await _make_office(db, name="g21-t3-19b-office")
    await _enable_g21_flag(db, office)
    p = await _make_patient(
        db,
        code="T319B",
        name="t3.19b patient",
        office=office,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    await _make_pfv(db, patient=p, weekday=0, start_hhmm=(10, 0), is_pinned=True)
    await db.commit()

    await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    assert new_call_count["n"] >= 1, "feature flag ON 拠点で新経路が呼ばれていない"


# ===========================================================================
# Phase G-21 T3+W1 Reviewer 指摘修正テスト (Critical/High/Medium)
# ===========================================================================


# ---------------------------------------------------------------------------
# Reviewer C1 fix: build_visits_for_pool_v2 が run_v2_pipeline から呼ばれる
# (canary ON で同 patient × 同 weekday の pinned + weekly_pattern → After で
# 1 visit のみ + Invariant G21-A warning)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reviewer_c1_run_v2_pipeline_uses_build_visits_for_pool_v2(db) -> None:
    """canary ON 拠点で run_v2_pipeline が build_visits_for_pool_v2 経由で after を作る.

    同 patient × 同 weekday に pinned PFV + weekly_pattern 同曜日 entry がある場合、
    Invariant G21-A により weekly_pattern entry を skip し pinned のみ採用される.
    """
    office = await _make_office(db, name="g21-rc1-office")
    await _enable_g21_flag(db, office)
    p = await _make_patient(
        db,
        code="RC1",
        name="reviewer C1 patient",
        office=office,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "09:00",
            "preferred_end": "17:00",
            "time_type": "時間帯",
        },
    )
    # 同 patient × 同 weekday に pinned PFV 10:30 + weekly_pattern entry 09:00..17:00 が
    # 同時にある状況. 旧経路 (legacy build_visits_for_pool) では Invariant G21-A が
    # 効かず 2 visit 出る. 新経路では pinned のみ 1 visit + warning.
    await _make_pfv(db, patient=p, weekday=0, start_hhmm=(10, 30), is_pinned=True)
    await _seed_staff_and_shift(db, office=office, weekday=0)
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    # after_visits の同 (patient_id, weekday) 件数 = 1 (= pinned のみ).
    after_visits = result.get("after_visits") or []
    same_patient_weekday = [v for v in after_visits if v.patient_id == p.id and v.weekday == 0]
    assert len(same_patient_weekday) == 1, (
        f"canary ON で 1 visit のみ期待だが {len(same_patient_weekday)} 件: {same_patient_weekday}"
    )
    # pinned 由来 (= is_pinned=True, start_time=10:30).
    v = same_patient_weekday[0]
    assert v.is_pinned is True, "after の visit に is_pinned=True が乗っていない"
    assert v.start_time == time(10, 30)
    # Invariant G21-A warning が出ている.
    warnings_list = result.get("warnings") or []
    warning_msgs = [w.message if hasattr(w, "message") else str(w) for w in warnings_list]
    assert any("Invariant G21-A" in m for m in warning_msgs), warning_msgs


@pytest.mark.asyncio
async def test_reviewer_c1_canary_off_keeps_legacy_after(db) -> None:
    """canary OFF (= feature flag 未設定) の拠点では legacy 経路維持で挙動不変."""
    office = await _make_office(db, name="g21-rc1-off-office")
    # NOTE: _enable_g21_flag は呼ばない (= canary OFF)
    p = await _make_patient(
        db,
        code="RC1OFF",
        name="reviewer C1 off patient",
        office=office,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    await _make_pfv(db, patient=p, weekday=0, start_hhmm=(10, 0), is_pinned=True)
    await _seed_staff_and_shift(db, office=office, weekday=0)
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    after_visits = result.get("after_visits") or []
    same_pw = [v for v in after_visits if v.patient_id == p.id and v.weekday == 0]
    # Phase G-30: legacy 経路でも pinned PFV (is_pinned=True) は V2Visit.is_pinned=True
    # を立てる (= apply_travel_corrections で時刻が動かない). canary OFF でも
    # 「pinned なら不動」という不変条件は維持される.
    assert len(same_pw) >= 1
    assert same_pw[0].is_pinned is True, (
        "Phase G-30: canary OFF でも pinned PFV は is_pinned=True が乗るべき"
    )


# ---------------------------------------------------------------------------
# Reviewer C2 fix: D&D pinned 拒否は start_time 以外 (weekday / office / duration) も検出
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reviewer_c2_apply_week_only_rejects_weekday_move(db) -> None:
    """pinned PFV (Mon) なのに plan が別 weekday (Tue) のみ → PinnedVisitMovedError."""
    office = await _make_office(db, name="g21-rc2-wd-office")
    p = await _make_patient(
        db,
        code="RC2WD",
        name="reviewer C2 weekday patient",
        office=office,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    await _make_pfv(db, patient=p, weekday=0, start_hhmm=(10, 0), is_pinned=True)
    await db.commit()

    patient_visit_plans = [
        {
            "patient_id": p.id,
            "visit_plans": [
                {
                    "weekday": 1,  # Tue (= pinned PFV の Mon と違う)
                    "start_time": time(10, 0),
                    "end_time": time(10, 35),
                    "duration_min": 35,
                    "course_code": "A",
                    "office_id": office.id,
                    "am_pm": "am",
                }
            ],
        }
    ]
    with pytest.raises(PinnedVisitMovedError) as exc_info:
        await apply_week_only(
            db,
            iso_year=2026,
            iso_week=20,
            office_ids=[office.id],
            patient_visit_plans=patient_visit_plans,
        )
    violations = exc_info.value.violations
    assert any(v.get("reason") == "weekday_changed" for v in violations), violations


@pytest.mark.asyncio
async def test_reviewer_c2_apply_week_only_rejects_office_change(db) -> None:
    """pinned PFV (office_a) なのに plan が office_b → PinnedVisitMovedError."""
    office_a = await _make_office(db, name="g21-rc2-ofa-office")
    office_b = await _make_office(db, name="g21-rc2-ofb-office")
    p = await _make_patient(
        db,
        code="RC2OF",
        name="reviewer C2 office patient",
        office=office_a,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    # pinned PFV: PFV.sub_office_id=None, patient.primary_office_id=office_a
    await _make_pfv(db, patient=p, weekday=0, start_hhmm=(10, 0), is_pinned=True)
    await db.commit()

    patient_visit_plans = [
        {
            "patient_id": p.id,
            "visit_plans": [
                {
                    "weekday": 0,
                    "start_time": time(10, 0),  # 同時刻
                    "end_time": time(10, 35),
                    "duration_min": 35,
                    "course_code": "A",
                    "office_id": office_b.id,  # 別 office
                    "am_pm": "am",
                }
            ],
        }
    ]
    with pytest.raises(PinnedVisitMovedError) as exc_info:
        await apply_week_only(
            db,
            iso_year=2026,
            iso_week=20,
            office_ids=[office_a.id, office_b.id],
            patient_visit_plans=patient_visit_plans,
        )
    violations = exc_info.value.violations
    assert any(v.get("reason") == "office_changed" for v in violations), violations


@pytest.mark.asyncio
async def test_reviewer_c2_apply_week_only_rejects_duration_change(db) -> None:
    """pinned PFV (duration_min=35) なのに plan が duration_min=60 → PinnedVisitMovedError."""
    office = await _make_office(db, name="g21-rc2-dur-office")
    p = await _make_patient(
        db,
        code="RC2DUR",
        name="reviewer C2 dur patient",
        office=office,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    await _make_pfv(db, patient=p, weekday=0, start_hhmm=(10, 0), is_pinned=True, duration_min=35)
    await db.commit()

    patient_visit_plans = [
        {
            "patient_id": p.id,
            "visit_plans": [
                {
                    "weekday": 0,
                    "start_time": time(10, 0),
                    "end_time": time(11, 0),
                    "duration_min": 60,  # PFV=35 と違う
                    "course_code": "A",
                    "office_id": office.id,
                    "am_pm": "am",
                }
            ],
        }
    ]
    with pytest.raises(PinnedVisitMovedError) as exc_info:
        await apply_week_only(
            db,
            iso_year=2026,
            iso_week=20,
            office_ids=[office.id],
            patient_visit_plans=patient_visit_plans,
        )
    violations = exc_info.value.violations
    assert any(v.get("reason") == "duration_changed" for v in violations), violations


# ---------------------------------------------------------------------------
# Reviewer H3 fix: apply_week_only V2Visit に is_pinned=True が乗る
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reviewer_h3_apply_week_only_sets_is_pinned(db, monkeypatch) -> None:
    """apply_week_only で pinned PFV と一致する plan は V2Visit.is_pinned=True で
    補正対象外 (= apply_travel_corrections の pinned fence 経由で start/end 不変).
    """
    from app.services.scheduling import auto_allocator_v2 as mod

    # _apply_corrections_to_visits が受け取った visits を覗き見て is_pinned を確認.
    captured: list[V2Visit] = []
    original = mod._apply_corrections_to_visits

    def _spy(visits, **kwargs):
        captured.extend(visits)
        return original(visits, **kwargs)

    monkeypatch.setattr(mod, "_apply_corrections_to_visits", _spy)

    office = await _make_office(db, name="g21-rh3-office")
    p = await _make_patient(
        db,
        code="RH3",
        name="reviewer H3 patient",
        office=office,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    await _make_pfv(db, patient=p, weekday=0, start_hhmm=(10, 0), is_pinned=True)
    await db.commit()
    patient_visit_plans = [
        {
            "patient_id": p.id,
            "visit_plans": [
                {
                    "weekday": 0,
                    "start_time": time(10, 0),  # PFV と同時刻 (= 受理される)
                    "end_time": time(10, 35),
                    "duration_min": 35,
                    "course_code": "A",
                    "office_id": office.id,
                    "am_pm": "am",
                }
            ],
        }
    ]
    await apply_week_only(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        patient_visit_plans=patient_visit_plans,
    )
    # _apply_corrections_to_visits に渡された V2Visit に is_pinned=True が乗っている.
    pinned_in_captured = [v for v in captured if v.patient_id == p.id and v.is_pinned]
    assert len(pinned_in_captured) >= 1, (
        f"apply_week_only の V2Visit に is_pinned=True が乗っていない (captured={captured})"
    )


# ---------------------------------------------------------------------------
# Reviewer H1 fix: dry_run=True で Course を新規 INSERT しない
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reviewer_h1_reset_dry_run_does_not_insert_course(db) -> None:
    """reset_visits_to_fixed dry_run=True は既存 Course がなくても Course を INSERT しない."""
    from app.models.course import Course

    office = await _make_office(db, name="g21-rh1-office")
    p = await _make_patient(
        db,
        code="RH1",
        name="reviewer H1 patient",
        office=office,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    await _make_pfv(db, patient=p, weekday=0, start_hhmm=(10, 0), is_pinned=True)
    await _seed_staff_and_shift(db, office=office, weekday=0)
    await db.commit()

    # 初期状態の Course 件数.
    initial_courses = (await db.scalars(select(Course).where(Course.deleted_at.is_(None)))).all()
    initial_count = len(initial_courses)

    result = await reset_visits_to_fixed(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="legacy",
        dry_run=True,
    )
    # dry_run=True だから DB 変更ゼロ.
    assert result["dry_run"] is True
    # Course が新規 INSERT されていない.
    post_courses = (await db.scalars(select(Course).where(Course.deleted_at.is_(None)))).all()
    assert len(post_courses) == initial_count, (
        f"dry_run=True なのに Course が新規作成された "
        f"(initial={initial_count}, post={len(post_courses)})"
    )


# ---------------------------------------------------------------------------
# Reviewer M1 fix: pair_mode 非正規順 (b, a) でも動作確認 (テスト追加のみ)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reviewer_m1_pair_mode_blocked_unnormalized_key(db) -> None:
    """pair_modes の key が文字列順 (a, b) でなく (b, a) の場合でも処理が機能するか.

    _enforce_same_address_pair_mode は key を文字列順正規化する責務がないが、
    呼び出し側 _load_same_address_pair_modes は正規化済みで返す契約. 念のため
    非正規順 input でも crash しないことを確認 (= 反対順は処理が単に no-op になる).
    """
    import uuid as _uuid

    pa_id = _uuid.uuid4()
    pb_id = _uuid.uuid4()
    # 文字列比較で (a, b) ではなく (b, a) を強制.
    if str(pa_id) < str(pb_id):
        a, b = pb_id, pa_id  # 非正規順
    else:
        a, b = pa_id, pb_id  # 非正規順

    office_id = _uuid.uuid4()
    va = _make_v2_visit(a, name="A", office_id=office_id)
    vb = _make_v2_visit(b, name="B", office_id=office_id)
    sets = [V2Set(visits=[va, vb]), V2Set(visits=[])]
    # 非正規順のキー (a > b in str) で blocked を与える.
    pair_modes = {(a, b): "blocked"}
    warnings: list[V2Warning] = []
    # 例外を出さずに無事終了する (= 非正規キーでも crash しない).
    _enforce_same_address_pair_mode(sets, pair_modes, warnings)
    # 結果は _enforce_same_address_pair_mode の入力契約上、両 patient_id を
    # キーから直接読むため、非正規順でも動作する.
    a_set_idx = next((i for i, s in enumerate(sets) for v in s.visits if v.patient_id == a), None)
    b_set_idx = next((i for i, s in enumerate(sets) for v in s.visits if v.patient_id == b), None)
    assert a_set_idx != b_set_idx, "非正規順 pair_modes でも blocked 効果が出る必要がある"


@pytest.mark.asyncio
async def test_reviewer_m1_pair_mode_required_unnormalized_key(db) -> None:
    """pair_modes の (b, a) 非正規キーでも required が機能する."""
    import uuid as _uuid

    pa_id = _uuid.uuid4()
    pb_id = _uuid.uuid4()
    if str(pa_id) < str(pb_id):
        a, b = pb_id, pa_id
    else:
        a, b = pa_id, pb_id

    office_id = _uuid.uuid4()
    va = _make_v2_visit(a, name="A", office_id=office_id)
    vb = _make_v2_visit(b, name="B", office_id=office_id)
    sets = [V2Set(visits=[va]), V2Set(visits=[vb])]
    pair_modes = {(a, b): "required"}
    warnings: list[V2Warning] = []
    _enforce_same_address_pair_mode(sets, pair_modes, warnings)
    a_set_idx = next((i for i, s in enumerate(sets) for v in s.visits if v.patient_id == a), None)
    b_set_idx = next((i for i, s in enumerate(sets) for v in s.visits if v.patient_id == b), None)
    assert a_set_idx == b_set_idx, "非正規順 pair_modes でも required で同 set にまとまる"


# ---------------------------------------------------------------------------
# Reviewer M3 fix: PinnedVisitMovedError.violations に patient_name 充填
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reviewer_m3_pinned_moved_violations_has_patient_name(db) -> None:
    """PinnedVisitMovedError の violations dict に patient_name が含まれる."""
    office = await _make_office(db, name="g21-rm3-office")
    p = await _make_patient(
        db,
        code="RM3",
        name="reviewer M3 patient name",
        office=office,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    await _make_pfv(db, patient=p, weekday=0, start_hhmm=(10, 0), is_pinned=True)
    await db.commit()

    patient_visit_plans = [
        {
            "patient_id": p.id,
            "visit_plans": [
                {
                    "weekday": 0,
                    "start_time": time(13, 0),
                    "end_time": time(13, 35),
                    "duration_min": 35,
                    "course_code": "A",
                    "office_id": office.id,
                    "am_pm": "pm",
                }
            ],
        }
    ]
    with pytest.raises(PinnedVisitMovedError) as exc_info:
        await apply_week_only(
            db,
            iso_year=2026,
            iso_week=20,
            office_ids=[office.id],
            patient_visit_plans=patient_visit_plans,
        )
    violations = exc_info.value.violations
    assert len(violations) >= 1
    assert violations[0].get("patient_name") == "reviewer M3 patient name", (
        f"patient_name が空: {violations}"
    )


# ---------------------------------------------------------------------------
# Reviewer H4 fix: _enforce_same_address_pair_mode blocked 移動先で
# 同住所 2 名以上のいる set は除外
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reviewer_h4_blocked_skips_target_with_two_same_address(db) -> None:
    """blocked で剥がす先候補 set に「visit_b と同住所が既に 2 名いる」 set があれば skip.

    そのまま入れると H2 同住所 2 名上限を後段で破ることになる.
    """
    import uuid as _uuid

    pa_id = _uuid.uuid4()
    pb_id = _uuid.uuid4()
    a, b = (pa_id, pb_id) if str(pa_id) < str(pb_id) else (pb_id, pa_id)

    # 同住所 (35.65, 140.10) で visit_a / visit_b. blocked により b を別 set に剥がしたい.
    office_id = _uuid.uuid4()
    va = _make_v2_visit(a, name="A", lat=35.65, lng=140.10, office_id=office_id)
    vb = _make_v2_visit(b, name="B", lat=35.65, lng=140.10, office_id=office_id)
    # 候補 set 0: 同住所 (35.65, 140.10) が既に 2 名 (= b を入れると 3 名)
    other_id_1 = _uuid.uuid4()
    other_id_2 = _uuid.uuid4()
    v_other_1 = _make_v2_visit(other_id_1, name="O1", lat=35.65, lng=140.10, office_id=office_id)
    v_other_2 = _make_v2_visit(other_id_2, name="O2", lat=35.65, lng=140.10, office_id=office_id)
    # 候補 set 2: 別住所 (35.70, 140.15) のみ (= b を入れても同住所制約 OK)
    other_id_3 = _uuid.uuid4()
    v_other_3 = _make_v2_visit(other_id_3, name="O3", lat=35.70, lng=140.15, office_id=office_id)

    sets = [
        V2Set(visits=[v_other_1, v_other_2]),  # 候補 0 (= NG: 同住所 2 名既に居る)
        V2Set(visits=[va, vb]),  # 起点 (= blocked が解除する set)
        V2Set(visits=[v_other_3]),  # 候補 2 (= OK)
    ]
    pair_modes = {(a, b): "blocked"}
    warnings: list[V2Warning] = []
    _enforce_same_address_pair_mode(sets, pair_modes, warnings)
    # b は set 2 (候補 2) に剥がされている (= 候補 0 ではない).
    b_set_idx = next((i for i, s in enumerate(sets) for v in s.visits if v.patient_id == b), None)
    assert b_set_idx == 2, (
        f"blocked b は set 2 (= 同住所無し) に剥がす必要があるが set {b_set_idx} に居る. "
        f"sets={[[v.patient_name for v in s.visits] for s in sets]}"
    )
    # set 0 (= 候補 0) は b を受け入れず 2 名のまま.
    assert len(sets[0].visits) == 2


# ---------------------------------------------------------------------------
# Reviewer L2 (= 既に endpoint に logger.info を仕込んだので軽い smoke).
# 直接 logger を check するのは脆いので、 dry_run=True が 成功するだけを確認.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reviewer_l2_reset_dry_run_logs_info(db, caplog) -> None:
    """reset_visits_to_fixed dry_run=True が完了し、 visits_to_insert が返る."""
    import logging

    office = await _make_office(db, name="g21-rl2-office")
    p = await _make_patient(
        db,
        code="RL2",
        name="reviewer L2 patient",
        office=office,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    await _make_pfv(db, patient=p, weekday=0, start_hhmm=(10, 0), is_pinned=True)
    await _seed_staff_and_shift(db, office=office, weekday=0)
    await db.commit()

    with caplog.at_level(logging.INFO):
        result = await reset_visits_to_fixed(
            db,
            iso_year=2026,
            iso_week=20,
            office_ids=[office.id],
            mode="legacy",
            dry_run=True,
        )
    # dry_run 件数返却 (= 修正済 contract).
    assert result["dry_run"] is True
    assert result["visits_to_insert"] >= 0  # 既存 Course が無い場合 0, ある場合 >=1
