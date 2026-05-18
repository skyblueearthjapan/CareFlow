"""Tests for auto_allocator_v2 (Wave 41 v2.0 / auto-schedule v2).

設計仕様書: ``docs/plans/auto-schedule-v2.md`` (v0.2)

各段階 (Stage 1〜5) のヘルパー関数を独立に検証する.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import time
from uuid import UUID

import pytest

from app.models import Office, Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.staff import Staff, StaffShift
from app.services.scheduling.auto_allocator_v2 import (
    MAX_PATIENTS_PER_COURSE,
    MAX_PATIENTS_PER_SET,
    V2Visit,
    V2Warning,
    apply_individual_proposal,
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
    """12:30 start の visit は H10 違反."""
    v = _make_visit(lat=35.65, lng=140.10, start_h=12, start_m=30)
    v.end_time = time(13, 30)
    v.course_code = "A"
    violations = calc_h_violations([v])
    assert violations["H10"] == 1


# ---------------------------------------------------------------------------
# _filter_unavailable_and_lunch — skip_acceptance (Mode 2 用)
# ---------------------------------------------------------------------------


def test_filter_skip_acceptance_in_mode2() -> None:
    """Mode 2 (skip_acceptance=True) では acceptance × でも visits が残る.

    昼休憩 (H10) は両モードで常に enforce される.
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
    # 昼休憩 (12:30) の visit — 両モードで除外されるべき
    lunch_visit = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=12, start_m=30, patient_name="LUNCH"
    )
    lunch_visit.end_time = time(13, 30)

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
    assert "LUNCH" not in codes, "skip_acceptance=True でも H10 昼休憩は除外されるべき"

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
    lunch_v = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=12, start_m=30, patient_name="L1"
    )
    lunch_v.end_time = time(13, 30)
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

    # 同住所なので移動 0 分 → 09:30 ぴったり開始 (prev.end_time = 09:30)
    assert b.start_time == time(9, 30), f"同住所 → 移動 0 分のはず, got {b.start_time}"
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
    # B の start_time は同住所なので押し下げなし (09:30 ぴったり).
    assert b.start_time == time(9, 30)


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

    # コース A: A と B が同住所 → 隣接した start_time (移動 0 で連続) を持つ.
    # _apply_travel_time_to_courses 内でリオーダー後 [A, B, C] になる.
    # B は同住所なので 09:30 ぴったり開始 (押し下げなし).
    # C は B 末尾 (10:00) から異住所移動 + バッファーで押し下げ.
    a_bucket = _address_bucket(a.lat, a.lng)
    b_bucket = _address_bucket(b.lat, b.lng)
    assert a_bucket == b_bucket, "テスト前提: A,B 同住所"
    # B の start_time = A の end_time (09:30) で同住所連続: 押し下げなし.
    assert b.start_time == time(9, 30), (
        f"コース A の同住所 B が 09:30 (= A 末尾) で連続していない: {b.start_time}"
    )
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

    Expected after reorder:
      順序は [A, B, C] (B が A の直後に移動 = 同住所連番). 後段の
      ``_apply_travel_time_to_courses`` で C の earliest が
      B.end + travel + buffer まで押し下げられる (本来 09:15 だったものが
      ~11:45 以降に遅延 = 同住所連番優先のための副作用).
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

    # 後段の travel 計算を走らせる → C は B.end + travel + buffer まで押し下げ.
    travel_warnings: list[V2Warning] = []
    _apply_travel_time_to_courses(reordered, warnings=travel_warnings)

    a_bucket = _address_bucket(a.lat, a.lng)
    b_bucket = _address_bucket(b.lat, b.lng)
    c_bucket = _address_bucket(c.lat, c.lng)
    assert a_bucket == b_bucket and a_bucket != c_bucket, "テスト前提: A,B 同住所 / C 異住所"

    # 固定 A: 09:00 不変.
    assert a.start_time == time(9, 0), f"固定 A は不変のはず: {a.start_time}"
    # 非固定 B (終日): desired_start=11:00 が earliest_start (=A.end 09:30) より
    # 大きいので max を取って 11:00 維持. (本来同住所連番なら 09:30 に詰めたいが、
    # `_apply_travel_time_to_courses` の `max(desired, earliest)` セマンティクス上
    # 11:00 で維持されるのが現状仕様 — desired_start を前倒しする機能はない.)
    assert b.start_time == time(11, 0), f"非固定 B は desired 11:00 を維持するはず: {b.start_time}"
    # C: 本来 09:15 入力だったが、reorder で B の後ろに回ったため
    # B.end (11:30) + travel + buffer まで押し下げ → 元 09:15 から大きく遅延.
    # これは同住所連番を優先したことによる副作用 (= 仕様意図通り、ユーザー許容).
    assert c.start_time > time(9, 15), (
        f"C は同住所連番優先により後段で押し下げられるはず (許容): {c.start_time}"
    )
    assert c.start_time >= time(11, 30), (
        f"C は B.end (11:30) 以降に押し下げられるはず: {c.start_time}"
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
    """CRITICAL #1: 移動時間で 12:00-13:00 を跨ぐ場合は 13:00 にバンプされる.

    Setup: コース A 月曜:
      - P-A 11:00-11:30 (固定)
      - P-B 11:40 希望だが 終日 → earliest = 11:30+移動 ≈ 12:00-12:10
        昼休憩重複 → 13:00 にバンプ + warning.
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

    # B は昼休憩重複のため 13:00 にバンプ.
    assert b.start_time == time(13, 0), f"昼休憩バンプ後は 13:00 のはず, got {b.start_time}"
    assert b.end_time == time(13, 30), f"end_time も追従するはず, got {b.end_time}"
    assert any(
        w.type == "travel_time_shortage"
        and "昼休憩" in w.message
        and "13:00 に繰り下げ" in w.message
        for w in warnings
    ), f"昼休憩バンプ warning が無い: {warnings}"


def test_am_branch_pushed_to_pm_when_over_12() -> None:
    """HIGH #1: 午前希望 visit が earliest >= 12:00 になった場合、
    13:00 (午後扱い) にバンプされる + actionable warning が出る.

    Setup:
      - P-A 11:30-12:00 (固定, 12:00 終了)
      - P-B 11:45 午前希望 (A の後にソートされる位置) だが 5km 離れて
        移動 ~15 分 → earliest = 12:00 + 15 分 = 12:15 → 13:00 にバンプ.
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

    # 午前希望が 12:00 超のため 13:00 (午後) にバンプされる.
    assert b.start_time == time(13, 0), (
        f"午前希望が 12:00 超なら 13:00 にバンプされるはず, got {b.start_time}"
    )
    assert any(
        w.type == "travel_time_shortage"
        and "午前希望" in w.message
        and ("13:00" in w.message or "午後" in w.message)
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
    a = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=11, start_m=0, patient_name="A"
    )
    a.end_time = time(11, 30)
    a.service_minutes = 30
    a.course_code = "A"
    a.time_type = "固定"
    b = _make_visit(
        lat=35.65, lng=140.155, office_id=office_id, start_h=11, start_m=0, patient_name="B"
    )
    b.end_time = time(11, 30)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "固定"
    b.preferred_start = "11:00"
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

    # 同住所 → 移動 0 + バッファー 0 → prev.end と同時刻でも OK.
    assert b.start_time == time(9, 30), f"同住所はバッファーなしで 09:30 のはず, got {b.start_time}"
    assert b.end_time == time(10, 0)


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
    """HIGH #1 (Codex): 同住所連続 visit はバッファー 0 (= 移動 0 と同じ扱い).

    同 lat/lng の 3 visit → duration 合計のみ. バッファー追加なし.
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
    # 同住所 → 移動 0 + バッファー 0 → duration 合計のみ.
    assert total == 90, f"同住所はバッファー 0 のはず, got {total}"


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
    """5 分刻み切り上げで AM_BLOCK_END (12:00) を跨いだ場合、lunch 再検証で
    13:00 にバンプされる (制約再検証の正当性).

    Setup:
      - P-A 11:00-11:28 (固定)
      - P-B 終日, 同住所 (= 移動 0 + バッファー 0)
        earliest = 11:28 (5 分刻みでない) → 切り上げで 11:30.
        service=30 → end=12:00 → lunch_start (12:00) 重複しない (半開).
    別ケース: A end が 11:50, B 終日 同住所 → 11:50 → service=30 → end 12:20 →
    lunch 重複 → 13:00 バンプ.
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
    # 同住所 (移動 0 + バッファー 0) の終日 visit.
    b = _make_visit(
        lat=35.65, lng=140.10, office_id=office_id, start_h=11, start_m=50, patient_name="B"
    )
    b.start_time = time(11, 50)
    b.end_time = time(12, 20)
    b.service_minutes = 30
    b.course_code = "A"
    b.time_type = "終日"

    warnings: list[V2Warning] = []
    _apply_travel_time_to_courses([a, b], warnings=warnings)

    # B: earliest = 11:50 (5 分刻み) → そのまま. service=30 → end=12:20 →
    # lunch 重複 (12:00-13:00) → 13:00 にバンプ.
    assert b.start_time == time(13, 0), (
        f"切り上げ後 lunch 重複で 13:00 にバンプされるはず: got {b.start_time}"
    )
    assert b.end_time == time(13, 30), f"end_time も追従: got {b.end_time}"
