"""Tests for auto_allocator_v2 (Wave 41 v2.0 / auto-schedule v2).

設計仕様書: ``docs/plans/auto-schedule-v2.md`` (v0.2)

各段階 (Stage 1〜5) のヘルパー関数を独立に検証する.
"""

from __future__ import annotations

import uuid
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
    warnings: list[str] = []
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
    warnings: list[str] = []
    courses = combine_am_pm_sets([am1, am2], [pm1, pm2], staff_count=2, warnings=warnings)
    # 各コースの合計 visits 数は 6 以下
    for am, pm in courses:
        total = (len(am.visits) if am else 0) + (len(pm.visits) if pm else 0)
        assert total <= MAX_PATIENTS_PER_COURSE


def test_combine_am_pm_single_side_only() -> None:
    """片方しかセットがない場合は単独コース."""
    from app.services.scheduling.auto_allocator_v2 import V2Set

    am1 = V2Set(visits=[_make_visit(lat=35.65, lng=140.10)])
    warnings: list[str] = []
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
    warnings: list[str] = []

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
    assert not any("blocked by acceptance_calendar" in w for w in warnings), (
        f"skip_acceptance=True なのに acceptance_calendar warning が出ている: {warnings}"
    )
    # 昼休憩 warning は出ている
    assert any("lunch break" in w for w in warnings), (
        f"H10 lunch break warning が出ていない: {warnings}"
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
    warnings: list[str] = []

    filtered = _filter_unavailable_and_lunch(
        [blocked_visit, ok_visit],
        unavailable_slots=unavailable,
        warnings=warnings,
    )
    codes = {v.patient_code for v in filtered}
    assert "X" not in codes, "Mode 1 (default) では acceptance × visit は除外されるべき"
    assert "OK" in codes
    assert any("blocked by acceptance_calendar" in w for w in warnings)


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
    assert "FIXED" not in pool_codes


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
    warnings: list[str] = []
    # 例外を投げずに完走すれば OK
    _enforce_h2_same_address(sets, warnings)
    # 1 つ以上のセットに 2 件まで集約され, 3 件目は警告として残る
    assert any("3+ visits" in w for w in warnings) or all(len(s.visits) <= 2 for s in sets)


# ---------------------------------------------------------------------------
# H4 — staff_count == 0 のとき course_code='M' + 警告
# ---------------------------------------------------------------------------


def test_combine_am_pm_zero_staff_emits_warning() -> None:
    """H4: staff_count=0 のとき manager 補充警告が出る."""
    from app.services.scheduling.auto_allocator_v2 import V2Set, combine_am_pm_sets

    am1 = V2Set(visits=[_make_visit(lat=35.65, lng=140.10)])
    warnings: list[str] = []
    combine_am_pm_sets([am1], [], staff_count=0, warnings=warnings)
    assert any("スタッフ 0 名" in w for w in warnings)


@pytest.mark.asyncio
async def test_run_v2_pipeline_zero_staff_assigns_course_code_m(db) -> None:
    """H4: staff_count=0 のとき after_visits.course_code は 'M' になる."""
    office = Office(name="h4-zero-staff")
    db.add(office)
    await db.flush()
    # スタッフを登録しない (= staff_count=0)
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
