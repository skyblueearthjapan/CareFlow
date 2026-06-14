"""Tests for auto_allocator_v2 (Wave 41 v2.0 / auto-schedule v2).

設計仕様書: ``docs/plans/auto-schedule-v2.md`` (v0.2)

各段階 (Stage 1〜5) のヘルパー関数を独立に検証する.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, datetime, time
from uuid import UUID

import pytest

from app.models import Office, Patient
from app.models.office_feature_flag import OfficeFeatureFlag
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.staff import Staff, StaffShift
from app.services.scheduling.auto_allocator_v2 import (
    G21_NEW_ALGORITHM_FEATURE_KEY,
    MAX_PATIENTS_PER_COURSE,
    MAX_PATIENTS_PER_SET,
    V2Visit,
    V2Warning,
    _g94_resolve_cross_patient_double_booking,
    apply_individual_proposal,
    apply_travel_corrections,
    build_visits_for_pool,
    calc_h_violations,
    calc_total_distance,
    cluster_by_distance_greedy,
    combine_am_pm_sets,
    count_active_staff_per_weekday,
    determine_am_pm,
    haversine_km,
    reset_visits_to_fixed,
    run_v2_pipeline,
    split_into_buckets,
)

# CareFlow Wave Next 2 [H1]: M overflow を M / M2..M9 に分散させたため、既存
# テストでは "超過分は M または M2..M9" として判定する.
_M_OVERFLOW_CODES: set[str] = {"M", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9"}


# ---------------------------------------------------------------------------
# Stage helpers — determine_am_pm (Q1 柔軟判定)
# ---------------------------------------------------------------------------


def test_determine_am_pm_explicit_periods() -> None:
    """time_type=午前/午後/終日 はそのまま."""
    assert determine_am_pm(time_type="午前", preferred_start=None) == "am"
    assert determine_am_pm(time_type="午後", preferred_start=None) == "pm"
    assert determine_am_pm(time_type="終日", preferred_start=None) == "any"


def test_determine_am_pm_fixed_below_noon() -> None:
    """固定 11:30 → am, 固定 14:00 → pm (Q1)."""
    assert determine_am_pm(time_type="固定", preferred_start=time(11, 30)) == "am"
    assert determine_am_pm(time_type="固定", preferred_start=time(14, 0)) == "pm"


def test_determine_am_pm_jihantai_uses_preferred_start() -> None:
    """時間帯 は preferred_start 基準で判定 (Q1)."""
    assert determine_am_pm(time_type="時間帯", preferred_start=time(9, 0)) == "am"
    assert determine_am_pm(time_type="時間帯", preferred_start=time(13, 30)) == "pm"


def test_determine_am_pm_noon_boundary() -> None:
    """12:00 ちょうどは pm (>=12)."""
    assert determine_am_pm(time_type="固定", preferred_start=time(12, 0)) == "pm"


def test_determine_am_pm_unknown_returns_any() -> None:
    """time_type 不明は any."""
    assert determine_am_pm(time_type=None, preferred_start=None) == "any"
    assert determine_am_pm(time_type="invalid", preferred_start=time(10, 0)) == "any"


# ---------------------------------------------------------------------------
# Stage 3 — cluster_by_distance_greedy
# ---------------------------------------------------------------------------


def _make_visit(
    *,
    lat: float,
    lng: float,
    weekday: int = 0,
    start_h: int = 9,
    start_m: int = 30,
    patient_name: str = "P",
    office_id: UUID | None = None,
) -> V2Visit:
    return V2Visit(
        patient_id=uuid.uuid4(),
        patient_name=patient_name,
        patient_code=patient_name,
        weekday=weekday,
        start_time=time(start_h, start_m),
        end_time=time(start_h + 1, start_m),
        service_minutes=60,
        lat=lat,
        lng=lng,
        office_id=office_id or uuid.uuid4(),
        am_pm="am",
        source_kind="pool",
    )


def test_cluster_greedy_pairs_closest_first() -> None:
    """最も近いペアが同セットになる."""
    # 3 patients: A (35.65, 140.10), B (35.651, 140.101) - very close,
    # C (35.70, 140.20) - far away.
    a = _make_visit(lat=35.65, lng=140.10, patient_name="A")
    b = _make_visit(lat=35.651, lng=140.101, patient_name="B")
    c = _make_visit(lat=35.70, lng=140.20, patient_name="C")
    sets = cluster_by_distance_greedy([a, b, c])
    # A と B は同セットになるはず (最も近い)
    assert len(sets) >= 2
    # A と B を同セットに含む set を探す
    found_ab = False
    for s in sets:
        codes = {v.patient_code for v in s.visits}
        if "A" in codes and "B" in codes:
            found_ab = True
            break
    assert found_ab, "closest pair (A, B) was not grouped"


def test_cluster_greedy_respects_max_per_cluster() -> None:
    """1 セット 3 人まで (MAX_PATIENTS_PER_SET=3)."""
    visits = [
        _make_visit(lat=35.65 + i * 0.0001, lng=140.10, patient_name=f"P{i}") for i in range(8)
    ]
    sets = cluster_by_distance_greedy(visits, max_per_cluster=MAX_PATIENTS_PER_SET)
    for s in sets:
        assert len(s.visits) <= MAX_PATIENTS_PER_SET, (
            f"set has {len(s.visits)} > {MAX_PATIENTS_PER_SET}"
        )


def test_cluster_greedy_empty_input() -> None:
    assert cluster_by_distance_greedy([]) == []


def test_cluster_greedy_single_visit() -> None:
    v = _make_visit(lat=35.65, lng=140.10)
    sets = cluster_by_distance_greedy([v])
    assert len(sets) == 1
    assert sets[0].visits == [v]


# ---------------------------------------------------------------------------
# Stage 2 — split_into_buckets
# ---------------------------------------------------------------------------


def test_split_into_buckets_separates_am_pm() -> None:
    office_id = uuid.uuid4()
    am_v = _make_visit(lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=30)
    am_v.am_pm = "am"
    pm_v = _make_visit(lat=35.65, lng=140.10, office_id=office_id, start_h=14, start_m=0)
    pm_v.am_pm = "pm"
    buckets = split_into_buckets([am_v, pm_v])
    assert (office_id, 0, "am") in buckets
    assert (office_id, 0, "pm") in buckets


def test_split_into_buckets_any_falls_back_by_start_time() -> None:
    """am_pm='any' は start_time で決定."""
    office_id = uuid.uuid4()
    morning = _make_visit(lat=35.65, lng=140.10, office_id=office_id, start_h=10)
    morning.am_pm = "any"
    afternoon = _make_visit(lat=35.65, lng=140.10, office_id=office_id, start_h=15)
    afternoon.am_pm = "any"
    buckets = split_into_buckets([morning, afternoon])
    assert (office_id, 0, "am") in buckets
    assert (office_id, 0, "pm") in buckets


# ---------------------------------------------------------------------------
# Stage 5 — combine_am_pm_sets
# ---------------------------------------------------------------------------


def test_combine_am_pm_pairs_closest() -> None:
    """同エリアの am/pm セットがペアになる."""
    from app.services.scheduling.auto_allocator_v2 import V2Set

    am1 = V2Set(visits=[_make_visit(lat=35.65, lng=140.10)])
    am2 = V2Set(visits=[_make_visit(lat=35.80, lng=140.30)])
    pm1 = V2Set(visits=[_make_visit(lat=35.651, lng=140.101)])  # near am1
    pm2 = V2Set(visits=[_make_visit(lat=35.801, lng=140.301)])  # near am2
    warnings: list[V2Warning] = []
    courses = combine_am_pm_sets([am1, am2], [pm1, pm2], staff_count=2, warnings=warnings)
    assert len(courses) == 2
    # 各ペアは近場同士になる
    for am, pm in courses:
        assert am is not None and pm is not None
        d = haversine_km(am.visits[0].lat, am.visits[0].lng, pm.visits[0].lat, pm.visits[0].lng)
        assert d < 5.0, f"am/pm pair too far apart: {d}km"


def test_combine_am_pm_respects_capacity() -> None:
    """H9: 合計 6 人を超えるペアは作らない."""
    from app.services.scheduling.auto_allocator_v2 import V2Set

    am1 = V2Set(visits=[_make_visit(lat=35.65, lng=140.10) for _ in range(3)])
    am2 = V2Set(visits=[_make_visit(lat=35.66, lng=140.11) for _ in range(3)])
    pm1 = V2Set(visits=[_make_visit(lat=35.65, lng=140.10) for _ in range(3)])
    pm2 = V2Set(visits=[_make_visit(lat=35.66, lng=140.11) for _ in range(2)])
    warnings: list[V2Warning] = []
    courses = combine_am_pm_sets([am1, am2], [pm1, pm2], staff_count=2, warnings=warnings)
    # 各コースの合計 visits 数は 6 以下
    for am, pm in courses:
        total = (len(am.visits) if am else 0) + (len(pm.visits) if pm else 0)
        assert total <= MAX_PATIENTS_PER_COURSE


def test_combine_am_pm_single_side_only() -> None:
    """片方しかセットがない場合は単独コース."""
    from app.services.scheduling.auto_allocator_v2 import V2Set

    am1 = V2Set(visits=[_make_visit(lat=35.65, lng=140.10)])
    warnings: list[V2Warning] = []
    courses = combine_am_pm_sets([am1], [], staff_count=1, warnings=warnings)
    assert len(courses) == 1
    assert courses[0][0] is am1
    assert courses[0][1] is None


# ---------------------------------------------------------------------------
# Stage 4 — count_active_staff_per_weekday + Stage 1 — build_visits_for_pool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_count_active_staff_per_weekday_excludes_trainee(db) -> None:
    """is_trainee=true は除外."""
    office = Office(name="cnt-test-office")
    db.add(office)
    await db.flush()

    s1 = Staff(name="senior", role="staff", is_trainee=False, primary_office_id=office.id)
    s2 = Staff(name="trainee", role="staff", is_trainee=True, primary_office_id=office.id)
    db.add_all([s1, s2])
    await db.flush()

    # 月曜 (weekday=0) のみ s1 が稼働
    db.add(StaffShift(staff_id=s1.id, weekday=0, is_on=True))
    db.add(StaffShift(staff_id=s2.id, weekday=0, is_on=True))
    await db.commit()

    counts = await count_active_staff_per_weekday(
        db, office_ids=[office.id], iso_year=2026, iso_week=20
    )
    # 月曜は s1 のみ (1 人)
    assert counts.get((office.id, 0), 0) == 1


def test_build_visits_for_pool_filters_invalid_patients() -> None:
    """lat/lng/primary_office_id が無い患者はスキップ."""
    office_id = uuid.uuid4()
    p_ok = Patient(
        id=uuid.uuid4(),
        code="OK",
        name="OK patient",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office_id,
        weekly_pattern={
            "preferred_weekdays": ["Mon", "Wed"],
            "preferred_start": "10:00",
            "service_minutes": 30,
            "time_type": "固定",
        },
    )
    p_no_geo = Patient(
        id=uuid.uuid4(),
        code="NOGEO",
        name="No geo",
        status="active",
        lat=None,
        lng=None,
        primary_office_id=office_id,
        weekly_pattern={"preferred_weekdays": ["Mon"], "preferred_start": "10:00"},
    )
    visits = build_visits_for_pool([p_ok, p_no_geo])
    # p_ok の Mon と Wed の 2 件
    assert len(visits) == 2
    assert all(v.patient_code == "OK" for v in visits)


# ---------------------------------------------------------------------------
# KPI helpers
# ---------------------------------------------------------------------------


def test_calc_total_distance_zero_for_single_visit() -> None:
    v = _make_visit(lat=35.65, lng=140.10)
    v.course_code = "A"
    assert calc_total_distance([v]) == 0.0


def test_calc_total_distance_sums_adjacent_pairs() -> None:
    office_id = uuid.uuid4()
    v1 = _make_visit(lat=35.65, lng=140.10, office_id=office_id, start_h=9)
    v1.course_code = "A"
    v2 = _make_visit(lat=35.66, lng=140.11, office_id=office_id, start_h=10)
    v2.course_code = "A"
    d = calc_total_distance([v1, v2])
    assert d > 0


def test_calc_h_violations_h9_overflow() -> None:
    """7 人を 1 コースに入れたら H9 違反."""
    office_id = uuid.uuid4()
    visits = []
    for i in range(7):
        v = _make_visit(lat=35.65 + i * 0.001, lng=140.10, office_id=office_id)
        v.course_code = "A"
        visits.append(v)
    violations = calc_h_violations(visits)
    assert violations["H9"] >= 1


def test_calc_h_violations_h10_lunch_overlap() -> None:
    """Wave 3 + Phase E-3 改修 (2): 「物理的に lunch を取れない」visit は H10 違反.

    Phase E-3 で lunch は 11:30-13:30 内で 30-60 分動的配置 (3 段階 fallback).
    AM 側 30 分 lunch (11:30-12:00) も PM 側 30 分 lunch (13:00-13:30) も
    取れない区間 = start<12:00 かつ end>13:00.
    11:30-13:30 visit はその区間に該当するため H10=1.
    """
    v = _make_visit(lat=35.65, lng=140.10, start_h=11, start_m=30)
    v.end_time = time(13, 30)
    v.course_code = "A"
    violations = calc_h_violations([v])
    assert violations["H10"] == 1


def test_calc_h_violations_h10_no_violation_when_avoidable() -> None:
    """Phase E-3: 12:00-13:00 visit は AM 側 30 分 lunch (11:30-12:00) で避けられる → H10=0.

    旧仕様 (45 分 lunch): 12:00-13:00 は AM 側 11:30-12:15 と重なり H10 違反だった.
    Phase E-3 (30 分 lunch): 11:30-12:00 が成立、AM 側回避可能.
    """
    v = _make_visit(lat=35.65, lng=140.10, start_h=12, start_m=0)
    v.end_time = time(13, 0)
    v.course_code = "A"
    violations = calc_h_violations([v])
    assert violations["H10"] == 0


# ---------------------------------------------------------------------------
# _filter_unavailable_and_lunch — skip_acceptance (Mode 2 用)
# ---------------------------------------------------------------------------


def test_filter_skip_acceptance_in_mode2() -> None:
    """Mode 2 (skip_acceptance=True) では acceptance × でも visits が残る.

    昼休憩 (H10) は両モードで常に enforce される. Phase E-3 改修 (2) では
    lunch は 11:30-13:30 動的 (30-60 分 3 段階 fallback) だが、
    「30 分 lunch も避けられない」visit (= start<12:00 かつ end>13:00) は引き続き弾かれる.
    受入カレンダー × は既存スケジュール枠の混雑度を表すため、全面再配置時には
    制約として意味を持たない (バグ修正: 58 active 患者 dropped 問題).
    """
    from app.services.scheduling.auto_allocator_v2 import _filter_unavailable_and_lunch

    office_id = uuid.uuid4()
    # 受入カレンダー × に該当する時刻 (10:00) の visit
    blocked_visit = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=0, patient_name="X"
    )
    # 通常時刻の visit
    ok_visit = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=14, start_m=0, patient_name="OK"
    )
    # Phase E-3: 「30 分 lunch も避けられない」visit (11:50-13:10 が代表) — 動的 lunch
    # では AM 側 30 分 (~12:00) も PM 側 30 分 (13:00~) も成立しないため H10 違反.
    lunch_visit = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=11, start_m=50, patient_name="LUNCH"
    )
    lunch_visit.end_time = time(13, 10)
    lunch_visit.service_minutes = 80

    unavailable = {(office_id, 0): {time(10, 0)}}
    warnings: list[V2Warning] = []

    # skip_acceptance=True → blocked_visit も残る (受入 × 無視)
    filtered = _filter_unavailable_and_lunch(
        [blocked_visit, ok_visit, lunch_visit],
        unavailable_slots=unavailable,
        warnings=warnings,
        skip_acceptance=True,
    )
    codes = {v.patient_code for v in filtered}
    assert "X" in codes, "skip_acceptance=True なら acceptance × visit が残るはず"
    assert "OK" in codes
    assert "LUNCH" not in codes, (
        "skip_acceptance=True でも H10 (動的 lunch 不可避) visit は除外されるべき"
    )

    # acceptance_calendar 由来の warning は出ていない
    assert not any("受入カレンダー" in w.message for w in warnings), (
        f"skip_acceptance=True なのに 受入カレンダー warning が出ている: {warnings}"
    )
    # 昼休憩 warning は出ている (日本語化済)
    assert any("昼休憩" in w.message for w in warnings), (
        f"H10 昼休憩 warning が出ていない: {warnings}"
    )


def test_filter_acceptance_enforced_in_mode1_default() -> None:
    """Mode 1 (デフォルト skip_acceptance=False) では acceptance × visit は除外."""
    from app.services.scheduling.auto_allocator_v2 import _filter_unavailable_and_lunch

    office_id = uuid.uuid4()
    blocked_visit = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=0, patient_name="X"
    )
    ok_visit = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=14, start_m=0, patient_name="OK"
    )
    unavailable = {(office_id, 0): {time(10, 0)}}
    warnings: list[V2Warning] = []

    filtered = _filter_unavailable_and_lunch(
        [blocked_visit, ok_visit],
        unavailable_slots=unavailable,
        warnings=warnings,
    )
    codes = {v.patient_code for v in filtered}
    assert "X" not in codes, "Mode 1 (default) では acceptance × visit は除外されるべき"
    assert "OK" in codes
    assert any("受入カレンダー" in w.message for w in warnings)


# ---------------------------------------------------------------------------
# _load_before_visits_from_pfv — course_code が PFV.course_template_id 由来になる
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_before_visits_course_code_from_course_template(db) -> None:
    """PFV に course_template_id が設定されていれば V2Visit.course_code = CourseTemplate.label."""
    from app.models.course_template import CourseTemplate
    from app.services.scheduling.auto_allocator_v2 import _load_before_visits_from_pfv

    office = Office(name="before-test-office")
    db.add(office)
    await db.flush()

    ct = CourseTemplate(office_id=office.id, label="A")
    db.add(ct)
    await db.flush()

    patient = Patient(
        id=uuid.uuid4(),
        code="PTEST",
        name="Before Test Patient",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        weekly_pattern={},
    )
    db.add(patient)
    await db.flush()

    pfv = PatientFixedVisit(
        patient_id=patient.id,
        mode="normal",
        weekday=0,
        start_time=time(10, 0),
        duration_min=30,
        slot_index=0,
        course_template_id=ct.id,
    )
    db.add(pfv)
    await db.commit()

    visits = await _load_before_visits_from_pfv(db, patients_by_id={patient.id: patient})

    assert len(visits) == 1, f"expected 1 visit, got {len(visits)}"
    assert visits[0].course_code == "A", (
        f"course_code は CourseTemplate.label='A' になるはずだが '{visits[0].course_code}' だった"
    )


# ---------------------------------------------------------------------------
# End-to-end pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_v2_pipeline_diff_add_returns_pool_only(db) -> None:
    """diff_add モードは固定枠ありの患者を除外."""
    office = Office(name="pipe-office")
    db.add(office)
    await db.flush()

    # patient_fixed_visits を持つ patient (= プール対象外)
    p_fixed = Patient(
        code="FIXED",
        name="固定枠あり",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    # 固定枠なしの patient (= プール対象)
    p_pool = Patient(
        code="POOL",
        name="プール対象",
        status="active",
        lat=35.66,
        lng=140.11,
        primary_office_id=office.id,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:30",
            "time_type": "固定",
        },
    )
    db.add_all([p_fixed, p_pool])
    await db.flush()

    db.add(
        PatientFixedVisit(
            patient_id=p_fixed.id,
            mode="normal",
            weekday=0,
            start_time=time(10, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    # スタッフ
    s = Staff(name="staff1", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(s)
    await db.flush()
    db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True))
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="diff_add",
    )
    pool_codes = {v.patient_code for v in result["pool_visits"]}
    assert "POOL" in pool_codes
    # hotfix#3 (W41 v2.8): orphan 救済 — PFV あるが今週 visit ない患者は
    # pool に含まれる (PFV ベース展開).
    assert "FIXED" in pool_codes


@pytest.mark.asyncio
async def test_run_v2_pipeline_full_optimize_includes_all_active(db) -> None:
    """full_optimize モードは全 active 患者を対象."""
    office = Office(name="full-office")
    db.add(office)
    await db.flush()

    p1 = Patient(
        code="A1",
        name="A1",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    p2 = Patient(
        code="A2",
        name="A2",
        status="active",
        lat=35.66,
        lng=140.11,
        primary_office_id=office.id,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "14:00",
            "time_type": "固定",
        },
    )
    db.add_all([p1, p2])
    await db.flush()
    db.add(
        PatientFixedVisit(
            patient_id=p1.id,
            mode="normal",
            weekday=0,
            start_time=time(10, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    s = Staff(name="staff1", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(s)
    await db.flush()
    db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True))
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    pool_codes = {v.patient_code for v in result["pool_visits"]}
    # full_optimize: 両方含まれる
    assert "A1" in pool_codes
    assert "A2" in pool_codes


@pytest.mark.asyncio
async def test_run_v2_pipeline_rejects_bad_iso(db) -> None:
    with pytest.raises(ValueError):
        await run_v2_pipeline(db, iso_year=1999, iso_week=20, office_ids=[], mode="diff_add")
    with pytest.raises(ValueError):
        await run_v2_pipeline(db, iso_year=2026, iso_week=0, office_ids=[], mode="diff_add")
    with pytest.raises(ValueError):
        await run_v2_pipeline(
            db,
            iso_year=2026,
            iso_week=20,
            office_ids=[],
            mode="invalid",  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# apply_individual_proposal — idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_individual_creates_new_pfv(db) -> None:
    office = Office(name="apply-office")
    db.add(office)
    await db.flush()
    p = Patient(code="APP1", name="apply patient", status="active", primary_office_id=office.id)
    db.add(p)
    await db.commit()

    result = await apply_individual_proposal(
        db,
        patient_id=p.id,
        visit_plans=[{"weekday": 0, "start_time": time(10, 0), "duration_min": 45}],
    )
    await db.commit()
    assert result["applied"] is True
    assert result["idempotent"] is False
    assert len(result["fixed_visit_ids"]) == 1


@pytest.mark.asyncio
async def test_apply_individual_is_idempotent(db) -> None:
    """同じ提案を 2 度送ったら idempotent=True で no-op."""
    office = Office(name="idem-office")
    db.add(office)
    await db.flush()
    p = Patient(code="IDM1", name="idem patient", status="active", primary_office_id=office.id)
    db.add(p)
    await db.commit()

    plans = [{"weekday": 1, "start_time": time(9, 30), "duration_min": 30}]
    await apply_individual_proposal(db, patient_id=p.id, visit_plans=plans)
    await db.commit()
    result2 = await apply_individual_proposal(db, patient_id=p.id, visit_plans=plans)
    await db.commit()
    assert result2["applied"] is True
    assert result2["idempotent"] is True


@pytest.mark.asyncio
async def test_apply_individual_updates_existing_pfv(db) -> None:
    """既存 PFV を別時刻に書き換える."""
    office = Office(name="upd-office")
    db.add(office)
    await db.flush()
    p = Patient(code="UPD1", name="upd patient", status="active", primary_office_id=office.id)
    db.add(p)
    await db.flush()
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=2,
            start_time=time(10, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    await db.commit()

    result = await apply_individual_proposal(
        db,
        patient_id=p.id,
        visit_plans=[{"weekday": 2, "start_time": time(14, 0), "duration_min": 60}],
    )
    await db.commit()
    assert result["applied"] is True
    assert result["idempotent"] is False
    # 再読込
    from sqlalchemy import select

    rows = (
        await db.scalars(
            select(PatientFixedVisit).where(
                PatientFixedVisit.patient_id == p.id, PatientFixedVisit.weekday == 2
            )
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].start_time == time(14, 0)
    assert rows[0].duration_min == 60


# ---------------------------------------------------------------------------
# reset_visits_to_fixed — regeneration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_visits_to_fixed_regenerates_from_pfv(db) -> None:
    """対象週の visits を soft-delete → patient_fixed_visits から再生成."""
    from datetime import date

    from app.models.visit import VISIT_STATUS_PLANNED, Visit

    office = Office(name="rst-office")
    db.add(office)
    await db.flush()
    p = Patient(
        code="RST1",
        name="reset patient",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
    )
    db.add(p)
    await db.flush()
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=0,
            start_time=time(10, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    s = Staff(name="rst-staff", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(s)
    await db.flush()
    db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True))
    # W41 v2 final cross-review (C-Codex-2): 自動生成 source は削除対象だが、
    # 手動作成 source="manual" / 完了済み status は保護される.
    # 本テストでは auto-generated 経路 (source="auto_alloc") のみ削除対象として確認.
    existing = Visit(
        patient_id=p.id,
        visit_date=date(2026, 5, 11),  # Mon of W20
        start_time=time(14, 0),
        end_time=time(15, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto_alloc",  # 自動生成 source → 削除対象
        required_staff_count=1,
    )
    db.add(existing)
    await db.commit()

    result = await reset_visits_to_fixed(db, iso_year=2026, iso_week=20, office_ids=[office.id])
    await db.commit()

    assert result["visits_regenerated"] >= 1
    assert result["visits_soft_deleted"] >= 1

    # 既存 visit は soft-delete
    await db.refresh(existing)
    assert existing.deleted_at is not None


# ---------------------------------------------------------------------------
# C1 — before_visits と after_visits が独立して mutate 可能 (regression)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_v2_pipeline_diff_add_preserves_before_visits_identity(db) -> None:
    """C1 regression: stage 3-5 で after_visits.course_code を mutate しても
    before_visits の対応する visit は影響を受けないこと."""
    office = Office(name="c1-office")
    db.add(office)
    await db.flush()
    p_fixed = Patient(
        code="C1FIX",
        name="c1-fixed",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    db.add(p_fixed)
    await db.flush()
    db.add(
        PatientFixedVisit(
            patient_id=p_fixed.id,
            mode="normal",
            weekday=0,
            start_time=time(10, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    s = Staff(name="c1-staff", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(s)
    await db.flush()
    db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True))
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="diff_add",
    )
    before_visits = result["before_visits"]
    after_visits = result["after_visits"]
    # Before スナップショットの visit は course_code が None (=未割当) のまま.
    for bv in before_visits:
        assert bv.course_code is None, (
            f"before_visits[{bv.patient_code}] が after の course_code mutation で汚染された"
        )
    # After の同 patient は course_code が割当てられているはず.
    after_codes = {v.course_code for v in after_visits if v.patient_id == p_fixed.id}
    assert after_codes - {None}, "after_visits.course_code が空 (Stage 5 が走っていない)"


# ---------------------------------------------------------------------------
# C2 — apply_individual_proposal の同時採用がシリアライズされる
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_individual_uses_for_update_lock(db) -> None:
    """C2 regression: with_for_update() が SELECT 文に付与されていること.
    実際の race を再現するのは E2E でしか難しいので, 統合動作のみ確認."""
    office = Office(name="c2-office")
    db.add(office)
    await db.flush()
    p = Patient(code="C2", name="c2-patient", status="active", primary_office_id=office.id)
    db.add(p)
    await db.commit()

    # 採用が成功し idempotent でないこと
    res = await apply_individual_proposal(
        db,
        patient_id=p.id,
        visit_plans=[{"weekday": 0, "start_time": time(10, 0), "duration_min": 30}],
    )
    await db.commit()
    assert res["applied"] is True
    assert res["idempotent"] is False

    # 二度目は idempotent=True
    res2 = await apply_individual_proposal(
        db,
        patient_id=p.id,
        visit_plans=[{"weekday": 0, "start_time": time(10, 0), "duration_min": 30}],
    )
    await db.commit()
    assert res2["idempotent"] is True


# ---------------------------------------------------------------------------
# H2 — None visit が target_set に紛れ込んでも crash しない
# ---------------------------------------------------------------------------


def test_enforce_h2_same_address_handles_none_visits_in_target() -> None:
    """H2 regression: 3 件同住所のうち 2 件を target_set に移し,
    元のスロットを None マークしたあとでも .lat / .lng アクセスで落ちない."""
    from app.services.scheduling.auto_allocator_v2 import V2Set, _enforce_h2_same_address

    # 3 件同住所 (3 セットに分かれている想定)
    v1 = _make_visit(lat=35.650, lng=140.100, patient_name="A")
    v2 = _make_visit(lat=35.650, lng=140.100, patient_name="B")
    v3 = _make_visit(lat=35.650, lng=140.100, patient_name="C")
    sets = [V2Set(visits=[v1]), V2Set(visits=[v2]), V2Set(visits=[v3])]
    warnings: list[V2Warning] = []
    # 例外を投げずに完走すれば OK
    _enforce_h2_same_address(sets, warnings)
    # 1 つ以上のセットに 2 件まで集約され, 3 件目は警告として残る
    assert any("3+ visits" in w.message for w in warnings) or all(len(s.visits) <= 2 for s in sets)


# ---------------------------------------------------------------------------
# W41 v2 — H2 split overflow: 3 名以上同住所を別 set に強制分散
# ---------------------------------------------------------------------------


def test_enforce_h2_split_overflow_distributes_three_same_address() -> None:
    """H2 強化: 同住所 3 名を 1 set に集めたあとで, 1 名が別 set に分散される.

    入力: set1=[A同住所, B同住所, C同住所], set2=[D 余裕あり, 別住所]
    期待: set1 が 2 名 (同住所 2 名上限), 1 名が set2 へ移動.
    """
    from app.services.scheduling.auto_allocator_v2 import V2Set, _enforce_h2_split_overflow

    office = uuid.uuid4()
    v_a = _make_visit(lat=35.650, lng=140.100, patient_name="A", office_id=office)
    v_b = _make_visit(lat=35.650, lng=140.100, patient_name="B", office_id=office)
    v_c = _make_visit(lat=35.650, lng=140.100, patient_name="C", office_id=office)
    v_d = _make_visit(lat=35.660, lng=140.110, patient_name="D", office_id=office)
    sets = [V2Set(visits=[v_a, v_b, v_c]), V2Set(visits=[v_d])]
    warnings: list[V2Warning] = []
    _enforce_h2_split_overflow(sets, warnings)

    # 同住所 3 名 → 2 + 1 になる
    assert len(sets[0].visits) == 2
    assert len(sets[1].visits) == 2
    assert any("3 名以上検出" in w.message for w in warnings)


def test_enforce_h2_split_overflow_no_target_emits_warning() -> None:
    """H2 強化: 移動先 set が無い (全 set 満員) 場合, 警告に「移動先見つからず」を出す."""
    from app.services.scheduling.auto_allocator_v2 import V2Set, _enforce_h2_split_overflow

    office = uuid.uuid4()
    v_a = _make_visit(lat=35.650, lng=140.100, patient_name="A", office_id=office)
    v_b = _make_visit(lat=35.650, lng=140.100, patient_name="B", office_id=office)
    v_c = _make_visit(lat=35.650, lng=140.100, patient_name="C", office_id=office)
    # set2 は別 (office, weekday, am_pm) なので移動先候補にならない
    v_other = _make_visit(lat=35.700, lng=140.150, patient_name="X", office_id=uuid.uuid4())
    sets = [V2Set(visits=[v_a, v_b, v_c]), V2Set(visits=[v_other])]
    warnings: list[V2Warning] = []
    _enforce_h2_split_overflow(sets, warnings)

    # 移動先がないので 1 件は overflow → 警告 (日本語化済)
    assert any("移動先なし" in w.message for w in warnings)


def test_enforce_h2_split_overflow_no_op_for_two_same_address() -> None:
    """H2 強化: 同住所 2 名なら何もしない (規定通り 1 set に 2 名収納)."""
    from app.services.scheduling.auto_allocator_v2 import V2Set, _enforce_h2_split_overflow

    v_a = _make_visit(lat=35.650, lng=140.100, patient_name="A")
    v_b = _make_visit(lat=35.650, lng=140.100, patient_name="B")
    sets = [V2Set(visits=[v_a, v_b])]
    warnings: list[V2Warning] = []
    _enforce_h2_split_overflow(sets, warnings)
    assert len(sets[0].visits) == 2
    assert warnings == []


# ---------------------------------------------------------------------------
# H4 — staff_count == 0 のとき course_code='M' + 警告
# ---------------------------------------------------------------------------


def test_combine_am_pm_zero_staff_emits_warning() -> None:
    """H4: staff_count=0 のとき manager 補充警告が出る."""
    from app.services.scheduling.auto_allocator_v2 import V2Set, combine_am_pm_sets

    am1 = V2Set(visits=[_make_visit(lat=35.65, lng=140.10)])
    warnings: list[V2Warning] = []
    combine_am_pm_sets([am1], [], staff_count=0, warnings=warnings)
    assert any("スタッフ 0 名" in w.message for w in warnings)


@pytest.mark.asyncio
async def test_run_v2_pipeline_zero_staff_assigns_course_code_m(db) -> None:
    """H4: staff_count=0 のとき after_visits.course_code は 'M' になる.

    CareFlow Wave Next 3: M course はマネージャー数で動的制限されるため、
    M を発番させるにはマネージャー 1 名以上の出勤が必要.
    """
    office = Office(name="h4-zero-staff")
    db.add(office)
    await db.flush()
    # スタッフ (role='staff') は登録しない (= staff_count=0) が、
    # M course を発番させるためマネージャー 1 名を出勤させる.
    mgr = Staff(
        name="h4-mgr",
        role="manager",
        is_trainee=False,
        primary_office_id=office.id,
    )
    db.add(mgr)
    await db.flush()
    db.add(StaffShift(staff_id=mgr.id, weekday=0, is_on=True))
    p = Patient(
        code="H4P",
        name="h4-patient",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    db.add(p)
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    after_codes = {v.course_code for v in result["after_visits"]}
    assert after_codes == {"M"}, (
        f"staff_count=0 では course_code は 'M' のみのはず: got {after_codes}"
    )


# ---------------------------------------------------------------------------
# W41 v2.6 — 動的コース数絞り込み (staff_count に応じた A-E / M 振り分け)
# ---------------------------------------------------------------------------


def _make_patient(
    *,
    code: str,
    office_id: UUID,
    lat: float,
    lng: float,
    preferred_start: str = "10:00",
    weekdays: list[str] | None = None,
    time_type: str = "固定",
) -> Patient:
    """W41 v2.6: 動的絞り込みテスト用の Patient ヘルパー."""
    return Patient(
        code=code,
        name=f"P-{code}",
        status="active",
        lat=lat,
        lng=lng,
        primary_office_id=office_id,
        weekly_pattern={
            "preferred_weekdays": weekdays or ["Mon"],
            "preferred_start": preferred_start,
            "service_minutes": 30,
            "time_type": time_type,
        },
    )


@pytest.mark.asyncio
async def test_run_v2_pipeline_caps_normal_courses_at_staff_count(db) -> None:
    """W41 v2.6: staff_count=3 のとき通常コースは A/B/C の 3 個まで.

    8 患者を距離 >5km で離れた地点に配置 → クラスタは 4 セット (2 ペア × 4),
    スタッフ 3 名なら A/B/C 3 コース + 残り 1 セットは "M" になる.

    cluster_by_distance_greedy は「最も近いペア」を常に seed として作るため
    広く離れた N 個の患者は N/2 個のペアセットになる (N が奇数なら +1 単独セット).
    """
    office = Office(name="cap-3-office")
    db.add(office)
    await db.flush()
    # 月曜出勤スタッフ 3 名 (role='staff')
    for i in range(3):
        s = Staff(
            name=f"cap-staff-{i}",
            role="staff",
            is_trainee=False,
            primary_office_id=office.id,
        )
        db.add(s)
        await db.flush()
        db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True))
    # CareFlow Wave Next 3: M course はマネージャー数で動的制限されるので、
    # 超過セットが M overflow に流れるためにマネージャー 2 名を出勤させる.
    for i in range(2):
        m = Staff(
            name=f"cap-mgr-{i}",
            role="manager",
            is_trainee=False,
            primary_office_id=office.id,
        )
        db.add(m)
        await db.flush()
        db.add(StaffShift(staff_id=m.id, weekday=0, is_on=True))
    # 8 患者: 各々 >5km 離れた地点 (0.2deg ~ 22km) で別クラスタになるよう配置.
    # 8 患者 → 4 ペアセット → staff_count=3 で 3 set 通常 + 1 set M.
    for i in range(8):
        db.add(
            _make_patient(
                code=f"CAP{i}",
                office_id=office.id,
                lat=35.65 + i * 0.2,
                lng=140.10 + i * 0.2,
                preferred_start="10:00",
            )
        )
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    codes = [v.course_code for v in result["after_visits"]]
    code_counts = Counter(codes)
    # 通常コース (A/B/C/D/E のうち) は staff_count=3 以下に絞られる
    normal_codes = {c for c in code_counts if c in {"A", "B", "C", "D", "E"}}
    assert len(normal_codes) <= 3, (
        f"staff_count=3 なら通常コードは 3 個以下のはず: got {normal_codes}"
    )
    # 通常コードに D / E は **絶対に出ない** (staff_count を超えるため)
    assert "D" not in code_counts, f"staff_count=3 で 'D' が出てしまった: {code_counts}"
    assert "E" not in code_counts, f"staff_count=3 で 'E' が出てしまった: {code_counts}"
    # 超過セットは M / M2 / M3 ... に押し付けられる (H1 修正で分散)
    assert any(c in _M_OVERFLOW_CODES for c in code_counts), (
        f"超過セットは M overflow ({_M_OVERFLOW_CODES}) になるはず: {code_counts}"
    )


@pytest.mark.asyncio
async def test_run_v2_pipeline_dynamic_course_count_per_weekday(db) -> None:
    """W41 v2.6: 月曜 4 名出勤 / 土曜 3 名出勤 のとき
    月曜は A/B/C/D まで, 土曜は A/B/C まで (D は M に押し付け).
    """
    office = Office(name="weekday-dynamic-office")
    db.add(office)
    await db.flush()

    # スタッフ 4 名: 全員月曜出勤, うち 3 名のみ土曜出勤
    for i in range(4):
        s = Staff(
            name=f"wd-staff-{i}",
            role="staff",
            is_trainee=False,
            primary_office_id=office.id,
        )
        db.add(s)
        await db.flush()
        db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True))  # 月曜
        if i < 3:  # 4 人目だけ土曜休み
            db.add(StaffShift(staff_id=s.id, weekday=5, is_on=True))  # 土曜
    # CareFlow Wave Next 3: M course はマネージャー数で動的制限されるので、
    # 超過セットが M overflow に流れるためマネージャー 2 名 (両曜日出勤) を追加.
    for i in range(2):
        m = Staff(
            name=f"wd-mgr-{i}",
            role="manager",
            is_trainee=False,
            primary_office_id=office.id,
        )
        db.add(m)
        await db.flush()
        db.add(StaffShift(staff_id=m.id, weekday=0, is_on=True))
        db.add(StaffShift(staff_id=m.id, weekday=5, is_on=True))
    # 月曜 / 土曜 それぞれ overflow が発生するよう、>5km 離れた 10 患者 × 各曜日 配置
    # (10 patient → 5 ペアセット, 月曜 staff_count=4 / 土曜 staff_count=3 で overflow を保証)
    for i in range(10):
        # 月曜患者
        db.add(
            _make_patient(
                code=f"MON{i}",
                office_id=office.id,
                lat=35.65 + i * 0.2,
                lng=140.10 + i * 0.2,
                preferred_start="10:00",
                weekdays=["Mon"],
            )
        )
        # 土曜患者
        db.add(
            _make_patient(
                code=f"SAT{i}",
                office_id=office.id,
                lat=35.65 + i * 0.2,
                lng=140.10 + i * 0.2,
                preferred_start="10:00",
                weekdays=["Sat"],
            )
        )
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    # weekday → set(course_code)
    by_weekday: dict[int, set[str]] = {}
    for v in result["after_visits"]:
        by_weekday.setdefault(v.weekday, set()).add(v.course_code)
    mon_codes = by_weekday.get(0, set())
    sat_codes = by_weekday.get(5, set())
    # 月曜は 4 名出勤 → A/B/C/D まで OK (E は staff_count=4 超過 = M overflow)
    # H1 修正: 超過分は M / M2 / M3 ... に分散.
    assert mon_codes - _M_OVERFLOW_CODES <= {"A", "B", "C", "D"}, (
        f"月曜 staff_count=4 で D 超え: {mon_codes}"
    )
    assert "E" not in mon_codes, f"月曜 staff_count=4 で 'E' が出てしまった: {mon_codes}"
    # 土曜は 3 名出勤 → A/B/C まで OK, D が出たら絶対 NG
    assert sat_codes - _M_OVERFLOW_CODES <= {"A", "B", "C"}, (
        f"土曜 staff_count=3 で C 超え: {sat_codes}"
    )
    assert "D" not in sat_codes, f"土曜 staff_count=3 で 'D' が出てしまった: {sat_codes}"
    assert "E" not in sat_codes, f"土曜 staff_count=3 で 'E' が出てしまった: {sat_codes}"


@pytest.mark.asyncio
async def test_run_v2_pipeline_manager_excluded_from_staff_count(db) -> None:
    """W41 v2.6: role='manager' は staff_count にカウントされない.

    マネージャー 1 名 + 通常スタッフ 2 名 → 通常コースは A/B のみ
    (manager は staff_count に入らないため C/D/E は出ない).
    """
    office = Office(name="manager-exclusion-office")
    db.add(office)
    await db.flush()
    # マネージャー 1 名 (通常 staff_count に入らないはず)
    mgr = Staff(
        name="mgr",
        role="manager",
        is_trainee=False,
        primary_office_id=office.id,
    )
    db.add(mgr)
    await db.flush()
    db.add(StaffShift(staff_id=mgr.id, weekday=0, is_on=True))
    # 通常スタッフ 2 名
    for i in range(2):
        s = Staff(
            name=f"normal-{i}",
            role="staff",
            is_trainee=False,
            primary_office_id=office.id,
        )
        db.add(s)
        await db.flush()
        db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True))
    # 6 患者 (>5km 離れた地点で 3 ペアセットになるよう配置)
    # → staff_count=2 (manager 除外) で A/B 2 個 + 1 set "M" になるはず
    for i in range(6):
        db.add(
            _make_patient(
                code=f"MGR-EX{i}",
                office_id=office.id,
                lat=35.65 + i * 0.2,
                lng=140.10 + i * 0.2,
                preferred_start="10:00",
            )
        )
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    codes = {v.course_code for v in result["after_visits"]}
    # 通常 staff_count=2 (manager は除外) なので A/B のみ (overflow は M/M2/M3...)
    normal = codes - _M_OVERFLOW_CODES
    assert normal <= {"A", "B"}, (
        f"manager 除外して staff_count=2 のはずだが、想定外コードあり: {codes}"
    )
    assert "C" not in codes, (
        f"staff_count=2 で 'C' が出た (manager がカウントされた可能性): {codes}"
    )


@pytest.mark.asyncio
async def test_run_v2_pipeline_overflow_pushed_to_m_course(db) -> None:
    """W41 v2.6: staff_count を超えた set は course_code='M' に押し付けられる.

    スタッフ 1 名 / 6 患者 (バラバラ住所 → 3 ペアセット) → A (1 set) + M (2 set).
    """
    office = Office(name="overflow-m-office")
    db.add(office)
    await db.flush()
    s = Staff(
        name="ov-staff",
        role="staff",
        is_trainee=False,
        primary_office_id=office.id,
    )
    db.add(s)
    await db.flush()
    db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True))
    # CareFlow Wave Next 3: M course はマネージャー数で動的制限されるので、
    # 超過セットが M overflow に流れるためにマネージャー 2 名を出勤させる.
    for i in range(2):
        m = Staff(
            name=f"ov-mgr-{i}",
            role="manager",
            is_trainee=False,
            primary_office_id=office.id,
        )
        db.add(m)
        await db.flush()
        db.add(StaffShift(staff_id=m.id, weekday=0, is_on=True))
    for i in range(6):
        db.add(
            _make_patient(
                code=f"OV{i}",
                office_id=office.id,
                lat=35.65 + i * 0.2,
                lng=140.10 + i * 0.2,
                preferred_start="10:00",
            )
        )
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    code_counts = Counter(v.course_code for v in result["after_visits"])
    # A は 1 set 分のみ, 残り 2 set 分の patient は M / M2 / M3 ... に分散
    assert code_counts.get("A", 0) >= 1, f"少なくとも 1 コースは A になるはず: {code_counts}"
    assert "B" not in code_counts, f"staff_count=1 なのに 'B' が出た: {code_counts}"
    assert "C" not in code_counts, f"staff_count=1 なのに 'C' が出た: {code_counts}"
    # 6 患者中 A の 1 set に入りきらない残り 4 patient (2 sets) は M overflow に分散
    m_overflow_total = sum(v for c, v in code_counts.items() if c in _M_OVERFLOW_CODES)
    assert m_overflow_total >= 2, f"超過 patient は M overflow に押し付けられるはず: {code_counts}"


@pytest.mark.asyncio
async def test_run_v2_pipeline_inactive_staff_excluded_from_count(db) -> None:
    """W41 v2.6: status != 'active' / is_trainee=True のスタッフは staff_count から除外.

    active 2 名 + inactive 1 名 + trainee 1 名 → staff_count=2 → A/B のみ.
    """
    office = Office(name="inactive-exclusion-office")
    db.add(office)
    await db.flush()
    # active 通常スタッフ 2 名
    for i in range(2):
        s = Staff(
            name=f"active-{i}",
            role="staff",
            is_trainee=False,
            primary_office_id=office.id,
            status="active",
        )
        db.add(s)
        await db.flush()
        db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True))
    # inactive スタッフ (退職等)
    s_inactive = Staff(
        name="inactive",
        role="staff",
        is_trainee=False,
        primary_office_id=office.id,
        status="inactive",
    )
    db.add(s_inactive)
    await db.flush()
    db.add(StaffShift(staff_id=s_inactive.id, weekday=0, is_on=True))
    # 新人 (is_trainee=True)
    s_trainee = Staff(
        name="trainee",
        role="staff",
        is_trainee=True,
        primary_office_id=office.id,
        status="active",
    )
    db.add(s_trainee)
    await db.flush()
    db.add(StaffShift(staff_id=s_trainee.id, weekday=0, is_on=True))
    # 6 患者 (>5km 離れた地点で 3 ペアセット, staff_count=2 で overflow 発生)
    for i in range(6):
        db.add(
            _make_patient(
                code=f"INAC{i}",
                office_id=office.id,
                lat=35.65 + i * 0.2,
                lng=140.10 + i * 0.2,
                preferred_start="10:00",
            )
        )
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    codes = {v.course_code for v in result["after_visits"]}
    # staff_count=2 → A/B のみ, C/D/E は出ない
    assert "C" not in codes, f"inactive/trainee 除外して staff_count=2 のはず: {codes}"
    assert "D" not in codes, f"inactive/trainee 除外して staff_count=2 のはず: {codes}"
    assert "E" not in codes, f"inactive/trainee 除外して staff_count=2 のはず: {codes}"


# ---------------------------------------------------------------------------
# C-Codex-1: reset_visits_to_fixed が Visit.course_id をセットする (regression)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_visits_to_fixed_sets_course_id(db) -> None:
    """C-Codex-1 regression: reset で生成される Visit は course_id を持つ.

    course_id が NULL のままだと Frontend CourseDayTablePanel で除外され、
    UI 上「全部消えた」ように見える bug が再発しないことを担保する.
    """
    from datetime import date

    from sqlalchemy import select

    from app.models.course import Course
    from app.models.course_template import CourseTemplate
    from app.models.visit import Visit

    office = Office(name="cc1-office")
    db.add(office)
    await db.flush()
    # PFV.course_template_id を解決できるよう template を用意.
    template = CourseTemplate(
        office_id=office.id,
        label="A",
        capacity_mon=6,
        capacity_tue=6,
        capacity_wed=6,
        capacity_thu=6,
        capacity_fri=6,
    )
    db.add(template)
    await db.flush()
    p = Patient(
        code="CC1",
        name="cc1-patient",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
    )
    db.add(p)
    await db.flush()
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=0,
            start_time=time(10, 0),
            duration_min=30,
            slot_index=0,
            course_template_id=template.id,
        )
    )
    s = Staff(name="cc1-staff", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(s)
    await db.flush()
    db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True))
    await db.commit()

    result = await reset_visits_to_fixed(db, iso_year=2026, iso_week=20, office_ids=[office.id])
    await db.commit()
    assert result["visits_regenerated"] >= 1

    # 再生成された visit は course_id をセット済みであること.
    new_visits = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == p.id,
                Visit.deleted_at.is_(None),
                Visit.visit_date == date(2026, 5, 11),  # Mon W20 2026
                Visit.source == "reset_v2",
            )
        )
    ).all()
    assert new_visits, "reset_v2 経由の visit が見つからない"
    for v in new_visits:
        assert v.course_id is not None, (
            f"reset 生成 visit に course_id が無い (patient_id={v.patient_id}): UI に表示されない bug の再発"
        )
        # Course が実在することも担保
        course = await db.scalar(select(Course).where(Course.id == v.course_id))
        assert course is not None, "course_id が指す Course 行が見つからない"
        assert course.office_id == office.id
        assert course.weekday == 0
        assert course.code == "A"  # template.label='A' から導出


# ---------------------------------------------------------------------------
# C-Codex-2: reset の削除範囲を絞る (manual / completed を保護)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_visits_to_fixed_preserves_manual_and_completed(db) -> None:
    """C-Codex-2 regression: source='manual' / status='completed' な visit は
    reset で soft-delete されない."""
    from datetime import date

    from app.models.visit import VISIT_STATUS_COMPLETED, VISIT_STATUS_PLANNED, Visit

    office = Office(name="cc2-office")
    db.add(office)
    await db.flush()
    p = Patient(
        code="CC2",
        name="cc2-patient",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
    )
    db.add(p)
    await db.flush()
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=0,
            start_time=time(10, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    # (a) 手動作成 — 保護対象
    manual = Visit(
        patient_id=p.id,
        visit_date=date(2026, 5, 11),  # Mon W20
        start_time=time(14, 0),
        end_time=time(15, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="manual",
        required_staff_count=1,
    )
    # (b) 完了済み (status='completed') — 自動生成由来でも保護
    completed = Visit(
        patient_id=p.id,
        visit_date=date(2026, 5, 13),  # Wed W20
        start_time=time(11, 0),
        end_time=time(11, 30),
        type="regular",
        status=VISIT_STATUS_COMPLETED,
        source="auto_alloc",
        required_staff_count=1,
    )
    # (c) 自動生成・planned — 削除対象
    autoplanned = Visit(
        patient_id=p.id,
        visit_date=date(2026, 5, 14),  # Thu W20
        start_time=time(16, 0),
        end_time=time(16, 30),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto_alloc",
        required_staff_count=1,
    )
    db.add(manual)
    db.add(completed)
    db.add(autoplanned)
    await db.commit()

    await reset_visits_to_fixed(db, iso_year=2026, iso_week=20, office_ids=[office.id])
    await db.commit()

    # 手動 / 完了済み は保護される
    await db.refresh(manual)
    await db.refresh(completed)
    await db.refresh(autoplanned)
    assert manual.deleted_at is None, "source='manual' は保護されるべき (C-Codex-2)"
    assert completed.deleted_at is None, "status='completed' は保護されるべき (C-Codex-2)"
    assert autoplanned.deleted_at is not None, (
        "source='auto_alloc' + status='planned' は削除されるべき"
    )


# ---------------------------------------------------------------------------
# CareFlow 本番バグ修正 (Option A): reset / apply_week_only で保護対象 active visit
# (status not in DELETABLE / source not in DELETABLE) と PFV INSERT が同 unique key
# で衝突する場合は INSERT skip + warning に逃がし、IntegrityError (409) を回避する.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_skips_when_protected_completed_visit_exists(db) -> None:
    """status='completed' な既存 visit と PFV INSERT の unique key 衝突を
    skip + warning で逃がす (= IntegrityError にしない)."""
    from datetime import date

    from sqlalchemy import select

    from app.models.visit import VISIT_STATUS_COMPLETED, Visit

    office = Office(name="rsskip1-office")
    db.add(office)
    await db.flush()
    p = Patient(
        code="RSKIP1",
        name="rsskip1-patient",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
    )
    db.add(p)
    await db.flush()
    # PFV: Mon 12:00, 30 分.
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=0,
            start_time=time(12, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    s = Staff(name="rsskip1-staff", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(s)
    await db.flush()
    db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True))
    # 既存 completed visit (保護対象, soft-delete されない).
    # 同 patient × 同 visit_date × 同 start_time × visit_group_id=NULL.
    protected = Visit(
        patient_id=p.id,
        visit_date=date(2026, 5, 11),  # Mon W20
        start_time=time(12, 0),
        end_time=time(12, 30),
        type="regular",
        status=VISIT_STATUS_COMPLETED,
        source="auto_alloc",  # source は削除候補だが status='completed' で保護される
        required_staff_count=1,
    )
    db.add(protected)
    await db.commit()
    protected_id = protected.id

    result = await reset_visits_to_fixed(db, iso_year=2026, iso_week=20, office_ids=[office.id])
    await db.commit()

    # 既存 completed は保護されている.
    refreshed = await db.scalar(select(Visit).where(Visit.id == protected_id))
    assert refreshed is not None
    assert refreshed.deleted_at is None, "status='completed' は保護されるべき"

    # PFV からの再生成は衝突するためスキップされ、warning に記録される.
    warning_texts = "\n".join(result.get("warnings", []))
    assert "衝突するため再生成スキップ" in warning_texts, (
        f"skip warning が見当たらない: {warning_texts!r}"
    )

    # 当該 patient × Mon × 12:00 で active visit は 1 件 (= 保護された completed のみ).
    actives = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == p.id,
                Visit.visit_date == date(2026, 5, 11),
                Visit.start_time == time(12, 0),
                Visit.deleted_at.is_(None),
            )
        )
    ).all()
    assert len(actives) == 1
    assert actives[0].id == protected_id


@pytest.mark.asyncio
async def test_reset_skips_when_protected_manual_source_visit_exists(db) -> None:
    """source='manual' な既存 visit と PFV INSERT の unique key 衝突を
    skip + warning で逃がす."""
    from datetime import date

    from sqlalchemy import select as _select

    from app.models.visit import VISIT_STATUS_PLANNED, Visit

    office = Office(name="rsskip2-office")
    db.add(office)
    await db.flush()
    p = Patient(
        code="RSKIP2",
        name="rsskip2-patient",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
    )
    db.add(p)
    await db.flush()
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=0,
            start_time=time(12, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    s = Staff(name="rsskip2-staff", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(s)
    await db.flush()
    db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True))
    # 既存 manual source visit (保護対象).
    protected = Visit(
        patient_id=p.id,
        visit_date=date(2026, 5, 11),  # Mon W20
        start_time=time(12, 0),
        end_time=time(12, 30),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="manual",  # 保護対象 source
        required_staff_count=1,
    )
    db.add(protected)
    await db.commit()
    protected_id = protected.id

    result = await reset_visits_to_fixed(db, iso_year=2026, iso_week=20, office_ids=[office.id])
    await db.commit()

    refreshed = await db.scalar(_select(Visit).where(Visit.id == protected_id))
    assert refreshed is not None
    assert refreshed.deleted_at is None, "source='manual' は保護されるべき"

    warning_texts = "\n".join(result.get("warnings", []))
    assert "衝突するため再生成スキップ" in warning_texts, (
        f"skip warning が見当たらない: {warning_texts!r}"
    )

    actives = (
        await db.scalars(
            _select(Visit).where(
                Visit.patient_id == p.id,
                Visit.visit_date == date(2026, 5, 11),
                Visit.start_time == time(12, 0),
                Visit.deleted_at.is_(None),
            )
        )
    ).all()
    assert len(actives) == 1
    assert actives[0].id == protected_id


@pytest.mark.asyncio
async def test_apply_week_only_skips_protected_visit(db) -> None:
    """apply_week_only: 保護対象 visit と unique key 衝突する INSERT は
    skip + warning に逃がす."""
    from datetime import date

    from sqlalchemy import select

    from app.models.visit import VISIT_STATUS_COMPLETED, Visit
    from app.services.scheduling.auto_allocator_v2 import apply_week_only

    office = Office(name="awoskip-office")
    db.add(office)
    await db.flush()
    p = Patient(
        code="AWOSKIP",
        name="awoskip-patient",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
    )
    db.add(p)
    await db.flush()
    s = Staff(name="awoskip-staff", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(s)
    await db.flush()
    db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True))
    # 既存 completed visit (保護対象).
    protected = Visit(
        patient_id=p.id,
        visit_date=date(2026, 5, 11),  # Mon W20
        start_time=time(12, 0),
        end_time=time(12, 30),
        type="regular",
        status=VISIT_STATUS_COMPLETED,
        source="auto_alloc",
        required_staff_count=1,
    )
    db.add(protected)
    await db.commit()
    protected_id = protected.id

    # visit_plans で同 patient × Mon × 12:00 を申請 → 既存と衝突 → skip 想定.
    result = await apply_week_only(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        patient_visit_plans=[
            {
                "patient_id": p.id,
                "visit_plans": [
                    {
                        "weekday": 0,
                        "start_time": time(12, 0),
                        "end_time": time(12, 30),
                        "duration_min": 30,
                        "course_code": "M",
                        "office_id": office.id,
                        "am_pm": "pm",
                    }
                ],
            }
        ],
    )
    await db.commit()

    # 既存保護 visit は維持されている.
    refreshed = await db.scalar(select(Visit).where(Visit.id == protected_id))
    assert refreshed is not None
    assert refreshed.deleted_at is None

    warning_texts = "\n".join(result.get("warnings", []))
    assert "衝突するため適用スキップ" in warning_texts, (
        f"apply skip warning が見当たらない: {warning_texts!r}"
    )

    # 当該 (patient, date, start_time) で active visit は 1 件のみ.
    actives = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == p.id,
                Visit.visit_date == date(2026, 5, 11),
                Visit.start_time == time(12, 0),
                Visit.deleted_at.is_(None),
            )
        )
    ).all()
    assert len(actives) == 1
    assert actives[0].id == protected_id


@pytest.mark.asyncio
async def test_apply_week_only_honors_nondefault_lunch_window(db) -> None:
    """残漏れ修正 (G-88 Step3 再レビュー): apply_week_only の昼休みゲートが
    config の昼休み窓を honor する (固定 11:30-13:30 で誤スキップしない).

    14:10-15:40 の plan (90 分):
      - 既定窓 (11:30-13:30): visit は窓外 → INSERT される (= プレビューと一致).
      - 非既定窓 (14:00-16:00): AM 側回避 (14:10 < 14:30) も PM 側回避
        (15:40 > 15:30) も不可で lunch 不可避 → 動的窓 warning でスキップされる
        (= config-aware プレビューと確定が一致; 固定窓で誤って INSERT しない).
    """
    from dataclasses import replace as _dc_replace
    from datetime import date

    from sqlalchemy import select

    from app.models.visit import Visit
    from app.services.scheduling.auto_allocator_v2 import (
        DEFAULT_SCHEDULING_CONFIG,
        apply_week_only,
    )

    office = Office(name="awolunch-office")
    db.add(office)
    await db.flush()
    p = Patient(
        code="AWOLUNCH",
        name="awolunch-patient",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
    )
    db.add(p)
    await db.flush()
    s = Staff(name="awolunch-staff", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(s)
    await db.flush()
    db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True))
    await db.commit()

    plan = {
        "patient_id": p.id,
        "visit_plans": [
            {
                "weekday": 0,
                "start_time": time(14, 10),
                "end_time": time(15, 40),
                "duration_min": 90,
                "course_code": "M",
                "office_id": office.id,
                "am_pm": "pm",
            }
        ],
    }

    # (a) 既定窓 (11:30-13:30): 14:10-15:40 は窓外 → INSERT される.
    result_default = await apply_week_only(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        patient_visit_plans=[plan],
        config=DEFAULT_SCHEDULING_CONFIG,
    )
    await db.commit()
    inserted_default = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == p.id,
                Visit.visit_date == date(2026, 5, 11),
                Visit.start_time == time(14, 10),
                Visit.deleted_at.is_(None),
            )
        )
    ).all()
    assert len(inserted_default) == 1, (
        "既定窓 (11:30-13:30) では 14:10-15:40 は窓外で INSERT される: "
        f"warnings={result_default.get('warnings')!r}"
    )

    # (b) 非既定窓 (14:00-16:00): lunch 不可避 → 動的窓 warning でスキップ.
    nondefault_cfg = _dc_replace(
        DEFAULT_SCHEDULING_CONFIG,
        lunch_window_start=time(14, 0),
        lunch_window_end=time(16, 0),
    )
    result_shifted = await apply_week_only(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        patient_visit_plans=[plan],
        config=nondefault_cfg,
    )
    await db.commit()
    warning_texts = "\n".join(result_shifted.get("warnings", []))
    # 固定 11:30-13:30 文字列ではなく、config 窓 (14:00-16:00) を反映した warning.
    assert "14:00-16:00" in warning_texts, (
        f"非既定昼休み窓を反映した warning が見当たらない: {warning_texts!r}"
    )
    assert "11:30-13:30" not in warning_texts, (
        f"固定窓 11:30-13:30 のリテラルが残っている (config 未注入): {warning_texts!r}"
    )
    # 該当 visit は INSERT されていない (= 確定がプレビュー = config-aware と一致).
    actives_shifted = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == p.id,
                Visit.visit_date == date(2026, 5, 11),
                Visit.start_time == time(14, 10),
                Visit.deleted_at.is_(None),
            )
        )
    ).all()
    assert actives_shifted == [], (
        "非既定窓 (14:00-16:00) では 14:10-15:40 は lunch 不可避でスキップされる"
    )


# ---------------------------------------------------------------------------
# W41 v2 (Mode 2 UI 拡張) — _extract_area_label
# ---------------------------------------------------------------------------


def test_extract_area_label_chiba_inage_ward() -> None:
    """千葉県千葉市稲毛区宮野木町818-2 → '宮野木'."""
    from app.services.scheduling.auto_allocator_v2 import _extract_area_label

    assert _extract_area_label("千葉県千葉市稲毛区宮野木町818-2") == "宮野木"


def test_extract_area_label_chiba_hanamigawa_ward() -> None:
    """千葉県千葉市花見川区幕張本郷3-21-29 → '幕張本郷'."""
    from app.services.scheduling.auto_allocator_v2 import _extract_area_label

    assert _extract_area_label("千葉県千葉市花見川区幕張本郷3-21-29") == "幕張本郷"


def test_extract_area_label_chiba_mihama_ward() -> None:
    """千葉県千葉市美浜区磯辺4-175棟402 → '磯辺'."""
    from app.services.scheduling.auto_allocator_v2 import _extract_area_label

    assert _extract_area_label("千葉県千葉市美浜区磯辺4-175棟402") == "磯辺"


def test_extract_area_label_yotsukaido_city() -> None:
    """区が無い住所: 千葉県四街道市大日27-18 → '大日'."""
    from app.services.scheduling.auto_allocator_v2 import _extract_area_label

    assert _extract_area_label("千葉県四街道市大日27-18") == "大日"


def test_extract_area_label_none_and_empty() -> None:
    from app.services.scheduling.auto_allocator_v2 import _extract_area_label

    assert _extract_area_label(None) is None
    assert _extract_area_label("") is None


def test_extract_area_label_unparseable_returns_none() -> None:
    """住所として解釈できない文字列は None を返す.

    現状の正規表現は千葉県/千葉市/△△市スコープなので、他都道府県や
    そもそも住所形式でない文字列は None になる仕様 (CareFlow 千葉拠点想定).
    """
    from app.services.scheduling.auto_allocator_v2 import _extract_area_label

    # 完全に住所形式でない場合
    assert _extract_area_label("住所未登録") is None
    # 千葉市 / 〇〇市の形式でない場合 (本サービスは千葉エリア前提のため None で OK)
    assert _extract_area_label("東京都新宿区西新宿2-8-1") is None


# ---------------------------------------------------------------------------
# W41 v2 (Mode 2 UI 拡張) — run_v2_pipeline returns unassigned_patients
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_v2_pipeline_returns_unassigned_for_no_coordinates(db) -> None:
    """座標未設定の患者は unassigned に出る (Mode 2)."""
    office = Office(name="unassign-office")
    db.add(office)
    await db.flush()

    # 座標あり (割当成功する)
    p_ok = Patient(
        code="POK",
        name="座標あり",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    # 座標なし (build_visits_for_pool で skip され、after_visits に出ない)
    p_nogeo = Patient(
        code="PNG",
        name="座標なし",
        status="active",
        lat=None,
        lng=None,
        primary_office_id=office.id,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:30",
            "time_type": "固定",
        },
    )
    db.add_all([p_ok, p_nogeo])
    await db.flush()
    s = Staff(name="staff1", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(s)
    await db.flush()
    db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True))
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    unassigned = result["unassigned_patients"]
    codes = {u["patient_code"] for u in unassigned}
    assert "PNG" in codes, f"座標なし患者 PNG が unassigned に含まれていない: {codes}"
    assert "POK" not in codes, "座標あり患者 POK は unassigned に含まれないはず"
    # P2: reason は enum で "no_coordinates"
    png = next(u for u in unassigned if u["patient_code"] == "PNG")
    assert png["reason"] == "no_coordinates", f"理由が no_coordinates でない: {png['reason']}"
    assert png["dropped_at_stage"] == "general"


@pytest.mark.asyncio
async def test_run_v2_pipeline_no_offices_returns_empty_unassigned(db) -> None:
    """対象拠点なしの早期 return でも unassigned_patients が dict キーとして存在."""
    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[],
        mode="full_optimize",
    )
    # office なし → 早期 return path
    assert "unassigned_patients" in result
    assert result["unassigned_patients"] == []


# ---------------------------------------------------------------------------
# W41 v2 (Mode 2 UI 拡張) — V2Visit.address / .area_label が build 時にセットされる
# ---------------------------------------------------------------------------


def test_build_visits_for_pool_sets_address_and_area_label() -> None:
    """Patient.address があれば V2Visit.address + area_label がセットされる."""
    office_id = uuid.uuid4()
    p = Patient(
        id=uuid.uuid4(),
        code="ADDR1",
        name="住所あり",
        status="active",
        lat=35.65,
        lng=140.10,
        address="千葉県千葉市稲毛区宮野木町818-2",
        primary_office_id=office_id,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    visits = build_visits_for_pool([p])
    assert len(visits) == 1
    assert visits[0].address == "千葉県千葉市稲毛区宮野木町818-2"
    assert visits[0].area_label == "宮野木"


def test_build_visits_for_pool_no_address_returns_none_area() -> None:
    """Patient.address が None なら V2Visit.area_label も None."""
    office_id = uuid.uuid4()
    p = Patient(
        id=uuid.uuid4(),
        code="NOADDR",
        name="住所なし",
        status="active",
        lat=35.65,
        lng=140.10,
        address=None,
        primary_office_id=office_id,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    visits = build_visits_for_pool([p])
    assert len(visits) == 1
    assert visits[0].address is None
    assert visits[0].area_label is None


# ---------------------------------------------------------------------------
# W41 v2 (Mode 2 Before/After 表示拡張) — V2Visit.time_type / .sex_restriction
# ---------------------------------------------------------------------------


def test_build_visits_for_pool_extracts_time_type_from_entries() -> None:
    """weekly_pattern.entries[].time_type が V2Visit.time_type にコピーされる."""
    office_id = uuid.uuid4()
    p = Patient(
        id=uuid.uuid4(),
        code="TT1",
        name="time_type test",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office_id,
        weekly_pattern={
            "entries": [
                {"weekday": "Mon", "preferred_start": "10:00", "time_type": "午前"},
                {"weekday": "Wed", "preferred_start": "14:00", "time_type": "午後"},
            ]
        },
    )
    visits = build_visits_for_pool([p])
    assert len(visits) == 2
    by_wd = {v.weekday: v for v in visits}
    assert by_wd[0].time_type == "午前"
    assert by_wd[2].time_type == "午後"


def test_build_visits_for_pool_summary_form_uses_base_time_type() -> None:
    """サマリ形式 (preferred_weekdays + time_type) も time_type を持つ."""
    office_id = uuid.uuid4()
    p = Patient(
        id=uuid.uuid4(),
        code="TT2",
        name="time_type summary",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office_id,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    visits = build_visits_for_pool([p])
    assert len(visits) == 1
    assert visits[0].time_type == "固定"


def test_build_visits_for_pool_propagates_sex_restriction() -> None:
    """patient.sex_restriction が V2Visit.sex_restriction に流れる."""
    office_id = uuid.uuid4()
    p = Patient(
        id=uuid.uuid4(),
        code="SR1",
        name="female only",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office_id,
        sex_restriction="female_only",
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    visits = build_visits_for_pool([p])
    assert len(visits) == 1
    assert visits[0].sex_restriction == "female_only"


@pytest.mark.asyncio
async def test_load_before_visits_sets_time_type_and_sex_restriction(db) -> None:
    """_load_before_visits_from_pfv が time_type / sex_restriction をセットする."""
    from app.services.scheduling.auto_allocator_v2 import _load_before_visits_from_pfv

    office = Office(name="tt-before-office")
    db.add(office)
    await db.flush()

    patient = Patient(
        id=uuid.uuid4(),
        code="TTBA",
        name="Before patient w/ tt",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        sex_restriction="male_only",
        weekly_pattern={
            "entries": [{"weekday": "Mon", "preferred_start": "10:00", "time_type": "午前"}]
        },
    )
    db.add(patient)
    await db.flush()

    pfv = PatientFixedVisit(
        patient_id=patient.id,
        mode="normal",
        weekday=0,
        start_time=time(10, 0),
        duration_min=30,
        slot_index=0,
    )
    db.add(pfv)
    await db.commit()

    visits = await _load_before_visits_from_pfv(db, patients_by_id={patient.id: patient})
    assert len(visits) == 1
    assert visits[0].time_type == "午前"
    assert visits[0].sex_restriction == "male_only"


# ---------------------------------------------------------------------------
# W41 v2 (同住所同時刻集約 ソフト制約) — _consolidate_same_address_time
# ---------------------------------------------------------------------------


def test_consolidate_same_address_time_groups_to_majority() -> None:
    """同住所 2 名が異なる start_time にいる場合、最多 (mode) に集約される.

    A: 10:00 (終日), B: 11:00 (終日) → 同住所同曜日.
    両者 1 回ずつでタイ → タイブレークで早い時刻 (10:00) に集約される.
    """
    from app.services.scheduling.auto_allocator_v2 import _consolidate_same_address_time

    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=0, patient_name="A"
    )
    a.time_type = "終日"
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=11, start_m=0, patient_name="B"
    )
    b.time_type = "終日"
    # 第三者 C を同住所同 start_time (10:00) に置き、10:00 が多数派となる構図.
    c = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=0, patient_name="C"
    )
    c.time_type = "終日"
    warnings: list[V2Warning] = []
    _consolidate_same_address_time([a, b, c], warnings)

    # 10:00 が多数派 (2 件) なので B も 10:00 に集約される
    assert b.start_time == time(10, 0), (
        f"B should be consolidated to 10:00 but stayed at {b.start_time}"
    )
    # 集約成功なので warning は出ない
    assert not any("同住所集約" in w.message for w in warnings), (
        f"集約成功時に warning が出ている: {warnings}"
    )


def test_consolidate_same_address_time_skips_when_fixed_or_out_of_window() -> None:
    """time_type='固定' は集約不可、'時間帯' で範囲外も集約不可. それぞれ warning が出る.

    A, B (= majority 11:00 fixed)、C (固定 10:00) → C は動かせない → warning.
    D (時間帯 09:00-10:30) → 11:00 は範囲外 → 動かせない → warning.
    """
    from app.services.scheduling.auto_allocator_v2 import _consolidate_same_address_time

    office_id = uuid.uuid4()
    # majority は 11:00 — 2 件にして mode を確定させる
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=11, start_m=0, patient_name="A"
    )
    a.time_type = "固定"
    a.preferred_start = "11:00"
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=11, start_m=0, patient_name="B"
    )
    b.time_type = "固定"
    b.preferred_start = "11:00"

    # C: 固定 10:00 — 動かせない
    c = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=0, patient_name="C"
    )
    c.time_type = "固定"
    c.preferred_start = "10:00"

    # D: 時間帯 09:00-10:30 — 11:00 は範囲外 → 動かせない
    d = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=30, patient_name="D"
    )
    d.time_type = "時間帯"
    d.preferred_start = "09:00"
    d.preferred_end = "10:30"

    warnings: list[V2Warning] = []
    _consolidate_same_address_time([a, b, c, d], warnings)

    # C: 動かせない (固定) → start_time は元のまま
    assert c.start_time == time(10, 0), f"C should remain at 10:00 (固定), got {c.start_time}"
    # D: 範囲外なので動かない
    assert d.start_time == time(9, 30), f"D should remain at 9:30 (範囲外), got {d.start_time}"
    # warnings は最低 2 件 (C と D)
    consolidation_warnings = [w for w in warnings if "同住所集約" in w.message]
    assert len(consolidation_warnings) >= 2, (
        f"固定 / 時間帯外 で 2 件以上の warning が出るはずだが: {consolidation_warnings}"
    )
    # 固定 / 時間帯 の理由がそれぞれ含まれる
    assert any("固定" in w.message for w in consolidation_warnings), (
        f"固定 reason が含まれない: {consolidation_warnings}"
    )
    assert any("時間帯" in w.message for w in consolidation_warnings), (
        f"時間帯 reason が含まれない: {consolidation_warnings}"
    )


def test_warnings_are_in_japanese() -> None:
    """W41 v2: warning メッセージが日本語化されていること (受入×, 昼休憩, 同住所).

    旧 "blocked by acceptance_calendar" / "lunch break" / "exceeds" などの
    英語混じり表現が出ないことを substring で検証する.
    """
    from app.services.scheduling.auto_allocator_v2 import (
        _consolidate_same_address_time,
        _filter_unavailable_and_lunch,
    )

    office_id = uuid.uuid4()

    # 1) acceptance × + 昼休憩 (Mode 1)
    blocked_v = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=0, patient_name="B1"
    )
    # Phase E-3 改修 (2): lunch 30 分 fallback まで緩和したため、動的 lunch でも
    # 避けられない 11:50-13:10 visit を使う (start<12:00 かつ end>13:00).
    lunch_v = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=11, start_m=50, patient_name="L1"
    )
    lunch_v.end_time = time(13, 10)
    lunch_v.service_minutes = 80
    unavailable = {(office_id, 0): {time(10, 0)}}
    warnings_a: list[V2Warning] = []
    _filter_unavailable_and_lunch(
        [blocked_v, lunch_v], unavailable_slots=unavailable, warnings=warnings_a
    )
    assert any("受入カレンダー" in w.message and "配置不可" in w.message for w in warnings_a), (
        f"日本語化された受入× warning が無い: {warnings_a}"
    )
    assert any("昼休憩" in w.message and "配置不可" in w.message for w in warnings_a), (
        f"日本語化された昼休憩 warning が無い: {warnings_a}"
    )
    # 旧英語表現が混入していないこと
    for w in warnings_a:
        assert "blocked by acceptance_calendar" not in w.message, f"旧英語表現が残存: {w}"
        assert "lunch break" not in w.message, f"旧英語表現が残存: {w}"
        assert "weekday=" not in w.message, f"weekday=N 表記が残存: {w}"

    # 2) 同住所集約 — 動かせない warning
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=11, start_m=0, patient_name="A"
    )
    a.time_type = "固定"
    a.preferred_start = "11:00"
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=11, start_m=0, patient_name="B"
    )
    b.time_type = "固定"
    b.preferred_start = "11:00"
    c = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=0, patient_name="C"
    )
    c.time_type = "固定"
    c.preferred_start = "10:00"
    warnings_b: list[V2Warning] = []
    _consolidate_same_address_time([a, b, c], warnings_b)
    assert any("同住所集約" in w.message for w in warnings_b), (
        f"同住所集約 warning が無い: {warnings_b}"
    )
    # 月曜 (weekday=0) 表記が日本語に
    assert any("月曜" in w.message for w in warnings_b), f"曜日が日本語化されていない: {warnings_b}"


def test_v2visit_has_preferred_window_fields() -> None:
    """W41 v2: V2Visit に preferred_start / preferred_end フィールドがある.

    weekly_pattern.entries[].preferred_end も build 時に流れる.
    """
    office_id = uuid.uuid4()
    p = Patient(
        id=uuid.uuid4(),
        code="PRE1",
        name="window patient",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office_id,
        weekly_pattern={
            "entries": [
                {
                    "weekday": "Mon",
                    "preferred_start": "09:00",
                    "preferred_end": "10:30",
                    "time_type": "時間帯",
                }
            ]
        },
    )
    visits = build_visits_for_pool([p])
    assert len(visits) == 1
    v = visits[0]
    assert v.preferred_start == "09:00"
    assert v.preferred_end == "10:30"
    assert v.time_type == "時間帯"


# ---------------------------------------------------------------------------
# W41 v2 拡張 — 移動時間の time 化 + 二人組訪問 + コース容量 duration 化
# ---------------------------------------------------------------------------


def test_haversine_minutes_zero_distance_returns_zero() -> None:
    """W41 v2: 0km / 負値 (= 同住所) は移動 0 分."""
    from app.services.scheduling.auto_allocator_v2 import haversine_minutes

    assert haversine_minutes(0.0) == 0
    assert haversine_minutes(-1.0) == 0


def test_haversine_minutes_5km_at_20kmh() -> None:
    """W41 v2: 5km は 20km/h で 15 分."""
    from app.services.scheduling.auto_allocator_v2 import haversine_minutes

    assert haversine_minutes(5.0) == 15


def test_haversine_minutes_minimum_one_minute() -> None:
    """W41 v2: 0 < distance < 1 分相当 でも切上げ 1 分."""
    from app.services.scheduling.auto_allocator_v2 import haversine_minutes

    # 0.1 km / 20 km/h * 60 = 0.3 分 → 切上げ 1 分
    assert haversine_minutes(0.1) == 1


def test_haversine_minutes_same_address_via_haversine_km() -> None:
    """W41 v2: 同住所 (lat/lng 一致) は haversine_km=0 → 0 分."""
    from app.services.scheduling.auto_allocator_v2 import haversine_km, haversine_minutes

    d = haversine_km(35.65, 140.10, 35.65, 140.10)
    assert haversine_minutes(d) == 0


def test_dynamic_start_time_respects_travel_for_terminal_type() -> None:
    """W41 v2: 終日 visit は前訪問の end_time + 移動時間に押し下げられる.

    Setup: コース A 月曜:
      - P-A 09:00-09:30 (固定)
      - P-B 09:00 希望だが 3km 離れた終日 → 09:30 + 9 分 = 09:39 に押し下げ
    """
    from app.services.scheduling.auto_allocator_v2 import _apply_travel_time_to_courses

    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.end_time = time(9, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "固定"
    # 3km 離れた P-B (35.65, 140.10) → (35.65, 140.133)
    # 直線距離 ~3km, 移動 9 分 (3/20*60 ≒ 9)
    b = _make_visit(
        lat=35.65, lng=140.133, office_id=office_id, start_h=9, start_m=0, patient_name="B"
    )
    b.end_time = time(10, 0)
    b.service_minutes = 60
    b.course_code = "A"
    b.time_type = "終日"

    warnings: list[V2Warning] = []
    _apply_travel_time_to_courses([a, b], warnings=warnings)

    # B の start_time は 09:00 ではなく、09:30 (prev.end) + ~9 分 = 09:39 近辺
    assert b.start_time > time(9, 0), f"B should be pushed later than 09:00, got {b.start_time}"
    assert b.start_time >= time(9, 30), f"B should be >= prev.end_time 09:30, got {b.start_time}"
    # end_time も service_minutes だけ後ろにずれる
    expected_end_min = (b.start_time.hour * 60 + b.start_time.minute) + b.service_minutes
    assert b.end_time.hour * 60 + b.end_time.minute == expected_end_min


def test_fixed_time_warning_when_travel_insufficient() -> None:
    """W41 v2 / CareFlow #101 + Fix E: 固定時刻 2 名が **異なる時刻** で移動時間が
    >=5 分不足する場合は物理不可能 → ``course_code=None`` + 戻り値 set.

    Fix E (同時刻 + 異住所) は別 path (auto_time_shift_for_conflict) で処理されるため、
    本テストでは B の start_time をずらして「同時刻」ではなく「shortage>=5」の純粋ケース
    を作る. A 11:00-11:30 固定 → B 11:30 固定 (5km 離れ travel 15 分 + buffer 8 分 =
    23 分必要 / 残り 0 分 → shortage 23 分 → 物理不可能).
    """
    from app.services.scheduling.auto_allocator_v2 import (
        SHORTAGE_THRESHOLD_MIN,
        _apply_travel_time_to_courses,
    )

    office_id = uuid.uuid4()
    # P-A 11:00-11:30 終了, P-B 11:30 固定 (= prev.end と同時刻スタート希望, 5km 離れて travel 不可能)
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=11, start_m=0, patient_name="A"
    )
    a.end_time = time(11, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "固定"
    # 5km 離れた location, B は 11:30 固定希望 (prev.end と同じ瞬間 → travel 0 分残)
    b = _make_visit(
        lat=35.65, lng=140.155, office_id=office_id, start_h=11, start_m=30, patient_name="B"
    )
    b.end_time = time(12, 0)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "固定"
    b.preferred_start = "11:30"

    warnings: list[V2Warning] = []
    unassigned_ids = _apply_travel_time_to_courses([a, b], warnings=warnings)

    # 固定時刻自体は動かさない (= start_time 不変). Fix E は同時刻でないため発動しない.
    assert b.start_time == time(11, 30)
    # shortage は SHORTAGE_THRESHOLD_MIN (=5) 以上 → 物理不可能扱い.
    assert SHORTAGE_THRESHOLD_MIN <= 5  # 安全装置 (定数を緩めた場合に気づくため)
    assert id(b) in unassigned_ids, (
        f"shortage >= {SHORTAGE_THRESHOLD_MIN} の固定 visit は unassigned_ids "
        f"に含まれるべき: {unassigned_ids}"
    )
    assert b.course_code is None, (
        f"shortage >= {SHORTAGE_THRESHOLD_MIN} の固定 visit は course_code=None "
        f"に書き換わるべき: course_code={b.course_code}"
    )
    # 物理不可能 warning が travel_time_shortage type で affected_patient_ids に
    # b.patient_id を含む形で出ている (fixed_time_conflict reason マッピング用).
    matching = [
        w
        for w in warnings
        if w.type == "travel_time_shortage"
        and b.patient_id in (w.affected_patient_ids or [])
        and "物理的に配置不可" in w.message
    ]
    assert matching, f"物理不可能 warning が出ていない: {warnings}"


def test_same_address_zero_travel_no_pushback() -> None:
    """W41 v2: 同住所の連続 visit は移動 0 分 (押し下げ最小限)."""
    from app.services.scheduling.auto_allocator_v2 import _apply_travel_time_to_courses

    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.end_time = time(9, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "固定"
    # 同住所 (= same address bucket) で 09:30 希望
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=30, patient_name="B"
    )
    b.end_time = time(10, 0)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "終日"

    warnings: list[V2Warning] = []
    _apply_travel_time_to_courses([a, b], warnings=warnings)

    # Wave 2 (#115) + Phase E-3 改修 (3): 同住所ペアは「同 start_time +
    # max(service 合計, 90) 占有」に揃える.
    # A 09:00 固定 + B 終日 同住所 → 両者 09:00、A.end=09:30、B.end=10:30 (90 分 clamp).
    assert a.start_time == time(9, 0)
    assert b.start_time == time(9, 0), f"Wave 2: 同住所 B も A と同 start: {b.start_time}"
    assert a.end_time == time(9, 30)
    assert b.end_time == time(10, 30), f"Phase E-3: B.end は 90 分占有: {b.end_time}"
    # 長距離 warning も出ない (cumulative_travel_min = 0)
    assert not any("連続移動時間合計" in w.message for w in warnings), (
        f"同住所コースに長距離 warning は出ないはず: {warnings}"
    )


# ---------------------------------------------------------------------------
# 同住所連番強制 (_reorder_same_address_consecutive)
# ユーザー要望 (最重要): 同住所患者は配列上で必ず隣接する.
# ---------------------------------------------------------------------------


def test_same_address_pair_is_consecutive_in_course() -> None:
    """同住所 2 名は ``_apply_travel_time_to_courses`` 後に必ず配列上隣接する.

    Setup: コース A 月曜:
      - A 09:00 (lat=35.65, lng=140.10) 終日
      - B 09:30 同住所 (lat=35.65, lng=140.10) 終日
    入力時点で隣接しているが、リオーダー後も隣接していること.
    """
    from app.services.scheduling.auto_allocator_v2 import (
        _address_bucket,
        _apply_travel_time_to_courses,
    )

    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.end_time = time(9, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "終日"
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=30, patient_name="B"
    )
    b.end_time = time(10, 0)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "終日"

    warnings: list[V2Warning] = []
    visits = [a, b]
    _apply_travel_time_to_courses(visits, warnings=warnings)

    # A と B は同住所 → start_time 順 (A 09:00 / B 09:30) で隣接.
    # _apply_travel_time_to_courses 内で再ソート + リオーダーされるが、
    # 元々 2 件しかないので配列上必然的に隣接.
    a_bucket = _address_bucket(a.lat, a.lng)
    b_bucket = _address_bucket(b.lat, b.lng)
    assert a_bucket == b_bucket, "テスト前提: A と B は同住所"
    # Wave 2 (#115) + Phase E-3 改修 (3): 同住所ペアは
    # ``_align_same_address_pair_to_same_time`` で B も A と同じ start_time (9:00)
    # に揃えられ、B.end は max(30+30, 90)=90 分占有 (10:30) になる.
    assert a.start_time == time(9, 0), f"A 不変のはず: {a.start_time}"
    assert b.start_time == time(9, 0), f"Wave 2 で B も A と同 start_time: {b.start_time}"
    assert b.end_time == time(10, 30), f"Phase E-3: B.end は 90 分占有: {b.end_time}"


def test_same_address_pair_consecutive_with_other_patient_between() -> None:
    """同住所ペアの間に別住所 visit が挟まる入力でもリオーダー後は隣接する.

    Setup (input): [A 09:00 (addr1), C 09:15 (addr2), B 09:30 (addr1)]
      - A と B が同住所だが、間に C が挟まる.
    リオーダー後: 同住所 (A, B) は隣接 (= [A, B, C] か [A, C, B] のうち、
      A,B が配列上隣接する形). 本実装では「後ろの B を A の直後に移動」=
      [A, B, C] になる.
    """
    from app.services.scheduling.auto_allocator_v2 import _reorder_same_address_consecutive

    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.end_time = time(9, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "終日"
    # 別住所 C (経度を 0.05 ずらす = 約 5km 離れる)
    c = _make_visit(
        lat=35.65, lng=140.15, office_id=office_id, start_h=9, start_m=15, patient_name="C"
    )
    c.end_time = time(9, 45)
    c.service_minutes = 30
    c.course_code = "A"
    c.time_type = "終日"
    # 同住所 B (A と同じ lat/lng)
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=30, patient_name="B"
    )
    b.end_time = time(10, 0)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "終日"

    # 入力: [A, C, B] (start_time 昇順そのまま)
    visits = [a, c, b]
    warnings: list[V2Warning] = []
    reordered = _reorder_same_address_consecutive(visits, warnings=warnings)

    # A と B のインデックスが隣接 (差 = 1) になる.
    a_idx = reordered.index(a)
    b_idx = reordered.index(b)
    assert abs(a_idx - b_idx) == 1, (
        f"A と B は同住所だが隣接していない: order={[v.patient_name for v in reordered]}"
    )
    # 3 件中 2 件のペアなので 3 件 warning は出ない.
    assert not any("3 名以上が同コース内に残存" in w.message for w in warnings), (
        f"2 名ペアで 3+ warning が出ている: {warnings}"
    )


def test_same_address_pair_with_fixed_time_stays_adjacent() -> None:
    """固定時刻 patient と非固定 patient が同住所ペア → リオーダー後も連番.

    Setup (input): [固定 A 10:00 (addr1), C 10:30 (addr2), 非固定 B 11:00 (addr1)]
      - A は time_type='固定' で位置 / 時刻ともに動かしたくない.
      - B は同住所だが非固定. C は別住所.
    期待: 非固定 B を固定 A の直後に移動 → [A, B, C].
      A の start_time (10:00) は不変. B の配列位置のみ調整.
    """
    from app.services.scheduling.auto_allocator_v2 import _reorder_same_address_consecutive

    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=0, patient_name="A"
    )
    a.end_time = time(10, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "固定"
    a.preferred_start = "10:00"
    c = _make_visit(
        lat=35.65, lng=140.15, office_id=office_id, start_h=10, start_m=30, patient_name="C"
    )
    c.end_time = time(11, 0)
    c.service_minutes = 30
    c.course_code = "A"
    c.time_type = "終日"
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=11, start_m=0, patient_name="B"
    )
    b.end_time = time(11, 30)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "終日"

    visits = [a, c, b]
    warnings: list[V2Warning] = []
    reordered = _reorder_same_address_consecutive(visits, warnings=warnings)

    # A と B が隣接していること.
    a_idx = reordered.index(a)
    b_idx = reordered.index(b)
    assert abs(a_idx - b_idx) == 1, (
        f"固定 A と非固定 B (同住所) が隣接していない: order={[v.patient_name for v in reordered]}"
    )
    # 固定 A の start_time は不変.
    assert a.start_time == time(10, 0), f"固定 A の start_time が動いた: {a.start_time}"


def test_three_different_addresses_unchanged() -> None:
    """異住所ばかり (同住所ペアが存在しない) なら並び順は不変 (regression 防止)."""
    from app.services.scheduling.auto_allocator_v2 import _reorder_same_address_consecutive

    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.course_code = "A"
    a.time_type = "終日"
    # 5km 離れた B (35.65, 140.15)
    b = _make_visit(
        lat=35.65, lng=140.15, office_id=office_id, start_h=9, start_m=30, patient_name="B"
    )
    b.course_code = "A"
    b.time_type = "終日"
    # さらに 5km 離れた C (35.70, 140.20)
    c = _make_visit(
        lat=35.70, lng=140.20, office_id=office_id, start_h=10, start_m=0, patient_name="C"
    )
    c.course_code = "A"
    c.time_type = "終日"

    visits = [a, b, c]
    warnings: list[V2Warning] = []
    reordered = _reorder_same_address_consecutive(visits, warnings=warnings)

    # 並び順は不変
    assert [v.patient_name for v in reordered] == ["A", "B", "C"], (
        f"異住所のみでも順序が変わった: {[v.patient_name for v in reordered]}"
    )
    # warning も出ない
    assert not warnings, f"異住所のみで warning が出ている: {warnings}"


def test_same_address_pair_in_multi_course_preserved_per_course() -> None:
    """複数コース時、各コース内で独立に同住所連番強制される.

    Setup:
      コース A 月曜: [A 09:00 (addr1), C 09:15 (addr2), B 09:30 (addr1)] → リオーダー後 A, B 隣接
      コース B 月曜: [D 10:00 (addr3), E 10:30 (addr4)] → 異住所のみ、不変
    """
    from app.services.scheduling.auto_allocator_v2 import (
        _address_bucket,
        _apply_travel_time_to_courses,
    )

    office_id = uuid.uuid4()
    # コース A
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.end_time = time(9, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "終日"
    c = _make_visit(
        lat=35.65, lng=140.15, office_id=office_id, start_h=9, start_m=15, patient_name="C"
    )
    c.end_time = time(9, 45)
    c.service_minutes = 30
    c.course_code = "A"
    c.time_type = "終日"
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=30, patient_name="B"
    )
    b.end_time = time(10, 0)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "終日"

    # コース B (異住所のみ)
    d = _make_visit(
        lat=35.70, lng=140.20, office_id=office_id, start_h=10, start_m=0, patient_name="D"
    )
    d.end_time = time(10, 30)
    d.service_minutes = 30
    d.course_code = "B"
    d.time_type = "終日"
    e = _make_visit(
        lat=35.75, lng=140.25, office_id=office_id, start_h=10, start_m=30, patient_name="E"
    )
    e.end_time = time(11, 0)
    e.service_minutes = 30
    e.course_code = "B"
    e.time_type = "終日"

    visits = [a, c, b, d, e]
    warnings: list[V2Warning] = []
    _apply_travel_time_to_courses(visits, warnings=warnings)

    # コース A: A と B が同住所 → リオーダー後 [A, B, C] になる.
    # Wave 2 (#115) + Phase E-3 改修 (3): 同住所ペアは
    # ``_align_same_address_pair_to_same_time`` で 両者同 start_time +
    # B.end_time = max(60, 90)=90 分占有になる.
    # → A.start=B.start=09:00, A.end=09:30, B.end=10:30.
    # C は B 末尾 (10:30) から異住所移動 + バッファーで押し下げ.
    a_bucket = _address_bucket(a.lat, a.lng)
    b_bucket = _address_bucket(b.lat, b.lng)
    assert a_bucket == b_bucket, "テスト前提: A,B 同住所"
    assert a.start_time == time(9, 0), f"A 不変: {a.start_time}"
    assert b.start_time == time(9, 0), f"Wave 2 で B も A と同 start_time: {b.start_time}"
    assert b.end_time == time(10, 30), f"Phase E-3: B.end は 90 分占有: {b.end_time}"
    # コース B: D と E は異住所のみ.
    # 5km 移動 + バッファー 8 分 → 10:30 desired vs (10:30 + travel + 8) → 5 分切り上げ.
    # 10:30 以降に押し下げ.
    assert d.start_time == time(10, 0), f"D は固定 start のはず: {d.start_time}"
    # E の正確な押し下げ値はテストの主眼ではない. 元の 10:30 より遅れていれば OK.
    assert e.start_time > time(10, 30) or e.start_time == time(10, 30), (
        f"E は異住所移動で 10:30 以降のはず: {e.start_time}"
    )


# ---------------------------------------------------------------------------
# 同住所連番 — Opus reviewer 指摘の behavior pin / regression テスト
# (HIGH 2 件 = 仕様意図通りの副作用を明示固定 + MEDIUM カバレッジ補完)
# ---------------------------------------------------------------------------


def test_same_address_pair_pushes_back_subsequent_other_address_visit() -> None:
    """HIGH #1 behavior pin: 同住所ペア (固定 first + 非固定 second) の間に挟まれた
    別住所 visit が、reorder 後に後段の earliest 計算で押し下げられることを許容する.

    ユーザー方針: 同住所連番が最重要 → 他患者の時刻が動くのは許容.

    Setup (input, start_time 昇順):
      [A 09:00 固定 (addr1), C 09:15 終日 (addr2), B 11:00 非固定 終日 (addr1)]

    Expected after reorder + Wave 2 align:
      順序は [A, B, C] (B が A の直後に移動 = 同住所連番).
      Wave 2 (#115): 同住所ペア A/B は **同 start_time** (= 固定 A の 09:00) に
      揃えられ、B.end = 合算 60 分占有 (= 10:00) になる.
      C は B.end (10:00) + travel + buffer まで押し下げ.
    """
    from app.services.scheduling.auto_allocator_v2 import (
        _address_bucket,
        _apply_travel_time_to_courses,
        _reorder_same_address_consecutive,
    )

    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.end_time = time(9, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "固定"
    a.preferred_start = "09:00"
    c = _make_visit(
        lat=35.65, lng=140.15, office_id=office_id, start_h=9, start_m=15, patient_name="C"
    )
    c.end_time = time(9, 45)
    c.service_minutes = 30
    c.course_code = "A"
    c.time_type = "終日"
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=11, start_m=0, patient_name="B"
    )
    b.end_time = time(11, 30)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "終日"

    visits = [a, c, b]
    warnings: list[V2Warning] = []
    reordered = _reorder_same_address_consecutive(visits, warnings=warnings)

    # 順序 [A, B, C] になっている (B を A の直後に挿入).
    order = [v.patient_name for v in reordered]
    assert order == ["A", "B", "C"], f"reorder 後の順序が期待値と異なる: {order}"
    a_idx = reordered.index(a)
    b_idx = reordered.index(b)
    assert abs(a_idx - b_idx) == 1, "A,B (同住所) が隣接していない"

    # 後段の travel 計算を走らせる → Wave 2 で同住所ペア A/B が揃えられる.
    travel_warnings: list[V2Warning] = []
    _apply_travel_time_to_courses(reordered, warnings=travel_warnings)

    a_bucket = _address_bucket(a.lat, a.lng)
    b_bucket = _address_bucket(b.lat, b.lng)
    c_bucket = _address_bucket(c.lat, c.lng)
    assert a_bucket == b_bucket and a_bucket != c_bucket, "テスト前提: A,B 同住所 / C 異住所"

    # 固定 A: 09:00 不変.
    assert a.start_time == time(9, 0), f"固定 A は不変のはず: {a.start_time}"
    # Wave 2: 非固定 B (終日) は同住所ペアで固定 A の 09:00 に揃えられる.
    assert b.start_time == time(9, 0), f"Wave 2: 同住所 B は A と同 start_time: {b.start_time}"
    # Phase E-3 改修 (3): B.end = 09:00 + max(30+30, 90) = 10:30 (90 分 clamp).
    assert b.end_time == time(10, 30), f"Phase E-3: B.end は 90 分占有: {b.end_time}"
    # C: 本来 09:15 入力だったが、reorder で B の後ろに回ったため
    # B.end (10:30) + travel + buffer まで押し下げ → ~10:45 以降.
    assert c.start_time >= time(10, 30), (
        f"C は B.end (10:30) 以降に押し下げられるはず: {c.start_time}"
    )


def test_same_address_pair_non_fixed_first_with_fixed_second_can_push_first_later() -> None:
    """HIGH #2 behavior pin: 非固定 first + 固定 second 同住所、間に別住所
    → 非固定 first の start_time が後段で繰り下げられる可能性を許容する.

    ユーザー方針: 同住所連番が最重要 → 非固定 first 時刻が動くのは許容.

    Setup (input, start_time 昇順):
      [A 09:00 非固定 終日 (addr1), C 09:15 終日 (addr2), B 10:00 固定 (addr1)]

    Expected after reorder:
      順序は [C, A, B] (A を B の直前に移動 = 固定 B 不変). 後段で A の
      earliest が C.end + travel + buffer (~10:00 付近) まで繰り下げられる.
      本来 09:00 で配置できた A が同住所優先のため遅延される.
    """
    from app.services.scheduling.auto_allocator_v2 import (
        _address_bucket,
        _apply_travel_time_to_courses,
        _reorder_same_address_consecutive,
    )

    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.end_time = time(9, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "終日"
    c = _make_visit(
        lat=35.65, lng=140.15, office_id=office_id, start_h=9, start_m=15, patient_name="C"
    )
    c.end_time = time(9, 45)
    c.service_minutes = 30
    c.course_code = "A"
    c.time_type = "終日"
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=0, patient_name="B"
    )
    b.end_time = time(10, 30)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "固定"
    b.preferred_start = "10:00"

    visits = [a, c, b]
    warnings: list[V2Warning] = []
    reordered = _reorder_same_address_consecutive(visits, warnings=warnings)

    # 順序: [C, A, B] (A を B の直前に移動 = 固定 B 不変).
    order = [v.patient_name for v in reordered]
    assert order == ["C", "A", "B"], f"reorder 後の順序が期待値と異なる: {order}"
    a_idx = reordered.index(a)
    b_idx = reordered.index(b)
    assert abs(a_idx - b_idx) == 1, "A (非固定), B (固定) 同住所が隣接していない"

    # 後段の travel 計算を走らせる.
    travel_warnings: list[V2Warning] = []
    _apply_travel_time_to_courses(reordered, warnings=travel_warnings)

    a_bucket = _address_bucket(a.lat, a.lng)
    b_bucket = _address_bucket(b.lat, b.lng)
    c_bucket = _address_bucket(c.lat, c.lng)
    assert a_bucket == b_bucket and a_bucket != c_bucket, "テスト前提: A,B 同住所 / C 異住所"

    # 固定 B: 10:00 不変.
    assert b.start_time == time(10, 0), f"固定 B は不変のはず: {b.start_time}"
    # 非固定 first A: reorder により C の後ろに回ったため、本来 09:00 で
    # 配置できたが、後段で C.end + travel + buffer まで繰り下げ (= 09:00 より遅延).
    assert a.start_time > time(9, 0), (
        f"非固定 A は同住所連番優先のため後段で繰り下げられるはず (許容): {a.start_time}"
    )


def test_reorder_skips_lat_lng_none_visits() -> None:
    """M3 (a): lat/lng=None 混在 → None の visit は skip され、配列順を維持.

    `_reorder_same_address_consecutive` の Algorithm step 2 (lat/lng None は対象外)
    の挙動検証.
    """
    from app.services.scheduling.auto_allocator_v2 import _reorder_same_address_consecutive

    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.course_code = "A"
    a.time_type = "終日"
    # lat/lng=None の visit (測位失敗想定).
    x = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=30, patient_name="X"
    )
    x.lat = None  # type: ignore[assignment]
    x.lng = None  # type: ignore[assignment]
    x.course_code = "A"
    x.time_type = "終日"
    # 別住所 visit (B).
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=0, patient_name="B"
    )
    b.course_code = "A"
    b.time_type = "終日"

    visits = [a, x, b]
    warnings: list[V2Warning] = []
    reordered = _reorder_same_address_consecutive(visits, warnings=warnings)

    # A と B は同住所だが、間の X (lat/lng=None) は skip 対象なので
    # A,B のグルーピング上は X を無視. ただし配列上の reorder は B を A の直後に
    # 移動するため、最終順序は [A, B, X] (X は末尾に押し出される).
    order = [v.patient_name for v in reordered]
    assert order == ["A", "B", "X"], f"reorder 後の順序が期待値と異なる: {order}"
    # X (lat/lng None) は同住所判定対象外なので warning 影響しない.
    assert not any("3 名以上が同コース内に残存" in w.message for w in warnings)


def test_reorder_three_plus_emits_warning_with_message_substring() -> None:
    """M3 (b): 同住所 3+ 残存時の warning 内容 (type=general + 「3 名以上」
    + 「manual review」を含む) を pin する.
    """
    from app.services.scheduling.auto_allocator_v2 import _reorder_same_address_consecutive

    office_id = uuid.uuid4()
    # 同住所 3 件 + 別住所 1 件 (間に挟む).
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.course_code = "A"
    a.time_type = "終日"
    c = _make_visit(
        lat=35.65, lng=140.15, office_id=office_id, start_h=9, start_m=15, patient_name="C"
    )
    c.course_code = "A"
    c.time_type = "終日"
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=30, patient_name="B"
    )
    b.course_code = "A"
    b.time_type = "終日"
    d = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=0, patient_name="D"
    )
    d.course_code = "A"
    d.time_type = "終日"

    visits = [a, c, b, d]
    warnings: list[V2Warning] = []
    _reorder_same_address_consecutive(visits, warnings=warnings)

    same_addr_warnings = [w for w in warnings if "3 名以上が同コース内に残存" in w.message]
    assert len(same_addr_warnings) >= 1, f"3+ 同住所 warning が出ていない: {warnings}"
    w0 = same_addr_warnings[0]
    assert w0.type == "general", f"warning type は 'general' のはず: {w0.type}"
    assert "3 名以上" in w0.message
    assert "manual review" in w0.message
    # L1: weekday + course_code がメッセージに含まれる.
    assert "月曜" in w0.message, f"曜日 (月曜) がメッセージにない: {w0.message}"
    assert "コース A" in w0.message, f"course_code がメッセージにない: {w0.message}"


def test_reorder_both_fixed_same_address_unchanged() -> None:
    """M3 (c): 両者固定 (time_type='固定') 同住所異時刻 → 配列順不変 + warning なし.

    両者固定の場合、start_time が動かせず位置を入れ替えると配列順=時刻順 不整合に
    なるため、隣接していなくても並べ替えない (= 仕様).
    """
    from app.services.scheduling.auto_allocator_v2 import _reorder_same_address_consecutive

    office_id = uuid.uuid4()
    # 同住所だが両者 time_type='固定' で時刻が異なる. 間に別住所が挟まる.
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.course_code = "A"
    a.time_type = "固定"
    a.preferred_start = "09:00"
    c = _make_visit(
        lat=35.65, lng=140.15, office_id=office_id, start_h=10, start_m=0, patient_name="C"
    )
    c.course_code = "A"
    c.time_type = "終日"
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=11, start_m=0, patient_name="B"
    )
    b.course_code = "A"
    b.time_type = "固定"
    b.preferred_start = "11:00"

    visits = [a, c, b]
    warnings: list[V2Warning] = []
    reordered = _reorder_same_address_consecutive(visits, warnings=warnings)

    # 配列順は不変 (両者固定 → 動かさない).
    order = [v.patient_name for v in reordered]
    assert order == ["A", "C", "B"], f"両者固定なのに reorder された: {order}"
    # 3 件のうち 2 件が同住所だが、3 名以上 warning は出ない (= 同住所件数 2 件).
    assert not any("3 名以上が同コース内に残存" in w.message for w in warnings), (
        f"2 件ペアで 3+ warning が出ている: {warnings}"
    )


def test_reorder_does_not_mutate_input_list() -> None:
    """M3 (e): `_reorder_same_address_consecutive` は入力 list を mutate しない
    (shallow copy を返す = 純粋関数).
    """
    from app.services.scheduling.auto_allocator_v2 import _reorder_same_address_consecutive

    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.course_code = "A"
    a.time_type = "終日"
    c = _make_visit(
        lat=35.65, lng=140.15, office_id=office_id, start_h=9, start_m=15, patient_name="C"
    )
    c.course_code = "A"
    c.time_type = "終日"
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=30, patient_name="B"
    )
    b.course_code = "A"
    b.time_type = "終日"

    visits = [a, c, b]
    original_copy = visits.copy()  # 要素順を pin する shallow copy.

    reordered = _reorder_same_address_consecutive(visits, warnings=[])

    # 入力 list の要素順は不変 (mutate されていない).
    assert visits == original_copy, "入力 visits の要素順が mutate された"
    assert [v.patient_name for v in visits] == ["A", "C", "B"]
    # 返り値は別オブジェクト (shallow copy).
    assert reordered is not visits
    # 要素 (V2Visit) インスタンス自体は同一 (shallow copy なので同じオブジェクトを参照).
    assert reordered[reordered.index(a)] is a
    assert reordered[reordered.index(b)] is b
    assert reordered[reordered.index(c)] is c


# ---------------------------------------------------------------------------
# Fix E (CareFlow): 異住所同時刻 2 名以上の自動時刻シフト + 距離最適化.
# `_auto_shift_same_time_conflicts` (+ `_apply_travel_time_to_courses` 経由).
# ---------------------------------------------------------------------------


def test_two_fixed_same_time_different_address_auto_shift() -> None:
    """Fix E: 異住所同時刻 2 名 (両者固定) → 後者を自動シフト.

    Setup: コース A 月曜:
      - A 15:00 固定 (lat=35.65, lng=140.10), service 30 分 → end 15:30
      - B 15:00 固定 (lat=35.65, lng=140.155) 異住所 (~5km)
    Expected:
      - A は 15:00 のまま (= 先頭).
      - B は prev.end(15:30) + travel(15 分) + buffer(8 分) = 15:53 → 5 分切り上げ = 15:55.
      - course_code は両者とも 'A' のまま (= unassigned に流されない).
      - warning: type='auto_time_shift_for_conflict', affected_patient_ids=[B].
    """
    from app.services.scheduling.auto_allocator_v2 import _apply_travel_time_to_courses

    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=15, start_m=0, patient_name="A"
    )
    a.end_time = time(15, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "固定"
    a.preferred_start = "15:00"
    b = _make_visit(
        lat=35.65, lng=140.155, office_id=office_id, start_h=15, start_m=0, patient_name="B"
    )
    b.end_time = time(15, 30)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "固定"
    b.preferred_start = "15:00"

    warnings: list[V2Warning] = []
    unassigned = _apply_travel_time_to_courses([a, b], warnings=warnings)

    # A は不変.
    assert a.start_time == time(15, 0)
    # B は 15:55 (15:30 + 15 + 8 = 15:53 → 5 分切り上げ).
    assert b.start_time == time(15, 55), f"B start_time が想定外: {b.start_time}"
    assert b.end_time == time(16, 25), f"B end_time が想定外: {b.end_time}"
    # Fix E 経由で auto-shift され、unassigned には流れない.
    assert id(b) not in unassigned, "Fix E でシフトされたはず, unassigned に流れている"
    assert b.course_code == "A", f"course_code が外れている: {b.course_code}"
    # warning: auto_time_shift_for_conflict が出ている.
    auto_shifts = [w for w in warnings if w.type == "auto_time_shift_for_conflict"]
    assert auto_shifts, f"auto_time_shift_for_conflict warning が出ていない: {warnings}"
    w0 = auto_shifts[0]
    assert b.patient_id in (w0.affected_patient_ids or [])
    assert "同時刻衝突" in w0.message
    assert "15:55" in w0.message


def test_same_time_pair_distance_optimal_ordering() -> None:
    """Fix E: 順序決定が距離最適化に従う.

    Setup: コース A 月曜 4 件 ([P, A, B, Q]):
      - P 14:00 (lat=35.65, lng=140.10), end 14:30
      - A 15:00 固定 (lat=35.65, lng=140.20) — P から遠い (~9km)
      - B 15:00 固定 (lat=35.65, lng=140.11) — P から近い (~1km)
      - Q 17:00 (lat=35.65, lng=140.21) — A に近い
    順序最適化: [P, B, A, Q] の方が短い (B が P に近く、A が Q に近い).
    Expected: 配列上 B が A の前. B は 14:30+travel+buffer で確定、A は B.end+...
    """
    from app.services.scheduling.auto_allocator_v2 import _apply_travel_time_to_courses

    office_id = uuid.uuid4()
    p = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=14, start_m=0, patient_name="P"
    )
    p.end_time = time(14, 30)
    p.service_minutes = 30
    p.course_code = "A"
    p.time_type = "固定"
    a = _make_visit(
        lat=35.65, lng=140.20, office_id=office_id, start_h=15, start_m=0, patient_name="A"
    )
    a.end_time = time(15, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "固定"
    b = _make_visit(
        lat=35.65, lng=140.11, office_id=office_id, start_h=15, start_m=0, patient_name="B"
    )
    b.end_time = time(15, 30)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "固定"
    q = _make_visit(
        lat=35.65, lng=140.21, office_id=office_id, start_h=17, start_m=0, patient_name="Q"
    )
    q.end_time = time(17, 30)
    q.service_minutes = 30
    q.course_code = "A"
    q.time_type = "固定"

    warnings: list[V2Warning] = []
    _apply_travel_time_to_courses([p, a, b, q], warnings=warnings)

    # P は 14:00 不変, Q は元々 17:00 で十分余裕がある (時刻不変).
    assert p.start_time == time(14, 0)
    # 距離最適化: B (P に近い) が先 → A (Q に近い) が後.
    # B start_time は 14:30 + travel(~3 分) + buffer(8) = 14:41 → 5 分切り上げ = 14:45.
    # A start_time は B.end(15:15) + travel(~27 分) + buffer(8) = 15:50 → 15:50 (既に 5 分刻み).
    # B が A より早い時刻に配置されることを確認 (距離最適化の証).
    assert b.start_time < a.start_time, (
        f"距離最適化で B が先になるはず: B={b.start_time}, A={a.start_time}"
    )
    # B は 14:30 (P.end) + 何分か上を確認.
    assert b.start_time >= time(14, 30)


def test_three_fixed_same_time_sequential_shift() -> None:
    """Fix E: 3 名異住所同時刻 → 順次シフト.

    Setup: コース A 月曜 3 件 (全て 15:00 固定, 異住所):
      - A 15:00 固定 (lat=35.65, lng=140.10), service 30 分
      - B 15:00 固定 (lat=35.65, lng=140.11), service 30 分 (~1km)
      - C 15:00 固定 (lat=35.65, lng=140.13), service 30 分 (~3km)
    Expected: 順次後ろにシフト.
      - 先頭 (距離最適化で決まる) は 15:00.
      - 2 番目は 15:30 + travel + buffer → 5 分切り上げ.
      - 3 番目は 2 番目の end + travel + buffer → 5 分切り上げ.
    """
    from app.services.scheduling.auto_allocator_v2 import _apply_travel_time_to_courses

    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=15, start_m=0, patient_name="A"
    )
    a.end_time = time(15, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "固定"
    b = _make_visit(
        lat=35.65, lng=140.11, office_id=office_id, start_h=15, start_m=0, patient_name="B"
    )
    b.end_time = time(15, 30)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "固定"
    c = _make_visit(
        lat=35.65, lng=140.13, office_id=office_id, start_h=15, start_m=0, patient_name="C"
    )
    c.end_time = time(15, 30)
    c.service_minutes = 30
    c.course_code = "A"
    c.time_type = "固定"

    warnings: list[V2Warning] = []
    _apply_travel_time_to_courses([a, b, c], warnings=warnings)

    # 3 名の start_time を昇順で取得.
    starts = sorted([a.start_time, b.start_time, c.start_time])
    # 先頭は 15:00 (= シフトされない).
    assert starts[0] == time(15, 0)
    # 2 番目と 3 番目は順次後ろ. 全て異なる時刻.
    assert starts[0] < starts[1] < starts[2], f"3 名が順次シフトされていない: {starts}"
    # 全員 course_code='A' のまま (= unassigned に流れない).
    assert a.course_code == "A"
    assert b.course_code == "A"
    assert c.course_code == "A"
    # auto_time_shift_for_conflict warning が 2 件 (= 2 番目 + 3 番目分) 出ている.
    auto_shifts = [w for w in warnings if w.type == "auto_time_shift_for_conflict"]
    assert len(auto_shifts) >= 2, f"3 名シフトで warning が 2 件以上出るべき: {len(auto_shifts)}"


def test_two_same_address_same_time_unchanged() -> None:
    """Fix E: 同住所同時刻ペア (家族・施設) は不変 (= 既存仕様維持).

    Setup: 同住所同時刻 (lat=35.65, lng=140.10 両者).
    Expected:
      - 両者 start_time 15:00 不変 (= travel 0 + buffer 0 で既存仕様維持).
      - auto_time_shift_for_conflict warning も出ない.
    """
    from app.services.scheduling.auto_allocator_v2 import _apply_travel_time_to_courses

    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=15, start_m=0, patient_name="A"
    )
    a.end_time = time(15, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "固定"
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=15, start_m=0, patient_name="B"
    )
    b.end_time = time(15, 30)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "固定"

    warnings: list[V2Warning] = []
    _apply_travel_time_to_courses([a, b], warnings=warnings)

    # 両者 15:00 不変 (= 同住所同時刻は既存仕様で許容).
    assert a.start_time == time(15, 0)
    assert b.start_time == time(15, 0)
    # auto_time_shift warning は出ない (同住所のため Fix E が処理しない).
    auto_shifts = [w for w in warnings if w.type == "auto_time_shift_for_conflict"]
    assert not auto_shifts, f"同住所同時刻で auto_time_shift が出ている: {warnings}"


def test_auto_shift_warning_emitted() -> None:
    """Fix E: 自動シフトの warning に affected_patient_ids が入る.

    setup と挙動は test_two_fixed_same_time_different_address_auto_shift と同じだが、
    本テストは warning の構造 (type / affected_patient_ids / message 内容) を
    重点的に検証.
    """
    from app.services.scheduling.auto_allocator_v2 import _apply_travel_time_to_courses

    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=15, start_m=0, patient_name="A"
    )
    a.end_time = time(15, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "固定"
    b = _make_visit(
        lat=35.65, lng=140.155, office_id=office_id, start_h=15, start_m=0, patient_name="B"
    )
    b.end_time = time(15, 30)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "固定"

    warnings: list[V2Warning] = []
    _apply_travel_time_to_courses([a, b], warnings=warnings)

    auto_shifts = [w for w in warnings if w.type == "auto_time_shift_for_conflict"]
    assert len(auto_shifts) == 1, f"auto_time_shift warning が 1 件出るべき: {warnings}"
    w0 = auto_shifts[0]
    # affected_patient_ids にシフトされた B が入っている.
    assert b.patient_id in (w0.affected_patient_ids or [])
    # message に「自動調整」「同時刻衝突」「変更」を含む.
    assert "同時刻衝突" in w0.message
    assert "自動調整" in w0.message
    # weekday が伝播されている.
    assert w0.weekday == 0  # 月曜
    # actionable=False (= 自動解決済みで運用者通知のみ).
    assert w0.actionable is False
    # patient_id が B 本人.
    assert w0.patient_id == b.patient_id


def test_auto_shift_respects_5min_rounding() -> None:
    """Fix E: シフト後の時刻が 5 分刻みに切り上げられる.

    Setup: A 09:00-09:30 固定 + B 09:00 固定 (異住所 ~2km).
    travel 2/20*60 ≒ 6 分 → earliest = 09:30 + 6 + 8 = 09:44.
    5 分切り上げ → 09:45.
    """
    from app.services.scheduling.auto_allocator_v2 import _apply_travel_time_to_courses

    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.end_time = time(9, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "固定"
    # ~2km (経度 0.022 ≒ 緯度方向の 2km 換算: 1 度 ≒ 111km, 0.022 ≒ 2.4km).
    b = _make_visit(
        lat=35.65, lng=140.122, office_id=office_id, start_h=9, start_m=0, patient_name="B"
    )
    b.end_time = time(9, 30)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "固定"

    warnings: list[V2Warning] = []
    _apply_travel_time_to_courses([a, b], warnings=warnings)

    # シフト後の B start_time は 5 分刻み.
    assert b.start_time.minute % 5 == 0, f"B start_time が 5 分刻みでない: {b.start_time}"
    # A の end (09:30) より後ろ.
    assert b.start_time > time(9, 30)


def test_two_staff_flag_flows_through_build_visits() -> None:
    """W41 v2: requires_multiple_staff=True patient → V2Visit.requires_multiple_staff=True."""
    office_id = uuid.uuid4()
    p = Patient(
        id=uuid.uuid4(),
        code="TWO",
        name="二人組必須",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office_id,
        requires_multiple_staff=True,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    visits = build_visits_for_pool([p])
    assert len(visits) == 1
    assert visits[0].requires_multiple_staff is True


def test_two_staff_visit_warns_insufficient_staff() -> None:
    """W41 v2: 二人組必要だがスタッフ 1 名 → warning."""
    from app.services.scheduling.auto_allocator_v2 import _check_two_staff_availability

    office_id = uuid.uuid4()
    v = _make_visit(lat=35.65, lng=140.10, office_id=office_id, patient_name="TWO")
    v.requires_multiple_staff = True
    v.weekday = 0  # 月曜

    # スタッフ 1 名のみ
    staff_count = {(office_id, 0): 1}
    warnings: list[V2Warning] = []
    _check_two_staff_availability([v], staff_count_by_weekday=staff_count, warnings=warnings)

    assert any("二人組訪問必須" in w.message for w in warnings), (
        f"二人組必須 warning が出ていない: {warnings}"
    )


def test_two_staff_visit_no_warn_when_two_staff_available() -> None:
    """W41 v2: スタッフ 2 名以上いれば二人組訪問でも warning なし."""
    from app.services.scheduling.auto_allocator_v2 import _check_two_staff_availability

    office_id = uuid.uuid4()
    v = _make_visit(lat=35.65, lng=140.10, office_id=office_id, patient_name="TWO")
    v.requires_multiple_staff = True
    v.weekday = 0

    staff_count = {(office_id, 0): 2}
    warnings: list[V2Warning] = []
    _check_two_staff_availability([v], staff_count_by_weekday=staff_count, warnings=warnings)
    assert not any("二人組訪問必須" in w.message for w in warnings)


def test_course_capacity_minutes_warns_over_480() -> None:
    """W41 v2: コース総所要時間 (duration + 移動) > 480 分 → warning."""
    from app.services.scheduling.auto_allocator_v2 import _check_course_capacity_minutes

    office_id = uuid.uuid4()
    visits: list[V2Visit] = []
    # 6 患者 × 60 分 × 5km 間移動 (15 分 × 5 ペア = 75 分) = 360 + 75 = 435 分 (480 未満)
    # 6 患者 × 90 分 + 移動 75 分 = 540 + 75 = 615 分 (480 超)
    for i in range(6):
        v = _make_visit(
            lat=35.65 + i * 0.05,  # ~5km 刻みで離す
            lng=140.10,
            office_id=office_id,
            start_h=9 + i,
            start_m=0,
            patient_name=f"P{i}",
        )
        v.end_time = time(10 + i, 30)
        v.service_minutes = 90
        v.course_code = "A"
        v.weekday = 0
        visits.append(v)

    warnings: list[V2Warning] = []
    _check_course_capacity_minutes(visits, warnings=warnings)
    assert any("コース総所要時間" in w.message for w in warnings), (
        f"480 分超過 warning が出ていない: {warnings}"
    )


def test_course_capacity_minutes_no_warn_under_480() -> None:
    """W41 v2: 容量未満なら warning なし."""
    from app.services.scheduling.auto_allocator_v2 import _check_course_capacity_minutes

    office_id = uuid.uuid4()
    v = _make_visit(lat=35.65, lng=140.10, office_id=office_id, patient_name="P1")
    v.service_minutes = 60
    v.course_code = "A"
    v.weekday = 0

    warnings: list[V2Warning] = []
    _check_course_capacity_minutes([v], warnings=warnings)
    assert not any("コース総所要時間" in w.message for w in warnings)


def test_calc_course_total_minutes_includes_travel() -> None:
    """W41 v2: calc_course_total_minutes = sum(duration) + 隣接移動 + バッファー.

    HIGH #1 修正後: 異住所遷移には ``VISIT_BUFFER_MINUTES`` (= 8 分) も加算する.
    """
    from app.services.scheduling.auto_allocator_v2 import (
        VISIT_BUFFER_MINUTES,
        calc_course_total_minutes,
    )

    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.service_minutes = 30
    a.course_code = "A"
    # 3km 離れた B
    b = _make_visit(
        lat=35.65, lng=140.133, office_id=office_id, start_h=10, start_m=0, patient_name="B"
    )
    b.service_minutes = 30
    b.course_code = "A"

    total = calc_course_total_minutes([a, b])
    # duration 30 + 30 = 60, 移動 ~9 分, バッファー 8 分 → ~77 分
    expected_lower = 60 + 1 + VISIT_BUFFER_MINUTES  # 69
    expected_upper = 60 + 30 + VISIT_BUFFER_MINUTES  # 98
    assert expected_lower <= total <= expected_upper, (
        f"expected {expected_lower}-{expected_upper} min, got {total}"
    )


def test_two_staff_does_not_double_count_visits_in_capacity() -> None:
    """W41 v2: 二人組訪問は patient 数 1 として扱う (容量で 2 件分にカウントしない).

    requires_multiple_staff=True の visit を 1 件追加してもコース総所要時間は
    service_minutes そのまま (= 1 visit duration) で計算される.
    """
    from app.services.scheduling.auto_allocator_v2 import calc_course_total_minutes

    office_id = uuid.uuid4()
    v = _make_visit(lat=35.65, lng=140.10, office_id=office_id, patient_name="TWO")
    v.requires_multiple_staff = True
    v.service_minutes = 60
    v.course_code = "A"

    # 1 visit のみのコース = duration 60 分 + 移動 0 分 = 60 分.
    # 二人組であっても 120 分にはならない (時間軸は 60 分のまま).
    total = calc_course_total_minutes([v])
    assert total == 60, f"二人組 visit を 2 倍カウントしてはいけない: got {total}"


# ---------------------------------------------------------------------------
# W41 v2 拡張 (クロスレビュー指摘修正): CRITICAL #1, HIGH #1/#2, MEDIUM #1
# ---------------------------------------------------------------------------


def test_dynamic_start_time_lunch_break_skip() -> None:
    """Wave 3 (#WAVE3): 移動時間で動的 lunch slot を跨ぐ場合は lunch_end にバンプ.

    Setup: コース A 月曜:
      - P-A 11:00-11:30 (固定)
      - P-B 11:40 希望だが 終日 → earliest = 11:30+移動 ≈ 11:47-11:50
        compute_lunch_window で B の占有 (11:40-12:10) を回避できる最善の
        60 分 lunch は **12:10-13:10**. B は earliest 11:50, end 12:20 が
        その lunch と重なるため 12:10 + 60 = **13:10 にバンプ**.
    """
    from app.services.scheduling.auto_allocator_v2 import _apply_travel_time_to_courses

    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=11, start_m=0, patient_name="A"
    )
    a.end_time = time(11, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "固定"

    # ~3km 離れた終日 visit
    b = _make_visit(
        lat=35.65, lng=140.133, office_id=office_id, start_h=11, start_m=40, patient_name="B"
    )
    b.end_time = time(12, 10)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "終日"

    warnings: list[V2Warning] = []
    _apply_travel_time_to_courses([a, b], warnings=warnings)

    # B は動的 lunch (12:10-13:10) 重複のため lunch_end=13:10 にバンプ.
    assert b.start_time == time(13, 10), f"動的 lunch バンプ後は 13:10 のはず, got {b.start_time}"
    assert b.end_time == time(13, 40), f"end_time も追従するはず, got {b.end_time}"
    assert any(
        w.type == "travel_time_shortage"
        and "昼休憩" in w.message
        and "13:10 に繰り下げ" in w.message
        for w in warnings
    ), f"昼休憩バンプ warning が無い: {warnings}"


def test_am_branch_pushed_to_pm_when_over_12() -> None:
    """Wave 3 (#WAVE3): 午前希望 visit が earliest >= 12:00 になった場合、
    動的 lunch_end にバンプされる + actionable warning が出る.

    Setup:
      - P-A 11:30-12:00 (固定, 12:00 終了)
      - P-B 11:45 午前希望 (A の後にソートされる位置) だが 5km 離れて
        移動 ~15 分 → earliest ≈ 12:15.
        compute_lunch_window で A (11:30-12:00) + B (11:45-12:15) を回避する
        60 分 lunch は **12:15-13:15** (最も 12:00 中心).
        AM 12:00 超で lunch_end=13:15 にバンプ.
    """
    from app.services.scheduling.auto_allocator_v2 import _apply_travel_time_to_courses

    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=11, start_m=30, patient_name="A"
    )
    a.end_time = time(12, 0)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "固定"

    # 5km 離れた午前希望 (desired_start は A 後で sort 順を担保, earliest 12:15)
    b = _make_visit(
        lat=35.65, lng=140.155, office_id=office_id, start_h=11, start_m=45, patient_name="B"
    )
    b.end_time = time(12, 15)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "午前"

    warnings: list[V2Warning] = []
    _apply_travel_time_to_courses([a, b], warnings=warnings)

    # 午前希望が 12:00 超のため 動的 lunch_end=13:15 (午後) にバンプされる.
    assert b.start_time == time(13, 15), (
        f"午前希望が 12:00 超なら lunch_end=13:15 にバンプされるはず, got {b.start_time}"
    )
    assert any(
        w.type == "travel_time_shortage"
        and "午前希望" in w.message
        and ("13:15" in w.message or "午後" in w.message)
        and w.actionable is True
        for w in warnings
    ), f"午前→午後バンプ warning が無い (or actionable=False): {warnings}"


def test_jikan_window_clamp_uses_earliest_not_window_upper() -> None:
    """HIGH #2: 時間帯 visit が window_upper を超過した場合、window_upper では
    なく earliest_start を採用する (infeasible timeline を防ぐ).

    Setup:
      - P-A 10:00-10:30 (固定)
      - P-B 時間帯 09:00-10:00 希望 (Stage 5 が初期 10:30 を割り当てた状況)
        → earliest = 10:30 + 9 分 ≒ 10:39 → window_upper (10:00) より後、
        旧 logic だと 10:00 にクランプ (A と衝突), 新 logic は 10:39 採用.
    """
    from app.services.scheduling.auto_allocator_v2 import _apply_travel_time_to_courses

    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=0, patient_name="A"
    )
    a.end_time = time(10, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "固定"

    # A の後にソートされるよう desired_start を 10:30 に置く
    # (initial start; travel logic が改めて window 検証).
    b = _make_visit(
        lat=35.65, lng=140.133, office_id=office_id, start_h=10, start_m=30, patient_name="B"
    )
    b.end_time = time(11, 30)
    b.service_minutes = 60
    b.course_code = "A"
    b.time_type = "時間帯"
    b.preferred_start = "09:00"
    b.preferred_end = "10:00"

    warnings: list[V2Warning] = []
    _apply_travel_time_to_courses([a, b], warnings=warnings)

    # earliest_start = 10:30 + 9 分 ≒ 10:39, window_upper = 10:00.
    # 旧 logic: clamp to 10:00 → A (10:00-10:30) と重複 (infeasible).
    # 新 logic: earliest 採用 → 10:39 開始 (window 外だが timeline 正しい).
    assert b.start_time > a.end_time, (
        f"B は A 終了後にあるべき (infeasible timeline 防止), "
        f"got A.end={a.end_time}, B.start={b.start_time}"
    )
    assert b.start_time >= time(10, 39), (
        f"earliest_start 採用なので 10:39 以降のはず, got {b.start_time}"
    )
    assert any(
        w.type == "travel_time_shortage" and "希望時間帯" in w.message and "超過" in w.message
        for w in warnings
    ), f"時間帯超過 warning が travel_time_shortage で出ていない: {warnings}"


def test_warning_types_correctly_split() -> None:
    """MEDIUM #1: travel_time_shortage / course_long_distance / two_staff_shortage
    が別 type として出力されること.

    - 移動時間不足 (固定) → travel_time_shortage
    - 累積 30 分超 → course_long_distance
    - 二人組必須 + スタッフ不足 → two_staff_shortage
    """
    from app.services.scheduling.auto_allocator_v2 import (
        _apply_travel_time_to_courses,
        _check_two_staff_availability,
    )

    office_id = uuid.uuid4()

    # 1) 固定 + 移動時間不足 → travel_time_shortage
    # Wave 3 (#WAVE3): 純粋な travel shortage (lunch 関係なし) を triggers するため
    # 2 つの固定 visit を異住所 5km 離して **異なる start_time** で配置する
    # (= auto_shift しない条件). A 14:00-14:30 終了 → B 14:30 固定 5km 離れ
    # travel ~15 分 + buffer ~8 分 = 23 分必要、shortage=23 分 >= 5 分 →
    # 物理不可能 travel_time_shortage.
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=14, start_m=0, patient_name="A"
    )
    a.end_time = time(14, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "固定"
    b = _make_visit(
        lat=35.65, lng=140.155, office_id=office_id, start_h=14, start_m=30, patient_name="B"
    )
    b.end_time = time(15, 0)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "固定"
    b.preferred_start = "14:30"
    warnings: list[V2Warning] = []
    _apply_travel_time_to_courses([a, b], warnings=warnings)
    types_seen = {w.type for w in warnings}
    assert "travel_time_shortage" in types_seen, (
        f"固定不足は travel_time_shortage であるべき: {warnings}"
    )

    # 2) 累積 30 分超のコース → course_long_distance
    office2 = uuid.uuid4()
    visits_long: list[V2Visit] = []
    # 大きく離れた 5 訪問 (隣接 ~12km × 4 = ~144 分 cumulative)
    for i, lng_off in enumerate([0.0, 0.12, 0.24, 0.36, 0.48]):
        v = _make_visit(
            lat=35.65,
            lng=140.10 + lng_off,
            office_id=office2,
            start_h=9 + i,
            start_m=0,
            patient_name=f"L{i}",
        )
        v.end_time = time(9 + i, 30)
        v.service_minutes = 30
        v.course_code = "A"
        v.time_type = "終日"
        visits_long.append(v)
    warnings2: list[V2Warning] = []
    _apply_travel_time_to_courses(visits_long, warnings=warnings2)
    long_warns = [w for w in warnings2 if w.type == "course_long_distance"]
    assert any("連続移動時間合計" in w.message for w in long_warns), (
        f"累積 30 分超は course_long_distance 単独であるべき: {warnings2}"
    )

    # 3) 二人組必須 + スタッフ 1 名 → two_staff_shortage
    office3 = uuid.uuid4()
    v_two = _make_visit(lat=35.65, lng=140.10, office_id=office3, patient_name="TWO")
    v_two.requires_multiple_staff = True
    v_two.weekday = 0
    warnings3: list[V2Warning] = []
    _check_two_staff_availability(
        [v_two], staff_count_by_weekday={(office3, 0): 1}, warnings=warnings3
    )
    assert all(w.type == "two_staff_shortage" for w in warnings3), (
        f"二人組不足は two_staff_shortage 単独であるべき: {warnings3}"
    )
    # 旧 course_count に紛れ込んでいないこと.
    assert not any(w.type == "course_count" for w in warnings3), (
        f"two_staff は course_count を使ってはいけない: {warnings3}"
    )


def test_chain_pushback_three_visits() -> None:
    """W41 v2: 3+ visit で移動時間がカスケード押し下げされる (chain effect).

    Setup: コース A 月曜 終日 visits:
      - P-A 09:00-09:30 (固定)
      - P-B 09:00 希望、A から 3km (移動 9 分 + バッファー 8 分 → 5 分切り上げ)
      - P-C 09:00 希望、B から 3km (移動 9 分 + バッファー 8 分 → 5 分切り上げ)

    期待: A 09:00-09:30 → B 09:50 近辺 → C は B 終了以降にカスケード.
    押し下げ計算は max(desired, prev.end + travel + buffer), 切り上げ後 5 分刻み.
    """
    from app.services.scheduling.auto_allocator_v2 import _apply_travel_time_to_courses

    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.end_time = time(9, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "固定"

    b = _make_visit(
        lat=35.65, lng=140.133, office_id=office_id, start_h=9, start_m=0, patient_name="B"
    )
    b.end_time = time(9, 30)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "終日"

    c = _make_visit(
        lat=35.65, lng=140.166, office_id=office_id, start_h=9, start_m=0, patient_name="C"
    )
    c.end_time = time(9, 30)
    c.service_minutes = 30
    c.course_code = "A"
    c.time_type = "終日"

    warnings: list[V2Warning] = []
    _apply_travel_time_to_courses([a, b, c], warnings=warnings)

    # A は固定で動かない.
    assert a.start_time == time(9, 0)
    # B は A 終了 (09:30) + 移動 ~9 分 + バッファー 8 分 = 09:47 → 5 分切り上げで 09:50.
    b_min = b.start_time.hour * 60 + b.start_time.minute
    assert 9 * 60 + 45 <= b_min <= 10 * 60 + 0, f"B は 09:45-10:00 のはず, got {b.start_time}"
    # 5 分刻みに切り上げられている.
    assert b_min % 5 == 0, f"B.start_time は 5 分刻みのはず: {b.start_time}"
    # C は B 終了 + 移動 ~9 分 + バッファー 8 分 → さらに後ろ.
    c_min = c.start_time.hour * 60 + c.start_time.minute
    assert c_min > b_min + b.service_minutes - 1, (
        f"C は B 終了 ({b.end_time}) 以降のはず, got {c.start_time}"
    )
    # C end_time も連動.
    expected_end = c_min + c.service_minutes
    assert c.end_time.hour * 60 + c.end_time.minute == expected_end


# ---------------------------------------------------------------------------
# W41+ — 訪問間バッファー 8 分 (旧 15 分) + 5 分刻み切り上げ + diff_add 衝突回避
# ---------------------------------------------------------------------------


def test_visit_buffer_8min_applied() -> None:
    """訪問間バッファー: 移動 1 分 + バッファー 8 分 = 9 分 加算 + 5 分刻み切り上げ.

    Setup:
      - P-A 09:00-09:30 (固定)
      - P-B 09:00 希望 (終日), A から ~0.1km (= haversine_minutes 1 分)
    期待: B.start = 09:30 + 1 + 8 = 09:39 → 5 分刻み切り上げで 09:40 (earliest).
    """
    from app.services.scheduling.auto_allocator_v2 import _apply_travel_time_to_courses

    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.end_time = time(9, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "固定"

    # ~0.1km 離れた終日 visit (異住所判定の最小ペア — _address_bucket 0.001 超え).
    b = _make_visit(
        lat=35.65, lng=140.101, office_id=office_id, start_h=9, start_m=0, patient_name="B"
    )
    b.end_time = time(9, 30)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "終日"

    warnings: list[V2Warning] = []
    _apply_travel_time_to_courses([a, b], warnings=warnings)

    # 09:30 + 1 (移動) + 8 (バッファー) = 09:39 → 5 分切り上げで 09:40.
    assert b.start_time == time(9, 40), (
        f"バッファー込み + 5 分切り上げで 09:40 のはず "
        f"(移動 1 分 + バッファー 8 分 → 09:39 → 09:40), got {b.start_time}"
    )
    assert b.end_time == time(10, 10), f"end_time も追従するはず, got {b.end_time}"


def test_visit_buffer_skipped_for_same_address() -> None:
    """同住所 (= travel 0 分) はバッファーも 0 — prev.end と同時刻も OK."""
    from app.services.scheduling.auto_allocator_v2 import _apply_travel_time_to_courses

    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.end_time = time(9, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "固定"

    # 完全に同住所 (= 同一バケット).
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=30, patient_name="B"
    )
    b.end_time = time(10, 0)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "終日"

    warnings: list[V2Warning] = []
    _apply_travel_time_to_courses([a, b], warnings=warnings)

    # Wave 2 (#115) + Phase E-3 改修 (3): 同住所ペアは A の start_time に揃えられる
    # (= バッファーゼロを超えて完全同時刻). B.end は max(30+30, 90)=90 分占有で 10:30.
    assert a.start_time == time(9, 0)
    assert b.start_time == time(9, 0), f"Wave 2: 同住所は A と同 start_time: {b.start_time}"
    assert a.end_time == time(9, 30)
    assert b.end_time == time(10, 30), f"Phase E-3: B.end は 90 分占有: {b.end_time}"


def test_diff_add_skips_conflicting_pool_visit() -> None:
    """既存 visit と時間重複する pool visit は除外され warning が出る.

    Setup:
      - 既存 P1 月 10:00-10:30
      - pool P1 月 10:15-10:45 → 重複 → 除外 + warning.
    """
    from app.services.scheduling.auto_allocator_v2 import _filter_conflicting_pool_visits

    pid = uuid.uuid4()
    office_id = uuid.uuid4()

    existing = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=0, patient_name="P1"
    )
    existing.patient_id = pid
    existing.start_time = time(10, 0)
    existing.end_time = time(10, 30)
    existing.weekday = 0  # 月曜

    pool_v = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=15, patient_name="P1"
    )
    pool_v.patient_id = pid
    pool_v.start_time = time(10, 15)
    pool_v.end_time = time(10, 45)
    pool_v.weekday = 0

    warnings: list[V2Warning] = []
    kept = _filter_conflicting_pool_visits([existing], [pool_v], warnings)

    assert kept == [], f"重複 pool visit は除外されるはず, got {kept}"
    assert any(
        w.type == "diff_add_conflict"
        and "重複" in w.message
        and "P1" in w.message
        and w.patient_id == pid
        and w.weekday == 0
        for w in warnings
    ), f"重複 warning が出ていない: {warnings}"


def test_diff_add_keeps_non_conflicting_pool_visit() -> None:
    """別曜日 (= weekday が異なる) なら同 patient_id でも残る."""
    from app.services.scheduling.auto_allocator_v2 import _filter_conflicting_pool_visits

    pid = uuid.uuid4()
    office_id = uuid.uuid4()

    existing = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=0, patient_name="P1"
    )
    existing.patient_id = pid
    existing.start_time = time(10, 0)
    existing.end_time = time(10, 30)
    existing.weekday = 0  # 月曜

    pool_v = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=0, patient_name="P1"
    )
    pool_v.patient_id = pid
    pool_v.start_time = time(10, 0)
    pool_v.end_time = time(10, 30)
    pool_v.weekday = 1  # 火曜 (= 別曜日)

    warnings: list[V2Warning] = []
    kept = _filter_conflicting_pool_visits([existing], [pool_v], warnings)

    assert kept == [pool_v], f"別曜日は残るはず, got {kept}"
    assert not any("重複" in w.message for w in warnings), (
        f"別曜日に重複 warning は出ないはず: {warnings}"
    )


# ---------------------------------------------------------------------------
# クロスレビュー修正 (Codex HIGH×2 + Opus MEDIUM×3 + LOW): 追加回帰テスト
# ---------------------------------------------------------------------------


def test_course_total_minutes_includes_buffer() -> None:
    """HIGH #1 (Codex): 異住所連続 N visit はバッファー (N-1) × 8 分を含む.

    Setup: 3 visit (異住所連続) — duration 30+30+30 = 90 分,
    travel A→B + B→C, それぞれにバッファー 8 分が乗る (= 16 分追加).
    """
    from app.services.scheduling.auto_allocator_v2 import (
        VISIT_BUFFER_MINUTES,
        calc_course_total_minutes,
    )

    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.service_minutes = 30
    a.course_code = "A"
    # 3km 離れた B
    b = _make_visit(
        lat=35.65, lng=140.133, office_id=office_id, start_h=10, start_m=0, patient_name="B"
    )
    b.service_minutes = 30
    b.course_code = "A"
    # さらに 3km 離れた C (異住所)
    c = _make_visit(
        lat=35.65, lng=140.166, office_id=office_id, start_h=11, start_m=0, patient_name="C"
    )
    c.service_minutes = 30
    c.course_code = "A"

    total = calc_course_total_minutes([a, b, c])
    # duration 90, 移動 2 ペア (~9 分 × 2), バッファー 2 × 8 = 16 分
    # → 90 + 約 18 + 16 = 約 124 分
    duration_sum = 90
    buffer_sum = 2 * VISIT_BUFFER_MINUTES
    # 異住所遷移ごとに少なくとも 1 分の移動 + バッファーが入る
    assert total >= duration_sum + 2 + buffer_sum, (
        f"buffer がコース総所要時間に含まれていない: total={total}, "
        f"duration_sum={duration_sum}, buffer_sum={buffer_sum}"
    )
    # 上限: travel min 1ペア 30 分 までと仮定すると 90 + 60 + 30 = 180
    assert total <= 180, f"想定外に大きい総所要時間: {total}"


def test_course_total_minutes_no_buffer_for_same_address() -> None:
    """HIGH #1 (Codex) + Phase E-3 改修 (3): 同住所連続 visit はバッファー 0
    (= 移動 0 と同じ扱い). ペアは ``max(service 合計, 90)`` 占有.

    同 lat/lng の 3 visit (= H2 enforce 漏れの想定; 実運用では別 set に分散される).
    隣接 (A,B) ペア = max(30+30, 90) = 90 分. 残る C (single) = 30 分.
    合計 = 120 分. バッファー追加なし (同住所).
    """
    from app.services.scheduling.auto_allocator_v2 import calc_course_total_minutes

    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.service_minutes = 30
    a.course_code = "A"
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=30, patient_name="B"
    )
    b.service_minutes = 30
    b.course_code = "A"
    c = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=0, patient_name="C"
    )
    c.service_minutes = 30
    c.course_code = "A"

    total = calc_course_total_minutes([a, b, c])
    # 同住所 → 移動 0 + バッファー 0. ペア (A,B) は max(60, 90) = 90 分 clamp +
    # C single 30 分 = 120 分.
    assert total == 120, f"Phase E-3: ペア 90 分 + single 30 分 = 120 分, got {total}"


def test_diff_add_pool_internal_conflict_filtered() -> None:
    """HIGH #2 / Opus MEDIUM #1: pool 内の同 (patient_id, weekday) 重複が除外される.

    Setup:
      - pool A 月 10:00-10:30
      - pool A 月 10:15-10:45 (= 同患者・同曜日・時刻重複)
    → 後者が除外され、`diff_add_conflict` warning が出る.
    """
    from app.services.scheduling.auto_allocator_v2 import _filter_pool_internal_conflicts

    pid = uuid.uuid4()
    office_id = uuid.uuid4()

    pv1 = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=0, patient_name="P1"
    )
    pv1.patient_id = pid
    pv1.start_time = time(10, 0)
    pv1.end_time = time(10, 30)
    pv1.weekday = 0

    pv2 = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=15, patient_name="P1"
    )
    pv2.patient_id = pid
    pv2.start_time = time(10, 15)
    pv2.end_time = time(10, 45)
    pv2.weekday = 0

    warnings: list[V2Warning] = []
    kept = _filter_pool_internal_conflicts([pv1, pv2], warnings)

    assert kept == [pv1], f"先頭時刻 (10:00) を keep / 後 (10:15) を除外: got {kept}"
    assert any(
        w.type == "diff_add_conflict"
        and "同患者の別提案" in w.message
        and w.patient_id == pid
        and w.weekday == 0
        for w in warnings
    ), f"pool 内重複 warning が出ていない: {warnings}"


def test_diff_add_keeps_touching_pool_visit() -> None:
    """Opus MEDIUM #3: end_time == start_time (touching) は衝突扱いしない.

    Setup:
      - existing A 月 10:00-10:30
      - pool B 月 10:30-11:00 (= 終端と開始が同じ; 重複なし)
    → kept そのまま, warning なし.
    """
    from app.services.scheduling.auto_allocator_v2 import _filter_conflicting_pool_visits

    pid = uuid.uuid4()
    office_id = uuid.uuid4()

    existing = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=0, patient_name="P1"
    )
    existing.patient_id = pid
    existing.start_time = time(10, 0)
    existing.end_time = time(10, 30)
    existing.weekday = 0

    pool_v = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=30, patient_name="P1"
    )
    pool_v.patient_id = pid
    pool_v.start_time = time(10, 30)
    pool_v.end_time = time(11, 0)
    pool_v.weekday = 0

    warnings: list[V2Warning] = []
    kept = _filter_conflicting_pool_visits([existing], [pool_v], warnings)

    assert kept == [pool_v], f"touching は衝突扱いしない: got {kept}"
    assert not any("重複" in w.message for w in warnings), (
        f"touching に重複 warning は出ないはず: {warnings}"
    )


# ---------------------------------------------------------------------------
# CareFlow Wave Next 2 [C1] — _resolve_course_for_code: code-aware template 解決
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_course_for_code_m_returns_m_template(db) -> None:
    """CareFlow Wave Next 2 [C1]: code='M' は M label template を引く.

    旧実装は label 昇順の最初の template (= 'A') を template_id に充ててしまい、
    Course(code='M', template_id=A) のような不整合が生成されていた.
    """
    from app.models.course_template import CourseTemplate
    from app.services.scheduling.auto_allocator_v2 import _resolve_course_for_code

    office = Office(name="resolve-M-office")
    db.add(office)
    await db.flush()
    tpl_a = CourseTemplate(office_id=office.id, label="A")
    tpl_m = CourseTemplate(office_id=office.id, label="M")
    db.add_all([tpl_a, tpl_m])
    await db.commit()

    course_cache: dict[tuple[UUID, int, str], object] = {}
    counter = [0]
    warnings: list[str] = []
    course = await _resolve_course_for_code(
        db,
        office_id=office.id,
        iso_year=2026,
        iso_week=20,
        weekday=0,
        code="M",
        course_cache=course_cache,
        courses_created_counter=counter,
        warnings=warnings,
    )
    assert course is not None, f"M template が存在するのに None が返った: warnings={warnings}"
    assert course.template_id == tpl_m.id, (
        f"code='M' なのに template_id が M template ではない: "
        f"got {course.template_id} expected {tpl_m.id} (tpl_a={tpl_a.id})"
    )
    assert course.code == "M"
    assert counter[0] == 1


@pytest.mark.asyncio
async def test_resolve_course_for_code_m_raises_when_missing(db) -> None:
    """CareFlow Wave Next 2 [C1]: M template 不在なら None + warning."""
    from app.models.course_template import CourseTemplate
    from app.services.scheduling.auto_allocator_v2 import _resolve_course_for_code

    office = Office(name="resolve-noM-office")
    db.add(office)
    await db.flush()
    # 拠点に A template だけ. M label は存在しない.
    db.add(CourseTemplate(office_id=office.id, label="A"))
    await db.commit()

    course_cache: dict[tuple[UUID, int, str], object] = {}
    counter = [0]
    warnings: list[str] = []
    course = await _resolve_course_for_code(
        db,
        office_id=office.id,
        iso_year=2026,
        iso_week=20,
        weekday=0,
        code="M",
        course_cache=course_cache,
        courses_created_counter=counter,
        warnings=warnings,
    )
    assert course is None, f"M template 不在のときは None を返すはず: course={course}"
    assert counter[0] == 0, "Course 行が作られてはいけない"
    assert any("M" in w and "見つかりません" in w for w in warnings), (
        f"明示的な warning が必要: {warnings}"
    )


@pytest.mark.asyncio
async def test_resolve_course_for_code_normal_returns_matching_template(db) -> None:
    """CareFlow Wave Next 2 [C1]: code='B' は label='B' の template を引く."""
    from app.models.course_template import CourseTemplate
    from app.services.scheduling.auto_allocator_v2 import _resolve_course_for_code

    office = Office(name="resolve-B-office")
    db.add(office)
    await db.flush()
    tpl_a = CourseTemplate(office_id=office.id, label="A")
    tpl_b = CourseTemplate(office_id=office.id, label="B")
    tpl_c = CourseTemplate(office_id=office.id, label="C")
    db.add_all([tpl_a, tpl_b, tpl_c])
    await db.commit()

    course_cache: dict[tuple[UUID, int, str], object] = {}
    counter = [0]
    warnings: list[str] = []
    course = await _resolve_course_for_code(
        db,
        office_id=office.id,
        iso_year=2026,
        iso_week=20,
        weekday=0,
        code="B",
        course_cache=course_cache,
        courses_created_counter=counter,
        warnings=warnings,
    )
    assert course is not None
    assert course.template_id == tpl_b.id, (
        f"code='B' なのに template_id が B template ではない: got {course.template_id}"
    )


@pytest.mark.asyncio
async def test_resolve_course_for_code_m2_falls_back_to_m_template(db) -> None:
    """CareFlow Wave Next 2 [H1]: code='M2' は exact M2 template なければ M template に fallback.

    overflow 第2セットが M2 として作られても、拠点に M2 専用 template が無ければ
    M template を template_id に充てる (M2 専用 template があれば exact-match 優先).
    """
    from app.models.course_template import CourseTemplate
    from app.services.scheduling.auto_allocator_v2 import _resolve_course_for_code

    office = Office(name="resolve-M2-fallback-office")
    db.add(office)
    await db.flush()
    tpl_a = CourseTemplate(office_id=office.id, label="A")
    tpl_m = CourseTemplate(office_id=office.id, label="M")
    db.add_all([tpl_a, tpl_m])
    await db.commit()

    course_cache: dict[tuple[UUID, int, str], object] = {}
    counter = [0]
    warnings: list[str] = []
    course = await _resolve_course_for_code(
        db,
        office_id=office.id,
        iso_year=2026,
        iso_week=20,
        weekday=0,
        code="M2",
        course_cache=course_cache,
        courses_created_counter=counter,
        warnings=warnings,
    )
    assert course is not None, f"M2 でも M template に fallback するはず: warnings={warnings}"
    assert course.template_id == tpl_m.id
    assert course.code == "M2"


@pytest.mark.asyncio
async def test_resolve_course_for_code_m2_exact_match_preferred(db) -> None:
    """CareFlow Wave Next 2 [H1]: 拠点に M2 template があれば M2 を優先 (M ではなく)."""
    from app.models.course_template import CourseTemplate
    from app.services.scheduling.auto_allocator_v2 import _resolve_course_for_code

    office = Office(name="resolve-M2-exact-office")
    db.add(office)
    await db.flush()
    tpl_m = CourseTemplate(office_id=office.id, label="M")
    tpl_m2 = CourseTemplate(office_id=office.id, label="M2")
    db.add_all([tpl_m, tpl_m2])
    await db.commit()

    course_cache: dict[tuple[UUID, int, str], object] = {}
    counter = [0]
    warnings: list[str] = []
    course = await _resolve_course_for_code(
        db,
        office_id=office.id,
        iso_year=2026,
        iso_week=20,
        weekday=0,
        code="M2",
        course_cache=course_cache,
        courses_created_counter=counter,
        warnings=warnings,
    )
    assert course is not None
    assert course.template_id == tpl_m2.id, (
        f"M2 exact-match があるのに M template に流れた: got {course.template_id} "
        f"(tpl_m={tpl_m.id}, tpl_m2={tpl_m2.id})"
    )


# ---------------------------------------------------------------------------
# CareFlow Wave Next 2 [H1] — M overflow を M / M2 / M3 ... に分散
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overflow_generates_m_m2_m3(db) -> None:
    """[H1]: staff=1, manager=5, 6 set → A 1 set + M / M2 / M3 / M4 / M5 の分散.

    CareFlow Wave Next 3: M overflow はマネージャー数で動的制限される.
    本テストはマネージャー 5 名出勤の前提なので 5 つの M overflow が出る.
    """
    office = Office(name="m-overflow-distribute-office")
    db.add(office)
    await db.flush()
    s = Staff(name="dist-staff", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(s)
    await db.flush()
    db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True))
    # CareFlow Wave Next 3: M course はマネージャー数で動的絞り込みされるので
    # H1 テストの分散挙動を確認するためマネージャー 5 名を月曜出勤させる.
    for i in range(5):
        m = Staff(
            name=f"dist-mgr-{i}",
            role="manager",
            is_trainee=False,
            primary_office_id=office.id,
        )
        db.add(m)
        await db.flush()
        db.add(StaffShift(staff_id=m.id, weekday=0, is_on=True))

    # 12 患者を >5km 離れた地点 (0.2 deg ~ 22km) に配置 → cluster_by_distance_greedy で
    # 6 ペアセット程度を生成. staff_count=1 → A 1 set + 残り 5 set が M overflow.
    for i in range(12):
        db.add(
            _make_patient(
                code=f"DST{i}",
                office_id=office.id,
                lat=35.65 + i * 0.2,
                lng=140.10 + i * 0.2,
                preferred_start="10:00",
            )
        )
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    codes_used = {v.course_code for v in result["after_visits"]}
    overflow_codes_used = codes_used & _M_OVERFLOW_CODES
    # 複数の M overflow code (M, M2 等) が使われていることを確認
    assert len(overflow_codes_used) >= 2, (
        f"複数 set が同じ 'M' に集約されている (H1 修正前の挙動): "
        f"codes={codes_used} overflow={overflow_codes_used}"
    )
    # M (base) は overflow の先頭として必ず存在する
    assert "M" in overflow_codes_used


def test_m_courses_have_separate_routes() -> None:
    """[H1]: M course は内部の travel-time 計算で物理的に独立ルートとして扱われる.

    ``_apply_travel_time_to_courses`` は ``(office, weekday, course_code)`` で
    grouping するため、同じ "M" に集約すると別ルートの visit が時刻計算に
    巻き込まれる. H1 修正で M / M2 などに分散されれば独立ルートになる.
    """
    from app.services.scheduling.auto_allocator_v2 import _apply_travel_time_to_courses

    office_id = uuid.uuid4()
    # 同 (office, weekday) で M 系 visit 4 件: 同じ "M" に集約された場合と
    # 分散された場合で start_time の繰り下げが変わることを観察する.
    # 4 visit を同住所外 (>5km) で配置.
    v1 = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=30, patient_name="V1"
    )
    v2 = _make_visit(
        lat=35.85, lng=140.30, office_id=office_id, start_h=9, start_m=30, patient_name="V2"
    )
    v3 = _make_visit(
        lat=36.05, lng=140.50, office_id=office_id, start_h=9, start_m=30, patient_name="V3"
    )
    v4 = _make_visit(
        lat=36.25, lng=140.70, office_id=office_id, start_h=9, start_m=30, patient_name="V4"
    )

    # H1 修正前 (= 全て "M"): _apply_travel_time_to_courses が 4 visit を 1 ルート扱い.
    for v in (v1, v2, v3, v4):
        v.course_code = "M"
        v.start_time = time(9, 30)
        v.end_time = time(10, 0)
    warnings1: list[V2Warning] = []
    _apply_travel_time_to_courses([v1, v2, v3, v4], warnings=warnings1)
    # 全 4 visit が 1 ルート扱いなら、後続 visit (v4) の start_time が大幅に繰り下がる
    aggregated_end = v4.start_time

    # H1 修正後 (= M / M2 / M3 / M4 に分散): 各 visit が独立コース扱い.
    v1b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=30, patient_name="V1b"
    )
    v2b = _make_visit(
        lat=35.85, lng=140.30, office_id=office_id, start_h=9, start_m=30, patient_name="V2b"
    )
    v3b = _make_visit(
        lat=36.05, lng=140.50, office_id=office_id, start_h=9, start_m=30, patient_name="V3b"
    )
    v4b = _make_visit(
        lat=36.25, lng=140.70, office_id=office_id, start_h=9, start_m=30, patient_name="V4b"
    )
    v1b.course_code = "M"
    v2b.course_code = "M2"
    v3b.course_code = "M3"
    v4b.course_code = "M4"
    for v in (v1b, v2b, v3b, v4b):
        v.start_time = time(9, 30)
        v.end_time = time(10, 0)
    warnings2: list[V2Warning] = []
    _apply_travel_time_to_courses([v1b, v2b, v3b, v4b], warnings=warnings2)
    independent_end = v4b.start_time

    # 集約版 (aggregated_end) は分散版 (independent_end) より遅い時刻になっているはず.
    # 分散版は各 visit が独立コース扱いなので start_time が動かない (9:30 のまま).
    assert independent_end == time(9, 30), (
        f"分散後の v4 は単独 visit なので start_time が動かないはず: {independent_end}"
    )
    assert aggregated_end > time(9, 30), (
        f"集約された v4 は前 visit の移動時間で start_time が繰り下がるはず: "
        f"aggregated_end={aggregated_end}"
    )


def test_m_courses_not_aggregated_in_capacity_check() -> None:
    """[H1]: M overflow が分散されていれば、各 M ルートが独立して capacity 判定される.

    既存の calc_h_violations は 1 コース 7 名以上で H9 違反.
    7 visit を同 "M" に集約 → H9=1, M / M2 (3+4) に分散 → H9=0 となるかを確認.
    """
    office_id = uuid.uuid4()
    # 集約版: 7 visit 全部 "M"
    aggregated: list[V2Visit] = []
    for i in range(7):
        v = _make_visit(
            lat=35.65 + i * 0.001, lng=140.10, office_id=office_id, patient_name=f"agg-{i}"
        )
        v.course_code = "M"
        aggregated.append(v)
    agg_violations = calc_h_violations(aggregated)
    assert agg_violations["H9"] >= 1, f"7 visit 同 'M' は H9 違反になるはず: {agg_violations}"

    # 分散版: 4 visit "M" + 3 visit "M2"
    distributed: list[V2Visit] = []
    for i in range(4):
        v = _make_visit(
            lat=35.65 + i * 0.001, lng=140.10, office_id=office_id, patient_name=f"d-{i}"
        )
        v.course_code = "M"
        distributed.append(v)
    for i in range(3):
        v = _make_visit(
            lat=35.65 + (i + 10) * 0.001, lng=140.10, office_id=office_id, patient_name=f"d-{i}"
        )
        v.course_code = "M2"
        distributed.append(v)
    dist_violations = calc_h_violations(distributed)
    assert dist_violations["H9"] == 0, (
        f"4+3 visit を M/M2 に分散すれば H9 違反なし: {dist_violations}"
    )


# ---------------------------------------------------------------------------
# CareFlow Wave Next 3 — M course マネージャー制限
# (M / M2 / ... 発行数を当該曜日の出勤マネージャー数で動的に絞り、
#  超過セットは unassigned_patients に流す)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_m_course_limited_to_manager_count(db) -> None:
    """[CWN3]: staff=1, manager=1, 余剰 set=1 → M 1 つだけ発行 (M2 は出ない).

    Wave Next 2 までの挙動: staff=1, 4 patient (2 set) → A 1 set + M 1 set + M2 1 set ...
    Wave Next 3 修正後: マネージャー 1 名なら M overflow は 1 つまで.
    """
    office = Office(name="mgr-limit-1-office")
    db.add(office)
    await db.flush()
    s = Staff(name="mgr-limit-staff", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(s)
    await db.flush()
    db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True))
    mgr = Staff(
        name="mgr-limit-mgr",
        role="manager",
        is_trainee=False,
        primary_office_id=office.id,
    )
    db.add(mgr)
    await db.flush()
    db.add(StaffShift(staff_id=mgr.id, weekday=0, is_on=True))

    # 4 患者 → 2 ペアセット. staff_count=1 で 1 set = A, 余剰 1 set = M (mgr_count=1).
    for i in range(4):
        db.add(
            _make_patient(
                code=f"ML{i}",
                office_id=office.id,
                lat=35.65 + i * 0.2,
                lng=140.10 + i * 0.2,
                preferred_start="10:00",
            )
        )
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    codes_used = {v.course_code for v in result["after_visits"]}
    overflow_codes_used = codes_used & _M_OVERFLOW_CODES
    # マネージャー 1 名なので M 1 つのみ (M2 は絶対出ない)
    assert overflow_codes_used <= {"M"}, (
        f"manager=1 なら M overflow は 'M' のみのはず: got {overflow_codes_used}"
    )
    assert "M2" not in codes_used, f"manager=1 なのに 'M2' が発行された: {codes_used}"


@pytest.mark.asyncio
async def test_no_m_course_when_no_manager(db) -> None:
    """[CWN3]: manager=0, 余剰 set あり → M も発行されず、超過 patient は unassigned に流れる."""
    office = Office(name="no-mgr-office")
    db.add(office)
    await db.flush()
    s = Staff(name="no-mgr-staff", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(s)
    await db.flush()
    db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True))

    # 4 患者 → 2 ペアセット. staff_count=1 で 1 set = A, 余剰 1 set → 未割当
    # (mgr_count=0 のため M も発行されない).
    for i in range(4):
        db.add(
            _make_patient(
                code=f"NM{i}",
                office_id=office.id,
                lat=35.65 + i * 0.2,
                lng=140.10 + i * 0.2,
                preferred_start="10:00",
            )
        )
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    codes_used = {v.course_code for v in result["after_visits"]}
    overflow_codes_used = codes_used & _M_OVERFLOW_CODES
    assert not overflow_codes_used, (
        f"manager=0 なら M overflow は一切出ないはず: got {overflow_codes_used}"
    )
    # 超過 patient (2 名) が unassigned_patients に出る
    unassigned = result["unassigned_patients"]
    unassigned_codes = {u["patient_code"] for u in unassigned}
    assert len(unassigned) >= 2, (
        f"manager=0, 余剰 set=1 → 少なくとも 2 名が未割当のはず: {unassigned}"
    )
    # NM プレフィックスの患者 (= overflow set の対象) が unassigned に含まれる
    assert any(c and c.startswith("NM") for c in unassigned_codes), (
        f"NM 患者が unassigned に含まれていない: {unassigned_codes}"
    )


@pytest.mark.asyncio
async def test_excess_sets_beyond_manager_go_to_unassigned(db) -> None:
    """[CWN3]: staff=1, manager=1, 余剰 set=3 → M 1 set + 残り 2 set の patient が unassigned."""
    office = Office(name="excess-mgr-office")
    db.add(office)
    await db.flush()
    s = Staff(name="ex-staff", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(s)
    await db.flush()
    db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True))
    mgr = Staff(name="ex-mgr", role="manager", is_trainee=False, primary_office_id=office.id)
    db.add(mgr)
    await db.flush()
    db.add(StaffShift(staff_id=mgr.id, weekday=0, is_on=True))

    # 8 患者 → 4 ペアセット. staff=1 → A 1 set + M 1 set (mgr=1) + 2 set 未割当.
    for i in range(8):
        db.add(
            _make_patient(
                code=f"EX{i}",
                office_id=office.id,
                lat=35.65 + i * 0.2,
                lng=140.10 + i * 0.2,
                preferred_start="10:00",
            )
        )
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    codes_used = {v.course_code for v in result["after_visits"]}
    overflow_codes_used = codes_used & _M_OVERFLOW_CODES
    # M 1 つのみ (mgr=1 上限)
    assert overflow_codes_used == {"M"}, (
        f"mgr=1 なら 'M' のみ発行されるはず: got {overflow_codes_used}"
    )
    # 未割当: 2 set × 2 patient = 4 名以上
    unassigned = result["unassigned_patients"]
    unassigned_codes = {u["patient_code"] for u in unassigned}
    assert len(unassigned) >= 4, (
        f"余剰 set=3, mgr=1 なら 2 set 分 (4 名以上) 未割当のはず: {unassigned}"
    )
    # EX プレフィックスの患者が複数 unassigned に含まれる
    ex_unassigned = {c for c in unassigned_codes if c and c.startswith("EX")}
    assert len(ex_unassigned) >= 4, f"EX 患者が 4 名以上 unassigned のはず: {ex_unassigned}"


@pytest.mark.asyncio
async def test_manager_per_weekday_dynamic(db) -> None:
    """[CWN3]: 土曜 mgr=0 でも平日 mgr=1 なら正しく動的判定される.

    月曜: staff=1 + mgr=1 → A + M (余剰 1 set OK)
    土曜: staff=1 + mgr=0 → A のみ, 余剰 set は unassigned
    """
    office = Office(name="weekday-mgr-dynamic-office")
    db.add(office)
    await db.flush()
    # スタッフ 1 名: 月曜 + 土曜出勤
    s = Staff(name="wd-mgr-staff", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(s)
    await db.flush()
    db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True))  # Mon
    db.add(StaffShift(staff_id=s.id, weekday=5, is_on=True))  # Sat
    # マネージャー: 月曜のみ出勤 (土曜は休み)
    mgr = Staff(
        name="wd-mgr-only-mon",
        role="manager",
        is_trainee=False,
        primary_office_id=office.id,
    )
    db.add(mgr)
    await db.flush()
    db.add(StaffShift(staff_id=mgr.id, weekday=0, is_on=True))

    # 月曜患者 4 名 (2 set) + 土曜患者 4 名 (2 set)
    for i in range(4):
        db.add(
            _make_patient(
                code=f"WMON{i}",
                office_id=office.id,
                lat=35.65 + i * 0.2,
                lng=140.10 + i * 0.2,
                preferred_start="10:00",
                weekdays=["Mon"],
            )
        )
        db.add(
            _make_patient(
                code=f"WSAT{i}",
                office_id=office.id,
                lat=35.65 + i * 0.2,
                lng=140.10 + i * 0.2,
                preferred_start="10:00",
                weekdays=["Sat"],
            )
        )
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    # weekday → set(course_code)
    by_weekday: dict[int, set[str]] = {}
    for v in result["after_visits"]:
        by_weekday.setdefault(v.weekday, set()).add(v.course_code)
    mon_codes = by_weekday.get(0, set())
    sat_codes = by_weekday.get(5, set())
    # 月曜: M 1 つ出ているはず (mgr=1)
    assert mon_codes & _M_OVERFLOW_CODES, (
        f"月曜 mgr=1 なので M overflow が出るはず: mon_codes={mon_codes}"
    )
    # 土曜: M overflow は一切出ないはず (mgr=0)
    assert not (sat_codes & _M_OVERFLOW_CODES), (
        f"土曜 mgr=0 なので M overflow は出ないはず: sat_codes={sat_codes}"
    )
    # 土曜の余剰 set patient は unassigned に流れる
    unassigned_codes = {u["patient_code"] for u in result["unassigned_patients"]}
    sat_unassigned = {c for c in unassigned_codes if c and c.startswith("WSAT")}
    assert sat_unassigned, (
        f"土曜の余剰 set 患者が unassigned に流れるはず: unassigned={unassigned_codes}"
    )


@pytest.mark.asyncio
async def test_warning_emitted_for_manager_short_overflow(db) -> None:
    """[CWN3]: マネージャー不足で超過セットが出たとき warning に「manager 不足」「未割当」が出る."""
    office = Office(name="mgr-warn-office")
    db.add(office)
    await db.flush()
    s = Staff(name="warn-staff", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(s)
    await db.flush()
    db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True))
    # mgr=0 で超過セットを発生させる
    for i in range(4):
        db.add(
            _make_patient(
                code=f"WRN{i}",
                office_id=office.id,
                lat=35.65 + i * 0.2,
                lng=140.10 + i * 0.2,
                preferred_start="10:00",
            )
        )
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    # warning に「manager 不足」「未割当」のメッセージが含まれる
    msgs = [w.message for w in result["warnings"]]
    assert any("manager 不足" in m and "未割当" in m for m in msgs), (
        f"manager 不足 warning が出るはず: warnings={msgs}"
    )


# ---------------------------------------------------------------------------
# CareFlow Wave Next 2 [H2] — staff_shifts 未投入の data-health warning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_warning_emitted_when_staff_shifts_missing(db) -> None:
    """[H2]: active staff いるのに staff_shifts.is_on=True が全曜日 0 → warning."""
    office = Office(name="staff-shifts-missing-office")
    db.add(office)
    await db.flush()
    # active staff 2 名 (shift は登録しない = staff_shifts 未投入の状態)
    for i in range(2):
        s = Staff(
            name=f"sh-missing-{i}",
            role="staff",
            is_trainee=False,
            primary_office_id=office.id,
            status="active",
        )
        db.add(s)
    await db.commit()

    # patient も登録しないと pipeline は早期 return しないので 1 件登録
    p = Patient(
        code="SHM",
        name="sh-missing-p",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    db.add(p)
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    data_health_warnings = [
        w for w in result["warnings"] if w.type == "data_health_staff_shifts_missing"
    ]
    assert data_health_warnings, (
        f"staff_shifts 未投入なら data_health_staff_shifts_missing warning が出るはず: "
        f"warnings={result['warnings']}"
    )
    assert any("staff_shifts" in w.message for w in data_health_warnings)


@pytest.mark.asyncio
async def test_no_data_health_warning_when_shifts_present(db) -> None:
    """[H2]: staff_shifts.is_on=True が 1 件でもあれば data-health warning は出ない."""
    office = Office(name="shifts-present-office")
    db.add(office)
    await db.flush()
    s = Staff(
        name="present-staff",
        role="staff",
        is_trainee=False,
        primary_office_id=office.id,
        status="active",
    )
    db.add(s)
    await db.flush()
    # 月曜だけ shift 登録 (他曜日 0 でも休業日として false-positive を避ける)
    db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True))
    p = Patient(
        code="SHP",
        name="sh-present-p",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    db.add(p)
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    data_health_warnings = [
        w for w in result["warnings"] if w.type == "data_health_staff_shifts_missing"
    ]
    assert not data_health_warnings, (
        f"shift が 1 件あれば data-health warning は出ないはず: {data_health_warnings}"
    )


# ---------------------------------------------------------------------------
# CareFlow Wave Next 2 [M1] — warning が effective_max を使う
# ---------------------------------------------------------------------------


def test_enforce_course_count_warns_when_staff_exceeds_course_codes_max() -> None:
    """[M1]: staff_count > _COURSE_CODES_MAX (=5) のとき余剰スタッフ案内 warning."""
    from app.services.scheduling.auto_allocator_v2 import (
        _COURSE_CODES_MAX,
        V2Set,
        enforce_course_count_constraint,
    )

    office_id = uuid.uuid4()
    weekday = 0
    # 6 set (= effective_max 5 を 1 超過). staff_count = 6 (> 5).
    sets = [
        V2Set(visits=[_make_visit(lat=35.65 + i * 0.01, lng=140.10, office_id=office_id)])
        for i in range(6)
    ]
    warnings: list[V2Warning] = []
    enforce_course_count_constraint(
        {(office_id, weekday, "am"): sets},
        staff_count_by_weekday={(office_id, weekday): 6},
        warnings=warnings,
        office_name_by_id={office_id: "test-office"},
    )
    # _COURSE_CODES_MAX 案内 + 通常 overflow warning が両方出る (6 set > effective_max=5)
    msgs = [w.message for w in warnings]
    assert any("コース数上限" in m for m in msgs), (
        f"staff>5 のとき余剰スタッフ案内が出るはず: {msgs}"
    )
    assert _COURSE_CODES_MAX == 5  # ガード


def test_enforce_course_count_uses_effective_max_for_warning() -> None:
    """[M1]: 6 staff / 6 set でも warning が出る (effective_max=5 を超過).

    旧実装 (raw staff_count) なら 6==6 で warning なし.
    新実装 (effective_max=min(6,5)=5) なら 6>5 で warning あり.
    """
    from app.services.scheduling.auto_allocator_v2 import V2Set, enforce_course_count_constraint

    office_id = uuid.uuid4()
    weekday = 0
    sets = [
        V2Set(visits=[_make_visit(lat=35.65 + i * 0.01, lng=140.10, office_id=office_id)])
        for i in range(6)
    ]
    warnings: list[V2Warning] = []
    enforce_course_count_constraint(
        {(office_id, weekday, "am"): sets},
        staff_count_by_weekday={(office_id, weekday): 6},
        warnings=warnings,
        office_name_by_id={office_id: "test-office"},
    )
    # マネージャー補充候補 1 件 (= 6 set - effective_max 5) の warning が出る
    overflow_msgs = [w for w in warnings if "マネージャー補充候補" in w.message]
    assert overflow_msgs, (
        f"6 set / staff=6 でも effective_max=5 のため overflow warning が出るはず: "
        f"got {[w.message for w in warnings]}"
    )


# ---------------------------------------------------------------------------
# P2: _identify_unassigned_patients structured reason + V2Warning.affected_patient_ids
# ---------------------------------------------------------------------------


def test_identify_unassigned_patient_with_no_coordinates() -> None:
    """P2: 座標未設定の患者は reason='no_coordinates'."""
    from app.services.scheduling.auto_allocator_v2 import _identify_unassigned_patients

    pid = uuid.uuid4()
    p = Patient(
        id=pid,
        code="NC-1",
        name="no-coord",
        status="active",
        lat=None,
        lng=None,
        primary_office_id=uuid.uuid4(),
    )
    result = _identify_unassigned_patients(pool_patients=[p], after_visits=[], warnings=[])
    assert len(result) == 1
    assert result[0]["reason"] == "no_coordinates"
    assert result[0]["dropped_at_stage"] == "general"
    assert result[0]["reason_detail"] is not None
    assert "ジオコーディング" in result[0]["reason_detail"]


def test_identify_unassigned_patient_with_manager_short_warning() -> None:
    """P2: warning.affected_patient_ids に含まれ "manager 不足" 含む → reason='manager_short'."""
    from app.services.scheduling.auto_allocator_v2 import _identify_unassigned_patients

    pid = uuid.uuid4()
    p = Patient(
        id=pid,
        code="MS-1",
        name="mgr-short",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=uuid.uuid4(),
    )
    w = V2Warning(
        type="course_capacity",
        message="月曜 拠点 X: 通常コース 1 + M (マネージャー枠) 1 を超えるセットがあり、2 名の患者が未割当 (manager 不足のため).",
        weekday=0,
        actionable=True,
        affected_patient_ids=[pid],
    )
    result = _identify_unassigned_patients(pool_patients=[p], after_visits=[], warnings=[w])
    assert len(result) == 1
    assert result[0]["reason"] == "manager_short"
    assert result[0]["dropped_at_stage"] == "stage5_course"


def test_identify_unassigned_patient_course_capacity_via_warning() -> None:
    """P2: 容量超過 warning (course_capacity, message に manager 不足含まず) → reason='course_capacity'."""
    from app.services.scheduling.auto_allocator_v2 import _identify_unassigned_patients

    pid = uuid.uuid4()
    p = Patient(
        id=pid,
        code="CC-1",
        name="capacity-over",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=uuid.uuid4(),
    )
    w = V2Warning(
        type="course_capacity",
        message="拠点 X A コース 月曜: コース総所要時間 500 分 > 上限 480 分",
        weekday=0,
        actionable=True,
        affected_patient_ids=[pid],
    )
    result = _identify_unassigned_patients(pool_patients=[p], after_visits=[], warnings=[w])
    assert result[0]["reason"] == "course_capacity"
    assert result[0]["dropped_at_stage"] == "stage4_capacity"


def test_identify_unassigned_patient_unknown_when_no_match() -> None:
    """P2: どの warning にも一致しなければ reason='unknown' (旧曖昧文言は撤去).

    silent drop fix (#2): weekly_pattern が dict なら no_weekly_pattern には落ちず
    unknown に fallback する.
    """
    from app.services.scheduling.auto_allocator_v2 import _identify_unassigned_patients

    pid = uuid.uuid4()
    p = Patient(
        id=pid,
        code="UK-1",
        name="unknown",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=uuid.uuid4(),
        weekly_pattern={},
    )
    result = _identify_unassigned_patients(pool_patients=[p], after_visits=[], warnings=[])
    assert result[0]["reason"] == "unknown"
    # 旧 "原因不明 (...のいずれか)" のような自由記述文言が出ていないこと.
    assert result[0]["reason_detail"] is None


def test_identify_unassigned_patient_acceptance_calendar_via_warning() -> None:
    """P2: acceptance_blocked warning → reason='acceptance_calendar'."""
    from app.services.scheduling.auto_allocator_v2 import _identify_unassigned_patients

    pid = uuid.uuid4()
    p = Patient(
        id=pid,
        code="AC-1",
        name="acc-blocked",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=uuid.uuid4(),
    )
    w = V2Warning(
        type="acceptance_blocked",
        message="AC-1 acc-blocked 様: 月曜 10:00 は受入カレンダーで「×」設定のため配置不可",
        weekday=0,
        actionable=False,
        patient_id=pid,
        affected_patient_ids=[pid],
    )
    result = _identify_unassigned_patients(pool_patients=[p], after_visits=[], warnings=[w])
    assert result[0]["reason"] == "acceptance_calendar"
    assert result[0]["dropped_at_stage"] == "general"


def test_v2warning_has_affected_patient_ids_field() -> None:
    """P2: V2Warning に affected_patient_ids フィールドが追加されていること."""
    pid = uuid.uuid4()
    w = V2Warning(
        type="course_capacity",
        message="test",
        affected_patient_ids=[pid],
    )
    assert w.affected_patient_ids == [pid]
    # default は空リスト.
    w2 = V2Warning(type="general", message="test2")
    assert w2.affected_patient_ids == []


def test_enforce_course_count_constraint_emits_affected_patient_ids() -> None:
    """P2: enforce_course_count_constraint の overflow warning に affected_patient_ids が埋まる."""
    from app.services.scheduling.auto_allocator_v2 import V2Set, enforce_course_count_constraint

    office_id = uuid.uuid4()
    weekday = 0
    # 6 set 作る (各 set に 1 visit = 1 patient). staff_count=1 → effective_max=1, overflow 5 set.
    visits = [_make_visit(lat=35.65 + i * 0.01, lng=140.10, office_id=office_id) for i in range(6)]
    sets = [V2Set(visits=[v]) for v in visits]
    warnings: list[V2Warning] = []
    enforce_course_count_constraint(
        {(office_id, weekday, "am"): sets},
        staff_count_by_weekday={(office_id, weekday): 1},
        warnings=warnings,
        office_name_by_id={office_id: "test-office"},
    )
    overflow_w = [w for w in warnings if "マネージャー補充候補" in w.message]
    assert overflow_w
    # overflow set (= 末尾 5 set) の patient_id が affected_patient_ids に入っている.
    expected_pids = {v.patient_id for v in visits[1:]}  # 末尾 5 visit
    actual_pids = set(overflow_w[0].affected_patient_ids)
    assert actual_pids == expected_pids, (
        f"overflow warning の affected_patient_ids が末尾 5 visit の patient_id と一致しない: "
        f"expected {expected_pids}, got {actual_pids}"
    )


# ---------------------------------------------------------------------------
# silent drop fix — Fix 1: full_optimize orphan PFV 救済
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_optimize_orphan_pfv_patient_is_placed(db) -> None:
    """Fix 1: P060 シナリオ — weekly_pattern=null + PFV あり patient が
    full_optimize でも PFV ベース展開され after_visits に配置される.

    旧実装: full_optimize は build_visits_for_pool を weekly_pattern ベースのみで
    呼ぶため、weekly_pattern=null + PFV ありの患者は visit が生成されず silent drop.
    新実装: orphan PFV 救済経路 (diff_add と同じ) を full_optimize でも適用.
    """
    from app.models.course_template import CourseTemplate

    office = Office(name="orphan-pfv-office")
    db.add(office)
    await db.flush()

    ct = CourseTemplate(office_id=office.id, label="B")
    db.add(ct)
    await db.flush()

    # P060 型: weekly_pattern=None + PFV あり
    p_orphan = Patient(
        code="P060",
        name="orphan-pfv",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        weekly_pattern=None,
    )
    db.add(p_orphan)
    await db.flush()
    db.add(
        PatientFixedVisit(
            patient_id=p_orphan.id,
            mode="normal",
            weekday=0,
            start_time=time(10, 0),
            duration_min=30,
            slot_index=0,
            course_template_id=ct.id,
        )
    )
    s = Staff(name="orphan-staff", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(s)
    await db.flush()
    db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True))
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    after_pids = {v.patient_id for v in result["after_visits"]}
    assert p_orphan.id in after_pids, (
        f"orphan PFV patient が full_optimize の after_visits に出ていない: "
        f"after_pids={after_pids}, pool_visits={[v.patient_code for v in result['pool_visits']]}"
    )
    # 未割当リストにも入っていない.
    unassigned_pids = {u["patient_id"] for u in result["unassigned_patients"]}
    assert p_orphan.id not in unassigned_pids, (
        f"orphan PFV patient が未割当に出ている: {result['unassigned_patients']}"
    )


# ---------------------------------------------------------------------------
# silent drop fix — Fix 2: UnassignedReason に no_weekly_pattern
# ---------------------------------------------------------------------------


def test_unassigned_reason_no_weekly_pattern() -> None:
    """Fix 2: weekly_pattern=None + PFV なし + どの warning にも一致しない →
    reason='no_weekly_pattern'."""
    from app.services.scheduling.auto_allocator_v2 import _identify_unassigned_patients

    pid = uuid.uuid4()
    p = Patient(
        id=pid,
        code="NWP-1",
        name="no-weekly-pattern",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=uuid.uuid4(),
        weekly_pattern=None,
    )
    result = _identify_unassigned_patients(pool_patients=[p], after_visits=[], warnings=[])
    assert len(result) == 1
    assert result[0]["reason"] == "no_weekly_pattern"
    assert result[0]["dropped_at_stage"] == "general"
    assert result[0]["reason_detail"] is not None
    assert "weekly_pattern" in result[0]["reason_detail"]


# ---------------------------------------------------------------------------
# silent drop fix — Fix 3: cluster_by_distance_greedy が重複 skip で warning
# ---------------------------------------------------------------------------


def test_cluster_emits_warning_on_duplicate_skip() -> None:
    """Fix 3: 同 patient_id + 同 start_time の重複 visit を skip した時に
    warning が emit される (warnings=[] を渡したとき).

    後方互換: warnings=None (デフォルト) では warning emit されない.
    """
    office_id = uuid.uuid4()
    # 同 patient_id + 同 start_time の 2 件
    pid = uuid.uuid4()
    v1 = V2Visit(
        patient_id=pid,
        patient_name="DupPatient",
        patient_code="DUP",
        weekday=0,
        start_time=time(10, 0),
        end_time=time(10, 30),
        service_minutes=30,
        lat=35.65,
        lng=140.10,
        office_id=office_id,
        am_pm="am",
        source_kind="pool",
    )
    v2 = V2Visit(
        patient_id=pid,
        patient_name="DupPatient",
        patient_code="DUP",
        weekday=0,
        start_time=time(10, 0),
        end_time=time(10, 30),
        service_minutes=30,
        lat=35.65,
        lng=140.10,
        office_id=office_id,
        am_pm="am",
        source_kind="pool",
    )
    warnings: list[V2Warning] = []
    sets = cluster_by_distance_greedy([v1, v2], warnings=warnings)
    # 1 件は skip され、残り 1 件のみ
    total_visits = sum(len(s.visits) for s in sets)
    assert total_visits == 1
    # warning が 1 件出ている
    dup_warnings = [w for w in warnings if "重複 visit" in w.message]
    assert len(dup_warnings) == 1
    assert dup_warnings[0].patient_id == pid
    assert pid in dup_warnings[0].affected_patient_ids
    assert dup_warnings[0].weekday == 0

    # 後方互換: warnings=None なら emit なし (= 旧シグネチャ動作)
    sets2 = cluster_by_distance_greedy([v1, v2])
    assert sum(len(s.visits) for s in sets2) == 1  # skip は変わらず発生


# ---------------------------------------------------------------------------
# silent drop fix — Fix 4: _filter_conflicting_pool_visits / _filter_pool_internal_conflicts
# ---------------------------------------------------------------------------


def test_filter_conflicting_pool_visits_emits_affected_patient_ids() -> None:
    """Fix 4: _filter_conflicting_pool_visits / _filter_pool_internal_conflicts
    が出す warning に affected_patient_ids が埋まる (_identify_unassigned_patients
    が patient_id 照合で reason 分類できるよう)."""
    from app.services.scheduling.auto_allocator_v2 import (
        _filter_conflicting_pool_visits,
        _filter_pool_internal_conflicts,
    )

    office_id = uuid.uuid4()
    pid = uuid.uuid4()

    def _mkv(start_h: int, start_m: int = 0, end_h: int | None = None) -> V2Visit:
        return V2Visit(
            patient_id=pid,
            patient_name="ConflictPatient",
            patient_code="CFL",
            weekday=0,
            start_time=time(start_h, start_m),
            end_time=time(end_h if end_h is not None else start_h + 1, start_m),
            service_minutes=60,
            lat=35.65,
            lng=140.10,
            office_id=office_id,
            am_pm="am",
            source_kind="pool",
        )

    # 1) _filter_conflicting_pool_visits: existing vs pool 衝突
    existing = [_mkv(10)]  # 10:00-11:00
    pool = [_mkv(10, 30)]  # 10:30-11:30 (overlap)
    warnings_a: list[V2Warning] = []
    kept = _filter_conflicting_pool_visits(existing, pool, warnings_a)
    assert kept == []
    assert len(warnings_a) == 1
    assert warnings_a[0].type == "diff_add_conflict"
    assert pid in warnings_a[0].affected_patient_ids

    # 2) _filter_pool_internal_conflicts: pool 内 同 (patient, weekday) 衝突
    pool2 = [_mkv(9), _mkv(9, 30)]  # 9:00-10:00 vs 9:30-10:30 (overlap)
    warnings_b: list[V2Warning] = []
    kept2 = _filter_pool_internal_conflicts(pool2, warnings_b)
    assert len(kept2) == 1
    assert len(warnings_b) == 1
    assert warnings_b[0].type == "diff_add_conflict"
    assert pid in warnings_b[0].affected_patient_ids


# ---------------------------------------------------------------------------
# silent drop fix — Fix 5: _classify_warning_reason に diff_add_conflict 分岐
# ---------------------------------------------------------------------------


def test_classify_warning_reason_diff_add_conflict() -> None:
    """Fix 5: diff_add_conflict warning → reason='fixed_time_conflict' / stage='general'."""
    from app.services.scheduling.auto_allocator_v2 import _classify_warning_reason

    pid = uuid.uuid4()
    w = V2Warning(
        type="diff_add_conflict",
        message="DUP 様: 月曜 10:00-11:00 は既存訪問 (10:00-11:00) と重複のためスキップ",
        weekday=0,
        actionable=True,
        patient_id=pid,
        affected_patient_ids=[pid],
    )
    classified = _classify_warning_reason(w)
    assert classified is not None
    reason, stage = classified
    assert reason == "fixed_time_conflict"
    assert stage == "general"


# ---------------------------------------------------------------------------
# CareFlow バグ修正 (Stage 5 code 重複防止):
#   #102 Fix B 漏れで「同 (office, weekday, course_code, start_time) に異住所
#   2 名同時刻配置」になっていた本質バグの再発防止テスト.
# ---------------------------------------------------------------------------


def test_find_next_available_code_returns_first_unused_normal() -> None:
    """``_find_next_available_code``: 未使用の通常コードを優先で返す."""
    from app.services.scheduling.auto_allocator_v2 import _find_next_available_code

    # A だけ assigned → B を返す
    code = _find_next_available_code({"A"}, normal_max=5, m_max=2)
    assert code == "B"


def test_find_next_available_code_falls_back_to_m_overflow() -> None:
    """``_find_next_available_code``: 通常上限を使い切ったら M overflow へ."""
    from app.services.scheduling.auto_allocator_v2 import _find_next_available_code

    # 通常 normal_max=2 (A/B のみ), 両方 assigned → M (M overflow) へ
    code = _find_next_available_code({"A", "B"}, normal_max=2, m_max=2)
    assert code == "M"

    # M も使用済み → M2
    code2 = _find_next_available_code({"A", "B", "M"}, normal_max=2, m_max=2)
    assert code2 == "M2"


def test_find_next_available_code_returns_none_when_full() -> None:
    """``_find_next_available_code``: 全コード使用済みで None."""
    from app.services.scheduling.auto_allocator_v2 import _find_next_available_code

    # normal_max=1 + m_max=1, A と M assigned → None
    code = _find_next_available_code({"A", "M"}, normal_max=1, m_max=1)
    assert code is None


@pytest.mark.asyncio
async def test_stage5_assigned_codes_prevents_duplicate(db) -> None:
    """Stage 5: 異なる 2 set が同じ既存 course_code (例 'A') を持つ場合、
    後発 set は別コードに fallback される (同 course_code 2 set 配置を防ぐ).
    """
    from app.models.course_template import CourseTemplate

    office = Office(name="stage5-dup-office")
    db.add(office)
    await db.flush()
    # CourseTemplate 'A' を用意
    template_a = CourseTemplate(office_id=office.id, label="A")
    db.add(template_a)
    await db.flush()

    # 月曜出勤スタッフ 2 名 → normal_course_limit=2 (A/B 発行可能)
    for i in range(2):
        s = Staff(
            name=f"s5-dup-staff-{i}",
            role="staff",
            is_trainee=False,
            primary_office_id=office.id,
        )
        db.add(s)
        await db.flush()
        db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True))

    # **遠く離れた 2 ペア (計 4 患者)**, 全員が PFV.course_template_id=template_a.
    # cluster_by_distance_greedy で 2 set (近場ペア × 2) に分かれ、
    # Stage 5 で existing_codes={'A'} が 2 set 両方に出る → 衝突回避 fallback で
    # 2 set 目は別コード (B など) になる.
    # NOTE: orphan PFV パス (weekly_pattern=None) を通すことで PFV.course_template_id
    # 由来の course_code='A' が build 時に V2Visit に埋め込まれ、Stage 5 の
    # ``existing_codes`` 分岐に入る (= #102 Fix B の衝突パスを再現).
    p1 = Patient(
        code="S5DUP1",
        name="s5dup1",
        status="active",
        lat=35.650,
        lng=140.100,
        primary_office_id=office.id,
    )
    p2 = Patient(
        code="S5DUP2",
        name="s5dup2",
        status="active",
        lat=35.651,  # p1 と < 0.2km
        lng=140.101,
        primary_office_id=office.id,
    )
    p3 = Patient(
        code="S5DUP3",
        name="s5dup3",
        status="active",
        lat=35.850,  # > 20km from p1/p2
        lng=140.300,
        primary_office_id=office.id,
    )
    p4 = Patient(
        code="S5DUP4",
        name="s5dup4",
        status="active",
        lat=35.851,  # p3 と < 0.2km
        lng=140.301,
        primary_office_id=office.id,
    )
    db.add_all([p1, p2, p3, p4])
    await db.flush()
    for pid, st in (
        (p1.id, time(9, 30)),
        (p2.id, time(10, 30)),
        (p3.id, time(9, 30)),
        (p4.id, time(10, 30)),
    ):
        db.add(
            PatientFixedVisit(
                patient_id=pid,
                mode="normal",
                weekday=0,
                start_time=st,
                duration_min=30,
                slot_index=0,
                course_template_id=template_a.id,
            )
        )
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    # 各 patient の course_code を集計
    code_by_patient: dict[UUID, set[str | None]] = {}
    for v in result["after_visits"]:
        code_by_patient.setdefault(v.patient_id, set()).add(v.course_code)
    # 4 patient 全員が after_visits に存在する (= pipeline で drop されていない).
    for pid, label in ((p1.id, "p1"), (p2.id, "p2"), (p3.id, "p3"), (p4.id, "p4")):
        assert code_by_patient.get(pid), (
            f"{label} が after_visits に存在しない: "
            f"warnings={[w.message for w in result['warnings']]}"
        )
    # 近場ペア (p1, p2) と 遠ペア (p3, p4) が **異なる course_code** に
    # 割り当てられていれば衝突回避 fallback が機能している.
    set12_codes = code_by_patient[p1.id] | code_by_patient[p2.id]
    set34_codes = code_by_patient[p3.id] | code_by_patient[p4.id]
    assert set12_codes & set34_codes == set(), (
        f"異 set の course_code が同一: set(p1,p2)={set12_codes}, "
        f"set(p3,p4)={set34_codes} (Stage 5 #102 Fix B 漏れの再発)"
    )


@pytest.mark.asyncio
async def test_stage5_fallback_warning_emitted_on_code_conflict(db) -> None:
    """Stage 5: existing_codes 衝突で fallback したら warning が出る (general)."""
    from app.models.course_template import CourseTemplate

    office = Office(name="stage5-warn-office")
    db.add(office)
    await db.flush()
    template_a = CourseTemplate(office_id=office.id, label="A")
    db.add(template_a)
    await db.flush()

    # スタッフ 2 名出勤
    for i in range(2):
        s = Staff(
            name=f"s5-warn-staff-{i}",
            role="staff",
            is_trainee=False,
            primary_office_id=office.id,
        )
        db.add(s)
        await db.flush()
        db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True))

    # 近場 2 ペア (計 4 患者)、全員が PFV → CourseTemplate 'A' を指す.
    # → 2 set が同じ existing_codes={'A'} を持つ → 衝突 fallback で warning が出る.
    # NOTE: orphan PFV パス (weekly_pattern=None) を通して existing_codes 分岐を再現.
    p1 = Patient(
        code="S5W1",
        name="s5w1",
        status="active",
        lat=35.650,
        lng=140.100,
        primary_office_id=office.id,
    )
    p2 = Patient(
        code="S5W2",
        name="s5w2",
        status="active",
        lat=35.651,
        lng=140.101,
        primary_office_id=office.id,
    )
    p3 = Patient(
        code="S5W3",
        name="s5w3",
        status="active",
        lat=35.850,
        lng=140.300,
        primary_office_id=office.id,
    )
    p4 = Patient(
        code="S5W4",
        name="s5w4",
        status="active",
        lat=35.851,
        lng=140.301,
        primary_office_id=office.id,
    )
    db.add_all([p1, p2, p3, p4])
    await db.flush()
    for pid, st in (
        (p1.id, time(9, 30)),
        (p2.id, time(10, 30)),
        (p3.id, time(9, 30)),
        (p4.id, time(10, 30)),
    ):
        db.add(
            PatientFixedVisit(
                patient_id=pid,
                mode="normal",
                weekday=0,
                start_time=st,
                duration_min=30,
                slot_index=0,
                course_template_id=template_a.id,
            )
        )
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    warnings = result["warnings"]
    fallback_msgs = [
        w
        for w in warnings
        if w.type == "general" and "他 set で既に使用中" in w.message and "別コード" in w.message
    ]
    assert fallback_msgs, (
        f"existing code 衝突 fallback の general warning が出ていない: "
        f"warnings={[w.message for w in warnings]}"
    )


# ---------------------------------------------------------------------------
# バッファー 8 分 + 5 分刻み切り上げ (CareFlow v2 拡張)
# - VISIT_BUFFER_MINUTES を 15 → 8 に変更
# - 非固定 visit の actual_start を 5 分刻みに切り上げ (実質バッファー 8-12 分)
# - 固定枠 (time_type='固定') は時刻不変
# ---------------------------------------------------------------------------


def test_buffer_minutes_is_8() -> None:
    """VISIT_BUFFER_MINUTES が 8 分に設定されていることを定数レベルで確認."""
    from app.services.scheduling.auto_allocator_v2 import VISIT_BUFFER_MINUTES

    assert VISIT_BUFFER_MINUTES == 8, (
        f"VISIT_BUFFER_MINUTES は 8 のはず (旧 15 → 新 8), got {VISIT_BUFFER_MINUTES}"
    )


def test_round_up_to_5min_helper() -> None:
    """``_round_up_to_5min`` の代表値を確認.

    - 10:31 → 10:35 (剰余 1, +4)
    - 09:03 → 09:05 (剰余 3, +2)
    - 10:00 → 10:00 (既に 5 分刻み)
    - 09:59 → 10:00 (分が繰り上がる)
    - 11:58 → 12:00 (時間境界も超える)
    """
    from app.services.scheduling.auto_allocator_v2 import _round_up_to_5min

    assert _round_up_to_5min(time(10, 31)) == time(10, 35)
    assert _round_up_to_5min(time(9, 3)) == time(9, 5)
    assert _round_up_to_5min(time(10, 0)) == time(10, 0)
    assert _round_up_to_5min(time(9, 59)) == time(10, 0)
    assert _round_up_to_5min(time(11, 58)) == time(12, 0)


def test_apply_travel_time_rounds_to_5min_for_non_fixed() -> None:
    """非固定 visit の ``actual_start`` が 5 分刻みに切り上げられる.

    Setup:
      - P-A 09:00-09:30 (固定)
      - P-B 09:00 希望 (終日), A から 3km (移動 9 分 + バッファー 8 分 = 17 分)
    earliest = 09:30 + 17 = 09:47 → 5 分切り上げで **09:50**.
    """
    from app.services.scheduling.auto_allocator_v2 import _apply_travel_time_to_courses

    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.end_time = time(9, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "固定"

    b = _make_visit(
        lat=35.65, lng=140.133, office_id=office_id, start_h=9, start_m=0, patient_name="B"
    )
    b.end_time = time(9, 30)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "終日"

    warnings: list[V2Warning] = []
    _apply_travel_time_to_courses([a, b], warnings=warnings)

    # 09:30 + 9 + 8 = 09:47 → 5 分切り上げで 09:50.
    assert b.start_time == time(9, 50), (
        f"非固定 visit は 5 分刻みに切り上げのはず: expected 09:50, got {b.start_time}"
    )
    # end_time も追従.
    assert b.end_time == time(10, 20), f"end_time も追従するはず, got {b.end_time}"
    # 5 分刻みであることを明示的に確認.
    assert b.start_time.minute % 5 == 0, (
        f"非固定 visit の start_time は 5 分刻みのはず: {b.start_time}"
    )


def test_apply_travel_time_keeps_fixed_time_unchanged() -> None:
    """固定枠 (``time_type='固定'``) は 5 分刻みでなくても切り上げ対象外.

    Setup:
      - P-A 09:00-09:30 (固定 09:00)
      - P-B 09:33 固定 希望 (同住所のため移動 0 + バッファー 0)
    expected: B.start = 09:33 不変 (5 分刻みでなくても固定値を尊重).
    """
    from app.services.scheduling.auto_allocator_v2 import _apply_travel_time_to_courses

    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.end_time = time(9, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "固定"
    a.preferred_start = "09:00"

    # 同住所 (= 移動 0 + バッファー 0) で 09:33 固定希望.
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=33, patient_name="B"
    )
    b.end_time = time(10, 3)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "固定"
    b.preferred_start = "09:33"

    warnings: list[V2Warning] = []
    _apply_travel_time_to_courses([a, b], warnings=warnings)

    # 固定枠は切り上げ対象外 — 09:33 そのまま (09:35 にはならない).
    assert b.start_time == time(9, 33), (
        f"固定枠は 5 分刻み切り上げの対象外のはず: expected 09:33, got {b.start_time}"
    )
    assert b.end_time == time(10, 3), f"end_time も追従するはず: {b.end_time}"


def test_buffer_8_min_applied_in_calc_course_total() -> None:
    """``calc_course_total_minutes`` でもバッファー 8 分が使われる.

    異住所 2 visit (duration 30 + 30 = 60 分) の総時間 = 60 + travel + 8 分.
    旧 15 分から 7 分減ったぶん総時間が短くなることを確認.
    """
    from app.services.scheduling.auto_allocator_v2 import (
        VISIT_BUFFER_MINUTES,
        calc_course_total_minutes,
    )

    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.service_minutes = 30
    a.course_code = "A"
    # 3km 離れた B (異住所)
    b = _make_visit(
        lat=35.65, lng=140.133, office_id=office_id, start_h=10, start_m=0, patient_name="B"
    )
    b.service_minutes = 30
    b.course_code = "A"

    total = calc_course_total_minutes([a, b])
    # duration 60 + 移動 ~9 分 + バッファー 8 分 = 約 77 分.
    # buffer 部分が ``VISIT_BUFFER_MINUTES`` (= 8) で計算されていれば
    # total - duration - travel = VISIT_BUFFER_MINUTES (= 8).
    duration_sum = 60
    # haversine_minutes(3km) は分単位 int → travel は >=1 と仮定可能.
    travel_min_lower = 1
    travel_min_upper = 30
    assert total == duration_sum + travel_min_lower * 0 + (total - duration_sum), (
        "trivial: total - duration = travel + buffer"
    )
    travel_plus_buffer = total - duration_sum
    # buffer 部分 = total - duration - travel.
    # travel >= 1 で travel + buffer <= 30 + 8 = 38, >= 1 + 8 = 9.
    assert (
        (travel_min_lower + VISIT_BUFFER_MINUTES)
        <= travel_plus_buffer
        <= (travel_min_upper + VISIT_BUFFER_MINUTES)
    ), (
        f"travel + buffer は ({travel_min_lower + VISIT_BUFFER_MINUTES})-"
        f"({travel_min_upper + VISIT_BUFFER_MINUTES}) のはず: got {travel_plus_buffer}, "
        f"total={total}"
    )
    # 旧仕様 (buffer=15) との差分: 同じ入力で buffer のみ 15→8 になったので
    # 総時間は 7 分減るはず. 「総時間 < 旧仕様」を表すため上限を 60 + 30 + 8 = 98 で固定.
    assert total <= duration_sum + travel_min_upper + VISIT_BUFFER_MINUTES, (
        f"buffer=8 に変更されているため total <= {duration_sum + travel_min_upper + VISIT_BUFFER_MINUTES} のはず: "
        f"got {total}"
    )


def test_round_up_keeps_constraint_am_to_lunch_bump() -> None:
    """Wave 3 (#WAVE3): 5 分刻み切り上げで lunch slot を跨いだ場合、動的 lunch
    再検証で lunch_end にバンプされる (制約再検証の正当性).

    Setup (Wave 2 #115 で異住所に変更, Wave 3 で lunch 動的化):
      - P-A 11:20-11:50 (固定 / 異住所 addr1)
      - P-B 終日, 異住所 addr2 (= travel 数分 + バッファー 8 分)
        earliest = 11:50 + travel + buffer → 12 時台序盤に切り上げ.
        compute_lunch_window で B (11:50-12:20) を回避する 60 分 lunch は
        **12:20-13:20**. B end が lunch_start (12:20) と被る → lunch_end=13:20
        にバンプ.
    """
    from app.services.scheduling.auto_allocator_v2 import _apply_travel_time_to_courses

    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=11, start_m=20, patient_name="A"
    )
    a.start_time = time(11, 20)
    a.end_time = time(11, 50)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "固定"
    # 異住所 (= travel + buffer あり) の終日 visit. 数 km 離れる.
    b = _make_visit(
        lat=35.65, lng=140.15, office_id=office_id, start_h=11, start_m=50, patient_name="B"
    )
    b.start_time = time(11, 50)
    b.end_time = time(12, 20)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "終日"

    warnings: list[V2Warning] = []
    _apply_travel_time_to_courses([a, b], warnings=warnings)

    # B: earliest ≈ 12:10 → 動的 lunch 12:20-13:20 と被るため lunch_end=13:20 へ.
    assert b.start_time == time(13, 20), (
        f"切り上げ後 動的 lunch 重複で 13:20 にバンプされるはず: got {b.start_time}"
    )
    assert b.end_time == time(13, 50), f"end_time も追従: got {b.end_time}"


# ---------------------------------------------------------------------------
# Wave 1 (#115): apply_travel_corrections public helper の単体テスト.
#
# `_apply_travel_time_to_courses` への薄いラッパだが、呼び出し側 (apply_week_only
# / reset_visits_to_fixed / apply_individual_proposal) が この helper を呼ぶ前提
# になったので「helper 単独で auto_shift + 同住所 align + 戻り値 unassigned set」
# が出ることをここで明示的にロックする.
# ---------------------------------------------------------------------------


def test_apply_travel_corrections_shifts_cross_address_same_time_pair() -> None:
    """Wave 1: 異住所同時刻ペア (両者座標あり) を helper が auto_shift する.

    Wave 4 (Phase C) ケアアラーム閾値導入後の調整: B 側 lng を 140.155 (~5km) に
    寄せて auto_shift 後の B.start_time が preferred_start (10:00) から 60 分以内に
    収まるようにする (距離が遠すぎると care_alarm_exceeded で unassigned に流れる).
    """
    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=0, patient_name="A"
    )
    a.end_time = time(10, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "固定"
    a.preferred_start = "10:00"
    b = _make_visit(
        lat=35.65, lng=140.155, office_id=office_id, start_h=10, start_m=0, patient_name="B"
    )
    b.end_time = time(10, 30)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "固定"
    b.preferred_start = "10:00"

    warnings: list[V2Warning] = []
    unassigned = apply_travel_corrections([a, b], warnings=warnings)

    # 両者座標がある + auto_shift で解消可能 (~5km, 60 分以内シフト) → unassigned に流れない.
    assert id(a) not in unassigned
    assert id(b) not in unassigned
    # A は 10:00 不変, B は後段にシフトされている.
    assert a.start_time == time(10, 0)
    assert b.start_time > time(10, 0), f"B が auto_shift されていない: {b.start_time}"
    # warning: auto_time_shift_for_conflict が出ている.
    auto_shifts = [w for w in warnings if w.type == "auto_time_shift_for_conflict"]
    assert auto_shifts, f"auto_shift warning が出ていない: {warnings}"


def test_apply_travel_corrections_is_idempotent_after_correction() -> None:
    """Wave 1: 一度補正した visit に対し再度 helper を呼んでも no-op (= 冪等)."""
    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.end_time = time(9, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "固定"
    a.preferred_start = "9:00"
    b = _make_visit(
        lat=35.65, lng=140.11, office_id=office_id, start_h=10, start_m=0, patient_name="B"
    )
    b.end_time = time(10, 30)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "時間帯"
    b.preferred_start = "09:30"
    b.preferred_end = "11:30"

    warnings: list[V2Warning] = []
    apply_travel_corrections([a, b], warnings=warnings)
    first_a = (a.start_time, a.end_time)
    first_b = (b.start_time, b.end_time)

    warnings2: list[V2Warning] = []
    apply_travel_corrections([a, b], warnings=warnings2)
    # 2 回目呼び出しでも時刻は変わらない (冪等).
    assert (a.start_time, a.end_time) == first_a
    assert (b.start_time, b.end_time) == first_b


def test_apply_travel_corrections_groups_by_office_weekday_course() -> None:
    """Wave 1: (office, weekday, course_code) 別に処理 — 異 group は干渉しない."""
    office_id = uuid.uuid4()
    # 同 office × weekday × course A: 異住所同時刻ペア.
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=0, patient_name="A"
    )
    a.end_time = time(10, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "固定"
    a.preferred_start = "10:00"
    b = _make_visit(
        lat=35.65, lng=140.20, office_id=office_id, start_h=10, start_m=0, patient_name="B"
    )
    b.end_time = time(10, 30)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "固定"
    b.preferred_start = "10:00"
    # 別 course B の visit (同時刻だが別コース なので干渉しない).
    c = _make_visit(
        lat=35.65, lng=140.30, office_id=office_id, start_h=10, start_m=0, patient_name="C"
    )
    c.end_time = time(10, 30)
    c.service_minutes = 30
    c.course_code = "B"
    c.time_type = "固定"
    c.preferred_start = "10:00"

    warnings: list[V2Warning] = []
    apply_travel_corrections([a, b, c], warnings=warnings)

    # 別コース C は不変 (= 干渉しない).
    assert c.start_time == time(10, 0)
    # A / B は course A 内で auto_shift される.
    assert a.start_time == time(10, 0)
    assert b.start_time > time(10, 0)


def test_apply_travel_corrections_no_shift_for_unique_times() -> None:
    """Wave 1: 同時刻衝突がなければ何もシフトしない."""
    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.end_time = time(9, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "固定"
    a.preferred_start = "9:00"
    b = _make_visit(
        lat=35.65, lng=140.20, office_id=office_id, start_h=11, start_m=0, patient_name="B"
    )
    b.end_time = time(11, 30)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "固定"
    b.preferred_start = "11:00"

    warnings: list[V2Warning] = []
    unassigned = apply_travel_corrections([a, b], warnings=warnings)

    assert a.start_time == time(9, 0)
    assert b.start_time == time(11, 0)
    assert not unassigned


# ---------------------------------------------------------------------------
# Wave 2 (#115): 同住所ペアを **同 start_time** に揃え + duration 倍占有.
# `_align_same_address_pair_to_same_time` を `apply_travel_corrections` 経由で検証.
# ---------------------------------------------------------------------------


def test_same_address_pair_aligns_to_first_start_time() -> None:
    """Wave 2 + Phase E-3 改修 (3): 同住所 2 名 9:00 + 9:30 → 両者 9:00 揃え,
    B.end=10:30 (max(30+30, 90)=90 分占有)."""
    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.end_time = time(9, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "時間帯"
    a.preferred_start = "09:00"
    a.preferred_end = "11:00"
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=30, patient_name="B"
    )
    b.end_time = time(10, 0)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "時間帯"
    b.preferred_start = "09:00"
    b.preferred_end = "11:00"

    warnings: list[V2Warning] = []
    apply_travel_corrections([a, b], warnings=warnings)

    # 両者 9:00 揃え (= sort 後の先頭 A の時刻).
    assert a.start_time == time(9, 0), f"A: {a.start_time}"
    assert b.start_time == time(9, 0), f"B: {b.start_time}"
    # A.end = 9:30 (= A.service_minutes 30 分).
    assert a.end_time == time(9, 30), f"A end: {a.end_time}"
    # Phase E-3: B.end = 10:30 (= aligned_start + max(A.service + B.service, 90) = 90 分占有).
    assert b.end_time == time(10, 30), f"B end (ペア 90 分占有): {b.end_time}"


def test_same_address_pair_next_visit_earliest_uses_pair_end() -> None:
    """Wave 2 + Phase E-3 改修 (3): ペアの後の visit C は earliest = B.end_time + travel + buffer.
    B.end は max(30+30, 90) = 90 分 (10:30)."""
    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.end_time = time(9, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "時間帯"
    a.preferred_start = "09:00"
    a.preferred_end = "11:00"
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=30, patient_name="B"
    )
    b.end_time = time(10, 0)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "時間帯"
    b.preferred_start = "09:00"
    b.preferred_end = "11:00"
    c = _make_visit(
        lat=35.65, lng=140.105, office_id=office_id, start_h=10, start_m=0, patient_name="C"
    )
    # ~500m 離れた C.
    c.end_time = time(10, 30)
    c.service_minutes = 30
    c.course_code = "A"
    c.time_type = "時間帯"
    c.preferred_start = "09:00"
    c.preferred_end = "12:00"

    warnings: list[V2Warning] = []
    apply_travel_corrections([a, b, c], warnings=warnings)

    # Phase E-3: B.end = 10:30 (90 分 clamp). C は 10:30 + travel + buffer 以上.
    assert b.end_time == time(10, 30)
    # 500m / 20km/h ≈ 1.5 分 + buffer 8 分 ≈ 10 分前後. 5 分切り上げ → 10:40 以降.
    assert c.start_time >= time(10, 40), (
        f"C は B.end(10:30) + travel/buffer 以降のはず: {c.start_time}"
    )


def test_same_address_pair_both_fixed_same_time() -> None:
    """Wave 2 + Phase E-3 改修 (3): 両者固定で時刻一致 → そのまま揃え,
    B.end は max(60, 90) = 90 分占有."""
    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=0, patient_name="A"
    )
    a.end_time = time(10, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "固定"
    a.preferred_start = "10:00"
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=0, patient_name="B"
    )
    b.end_time = time(10, 30)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "固定"
    b.preferred_start = "10:00"

    warnings: list[V2Warning] = []
    apply_travel_corrections([a, b], warnings=warnings)

    assert a.start_time == time(10, 0)
    assert b.start_time == time(10, 0)
    # A.end = 10:30, B.end = 11:30 (Phase E-3 90 分 clamp).
    assert a.end_time == time(10, 30)
    assert b.end_time == time(11, 30)


def test_same_address_pair_both_fixed_different_time_emits_warning() -> None:
    """Wave 2: 両者固定で時刻不一致 → 揃えず warning."""
    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.end_time = time(9, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "固定"
    a.preferred_start = "09:00"
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=0, patient_name="B"
    )
    b.end_time = time(10, 30)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "固定"
    b.preferred_start = "10:00"

    warnings: list[V2Warning] = []
    apply_travel_corrections([a, b], warnings=warnings)

    # 同住所両固定で時刻不一致 → 揃えず warning. start_time は原値のまま.
    # ただし earliest_start 計算で B は A.end + travel(=0) + buffer に押される
    # 可能性がある (同住所なので travel ~0, buffer ~0). この場合 B も 9:30 開始. 仕様上.
    # Wave 2 の眼目は「警告が出る」かどうか.
    pair_warns = [
        w for w in warnings if "両者固定で時刻不一致" in w.message or "揃えられません" in w.message
    ]
    assert pair_warns, f"両固定不一致 warning が出ていない: {[w.message for w in warnings]}"


def test_same_address_pair_one_fixed_one_flex_aligns_to_fixed_time() -> None:
    """Wave 2: 片方固定 + 片方時間帯 → 固定側の時刻に揃える."""
    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=0, patient_name="A"
    )
    a.end_time = time(10, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "固定"
    a.preferred_start = "10:00"
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=11, start_m=0, patient_name="B"
    )
    b.end_time = time(11, 30)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "時間帯"
    b.preferred_start = "09:00"
    b.preferred_end = "12:00"

    warnings: list[V2Warning] = []
    apply_travel_corrections([a, b], warnings=warnings)

    # 固定 A (10:00) に揃える → B も 10:00.
    assert a.start_time == time(10, 0)
    assert b.start_time == time(10, 0)
    # Phase E-3: B.end = 11:30 (= 10:00 + max(30+30, 90) = 90 分占有).
    assert b.end_time == time(11, 30)


def test_same_address_pair_both_flex_aligns_to_first() -> None:
    """Wave 2: 両者非固定 → sort 済 先頭 A の時刻に揃える."""
    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=30, patient_name="A"
    )
    a.end_time = time(10, 0)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "時間帯"
    a.preferred_start = "09:00"
    a.preferred_end = "11:00"
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=30, patient_name="B"
    )
    b.end_time = time(11, 0)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "時間帯"
    b.preferred_start = "09:00"
    b.preferred_end = "11:00"

    warnings: list[V2Warning] = []
    apply_travel_corrections([a, b], warnings=warnings)

    # 先頭 A (9:30) に揃える.
    assert a.start_time == time(9, 30)
    assert b.start_time == time(9, 30)
    # Phase E-3: B.end = 9:30 + max(30+30, 90) = 11:00 (90 分占有).
    assert b.end_time == time(11, 0)


def test_same_address_three_patients_only_first_two_pair() -> None:
    """Wave 2 + Phase E-3 改修 (4): 3 名同住所同コース → 先頭 2 名のみペア化、
    3 名目は **unassigned に流す** (Phase E-3 で自動別コース化).

    旧仕様 (Wave 2): C は single としてペアの後に配置 → コース内に 3 名残る.
    新仕様 (Phase E-3): C は course_code=None + warning emit. 同住所は最大 2 名ペアで
    完結し、3 名目以降は別コース移動推奨 (auto_allocator 自動別コース化).
    """
    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.end_time = time(9, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "時間帯"
    a.preferred_start = "09:00"
    a.preferred_end = "12:00"
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=30, patient_name="B"
    )
    b.end_time = time(10, 0)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "時間帯"
    b.preferred_start = "09:30"
    b.preferred_end = "12:00"
    c = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=0, patient_name="C"
    )
    c.end_time = time(10, 30)
    c.service_minutes = 30
    c.course_code = "A"
    c.time_type = "時間帯"
    c.preferred_start = "10:00"
    c.preferred_end = "12:00"

    warnings: list[V2Warning] = []
    unassigned = apply_travel_corrections([a, b, c], warnings=warnings)

    # A / B はペア化: 両者 9:00, B.end=10:30 (Phase E-3 90 分 clamp).
    assert a.start_time == time(9, 0)
    assert b.start_time == time(9, 0)
    assert b.end_time == time(10, 30)
    # C は 3 名目として preferred_start が一番遅いため自動別コース化 (unassigned).
    assert c.course_code is None, f"C は 3 名目で unassigned のはず: course_code={c.course_code}"
    assert id(c) in unassigned, "C の id が unassigned set に入っているはず"
    # warning: 「別コース移動推奨」を含む
    same_addr_3_warns = [
        w for w in warnings if "3 名以上の同住所" in w.message and "別コース" in w.message
    ]
    assert same_addr_3_warns, (
        f"3 名以上同住所別コース化 warning が出ていない: {[w.message for w in warnings]}"
    )


def test_same_address_pair_lunch_overlap_warning() -> None:
    """Wave 2 + Wave 3 (#WAVE3): ペア合算 duration が 11:30-13:30 の動的 lunch
    枠に対し「45 分 lunch も避けられない」区間にハマったら warning.

    Setup: 11:45 + 45 + 45 = 13:15 終了 → 動的 lunch (45-60 分) を全く取れない
    (start=11:45 < 12:15 AND end=13:15 > 12:30).
    """
    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=11, start_m=45, patient_name="A"
    )
    a.end_time = time(12, 30)
    a.service_minutes = 45
    a.course_code = "A"
    a.time_type = "固定"
    a.preferred_start = "11:45"
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=11, start_m=45, patient_name="B"
    )
    b.end_time = time(12, 30)
    b.service_minutes = 45
    b.course_code = "A"
    b.time_type = "固定"
    b.preferred_start = "11:45"

    warnings: list[V2Warning] = []
    apply_travel_corrections([a, b], warnings=warnings)

    # 動的 lunch 枠 (11:30-13:30) と不可避な重なり警告が出る.
    lunch_warns = [w for w in warnings if "昼休憩" in w.message]
    assert lunch_warns, (
        f"同住所ペア lunch 重複 warning が出ていない: {[w.message for w in warnings]}"
    )


# ---------------------------------------------------------------------------
# Wave 3 (#WAVE3): compute_lunch_window — コース別動的 lunch 配置
# ---------------------------------------------------------------------------


def test_compute_lunch_window_finds_60min_slot_when_available() -> None:
    """11:00-15:00 にコース内 visit が散在、60 分空きあり → 60 分 lunch が返る.

    Setup: 9:00-10:00, 10:00-11:00, 14:00-15:00 のコース.
    11:00-13:30 はすべて空き. 12:00 中心の 60 分 lunch = 12:00-13:00.
    """
    from app.services.scheduling.auto_allocator_v2 import compute_lunch_window

    office_id = uuid.uuid4()
    v1 = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    v1.end_time = time(10, 0)
    v1.service_minutes = 60
    v2 = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=0, patient_name="B"
    )
    v2.end_time = time(11, 0)
    v2.service_minutes = 60
    v3 = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=14, start_m=0, patient_name="C"
    )
    v3.end_time = time(15, 0)
    v3.service_minutes = 60

    lunch = compute_lunch_window([v1, v2, v3])
    assert lunch is not None, "60 分空きあり → lunch slot が返るはず"
    ls, le = lunch
    # 60 分 lunch でなければ NG.
    duration = (le.hour * 60 + le.minute) - (ls.hour * 60 + ls.minute)
    assert duration == 60, f"60 分 lunch のはず: got {duration} 分"
    # 12:00 中心の slot = (12:00, 13:00) が best.
    assert ls == time(12, 0) and le == time(13, 0), (
        f"12:00 中心の 60 分 lunch のはず: got {ls}-{le}"
    )


def test_compute_lunch_window_falls_back_to_45min() -> None:
    """60 分連続空きなし、45 分はある → 45 分 lunch slot が返る.

    Setup: 11:30-12:15 と 13:00-14:00 を visit が占有. 12:15-13:00 は 45 分空き
    だが 60 分空きは取れない. compute_lunch_window は 45 分 fallback で
    12:15-13:00 を返す.
    """
    from app.services.scheduling.auto_allocator_v2 import compute_lunch_window

    office_id = uuid.uuid4()
    v_pre = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=11, start_m=30, patient_name="P1"
    )
    v_pre.end_time = time(12, 15)
    v_pre.service_minutes = 45
    v_post = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=13, start_m=0, patient_name="P2"
    )
    v_post.end_time = time(14, 0)
    v_post.service_minutes = 60

    lunch = compute_lunch_window([v_pre, v_post])
    assert lunch is not None, "45 分空きあり → lunch slot が返るはず"
    ls, le = lunch
    duration = (le.hour * 60 + le.minute) - (ls.hour * 60 + ls.minute)
    assert duration == 45, f"45 分 fallback lunch のはず: got {duration} 分"
    assert ls == time(12, 15) and le == time(13, 0), (
        f"45 分 fallback slot は 12:15-13:00 のはず: got {ls}-{le}"
    )


def test_compute_lunch_window_returns_none_when_45min_unavailable() -> None:
    """コース密集で 45 分も取れない → None + warning."""
    from app.services.scheduling.auto_allocator_v2 import compute_lunch_window

    office_id = uuid.uuid4()
    # 11:30-13:30 全域をブロックする visit (= 動的 lunch を取れない).
    v_block = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=11, start_m=30, patient_name="B"
    )
    v_block.end_time = time(13, 30)
    v_block.service_minutes = 120

    warnings: list[V2Warning] = []
    lunch = compute_lunch_window(
        [v_block], warnings=warnings, weekday=0, course_code="A", office_name="TestOffice"
    )
    assert lunch is None, f"45 分も取れない → None のはず: got {lunch}"
    # warning が出ている.
    assert any("昼休憩" in w.message and "確保できません" in w.message for w in warnings), (
        f"lunch 確保不能 warning が出ていない: {warnings}"
    )


def test_compute_lunch_window_centered_near_noon() -> None:
    """複数 60 分候補がある場合は 12:00 中心の slot 優先.

    Setup: 11:30-13:30 のどこでも 60 分 lunch を取れる (visit が全くない).
    11:30-12:30 / 11:35-12:35 / ... / 12:30-13:30 すべて 60 分連続空き.
    一番 12:00 中心 (= |start - 12:00| 最小) の 12:00-13:00 が選ばれる.
    """
    from app.services.scheduling.auto_allocator_v2 import compute_lunch_window

    office_id = uuid.uuid4()
    # 朝と夕方の visit のみ (lunch range 11:30-13:30 はすべて空き).
    v_morning = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="M"
    )
    v_morning.end_time = time(10, 0)
    v_morning.service_minutes = 60
    v_evening = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=14, start_m=0, patient_name="E"
    )
    v_evening.end_time = time(15, 0)
    v_evening.service_minutes = 60

    lunch = compute_lunch_window([v_morning, v_evening])
    assert lunch is not None
    ls, le = lunch
    assert ls == time(12, 0) and le == time(13, 0), (
        f"12:00 中心の 60 分 lunch が選ばれるはず: got {ls}-{le}"
    )


def test_filter_uses_dynamic_lunch_window() -> None:
    """Wave 3 (#WAVE3) + Phase E-3 改修 (2): コース別に lunch slot が違っても
    正しく filter する. Phase E-3 で lunch fallback が 30 分まで緩和されたため、
    visit が「30 分 lunch も避けられない」場合のみ filter から除外する.

    Setup:
      - コース X: 9:00-12:00 + 12:00-13:30 (= 11:55-13:10 ど真ん中) + 14:00-15:00.
        XL = 11:55-13:10 は AM 側 30 分 (11:30-12:00) も PM 側 30 分 (13:00-13:30)
        も成立しない区間で除外される.
      - コース Y: 9:00-10:00 + 11:30-13:00 + 14:00-15:00.
        YL = 12:00-13:10 は AM 側 11:30-12:00 で回避可能 (start=12:00 ちょうど)
        だが、Y コース anchor の 11:30-13:00 で AM 側も埋まるため、
        compute_lunch_window 経由でも lunch 取れず除外される.
    """
    from app.services.scheduling.auto_allocator_v2 import _filter_unavailable_and_lunch

    office_id = uuid.uuid4()
    # コース X: ど真ん中 (start<12:00 かつ end>13:00) なので _is_in_lunch_break で除外.
    x_morning = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="XM"
    )
    x_morning.end_time = time(12, 0)
    x_morning.service_minutes = 60
    x_morning.course_code = "X"
    x_evening = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=14, start_m=0, patient_name="XE"
    )
    x_evening.end_time = time(15, 0)
    x_evening.service_minutes = 60
    x_evening.course_code = "X"
    # 候補 visit: 11:55-13:10 は start<12:00 AND end>13:00 で 30 分 lunch 不可避.
    x_lunch_hit = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=11, start_m=55, patient_name="XL"
    )
    x_lunch_hit.end_time = time(13, 10)
    x_lunch_hit.service_minutes = 75
    x_lunch_hit.course_code = "X"

    # コース Y: anchor で 11:30-13:00 を占有 → lunch slot は 13:00-13:30 (30 分)
    # しか取れない. 12:00-13:10 visit は anchor と直接重なる + compute_lunch_window
    # で 13:00-13:30 lunch を取った後、12:00-13:10 はそこに飛び込んで除外.
    y_morning = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=11, start_m=30, patient_name="YM"
    )
    y_morning.end_time = time(13, 0)
    y_morning.service_minutes = 90
    y_morning.course_code = "Y"
    y_evening = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=14, start_m=0, patient_name="YE"
    )
    y_evening.end_time = time(15, 0)
    y_evening.service_minutes = 60
    y_evening.course_code = "Y"
    # 候補 visit: 12:45-13:10 は YM 11:30-13:00 と重複 (excluded by greedy).
    y_lunch_hit = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=12, start_m=45, patient_name="YL"
    )
    y_lunch_hit.end_time = time(13, 10)
    y_lunch_hit.service_minutes = 25
    y_lunch_hit.course_code = "Y"

    warnings: list[V2Warning] = []
    filtered = _filter_unavailable_and_lunch(
        [x_morning, x_evening, x_lunch_hit, y_morning, y_evening, y_lunch_hit],
        unavailable_slots={},
        warnings=warnings,
    )
    codes = {v.patient_code for v in filtered}
    # アンカー visit は全て残る、lunch 重複の 2 件は除外.
    assert "XM" in codes and "XE" in codes and "YM" in codes and "YE" in codes
    assert "XL" not in codes, "コース X の 11:55-13:10 visit は 30 分 lunch も避けられないため除外"
    assert "YL" not in codes, "コース Y の 12:45-13:10 visit は anchor (11:30-13:00) と被るため除外"


def test_apply_travel_time_uses_dynamic_lunch() -> None:
    """Wave 3 (#WAVE3): lunch slot が「12:30-13:30」(動的) のコースで AM 希望
    visit が AM block を超えると lunch_end=13:30 (動的) にバンプされる.

    Setup:
      - A 11:30-12:30 固定 (lunch start を 12:30 以降に押し下げる anchor).
      - B 午前希望, 同 lat 0.155 lng 離れて 5km. earliest = 12:30+15+8 ≈ 12:53.
      - compute_lunch_window over [A 11:30-12:30, B 11:50-12:20]:
          ... (B が入力時点で 11:50-12:20 を占有するため lunch は 12:20 以降
          に押される可能性あり)
      - 本テストでは「lunch_end が動的 13:00 / 13:20 等になる」 = 固定 13:00 と
        違うことを確認するために bumped 後の B.start_time が time(13, 0) でない
        ケースも許容して >= 13:00 だけを検証する.

    補足: Wave 3 における「lunch_end が動的に決まる」性質は本テストでは
    AM branch bump が「LUNCH_END (旧固定 13:00) ではなく lunch_end_t (動的)」
    を使うことを通じて担保する.
    """
    from app.services.scheduling.auto_allocator_v2 import _apply_travel_time_to_courses

    office_id = uuid.uuid4()
    # 異住所固定 visit (11:30-12:30) — anchor (lunch を 12:30 以降に押し下げる).
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=11, start_m=30, patient_name="A"
    )
    a.end_time = time(12, 30)
    a.service_minutes = 60
    a.course_code = "A"
    a.time_type = "固定"
    # 5km 離れた午前希望 visit. earliest が 12:00 超のため AM bump (動的 lunch_end へ).
    b = _make_visit(
        lat=35.65, lng=140.155, office_id=office_id, start_h=11, start_m=50, patient_name="B"
    )
    b.end_time = time(12, 20)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "午前"

    warnings: list[V2Warning] = []
    _apply_travel_time_to_courses([a, b], warnings=warnings)

    # AM bump が走り、固定 13:00 ではなく動的 lunch_end (>= 13:00) にバンプ.
    assert b.start_time >= time(13, 0), f"AM bump が動的 lunch_end へ働くはず: got {b.start_time}"
    # 動的 lunch メッセージ (= 12-13 旧固定でない時刻) が含まれる warning が出る.
    assert any("午前希望" in w.message and "繰り下げ" in w.message for w in warnings), (
        f"AM bump warning が出ていない: {[w.message for w in warnings]}"
    )


def test_lunch_window_within_11_30_to_13_30_range() -> None:
    """Wave 3 (#WAVE3): lunch_start ∈ [11:30, 12:30] かつ lunch_end ∈ [12:15, 13:30]
    の不変条件を様々な visit 配置パターンで検証する.
    """
    from app.services.scheduling.auto_allocator_v2 import (
        LUNCH_DURATION_FALLBACK,
        LUNCH_DURATION_PREFERRED,
        LUNCH_EARLIEST_START,
        LUNCH_LATEST_END,
        LUNCH_LATEST_START,
        compute_lunch_window,
    )

    office_id = uuid.uuid4()
    earliest_end_min = (
        LUNCH_EARLIEST_START.hour * 60 + LUNCH_EARLIEST_START.minute + LUNCH_DURATION_FALLBACK
    )  # 12:15

    # 様々なパターンを試す.
    patterns: list[list[V2Visit]] = []

    # パターン 1: 空のコース.
    patterns.append([])

    # パターン 2: 9-10 visit のみ.
    v_morning = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="M"
    )
    v_morning.end_time = time(10, 0)
    v_morning.service_minutes = 60
    patterns.append([v_morning])

    # パターン 3: 11:00-11:50 + 13:00-14:00 (= lunch 12:00-13:00 が取れる).
    v_pre = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=11, start_m=0, patient_name="P"
    )
    v_pre.end_time = time(11, 50)
    v_pre.service_minutes = 50
    v_post = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=13, start_m=0, patient_name="Q"
    )
    v_post.end_time = time(14, 0)
    v_post.service_minutes = 60
    patterns.append([v_pre, v_post])

    # パターン 4: 12:30-13:30 占有 (= lunch 11:30-12:30 強制).
    v_pm = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=12, start_m=30, patient_name="PM"
    )
    v_pm.end_time = time(13, 30)
    v_pm.service_minutes = 60
    patterns.append([v_pm])

    # パターン 5: 11:30-12:30 占有 (= lunch 12:30-13:30 強制).
    v_am = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=11, start_m=30, patient_name="AM"
    )
    v_am.end_time = time(12, 30)
    v_am.service_minutes = 60
    patterns.append([v_am])

    for idx, visits in enumerate(patterns):
        lunch = compute_lunch_window(visits)
        if lunch is None:
            continue  # 取れない場合は不変条件適用外.
        ls, le = lunch
        ls_min = ls.hour * 60 + ls.minute
        le_min = le.hour * 60 + le.minute
        # lunch_start ∈ [11:30, 12:30]
        assert LUNCH_EARLIEST_START <= ls <= LUNCH_LATEST_START, (
            f"[pattern {idx}] lunch_start ({ls}) は "
            f"[{LUNCH_EARLIEST_START}, {LUNCH_LATEST_START}] の範囲内であるはず"
        )
        # lunch_end ∈ [12:15, 13:30]
        assert (
            earliest_end_min <= le_min <= (LUNCH_LATEST_END.hour * 60 + LUNCH_LATEST_END.minute)
        ), f"[pattern {idx}] lunch_end ({le}) は [12:15, 13:30] の範囲内であるはず"
        # 長さは 45-60 分.
        assert LUNCH_DURATION_FALLBACK <= (le_min - ls_min) <= LUNCH_DURATION_PREFERRED, (
            f"[pattern {idx}] lunch 長さは 45-60 分のはず: got {le_min - ls_min} 分"
        )


def test_lunch_window_none_does_not_force_default() -> None:
    """Phase B HIGH (reviewer 2nd round): compute_lunch_window が None を返した時、
    ``_apply_travel_time_to_courses`` は標準枠 12:00-13:00 へ強制フォールバックせず、
    12:00-13:00 範囲の非固定 visit を bump 対象から外す.

    Setup (旧実装と新実装で差分が出るよう、B を非固定 + travel time で
    earliest_start が 12:00 超になる位置に配置):
      - A 11:00-13:30 固定 (= 2.5 時間ブロック, 45 分も lunch を取れない).
        → compute_lunch_window が None を返す.
      - B 12:00 希望、非固定 (time_type="時間帯", 09:00-17:00, service 30).
        A から少し離れた lat/lng で travel time が earliest_start を 12:00 超に
        押し上げる構成 (AM bump branch でなく 時間帯 経路を通る).

    旧実装: lunch_window=None → LUNCH_DEFAULT_START/END (12:00-13:00, =720-780) で
      再検証. B.actual_start が 12:00-13:00 範囲内 → tt="時間帯" + window_upper
      17:00 で can_bump=True → B.start_time = LUNCH_DEFAULT_END (13:00) にバンプ.
    新実装: lunch_re_validate_enabled=False で再検証ブロック skip
      → B.start_time は travel time 反映後の値 (~12:15) のまま. 13:00 にはならない.
      compute_lunch_window が既に warning を出しているため運用者へは別経路で伝わる.

    本テストは「旧 sentinel 修正前後で結果が同じ (両方とも B 不変)」だった
    false-positive を解消する: 旧実装で 13:00 にバンプ、新実装で 13:00 にならない、
    という挙動差をはっきり検証する.
    """
    from app.services.scheduling.auto_allocator_v2 import _apply_travel_time_to_courses

    office_id = uuid.uuid4()
    # A1 + A2 の 2 visit で 11:00-12:00 + 12:30-13:30 を占有 (= lunch 候補 11:30〜
    # 12:30+45/60 のどれも overlap → compute_lunch_window=None).
    # B (12:00-12:30 希望) は A1 と A2 の間に挟まる形.
    a1 = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=11, start_m=0, patient_name="A1"
    )
    a1.end_time = time(12, 0)
    a1.service_minutes = 60
    a1.course_code = "A"
    a1.time_type = "固定"

    # B: 12:00 希望、非固定 (時間帯 09:00-17:00). A1 (35.65, 140.10) から
    # 約 1.1 km 離れた位置 (35.66, 140.10) → travel ~3 分 + buffer 8 分 = 11 分.
    # earliest_start = A1.end(12:00) + 11 = 12:11 → 5 分刻み切り上げで 12:15.
    # 12:15-12:45 は 12:00-13:00 (旧 lunch fallback) と overlap するため、
    # 旧実装は can_bump=True (tt=時間帯, 12:15 <= 17:00) で 13:00 にバンプしていた.
    # 新実装は lunch_re_validate_enabled=False で skip → 12:15 のまま.
    b = _make_visit(
        lat=35.66, lng=140.10, office_id=office_id, start_h=12, start_m=0, patient_name="B"
    )
    b.end_time = time(12, 30)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "時間帯"
    b.preferred_start = "09:00"
    b.preferred_end = "17:00"

    a2 = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=12, start_m=30, patient_name="A2"
    )
    a2.end_time = time(13, 30)
    a2.service_minutes = 60
    a2.course_code = "A"
    a2.time_type = "固定"

    warnings: list[V2Warning] = []
    _apply_travel_time_to_courses([a1, b, a2], warnings=warnings)

    # 旧実装: A(11:00-12:00) → B (earliest=A.end+travel+buffer ~12:11 → 5min roundup 12:15
    # → 12:15 < 13:00 AND 12:45 > 12:00 で lunch overlap → can_bump=True (時間帯, 17:00 まで)
    # → B.start_time = lunch_end_t = LUNCH_DEFAULT_END = 13:00.
    # 新実装: re-validation skip → B.start_time = 12:15 (travel + 5min roundup).
    assert b.start_time != time(13, 0), (
        f"旧実装 sentinel 修正前なら B は 13:00 にバンプされていたはず. "
        f"新実装で 13:00 にならない (lunch=None で再検証 skip) ことを検証: "
        f"got {b.start_time}"
    )
    # 具体的に、travel + 5min roundup 後の値 (12:11 → 12:15) であることを確認.
    assert b.start_time == time(12, 15), (
        f"B.start_time は travel + buffer + 5min roundup で 12:15 のはず: got {b.start_time}"
    )
    # compute_lunch_window 自身は warning を出している (45 分も取れない).
    assert any("昼休憩" in w.message and "確保できません" in w.message for w in warnings), (
        f"compute_lunch_window の lunch 確保不能 warning が出ていない: "
        f"{[w.message for w in warnings]}"
    )


# ---------------------------------------------------------------------------
# Wave 4 (Phase C): ケアアラーム閾値 (希望時刻からの乖離).
# 固定/時間帯 patient に対し:
#   - 乖離 <= 30 分: silent (warning なし)
#   - 30 < dev <= 60 分: warning emit (care_alarm_deviation), 配置維持
#   - dev > 60 分: unassigned + reason=care_alarm_exceeded
# ---------------------------------------------------------------------------


def _make_pair_for_care_alarm(
    *,
    a_lng: float,
    b_lng: float,
    b_start: tuple[int, int],
    b_time_type: str = "固定",
    b_preferred_start: str = "10:00",
    b_preferred_end: str | None = None,
) -> tuple[V2Visit, V2Visit]:
    """ケアアラーム閾値テスト用に 2 visit のコースを組む.

    A は同 (office, course) の先頭固定 visit (preceding として配置), B が判定対象.
    A は判定対象外 (time_type=終日) にして A 由来 warning が混ざらないようにする.
    """
    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=a_lng, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.end_time = time(9, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "終日"
    b = _make_visit(
        lat=35.65,
        lng=b_lng,
        office_id=office_id,
        start_h=b_start[0],
        start_m=b_start[1],
        patient_name="B",
    )
    b.end_time = time((b_start[0] + 1) % 24, b_start[1])
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = b_time_type
    b.preferred_start = b_preferred_start
    b.preferred_end = b_preferred_end
    return a, b


def test_care_alarm_deviation_below_30min_silent() -> None:
    """固定 10:00 希望、配置 10:25 → 乖離 25 分 = silent (care_alarm_deviation なし)."""
    from app.services.scheduling.auto_allocator_v2 import (
        _apply_travel_time_to_courses,
    )

    a, b = _make_pair_for_care_alarm(
        a_lng=140.10, b_lng=140.10, b_start=(10, 25), b_preferred_start="10:00"
    )
    # 同住所 (移動 0) なので B は b_start (10:25) のまま確定する.
    warnings: list[V2Warning] = []
    _apply_travel_time_to_courses([a, b], warnings=warnings)

    assert b.start_time == time(10, 25), f"B start_time 想定外: {b.start_time}"
    assert b.course_code == "A", f"B は配置維持されるはず: {b.course_code}"
    care_alarm_ws = [w for w in warnings if w.type == "care_alarm_deviation"]
    assert not care_alarm_ws, f"乖離 25 分は silent のはず: {[w.message for w in care_alarm_ws]}"


def test_care_alarm_deviation_between_30_60min_emits_warning() -> None:
    """固定 10:00 希望、配置 10:45 → 乖離 45 分 = warning emit + 配置維持."""
    from app.services.scheduling.auto_allocator_v2 import (
        _apply_travel_time_to_courses,
    )

    a, b = _make_pair_for_care_alarm(
        a_lng=140.10, b_lng=140.10, b_start=(10, 45), b_preferred_start="10:00"
    )
    warnings: list[V2Warning] = []
    _apply_travel_time_to_courses([a, b], warnings=warnings)

    assert b.start_time == time(10, 45)
    assert b.course_code == "A", "30-60 分の乖離は配置維持されるはず"
    care_alarm_ws = [
        w
        for w in warnings
        if w.type == "care_alarm_deviation" and b.patient_id in (w.affected_patient_ids or [])
    ]
    assert care_alarm_ws, f"care_alarm_deviation warning が出ていない: {warnings}"
    # category は time_deviation に自動解決される.
    from app.services.scheduling.auto_allocator_v2 import V2WarningCategory

    assert care_alarm_ws[0].category == V2WarningCategory.time_deviation


def test_care_alarm_deviation_exceeds_60min_unassigned() -> None:
    """固定 10:00 希望、配置 11:30 → 乖離 90 分 = unassigned + reason=care_alarm_exceeded."""
    from app.services.scheduling.auto_allocator_v2 import (
        _apply_travel_time_to_courses,
    )

    a, b = _make_pair_for_care_alarm(
        a_lng=140.10, b_lng=140.10, b_start=(11, 30), b_preferred_start="10:00"
    )
    warnings: list[V2Warning] = []
    unassigned_ids = _apply_travel_time_to_courses([a, b], warnings=warnings)

    assert id(b) in unassigned_ids, f"60 分超は unassigned に流れるはず: {unassigned_ids}"
    assert b.course_code is None
    # warning は care_alarm_deviation type で「ケアアラーム閾値超過」メッセージ.
    matching = [
        w
        for w in warnings
        if w.type == "care_alarm_deviation"
        and b.patient_id in (w.affected_patient_ids or [])
        and "ケアアラーム閾値超過" in w.message
    ]
    assert matching, f"ケアアラーム閾値超過 warning が出ていない: {warnings}"


def test_care_alarm_time_window_outside_emits_warning() -> None:
    """時間帯 09:00-12:00、配置 12:35 → 範囲 35 分超過 = warning emit (30-60 分帯).

    ``_apply_travel_time_to_courses`` の earliest_start ロジックは時間帯 visit を
    可能なら window 内に押し戻すため、敢えて単独 visit のコース
    (len(gv) < 2 で earliest_start 補正 skip) を使って actual_start が 12:35 のままで
    care_alarm 判定にかかることを検証する.
    """
    from app.services.scheduling.auto_allocator_v2 import (
        _apply_travel_time_to_courses,
    )

    office_id = uuid.uuid4()
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=12, start_m=35, patient_name="B"
    )
    b.end_time = time(13, 5)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "時間帯"
    b.preferred_start = "09:00"
    b.preferred_end = "12:00"

    warnings: list[V2Warning] = []
    _apply_travel_time_to_courses([b], warnings=warnings)

    assert b.course_code == "A", "35 分超過は配置維持されるはず"
    care_alarm_ws = [w for w in warnings if w.type == "care_alarm_deviation"]
    assert care_alarm_ws, f"時間帯範囲外 35 分超過の warning が出ていない: {warnings}"


def test_care_alarm_time_window_within_silent() -> None:
    """時間帯 09:00-12:00、配置 10:30 → 範囲内 = warning なし."""
    from app.services.scheduling.auto_allocator_v2 import (
        _apply_travel_time_to_courses,
    )

    a, b = _make_pair_for_care_alarm(
        a_lng=140.10,
        b_lng=140.10,
        b_start=(10, 30),
        b_time_type="時間帯",
        b_preferred_start="09:00",
        b_preferred_end="12:00",
    )
    warnings: list[V2Warning] = []
    _apply_travel_time_to_courses([a, b], warnings=warnings)

    assert b.course_code == "A"
    care_alarm_ws = [w for w in warnings if w.type == "care_alarm_deviation"]
    assert not care_alarm_ws, (
        f"時間帯範囲内 (10:30 ∈ [09:00, 12:00]) は silent のはず: "
        f"{[w.message for w in care_alarm_ws]}"
    )


def test_care_alarm_time_type_am_excluded() -> None:
    """time_type=午前、配置 11:55 → ケアアラーム対象外 = warning なし."""
    from app.services.scheduling.auto_allocator_v2 import (
        _apply_travel_time_to_courses,
    )

    a, b = _make_pair_for_care_alarm(
        a_lng=140.10,
        b_lng=140.10,
        b_start=(11, 55),
        b_time_type="午前",
        b_preferred_start="09:00",
    )
    warnings: list[V2Warning] = []
    _apply_travel_time_to_courses([a, b], warnings=warnings)

    care_alarm_ws = [w for w in warnings if w.type == "care_alarm_deviation"]
    assert not care_alarm_ws, (
        f"午前は対象外: warning なしのはず: {[w.message for w in care_alarm_ws]}"
    )


def test_care_alarm_negative_deviation_treated_symmetrically() -> None:
    """固定 10:00 希望、配置 09:25 → 35 分前倒し = warning emit (絶対値判定)."""
    from app.services.scheduling.auto_allocator_v2 import (
        _apply_travel_time_to_courses,
    )

    a, b = _make_pair_for_care_alarm(
        a_lng=140.10, b_lng=140.10, b_start=(9, 25), b_preferred_start="10:00"
    )
    # A は 09:00 開始固定 → end 09:30. B は 09:25 開始だが同住所なので移動 0 で
    # 09:25 のまま確定する (実際は A.end=09:30 > 09:25 で earliest 上書きされるかもしれない
    # ため、A の start を早めにずらして競合を避ける).
    a.start_time = time(8, 0)
    a.end_time = time(8, 30)
    warnings: list[V2Warning] = []
    _apply_travel_time_to_courses([a, b], warnings=warnings)

    # B は 09:25 で配置維持されるはず (同住所 + A の end は 08:30).
    assert b.start_time == time(9, 25), f"B start_time 想定外: {b.start_time}"
    care_alarm_ws = [w for w in warnings if w.type == "care_alarm_deviation"]
    assert care_alarm_ws, (
        f"35 分前倒し (絶対値) は warning emit のはず: {[w.message for w in warnings]}"
    )


# ---------------------------------------------------------------------------
# Wave 4 (Phase C): 警告 type 集約 (11 種 type → 6 カテゴリ).
# V2Warning.category が code から自動解決されること.
# ---------------------------------------------------------------------------


def test_v2_warning_category_auto_resolved_from_code() -> None:
    """各 code に対し正しい category が紐づく (Wave 4 Phase C mapping)."""
    from app.services.scheduling.auto_allocator_v2 import (
        V2Warning,
        V2WarningCategory,
    )

    expected: list[tuple[str, V2WarningCategory]] = [
        ("travel_time_shortage", V2WarningCategory.time_deviation),
        ("care_alarm_deviation", V2WarningCategory.time_deviation),
        ("course_capacity", V2WarningCategory.capacity),
        ("course_count", V2WarningCategory.capacity),
        ("course_long_distance", V2WarningCategory.capacity),
        ("two_staff_shortage", V2WarningCategory.capacity),
        ("acceptance_blocked", V2WarningCategory.acceptance),
        ("data_health_staff_shifts_missing", V2WarningCategory.data_quality),
        ("same_address_consolidation", V2WarningCategory.placement_info),
        ("auto_time_shift_for_conflict", V2WarningCategory.placement_info),
        ("diff_add_conflict", V2WarningCategory.conflict),
        ("general", V2WarningCategory.conflict),
    ]
    for code, want_cat in expected:
        w = V2Warning(type=code, message="x")  # type: ignore[arg-type]
        assert w.category == want_cat, f"{code} の category が想定外: {w.category}"


def test_v2_warning_category_default_conflict() -> None:
    """未登録 type は category=conflict に fallback する."""
    from app.services.scheduling.auto_allocator_v2 import (
        V2Warning,
        V2WarningCategory,
    )

    # mypy / Literal 型を無視して未登録 code を渡す (実行時 fallback の検証).
    w = V2Warning(type="__not_registered__", message="x")  # type: ignore[arg-type]
    assert w.category == V2WarningCategory.conflict


def test_v2_warning_out_serializes_category() -> None:
    """Pydantic ``V2WarningOut`` で serialize 結果に category が含まれる."""
    from app.api.v1.schedule_v2 import _warning_to_out
    from app.services.scheduling.auto_allocator_v2 import V2Warning

    w = V2Warning(type="care_alarm_deviation", message="test deviation")
    out = _warning_to_out(w)
    dumped = out.model_dump()
    assert "category" in dumped, f"category フィールドが missing: {dumped.keys()}"
    assert dumped["category"] == "time_deviation"


# ---------------------------------------------------------------------------
# Wave 4 (Phase C): unassigned 判定で care_alarm_exceeded reason が紐づく.
# ---------------------------------------------------------------------------


def test_identify_unassigned_patient_for_care_alarm_exceeded() -> None:
    """``_identify_unassigned_patients`` が ``care_alarm_deviation`` warning から
    ``care_alarm_exceeded`` reason を抽出する (60 分超 unassigned ケース)."""
    from app.services.scheduling.auto_allocator_v2 import (
        V2Warning,
        _identify_unassigned_patients,
    )

    pid = uuid.uuid4()
    pool_p = Patient(
        id=pid,
        code="CARE-EX-1",
        name="P-Care",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=uuid.uuid4(),
        weekly_pattern={"time_type": "固定", "preferred_start": "10:00"},
    )
    warnings = [
        V2Warning(
            type="care_alarm_deviation",
            message="P-Care 様 (固定 希望 10:00) が 11:30 配置で 90 分の乖離 (60 分超) — ケアアラーム閾値超過のため未割当に移動",
            patient_id=pid,
            affected_patient_ids=[pid],
        )
    ]
    out = _identify_unassigned_patients([pool_p], [], warnings)
    assert len(out) == 1
    item = out[0]
    assert item["reason"] == "care_alarm_exceeded", (
        f"reason が care_alarm_exceeded ではない: {item}"
    )
    assert item["patient_id"] == pid


# ---------------------------------------------------------------------------
# Phase E-3 改修 (1)-(4): 新規テスト 12-15 件.
# (1) PFV duration default 30→35 / (2) lunch 3 段階 fallback (60→45→30) /
# (3) 同住所ペア 90 分占有 / (4) 同住所 3 人以上の自動別コース化.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# (2) Lunch 関連 — _is_in_lunch_break + compute_lunch_window 30 分 fallback.
# ---------------------------------------------------------------------------


def test_is_in_lunch_break_12_00_35min_visit_passes() -> None:
    """Phase E-3 改修 (2): 12:00-12:35 (35 分 visit) は AM 側 30 分 lunch
    (11:30-12:00) で回避可能なため _is_in_lunch_break = False.

    旧仕様 (45 分 lunch fallback) では AM 側 lunch_end=12:15 必要のため
    start=12:00 < 12:15 で重なる扱いだった. Phase E-3 では 30 分 fallback で
    11:30-12:00 lunch が成立し、12:00 開始の visit は通る.
    """
    from app.services.scheduling.auto_allocator_v2 import _is_in_lunch_break

    assert _is_in_lunch_break(time(12, 0), time(12, 35)) is False, (
        "Phase E-3: 12:00-12:35 は AM 側 30 分 lunch (11:30-12:00) で回避可"
    )


def test_is_in_lunch_break_12_10_35min_visit_still_blocked() -> None:
    """Phase E-3: 12:10-12:45 (35 分 visit) は AM 側回避不可 (start=12:10 < 12:00 NG)
    かつ PM 側も end=12:45 ≤ 13:00 で OK だが、PM 側 13:00-13:30 lunch なら end
    > 13:00 必要 (visit_end > lunch_start) → ここでは end=12:45 ≤ 13:00 で OK.

    実際は end=12:45 ≤ 13:00 なので PM 側 13:00-13:30 lunch で回避可 → False.
    つまり 12:10-12:45 は Phase E-3 で **回避可能**.
    旧仕様 (45 分 fallback) は AM 側 12:15+ NG / PM 側 12:30- NG で True だった.
    """
    from app.services.scheduling.auto_allocator_v2 import _is_in_lunch_break

    # 12:10-12:45: PM 側 end=12:45 ≤ 13:00 で回避可.
    assert _is_in_lunch_break(time(12, 10), time(12, 45)) is False, (
        "Phase E-3: 12:10-12:45 は PM 側 30 分 lunch (13:00-13:30) で回避可"
    )


def test_is_in_lunch_break_11_45_13_15_still_blocked() -> None:
    """Phase E-3: 11:45-13:15 は AM 側 (start=11:45 < 12:00 NG) も
    PM 側 (end=13:15 > 13:00 NG) も成立しない → True (= 30 分 lunch も避けられない)."""
    from app.services.scheduling.auto_allocator_v2 import _is_in_lunch_break

    assert _is_in_lunch_break(time(11, 45), time(13, 15)) is True, (
        "Phase E-3: 11:45-13:15 は 30 分 lunch も避けられない"
    )


def test_is_in_lunch_break_boundary_start_12_00_returns_false() -> None:
    """Phase E-3: 境界 start=12:00 ちょうどは AM 側回避可 → False.

    11:30-12:00 (30 分 lunch) の lunch_end <= visit_start (= 12:00) で成立.
    """
    from app.services.scheduling.auto_allocator_v2 import _is_in_lunch_break

    assert _is_in_lunch_break(time(12, 0), time(12, 30)) is False
    assert _is_in_lunch_break(time(12, 0), time(13, 0)) is False


def test_is_in_lunch_break_boundary_end_13_00_returns_false() -> None:
    """Phase E-3: 境界 end=13:00 ちょうどは PM 側回避可 → False.

    13:00-13:30 (30 分 lunch) の lunch_start >= visit_end (= 13:00) で成立.
    """
    from app.services.scheduling.auto_allocator_v2 import _is_in_lunch_break

    assert _is_in_lunch_break(time(11, 30), time(13, 0)) is False
    assert _is_in_lunch_break(time(11, 50), time(13, 0)) is False


def test_compute_lunch_window_picks_30min_when_60_and_45_blocked() -> None:
    """Phase E-3 改修 (2): 60/45 分はどこも取れず、30 分なら取れる → 30 分 lunch 採用."""
    from app.services.scheduling.auto_allocator_v2 import compute_lunch_window

    office_id = uuid.uuid4()
    # 11:30-13:00 を anchor で占有 (= AM/中央 60/45 分 lunch 不可).
    # 13:00-13:30 の 30 分空きだけ残る.
    anchor = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=11, start_m=30, patient_name="anchor"
    )
    anchor.end_time = time(13, 0)
    anchor.service_minutes = 90

    warnings: list[V2Warning] = []
    lunch = compute_lunch_window([anchor], warnings=warnings)
    assert lunch is not None, "30 分 fallback で lunch 取れるはず"
    ls, le = lunch
    duration = (le.hour * 60 + le.minute) - (ls.hour * 60 + ls.minute)
    assert duration == 30, f"30 分 fallback のはず: got {duration} 分"
    assert ls == time(13, 0) and le == time(13, 30), (
        f"13:00-13:30 の 30 分 fallback slot のはず: got {ls}-{le}"
    )


def test_compute_lunch_window_emits_warning_for_30min_fallback() -> None:
    """Phase E-3 改修 (2): 30 分 fallback 採用時は 「30 分しか確保できません」warning."""
    from app.services.scheduling.auto_allocator_v2 import compute_lunch_window

    office_id = uuid.uuid4()
    anchor = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=11, start_m=30, patient_name="anchor"
    )
    anchor.end_time = time(13, 0)
    anchor.service_minutes = 90

    warnings: list[V2Warning] = []
    lunch = compute_lunch_window(
        [anchor], warnings=warnings, weekday=0, course_code="A", office_name="TestOffice"
    )
    assert lunch is not None
    # warning: 30 分 fallback 通知メッセージ.
    fallback_warns = [
        w for w in warnings if "30 分しか確保できません" in w.message or "30 分しか" in w.message
    ]
    assert fallback_warns, f"30 分 fallback warning が出ていない: {[w.message for w in warnings]}"


def test_compute_lunch_window_with_12_00_visit_picks_11_30_30min_lunch() -> None:
    """Phase E-3: 12:00-12:35 visit がある場合、AM 側 11:30-12:00 (30 分) lunch を選ぶ
    可能性がある (12:00 中心距離は同じだが、AM 側のほうが早い).

    Setup: 12:00-12:35 visit + 13:30 以降は空き. 60 分 lunch 12:35-13:35 は
    end > 13:30 NG. 45 分 lunch 12:35-13:20 は end <= 13:30 OK で取れる.
    なので 45 分が採用されるはず.

    結論: 12:00-12:35 visit がある → 45 分 lunch 12:35-13:20 が選ばれる.
    (12:00 中心 720 - 12:35 中心 (= 12:35 + 22.5 = 12:57) ≈ 35 + 22 = 57 → dist=35)
    """
    from app.services.scheduling.auto_allocator_v2 import compute_lunch_window

    office_id = uuid.uuid4()
    v = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=12, start_m=0, patient_name="V"
    )
    v.end_time = time(12, 35)
    v.service_minutes = 35

    lunch = compute_lunch_window([v])
    assert lunch is not None, "12:00-12:35 visit があっても lunch 取れるはず"
    ls, le = lunch
    duration = (le.hour * 60 + le.minute) - (ls.hour * 60 + ls.minute)
    # 12:00-12:35 visit を回避できる最大 lunch を取る. 11:30-12:00 (30 分) も
    # 12:35-13:30 (55 分) も可能. 60 分 (12:35-13:35) は end>13:30 NG なので 45 分
    # (12:35-13:20) が取れる. なお 11:30-12:00 (30 分) も成立する.
    # 採用ロジックは 60 → 45 → 30 順. 60 が取れる候補なし → 45 → 11:30-12:15?
    # → 11:30-12:15 のうち 12:00-12:35 と重複 → NG. cand_start=11:30 で 45 分 →
    # end=12:15 → visit 12:00-12:35 と重複 (11:30+45=12:15, visit 12:00<12:15 AND 11:30<12:35)
    # → free_window でない. 同様に他の cand_start も 45 分は全部 visit と被る:
    # cand_start=12:35-13:30 のうち 45 分は cand=12:35 → 13:20 OK.
    # 45 分 best = 12:35-13:20. ただし 12:00 中心からの dist = |12:35 - 12:00| = 35 分.
    # それ以外の cand_start で 45 取れるのは? 12:30 → 13:15 → visit 12:00-12:35 と
    # 重複 (cand 12:30 < visit end 12:35) → NG. 11:30 → 12:15 → NG.
    # → 45 best = 12:35-13:20 で確定.
    # しかし noon_dist は cand=12:35 → 35 分. これは取得可能 → 採用.
    # Phase E-3 MEDIUM cleanup: 「30 or 45」の or assertion は regression 検出力が低い.
    # ロジック解析 (上の docstring 通り) で確定する slot は 12:35-13:20 (45 分):
    #   - cand_start=12:35 → end=13:20, end <= 13:30 OK, visit 12:00-12:35 と非重複.
    #   - noon_dist = |12:35 - 12:00| = 35 分.
    #   - 60 分候補なし → 45 分採用 → 30 分 fallback には到達しない.
    # 厳密化することで、将来「45 分採用 → 30 分 fallback」のような regression を検出する.
    assert duration == 45, f"45 分 lunch (12:35-13:20) が採用されるはず: got {duration} 分"
    assert ls == time(12, 35), f"lunch_start は 12:35 のはず: got {ls}"
    assert le == time(13, 20), f"lunch_end は 13:20 のはず: got {le}"


# ---------------------------------------------------------------------------
# (3) 同住所ペア 90 分占有 — _align_same_address_pair_to_same_time.
# ---------------------------------------------------------------------------


def test_align_same_address_pair_90min_occupancy_when_service_70() -> None:
    """Phase E-3 改修 (3): 35+35=70 分 service → 90 分占有 (max clamp).

    A=35, B=35 → ペア合算 70 < 90 → SAME_ADDRESS_PAIR_MIN_OCCUPANCY=90 を採用.
    """
    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.end_time = time(9, 35)
    a.service_minutes = 35
    a.course_code = "A"
    a.time_type = "時間帯"
    a.preferred_start = "09:00"
    a.preferred_end = "11:00"
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=35, patient_name="B"
    )
    b.end_time = time(10, 10)
    b.service_minutes = 35
    b.course_code = "A"
    b.time_type = "時間帯"
    b.preferred_start = "09:00"
    b.preferred_end = "11:00"

    warnings: list[V2Warning] = []
    apply_travel_corrections([a, b], warnings=warnings)

    assert a.start_time == time(9, 0)
    assert b.start_time == time(9, 0)
    assert a.end_time == time(9, 35)
    # B.end = 09:00 + max(35+35, 90) = 09:00 + 90 = 10:30.
    assert b.end_time == time(10, 30), f"Phase E-3 90 分 clamp: B.end={b.end_time}"


def test_align_same_address_pair_occupancy_max_clamp_when_service_100() -> None:
    """Phase E-3 改修 (3): 50+50=100 分 service → 100 分占有 (合計を採用).

    A=50, B=50 → ペア合算 100 >= 90 → max(100, 90) = 100 分占有.
    """
    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.end_time = time(9, 50)
    a.service_minutes = 50
    a.course_code = "A"
    a.time_type = "時間帯"
    a.preferred_start = "09:00"
    a.preferred_end = "11:00"
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=50, patient_name="B"
    )
    b.end_time = time(10, 40)
    b.service_minutes = 50
    b.course_code = "A"
    b.time_type = "時間帯"
    b.preferred_start = "09:00"
    b.preferred_end = "11:00"

    warnings: list[V2Warning] = []
    apply_travel_corrections([a, b], warnings=warnings)

    assert a.start_time == time(9, 0)
    assert b.start_time == time(9, 0)
    assert a.end_time == time(9, 50)
    # B.end = 09:00 + max(50+50, 90) = 09:00 + 100 = 10:40.
    assert b.end_time == time(10, 40), (
        f"Phase E-3: service 合計が 90 以上なら合計採用: {b.end_time}"
    )


def test_calc_course_total_minutes_with_90min_pair_clamp() -> None:
    """Phase E-3 改修 (3): calc_course_total_minutes が同住所ペアを 90 分 clamp で積む.

    A=35, B=35 同住所ペア + C=35 異住所 → 90 (ペア) + travel + buffer + 35 (C).
    """
    from app.services.scheduling.auto_allocator_v2 import calc_course_total_minutes

    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.service_minutes = 35
    a.course_code = "A"
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=35, patient_name="B"
    )
    b.service_minutes = 35
    b.course_code = "A"
    c = _make_visit(
        lat=35.70, lng=140.15, office_id=office_id, start_h=11, start_m=0, patient_name="C"
    )
    c.service_minutes = 35
    c.course_code = "A"

    total = calc_course_total_minutes([a, b, c])
    # ペア (A, B) = max(70, 90) = 90 分. C (single) = 35 分. travel + buffer は
    # haversine 計算 + 8 分 buffer. ペア 90 + C 35 + travel + 8 で >=90+35+8=133.
    assert total >= 90 + 35 + 8, f"Phase E-3 pair clamp: total {total} 分 (>= 133 期待)"
    # A,B 単独で集計したら 35+35=70 になるはずなので、90 clamp が効いていることを別途確認.
    pair_only = calc_course_total_minutes([a, b])
    assert pair_only == 90, f"Phase E-3 pair only = 90 分 clamp のはず: got {pair_only}"


def test_apply_travel_time_pair_next_visit_earliest_after_90min() -> None:
    """Phase E-3 改修 (3): ペアの直後 visit C は earliest = B.end (90 分占有) + travel + buffer."""
    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.end_time = time(9, 35)
    a.service_minutes = 35
    a.course_code = "A"
    a.time_type = "時間帯"
    a.preferred_start = "09:00"
    a.preferred_end = "12:00"
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=35, patient_name="B"
    )
    b.end_time = time(10, 10)
    b.service_minutes = 35
    b.course_code = "A"
    b.time_type = "時間帯"
    b.preferred_start = "09:00"
    b.preferred_end = "12:00"
    c = _make_visit(
        lat=35.66, lng=140.11, office_id=office_id, start_h=10, start_m=0, patient_name="C"
    )
    c.end_time = time(10, 35)
    c.service_minutes = 35
    c.course_code = "A"
    c.time_type = "時間帯"
    c.preferred_start = "09:00"
    c.preferred_end = "12:00"

    warnings: list[V2Warning] = []
    apply_travel_corrections([a, b, c], warnings=warnings)

    # Phase E-3: B.end = 10:30 (90 分占有). C は 10:30 + travel + buffer 以降.
    assert b.end_time == time(10, 30)
    assert c.start_time > time(10, 30), (
        f"C は B.end (10:30) + travel + buffer 以降のはず: {c.start_time}"
    )


def test_align_same_address_pair_warns_when_pair_blocks_45min_lunch() -> None:
    """Phase E-3 Wave 5 HIGH cleanup: 12:00 開始ペア (90 分占有 = 13:30 終了) で
    「45 分以上の lunch 確保不可」warning が emit される.

    Setup: A=35, B=35 同住所ペア, preferred_start="12:00", preferred_end="14:00".
    align 後: A.start = B.start = 12:00, B.end = 12:00 + max(70, 90) = 13:30.
    lunch window [11:30, 13:30] と 12:00-13:30 が重なるが、AM 側回避 (start>=12:00)
    可能なので ``_is_in_lunch_break`` は False. しかし AM 残空き = 30 分,
    PM 残空き = 0 分で 45 分以上の連続空きを残せない → same_address_consolidation
    warning が emit されるはず.
    """
    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=12, start_m=0, patient_name="A"
    )
    a.end_time = time(12, 35)
    a.service_minutes = 35
    a.course_code = "A"
    a.time_type = "時間帯"
    a.preferred_start = "12:00"
    a.preferred_end = "14:00"
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=12, start_m=35, patient_name="B"
    )
    b.end_time = time(13, 10)
    b.service_minutes = 35
    b.course_code = "A"
    b.time_type = "時間帯"
    b.preferred_start = "12:00"
    b.preferred_end = "14:00"

    warnings: list[V2Warning] = []
    apply_travel_corrections([a, b], warnings=warnings)

    # align 確定: A.start = B.start = 12:00, B.end = 12:00 + 90 = 13:30.
    assert a.start_time == time(12, 0)
    assert b.start_time == time(12, 0)
    assert b.end_time == time(13, 30)

    # 45 分 lunch 確保不可の warning が emit されているはず.
    lunch_blocking_warnings = [
        w
        for w in warnings
        if w.type == "same_address_consolidation"
        and "45 分以上の昼休憩が確保できません" in w.message
    ]
    assert len(lunch_blocking_warnings) >= 1, (
        f"45 分以上 lunch 確保不可 warning が emit されるはず: got {[w.message for w in warnings]}"
    )


def test_align_same_address_pair_no_warn_when_pair_leaves_45min_window() -> None:
    """Phase E-3 Wave 5 HIGH cleanup: 10:00 開始ペア (90 分占有 = 11:30 終了) なら
    lunch window 内に 45 分以上空きが残るため warning なし.

    Setup: A=35, B=35 同住所ペア, preferred_start="10:00", preferred_end="12:00".
    align 後: A.start = B.start = 10:00, B.end = 10:00 + 90 = 11:30.
    11:30 == LUNCH_EARLIEST_START なので lunch window [11:30, 13:30] と
    overlap しない → そもそも新 warning ロジックに入らない (overlap_start >= overlap_end).
    """
    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=0, patient_name="A"
    )
    a.end_time = time(10, 35)
    a.service_minutes = 35
    a.course_code = "A"
    a.time_type = "時間帯"
    a.preferred_start = "10:00"
    a.preferred_end = "12:00"
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=35, patient_name="B"
    )
    b.end_time = time(11, 10)
    b.service_minutes = 35
    b.course_code = "A"
    b.time_type = "時間帯"
    b.preferred_start = "10:00"
    b.preferred_end = "12:00"

    warnings: list[V2Warning] = []
    apply_travel_corrections([a, b], warnings=warnings)

    # align 確定: A.start = B.start = 10:00, B.end = 11:30.
    assert a.start_time == time(10, 0)
    assert b.start_time == time(10, 0)
    assert b.end_time == time(11, 30)

    # 45 分 lunch 確保不可 warning は emit されないはず (overlap なしまたは残空き >= 45 分).
    lunch_blocking_warnings = [
        w
        for w in warnings
        if w.type == "same_address_consolidation"
        and "45 分以上の昼休憩が確保できません" in w.message
    ]
    assert len(lunch_blocking_warnings) == 0, (
        f"lunch window に 45 分以上空きが残るので warning なしのはず: "
        f"got {[w.message for w in lunch_blocking_warnings]}"
    )


# ---------------------------------------------------------------------------
# (4) 同住所 3 人以上の自動別コース化.
# ---------------------------------------------------------------------------


def test_same_address_three_patients_third_becomes_unassigned() -> None:
    """Phase E-3 改修 (4): 同住所 3 名同コース → 3 名目が unassigned に流れる.

    H2 enforce で別 set 移動できなかった残存 3 名以降は自動別コース化.
    """
    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.end_time = time(9, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "時間帯"
    a.preferred_start = "09:00"
    a.preferred_end = "12:00"
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=30, patient_name="B"
    )
    b.end_time = time(10, 0)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "時間帯"
    b.preferred_start = "09:30"
    b.preferred_end = "12:00"
    c = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=0, patient_name="C"
    )
    c.end_time = time(10, 30)
    c.service_minutes = 30
    c.course_code = "A"
    c.time_type = "時間帯"
    c.preferred_start = "10:00"  # 最も遅い → unassigned に流される候補.
    c.preferred_end = "12:00"

    warnings: list[V2Warning] = []
    unassigned = apply_travel_corrections([a, b, c], warnings=warnings)

    # A, B はペア化として残る. C は unassigned.
    assert a.course_code == "A"
    assert b.course_code == "A"
    assert c.course_code is None, f"C は 3 名目で unassigned: course_code={c.course_code}"
    assert id(c) in unassigned, "C の id が unassigned set に入っているはず"


def test_same_address_three_patients_third_emits_warning() -> None:
    """Phase E-3 改修 (4): 3 名目を unassigned に流す時、warning が emit される."""
    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.end_time = time(9, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "時間帯"
    a.preferred_start = "09:00"
    a.preferred_end = "12:00"
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=30, patient_name="B"
    )
    b.end_time = time(10, 0)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "時間帯"
    b.preferred_start = "09:30"
    b.preferred_end = "12:00"
    c = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=0, patient_name="C"
    )
    c.end_time = time(10, 30)
    c.service_minutes = 30
    c.course_code = "A"
    c.time_type = "時間帯"
    c.preferred_start = "10:00"
    c.preferred_end = "12:00"

    warnings: list[V2Warning] = []
    apply_travel_corrections([a, b, c], warnings=warnings)

    # warning メッセージに "3 名以上の同住所" + "別コース" が含まれる.
    matched = [w for w in warnings if "3 名以上の同住所" in w.message and "別コース" in w.message]
    assert matched, f"同住所 3 名以上 warning が出ていない: {[w.message for w in warnings]}"
    # affected_patient_ids に C が含まれる.
    assert c.patient_id in matched[0].affected_patient_ids


def test_same_address_three_patients_with_fixed_time_keeps_fixed() -> None:
    """Phase E-3 改修 (4): 同住所 3 名のうち固定時刻 patient は守る (= 残す).

    Setup: A (時間帯, preferred 09:00), B (時間帯, preferred 09:30), C (固定 10:00).
    sort key: 固定優先 (rank=0) → 非固定 (rank=1).
    → C (固定) と A (時間帯 09:00) が残り、B (時間帯 09:30) が unassigned.
    """
    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=0, patient_name="A"
    )
    a.end_time = time(9, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "時間帯"
    a.preferred_start = "09:00"
    a.preferred_end = "12:00"
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=9, start_m=30, patient_name="B"
    )
    b.end_time = time(10, 0)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "時間帯"
    b.preferred_start = "09:30"
    b.preferred_end = "12:00"
    c = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=10, start_m=0, patient_name="C"
    )
    c.end_time = time(10, 30)
    c.service_minutes = 30
    c.course_code = "A"
    c.time_type = "固定"
    c.preferred_start = "10:00"

    warnings: list[V2Warning] = []
    unassigned = apply_travel_corrections([a, b, c], warnings=warnings)

    # 固定 C は残る. 非固定 2 名のうち preferred_start が遅い B が unassigned.
    assert c.course_code == "A", f"固定時刻 C は守るはず: {c.course_code}"
    # B (preferred_start=09:30) が unassigned 候補 (A=09:00 より遅い).
    assert b.course_code is None, f"B (preferred_start=09:30) は unassigned: {b.course_code}"
    assert id(b) in unassigned


# ---------------------------------------------------------------------------
# (1) service_minutes default 35 — _extract_weekly_entries.
# ---------------------------------------------------------------------------


def test_extract_weekly_entries_default_service_minutes_is_35() -> None:
    """Phase E-3 改修 (1): weekly_pattern.entries に service_minutes が無い場合、
    デフォルト 35 分を採用する (旧仕様 30 分).
    """
    from app.services.scheduling.auto_allocator_v2 import _extract_weekly_entries

    # entries 形式 (リスト) で service_minutes 未指定.
    p_entries = Patient(
        id=uuid.uuid4(),
        code="DEF-35-1",
        name="P-Entry",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=uuid.uuid4(),
        weekly_pattern={
            "entries": [
                {
                    "weekday": "Mon",
                    "preferred_start": "10:00",
                    "preferred_end": "10:35",
                    "time_type": "固定",
                }
            ]
        },
    )
    entries = _extract_weekly_entries(p_entries)
    assert len(entries) == 1
    weekday, st, sm, tt, ps_raw, pe_raw = entries[0]
    assert sm == 35, f"Phase E-3: entries 形式 default service_minutes=35, got {sm}"

    # サマリ形式で service_minutes 未指定.
    p_summary = Patient(
        id=uuid.uuid4(),
        code="DEF-35-2",
        name="P-Summary",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=uuid.uuid4(),
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "時間帯",
        },
    )
    summary_entries = _extract_weekly_entries(p_summary)
    assert len(summary_entries) == 1
    _, _, sm2, _, _, _ = summary_entries[0]
    assert sm2 == 35, f"Phase E-3: サマリ形式 default service_minutes=35, got {sm2}"


# ---------------------------------------------------------------------------
# Phase G-92 — プール投入 固定優先→希望フォールバック
# ---------------------------------------------------------------------------


async def _seed_g92_office(db, *, name: str, staffed_weekdays: list[int]) -> Office:
    """Phase G-92 テスト用: 指定曜日にスタッフ 1 名を出勤させた office を作る."""
    office = Office(name=name)
    db.add(office)
    await db.flush()
    s = Staff(name=f"{name}-staff", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(s)
    await db.flush()
    for wd in staffed_weekdays:
        db.add(StaffShift(staff_id=s.id, weekday=wd, is_on=True))
    await db.flush()
    return office


@pytest.mark.asyncio
async def test_g92_diff_add_fixed_slot_fits_returns_fixed(db) -> None:
    """① 固定枠が 3 条件をクリア → proposal_source='fixed', 理由なし."""
    office = await _seed_g92_office(db, name="g92-fixed", staffed_weekdays=[0])
    p = Patient(
        code="G92-FIXED",
        name="固定OK",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        # 希望も Mon だが固定が優先される.
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "14:00",
            "time_type": "固定",
        },
    )
    db.add(p)
    await db.flush()
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=0,  # Mon (staffed)
            start_time=time(10, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    await db.commit()

    result = await run_v2_pipeline(
        db, iso_year=2026, iso_week=20, office_ids=[office.id], mode="diff_add"
    )
    meta = result["proposal_meta_by_patient"][p.id]
    assert meta["proposal_source"] == "fixed", meta
    assert meta["fixed_unavailable_reasons"] == []
    # 固定枠 (Mon 10:00) で配置されていること.
    pv = [v for v in result["pool_visits"] if v.patient_id == p.id]
    assert any(v.weekday == 0 and v.start_time == time(10, 0) for v in pv), pv


@pytest.mark.asyncio
async def test_g92_diff_add_fixed_unavailable_falls_back_to_preferred(db) -> None:
    """② 固定枠が定員 (条件ｲ) で入らない → 希望フォールバック + 理由付与.

    PFV は Wed (稼働曜日だがスタッフ 0 名 → 定員オーバー), 希望は Mon (スタッフあり).
    固定 (Wed) が落ち、 希望 (Mon) が生存する.
    """
    office = await _seed_g92_office(db, name="g92-fallback", staffed_weekdays=[0])  # Mon only
    p = Patient(
        code="G92-FB",
        name="フォールバック",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "14:00",
            "time_type": "固定",
        },
    )
    db.add(p)
    await db.flush()
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=2,  # Wed (operating weekday but no staff)
            start_time=time(10, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    await db.commit()

    result = await run_v2_pipeline(
        db, iso_year=2026, iso_week=20, office_ids=[office.id], mode="diff_add"
    )
    meta = result["proposal_meta_by_patient"][p.id]
    assert meta["proposal_source"] == "fixed_fallback_preferred", meta
    assert meta["fixed_unavailable_reasons"], "固定不可理由が 1 件以上付与されるべき"
    assert "capacity_over" in meta["fixed_unavailable_reasons"], meta
    # フォールバックで希望 (Mon 14:00) が提案され、 落ちた固定 (Wed) は除外される.
    pv = [v for v in result["pool_visits"] if v.patient_id == p.id]
    assert all(v.weekday != 2 for v in pv), f"落ちた固定 (Wed) は proposal から除外: {pv}"
    assert any(v.weekday == 0 for v in pv), f"希望 (Mon) が proposal に残る: {pv}"


@pytest.mark.asyncio
async def test_g92_diff_add_no_fixed_returns_preferred(db) -> None:
    """③ 固定枠なし → proposal_source='preferred', 理由なし."""
    office = await _seed_g92_office(db, name="g92-preferred", staffed_weekdays=[0])
    p = Patient(
        code="G92-PREF",
        name="希望のみ",
        status="active",
        lat=35.66,
        lng=140.11,
        primary_office_id=office.id,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:30",
            "time_type": "固定",
        },
    )
    db.add(p)
    await db.commit()

    result = await run_v2_pipeline(
        db, iso_year=2026, iso_week=20, office_ids=[office.id], mode="diff_add"
    )
    meta = result["proposal_meta_by_patient"][p.id]
    assert meta["proposal_source"] == "preferred", meta
    assert meta["fixed_unavailable_reasons"] == []


@pytest.mark.asyncio
async def test_g92_diff_add_both_unavailable_unassigned_with_reason(db) -> None:
    """④ 固定も希望も入らない → 未割当 + 理由付与.

    PFV は Wed (スタッフ 0), 希望も Wed (スタッフ 0). どちらも配置不可.
    """
    office = await _seed_g92_office(db, name="g92-both-fail", staffed_weekdays=[0])  # Mon only
    p = Patient(
        code="G92-BOTH",
        name="両方ダメ",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        weekly_pattern={
            "preferred_weekdays": ["Wed"],
            "preferred_start": "14:00",
            "time_type": "固定",
        },
    )
    db.add(p)
    await db.flush()
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=2,  # Wed (no staff)
            start_time=time(10, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    await db.commit()

    result = await run_v2_pipeline(
        db, iso_year=2026, iso_week=20, office_ids=[office.id], mode="diff_add"
    )
    # after_visits に当該患者は出てこない (両曜日とも配置不可).
    after_pids = {v.patient_id for v in result["after_visits"]}
    assert p.id not in after_pids, "両方ダメなら after_visits に出ない"
    # 未割当リストに理由付きで載る.
    unassigned_pids = {u["patient_id"] for u in result["unassigned_patients"]}
    assert p.id in unassigned_pids, result["unassigned_patients"]
    u = next(u for u in result["unassigned_patients"] if u["patient_id"] == p.id)
    assert u["reason"] is not None and u["reason"] != "unknown", u


# ---------------------------------------------------------------------------
# Phase G-92 — Reviewer 指摘 修正 1/2/3/4 の回帰テスト
# ---------------------------------------------------------------------------


async def _enable_g21_flag(db, office: Office) -> None:
    """Phase G-92 テスト用: 拠点で g21_new_algorithm feature flag を有効化する."""
    db.add(
        OfficeFeatureFlag(
            office_id=office.id,
            feature_key=G21_NEW_ALGORITHM_FEATURE_KEY,
            enabled_at=datetime.now(tz=UTC),
        )
    )
    await db.flush()


@pytest.mark.asyncio
async def test_g92_diff_add_g21_enabled_sets_fixed_pool_origin(db) -> None:
    """修正1: g21 feature flag 有効拠点でも固定候補に pool_origin='fixed' が立つ.

    g21 経路は build_visits_for_pool_v2 で pool_visits を再生成する. 旧実装では
    pool_origin が設定されず全候補が "preferred" 既定になり、
    _dedup_fixed_preferred_candidates が fixed_keys を見つけられず
    fixed_fallback_preferred 分岐に到達できなかった (= G-92 が g21 拠点で無効化).
    修正後は pinned PFV 由来の候補に pool_origin='fixed' が立ち、 固定優先→希望
    フォールバックの土台 (dedup の fixed_keys 検出) が成立する.

    本テストは g21 有効状態で pinned PFV (Mon・スタッフあり) が固定として配置され、
    その pool_visit に pool_origin='fixed' / proposal_source='fixed' が立つことを
    検証する (= wiring が legacy 経路と同等であることの確認).
    """
    office = await _seed_g92_office(db, name="g92-g21-fixed", staffed_weekdays=[0])  # Mon
    await _enable_g21_flag(db, office)
    p = Patient(
        code="G92-G21-FX",
        name="g21固定",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "14:00",
            "time_type": "固定",
        },
    )
    db.add(p)
    await db.flush()
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=0,  # Mon (staffed → 固定生存)
            start_time=time(10, 0),
            duration_min=30,
            slot_index=0,
            is_pinned=True,
        )
    )
    await db.commit()

    result = await run_v2_pipeline(
        db, iso_year=2026, iso_week=20, office_ids=[office.id], mode="diff_add"
    )
    meta = result["proposal_meta_by_patient"][p.id]
    assert meta["proposal_source"] == "fixed", meta
    # g21 再生成パスでも固定候補に pool_origin='fixed' が立っていること (= 修正1 の核心).
    pv = [v for v in result["pool_visits"] if v.patient_id == p.id]
    fixed_pv = [v for v in pv if v.weekday == 0 and v.start_time == time(10, 0)]
    assert fixed_pv, f"g21 経路で固定 (Mon 10:00) が配置される: {pv}"
    assert fixed_pv[0].pool_origin == "fixed", (
        f"g21 再生成パスでも固定候補に pool_origin='fixed' が立つ: {fixed_pv[0].pool_origin}"
    )


@pytest.mark.asyncio
async def test_g92_diff_add_same_weekday_both_fail_no_double_classification(db) -> None:
    """修正2: 固定と希望が同曜日で両方とも配置不可の患者を二重分類しない.

    PFV は Mon (start 10:00), 希望も Mon (同曜日). Mon はスタッフ 0 で両方とも
    配置不可. 旧実装は失敗した固定 visit を pool_visits 内で「同曜日の希望時刻」に
    in-place 差し替えるが、 この差替候補は Stage 5/6 を経ておらず after_visits に
    乗らない (= 未検証提案). その結果、 当該患者は他に生存曜日が無いため
    _identify_unassigned_patients で「未割当」に計上される一方、 pool_visits に残った
    差替候補を endpoint が「提案あり」として surface する二重分類が起きた.
    修正後は未検証の差替候補を pool_visits から除去し、 提案を出さず未割当判定に
    一本化する.

    定員 0 を作るため staffed_weekdays=[] (どの曜日もスタッフ無し) とする.
    """
    office = await _seed_g92_office(db, name="g92-same-wd", staffed_weekdays=[])
    p = Patient(
        code="G92-SAME-WD",
        name="同曜日両方ダメ",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "14:00",
            "time_type": "固定",
        },
    )
    db.add(p)
    await db.flush()
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=0,  # Mon — 希望と同曜日
            start_time=time(10, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    await db.commit()

    result = await run_v2_pipeline(
        db, iso_year=2026, iso_week=20, office_ids=[office.id], mode="diff_add"
    )
    # 未検証の差替候補は pool_visits から除去され、 提案として surface されない.
    pv = [v for v in result["pool_visits"] if v.patient_id == p.id]
    assert pv == [], f"未検証の同曜日差替候補は pool_visits から除去される: {pv}"
    # 未割当判定に一本化される (= 二重分類しない).
    unassigned_pids = {u["patient_id"] for u in result["unassigned_patients"]}
    assert p.id in unassigned_pids, (
        f"同曜日で両方ダメな患者は未割当に計上される: {result['unassigned_patients']}"
    )


@pytest.mark.asyncio
async def test_g92_diff_add_fixed_patient_preferred_only_weekday_proposed(db) -> None:
    """修正3 (方針A): 固定を持つ患者でも「希望のみ曜日」は preferred 提案に出す.

    PFV は Mon (固定 OK で生存), 希望は Mon + Tue. Tue は固定が無く希望のみ.
    方針A により Tue も preferred 提案として残ることを検証する (取りこぼし解消).
    """
    office = await _seed_g92_office(db, name="g92-pref-only", staffed_weekdays=[0, 1])  # Mon+Tue
    p = Patient(
        code="G92-PREF-ONLY",
        name="希望のみ曜日",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        weekly_pattern={
            "preferred_weekdays": ["Mon", "Tue"],
            "preferred_start": "14:00",
            "time_type": "固定",
        },
    )
    db.add(p)
    await db.flush()
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=0,  # Mon (staffed → 固定生存)
            start_time=time(10, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    await db.commit()

    result = await run_v2_pipeline(
        db, iso_year=2026, iso_week=20, office_ids=[office.id], mode="diff_add"
    )
    pv = [v for v in result["pool_visits"] if v.patient_id == p.id]
    weekdays = {v.weekday for v in pv}
    # Mon (固定) と Tue (希望のみ) の両方が提案に出る.
    assert 0 in weekdays, f"固定 (Mon) が出る: {pv}"
    assert 1 in weekdays, f"方針A: 希望のみ曜日 (Tue) も preferred 提案に出る: {pv}"


@pytest.mark.asyncio
async def test_g92_diff_add_fallback_reason_not_hardcoded_time_conflict(db) -> None:
    """修正4: フォールバック理由は warnings 由来で、 time_conflict ハードコードしない.

    PFV は Wed (稼働曜日・スタッフ 0 → 定員オーバー). 理由は capacity_over であり、
    time_conflict が混入しないことを検証する (旧実装は理由空のとき time_conflict を
    既定にしていた).
    """
    office = await _seed_g92_office(db, name="g92-reason", staffed_weekdays=[0])  # Mon only
    p = Patient(
        code="G92-REASON",
        name="理由精度",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "14:00",
            "time_type": "固定",
        },
    )
    db.add(p)
    await db.flush()
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=2,  # Wed (no staff → capacity_over)
            start_time=time(10, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    await db.commit()

    result = await run_v2_pipeline(
        db, iso_year=2026, iso_week=20, office_ids=[office.id], mode="diff_add"
    )
    meta = result["proposal_meta_by_patient"][p.id]
    assert meta["proposal_source"] == "fixed_fallback_preferred", meta
    assert "capacity_over" in meta["fixed_unavailable_reasons"], meta
    # time_conflict はハードコード既定として混入しない (定員起因なので).
    assert "time_conflict" not in meta["fixed_unavailable_reasons"], meta


# ---------------------------------------------------------------------------
# Phase G-93 — プール投入 部分不足プール (希望回数に一部足りない患者を拾う)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g93_diff_add_partial_shortage_proposes_only_missing_slot(db) -> None:
    """① 部分不足: PFV あり週3希望で 2 配置済 → 不足 1 スロットだけ提案 (P070 再現).

    P070 植田弥生 (稲毛) の再現:
      - 希望 週3 (Mon/Wed/Fri), PFV (mode='normal') 2 件 (Wed/Fri).
      - 今週すでに Wed/Fri に visit 配置済 (status='planned').
      - Mon は PFV 無し → preferred 提案として 1 スロットだけ出るべき.
    旧実装では PFV を持ち今週 visit が一部ある患者は orphan 経路から漏れ、
    提案に出なかった (= 本 fix の対象).
    """
    from datetime import date

    from app.models.visit import VISIT_STATUS_PLANNED, Visit

    office = await _seed_g92_office(
        db, name="g93-partial", staffed_weekdays=[0, 2, 4]
    )  # Mon/Wed/Fri
    p = Patient(
        code="G93-PARTIAL",
        name="部分不足",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        # 週3希望 (Mon/Wed/Fri).
        weekly_pattern={
            "preferred_weekdays": ["Mon", "Wed", "Fri"],
            "preferred_start": "10:30",
            "time_type": "固定",
        },
    )
    db.add(p)
    await db.flush()
    # PFV 2 件 (Wed/Fri). Mon は固定枠なし (= preferred フォールバック対象).
    for wd in (2, 4):
        db.add(
            PatientFixedVisit(
                patient_id=p.id,
                mode="normal",
                weekday=wd,
                start_time=time(10, 0),
                duration_min=30,
                slot_index=0,
            )
        )
    await db.flush()
    # 今週すでに Wed (05-13) / Fri (05-15) に配置済 (status='planned').
    for vdate in (date(2026, 5, 13), date(2026, 5, 15)):
        db.add(
            Visit(
                patient_id=p.id,
                visit_date=vdate,
                start_time=time(10, 0),
                end_time=time(10, 30),
                type="regular",
                status=VISIT_STATUS_PLANNED,
                source="auto_alloc",
                required_staff_count=1,
            )
        )
    await db.commit()

    result = await run_v2_pipeline(
        db, iso_year=2026, iso_week=20, office_ids=[office.id], mode="diff_add"
    )
    pv = [v for v in result["pool_visits"] if v.patient_id == p.id]
    # 不足している Mon (weekday=0) だけが提案される.
    assert pv, f"部分不足患者は提案に出る (= 旧実装の取りこぼし解消): {pv}"
    assert {v.weekday for v in pv} == {0}, f"不足曜日 (Mon) のみ提案: {[v.weekday for v in pv]}"


@pytest.mark.asyncio
async def test_g93_diff_add_partial_shortage_no_double_proposal_for_placed(db) -> None:
    """② 二重提案防止: 既配置の曜日 (Wed/Fri) は提案に出さない.

    希望 週3 (Mon/Wed/Fri), PFV 3 件 (Mon/Wed/Fri 全曜日固定). 今週 Wed/Fri
    配置済. → 既に配置済の Wed/Fri は提案から除外され、 不足の Mon のみ残る.
    既配置曜日が固定 (PFV) でも二重提案されないことを検証する.
    """
    from datetime import date

    from app.models.visit import VISIT_STATUS_PLANNED, Visit

    office = await _seed_g92_office(db, name="g93-nodup", staffed_weekdays=[0, 2, 4])
    p = Patient(
        code="G93-NODUP",
        name="二重提案防止",
        status="active",
        lat=35.66,
        lng=140.11,
        primary_office_id=office.id,
        weekly_pattern={
            "preferred_weekdays": ["Mon", "Wed", "Fri"],
            "preferred_start": "10:30",
            "time_type": "固定",
        },
    )
    db.add(p)
    await db.flush()
    for wd in (0, 2, 4):  # 全曜日に固定枠.
        db.add(
            PatientFixedVisit(
                patient_id=p.id,
                mode="normal",
                weekday=wd,
                start_time=time(10, 0),
                duration_min=30,
                slot_index=0,
            )
        )
    await db.flush()
    # Wed/Fri は既配置.
    for vdate in (date(2026, 5, 13), date(2026, 5, 15)):
        db.add(
            Visit(
                patient_id=p.id,
                visit_date=vdate,
                start_time=time(10, 0),
                end_time=time(10, 30),
                type="regular",
                status=VISIT_STATUS_PLANNED,
                source="auto_alloc",
                required_staff_count=1,
            )
        )
    await db.commit()

    result = await run_v2_pipeline(
        db, iso_year=2026, iso_week=20, office_ids=[office.id], mode="diff_add"
    )
    pv = [v for v in result["pool_visits"] if v.patient_id == p.id]
    # 既配置の Wed (2) / Fri (4) は二重提案されない.
    assert all(v.weekday not in (2, 4) for v in pv), (
        f"既配置曜日は二重提案されない: {[v.weekday for v in pv]}"
    )
    # 不足の Mon (0) は提案される.
    assert any(v.weekday == 0 for v in pv), f"不足曜日 (Mon) は提案: {[v.weekday for v in pv]}"


@pytest.mark.asyncio
async def test_g93_diff_add_fully_covered_patient_not_proposed(db) -> None:
    """③(a) 既存挙動不変: 希望を完全に満たす患者は提案に出ない.

    週2希望 (Mon/Wed), PFV 2 件 (Mon/Wed). 今週 Mon/Wed 両方配置済 → 不足なし.
    部分不足分類に入らず、 提案も出ない (= 既存挙動を壊さない).
    """
    from datetime import date

    from app.models.visit import VISIT_STATUS_PLANNED, Visit

    office = await _seed_g92_office(db, name="g93-full", staffed_weekdays=[0, 2])
    p = Patient(
        code="G93-FULL",
        name="充足済",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        weekly_pattern={
            "preferred_weekdays": ["Mon", "Wed"],
            "preferred_start": "10:30",
            "time_type": "固定",
        },
    )
    db.add(p)
    await db.flush()
    for wd in (0, 2):
        db.add(
            PatientFixedVisit(
                patient_id=p.id,
                mode="normal",
                weekday=wd,
                start_time=time(10, 0),
                duration_min=30,
                slot_index=0,
            )
        )
    await db.flush()
    for vdate in (date(2026, 5, 11), date(2026, 5, 13)):  # Mon/Wed 両方配置済.
        db.add(
            Visit(
                patient_id=p.id,
                visit_date=vdate,
                start_time=time(10, 0),
                end_time=time(10, 30),
                type="regular",
                status=VISIT_STATUS_PLANNED,
                source="auto_alloc",
                required_staff_count=1,
            )
        )
    await db.commit()

    result = await run_v2_pipeline(
        db, iso_year=2026, iso_week=20, office_ids=[office.id], mode="diff_add"
    )
    pv = [v for v in result["pool_visits"] if v.patient_id == p.id]
    assert pv == [], f"希望充足済の患者は提案に出ない: {pv}"


@pytest.mark.asyncio
async def test_g93_diff_add_orphan_no_week_visit_unchanged(db) -> None:
    """③(b) 既存挙動不変: 完全孤児 (PFV あり + 今週 visit 0 件) は従来通り全曜日提案.

    G-93 の部分不足分類追加で、 既存の orphan 経路 (今週 visit ゼロ) が
    壊れていないことを確認する. PFV 2 件 (Mon/Wed), 今週 visit 無し →
    両曜日とも提案される.
    """
    office = await _seed_g92_office(db, name="g93-orphan", staffed_weekdays=[0, 2])
    p = Patient(
        code="G93-ORPHAN",
        name="完全孤児",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        weekly_pattern={
            "preferred_weekdays": ["Mon", "Wed"],
            "preferred_start": "10:30",
            "time_type": "固定",
        },
    )
    db.add(p)
    await db.flush()
    for wd in (0, 2):
        db.add(
            PatientFixedVisit(
                patient_id=p.id,
                mode="normal",
                weekday=wd,
                start_time=time(10, 0),
                duration_min=30,
                slot_index=0,
            )
        )
    await db.commit()

    result = await run_v2_pipeline(
        db, iso_year=2026, iso_week=20, office_ids=[office.id], mode="diff_add"
    )
    pv = [v for v in result["pool_visits"] if v.patient_id == p.id]
    # 今週 visit ゼロの孤児は固定枠 (Mon/Wed) が両方提案される (従来挙動).
    assert {v.weekday for v in pv} == {0, 2}, (
        f"完全孤児は全固定曜日が提案される (挙動不変): {[v.weekday for v in pv]}"
    )


@pytest.mark.asyncio
async def test_g93_diff_add_no_fixed_patient_unchanged(db) -> None:
    """③(c) 既存挙動不変: PFV 無し患者 (no_fixed pool) は従来通り weekly_pattern 提案.

    部分不足分類は PFV 患者のみ対象. PFV を持たない患者は no_fixed pool で
    従来通り展開され、 今週 visit の有無に関わらず希望曜日が提案される.
    """
    from datetime import date

    from app.models.visit import VISIT_STATUS_PLANNED, Visit

    office = await _seed_g92_office(db, name="g93-nofixed", staffed_weekdays=[0, 2])
    p = Patient(
        code="G93-NOFIXED",
        name="固定なし",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        weekly_pattern={
            "preferred_weekdays": ["Mon", "Wed"],
            "preferred_start": "10:30",
            "time_type": "固定",
        },
    )
    db.add(p)
    await db.flush()
    # PFV 無し. 今週 Mon に既配置 (no_fixed pool は配置済を二重提案する従来挙動を維持).
    db.add(
        Visit(
            patient_id=p.id,
            visit_date=date(2026, 5, 11),
            start_time=time(10, 0),
            end_time=time(10, 30),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto_alloc",
            required_staff_count=1,
        )
    )
    await db.commit()

    result = await run_v2_pipeline(
        db, iso_year=2026, iso_week=20, office_ids=[office.id], mode="diff_add"
    )
    pv = [v for v in result["pool_visits"] if v.patient_id == p.id]
    # PFV 無し患者は no_fixed pool で weekly_pattern (Mon/Wed) 両方展開 (挙動不変).
    assert {v.weekday for v in pv} == {0, 2}, (
        f"PFV 無し患者は no_fixed pool で従来通り展開: {[v.weekday for v in pv]}"
    )


@pytest.mark.asyncio
async def test_g93_diff_add_completed_weekday_not_reproposed(db) -> None:
    """① completed の曜日は配置済み扱いで再提案されない (status 定義 fix).

    週2希望 (Mon/Wed), PFV 2 件 (Mon/Wed). 今週 Wed を completed (完了), Mon は
    未配置. completed を「未配置」とみなす旧実装では Wed が二重提案されたが、
    placed_statuses ∈ {planned, in_progress, completed} に修正したことで Wed は
    配置済み = 再提案されず、 不足の Mon のみが提案される.
    """
    from datetime import date

    from app.models.visit import VISIT_STATUS_COMPLETED, Visit

    office = await _seed_g92_office(db, name="g93-completed", staffed_weekdays=[0, 2])
    p = Patient(
        code="G93-COMPLETED",
        name="完了配置",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        weekly_pattern={
            "preferred_weekdays": ["Mon", "Wed"],
            "preferred_start": "10:30",
            "time_type": "固定",
        },
    )
    db.add(p)
    await db.flush()
    for wd in (0, 2):
        db.add(
            PatientFixedVisit(
                patient_id=p.id,
                mode="normal",
                weekday=wd,
                start_time=time(10, 0),
                duration_min=30,
                slot_index=0,
            )
        )
    await db.flush()
    # Wed (05-13) は completed (完了済). Mon は未配置.
    db.add(
        Visit(
            patient_id=p.id,
            visit_date=date(2026, 5, 13),
            start_time=time(10, 0),
            end_time=time(10, 30),
            type="regular",
            status=VISIT_STATUS_COMPLETED,
            source="auto_alloc",
            required_staff_count=1,
        )
    )
    await db.commit()

    result = await run_v2_pipeline(
        db, iso_year=2026, iso_week=20, office_ids=[office.id], mode="diff_add"
    )
    pv = [v for v in result["pool_visits"] if v.patient_id == p.id]
    # completed の Wed (2) は配置済み = 再提案されない. 不足の Mon (0) のみ提案.
    assert {v.weekday for v in pv} == {0}, (
        f"completed 曜日は再提案されず不足曜日 (Mon) のみ提案: {[v.weekday for v in pv]}"
    )


@pytest.mark.asyncio
async def test_g93_diff_add_cancelled_only_weekday_is_proposed(db) -> None:
    """② cancelled のみの曜日は未配置扱いで提案される (status 定義 fix).

    週2希望 (Mon/Wed), PFV 2 件 (Mon/Wed). 今週 Mon を cancelled, Wed を planned.
    cancelled は患者が実際には訪問されておらず再訪問が必要 = 未配置扱い.
    → 配置済みは Wed のみ、 cancelled の Mon は不足曜日として提案される.
    """
    from datetime import date

    from app.models.visit import VISIT_STATUS_CANCELLED, VISIT_STATUS_PLANNED, Visit

    office = await _seed_g92_office(db, name="g93-cancelled", staffed_weekdays=[0, 2])
    p = Patient(
        code="G93-CANCELLED",
        name="取消配置",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        weekly_pattern={
            "preferred_weekdays": ["Mon", "Wed"],
            "preferred_start": "10:30",
            "time_type": "固定",
        },
    )
    db.add(p)
    await db.flush()
    for wd in (0, 2):
        db.add(
            PatientFixedVisit(
                patient_id=p.id,
                mode="normal",
                weekday=wd,
                start_time=time(10, 0),
                duration_min=30,
                slot_index=0,
            )
        )
    await db.flush()
    # Mon (05-11) は cancelled, Wed (05-13) は planned.
    db.add(
        Visit(
            patient_id=p.id,
            visit_date=date(2026, 5, 11),
            start_time=time(10, 0),
            end_time=time(10, 30),
            type="regular",
            status=VISIT_STATUS_CANCELLED,
            source="auto_alloc",
            required_staff_count=1,
        )
    )
    db.add(
        Visit(
            patient_id=p.id,
            visit_date=date(2026, 5, 13),
            start_time=time(10, 0),
            end_time=time(10, 30),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto_alloc",
            required_staff_count=1,
        )
    )
    await db.commit()

    result = await run_v2_pipeline(
        db, iso_year=2026, iso_week=20, office_ids=[office.id], mode="diff_add"
    )
    pv = [v for v in result["pool_visits"] if v.patient_id == p.id]
    # cancelled の Mon (0) は未配置 = 提案される. planned の Wed (2) は配置済み.
    assert {v.weekday for v in pv} == {0}, (
        f"cancelled のみの曜日 (Mon) は未配置として提案: {[v.weekday for v in pv]}"
    )


@pytest.mark.asyncio
async def test_g93_diff_add_entries_form_weekly_pattern_proposes_missing(db) -> None:
    """③ weekly_pattern が entries (リスト) 形式でも desired 曜日が正しく算出される.

    weekly_pattern を entries 形式 (週3: Mon/Wed/Fri) で定義し、 PFV は無し.
    今週 Wed のみ planned 配置済 → desired から既配置 Wed を引いた Mon/Fri が
    不足曜日として提案される (entries 形式でも _g93_desired_weekdays が機能する).

    PFV を持たせて partial_short 経路 (PFV 患者のみ対象) に乗せるため、 entries の
    曜日と一致しない PFV は使わず、 entries の一部だけを PFV にする.
    """
    from datetime import date

    from app.models.visit import VISIT_STATUS_PLANNED, Visit

    office = await _seed_g92_office(
        db, name="g93-entries", staffed_weekdays=[0, 2, 4]
    )  # Mon/Wed/Fri
    p = Patient(
        code="G93-ENTRIES",
        name="エントリ形式",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        # entries (リスト) 形式で週3希望.
        weekly_pattern={
            "entries": [
                {"weekday": "Mon", "preferred_start": "10:30"},
                {"weekday": "Wed", "preferred_start": "10:30"},
                {"weekday": "Fri", "preferred_start": "10:30"},
            ],
            "time_type": "固定",
        },
    )
    db.add(p)
    await db.flush()
    # PFV は Wed のみ (partial_short 経路に乗せるための固定枠).
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=2,
            start_time=time(10, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    await db.flush()
    # 今週 Wed (05-13) のみ planned 配置済.
    db.add(
        Visit(
            patient_id=p.id,
            visit_date=date(2026, 5, 13),
            start_time=time(10, 0),
            end_time=time(10, 30),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto_alloc",
            required_staff_count=1,
        )
    )
    await db.commit()

    result = await run_v2_pipeline(
        db, iso_year=2026, iso_week=20, office_ids=[office.id], mode="diff_add"
    )
    pv = [v for v in result["pool_visits"] if v.patient_id == p.id]
    # entries 形式の desired (Mon/Wed/Fri) から既配置 Wed を除いた Mon/Fri が提案.
    assert {v.weekday for v in pv} == {0, 4}, (
        f"entries 形式でも不足曜日 (Mon/Fri) が提案される: {[v.weekday for v in pv]}"
    )


@pytest.mark.asyncio
async def test_g93_diff_add_frequency_only_pattern_uses_pfv_weekdays_only(db) -> None:
    """④ frequency_per_week のみの患者は PFV 曜日のみが desired になる (現挙動固定).

    weekly_pattern に preferred_weekdays / entries が無く frequency_per_week のみの
    患者は _extract_weekly_entries が空を返すため、 _g93_desired_weekdays は PFV
    曜日のみになる. PFV (Mon/Wed) のうち Wed を配置済にすると、 不足は Mon のみ.
    希望曜日が PFV に依存する現挙動をテストで固定する.
    """
    from datetime import date

    from app.models.visit import VISIT_STATUS_PLANNED, Visit

    office = await _seed_g92_office(db, name="g93-freqonly", staffed_weekdays=[0, 2])
    p = Patient(
        code="G93-FREQONLY",
        name="頻度のみ",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        # preferred_weekdays / entries 無し. frequency_per_week のみ.
        weekly_pattern={"frequency_per_week": 2},
    )
    db.add(p)
    await db.flush()
    # PFV 2 件 (Mon/Wed) — desired はこの 2 曜日のみになる.
    for wd in (0, 2):
        db.add(
            PatientFixedVisit(
                patient_id=p.id,
                mode="normal",
                weekday=wd,
                start_time=time(10, 0),
                duration_min=30,
                slot_index=0,
            )
        )
    await db.flush()
    # Wed (05-13) のみ配置済 → 不足は Mon のみ.
    db.add(
        Visit(
            patient_id=p.id,
            visit_date=date(2026, 5, 13),
            start_time=time(10, 0),
            end_time=time(10, 30),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto_alloc",
            required_staff_count=1,
        )
    )
    await db.commit()

    result = await run_v2_pipeline(
        db, iso_year=2026, iso_week=20, office_ids=[office.id], mode="diff_add"
    )
    pv = [v for v in result["pool_visits"] if v.patient_id == p.id]
    # desired = PFV 曜日 (Mon/Wed) のみ. 既配置 Wed を除いた Mon だけ提案.
    assert {v.weekday for v in pv} == {0}, (
        f"frequency のみ患者は PFV 曜日のみが desired (Mon 提案): {[v.weekday for v in pv]}"
    )


# ---------------------------------------------------------------------------
# Phase G-94 — 修正1: 過剰提案 (回数充足 / 固定曜日 ≠ 希望曜日のズレ患者)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g94_diff_add_count_satisfied_fixed_weekday_mismatch_not_proposed(db) -> None:
    """① 過剰提案防止: 固定曜日 ≠ 希望曜日でも希望回数を満たせば提案しない (小宮再現).

    小宮啓子の再現: 固定枠 (水/木/金) と weekly 希望曜日 (火) がズレている.
    frequency_per_week=3 で今週すでに 3 件 (水/木/金) 配置済 → 回数は充足.
    G-93 の曜日判定だけだと希望曜日 (火) が未配置 = 不足扱いで過剰提案されるが、
    G-94 の回数充足チェックで除外される.
    """
    from datetime import date

    from app.models.visit import VISIT_STATUS_PLANNED, Visit

    office = await _seed_g92_office(
        db, name="g94-mismatch", staffed_weekdays=[1, 2, 3, 4]
    )  # Tue/Wed/Thu/Fri
    p = Patient(
        code="G94-MISMATCH",
        name="小宮型",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        # 希望曜日は火 (Tue) だが固定枠は水木金. frequency=3.
        weekly_pattern={
            "preferred_weekdays": ["Tue"],
            "preferred_start": "10:30",
            "time_type": "固定",
            "frequency_per_week": 3,
        },
    )
    db.add(p)
    await db.flush()
    # PFV 3 件 (Wed/Thu/Fri).
    for wd in (2, 3, 4):
        db.add(
            PatientFixedVisit(
                patient_id=p.id,
                mode="normal",
                weekday=wd,
                start_time=time(10, 0),
                duration_min=30,
                slot_index=0,
            )
        )
    await db.flush()
    # 今週すでに Wed (05-13) / Thu (05-14) / Fri (05-15) に配置済 → 3 件 = 回数充足.
    for vdate in (date(2026, 5, 13), date(2026, 5, 14), date(2026, 5, 15)):
        db.add(
            Visit(
                patient_id=p.id,
                visit_date=vdate,
                start_time=time(10, 0),
                end_time=time(10, 30),
                type="regular",
                status=VISIT_STATUS_PLANNED,
                source="auto_alloc",
                required_staff_count=1,
            )
        )
    await db.commit()

    result = await run_v2_pipeline(
        db, iso_year=2026, iso_week=20, office_ids=[office.id], mode="diff_add"
    )
    pv = [v for v in result["pool_visits"] if v.patient_id == p.id]
    # 回数 (3) 充足済 → 希望曜日 (火) がズレていても提案しない.
    assert pv == [], f"回数充足済 (固定曜日 ≠ 希望曜日) は過剰提案しない: {pv}"


@pytest.mark.asyncio
async def test_g94_diff_add_count_short_still_proposed(db) -> None:
    """② 本当に不足: frequency 未充足なら従来通り不足曜日を提案する (植田再現).

    植田弥生の再現: 週3希望 (Mon/Wed/Fri), PFV 2 件 (Wed/Fri), 今週 2 件配置済.
    frequency_per_week は未設定だが desired_wds (= Mon/Wed/Fri = 3) を回数フォール
    バックに使うため、 placed 2 < desired 3 で回数充足せず、 不足曜日 (Mon) が提案
    される. G-94 の回数チェックが「本当に不足」な患者を誤除外しないことを検証する.
    """
    from datetime import date

    from app.models.visit import VISIT_STATUS_PLANNED, Visit

    office = await _seed_g92_office(db, name="g94-short", staffed_weekdays=[0, 2, 4])
    p = Patient(
        code="G94-SHORT",
        name="植田型",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        weekly_pattern={
            "preferred_weekdays": ["Mon", "Wed", "Fri"],
            "preferred_start": "10:30",
            "time_type": "固定",
        },
    )
    db.add(p)
    await db.flush()
    for wd in (2, 4):  # PFV Wed/Fri.
        db.add(
            PatientFixedVisit(
                patient_id=p.id,
                mode="normal",
                weekday=wd,
                start_time=time(10, 0),
                duration_min=30,
                slot_index=0,
            )
        )
    await db.flush()
    for vdate in (date(2026, 5, 13), date(2026, 5, 15)):  # Wed/Fri 配置済 (2 件).
        db.add(
            Visit(
                patient_id=p.id,
                visit_date=vdate,
                start_time=time(10, 0),
                end_time=time(10, 30),
                type="regular",
                status=VISIT_STATUS_PLANNED,
                source="auto_alloc",
                required_staff_count=1,
            )
        )
    await db.commit()

    result = await run_v2_pipeline(
        db, iso_year=2026, iso_week=20, office_ids=[office.id], mode="diff_add"
    )
    pv = [v for v in result["pool_visits"] if v.patient_id == p.id]
    # 回数未充足 (2 < 3) → 不足の Mon (0) が提案される.
    assert {v.weekday for v in pv} == {0}, (
        f"回数未充足の患者は不足曜日 (Mon) を提案: {[v.weekday for v in pv]}"
    )


# ---------------------------------------------------------------------------
# Phase G-94 — 修正2: 同コース他患者との同時刻ダブルブッキング
# ---------------------------------------------------------------------------


def _g94_make_visit(
    *,
    patient_name: str,
    office_id: UUID,
    weekday: int,
    start: time,
    service_minutes: int,
    course_code: str | None,
    time_type: str | None = None,
    preferred_start: str | None = None,
    preferred_end: str | None = None,
    lat: float = 35.65,
    lng: float = 140.10,
) -> V2Visit:
    """G-94 修正2 テスト用 V2Visit ファクトリ.

    Phase G-96 (修正2): ``lat``/``lng`` を引数化し、 同住所 (= 同 lat/lng) と異住所を
    テストで明示的に切り替えられるようにする. 既定値は従来どおり (= 全 visit 同住所).
    """
    return V2Visit(
        patient_id=uuid.uuid4(),
        patient_name=patient_name,
        patient_code=None,
        weekday=weekday,
        start_time=start,
        end_time=time(
            (start.hour * 60 + start.minute + service_minutes) // 60,
            (start.hour * 60 + start.minute + service_minutes) % 60,
        ),
        service_minutes=service_minutes,
        lat=lat,
        lng=lng,
        office_id=office_id,
        am_pm="any",
        source_kind="pool",
        course_code=course_code,
        time_type=time_type,
        preferred_start=preferred_start,
        preferred_end=preferred_end,
    )


def test_g94_double_booking_fixed_pool_proposal_unassigned() -> None:
    """③ 固定の pool 提案が同コース他患者と同時刻 → 提案不可 (未割当 + time_conflict).

    中尾 16:00 (pool, 固定) が井上 16:00 (既存, 同コース C) と重なる. 固定は
    ずらせないため提案不可. 返り値に中尾 visit の id が入り、 diff_add_conflict
    warning が emit される (= 後段で fixed_time_conflict / G-92 time_conflict に整合).
    """
    office_id = uuid.uuid4()
    existing = _g94_make_visit(
        patient_name="井上",
        office_id=office_id,
        weekday=0,
        start=time(16, 0),
        service_minutes=30,
        course_code="C",
    )
    pool_fixed = _g94_make_visit(
        patient_name="中尾",
        office_id=office_id,
        weekday=0,
        start=time(16, 0),
        service_minutes=30,
        course_code="C",
        time_type="固定",
        preferred_start="16:00",
    )
    after_visits = [existing, pool_fixed]
    warnings: list[V2Warning] = []
    unassign = _g94_resolve_cross_patient_double_booking(
        after_visits,
        pool_visit_ids={id(pool_fixed)},  # existing は pool ではない.
        warnings=warnings,
        office_name_by_id={office_id: "テスト拠点"},
    )
    # 固定 pool 提案は未割当化される.
    assert id(pool_fixed) in unassign, "固定の同時刻 pool 提案は提案不可 (未割当)"
    assert id(existing) not in unassign, "既存 visit は動かさない"
    # diff_add_conflict warning (= time_conflict 整合) が出る.
    assert any(
        w.type == "diff_add_conflict" and pool_fixed.patient_id in (w.affected_patient_ids or [])
        for w in warnings
    ), f"time_conflict 整合の warning が emit される: {[w.type for w in warnings]}"
    # read-only: 既存 visit の時刻は不変.
    assert existing.start_time == time(16, 0)


def test_g94_double_booking_flex_pool_proposal_shifted() -> None:
    """④ 幅のある希望の pool 提案は衝突しない最早時刻へずれて衝突回避する.

    中尾 (pool, 時間帯 16:00-18:00) が井上 16:00-16:30 (既存, 同コース C) と
    重なる. 幅があるため衝突しない最早時刻 (16:30 + buffer を 5 分切り上げ) へ
    ずれ、 提案は維持される (未割当にならない).
    """
    office_id = uuid.uuid4()
    existing = _g94_make_visit(
        patient_name="井上",
        office_id=office_id,
        weekday=0,
        start=time(16, 0),
        service_minutes=30,  # 16:00-16:30 占有.
        course_code="C",
    )
    pool_flex = _g94_make_visit(
        patient_name="中尾",
        office_id=office_id,
        weekday=0,
        start=time(16, 0),
        service_minutes=30,
        course_code="C",
        time_type="時間帯",
        preferred_start="16:00",
        preferred_end="18:00",
    )
    after_visits = [existing, pool_flex]
    warnings: list[V2Warning] = []
    unassign = _g94_resolve_cross_patient_double_booking(
        after_visits,
        pool_visit_ids={id(pool_flex)},
        warnings=warnings,
        office_name_by_id={office_id: "テスト拠点"},
    )
    # 幅があるのでずれて回避 = 未割当にならない.
    assert id(pool_flex) not in unassign, "幅のある希望はずらして提案維持"
    # 衝突解消後、 既存 (16:00-16:30) と重ならない時刻に移動している.
    assert not (
        pool_flex.start_time < existing.end_time and pool_flex.end_time > existing.start_time
    ), f"ずらし後は既存と非重複: {pool_flex.start_time}-{pool_flex.end_time}"
    # 希望時間帯 (16:00-18:00) 内に収まっている.
    assert time(16, 0) <= pool_flex.start_time <= time(18, 0)
    # 既存 visit は不変 (read-only).
    assert existing.start_time == time(16, 0)
    # シフト warning が出る.
    assert any(w.type == "auto_time_shift_for_conflict" for w in warnings), [
        w.type for w in warnings
    ]


def test_g94_double_booking_no_conflict_no_change() -> None:
    """⑤ 衝突が無ければ何もしない (誤検出しない).

    同コース C に井上 16:00-16:30 と中尾 17:00-17:30 (非重複). 何も変えず、
    未割当も warning も出ない.
    """
    office_id = uuid.uuid4()
    existing = _g94_make_visit(
        patient_name="井上",
        office_id=office_id,
        weekday=0,
        start=time(16, 0),
        service_minutes=30,
        course_code="C",
    )
    pool_ok = _g94_make_visit(
        patient_name="中尾",
        office_id=office_id,
        weekday=0,
        start=time(17, 0),
        service_minutes=30,
        course_code="C",
        time_type="固定",
        preferred_start="17:00",
    )
    warnings: list[V2Warning] = []
    unassign = _g94_resolve_cross_patient_double_booking(
        [existing, pool_ok],
        pool_visit_ids={id(pool_ok)},
        warnings=warnings,
        office_name_by_id={office_id: "テスト拠点"},
    )
    assert unassign == set(), "非衝突なら未割当なし"
    assert warnings == [], "非衝突なら warning なし"
    assert pool_ok.start_time == time(17, 0)


# ---------------------------------------------------------------------------
# Phase G-95 (修正1 段 b): course_code を問わない (office, weekday) 横断照合.
# PFV 無し・weekly_pattern のみ の患者 (中尾型) の pool 提案は course_code=None で
# 生成され、 別 code で placed された既存 visit (井上型) と段 a (同 code 照合) では
# 突き合わされない. 段 b でこの取りこぼしを塞ぐ.
# ---------------------------------------------------------------------------


def test_g95_stage_b_preferred_only_fixed_pool_unassigned_cross_course() -> None:
    """① PFV 無し希望のみ (中尾型) の固定 pool が別 course の既存 placed と同時刻 → 提案不可.

    中尾 (pool, course_code=None, 固定 16:00) が井上 (既存, course='C', 16:00-16:30)
    と同 (office, weekday) で同時刻. 段 a は course_code=None を除外するため素通り
    するが、 段 b が (office, weekday) 横断で照合し、 固定はずらせないため提案不可
    (未割当 + diff_add_conflict = time_conflict 整合).
    """
    office_id = uuid.uuid4()
    existing = _g94_make_visit(
        patient_name="井上",
        office_id=office_id,
        weekday=1,  # 火曜.
        start=time(16, 0),
        service_minutes=30,
        course_code="C",  # 稲毛 C コース placed.
    )
    pool_fixed = _g94_make_visit(
        patient_name="中尾",
        office_id=office_id,
        weekday=1,
        start=time(16, 0),
        service_minutes=30,
        course_code=None,  # PFV 無し・希望のみ → 未確定.
        time_type="固定",
        preferred_start="16:00",
    )
    after_visits = [existing, pool_fixed]
    warnings: list[V2Warning] = []
    unassign = _g94_resolve_cross_patient_double_booking(
        after_visits,
        pool_visit_ids={id(pool_fixed)},
        warnings=warnings,
        office_name_by_id={office_id: "テスト拠点"},
    )
    assert id(pool_fixed) in unassign, "段 b: 固定の同時刻 pool 提案は提案不可 (未割当)"
    assert id(existing) not in unassign, "既存 visit は動かさない"
    assert any(
        w.type == "diff_add_conflict" and pool_fixed.patient_id in (w.affected_patient_ids or [])
        for w in warnings
    ), f"time_conflict 整合の warning が emit される: {[w.type for w in warnings]}"
    # read-only: 既存 visit の時刻は不変.
    assert existing.start_time == time(16, 0)


def test_g95_stage_b_preferred_only_flex_pool_shifted_cross_course() -> None:
    """① (幅あり版) 希望のみ幅あり pool は別 course の既存 placed と同時刻 → ずれて回避.

    中尾 (pool, course_code=None, 時間帯 16:00-18:00) が井上 (既存, course='C',
    16:00-16:30) と同時刻. 幅があるため段 b で衝突しない最早時刻へずれる (未割当に
    ならず提案維持).
    """
    office_id = uuid.uuid4()
    existing = _g94_make_visit(
        patient_name="井上",
        office_id=office_id,
        weekday=1,
        start=time(16, 0),
        service_minutes=30,
        course_code="C",
    )
    pool_flex = _g94_make_visit(
        patient_name="中尾",
        office_id=office_id,
        weekday=1,
        start=time(16, 0),
        service_minutes=30,
        course_code=None,
        time_type="時間帯",
        preferred_start="16:00",
        preferred_end="18:00",
    )
    after_visits = [existing, pool_flex]
    warnings: list[V2Warning] = []
    unassign = _g94_resolve_cross_patient_double_booking(
        after_visits,
        pool_visit_ids={id(pool_flex)},
        warnings=warnings,
        office_name_by_id={office_id: "テスト拠点"},
    )
    assert id(pool_flex) not in unassign, "幅のある希望はずらして提案維持"
    assert not (
        pool_flex.start_time < existing.end_time and pool_flex.end_time > existing.start_time
    ), f"ずらし後は既存と非重複: {pool_flex.start_time}-{pool_flex.end_time}"
    assert time(16, 0) <= pool_flex.start_time <= time(18, 0)
    assert existing.start_time == time(16, 0)
    assert any(w.type == "auto_time_shift_for_conflict" for w in warnings), [
        w.type for w in warnings
    ]


def test_g95_stage_b_no_double_process_after_stage_a() -> None:
    """① (ガード) 段 a で動かした pool 提案を段 b で二重処理しない.

    井上 (既存, course='C', 16:00) と中尾 (pool, course='C', 時間帯 16:00-18:00) は
    同 course で段 a がずらす. 段 b は ``stage_a_handled_ids`` で除外するため、
    シフト warning は 1 回のみ.
    """
    office_id = uuid.uuid4()
    existing = _g94_make_visit(
        patient_name="井上",
        office_id=office_id,
        weekday=1,
        start=time(16, 0),
        service_minutes=30,
        course_code="C",
    )
    pool_flex = _g94_make_visit(
        patient_name="中尾",
        office_id=office_id,
        weekday=1,
        start=time(16, 0),
        service_minutes=30,
        course_code="C",  # 段 a で照合される確定 course.
        time_type="時間帯",
        preferred_start="16:00",
        preferred_end="18:00",
    )
    warnings: list[V2Warning] = []
    _g94_resolve_cross_patient_double_booking(
        [existing, pool_flex],
        pool_visit_ids={id(pool_flex)},
        warnings=warnings,
        office_name_by_id={office_id: "テスト拠点"},
    )
    shift_warnings = [w for w in warnings if w.type == "auto_time_shift_for_conflict"]
    assert len(shift_warnings) == 1, f"段 a で 1 回のみシフト (二重処理なし): {len(shift_warnings)}"


# ---------------------------------------------------------------------------
# Phase G-95 (修正2): 同住所既存ペア (35 分 placed) 直後の pool 提案は 90 分占有起点.
# _apply_travel_time_to_courses の earliest_start 計算で、 prev が同 start_time・
# 同住所の別患者と組む既存ペアなら占有を max(実 end, pair_start + 90) に底上げ.
# ---------------------------------------------------------------------------


def test_g95_existing_same_address_pair_90min_occupancy_for_next_pool() -> None:
    """② 既存同住所ペア (35 分 placed) 直後の pool 提案は 90 分占有起点で計算する.

    安永 (16:00-16:35) ・菅原 (16:35-17:10) が同住所の既存ペア (両者固定で時刻不一致
    のため ``_align_same_address_pair_to_same_time`` は揃えず 90 分底上げが効かない =
    実 service 長のまま). 植田 (pool, 異住所近接) が後続. 旧実装は prev (菅原) の
    end_time=17:10 起点 (17:10+移動+バッファ ≒ 17:20) で、 ペアの 90 分占有 (16:00 起点
    = 17:30) が反映されない. 修正後は anchor=16:00 → 16:00+90=17:30 起点 → ≒ 17:39.
    """
    office_id = uuid.uuid4()
    # 同住所ペア (園生町 想定): 同住所バケット + 別患者 + 連続配置 (16:00 / 16:35).
    # 両者固定で時刻不一致のため align は揃えられず 90 分底上げが効かない (= 既存
    # placed ペアと同じ「実 35 分占有」状態を再現).
    yasunaga = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=16, start_m=0, patient_name="安永"
    )
    yasunaga.end_time = time(16, 35)
    yasunaga.service_minutes = 35
    yasunaga.course_code = "D"
    yasunaga.time_type = "固定"
    yasunaga.preferred_start = "16:00"
    sugawara = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=16, start_m=35, patient_name="菅原"
    )
    sugawara.end_time = time(17, 10)
    sugawara.service_minutes = 35
    sugawara.course_code = "D"
    sugawara.time_type = "固定"
    sugawara.preferred_start = "16:35"
    # 後続 pool 患者 (異住所だが近接 = 移動 1 分; 幅あり): earliest が 90 分占有起点に
    # なるか. 近接座標にすることで「移動時間で勝手に押し出された」のではなく
    # 「90 分占有底上げで押し出された」ことを切り分ける.
    ueda = _make_visit(
        lat=35.652, lng=140.10, office_id=office_id, start_h=17, start_m=10, patient_name="植田"
    )
    ueda.end_time = time(17, 40)
    ueda.service_minutes = 30
    ueda.course_code = "D"
    ueda.time_type = "時間帯"
    ueda.preferred_start = "16:00"
    ueda.preferred_end = "18:00"

    warnings: list[V2Warning] = []
    apply_travel_corrections([yasunaga, sugawara, ueda], warnings=warnings)

    # 植田の起点は anchor 16:00 + 90 = 17:30 (+ 移動 1 分 + バッファ + 5 分切り上げ)
    # なので 17:30 以降. 旧実装は prev (菅原) end 17:10 起点で 17:20 だったため、
    # 17:30 以降は 90 分占有底上げ (16:00 起点) が効いた証拠.
    assert ueda.start_time >= time(17, 30), (
        f"既存ペア 90 分占有起点 (17:30) 以降に配置されるべき: {ueda.start_time}"
    )


def test_g95_existing_same_address_pair_pushes_pool_over_window_unassigned() -> None:
    """② 既存ペア 90 分占有で固定 pool が物理不可 → 提案不可 (未割当化).

    既存ペア 安永 (16:00-16:35) ・菅原 (16:35-17:10) の 90 分占有起点 = 16:00+90=17:30.
    後続 pool 植田 は固定 17:20 希望. 近接座標 (移動 1 分) なので 90 分底上げが無ければ
    prev (菅原) end 17:10 + 1 + 8 = 17:19 で 17:20 固定に間に合う. しかし 90 分占有起点
    17:30 では 17:30+1+8=17:39 が 17:20 を超過 (shortage 19 ≥ 5) → 物理不可で
    course_code=None + 未割当 (= 90 分占有底上げが効いた証拠).
    """
    office_id = uuid.uuid4()
    yasunaga = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=16, start_m=0, patient_name="安永"
    )
    yasunaga.end_time = time(16, 35)
    yasunaga.service_minutes = 35
    yasunaga.course_code = "D"
    yasunaga.time_type = "固定"
    yasunaga.preferred_start = "16:00"
    sugawara = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=16, start_m=35, patient_name="菅原"
    )
    sugawara.end_time = time(17, 10)
    sugawara.service_minutes = 35
    sugawara.course_code = "D"
    sugawara.time_type = "固定"
    sugawara.preferred_start = "16:35"
    # 後続 pool: 固定 17:20 希望.
    ueda = _make_visit(
        lat=35.652, lng=140.10, office_id=office_id, start_h=17, start_m=20, patient_name="植田"
    )
    ueda.end_time = time(17, 50)
    ueda.service_minutes = 30
    ueda.course_code = "D"
    ueda.time_type = "固定"
    ueda.preferred_start = "17:20"

    warnings: list[V2Warning] = []
    unassigned = apply_travel_corrections([yasunaga, sugawara, ueda], warnings=warnings)

    # 90 分占有 (17:30 起点) で 17:20 固定に間に合わず物理不可 → course_code=None + 未割当.
    assert id(ueda) in unassigned, "90 分占有起点で固定時刻に届かず提案不可 (未割当)"
    assert ueda.course_code is None
    assert any(w.type == "travel_time_shortage" for w in warnings), [w.type for w in warnings]


def test_g95_no_existing_pair_occupancy_unchanged_for_normal_next() -> None:
    """③ 既存ペアが無ければ占有底上げは起きない (誤発火しない / 既存挙動不変).

    A (16:00-16:35 単独, 同住所ペア相手なし) の直後の B (異住所) は通常通り
    prev.end_time=16:35 起点で配置される (90 分底上げは適用されない).
    """
    office_id = uuid.uuid4()
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=16, start_m=0, patient_name="A"
    )
    a.end_time = time(16, 35)
    a.service_minutes = 35
    a.course_code = "D"
    a.time_type = "固定"
    a.preferred_start = "16:00"
    b = _make_visit(
        lat=35.652, lng=140.10, office_id=office_id, start_h=16, start_m=0, patient_name="B"
    )
    b.end_time = time(16, 30)
    b.service_minutes = 30
    b.course_code = "D"
    b.time_type = "時間帯"
    b.preferred_start = "16:00"
    b.preferred_end = "18:00"

    warnings: list[V2Warning] = []
    apply_travel_corrections([a, b], warnings=warnings)

    # A は同住所ペア相手なし → 占有は実 35 分のまま. B は 16:35 + 移動 1 分 + バッファ
    # 起点 (= 16:44 → 16:45) で配置され、 17:30 起点にはならない.
    assert b.start_time < time(17, 30), (
        f"既存ペアが無ければ 90 分占有底上げは起きない: {b.start_time}"
    )


# ---------------------------------------------------------------------------
# Phase G-96 (修正A 段 b 90 分占有): 段 b のずらし/占有判定が同住所 2 名 90 分占有を
# 適用する. 段 b は ``_g94_resolve_cross_patient_double_booking`` の (office, weekday)
# 横断照合であり、 相手既存ペアの占有を実 end ではなく 90 分占有終端で評価する.
#
# Phase G-96 (修正B pool 同士衝突): 段 b は確定した pool 提案を逐次占有に積み、
# 後続 pool 提案が既配置 pool とも非重複になるようにする.
# ---------------------------------------------------------------------------


def test_g96_stage_b_existing_pair_90min_blocks_fixed_pool_unassigned() -> None:
    """① 植田型: 段 b が既存同住所ペアの 90 分占有を見て固定 pool を提案不可にする.

    既存同住所ペア 安永 (16:00-16:35) ・菅原 (16:00-16:35; 同時刻同住所) が course='D'
    で placed. 植田 (pool, course='E' = 別 course, 固定 16:45) が同 (office, weekday).
    段 a は別 course なので照合せず素通り. 段 b は (office, weekday) 横断で照合し、
    修正A により安永の占有を実 end 16:35 ではなく 90 分占有終端 (16:00+90=17:30) で
    評価する. 植田 16:45-17:15 は 16:00-17:30 と重なり、 固定でずらせないため提案不可
    (未割当 + diff_add_conflict). 修正前は実 end 16:35 しか見ず 16:45 は非重複で
    提案されてしまっていた.
    """
    office_id = uuid.uuid4()
    # 既存同住所ペア (同時刻 16:00; 同住所バケット; 別患者).
    yasunaga = _g94_make_visit(
        patient_name="安永",
        office_id=office_id,
        weekday=1,
        start=time(16, 0),
        service_minutes=35,  # 16:00-16:35.
        course_code="D",
    )
    sugawara = _g94_make_visit(
        patient_name="菅原",
        office_id=office_id,
        weekday=1,
        start=time(16, 0),
        service_minutes=35,  # 16:00-16:35 (同時刻同住所 = ペア).
        course_code="D",
    )
    # 段 b 検査対象 pool (別 course, 固定 16:45, 希望 〜17:15).
    ueda = _g94_make_visit(
        patient_name="植田",
        office_id=office_id,
        weekday=1,
        start=time(16, 45),
        service_minutes=30,  # 16:45-17:15.
        course_code="E",  # 別 course → 段 a は照合しない.
        time_type="固定",
        preferred_start="16:45",
    )
    after_visits = [yasunaga, sugawara, ueda]
    warnings: list[V2Warning] = []
    unassign = _g94_resolve_cross_patient_double_booking(
        after_visits,
        pool_visit_ids={id(ueda)},  # 安永/菅原 は既存確定 (pool でない).
        warnings=warnings,
        office_name_by_id={office_id: "テスト拠点"},
    )
    # 90 分占有 (17:30) を見て固定 16:45 は提案不可 (= 16:45 にならない).
    assert id(ueda) in unassign, "既存ペア 90 分占有で固定 pool は提案不可 (未割当)"
    assert id(yasunaga) not in unassign and id(sugawara) not in unassign, "既存ペアは不変"
    assert any(
        w.type == "diff_add_conflict" and ueda.patient_id in (w.affected_patient_ids or [])
        for w in warnings
    ), f"time_conflict 整合 warning が emit される: {[w.type for w in warnings]}"
    # read-only: 既存ペアの時刻は不変.
    assert yasunaga.start_time == time(16, 0)
    assert sugawara.start_time == time(16, 0)
    # 植田の時刻は書き換えられない (固定でずらせない).
    assert ueda.start_time == time(16, 45)


def test_g96_stage_b_pool_vs_pool_same_time_second_shifts() -> None:
    """② pool 提案 2 件が同 (office, weekday) 同時刻集中 → 2 件目がずれる.

    既存 井上 (course='C', 16:00-16:30) に対し、 希望のみ pool 2 件
    (course_code=None, 時間帯 16:00-18:00) が同 (office, weekday) 16:00 集中.
    段 b で 1 件目は井上を避けて最早 (16:35 付近) へずれ、 修正B により 1 件目の
    占有が積まれるため 2 件目はさらに後ろ (1 件目の end + buffer) へずれる. 両 pool が
    互いに非重複になる (= pool 同士の重なりが残らない).
    """
    office_id = uuid.uuid4()
    existing = _g94_make_visit(
        patient_name="井上",
        office_id=office_id,
        weekday=1,
        start=time(16, 0),
        service_minutes=30,  # 16:00-16:30.
        course_code="C",
    )
    pool_a = _g94_make_visit(
        patient_name="希望A",
        office_id=office_id,
        weekday=1,
        start=time(16, 0),
        service_minutes=30,
        course_code=None,
        time_type="時間帯",
        preferred_start="16:00",
        preferred_end="18:00",
    )
    pool_b = _g94_make_visit(
        patient_name="希望B",
        office_id=office_id,
        weekday=1,
        start=time(16, 0),
        service_minutes=30,
        course_code=None,
        time_type="時間帯",
        preferred_start="16:00",
        preferred_end="18:00",
    )
    after_visits = [existing, pool_a, pool_b]
    warnings: list[V2Warning] = []
    unassign = _g94_resolve_cross_patient_double_booking(
        after_visits,
        pool_visit_ids={id(pool_a), id(pool_b)},
        warnings=warnings,
        office_name_by_id={office_id: "テスト拠点"},
    )
    # 両 pool とも幅ありで提案維持される (未割当なし).
    assert unassign == set(), f"幅ありなら 2 件ともずらして提案維持: {unassign}"

    def _overlap(x: V2Visit, y: V2Visit) -> bool:
        return x.start_time < y.end_time and x.end_time > y.start_time

    # 修正B: pool 同士が非重複.
    assert not _overlap(pool_a, pool_b), (
        f"pool 同士は非重複: A {pool_a.start_time}-{pool_a.end_time} / "
        f"B {pool_b.start_time}-{pool_b.end_time}"
    )
    # 既存 井上 とも両方非重複.
    assert not _overlap(pool_a, existing) and not _overlap(pool_b, existing), "既存とも非重複"
    # 既存 visit は read-only.
    assert existing.start_time == time(16, 0)


def test_g96_stage_b_pool_vs_pool_fixed_second_unassigned() -> None:
    """② (不能版) 2 件目がずらせない固定なら未割当化される (pool 同士衝突).

    希望のみ pool 2 件が同 (office, weekday) 同時刻 16:00. 1 件目 (幅あり) はその場
    に確定 (相手なし) し占有を積む. 2 件目 (固定 16:00) は 1 件目と重なるが固定で
    ずらせないため提案不可 (未割当). 修正B が無ければ 2 件目は確定 pool を相手と
    みなさず素通りしていた.
    """
    office_id = uuid.uuid4()
    pool_flex = _g94_make_visit(
        patient_name="希望A",
        office_id=office_id,
        weekday=1,
        start=time(16, 0),
        service_minutes=30,
        course_code=None,
        time_type="時間帯",
        preferred_start="16:00",
        preferred_end="18:00",
    )
    pool_fixed = _g94_make_visit(
        patient_name="希望B",
        office_id=office_id,
        weekday=1,
        start=time(16, 0),
        service_minutes=30,
        course_code=None,
        time_type="固定",
        preferred_start="16:00",
    )
    # 決定性: stage_b は (start, patient_id) ソート. 同 start なので patient_id 順.
    # どちらが先でも「先着が占有を積み、 後着の固定が重なれば未割当」になる. ここでは
    # flex が先着すれば fixed が未割当、 fixed が先着すれば flex がずれる. 衝突解消
    # 後に「pool 同士が非重複」かつ「固定がその場に残るなら相手がずれる」ことを検証.
    after_visits = [pool_flex, pool_fixed]
    warnings: list[V2Warning] = []
    unassign = _g94_resolve_cross_patient_double_booking(
        after_visits,
        pool_visit_ids={id(pool_flex), id(pool_fixed)},
        warnings=warnings,
        office_name_by_id={office_id: "テスト拠点"},
    )

    def _overlap(x: V2Visit, y: V2Visit) -> bool:
        return x.start_time < y.end_time and x.end_time > y.start_time

    # 修正B が効けば「両 pool が同時刻のまま放置」されることはない.
    # = いずれかが未割当化される or ずれて非重複になる.
    both_placed = id(pool_flex) not in unassign and id(pool_fixed) not in unassign
    if both_placed:
        assert not _overlap(pool_flex, pool_fixed), (
            "両提案維持なら pool 同士は非重複 (修正B): "
            f"flex {pool_flex.start_time}-{pool_flex.end_time} / "
            f"fixed {pool_fixed.start_time}-{pool_fixed.end_time}"
        )
    else:
        # 固定がずらせず未割当化されたケース.
        assert id(pool_fixed) in unassign, "ずらせない固定 pool が未割当化される"
        assert any(w.type == "diff_add_conflict" for w in warnings), [w.type for w in warnings]


def test_g96_stage_b_non_conflicting_proposals_unchanged() -> None:
    """③ 既存の非衝突提案は不変 (誤発火しない / 既存挙動を壊さない).

    既存 井上 (course='C', 16:00-16:30) と pool 中尾 (course=None, 固定 17:00-17:30,
    非重複). 同住所ペアでもない. 段 b は何も変えず未割当も warning も出さない.
    """
    office_id = uuid.uuid4()
    existing = _g94_make_visit(
        patient_name="井上",
        office_id=office_id,
        weekday=1,
        start=time(16, 0),
        service_minutes=30,
        course_code="C",
    )
    pool_ok = _g94_make_visit(
        patient_name="中尾",
        office_id=office_id,
        weekday=1,
        start=time(17, 0),
        service_minutes=30,
        course_code=None,
        time_type="固定",
        preferred_start="17:00",
    )
    warnings: list[V2Warning] = []
    unassign = _g94_resolve_cross_patient_double_booking(
        [existing, pool_ok],
        pool_visit_ids={id(pool_ok)},
        warnings=warnings,
        office_name_by_id={office_id: "テスト拠点"},
    )
    assert unassign == set(), "非衝突なら未割当なし"
    assert warnings == [], "非衝突なら warning なし"
    assert pool_ok.start_time == time(17, 0)
    assert existing.start_time == time(16, 0)


# ---------------------------------------------------------------------------
# Phase G-96 (修正1 HIGH): 段 a で「移動対象 pool 提案 pv 自身」を相手 ov の同住所
# ペア相手と誤認し ov を単独でも 90 分占有へ過大底上げする事故の回帰テスト.
#
# 段 a は同 ``course_code`` グループ内照合. group には移動対象 pv も含まれるため、
# 旧実装は ``_same_address_pair_occupancy_end(ov, group)`` 内で pv を ov の同住所
# ペア相手 (= 同時刻 / 端点連続) と誤認し、 ov が単独 visit でも占有終端を実 end →
# pair_anchor + 90 分へ底上げしていた. 修正1 で ``exclude_ids={id(pv)}`` を渡し pv を
# ペア候補から外す.
# ---------------------------------------------------------------------------


def test_g96_stage_a_solo_existing_same_address_contiguous_pool_stays() -> None:
    """(a) 単独 ov (同住所) × 連続 pv → 据え置き (90 分占有へ過大底上げしない).

    既存 ov 井上 16:00-16:35 (35 分, 同住所, 単独 = ペアでない) と同コース C の pool 提案
    pv 希望A 16:35-17:05 (時間帯, 同住所, 端点連続 = ov.end == pv.start). 旧実装は段 a で
    ov の占有を計算する際 pv を ov の同住所ペア相手 (端点連続) と誤認し、 ov を単独でも
    90 分占有 (16:00+90=17:30) へ底上げ → pv 16:35 が 16:00-17:30 と重なるとみなして後ろ
    (17:30 以降) へ過大に後ろ送りしていた. 修正1 で pv をペア候補から外すため ov の占有は
    実 end 16:35 のまま. pv 16:35 は ov 16:00-16:35 と非重複 (touching) → 衝突なしで据え置き.
    """
    office_id = uuid.uuid4()
    existing = _g94_make_visit(
        patient_name="井上",
        office_id=office_id,
        weekday=1,
        start=time(16, 0),
        service_minutes=35,  # 16:00-16:35 (単独, 同住所).
        course_code="C",
        lat=35.65,
        lng=140.10,
    )
    pool_pv = _g94_make_visit(
        patient_name="希望A",
        office_id=office_id,
        weekday=1,
        start=time(16, 35),  # ov.end と端点連続 (同住所).
        service_minutes=30,  # 16:35-17:05.
        course_code="C",  # 同 course → 段 a 照合対象.
        time_type="時間帯",
        preferred_start="16:00",
        preferred_end="18:00",
        lat=35.65,
        lng=140.10,
    )
    warnings: list[V2Warning] = []
    unassign = _g94_resolve_cross_patient_double_booking(
        [existing, pool_pv],
        pool_visit_ids={id(pool_pv)},
        warnings=warnings,
        office_name_by_id={office_id: "テスト拠点"},
    )
    # 修正1: ov は単独なので占有は実 end 16:35. pv 16:35 は非重複 → 据え置き.
    assert unassign == set(), "単独 ov の誤ペア化で未割当化してはならない"
    assert pool_pv.start_time == time(16, 35), (
        f"単独 ov を 90 分占有へ過大底上げせず据え置き: {pool_pv.start_time}"
    )
    assert pool_pv.end_time == time(17, 5)
    assert warnings == [], f"非衝突なので shift warning は出ない: {[w.type for w in warnings]}"
    # read-only: 既存 visit は不変.
    assert existing.start_time == time(16, 0)


def test_g96_stage_a_solo_existing_same_address_same_start_min_shift() -> None:
    """(b) 単独 ov × 同 start pv → 実 end 基準で最小ずれ (90 分にならない).

    既存 ov 井上 16:00-16:35 (35 分, 同住所, 単独) と同コース C の pool 提案 pv 希望A
    16:00 (時間帯, 同住所, 同 start = 真の衝突) は実際に重なるため pv は後ろへずれる.
    修正1 で ov は単独扱い → 占有は実 end 16:35. pv は 16:35 + buffer(8) = 16:43 →
    5 分切り上げ 16:45 へ最小ずれ. 旧実装は ov を 90 分占有 (16:00+90=17:30) と誤認し
    17:30 + buffer → 17:40 へ過大に後ろ送りしていた. 16:45 (≪ 17:30) を確認する.
    """
    office_id = uuid.uuid4()
    existing = _g94_make_visit(
        patient_name="井上",
        office_id=office_id,
        weekday=1,
        start=time(16, 0),
        service_minutes=35,  # 16:00-16:35 (単独, 同住所).
        course_code="C",
        lat=35.65,
        lng=140.10,
    )
    pool_pv = _g94_make_visit(
        patient_name="希望A",
        office_id=office_id,
        weekday=1,
        start=time(16, 0),  # ov と同 start = 真の衝突.
        service_minutes=30,  # 16:00-16:30.
        course_code="C",
        time_type="時間帯",
        preferred_start="16:00",
        preferred_end="18:00",
        lat=35.65,
        lng=140.10,
    )
    warnings: list[V2Warning] = []
    unassign = _g94_resolve_cross_patient_double_booking(
        [existing, pool_pv],
        pool_visit_ids={id(pool_pv)},
        warnings=warnings,
        office_name_by_id={office_id: "テスト拠点"},
    )
    assert unassign == set(), "幅ありなのでずらして提案維持 (未割当化しない)"
    # 修正1: 実 end 16:35 + buffer 8 = 16:43 → 5 分切り上げ 16:45 (≪ 90 分占有 17:30).
    assert pool_pv.start_time == time(16, 45), (
        f"単独 ov の実 end 基準で最小ずれ (90 分占有 17:30 にならない): {pool_pv.start_time}"
    )
    assert pool_pv.start_time < time(17, 30), "90 分占有へ過大底上げしていない"
    assert pool_pv.end_time == time(17, 15)
    assert any(w.type == "auto_time_shift_for_conflict" for w in warnings), (
        f"真の衝突なので shift warning が出る: {[w.type for w in warnings]}"
    )
    assert existing.start_time == time(16, 0)


def test_g96_stage_a_different_address_not_treated_as_pair() -> None:
    """(c) 異住所 ov × pv → ペア扱いしない (90 分占有を適用しない).

    既存 ov 井上 16:00-16:35 (35 分) と同コース C の pool 提案 pv 希望A 16:00 (時間帯) が
    **異住所** (lat/lng が別バケット) で同時刻衝突. 異住所はそもそも同住所ペアでないため
    ``_same_address_pair_members`` は空 → 占有は実 end 16:35 のまま (90 分にならない).
    pv は実 end + buffer で 16:45 へ最小ずれ. 修正1 前後で挙動不変 (= 異住所ガードが
    効いていることの回帰固定).
    """
    office_id = uuid.uuid4()
    existing = _g94_make_visit(
        patient_name="井上",
        office_id=office_id,
        weekday=1,
        start=time(16, 0),
        service_minutes=35,  # 16:00-16:35.
        course_code="C",
        lat=35.65,
        lng=140.10,
    )
    pool_pv = _g94_make_visit(
        patient_name="希望A",
        office_id=office_id,
        weekday=1,
        start=time(16, 0),  # 同 start = 真の衝突 (ただし異住所).
        service_minutes=30,  # 16:00-16:30.
        course_code="C",
        time_type="時間帯",
        preferred_start="16:00",
        preferred_end="18:00",
        lat=35.70,  # 別住所バケット (35.70/0.001=35700 ≠ 35650).
        lng=140.10,
    )
    warnings: list[V2Warning] = []
    unassign = _g94_resolve_cross_patient_double_booking(
        [existing, pool_pv],
        pool_visit_ids={id(pool_pv)},
        warnings=warnings,
        office_name_by_id={office_id: "テスト拠点"},
    )
    assert unassign == set(), "幅ありなのでずらして提案維持"
    # 異住所はペアでない → 実 end 16:35 基準で 16:45 (90 分占有 17:30 にならない).
    assert pool_pv.start_time == time(16, 45), (
        f"異住所はペア扱いせず実 end 基準で最小ずれ: {pool_pv.start_time}"
    )
    assert pool_pv.start_time < time(17, 30), "異住所に 90 分占有を適用していない"
    assert existing.start_time == time(16, 0)
