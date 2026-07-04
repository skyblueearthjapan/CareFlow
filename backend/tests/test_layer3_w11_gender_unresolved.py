"""W-11: 性別残留違反 (候補ゼロ) の可視化テスト.

PO 報告「自動スタッフ割当で性別違反が警告なしで割り当てられている」の原因B 回帰:

原因B: 性別ブロックで未割当になったコースに override 候補が 1 名も居ないと、
従来は ``_build_review_items`` が黙って continue し、 過去の割付で残った性別制約
違反の ``assigned_staff_id`` が誰にも気づかれず「割当済み」に見え続けていた。

本テストは新設 ``_build_unresolved_gender_warnings`` の直接検証:

1. 男性スタッフが女性限定コースの担当のまま残っている → 残留違反警告 1 件
2. 現担当が性別制約を満たす (女性スタッフ) → 警告 0 件 (= 従来経路が不変・誤検知なし)
3. assigned_staff_id が null (純粋人手不足) → 警告 0 件 (= 挙動を変えない)
4. 複数コースの決定的ソート (weekday, course_code 昇順)
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from app.models import Course, Office, Staff
from app.models.course import COURSE_STATUS_STAFF_ASSIGNED
from app.services.scheduling.layer3_assignment import (
    CourseAssignmentTarget,
    Layer3Assigner,
)

TEST_ISO_YEAR = 2026
TEST_ISO_WEEK = 25
TEST_WEEK_MONDAY = date(2026, 6, 15)


def _target(
    *,
    course_id,
    course_code: str = "A",
    weekday: int = 0,
    office_id,
    restriction: str | None = "female_only",
) -> CourseAssignmentTarget:
    """gender_restrictions つきの assignment 対象を組み立てる (patient/visit 不要)."""
    return CourseAssignmentTarget(
        course_id=course_id,
        weekday=weekday,
        course_code=course_code,
        centroid_lat=None,
        centroid_lng=None,
        gender_restrictions=frozenset({restriction}) if restriction else frozenset(),
        patient_ids=[uuid4()],
        office_id=office_id,
    )


async def _seed_course(
    db,
    *,
    office_id,
    assigned_staff_id,
    code: str = "A",
    weekday: int = 0,
):
    """assigned_staff_id 付きの当該週コースを 1 件作成して返す."""
    course = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=weekday,
        code=code,
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=assigned_staff_id,
        office_id=office_id,
    )
    db.add(course)
    await db.flush()
    return course


# ---------------------------------------------------------------------------
# テスト 1: 男性が女性限定コースの担当のまま残っている → 残留違反警告 1 件
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_male_on_female_only_yields_warning(db) -> None:
    """現担当 (男性) が女性限定コースに残留 → UnresolvedGenderWarning 1 件."""
    office = Office(name="W11-T1 拠点", lat=35.65, lng=140.0)
    db.add(office)
    await db.flush()

    male = Staff(
        code="W11-T1-M",
        name="W11 男性担当",
        role="staff",
        status="active",
        sex="male",
        primary_office_id=office.id,
    )
    db.add(male)
    await db.flush()

    course = await _seed_course(db, office_id=office.id, assigned_staff_id=male.id)
    await db.commit()

    target = _target(course_id=course.id, office_id=office.id, restriction="female_only")

    assigner = Layer3Assigner()
    warnings = await assigner._build_unresolved_gender_warnings(
        db,
        course_ids={course.id},
        targets_by_id={course.id: target},
    )

    assert len(warnings) == 1, f"残留違反 1 件想定: {warnings}"
    w = warnings[0]
    assert w.course_id == course.id
    assert w.course_code == "A"
    assert w.weekday == 0
    assert w.office_name == "W11-T1 拠点"
    assert w.current_staff_name == "W11 男性担当"
    # 文言に現担当名が入り「手動」調整を促していること.
    assert "W11 男性担当" in w.reason_text
    assert "手動" in w.reason_text


# ---------------------------------------------------------------------------
# テスト 2: 現担当が性別制約を満たす (女性) → 警告 0 件 (誤検知なし)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_female_on_female_only_no_warning(db) -> None:
    """現担当 (女性) が女性限定を満たす → 残留違反ではない → 警告 0 件."""
    office = Office(name="W11-T2 拠点", lat=35.65, lng=140.0)
    db.add(office)
    await db.flush()

    female = Staff(
        code="W11-T2-F",
        name="W11 女性担当",
        role="staff",
        status="active",
        sex="female",
        primary_office_id=office.id,
    )
    db.add(female)
    await db.flush()

    course = await _seed_course(db, office_id=office.id, assigned_staff_id=female.id)
    await db.commit()

    target = _target(course_id=course.id, office_id=office.id, restriction="female_only")

    assigner = Layer3Assigner()
    warnings = await assigner._build_unresolved_gender_warnings(
        db,
        course_ids={course.id},
        targets_by_id={course.id: target},
    )

    assert warnings == [], f"性別制約を満たす担当は警告しないはず: {warnings}"


# ---------------------------------------------------------------------------
# テスト 3: assigned_staff_id が null (純粋人手不足) → 警告 0 件
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_assigned_staff_no_warning(db) -> None:
    """assigned_staff_id が null (純粋人手不足) → 従来どおり警告 0 件."""
    office = Office(name="W11-T3 拠点", lat=35.65, lng=140.0)
    db.add(office)
    await db.flush()

    course = await _seed_course(db, office_id=office.id, assigned_staff_id=None)
    await db.commit()

    target = _target(course_id=course.id, office_id=office.id, restriction="female_only")

    assigner = Layer3Assigner()
    warnings = await assigner._build_unresolved_gender_warnings(
        db,
        course_ids={course.id},
        targets_by_id={course.id: target},
    )

    assert warnings == [], f"担当なしコースは残留違反警告を出さないはず: {warnings}"


# ---------------------------------------------------------------------------
# テスト 4: 複数コースが (weekday, course_code) 昇順で決定的にソートされる
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_warnings_sorted_deterministically(db) -> None:
    """複数残留違反は (weekday, course_code) 昇順で返る."""
    office = Office(name="W11-T4 拠点", lat=35.65, lng=140.0)
    db.add(office)
    await db.flush()

    male = Staff(
        code="W11-T4-M",
        name="W11-T4 男性",
        role="staff",
        status="active",
        sex="male",
        primary_office_id=office.id,
    )
    db.add(male)
    await db.flush()

    # 意図的に投入順を崩す: (火/B), (月/C), (月/A)
    c_tue_b = await _seed_course(
        db, office_id=office.id, assigned_staff_id=male.id, code="B", weekday=1
    )
    c_mon_c = await _seed_course(
        db, office_id=office.id, assigned_staff_id=male.id, code="C", weekday=0
    )
    c_mon_a = await _seed_course(
        db, office_id=office.id, assigned_staff_id=male.id, code="A", weekday=0
    )
    await db.commit()

    targets = {
        c_tue_b.id: _target(course_id=c_tue_b.id, office_id=office.id, course_code="B", weekday=1),
        c_mon_c.id: _target(course_id=c_mon_c.id, office_id=office.id, course_code="C", weekday=0),
        c_mon_a.id: _target(course_id=c_mon_a.id, office_id=office.id, course_code="A", weekday=0),
    }

    assigner = Layer3Assigner()
    warnings = await assigner._build_unresolved_gender_warnings(
        db,
        course_ids=set(targets),
        targets_by_id=targets,
    )

    ordered = [(w.weekday, w.course_code) for w in warnings]
    assert ordered == [(0, "A"), (0, "C"), (1, "B")], f"決定的ソート不一致: {ordered}"
