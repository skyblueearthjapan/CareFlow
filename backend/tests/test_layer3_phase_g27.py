"""Phase G-27: Layer 3 ``_cost_single_cell`` の sex_restriction format mismatch 修正テスト.

VPS 本番 DB 検証で、 7 件未割当のうち 5 件が 「性別制限あり」 コース由来と判明:
``patients.sex_restriction`` は DB enum で ``'female_only'`` / ``'male_only'`` を持つが、
``staff.sex`` は ``'female'`` / ``'male'``。 cost 計算で直接比較していたため、
性別制限のあるコースは全 staff INF (= 全部未割当) になっていた構造的バグ。

Phase G-27 修正: ``_normalize_sex_restriction`` を導入し、 cost 計算前に
``'female_only' -> 'female'`` / ``'male_only' -> 'male'`` に正規化する。
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
    HUNGARIAN_INFINITY,
    CourseAssignmentTarget,
    Layer3Assigner,
    StaffInfo,
)

# fixture 用の ISO 週. 月曜 = 2026-05-25 (week 22).
TEST_ISO_YEAR = 2026
TEST_ISO_WEEK = 22
TEST_WEEK_MONDAY = date(2026, 5, 25)


def _make_staff_info(*, sex: str | None, name: str = "g27-staff") -> StaffInfo:
    """Phase G-27 テスト用最小 StaffInfo."""
    return StaffInfo(
        staff_id=uuid4(),
        name=name,
        sex=sex,
        role="staff",
        primary_office_lat=35.6383,
        primary_office_lng=140.1041,
        work_days=frozenset(range(7)),  # 全曜日勤務
    )


def _make_course_target(
    *,
    restrictions: frozenset[str],
    weekday: int = 0,
    course_code: str = "A",
) -> CourseAssignmentTarget:
    """Phase G-27 テスト用最小 CourseAssignmentTarget."""
    return CourseAssignmentTarget(
        course_id=uuid4(),
        weekday=weekday,
        course_code=course_code,
        centroid_lat=35.6383,
        centroid_lng=140.1041,
        gender_restrictions=restrictions,
        patient_ids=[],
        visits=[],
    )


# ---------------------------------------------------------------------------
# 1) Unit: _cost_single_cell に対する性別制限正規化テスト
# ---------------------------------------------------------------------------


def test_solve_female_only_restriction_matches_female_staff() -> None:
    """course.gender_restrictions={'female_only'} + staff.sex='female' で
    cost が有限値 (= 割当可能) になることを確認 (Phase G-27 fix の核心)."""
    assigner = Layer3Assigner()
    course = _make_course_target(restrictions=frozenset({"female_only"}))
    staff = _make_staff_info(sex="female", name="g27-female-staff")
    cost = assigner._cost_single_cell(
        weekday=0,
        course=course,
        staff=staff,
        history=[],
    )
    assert cost < HUNGARIAN_INFINITY, (
        f"female_only restriction + female staff が INF: cost={cost} "
        f"(Phase G-27 fix が効いていない)"
    )


def test_solve_female_only_restriction_excludes_male_staff() -> None:
    """course.gender_restrictions={'female_only'} + staff.sex='male' で INF (ハード除外)."""
    assigner = Layer3Assigner()
    course = _make_course_target(restrictions=frozenset({"female_only"}))
    staff = _make_staff_info(sex="male", name="g27-male-staff")
    cost = assigner._cost_single_cell(
        weekday=0,
        course=course,
        staff=staff,
        history=[],
    )
    assert cost >= HUNGARIAN_INFINITY, (
        f"female_only restriction + male staff が有限値: cost={cost} (ハード除外が効いていない)"
    )


def test_solve_male_only_restriction_matches_male_staff() -> None:
    """course.gender_restrictions={'male_only'} + staff.sex='male' で有限 cost."""
    assigner = Layer3Assigner()
    course = _make_course_target(restrictions=frozenset({"male_only"}))
    staff = _make_staff_info(sex="male", name="g27-male-staff")
    cost = assigner._cost_single_cell(
        weekday=0,
        course=course,
        staff=staff,
        history=[],
    )
    assert cost < HUNGARIAN_INFINITY, f"male_only restriction + male staff が INF: cost={cost}"


def test_solve_male_only_restriction_excludes_female_staff() -> None:
    """course.gender_restrictions={'male_only'} + staff.sex='female' で INF."""
    assigner = Layer3Assigner()
    course = _make_course_target(restrictions=frozenset({"male_only"}))
    staff = _make_staff_info(sex="female", name="g27-female-staff")
    cost = assigner._cost_single_cell(
        weekday=0,
        course=course,
        staff=staff,
        history=[],
    )
    assert cost >= HUNGARIAN_INFINITY, f"male_only restriction + female staff が有限値: cost={cost}"


def test_solve_no_restriction_matches_any_staff() -> None:
    """regression: restriction 空のとき male / female どちらも有限 cost."""
    assigner = Layer3Assigner()
    course = _make_course_target(restrictions=frozenset())
    for sex in ("female", "male"):
        staff = _make_staff_info(sex=sex, name=f"g27-{sex}-no-restr")
        cost = assigner._cost_single_cell(
            weekday=0,
            course=course,
            staff=staff,
            history=[],
        )
        assert cost < HUNGARIAN_INFINITY, (
            f"restriction 空なのに sex={sex} で INF: cost={cost} (regression)"
        )


def test_solve_unknown_restriction_value_unchanged() -> None:
    """regression: 万一 'female' が直接渡されたケース (_only suffix 無し) も
    fall-through で staff.sex='female' と一致する."""
    assigner = Layer3Assigner()
    course = _make_course_target(restrictions=frozenset({"female"}))
    staff = _make_staff_info(sex="female", name="g27-bare-female")
    cost = assigner._cost_single_cell(
        weekday=0,
        course=course,
        staff=staff,
        history=[],
    )
    assert cost < HUNGARIAN_INFINITY, (
        f"bare 'female' restriction + female staff が INF: cost={cost} "
        f"(_only 抜きの直接比較が壊れている)"
    )


# ---------------------------------------------------------------------------
# 2) E2E: female_only patient を含む course が female staff に DB レベルで割当される
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assign_e2e_female_only_course_assigned_to_female_staff(db) -> None:
    """E2E: ``sex_restriction='female_only'`` の patient を含む course を
    ``Layer3Assigner.assign()`` で割当し、 DB の ``courses.assigned_staff_id`` が
    female staff の id になることを確認 (Phase G-27 本番再現バグの直接検証).

    reviewer CRITICAL-1: 修正前バグの pseudo-positive 検出を避けるため、
    male_staff を 1 名追加して 1×2 cost matrix にし、 ハンガリアン法が
    INF セル (male) ではなく有限セル (female) を選ぶことを保証する.
    (1×1 行列だと修正前コードでも female_staff INF→[0] が返って pass する
    可能性があった.)
    """
    office = Office(name="G27 稲毛事業所", lat=35.6383, lng=140.1041)
    db.add(office)
    await db.flush()

    female_staff = Staff(
        code="G27-FS1",
        name="G27 female staff",
        sex="female",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    male_staff = Staff(
        code="G27-MS1",
        name="G27 male staff",
        sex="male",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    db.add(female_staff)
    db.add(male_staff)
    await db.flush()
    for wd in range(7):
        db.add(StaffShift(staff_id=female_staff.id, weekday=wd, is_on=True))
        db.add(StaffShift(staff_id=male_staff.id, weekday=wd, is_on=True))

    # course (weekday=2 = 水曜) を course_fixed で作成
    course = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=2,
        code="A",
        course_status=COURSE_STATUS_COURSE_FIXED,
        office_id=office.id,
    )
    db.add(course)
    await db.flush()

    # female_only 制限付き patient + Visit
    patient = Patient(
        code="G27-FP1",
        name="G27 female_only patient",
        status="active",
        sex_restriction="female_only",  # ← Phase G-27 対象の DB 値
        lat=35.6383,
        lng=140.1041,
    )
    db.add(patient)
    await db.flush()

    visit_date = date.fromordinal(TEST_WEEK_MONDAY.toordinal() + 2)
    db.add(
        Visit(
            patient_id=patient.id,
            course_id=course.id,
            visit_date=visit_date,
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
    await assigner.assign(db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK)
    await db.commit()

    refreshed = (await db.scalars(select(Course).where(Course.id == course.id))).one()
    # 正の検証: female_only 制限を満たす female_staff が選ばれる
    assert refreshed.assigned_staff_id == female_staff.id, (
        f"female_only patient course が female staff に割当されていない "
        f"(Phase G-27 本番バグ再現): "
        f"got={refreshed.assigned_staff_id}, expected={female_staff.id}"
    )
    # 負の検証: 不適合な male_staff は絶対に選ばれてはいけない
    # (修正前バグ: 全 staff INF → ハンガリアン法が [0] を返し male_staff が
    # 選ばれる可能性があった. 1×2 matrix にすることで pseudo-positive を防ぐ.)
    assert refreshed.assigned_staff_id != male_staff.id, (
        f"female_only 制限なのに male_staff が選ばれた "
        f"(性別ハード制約が効いていない): got={refreshed.assigned_staff_id}"
    )
