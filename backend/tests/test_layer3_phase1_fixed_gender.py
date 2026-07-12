"""Phase 1: 固定割当ルートの性別ハード制約穴塞ぎテスト.

背景: 通常ルート (``_cost_single_cell`` = Stage 1/2/3 共通のハンガリアン) は
性別ハード制約が効くが、 固定割当ルート
(``_solve_one_day`` の fixed 処理 / ``_build_fixed_assignments``) は性別未チェック
だった (= 患者安全の穴). 本テストは:
  1. ``_staff_satisfies_gender`` / ``_sex_satisfies_restrictions`` ヘルパーの単一ソース化
  2. ``_solve_one_day``: 性別違反の固定割当が確定されず free に回ること
  3. ``_solve_one_day``: 違反でドロップした固定スタッフが別コースで再利用できること
  4. ``_build_fixed_assignments`` (E2E): manager→M / 都賀→A が性別違反なら貼られないこと
を検証する.
"""

from __future__ import annotations

from datetime import date, time
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import Course, Office, Patient, Staff, StaffShift, Visit
from app.models.course import COURSE_STATUS_COURSE_FIXED
from app.models.visit import VISIT_STATUS_PLANNED
from app.services.scheduling.layer3_assignment import (
    CourseAssignmentTarget,
    Layer3Assigner,
    StaffInfo,
    _sex_satisfies_restrictions,
    _staff_satisfies_gender,
)

# fixture 用の ISO 週. 月曜 = 2026-05-25 (week 22).
TEST_ISO_YEAR = 2026
TEST_ISO_WEEK = 22
TEST_WEEK_MONDAY = date(2026, 5, 25)


def _make_course(
    *,
    code: str = "A",
    weekday: int = 0,
    restrictions: frozenset[str] = frozenset(),
    patient_ids: list | None = None,
) -> CourseAssignmentTarget:
    return CourseAssignmentTarget(
        course_id=uuid4(),
        weekday=weekday,
        course_code=code,
        centroid_lat=None,
        centroid_lng=None,
        gender_restrictions=restrictions,
        patient_ids=patient_ids if patient_ids is not None else [uuid4()],
        visits=[],
    )


def _make_staff(
    *,
    role: str = "staff",
    sex: str | None = "female",
    work_days: frozenset[int] = frozenset(range(7)),
    name: str = "p1-staff",
) -> StaffInfo:
    return StaffInfo(
        staff_id=uuid4(),
        name=name,
        sex=sex,
        role=role,
        primary_office_lat=None,
        primary_office_lng=None,
        work_days=work_days,
    )


# ---------------------------------------------------------------------------
# 1) ヘルパーの単一ソース化 (AND semantics)
# ---------------------------------------------------------------------------


def test_sex_satisfies_restrictions_semantics() -> None:
    """``_sex_satisfies_restrictions`` の AND セマンティクスを検証."""
    # 空制限 → 常に True (sex None でも)
    assert _sex_satisfies_restrictions(None, frozenset()) is True
    assert _sex_satisfies_restrictions("male", frozenset()) is True
    # 非空 + sex None → False
    assert _sex_satisfies_restrictions(None, frozenset({"female_only"})) is False
    # 正規化込みの一致 / 不一致
    assert _sex_satisfies_restrictions("female", frozenset({"female_only"})) is True
    assert _sex_satisfies_restrictions("male", frozenset({"female_only"})) is False
    assert _sex_satisfies_restrictions("male", frozenset({"male_only"})) is True
    # bare 値 (_only suffix 無し) も fall-through で一致
    assert _sex_satisfies_restrictions("female", frozenset({"female"})) is True
    # 複合制限 (female_only + male_only) は誰も満たせない (AND)
    both = frozenset({"female_only", "male_only"})
    assert _sex_satisfies_restrictions("female", both) is False
    assert _sex_satisfies_restrictions("male", both) is False


def test_staff_satisfies_gender_delegates() -> None:
    """``_staff_satisfies_gender`` が staff/course から正しく委譲する."""
    course_f = _make_course(restrictions=frozenset({"female_only"}))
    assert _staff_satisfies_gender(_make_staff(sex="female"), course_f) is True
    assert _staff_satisfies_gender(_make_staff(sex="male"), course_f) is False
    assert _staff_satisfies_gender(_make_staff(sex=None), course_f) is False
    course_none = _make_course(restrictions=frozenset())
    assert _staff_satisfies_gender(_make_staff(sex=None), course_none) is True


# ---------------------------------------------------------------------------
# 2) _solve_one_day: 性別違反の固定割当は確定されず正しい性別へ回る
# ---------------------------------------------------------------------------


def test_fixed_assignment_gender_violation_routed_to_correct_staff() -> None:
    """Mコース (女性のみ患者) × 男性 manager 固定 → manager は固定されず女性へ.

    固定割当ルートが性別未チェックだった穴を塞いだことの直接検証.
    fixed_staff_by_course で男性 manager を M コースに固定指定しても、 その
    コースが female_only なら manager は確定されず、 通常ハンガリアンで女性へ割当.
    """
    male_manager = _make_staff(role="manager", sex="male", name="male-mgr")
    female_staff = _make_staff(role="staff", sex="female", name="female-staff")

    m_course = _make_course(
        code="M",
        weekday=0,
        restrictions=frozenset({"female_only"}),
        patient_ids=[uuid4()],
    )

    assigner = Layer3Assigner()
    result = assigner.solve(
        [m_course],
        [male_manager, female_staff],
        fixed_staff_by_course={m_course.course_id: male_manager.staff_id},
    )

    a = next((x for x in result.assignments if x.course_id == m_course.course_id), None)
    assert a is not None, "Mコースが未割当 (女性が居るのに埋まっていない)"
    # 男性 manager は固定されない. 女性 staff が選ばれる.
    assert a.staff_id == female_staff.staff_id, (
        f"female_only コースに male manager が固定された (性別穴): got={a.staff_id}"
    )


def test_fixed_assignment_gender_violation_unassigned_when_no_valid_staff() -> None:
    """適合性別が居ない場合、 違反固定は確定されず未割当のまま (誤割当しない)."""
    male_manager = _make_staff(role="manager", sex="male", name="male-mgr")
    male_staff = _make_staff(role="staff", sex="male", name="male-staff")

    m_course = _make_course(
        code="M",
        weekday=0,
        restrictions=frozenset({"female_only"}),
        patient_ids=[uuid4()],
    )

    assigner = Layer3Assigner()
    result = assigner.solve(
        [m_course],
        [male_manager, male_staff],
        fixed_staff_by_course={m_course.course_id: male_manager.staff_id},
    )
    # 女性が居ないので未割当 (男性が誤って固定されてはならない)
    assert all(a.course_id != m_course.course_id for a in result.assignments), (
        "female_only コースに男性が割当された (固定 or fallback の性別穴)"
    )


def test_dropped_fixed_staff_reusable_for_other_course() -> None:
    """性別違反でドロップした固定スタッフは、 同曜日の別コースで再利用できる.

    旧実装は ``fixed_staff_by_course`` の全 values を free から除外していたため、
    違反でドロップした固定スタッフが別コースで使えず不要な未割当を生んでいた.
    Phase 1 修正: 有効確定した固定のみ除外するため、 ドロップ分は再利用可能.
    """
    male_manager = _make_staff(role="manager", sex="male", name="male-mgr")
    female_staff = _make_staff(role="staff", sex="female", name="female-staff")

    # M (female_only): 男性 manager 固定だが違反 → manager はドロップ
    m_course = _make_course(
        code="M", weekday=0, restrictions=frozenset({"female_only"}), patient_ids=[uuid4()]
    )
    # B (制限なし): manager がここで再利用されることを期待
    b_course = _make_course(code="B", weekday=0, restrictions=frozenset(), patient_ids=[uuid4()])

    assigner = Layer3Assigner()
    result = assigner.solve(
        [m_course, b_course],
        [male_manager, female_staff],
        fixed_staff_by_course={m_course.course_id: male_manager.staff_id},
    )

    by_course = {a.course_id: a.staff_id for a in result.assignments}
    # M は女性へ
    assert by_course.get(m_course.course_id) == female_staff.staff_id
    # B は manager が再利用される (= ドロップ固定スタッフが free に残っている)
    assert by_course.get(b_course.course_id) == male_manager.staff_id, (
        "ドロップした固定 manager が別コース B で再利用されていない"
    )


def test_valid_fixed_assignment_still_works() -> None:
    """regression: 性別適合の固定割当はこれまで通り確定される."""
    female_staff = _make_staff(role="staff", sex="female", name="female-fixed")
    other = _make_staff(role="staff", sex="female", name="other")

    a_course = _make_course(
        code="A", weekday=0, restrictions=frozenset({"female_only"}), patient_ids=[uuid4()]
    )
    assigner = Layer3Assigner()
    result = assigner.solve(
        [a_course],
        [female_staff, other],
        fixed_staff_by_course={a_course.course_id: female_staff.staff_id},
    )
    a = next(x for x in result.assignments if x.course_id == a_course.course_id)
    assert a.staff_id == female_staff.staff_id, "性別適合の固定割当が確定されていない"


# ---------------------------------------------------------------------------
# 3) _build_fixed_assignments E2E: manager→M / 都賀→A の性別ガード
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_fixed_skips_gender_violating_manager(db) -> None:
    """E2E: 男性 manager + female_only な M コース → 固定されず女性 staff へ割当."""
    office = Office(name="P1 稲毛事業所", lat=35.6383, lng=140.1041)
    db.add(office)
    await db.flush()

    male_manager = Staff(
        code="P1-MGR",
        name="P1 male manager",
        sex="male",
        role="manager",
        status="active",
        primary_office_id=office.id,
    )
    female_staff = Staff(
        code="P1-FS",
        name="P1 female staff",
        sex="female",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    db.add_all([male_manager, female_staff])
    await db.flush()
    for wd in range(7):
        db.add(StaffShift(staff_id=male_manager.id, weekday=wd, is_on=True))
        db.add(StaffShift(staff_id=female_staff.id, weekday=wd, is_on=True))

    # M コース (= manager 固定対象) を female_only 患者付きで作る
    m_course = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=0,
        code="M",
        course_status=COURSE_STATUS_COURSE_FIXED,
        office_id=office.id,
    )
    db.add(m_course)
    await db.flush()

    patient = Patient(
        code="P1-FP",
        name="P1 female_only patient",
        status="active",
        sex_restriction="female_only",
        lat=35.6383,
        lng=140.1041,
    )
    db.add(patient)
    await db.flush()
    db.add(
        Visit(
            patient_id=patient.id,
            course_id=m_course.id,
            visit_date=TEST_WEEK_MONDAY,
            start_time=time(9, 0),
            end_time=time(9, 30),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto",
            required_staff_count=1,
        )
    )
    await db.commit()

    assigner = Layer3Assigner()
    # _build_fixed_assignments 単体: 男性 manager は M コースに固定されない
    fixed = await assigner._build_fixed_assignments(
        db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK, office_id=office.id
    )
    assert fixed.get(m_course.id) != male_manager.id, (
        "female_only な M コースに男性 manager が固定された (性別穴)"
    )

    # E2E assign: 女性 staff が割当され、 男性 manager は選ばれない
    await assigner.assign(db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK, office_id=office.id)
    await db.commit()
    refreshed = (await db.scalars(select(Course).where(Course.id == m_course.id))).one()
    assert refreshed.assigned_staff_id == female_staff.id, (
        f"female_only M コースが女性へ割当されていない: got={refreshed.assigned_staff_id}"
    )
    assert refreshed.assigned_staff_id != male_manager.id


@pytest.mark.asyncio
async def test_build_fixed_skips_gender_violating_tsuga_staff(db) -> None:
    """E2E: 都賀拠点の男性 primary staff + female_only な A コース → 固定されない."""
    tsuga = Office(name="都賀事業所", lat=35.6500, lng=140.1700)
    db.add(tsuga)
    await db.flush()

    # 都賀の primary (code 昇順先頭) が男性. 別途女性 staff も用意.
    male_primary = Staff(
        code="P1-T-A",  # code 昇順で先頭 → primary_staff になる
        name="P1 tsuga male primary",
        sex="male",
        role="staff",
        status="active",
        primary_office_id=tsuga.id,
    )
    female_staff = Staff(
        code="P1-T-B",
        name="P1 tsuga female",
        sex="female",
        role="staff",
        status="active",
        primary_office_id=tsuga.id,
    )
    db.add_all([male_primary, female_staff])
    await db.flush()
    for wd in range(7):
        db.add(StaffShift(staff_id=male_primary.id, weekday=wd, is_on=True))
        db.add(StaffShift(staff_id=female_staff.id, weekday=wd, is_on=True))

    a_course = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_COURSE_FIXED,
        office_id=tsuga.id,
    )
    db.add(a_course)
    await db.flush()

    patient = Patient(
        code="P1-TFP",
        name="P1 tsuga female_only patient",
        status="active",
        sex_restriction="female_only",
        lat=35.6500,
        lng=140.1700,
    )
    db.add(patient)
    await db.flush()
    db.add(
        Visit(
            patient_id=patient.id,
            course_id=a_course.id,
            visit_date=TEST_WEEK_MONDAY,
            start_time=time(9, 0),
            end_time=time(9, 30),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto",
            required_staff_count=1,
        )
    )
    await db.commit()

    assigner = Layer3Assigner()
    fixed = await assigner._build_fixed_assignments(
        db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK, office_id=tsuga.id
    )
    assert fixed.get(a_course.id) != male_primary.id, (
        "female_only な 都賀 A コースに男性 primary staff が固定された (性別穴)"
    )

    await assigner.assign(db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK, office_id=tsuga.id)
    await db.commit()
    refreshed = (await db.scalars(select(Course).where(Course.id == a_course.id))).one()
    assert refreshed.assigned_staff_id == female_staff.id, (
        f"female_only 都賀 A コースが女性へ割当されていない: got={refreshed.assigned_staff_id}"
    )
    assert refreshed.assigned_staff_id != male_primary.id
