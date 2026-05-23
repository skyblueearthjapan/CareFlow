"""Phase G-29: Layer 3 manager fallback (2nd pass) テスト.

User 要望: 「割り当ての人がいなかった場合に manager を配置する。 最初からの
割り当てロジックの中に manager を混ぜない」。 1st pass (Hungarian, manager
除外) で NULL のまま残ったコースに対し、 work_days / 性別 / event 重複 /
1 day 1 course の制約を満たす manager を greedy で配置する 2nd pass を検証する。
"""

from __future__ import annotations

from datetime import date, datetime, time
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import Course, Office, Patient, Staff, StaffShift, Visit
from app.models.course import COURSE_STATUS_COURSE_FIXED
from app.models.staff import StaffEvent
from app.models.visit import VISIT_STATUS_PLANNED
from app.services.scheduling.layer3_assignment import (
    CourseAssignmentTarget,
    Layer3Assigner,
    StaffInfo,
    VisitTimeSlot,
)

# fixture 用の ISO 週. 月曜 = 2026-05-25 (week 22).
TEST_ISO_YEAR = 2026
TEST_ISO_WEEK = 22
TEST_WEEK_MONDAY = date(2026, 5, 25)


# ---------------------------------------------------------------------------
# helpers (E2E 用)
# ---------------------------------------------------------------------------


async def _seed_visit_for_course(
    db,
    *,
    course_id,
    patient_code: str,
    weekday: int,
    start_hour: int = 9,
    sex_restriction: str | None = None,
) -> Patient:
    """course に紐づく Patient + Visit を 1 件作成する."""
    patient = Patient(
        code=patient_code,
        name=f"phase-g29-{patient_code}",
        status="active",
        sex_restriction=sex_restriction,
        lat=35.6383,
        lng=140.1041,
    )
    db.add(patient)
    await db.flush()
    visit_date = date.fromordinal(TEST_WEEK_MONDAY.toordinal() + weekday)
    db.add(
        Visit(
            patient_id=patient.id,
            course_id=course_id,
            visit_date=visit_date,
            start_time=time(start_hour, 0),
            end_time=time(start_hour, 30),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto",
            required_staff_count=1,
        )
    )
    return patient


# ---------------------------------------------------------------------------
# pure unit helpers (solve() 直接呼び出し用)
# ---------------------------------------------------------------------------


def _make_course(
    *,
    code: str = "A",
    weekday: int = 0,
    centroid_lat: float = 35.6383,
    centroid_lng: float = 140.1041,
    restrictions: frozenset[str] = frozenset(),
    visits: list[VisitTimeSlot] | None = None,
    patient_ids: list | None = None,
) -> CourseAssignmentTarget:
    return CourseAssignmentTarget(
        course_id=uuid4(),
        weekday=weekday,
        course_code=code,
        centroid_lat=centroid_lat,
        centroid_lng=centroid_lng,
        gender_restrictions=restrictions,
        patient_ids=patient_ids or [uuid4()],
        visits=visits if visits is not None else [VisitTimeSlot(time(9, 0), time(9, 30))],
    )


def _make_staff(
    *,
    role: str = "staff",
    sex: str | None = "female",
    work_days: frozenset[int] = frozenset(range(7)),
    primary_lat: float = 35.6383,
    primary_lng: float = 140.1041,
    name: str = "g29-staff",
) -> StaffInfo:
    return StaffInfo(
        staff_id=uuid4(),
        name=name,
        sex=sex,
        role=role,
        primary_office_lat=primary_lat,
        primary_office_lng=primary_lng,
        work_days=work_days,
        is_trainee=False,
    )


# ---------------------------------------------------------------------------
# 1) Fallback で NULL が埋まる (核心ケース)
# ---------------------------------------------------------------------------


def test_manager_fallback_assigns_when_no_staff_available() -> None:
    """staff 1 名 + manager 1 名で月曜に 2 コース → 1st pass で 1 件のみ、
    2nd pass で manager が NULL コースに fallback 配置される.
    """
    assigner = Layer3Assigner()
    course_a = _make_course(code="A", weekday=0)
    course_b = _make_course(code="B", weekday=0)
    staff = _make_staff(name="g29-1-staff")
    manager = _make_staff(role="manager", name="g29-1-manager")

    result = assigner.solve(
        [course_a, course_b],
        [staff, manager],
    )

    assert len(result.assignments) == 2, (
        f"2 コース全てに割当されていない (2nd pass fallback 不発): assignments={result.assignments}"
    )
    staff_ids = {a.staff_id for a in result.assignments}
    assert staff.staff_id in staff_ids, (
        "通常 staff が 1st pass で割当されていない (regression): "
        f"staff_ids={staff_ids}, expected_includes={staff.staff_id}"
    )
    assert manager.staff_id in staff_ids, (
        "manager が 2nd pass fallback で割当されていない (Phase G-29 fix 不発): "
        f"staff_ids={staff_ids}, expected_includes={manager.staff_id}"
    )


# ---------------------------------------------------------------------------
# 2) 性別制限 — fallback でも対象外
# ---------------------------------------------------------------------------


def test_manager_fallback_respects_gender_restriction() -> None:
    """female_only コースに male manager しか居ない → 2nd pass でも対象外、 NULL のまま."""
    assigner = Layer3Assigner()
    course = _make_course(code="A", weekday=0, restrictions=frozenset({"female_only"}))
    male_manager = _make_staff(role="manager", sex="male", name="g29-2-male-manager")

    result = assigner.solve(
        [course],
        [male_manager],
    )

    assert len(result.assignments) == 0, (
        f"female_only コースに male manager が fallback で割当されている "
        f"(性別ハード制約が 2nd pass で効いていない): assignments={result.assignments}"
    )


# ---------------------------------------------------------------------------
# 3) 当日勤務でない manager は対象外
# ---------------------------------------------------------------------------


def test_manager_fallback_respects_work_day() -> None:
    """月曜の NULL コースに対し、 月曜出勤しない manager (work_days={1..6}) は対象外."""
    assigner = Layer3Assigner()
    course = _make_course(code="A", weekday=0)
    # 月曜以外勤務の manager
    manager = _make_staff(
        role="manager",
        work_days=frozenset({1, 2, 3, 4, 5, 6}),
        name="g29-3-non-mon-manager",
    )

    result = assigner.solve(
        [course],
        [manager],
    )

    assert len(result.assignments) == 0, (
        f"月曜出勤しない manager が月曜の NULL コースに割当されている "
        f"(work_days ハード制約が 2nd pass で効いていない): "
        f"assignments={result.assignments}"
    )


# ---------------------------------------------------------------------------
# 4) 1 day 1 course 制約 — M コース固定担当の manager は同曜日の他コース対象外
# ---------------------------------------------------------------------------


def test_manager_fallback_one_day_one_course() -> None:
    """manager が M コース固定担当 (fixed_staff_by_course) の日 → 同曜日の他 NULL
    コースには配置されない (1 staff 1 day 1 course 制約を manager にも適用).
    """
    assigner = Layer3Assigner()
    course_m = _make_course(code="M", weekday=0)
    course_a = _make_course(code="A", weekday=0)
    manager = _make_staff(role="manager", name="g29-4-manager")
    # 通常 staff 不在 → A は本来 NULL になるが、 manager は M に固定済なので
    # 2nd pass の対象外 → A は NULL のまま.

    result = assigner.solve(
        [course_m, course_a],
        [manager],
        fixed_staff_by_course={course_m.course_id: manager.staff_id},
    )

    # M コースには manager が固定割当
    m_assign = [a for a in result.assignments if a.course_id == course_m.course_id]
    assert len(m_assign) == 1 and m_assign[0].staff_id == manager.staff_id, (
        f"M コースに固定 manager が割当されていない (regression): assignments={result.assignments}"
    )
    # A コースは NULL のまま (manager は同日他コース対象外)
    a_assign = [a for a in result.assignments if a.course_id == course_a.course_id]
    assert len(a_assign) == 0, (
        f"M 固定担当の manager が同曜日の他 NULL コースにも配置されている "
        f"(1 day 1 course 制約違反): assignments={result.assignments}"
    )


# ---------------------------------------------------------------------------
# 5) event 重複あり manager は対象外
# ---------------------------------------------------------------------------


def test_manager_fallback_event_overlap_excluded() -> None:
    """manager に当該時間帯の StaffEvent (有給/外出) がある → 2nd pass でも対象外."""
    assigner = Layer3Assigner()
    # 月曜 9:00-9:30 visit のコース
    visit_slots = [VisitTimeSlot(time(9, 0), time(9, 30))]
    course = _make_course(code="A", weekday=0, visits=visit_slots)
    manager = _make_staff(role="manager", name="g29-5-manager")

    # manager に 9:00-12:00 の event を設定 (= visit 9:00-9:30 と完全重複)
    target_date = TEST_WEEK_MONDAY  # 月曜
    event = StaffEvent(
        staff_id=manager.staff_id,
        starts_at=datetime.combine(target_date, time(9, 0)),
        ends_at=datetime.combine(target_date, time(12, 0)),
        event_type="leave",  # 有給扱い
    )
    events_by_staff = {manager.staff_id: [event]}

    result = assigner.solve(
        [course],
        [manager],
        events_by_staff=events_by_staff,
        week_monday=TEST_WEEK_MONDAY,
    )

    assert len(result.assignments) == 0, (
        f"event 時間帯重複ありの manager が NULL コースに fallback 配置されている "
        f"(event ハード除外が 2nd pass で効いていない): assignments={result.assignments}"
    )


# ---------------------------------------------------------------------------
# 6) regression — 通常 staff で全 NULL 無くフィットすれば 2nd pass は実行されない
# ---------------------------------------------------------------------------


def test_manager_fallback_does_not_affect_1st_pass() -> None:
    """通常 staff 2 名 + 2 コース (visits>0) → 1st pass で全コース割当、
    manager は M 固定枠以外で 2nd pass に登場しない (regression).
    """
    assigner = Layer3Assigner()
    course_a = _make_course(code="A", weekday=0)
    course_b = _make_course(code="B", weekday=0)
    staff1 = _make_staff(name="g29-6-staff1")
    staff2 = _make_staff(name="g29-6-staff2")
    manager = _make_staff(role="manager", name="g29-6-manager")

    result = assigner.solve(
        [course_a, course_b],
        [staff1, staff2, manager],
    )

    # 2 コースとも staff (= manager 以外) で埋まる
    assigned_staff_ids = {a.staff_id for a in result.assignments}
    assert len(result.assignments) == 2, (
        f"通常 staff 2 名 + 2 コースで全件埋まっていない: assignments={result.assignments}"
    )
    assert manager.staff_id not in assigned_staff_ids, (
        f"M 固定枠なしの manager が 2nd pass で登場している "
        f"(1st pass で埋まったのに不要な fallback): "
        f"assignments={result.assignments}"
    )
    assert assigned_staff_ids == {staff1.staff_id, staff2.staff_id}, (
        f"通常 staff 2 名が 1st pass で全コースに割当されていない (regression): "
        f"assigned_staff_ids={assigned_staff_ids}"
    )


# ---------------------------------------------------------------------------
# 7) HIGH-1 regression — compound gender_restrictions (AND semantics)
# ---------------------------------------------------------------------------


def test_manager_fallback_respects_compound_gender_restriction() -> None:
    """course.gender_restrictions に female_only と male_only が両方含まれる
    稀ケース (= 同コース内に男女限定患者が同居) → 全 staff/manager が hard 制約
    違反となり、 2nd pass でも誰も配置されない (AND semantics).

    Phase G-29 reviewer 指摘 HIGH-1 の regression test:
        修正前の OR semantics (= ``mgr.sex in normalized_restrictions``) なら
        female manager が 「female_only に該当するから OK」 として誤割当され、
        この test は fail する (= 1 件割当される).
        修正後の AND semantics (= 全制約と mgr.sex 一致を要求) なら配置されず、
        NULL のまま → assertion を通過する.
    """
    assigner = Layer3Assigner()
    course = _make_course(
        code="A",
        weekday=0,
        restrictions=frozenset({"female_only", "male_only"}),
    )
    female_manager = _make_staff(role="manager", sex="female", name="g29-8-female-manager")

    result = assigner.solve(
        [course],
        [female_manager],
    )

    assert len(result.assignments) == 0, (
        f"compound (female_only + male_only) コースに female manager が "
        f"fallback で誤割当されている (HIGH-1: OR semantics 残存。 "
        f"AND semantics への統一が効いていない): assignments={result.assignments}"
    )


# ---------------------------------------------------------------------------
# 8) E2E — 月曜稲毛 (W21 本番再現): staff 4 名 + manager 1 名 + 5 コース (A-E)
#    1st pass で 4 件、 2nd pass で manager が 1 件 fallback して 5/5 OK
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manager_fallback_e2e_with_real_W21_scenario(db) -> None:  # noqa: N802
    """W21 本番再現: 月曜稲毛に 5 コース (A,B,C,D,E, visits>0) + 稲毛 staff 4 名
    + 稲毛 manager 1 名 で assign 実行. 1st pass で 4 件 (staff)、 2nd pass で
    manager が 1 件 fallback → 5 コース全て割当される.
    """
    inage = Office(name="G29-7 稲毛事業所", lat=35.6383, lng=140.1041)
    db.add(inage)
    await db.flush()

    # staff 4 名 (= 通常 staff)
    staffs = []
    for i in range(4):
        s = Staff(
            code=f"G29-7-S{i + 1}",
            name=f"G29-7 staff {i + 1}",
            sex="female",
            role="staff",
            status="active",
            primary_office_id=inage.id,
        )
        db.add(s)
        staffs.append(s)

    # manager 1 名
    manager = Staff(
        code="G29-7-M1",
        name="G29-7 manager 川名",
        sex="female",
        role="manager",
        status="active",
        primary_office_id=inage.id,
    )
    db.add(manager)
    await db.flush()

    # 全員 月曜出勤 ON (= 月曜だけテスト)
    for s in [*staffs, manager]:
        db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True))

    # コース A-E (月曜 = weekday=0) + 各 1 visit
    courses = []
    for code in ["A", "B", "C", "D", "E"]:
        c = Course(
            iso_year=TEST_ISO_YEAR,
            iso_week=TEST_ISO_WEEK,
            weekday=0,
            code=code,
            course_status=COURSE_STATUS_COURSE_FIXED,
            office_id=inage.id,
        )
        db.add(c)
        courses.append(c)
    await db.flush()

    for i, c in enumerate(courses):
        await _seed_visit_for_course(
            db,
            course_id=c.id,
            patient_code=f"G29-7-P-{c.code}",
            weekday=0,
            start_hour=9 + i,  # 9:00, 10:00, ..., 13:00 (時間帯ずらして event 重複なし)
        )
    await db.commit()

    assigner = Layer3Assigner()
    await assigner.assign(db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK)
    await db.commit()

    refreshed = list(
        (
            await db.scalars(
                select(Course).where(
                    Course.iso_year == TEST_ISO_YEAR,
                    Course.iso_week == TEST_ISO_WEEK,
                )
            )
        ).all()
    )
    assert len(refreshed) == 5, f"課題 5 コースが見つからない: {refreshed}"

    # 全コースに staff が割当されている (= NULL 0 件)
    null_courses = [c for c in refreshed if c.assigned_staff_id is None]
    assert len(null_courses) == 0, (
        f"W21 再現: 5 コース中 NULL が残っている (Phase G-29 fallback 不発): "
        f"null_courses={[(c.code, c.id) for c in null_courses]}"
    )

    # 内 4 件は通常 staff、 1 件は manager
    assigned_staff_ids = {c.assigned_staff_id for c in refreshed}
    staff_id_set = {s.id for s in staffs}
    assert manager.id in assigned_staff_ids, (
        f"W21 再現: manager が 2nd pass fallback で割当されていない: "
        f"assigned_staff_ids={assigned_staff_ids}, manager.id={manager.id}"
    )
    manager_assigned = [c for c in refreshed if c.assigned_staff_id == manager.id]
    assert len(manager_assigned) == 1, (
        f"manager が複数コースに割当されている (1 day 1 course 違反): "
        f"manager_assigned={[(c.code, c.id) for c in manager_assigned]}"
    )
    staff_assigned = [c for c in refreshed if c.assigned_staff_id in staff_id_set]
    assert len(staff_assigned) == 4, (
        f"通常 staff 4 名が 1st pass で 4 コースに割当されていない (regression): "
        f"staff_assigned={[(c.code, c.assigned_staff_id) for c in staff_assigned]}"
    )
