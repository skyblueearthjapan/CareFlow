"""W25: Layer 3 スタッフ重複割付バグ修正テスト.

Wave 25 で修正された 2 件の重複バグを検証する:
  1. Bug #1: manager が M コース (固定経路) + 別コース (ハンガリアン自由経路) に
             二重割付されないこと
  2. Bug #2: staff_assigned 状態のコースを保護するスタッフが、assign-staff-only
             再実行時に別コースにも重複割付されないこと
  3. assign-staff-only 再実行 (冪等性): 2 回実行しても重複が発生しないこと
"""

from __future__ import annotations

from datetime import date, time
from uuid import uuid4

import pytest

from app.models import Course, Office, Patient, Staff, StaffShift, Visit
from app.models.course import (
    COURSE_STATUS_COURSE_FIXED,
    COURSE_STATUS_STAFF_ASSIGNED,
)
from app.models.visit import VISIT_STATUS_PLANNED
from app.services.scheduling.layer3_assignment import (
    CourseAssignmentTarget,
    Layer3Assigner,
    StaffInfo,
)

W25_ISO_YEAR = 2026
W25_ISO_WEEK = 25
W25_WEEK_MONDAY = date(2026, 6, 15)  # 2026-W25 月曜


# ---------------------------------------------------------------------------
# helpers
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


# ---------------------------------------------------------------------------
# テスト 1: manager が M のみに割当てられ、他コースには割当てられないこと
# (Bug #1: manager が fixed 経路 + 自由 Hungarian に二重登場する問題)
# ---------------------------------------------------------------------------


def test_w25_manager_not_duplicated_to_non_m_course() -> None:
    """manager + 本店 staff 4 名のシナリオで manager が M のみに割当てられる.

    固定経路 (M -> manager) と自由マッチング (A,B,C,D,E -> non-manager staff)
    で manager が重複しないことを確認する。
    Bug #1 の再現条件: manager を fixed_staff_by_course に含め、
    かつ当日勤務しているシナリオ。
    """
    manager = _make_staff("川名-manager", role="manager")
    s1 = _make_staff("本名-S1")
    s2 = _make_staff("高岡-S2")
    s3 = _make_staff("宇田川-S3")
    s4 = _make_staff("熊澤-S4")

    # 月曜: A, B, C, D, E, M の 6 コース
    weekday = 0
    c_a = _make_course(weekday, "A")
    c_b = _make_course(weekday, "B")
    c_c = _make_course(weekday, "C")
    c_d = _make_course(weekday, "D")
    c_e = _make_course(weekday, "E")
    c_m = _make_course(weekday, "M")

    fixed = {c_m.course_id: manager.staff_id}

    assigner = Layer3Assigner()
    result = assigner.solve(
        [c_a, c_b, c_c, c_d, c_e, c_m],
        [manager, s1, s2, s3, s4],
        fixed_staff_by_course=fixed,
    )

    # manager は M コースにのみ割当てられる
    manager_assigns = [a for a in result.assignments if a.staff_id == manager.staff_id]
    assert len(manager_assigns) == 1, (
        f"manager が {len(manager_assigns)} コースに割当てられた: "
        f"{[a.course_code for a in manager_assigns]}"
    )
    assert manager_assigns[0].course_code == "M", (
        f"manager が M 以外のコースに割当てられた: {manager_assigns[0].course_code}"
    )

    # 1 曜日内でスタッフ重複なし
    staff_ids = [a.staff_id for a in result.assignments]
    assert len(staff_ids) == len(set(staff_ids)), f"同曜日内でスタッフ重複: {staff_ids}"


def test_w25_manager_not_duplicated_when_not_working_that_day() -> None:
    """manager が当日勤務しない曜日でも、他コースへの重複割付が起きないこと.

    Bug #1 の最も典型的な再現: manager が月曜のみ勤務し、火曜は非勤務。
    火曜に M コースが存在すると fixed 経路が skip されるが、
    manager が free_staff に残って E 等に割当てられてしまう問題を修正。
    """
    # manager は月曜のみ勤務
    manager = _make_staff("川名-manager", role="manager", work_days=frozenset([0]))
    s1 = _make_staff("本名-S1", work_days=frozenset(range(6)))
    s2 = _make_staff("高岡-S2", work_days=frozenset(range(6)))

    # 火曜 (weekday=1): M コース (manager は非勤務) + A, B コース
    weekday = 1
    c_a_tue = _make_course(weekday, "A")
    c_b_tue = _make_course(weekday, "B")
    c_m_tue = _make_course(weekday, "M")

    fixed = {c_m_tue.course_id: manager.staff_id}

    assigner = Layer3Assigner()
    result = assigner.solve(
        [c_a_tue, c_b_tue, c_m_tue],
        [manager, s1, s2],
        fixed_staff_by_course=fixed,
    )

    # manager は火曜非勤務なので M コースへの割当はなく、かつ A/B にも割当てられない
    manager_assigns = [a for a in result.assignments if a.staff_id == manager.staff_id]
    assert len(manager_assigns) == 0, (
        f"非勤務日の manager が割当てられた: "
        f"{[(a.weekday, a.course_code) for a in manager_assigns]}"
    )

    # A / B には s1 / s2 が割当てられる
    non_m_assigns = [a for a in result.assignments if a.course_code != "M"]
    assert len(non_m_assigns) == 2
    for a in non_m_assigns:
        assert a.staff_id in {s1.staff_id, s2.staff_id}, (
            f"manager が非勤務曜日に A/B コースに割当てられた: {a}"
        )


# ---------------------------------------------------------------------------
# テスト 2: staff_assigned コースの保護スタッフが別コースに重複しないこと
# (Bug #2: assign-staff-only 再実行時の重複防止)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_w25_staff_assigned_course_staff_excluded_from_free(db) -> None:
    """同曜日に既に staff_assigned のコース A があり、
    別コース B のハンガリアンで同スタッフが再度割当てられないこと.

    シナリオ:
      - 月曜: コース A が既に staff_assigned (本名 大 担当)
      - 月曜: コース B が course_fixed (新規割付対象)
      - assign-staff-only 実行時に 本名 大 が B にも割当てられないこと
    """
    office = Office(name="テスト事業所-W25", lat=35.64, lng=140.10)
    db.add(office)
    await db.flush()

    honna = Staff(
        code="W25-HONNA",
        name="本名 大",
        sex="female",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    takaoka = Staff(
        code="W25-TAKAOKA",
        name="高岡 真由美",
        sex="female",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    db.add_all([honna, takaoka])
    await db.flush()

    for staff in [honna, takaoka]:
        db.add(StaffShift(staff_id=staff.id, weekday=0, is_on=True))
    await db.flush()

    # コース A: 既に staff_assigned (本名 大 担当)
    course_a = Course(
        iso_year=W25_ISO_YEAR,
        iso_week=W25_ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=honna.id,
        office_id=office.id,
    )
    # コース B: 新規割付対象 (course_fixed)
    course_b = Course(
        iso_year=W25_ISO_YEAR,
        iso_week=W25_ISO_WEEK,
        weekday=0,
        code="B",
        course_status=COURSE_STATUS_COURSE_FIXED,
        office_id=office.id,
    )
    db.add_all([course_a, course_b])
    await db.flush()

    # 各コースに 1 患者 1 visit
    for course in [course_a, course_b]:
        patient = Patient(
            code=f"P-W25-{course.code}",
            name=f"W25 患者 {course.code}",
            status="active",
        )
        db.add(patient)
        await db.flush()
        visit = Visit(
            patient_id=patient.id,
            visit_date=W25_WEEK_MONDAY,
            start_time=time(9, 0),
            end_time=time(10, 0),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto",
            required_staff_count=1,
            course_id=course.id,
        )
        db.add(visit)
    await db.commit()

    assigner = Layer3Assigner()
    result = await assigner.assign(db, iso_year=W25_ISO_YEAR, iso_week=W25_ISO_WEEK)
    await db.commit()

    # 本名 大 (honna) が B コースに割当てられていない
    honna_assigns = [a for a in result.assignments if a.staff_id == honna.id]
    # honna はコース A を保護されており、B には割当てられないべき
    b_assigns = [a for a in honna_assigns if a.course_code == "B"]
    assert len(b_assigns) == 0, (
        f"staff_assigned 保護スタッフが別コースに重複割付された: {b_assigns}"
    )

    # 1 曜日内でスタッフ重複なし
    all_assigns_for_day = [a for a in result.assignments if a.weekday == 0]
    staff_ids = [a.staff_id for a in all_assigns_for_day]
    assert len(staff_ids) == len(set(staff_ids)), f"同曜日内でスタッフ重複: {staff_ids}"


# ---------------------------------------------------------------------------
# テスト 3: assign-staff-only 再実行で重複が起きないこと (冪等性)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_w25_rerun_assign_no_duplicate(db) -> None:
    """assign-staff-only を 2 回実行しても重複割付が発生しないこと (冪等性).

    1 回目: course_fixed コースに割付 → staff_assigned に遷移
    2 回目: 同じ週で再実行 → 既に staff_assigned なコースは保護され、
            同スタッフが別の course_fixed コースにも割当てられない。
    """
    office = Office(name="テスト事業所-W25-rerun", lat=35.65, lng=140.11)
    db.add(office)
    await db.flush()

    s1 = Staff(
        code="W25R-S1",
        name="スタッフ A",
        sex="female",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    s2 = Staff(
        code="W25R-S2",
        name="スタッフ B",
        sex="female",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    db.add_all([s1, s2])
    await db.flush()

    for staff in [s1, s2]:
        db.add(StaffShift(staff_id=staff.id, weekday=0, is_on=True))
    await db.flush()

    # 月曜: コース A, B (両方 course_fixed)
    course_a = Course(
        iso_year=W25_ISO_YEAR,
        iso_week=W25_ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_COURSE_FIXED,
        office_id=office.id,
    )
    course_b = Course(
        iso_year=W25_ISO_YEAR,
        iso_week=W25_ISO_WEEK,
        weekday=0,
        code="B",
        course_status=COURSE_STATUS_COURSE_FIXED,
        office_id=office.id,
    )
    db.add_all([course_a, course_b])
    await db.flush()

    for course in [course_a, course_b]:
        patient = Patient(
            code=f"P-W25R-{course.code}",
            name=f"W25R 患者 {course.code}",
            status="active",
        )
        db.add(patient)
        await db.flush()
        visit = Visit(
            patient_id=patient.id,
            visit_date=W25_WEEK_MONDAY,
            start_time=time(9, 0),
            end_time=time(10, 0),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto",
            required_staff_count=1,
            course_id=course.id,
        )
        db.add(visit)
    await db.commit()

    assigner = Layer3Assigner()

    # 1 回目の実行
    result1 = await assigner.assign(db, iso_year=W25_ISO_YEAR, iso_week=W25_ISO_WEEK)
    await db.commit()

    # 1 回目: 2 コースに割付
    assert len(result1.assignments) == 2, f"1 回目: 割付件数が 2 でない: {len(result1.assignments)}"
    staff_ids_1 = [a.staff_id for a in result1.assignments]
    assert len(set(staff_ids_1)) == 2, f"1 回目: 重複割付が発生した: {staff_ids_1}"

    # 2 回目の実行 (再実行)
    result2 = await assigner.assign(db, iso_year=W25_ISO_YEAR, iso_week=W25_ISO_WEEK)
    await db.commit()

    # 2 回目: 既に staff_assigned なコースは保護され、新規割付は 0 件
    # (course_fixed が 0 件のため、新規割付対象なし)
    # 重複が発生していないことを確認 (weekday=0 の割付は最大 2 件)
    all_assigns_day0 = [a for a in result2.assignments if a.weekday == 0]
    staff_ids_2 = [a.staff_id for a in all_assigns_day0]
    assert len(staff_ids_2) == len(set(staff_ids_2)), (
        f"2 回目の再実行で重複割付が発生した: {staff_ids_2}"
    )
