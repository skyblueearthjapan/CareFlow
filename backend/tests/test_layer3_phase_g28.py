"""Phase G-28: Layer 3 で visits=0 のコースに staff が無駄割当される bug 修正テスト.

VPS 本番 DB 2026-W21 で 30/35 件割当 (5 件 NULL) のうち、 0-visits コース
(稲毛 M2 / 稲毛 D / 都賀 D / 都賀 M / 都賀 A など) に staff が固定 / Hungarian
割当されて消費されている問題を発見。 これら staff を解放すれば NULL 5 件中
3 件 (水稲毛B, 水稲毛D, 金稲毛C) が割当可能になる。

Phase G-28 修正:
  - ``_load_course_targets`` で ``patient_ids`` (= visits) が空のコースを
    target list から除外 (= ハンガリアン法の対象から外す)
  - ``_build_fixed_assignments`` でも M / 都賀 A コースの visits=0 を
    固定対象から除外する (manager / 都賀 staff の無駄消費を回避)
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


async def _seed_visit_for_course(
    db,
    *,
    course_id,
    patient_code: str,
    weekday: int,
    start_hour: int = 9,
) -> None:
    """course に紐づく Patient + Visit を 1 件作成する."""
    patient = Patient(
        code=patient_code,
        name=f"phase-g28-{patient_code}",
        status="active",
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


# ---------------------------------------------------------------------------
# 1) Unit: _load_course_targets skip 検証
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_course_targets_skips_empty_courses(db) -> None:
    """visits=2 のコースは含まれ、 visits=0 のコースは target list から除外される.

    Phase G-28 の核心テスト. ハンガリアン法に空コースを渡さないことで
    staff_pool が他コースに流れる前提条件を保証する.
    """
    inage = Office(name="G28-1 稲毛事業所", lat=35.6383, lng=140.1041)
    db.add(inage)
    await db.flush()

    # visits ありコース (A)
    course_with_visits = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_COURSE_FIXED,
        office_id=inage.id,
    )
    # visits=0 のコース (B) — Visit を 1 件も seed しない
    course_empty = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=0,
        code="B",
        course_status=COURSE_STATUS_COURSE_FIXED,
        office_id=inage.id,
    )
    db.add_all([course_with_visits, course_empty])
    await db.flush()
    await _seed_visit_for_course(
        db, course_id=course_with_visits.id, patient_code="G28-1-P1", weekday=0
    )
    await _seed_visit_for_course(
        db,
        course_id=course_with_visits.id,
        patient_code="G28-1-P2",
        weekday=0,
        start_hour=10,
    )
    await db.commit()

    assigner = Layer3Assigner()
    targets = await assigner._load_course_targets(
        db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK, office_id=inage.id
    )
    target_ids = [t.course_id for t in targets]

    assert len(targets) == 1, (
        f"visits=0 コースが target に含まれている (Phase G-28 fix が効いていない): "
        f"len={len(targets)} target_ids={target_ids}"
    )
    assert course_with_visits.id in target_ids, (
        f"visits ありコースが落ちている (regression): target_ids={target_ids}"
    )
    assert course_empty.id not in target_ids, (
        f"visits=0 コースが target に含まれている: target_ids={target_ids}"
    )


# ---------------------------------------------------------------------------
# 2) Regression: visits ありコースは引き続き対象に含まれる
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_course_targets_includes_courses_with_visits(db) -> None:
    """regression: visits ありの複数コースは全て target に含まれ、
    patient_ids / centroid が正しく算出される (Phase G-28 修正で
    既存挙動が壊れていないことを担保).
    """
    inage = Office(name="G28-2 稲毛事業所", lat=35.6383, lng=140.1041)
    db.add(inage)
    await db.flush()

    courses = []
    for code, weekday in [("A", 0), ("B", 0), ("C", 1)]:
        c = Course(
            iso_year=TEST_ISO_YEAR,
            iso_week=TEST_ISO_WEEK,
            weekday=weekday,
            code=code,
            course_status=COURSE_STATUS_COURSE_FIXED,
            office_id=inage.id,
        )
        db.add(c)
        courses.append(c)
    await db.flush()
    for i, c in enumerate(courses):
        await _seed_visit_for_course(
            db, course_id=c.id, patient_code=f"G28-2-{i}", weekday=c.weekday
        )
    await db.commit()

    assigner = Layer3Assigner()
    targets = await assigner._load_course_targets(
        db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK, office_id=inage.id
    )
    target_ids = {t.course_id for t in targets}
    assert len(targets) == 3, (
        f"visits ありコース 3 件が target に揃っていない (regression): "
        f"len={len(targets)} target_ids={target_ids}"
    )
    for c in courses:
        assert c.id in target_ids, (
            f"visits ありコース {c.code} が target から落ちている: target_ids={target_ids}"
        )
    # patient_ids が空でない (= 0 件 skip ロジックの誤検出が無い)
    for t in targets:
        assert len(t.patient_ids) >= 1, (
            f"visits ありコース target.patient_ids が空: course_id={t.course_id}"
        )


# ---------------------------------------------------------------------------
# 3) Unit: _build_fixed_assignments M コースの空 skip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_fixed_assignments_skips_empty_M_course(db) -> None:  # noqa: N802
    """M course を 2 つ作る (月M visits=3, 火M visits=0).
    manager 1 名で _build_fixed_assignments を実行し、 月M のみ固定対象、
    火M は対象外 (= result に含まれない) であることを確認する.

    本番再現: 「稲毛 M2 (火) visits=0」 に宇田川さんが取られて他コースが
    NULL になる現象の修正検証.
    """
    inage = Office(name="G28-3 稲毛事業所", lat=35.6383, lng=140.1041)
    db.add(inage)
    await db.flush()

    manager = Staff(
        code="G28-3-M1",
        name="G28-3 manager 宇田川",
        sex="female",
        role="manager",
        status="active",
        primary_office_id=inage.id,
    )
    db.add(manager)
    await db.flush()
    for wd in range(7):
        db.add(StaffShift(staff_id=manager.id, weekday=wd, is_on=True))

    mon_m = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=0,  # 月
        code="M",
        course_status=COURSE_STATUS_COURSE_FIXED,
        office_id=inage.id,
    )
    tue_m_empty = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=1,  # 火
        code="M",
        course_status=COURSE_STATUS_COURSE_FIXED,
        office_id=inage.id,
    )
    db.add_all([mon_m, tue_m_empty])
    await db.flush()
    # 月M に visits=3 / 火M は visits=0 のまま
    for i, hour in enumerate([9, 10, 11]):
        await _seed_visit_for_course(
            db,
            course_id=mon_m.id,
            patient_code=f"G28-3-MON-{i}",
            weekday=0,
            start_hour=hour,
        )
    await db.commit()

    assigner = Layer3Assigner()
    fixed = await assigner._build_fixed_assignments(
        db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK, office_id=inage.id
    )
    assert fixed.get(mon_m.id) == manager.id, (
        f"月M (visits=3) が manager 固定対象から外れている (regression): fixed={fixed}"
    )
    assert tue_m_empty.id not in fixed, (
        f"火M (visits=0) が固定対象に含まれている (Phase G-28 fix 不発): "
        f"fixed.get(tue_m_empty.id)={fixed.get(tue_m_empty.id)}, expected=missing"
    )


# ---------------------------------------------------------------------------
# 4) Unit: _build_fixed_assignments 都賀 A コースの空 skip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_fixed_assignments_skips_empty_tsuga_A_course(db) -> None:  # noqa: N802
    """都賀 A を 2 つ作る (月=visits=4 / 土=visits=0). 都賀 staff 1 名で
    _build_fixed_assignments 実行 → 月のみ固定対象、 土は対象外."""
    tsuga = Office(name="G28-4 都賀事業所", lat=35.6500, lng=140.1700)
    db.add(tsuga)
    await db.flush()

    tsuga_staff = Staff(
        code="G28-4-T1",
        name="G28-4 都賀 staff 本名",
        sex="female",
        role="staff",
        status="active",
        primary_office_id=tsuga.id,
    )
    db.add(tsuga_staff)
    await db.flush()
    for wd in range(7):
        db.add(StaffShift(staff_id=tsuga_staff.id, weekday=wd, is_on=True))

    mon_a = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_COURSE_FIXED,
        office_id=tsuga.id,
    )
    sat_a_empty = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=5,  # 土
        code="A",
        course_status=COURSE_STATUS_COURSE_FIXED,
        office_id=tsuga.id,
    )
    db.add_all([mon_a, sat_a_empty])
    await db.flush()
    for i, hour in enumerate([9, 10, 11, 13]):
        await _seed_visit_for_course(
            db,
            course_id=mon_a.id,
            patient_code=f"G28-4-MON-{i}",
            weekday=0,
            start_hour=hour,
        )
    await db.commit()

    assigner = Layer3Assigner()
    fixed = await assigner._build_fixed_assignments(
        db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK, office_id=tsuga.id
    )
    assert fixed.get(mon_a.id) == tsuga_staff.id, (
        f"月都賀A (visits=4) が固定対象から外れている (regression): fixed={fixed}"
    )
    assert sat_a_empty.id not in fixed, (
        f"土都賀A (visits=0) が固定対象に含まれている (Phase G-28 fix 不発): "
        f"fixed.get(sat_a_empty.id)={fixed.get(sat_a_empty.id)}, expected=missing"
    )


# ---------------------------------------------------------------------------
# 5) E2E: 空コースに staff が消費されない
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assign_e2e_empty_course_does_not_consume_staff(db) -> None:
    """E2E: 稲毛 A (visits=3) + 稲毛 D (visits=0) + staff 1 名で assign 実行.

    修正前: 稲毛 D に staff を取られて稲毛 A が NULL になる可能性
    修正後: 稲毛 A に staff 割当、 稲毛 D は assigned_staff_id IS NULL のまま
    """
    inage = Office(name="G28-5 稲毛事業所", lat=35.6383, lng=140.1041)
    db.add(inage)
    await db.flush()

    staff = Staff(
        code="G28-5-S1",
        name="G28-5 稲毛 staff",
        sex="female",
        role="staff",
        status="active",
        primary_office_id=inage.id,
    )
    db.add(staff)
    await db.flush()
    for wd in range(7):
        db.add(StaffShift(staff_id=staff.id, weekday=wd, is_on=True))

    # 同曜日 (= 水曜 weekday=2) に 稲毛 A (visits=3) と 稲毛 D (visits=0) を用意
    course_a = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=2,
        code="A",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=None,
        office_id=inage.id,
    )
    course_d_empty = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=2,
        code="D",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=None,
        office_id=inage.id,
    )
    db.add_all([course_a, course_d_empty])
    await db.flush()
    for i, hour in enumerate([9, 10, 11]):
        await _seed_visit_for_course(
            db,
            course_id=course_a.id,
            patient_code=f"G28-5-A-{i}",
            weekday=2,
            start_hour=hour,
        )
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
    assert refreshed[course_a.id].assigned_staff_id == staff.id, (
        f"稲毛 A (visits=3) に staff が割当されていない (Phase G-28 fix 不発 — "
        f"staff が空 D コースに取られた可能性): "
        f"got={refreshed[course_a.id].assigned_staff_id}, expected={staff.id}"
    )
    assert refreshed[course_d_empty.id].assigned_staff_id is None, (
        f"稲毛 D (visits=0) に staff が割当されている (Phase G-28 fix 不発): "
        f"got={refreshed[course_d_empty.id].assigned_staff_id}, expected=None"
    )


# ---------------------------------------------------------------------------
# 6) H1 fix: assign() の W25 fix 経路で 0-visits + assigned_staff_id 持ちコースを
#    fixed_staff_by_course に再注入しない (= staff_pool を圧迫しない)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assign_already_assigned_empty_course_not_locked(db) -> None:
    """G-28 H1 fix: 0-visits + assigned_staff_id 持ちの staff_assigned コースが
    W25 fix 経路 (``already_assigned_stmt``) で再注入されず staff_pool を
    圧迫しないことを検証.

    本番再現シナリオ (2026-W21):
      - 過去の自動割付で M2 (visits=0) に宇田川を assign 済の状態のまま残っている
      - 再実行時に W25 fix が空 M2 を ``fixed_staff_by_course`` に注入
      - 結果: 宇田川が他コースに使えず NULL 化
    修正後:
      - M2 (visits=0) は W25 fix で skip → 宇田川は visits ありの稲毛 A に流れる
    """
    inage = Office(name="G28-6 稲毛事業所", lat=35.6383, lng=140.1041)
    db.add(inage)
    await db.flush()

    udagawa = Staff(
        code="G28-6-S1",
        name="G28-6 宇田川",
        sex="female",
        role="staff",
        status="active",
        primary_office_id=inage.id,
    )
    db.add(udagawa)
    await db.flush()
    for wd in range(7):
        db.add(StaffShift(staff_id=udagawa.id, weekday=wd, is_on=True))

    # 同曜日 (月 = weekday=0) に
    #   - 稲毛 A (visits=3, course_fixed, assigned_staff_id=None)
    #   - 稲毛 M2 (visits=0, staff_assigned, assigned_staff_id=宇田川) ← W25 fix で再注入される問題対象
    course_a = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_COURSE_FIXED,
        assigned_staff_id=None,
        office_id=inage.id,
    )
    course_m2_empty_locked = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=0,
        code="M2",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=udagawa.id,  # ← 過去のゴミ assign
        office_id=inage.id,
    )
    db.add_all([course_a, course_m2_empty_locked])
    await db.flush()
    for i, hour in enumerate([9, 10, 11]):
        await _seed_visit_for_course(
            db,
            course_id=course_a.id,
            patient_code=f"G28-6-A-{i}",
            weekday=0,
            start_hour=hour,
        )
    # course_m2_empty_locked には visit を seed しない (visits=0 を維持)
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
    assert refreshed[course_a.id].assigned_staff_id == udagawa.id, (
        f"稲毛 A (visits=3) に宇田川が流れていない (G-28 H1 fix 不発 — "
        f"W25 fix 経路で空 M2 が fixed_staff_by_course に再注入され宇田川がロックされた可能性): "
        f"got={refreshed[course_a.id].assigned_staff_id}, expected={udagawa.id}"
    )
