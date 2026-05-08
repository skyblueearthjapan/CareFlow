"""W16-BE2: Layer 3 曜日別ローテーション + 同患者連続回避テスト.

Wave 16 で追加された Layer 3 拡張のテスト:
  1. 固定割当: manager -> M / 都賀 staff -> 都賀 A
  2. 前日同コースペナルティ: 4 staff vs 5 コース (本店 A-E) で
     同一スタッフが連続曜日同コースを取らないことを確認
  3. 公平性: rotation できるスタッフ間で担当回数が大きく偏らない
  4. 4 staff vs 5 course の不均衡: 5 コースのうち 1 コース分が空 (assigned_staff_id NULL) になることを許容
"""

from __future__ import annotations

from collections import Counter
from datetime import date, time
from uuid import uuid4

import pytest

from app.models import Course, Office, Patient, Staff, StaffShift, Visit
from app.models.course import COURSE_STATUS_COURSE_FIXED
from app.models.visit import VISIT_STATUS_PLANNED
from app.services.scheduling.layer3_assignment import (
    CourseAssignmentTarget,
    Layer3Assigner,
    StaffInfo,
)

# ISO 週: 月曜 = 2026-05-25 (week 22)
W16_ISO_YEAR = 2026
W16_ISO_WEEK = 22
W16_WEEK_MONDAY = date(2026, 5, 25)


# ---------------------------------------------------------------------------
# Pure-function tests: solve() with W16 features
# ---------------------------------------------------------------------------


def _make_staff(
    name: str,
    role: str = "staff",
    work_days: frozenset[int] = frozenset(range(6)),
) -> StaffInfo:
    return StaffInfo(
        staff_id=uuid4(),
        name=name,
        sex=None,
        role=role,
        primary_office_lat=None,
        primary_office_lng=None,
        work_days=work_days,
    )


def _make_course(weekday: int, code: str) -> CourseAssignmentTarget:
    return CourseAssignmentTarget(
        course_id=uuid4(),
        weekday=weekday,
        course_code=code,
        centroid_lat=None,
        centroid_lng=None,
        gender_restrictions=frozenset(),
    )


def test_w16_fixed_manager_assignment_pinned_to_m_course() -> None:
    """role='manager' のスタッフが M コースに固定で割当てられる (W16)."""
    manager = _make_staff("M-川名", role="manager")
    s1 = _make_staff("S1-宇田川")
    s2 = _make_staff("S2-本名")

    # 月曜の A / B / M コース
    c_a = _make_course(weekday=0, code="A")
    c_b = _make_course(weekday=0, code="B")
    c_m = _make_course(weekday=0, code="M")

    fixed = {c_m.course_id: manager.staff_id}

    assigner = Layer3Assigner()
    result = assigner.solve(
        [c_a, c_b, c_m],
        [manager, s1, s2],
        fixed_staff_by_course=fixed,
    )

    # M コースには manager が割当される (manager 除外ロジックを bypass)
    m_assigns = [a for a in result.assignments if a.course_code == "M"]
    assert len(m_assigns) == 1
    assert m_assigns[0].staff_id == manager.staff_id

    # A / B には s1 / s2 のどちらかが割当される (manager は重複しない)
    non_m_assigns = [a for a in result.assignments if a.course_code != "M"]
    assert all(a.staff_id != manager.staff_id for a in non_m_assigns)


def test_w16_fixed_tsuga_staff_pinned_to_a_course() -> None:
    """都賀 staff (固定) が A コースに固定割当される (W16)."""
    tsuga = _make_staff("S0-関谷")
    s1 = _make_staff("S1-宇田川")
    s2 = _make_staff("S2-本名")

    c_a = _make_course(weekday=0, code="A")
    c_b = _make_course(weekday=0, code="B")

    fixed = {c_a.course_id: tsuga.staff_id}

    assigner = Layer3Assigner()
    result = assigner.solve(
        [c_a, c_b],
        [tsuga, s1, s2],
        fixed_staff_by_course=fixed,
    )

    a_assigns = [a for a in result.assignments if a.course_code == "A"]
    assert len(a_assigns) == 1
    assert a_assigns[0].staff_id == tsuga.staff_id


def test_w16_prev_day_same_course_avoided() -> None:
    """4 staff が 2 コース x 6 weekday を回るとき、連続曜日同コースを回避する (W16)."""
    s1 = _make_staff("S1")
    s2 = _make_staff("S2")
    s3 = _make_staff("S3")
    s4 = _make_staff("S4")

    courses: list[CourseAssignmentTarget] = []
    for wd in range(5):  # 月-金
        for code in ("A", "B"):
            courses.append(_make_course(weekday=wd, code=code))

    assigner = Layer3Assigner()
    result = assigner.solve(courses, [s1, s2, s3, s4])

    # weekday × course_code -> staff_id のマップを構築
    by_day_code: dict[tuple[int, str], object] = {}
    for a in result.assignments:
        by_day_code[(a.weekday, a.course_code)] = a.staff_id

    # 連続曜日で同 (course_code, staff_id) が繰り返される回数を数える
    consecutive_same = 0
    for wd in range(1, 5):
        for code in ("A", "B"):
            prev = by_day_code.get((wd - 1, code))
            cur = by_day_code.get((wd, code))
            if prev is not None and cur is not None and prev == cur:
                consecutive_same += 1
    # ペナルティが効いていれば連続発生は 0 (4 staff 2 course なので回避可能)
    assert consecutive_same == 0, (
        f"prev-day same-course penalty failed: {consecutive_same} consecutive assignments"
    )


def test_w16_fairness_4staff_5courses() -> None:
    """4 staff が 5 コース (本店 A-E) を 5 weekday 回す.

    各 staff の担当回数の差が大きく偏らないことを確認 (max - min <= 3).
    """
    s_pool = [_make_staff(f"S{i}") for i in range(1, 5)]  # S1..S4
    courses: list[CourseAssignmentTarget] = []
    for wd in range(5):  # 月-金 (5 weekday)
        for code in ("A", "B", "C", "D", "E"):
            courses.append(_make_course(weekday=wd, code=code))

    assigner = Layer3Assigner()
    result = assigner.solve(courses, s_pool)

    # 4 staff が 5 weekday * 5 course = 25 セルを担当
    # 1 staff 1 day 1 course なので 1 weekday あたり 4 staff -> 1 course 空くのを許容
    counts = Counter(a.staff_id for a in result.assignments)
    # 全 staff が少なくとも 1 度は割当てられる
    assert len(counts) == 4
    # 担当回数の差を確認
    if counts.values():
        diff = max(counts.values()) - min(counts.values())
        # 4 staff x 5 weekday で 1 staff = 5 day. ローテーションが回れば 5/staff.
        # 多少のブレは許容するが "1 人独占" は許さない
        assert diff <= 3, f"unfair distribution: counts={dict(counts)}"


def test_w16_4staff_5course_one_course_unfilled_per_day() -> None:
    """4 staff vs 5 コース で 1 weekday あたり 1 コース分が空になることを許容 (W16)."""
    s_pool = [_make_staff(f"S{i}") for i in range(1, 5)]  # 4 staff
    courses: list[CourseAssignmentTarget] = []
    for wd in range(5):
        for code in ("A", "B", "C", "D", "E"):
            courses.append(_make_course(weekday=wd, code=code))

    assigner = Layer3Assigner()
    result = assigner.solve(courses, s_pool)

    # 1 weekday あたりの割当数が <= staff 数 (4) であることを確認
    by_weekday: dict[int, int] = {}
    for a in result.assignments:
        by_weekday[a.weekday] = by_weekday.get(a.weekday, 0) + 1
    for wd, cnt in by_weekday.items():
        assert cnt <= 4, f"weekday {wd}: assignment count {cnt} exceeds staff 4"


def test_w16_no_regression_existing_behavior() -> None:
    """fixed_staff_by_course / prev_day ペナルティ なしで従来挙動と同等 (W16 後方互換)."""
    s1 = _make_staff("S1")
    s2 = _make_staff("S2")
    c_a = _make_course(weekday=0, code="A")
    c_b = _make_course(weekday=0, code="B")

    assigner = Layer3Assigner()
    result_old = assigner.solve([c_a, c_b], [s1, s2], same_course_prev_day_penalty=False)
    result_new = assigner.solve([c_a, c_b], [s1, s2])

    # 単一曜日 × 2 コース × 2 staff の場合、prev-day penalty は無関係 (履歴なし)
    assert len(result_old.assignments) == 2
    assert len(result_new.assignments) == 2


# ---------------------------------------------------------------------------
# DB-driven integration tests: assign() with W16 fixed assignments
# ---------------------------------------------------------------------------


async def _seed_w16_offices_and_staff(db) -> dict[str, object]:
    """W16 環境を seed する: 本店 (稲毛) / 都賀の 2 拠点 + manager + staff."""
    inage = Office(name="本店(稲毛)", lat=35.6383, lng=140.1041)
    tsuga = Office(name="都賀支店", lat=35.6500, lng=140.1700)
    db.add_all([inage, tsuga])
    await db.flush()

    # 川名 (本店 manager)
    kawana = Staff(
        code="MGR-001",
        name="川名 千恵",
        sex="female",
        role="manager",
        status="active",
        primary_office_id=inage.id,
    )
    # 関谷 (都賀 staff)
    sekiya = Staff(
        code="TSUGA-01",
        name="関谷 公佑",
        sex="male",
        role="staff",
        status="active",
        primary_office_id=tsuga.id,
    )
    # 本店 staff 4 名
    udagawa = Staff(
        code="HON-01",
        name="宇田川",
        sex="female",
        role="staff",
        status="active",
        primary_office_id=inage.id,
    )
    honna = Staff(
        code="HON-02",
        name="本名",
        sex="female",
        role="staff",
        status="active",
        primary_office_id=inage.id,
    )
    kumazawa = Staff(
        code="HON-03",
        name="熊澤",
        sex="female",
        role="staff",
        status="active",
        primary_office_id=inage.id,
    )
    takaoka = Staff(
        code="HON-04",
        name="高岡",
        sex="female",
        role="staff",
        status="active",
        primary_office_id=inage.id,
    )
    db.add_all([kawana, sekiya, udagawa, honna, kumazawa, takaoka])
    await db.flush()

    # 全員 月-金 (+ 土) 勤務
    for staff in [kawana, sekiya, udagawa, honna, kumazawa, takaoka]:
        for wd in range(6):
            db.add(StaffShift(staff_id=staff.id, weekday=wd, is_on=True))
    await db.flush()
    return {
        "inage": inage,
        "tsuga": tsuga,
        "kawana": kawana,
        "sekiya": sekiya,
        "honten_staff": [udagawa, honna, kumazawa, takaoka],
    }


async def _seed_w16_courses_for_week(db, *, inage_id, tsuga_id) -> list[Course]:
    """月曜 (weekday=0) に 本店 A,B,C,D,M / 都賀 A の 6 コースを seed (course_fixed)."""
    courses: list[Course] = []
    for code in ("A", "B", "C", "D", "M"):
        c = Course(
            iso_year=W16_ISO_YEAR,
            iso_week=W16_ISO_WEEK,
            weekday=0,
            code=code,
            course_status=COURSE_STATUS_COURSE_FIXED,
            office_id=inage_id,
        )
        db.add(c)
        courses.append(c)
    # 都賀 A
    tsuga_a = Course(
        iso_year=W16_ISO_YEAR,
        iso_week=W16_ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_COURSE_FIXED,
        office_id=tsuga_id,
    )
    db.add(tsuga_a)
    courses.append(tsuga_a)
    await db.flush()
    return courses


async def _seed_one_visit_per_course(db, *, courses, visit_date) -> None:
    """各コースに 1 患者 1 visit を seed (Layer 3 が空コースを skip しないように)."""
    for c in courses:
        patient = Patient(
            code=f"P-{c.code}-{c.id.hex[:8]}",
            name=f"P {c.code}",
            status="active",
        )
        db.add(patient)
        await db.flush()
        visit = Visit(
            patient_id=patient.id,
            visit_date=visit_date,
            start_time=time(9, 0),
            end_time=time(10, 0),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto",
            required_staff_count=1,
            course_id=c.id,
        )
        db.add(visit)
    await db.flush()


@pytest.mark.asyncio
async def test_w16_assign_pins_manager_to_m_course(db) -> None:
    """assign(): role=manager は M コースに固定で割当られる (W16)."""
    seeds = await _seed_w16_offices_and_staff(db)
    courses = await _seed_w16_courses_for_week(
        db, inage_id=seeds["inage"].id, tsuga_id=seeds["tsuga"].id
    )
    await _seed_one_visit_per_course(db, courses=courses, visit_date=W16_WEEK_MONDAY)
    await db.commit()

    assigner = Layer3Assigner()
    result = await assigner.assign(db, iso_year=W16_ISO_YEAR, iso_week=W16_ISO_WEEK)
    await db.commit()

    # M コースには 川名 が固定割当される
    m_course = next(c for c in courses if c.code == "M" and c.office_id == seeds["inage"].id)
    m_assignment = next((a for a in result.assignments if a.course_id == m_course.id), None)
    assert m_assignment is not None
    assert m_assignment.staff_id == seeds["kawana"].id


@pytest.mark.asyncio
async def test_w16_assign_pins_tsuga_staff_to_tsuga_a_course(db) -> None:
    """assign(): 都賀 staff が 都賀 A コースに固定で割当られる (W16)."""
    seeds = await _seed_w16_offices_and_staff(db)
    courses = await _seed_w16_courses_for_week(
        db, inage_id=seeds["inage"].id, tsuga_id=seeds["tsuga"].id
    )
    await _seed_one_visit_per_course(db, courses=courses, visit_date=W16_WEEK_MONDAY)
    await db.commit()

    assigner = Layer3Assigner()
    result = await assigner.assign(db, iso_year=W16_ISO_YEAR, iso_week=W16_ISO_WEEK)
    await db.commit()

    tsuga_a = next(c for c in courses if c.code == "A" and c.office_id == seeds["tsuga"].id)
    tsuga_a_assignment = next((a for a in result.assignments if a.course_id == tsuga_a.id), None)
    assert tsuga_a_assignment is not None
    assert tsuga_a_assignment.staff_id == seeds["sekiya"].id


@pytest.mark.asyncio
async def test_w16_assign_office_filter(db) -> None:
    """assign(office_id=...) で対象拠点のみ Layer 3 が動く (W16)."""
    seeds = await _seed_w16_offices_and_staff(db)
    courses = await _seed_w16_courses_for_week(
        db, inage_id=seeds["inage"].id, tsuga_id=seeds["tsuga"].id
    )
    await _seed_one_visit_per_course(db, courses=courses, visit_date=W16_WEEK_MONDAY)
    await db.commit()

    assigner = Layer3Assigner()
    result = await assigner.assign(
        db,
        iso_year=W16_ISO_YEAR,
        iso_week=W16_ISO_WEEK,
        office_id=seeds["inage"].id,
    )
    await db.commit()

    # 結果に 都賀 A のコースは含まれない
    tsuga_a = next(c for c in courses if c.code == "A" and c.office_id == seeds["tsuga"].id)
    assert all(a.course_id != tsuga_a.id for a in result.assignments)
