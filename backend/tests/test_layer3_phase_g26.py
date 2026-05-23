"""Phase G-26: Layer 3 ``_build_fixed_assignments`` の staff_assigned 拾い漏れ修正テスト.

「自動割付」 ボタン (POST /api/v1/schedule/assign-staff-only) の再実行時、
全コースが ``course_status='staff_assigned'`` + ``assigned_staff_id=NULL``
(= 一斉未割当直後) の状態でも、 manager → M / 都賀 staff → 都賀 A の
固定割当が効くことを検証する (両 status を条件に含めた regression 防止).
"""

from __future__ import annotations

from datetime import date, time

import pytest
from sqlalchemy import select

from app.models import Course, Office, Patient, Staff, StaffShift, Visit
from app.models.course import (
    COURSE_STATUS_COURSE_FIXED,
    COURSE_STATUS_STAFF_ASSIGNED,
)
from app.models.visit import VISIT_STATUS_PLANNED
from app.services.scheduling.layer3_assignment import Layer3Assigner

# fixture 用の ISO 週. 月曜 = 2026-05-25 (week 22).
TEST_ISO_YEAR = 2026
TEST_ISO_WEEK = 22
TEST_WEEK_MONDAY = date(2026, 5, 25)


async def _seed_visit_for_course(db, *, course_id, patient_code: str, weekday: int) -> None:
    """course に紐づく Patient + Visit を 1 件作成する."""
    patient = Patient(code=patient_code, name=f"phase-g26-{patient_code}", status="active")
    db.add(patient)
    await db.flush()
    # weekday=0 → 月曜 = 2026-05-25, weekday=1 → 2026-05-26, ...
    visit_date = date.fromordinal(TEST_WEEK_MONDAY.toordinal() + weekday)
    db.add(
        Visit(
            patient_id=patient.id,
            course_id=course_id,
            visit_date=visit_date,
            start_time=time(9, 0),
            end_time=time(9, 30),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto",
            required_staff_count=1,
        )
    )


@pytest.mark.asyncio
async def test_build_fixed_assignments_includes_staff_assigned_for_manager(db) -> None:
    """manager 固定割当: course_status='staff_assigned' でも M コースが拾われる.

    シナリオ: 一斉未割当ボタン押下後、 M コースは status='staff_assigned' /
    assigned_staff_id=NULL の状態。 ``_build_fixed_assignments`` が
    この status を拾えないと manager が 1 件も割当されない (本番 G-26 バグ再現).
    """
    inage = Office(name="G26 稲毛事業所", lat=35.6383, lng=140.1041)
    db.add(inage)
    await db.flush()

    manager = Staff(
        code="G26-M1",
        name="G26 manager 川名",
        sex="female",
        role="manager",
        status="active",
        primary_office_id=inage.id,
    )
    db.add(manager)
    await db.flush()
    # 全曜日 ON
    for wd in range(7):
        db.add(StaffShift(staff_id=manager.id, weekday=wd, is_on=True))

    # M コースを 'staff_assigned' (assigned_staff_id=NULL) で作成 (= 一斉未割当後の状態)
    m_course = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=0,
        code="M",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=None,
        office_id=inage.id,
    )
    db.add(m_course)
    await db.flush()
    await _seed_visit_for_course(db, course_id=m_course.id, patient_code="G26-M-P0", weekday=0)
    await db.commit()

    assigner = Layer3Assigner()
    fixed = await assigner._build_fixed_assignments(
        db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK, office_id=inage.id
    )
    assert m_course.id in fixed, (
        f"manager 固定割当が staff_assigned コースを拾えていない (Phase G-26 regression): "
        f"fixed_keys={list(fixed.keys())}"
    )
    assert fixed[m_course.id] == manager.id


@pytest.mark.asyncio
async def test_build_fixed_assignments_includes_staff_assigned_for_tsuga_staff(db) -> None:
    """都賀 staff 固定割当: course_status='staff_assigned' でも A コースが拾われる.

    シナリオ: 一斉未割当ボタン押下後、 都賀 A コースは status='staff_assigned' /
    assigned_staff_id=NULL の状態。 ``_build_fixed_assignments`` が
    この status を拾えないと都賀 staff の本名さんが稲毛 M に流れるバグ再現.
    """
    tsuga = Office(name="G26 都賀事業所", lat=35.6500, lng=140.1700)
    db.add(tsuga)
    await db.flush()

    tsuga_staff = Staff(
        code="G26-T1",
        name="G26 都賀 staff 本名",
        sex="female",
        role="staff",
        status="active",
        primary_office_id=tsuga.id,
    )
    db.add(tsuga_staff)
    await db.flush()
    for wd in range(7):
        db.add(StaffShift(staff_id=tsuga_staff.id, weekday=wd, is_on=True))

    a_course = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=1,
        code="A",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=None,
        office_id=tsuga.id,
    )
    db.add(a_course)
    await db.flush()
    await _seed_visit_for_course(db, course_id=a_course.id, patient_code="G26-T-P0", weekday=1)
    await db.commit()

    assigner = Layer3Assigner()
    fixed = await assigner._build_fixed_assignments(
        db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK, office_id=tsuga.id
    )
    assert a_course.id in fixed, (
        f"都賀 staff 固定割当が staff_assigned コースを拾えていない (Phase G-26 regression): "
        f"fixed_keys={list(fixed.keys())}"
    )
    assert fixed[a_course.id] == tsuga_staff.id


@pytest.mark.asyncio
async def test_build_fixed_assignments_still_picks_course_fixed(db) -> None:
    """既存挙動: course_fixed 状態のコースも引き続き固定割当の対象である (regression)."""
    inage = Office(name="G26 稲毛事業所 (fixed)", lat=35.6383, lng=140.1041)
    db.add(inage)
    await db.flush()

    manager = Staff(
        code="G26F-M1",
        name="G26 manager (fixed)",
        sex="female",
        role="manager",
        status="active",
        primary_office_id=inage.id,
    )
    db.add(manager)
    await db.flush()
    for wd in range(7):
        db.add(StaffShift(staff_id=manager.id, weekday=wd, is_on=True))

    m_course = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=2,
        code="M",
        course_status=COURSE_STATUS_COURSE_FIXED,
        office_id=inage.id,
    )
    db.add(m_course)
    await db.flush()
    await _seed_visit_for_course(db, course_id=m_course.id, patient_code="G26F-M-P0", weekday=2)
    await db.commit()

    assigner = Layer3Assigner()
    fixed = await assigner._build_fixed_assignments(
        db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK, office_id=inage.id
    )
    assert fixed.get(m_course.id) == manager.id


@pytest.mark.asyncio
async def test_build_fixed_assignments_mixed_status_both_picked(db) -> None:
    """course_fixed と staff_assigned が混在する場合、 両方とも固定割当に含まれる."""
    tsuga = Office(name="G26 都賀事業所 (mixed)", lat=35.6500, lng=140.1700)
    db.add(tsuga)
    await db.flush()

    tsuga_staff = Staff(
        code="G26X-T1",
        name="G26 都賀 staff (mixed)",
        sex="female",
        role="staff",
        status="active",
        primary_office_id=tsuga.id,
    )
    db.add(tsuga_staff)
    await db.flush()
    for wd in range(7):
        db.add(StaffShift(staff_id=tsuga_staff.id, weekday=wd, is_on=True))

    a_course_fixed = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_COURSE_FIXED,
        office_id=tsuga.id,
    )
    a_course_staff_assigned = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=1,
        code="A",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=None,
        office_id=tsuga.id,
    )
    db.add_all([a_course_fixed, a_course_staff_assigned])
    await db.flush()
    await _seed_visit_for_course(
        db, course_id=a_course_fixed.id, patient_code="G26X-A0-P", weekday=0
    )
    await db.commit()

    assigner = Layer3Assigner()
    fixed = await assigner._build_fixed_assignments(
        db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK, office_id=tsuga.id
    )
    assert fixed.get(a_course_fixed.id) == tsuga_staff.id
    assert fixed.get(a_course_staff_assigned.id) == tsuga_staff.id


@pytest.mark.asyncio
async def test_assign_staff_only_scenario_fixed_rules_apply_after_unassign(db) -> None:
    """End-to-end 風: 一斉未割当後 (全コース staff_assigned + NULL) で
    ``assign`` を実行し、 manager / 都賀 staff の固定ルールが効くこと.
    """
    inage = Office(name="G26E 稲毛事業所", lat=35.6383, lng=140.1041)
    tsuga = Office(name="G26E 都賀事業所", lat=35.6500, lng=140.1700)
    db.add_all([inage, tsuga])
    await db.flush()

    manager = Staff(
        code="G26E-M1",
        name="G26E manager",
        sex="female",
        role="manager",
        status="active",
        primary_office_id=inage.id,
    )
    inage_staff = Staff(
        code="G26E-S1",
        name="G26E inage staff",
        sex="female",
        role="staff",
        status="active",
        primary_office_id=inage.id,
    )
    tsuga_staff = Staff(
        code="G26E-T1",
        name="G26E 都賀 staff",
        sex="female",
        role="staff",
        status="active",
        primary_office_id=tsuga.id,
    )
    db.add_all([manager, inage_staff, tsuga_staff])
    await db.flush()
    for s in (manager, inage_staff, tsuga_staff):
        for wd in range(7):
            db.add(StaffShift(staff_id=s.id, weekday=wd, is_on=True))

    # 全コース staff_assigned / NULL (= 一斉未割当直後の状態)
    inage_m = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=0,
        code="M",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=None,
        office_id=inage.id,
    )
    inage_a = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=None,
        office_id=inage.id,
    )
    tsuga_a = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=None,
        office_id=tsuga.id,
    )
    db.add_all([inage_m, inage_a, tsuga_a])
    await db.flush()
    await _seed_visit_for_course(db, course_id=inage_m.id, patient_code="G26E-M-P", weekday=0)
    await _seed_visit_for_course(db, course_id=inage_a.id, patient_code="G26E-IA-P", weekday=0)
    await _seed_visit_for_course(db, course_id=tsuga_a.id, patient_code="G26E-TA-P", weekday=0)
    await db.commit()

    assigner = Layer3Assigner()
    await assigner.assign(db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK)
    await db.commit()

    refreshed = {
        c.id: c
        for c in (
            await db.scalars(
                select(Course).where(
                    Course.iso_year == TEST_ISO_YEAR,
                    Course.iso_week == TEST_ISO_WEEK,
                )
            )
        ).all()
    }
    # manager は 稲毛 M に固定
    assert refreshed[inage_m.id].assigned_staff_id == manager.id, (
        f"manager が稲毛 M に固定割当されていない (Phase G-26 regression): "
        f"got={refreshed[inage_m.id].assigned_staff_id}, expected={manager.id}"
    )
    # 都賀 staff は 都賀 A に固定 (= 稲毛 M に流れない)
    assert refreshed[tsuga_a.id].assigned_staff_id == tsuga_staff.id, (
        f"都賀 staff が都賀 A に固定割当されていない (Phase G-26 regression): "
        f"got={refreshed[tsuga_a.id].assigned_staff_id}, expected={tsuga_staff.id}"
    )

    # ---------- Phase G-26 Issue #3: 冪等性検証 ----------
    # 2 回目の assign() を実行しても固定割当 (manager → M / 都賀 staff → 都賀 A) が
    # 変化しないことを確認する。
    await assigner.assign(db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK)
    await db.commit()
    refreshed_2 = {
        c.id: c.assigned_staff_id
        for c in (
            await db.scalars(
                select(Course).where(
                    Course.iso_year == TEST_ISO_YEAR,
                    Course.iso_week == TEST_ISO_WEEK,
                )
            )
        ).all()
    }
    assert refreshed_2[inage_m.id] == manager.id, (
        f"2 回目 assign() で manager 固定が崩れた (冪等性違反): "
        f"got={refreshed_2[inage_m.id]}, expected={manager.id}"
    )
    assert refreshed_2[tsuga_a.id] == tsuga_staff.id, (
        f"2 回目 assign() で 都賀 staff 固定が崩れた (冪等性違反): "
        f"got={refreshed_2[tsuga_a.id]}, expected={tsuga_staff.id}"
    )


# ---------------------------------------------------------------------------- #
# Phase G-26 Issue #1 / #2: W25 fix (admin 手動割付保護) との競合回避
# ---------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_build_fixed_assignments_preserves_admin_manual_M_assignment(db) -> None:  # noqa: N802
    """M course を 他 staff (manager 以外) に admin が手動で割り当てた場合、
    ``_build_fixed_assignments`` がそれを上書きしないこと.

    シナリオ:
      - manager 川名 / staff 田中 (manager 以外) を作成
      - M course を status='staff_assigned' + assigned_staff_id=田中 で fixture 作成
        (= 川名さん休暇日に admin が UI から田中に手動変更した状態)
      - ``_build_fixed_assignments`` 実行 → M course id が結果 dict に含まれない
        (= manager で上書きしない / W25 fix の admin 手動割付保護と整合)
    """
    inage = Office(name="G26S1 稲毛事業所", lat=35.6383, lng=140.1041)
    db.add(inage)
    await db.flush()

    manager = Staff(
        code="G26S1-M1",
        name="G26S1 manager 川名",
        sex="female",
        role="manager",
        status="active",
        primary_office_id=inage.id,
    )
    other_staff = Staff(
        code="G26S1-S1",
        name="G26S1 staff 田中",
        sex="female",
        role="staff",
        status="active",
        primary_office_id=inage.id,
    )
    db.add_all([manager, other_staff])
    await db.flush()
    for s in (manager, other_staff):
        for wd in range(7):
            db.add(StaffShift(staff_id=s.id, weekday=wd, is_on=True))

    # M course を 他 staff (manager 以外) に admin が手動割付済 (= staff_assigned 状態)
    m_course = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=2,
        code="M",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=other_staff.id,
        office_id=inage.id,
    )
    db.add(m_course)
    await db.flush()
    await _seed_visit_for_course(db, course_id=m_course.id, patient_code="G26S1-M-P", weekday=2)
    await db.commit()

    assigner = Layer3Assigner()
    fixed = await assigner._build_fixed_assignments(
        db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK, office_id=inage.id
    )
    # admin 手動割付 (他 staff = manager 以外) の M course は固定対象から外れる
    assert m_course.id not in fixed, (
        f"admin 手動割付 (他 staff) の M course を manager で上書きしようとしている "
        f"(W25 fix との競合 / Phase G-26 Issue #1 regression): "
        f"fixed.get(m_course.id)={fixed.get(m_course.id)}, expected=missing"
    )


@pytest.mark.asyncio
async def test_build_fixed_assignments_preserves_admin_manual_tsuga_A_assignment(db) -> None:  # noqa: N802
    """都賀 A course を primary_staff 以外の staff に admin が手動割付した場合、
    ``_build_fixed_assignments`` がそれを上書きしないこと.
    """
    tsuga = Office(name="G26S2 都賀事業所", lat=35.6500, lng=140.1700)
    db.add(tsuga)
    await db.flush()

    primary_staff = Staff(
        code="G26S2-T1",
        name="G26S2 都賀 staff (primary)",
        sex="female",
        role="staff",
        status="active",
        primary_office_id=tsuga.id,
    )
    other_staff = Staff(
        code="G26S2-T2",
        name="G26S2 都賀 staff (other)",
        sex="female",
        role="staff",
        status="active",
        primary_office_id=tsuga.id,
    )
    db.add_all([primary_staff, other_staff])
    await db.flush()
    for s in (primary_staff, other_staff):
        for wd in range(7):
            db.add(StaffShift(staff_id=s.id, weekday=wd, is_on=True))

    # 都賀 A course を primary_staff 以外に admin が手動割付済 (= staff_assigned 状態)
    a_course = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=3,
        code="A",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=other_staff.id,
        office_id=tsuga.id,
    )
    db.add(a_course)
    await db.flush()
    await _seed_visit_for_course(db, course_id=a_course.id, patient_code="G26S2-A-P", weekday=3)
    await db.commit()

    assigner = Layer3Assigner()
    fixed = await assigner._build_fixed_assignments(
        db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK, office_id=tsuga.id
    )
    assert a_course.id not in fixed, (
        f"admin 手動割付 (他 staff) の 都賀 A course を primary_staff で上書きしようとしている "
        f"(W25 fix との競合 / Phase G-26 Issue #1 regression): "
        f"fixed.get(a_course.id)={fixed.get(a_course.id)}, expected=missing"
    )


@pytest.mark.asyncio
async def test_assign_staff_only_e2e_preserves_admin_manual_assignment(db) -> None:
    """E2E: ``Layer3Assigner.assign()`` を実行しても、
    admin 手動割付 (M course → 他 staff) が保持される.

    Phase G-26 修正前なら:
      - ``_build_fixed_assignments`` が M course を拾い manager で上書きしようとする
      - W25 fix の ``if c.id not in fixed_staff_by_course`` でスキップ
      - manager が当日休暇のため未割当 → M course が NULL に戻る
    修正後:
      - safe-guard で M course (他 staff 割付済) は固定対象から外れる
      - W25 fix が拾って admin 手動割付を保持
    """
    inage = Office(name="G26S3 稲毛事業所", lat=35.6383, lng=140.1041)
    db.add(inage)
    await db.flush()

    # manager は当日 (weekday=4) 休暇
    manager = Staff(
        code="G26S3-M1",
        name="G26S3 manager",
        sex="female",
        role="manager",
        status="active",
        primary_office_id=inage.id,
    )
    other_staff = Staff(
        code="G26S3-S1",
        name="G26S3 staff (admin 手動割付先)",
        sex="female",
        role="staff",
        status="active",
        primary_office_id=inage.id,
    )
    db.add_all([manager, other_staff])
    await db.flush()
    # other_staff は weekday=4 (金曜) 出勤、 manager は休暇
    for wd in range(7):
        db.add(StaffShift(staff_id=manager.id, weekday=wd, is_on=(wd != 4)))
        db.add(StaffShift(staff_id=other_staff.id, weekday=wd, is_on=True))

    # M course (weekday=4) を admin が他 staff に手動割付済
    m_course = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=4,
        code="M",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=other_staff.id,
        office_id=inage.id,
    )
    db.add(m_course)
    await db.flush()
    await _seed_visit_for_course(db, course_id=m_course.id, patient_code="G26S3-M-P", weekday=4)
    await db.commit()

    assigner = Layer3Assigner()
    await assigner.assign(db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK)
    await db.commit()

    refreshed = (await db.scalars(select(Course).where(Course.id == m_course.id))).one()
    assert refreshed.assigned_staff_id == other_staff.id, (
        f"admin 手動割付 (他 staff) が保持されていない (Phase G-26 Issue #1 regression): "
        f"got={refreshed.assigned_staff_id}, expected={other_staff.id}"
    )


# ---------------------------------------------------------------------------- #
# Phase G-26 Issue #4: edge case (manager 0 名 / 都賀 staff 0 名)
# ---------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_build_fixed_assignments_no_manager(db) -> None:
    """拠点に manager 0 名の場合、 fixed_assignments に M 関連エントリが含まれない."""
    inage = Office(name="G26E1 稲毛事業所 (no manager)", lat=35.6383, lng=140.1041)
    db.add(inage)
    await db.flush()

    # staff のみ存在 (manager 0 名)
    staff = Staff(
        code="G26E1-S1",
        name="G26E1 staff",
        sex="female",
        role="staff",
        status="active",
        primary_office_id=inage.id,
    )
    db.add(staff)
    await db.flush()
    for wd in range(7):
        db.add(StaffShift(staff_id=staff.id, weekday=wd, is_on=True))

    m_course = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=0,
        code="M",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=None,
        office_id=inage.id,
    )
    db.add(m_course)
    await db.flush()
    await _seed_visit_for_course(db, course_id=m_course.id, patient_code="G26E1-M-P", weekday=0)
    await db.commit()

    assigner = Layer3Assigner()
    fixed = await assigner._build_fixed_assignments(
        db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK, office_id=inage.id
    )
    assert m_course.id not in fixed, (
        f"manager 0 名なのに M course が固定割当に含まれている: "
        f"fixed.get(m_course.id)={fixed.get(m_course.id)}, expected=missing"
    )


@pytest.mark.asyncio
async def test_build_fixed_assignments_no_tsuga_staff(db) -> None:
    """都賀拠点に staff 0 名の場合、 都賀 A course が NULL のまま (固定割当に含まれない)."""
    tsuga = Office(name="G26E2 都賀事業所 (no staff)", lat=35.6500, lng=140.1700)
    db.add(tsuga)
    await db.flush()

    # 都賀拠点に staff 無し (manager だけ存在しても都賀 staff 固定対象外)
    manager = Staff(
        code="G26E2-M1",
        name="G26E2 都賀 manager (not staff)",
        sex="female",
        role="manager",
        status="active",
        primary_office_id=tsuga.id,
    )
    db.add(manager)
    await db.flush()
    for wd in range(7):
        db.add(StaffShift(staff_id=manager.id, weekday=wd, is_on=True))

    a_course = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=None,
        office_id=tsuga.id,
    )
    db.add(a_course)
    await db.flush()
    await _seed_visit_for_course(db, course_id=a_course.id, patient_code="G26E2-A-P", weekday=0)
    await db.commit()

    assigner = Layer3Assigner()
    fixed = await assigner._build_fixed_assignments(
        db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK, office_id=tsuga.id
    )
    assert a_course.id not in fixed, (
        f"都賀 staff 0 名なのに 都賀 A course が固定割当に含まれている: "
        f"fixed.get(a_course.id)={fixed.get(a_course.id)}, expected=missing"
    )
