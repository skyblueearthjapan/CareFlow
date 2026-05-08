"""W37 Phase 2-B: Layer 1 expander multi-staff (slot 0/1) のテスト.

検証観点:
  1. requires_multiple_staff=True + slot 0/1 両方あり
     → 2 visit 生成、visit_group_id 共有、course_id 異なる、required_staff_count=2.
  2. requires_multiple_staff=True + slot 1 欠け
     → 1 visit のみ生成 + 警告ログ.
  3. requires_multiple_staff=False
     → 既存挙動: slot 0 のみ 1 visit (slot 1 行があっても無視).
  4. manual visit が同 (patient, date, time) に存在
     → 2 visit 共にスキップ (片方だけスキップは不可).
  5. soft-delete ペア: 冪等再実行時に旧 2 visit が ペアごと soft-delete される.
"""

from __future__ import annotations

from datetime import date, time

import pytest
from sqlalchemy import select

from app.models import Patient, Visit
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.visit import VISIT_STATUS_PLANNED
from app.services.scheduling.layer1_expander import (
    LAYER1_VISIT_SOURCE,
    Layer1Expander,
)

# ISO week 19 of 2026 — Monday is 2026-05-04.
TEST_ISO_YEAR = 2026
TEST_ISO_WEEK = 19
TEST_WEEK_MONDAY = date(2026, 5, 4)


async def _make_patient(
    db,
    *,
    code: str,
    name: str | None = None,
    requires_multiple_staff: bool = False,
) -> Patient:
    p = Patient(
        code=code,
        name=name or f"利用者 {code}",
        status="active",
        requires_multiple_staff=requires_multiple_staff,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


# ---------------------------------------------------------------------------
# 1) requires_multiple_staff=True + slot 0/1 両方あり → 2 visit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_staff_both_slots_generates_two_visits_with_shared_group_id(db) -> None:
    """slot 0/1 両方ある複数スタッフ患者 → 2 visit 生成 + visit_group_id 共有."""
    expander = Layer1Expander()
    patient = await _make_patient(db, code="W37-M01", requires_multiple_staff=True)

    # slot 0: Mon 09:00 60min
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=0,
            start_time=time(9, 0),
            duration_min=60,
            slot_index=0,
        )
    )
    # slot 1: Mon 09:00 60min (同時刻 = 2 名同時)
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=0,
            start_time=time(9, 0),
            duration_min=60,
            slot_index=1,
        )
    )
    await db.commit()

    result = await expander.expand_week(db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK)
    await db.commit()

    assert result.visits_created_count == 2
    rows = list(
        await db.scalars(
            select(Visit).where(Visit.patient_id == patient.id, Visit.deleted_at.is_(None))
        )
    )
    assert len(rows) == 2
    # 同じ visit_date / start_time / end_time
    assert {r.visit_date for r in rows} == {TEST_WEEK_MONDAY}
    assert {r.start_time for r in rows} == {time(9, 0)}
    assert {r.end_time for r in rows} == {time(10, 0)}
    # visit_group_id 共有
    group_ids = {r.visit_group_id for r in rows}
    assert len(group_ids) == 1
    assert next(iter(group_ids)) is not None
    # required_staff_count=2
    assert all(r.required_staff_count == 2 for r in rows)


@pytest.mark.asyncio
async def test_multi_staff_both_slots_have_distinct_course_ids(db) -> None:
    """slot 0/1 で異なる course_template_id を指定すると、生成される 2 visit は
    異なる course_id を持つ (= 別コースに紐付く)."""
    from app.models.course import Course
    from app.models.course_template import CourseTemplate
    from app.models.office import Office

    expander = Layer1Expander()

    office = Office(name="W37事業所")
    db.add(office)
    await db.commit()
    await db.refresh(office)

    tpl_a = CourseTemplate(office_id=office.id, label="A")
    tpl_b = CourseTemplate(office_id=office.id, label="B")
    db.add_all([tpl_a, tpl_b])
    await db.commit()
    await db.refresh(tpl_a)
    await db.refresh(tpl_b)

    patient = await _make_patient(db, code="W37-M02", requires_multiple_staff=True)
    patient.primary_office_id = office.id
    await db.commit()
    await db.refresh(patient)

    # slot 0 → tpl_a, slot 1 → tpl_b
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=2,  # Wed
            start_time=time(10, 0),
            duration_min=60,
            slot_index=0,
            course_template_id=tpl_a.id,
        )
    )
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=2,
            start_time=time(10, 0),
            duration_min=60,
            slot_index=1,
            course_template_id=tpl_b.id,
        )
    )
    await db.commit()

    result = await expander.expand_week(db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK)
    await db.commit()

    assert result.visits_created_count == 2
    rows = list(
        await db.scalars(
            select(Visit).where(Visit.patient_id == patient.id, Visit.deleted_at.is_(None))
        )
    )
    course_ids = [r.course_id for r in rows]
    assert all(cid is not None for cid in course_ids)
    assert len(set(course_ids)) == 2, f"2 visit は異なる course_id を持つはず, got {course_ids}"

    # 各 course は対応する template にひもづく
    courses = list(await db.scalars(select(Course).where(Course.id.in_(course_ids))))
    template_ids = {c.template_id for c in courses}
    assert template_ids == {tpl_a.id, tpl_b.id}


# ---------------------------------------------------------------------------
# 2) requires_multiple_staff=True + slot 1 欠け → 1 visit + 警告
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_staff_only_slot0_generates_one_visit_with_warning(db, caplog) -> None:
    """slot 0 のみあり slot 1 欠け → 1 visit のみ生成 + 警告ログ."""
    import logging

    expander = Layer1Expander()
    patient = await _make_patient(db, code="W37-M03", requires_multiple_staff=True)

    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=0,
            start_time=time(9, 0),
            duration_min=60,
            slot_index=0,
        )
    )
    await db.commit()

    with caplog.at_level(logging.WARNING, logger="app.services.scheduling.layer1_expander"):
        result = await expander.expand_week(db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK)
        await db.commit()

    assert result.visits_created_count == 1
    rows = list(
        await db.scalars(
            select(Visit).where(Visit.patient_id == patient.id, Visit.deleted_at.is_(None))
        )
    )
    assert len(rows) == 1
    # required_staff_count は 2 (フラグに従う)
    assert rows[0].required_staff_count == 2
    # visit_group_id は None (1 visit しかないので group は不要)
    assert rows[0].visit_group_id is None

    warning_logs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("slot 1 missing" in m for m in warning_logs), (
        f"slot 1 missing 警告が出るはず, got {warning_logs}"
    )


@pytest.mark.asyncio
async def test_multi_staff_only_slot1_generates_one_visit_with_warning(db, caplog) -> None:
    """異常データ: slot 0 が無く slot 1 のみあり → 1 visit + 警告."""
    import logging

    expander = Layer1Expander()
    patient = await _make_patient(db, code="W37-M04", requires_multiple_staff=True)

    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=4,  # Fri
            start_time=time(13, 0),
            duration_min=45,
            slot_index=1,
        )
    )
    await db.commit()

    with caplog.at_level(logging.WARNING, logger="app.services.scheduling.layer1_expander"):
        result = await expander.expand_week(db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK)
        await db.commit()

    assert result.visits_created_count == 1
    rows = list(
        await db.scalars(
            select(Visit).where(Visit.patient_id == patient.id, Visit.deleted_at.is_(None))
        )
    )
    assert len(rows) == 1
    assert rows[0].weekday() == 4 if False else rows[0].visit_date.weekday() == 4
    warning_logs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("slot 0 missing" in m for m in warning_logs), (
        f"slot 0 missing 警告が出るはず, got {warning_logs}"
    )


# ---------------------------------------------------------------------------
# 3) requires_multiple_staff=False → 既存挙動 (regression)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_staff_uses_only_slot0_ignores_slot1(db) -> None:
    """非・複数スタッフ患者は slot_index=0 のみ採用.

    誤って slot_index=1 行が残っていても無視する (regression)."""
    expander = Layer1Expander()
    patient = await _make_patient(db, code="W37-M05", requires_multiple_staff=False)

    # slot 0
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=1,  # Tue
            start_time=time(11, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    # slot 1 — 誤って残ったデータと想定. 無視されるはず.
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=1,
            start_time=time(11, 0),
            duration_min=30,
            slot_index=1,
        )
    )
    await db.commit()

    result = await expander.expand_week(db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK)
    await db.commit()

    # 1 visit のみ生成されているはず
    assert result.visits_created_count == 1
    rows = list(
        await db.scalars(
            select(Visit).where(Visit.patient_id == patient.id, Visit.deleted_at.is_(None))
        )
    )
    assert len(rows) == 1
    assert rows[0].required_staff_count == 1
    assert rows[0].visit_group_id is None


# ---------------------------------------------------------------------------
# 4) manual visit 衝突 → 2 visit 共にスキップ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_staff_manual_conflict_skips_both_visits(db) -> None:
    """manual visit が同 (patient, date, time) に存在する場合、
    2 visit ペアの両方ともスキップする (片方残しは禁止)."""
    expander = Layer1Expander()
    patient = await _make_patient(db, code="W37-M06", requires_multiple_staff=True)

    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=0,  # Mon
            start_time=time(9, 0),
            duration_min=60,
            slot_index=0,
        )
    )
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=0,
            start_time=time(9, 0),
            duration_min=60,
            slot_index=1,
        )
    )
    # manual visit を事前投入
    manual_visit = Visit(
        patient_id=patient.id,
        visit_date=TEST_WEEK_MONDAY,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="manual",
        required_staff_count=1,
    )
    db.add(manual_visit)
    await db.commit()
    await db.refresh(manual_visit)
    manual_id = manual_visit.id

    result = await expander.expand_week(db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK)
    await db.commit()

    # auto 2 visit 共にスキップ → visits_created は 0
    assert result.visits_created_count == 0

    # active な visit は manual のみ
    active = list(
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == patient.id,
                Visit.deleted_at.is_(None),
            )
        )
    )
    assert len(active) == 1
    assert active[0].id == manual_id
    assert active[0].source == "manual"


# ---------------------------------------------------------------------------
# 5) 冪等性: 旧 2 visit ペアが soft-delete されてから新 2 visit が INSERT される
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_staff_idempotent_re_run_soft_deletes_pair(db) -> None:
    """冪等再実行時に旧 2 visit (ペア) が両方とも soft-delete され, 新 2 visit が INSERT される."""
    expander = Layer1Expander()
    patient = await _make_patient(db, code="W37-M07", requires_multiple_staff=True)

    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=2,  # Wed
            start_time=time(14, 0),
            duration_min=60,
            slot_index=0,
        )
    )
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=2,
            start_time=time(14, 0),
            duration_min=60,
            slot_index=1,
        )
    )
    await db.commit()

    # 1 回目
    res1 = await expander.expand_week(db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK)
    await db.commit()
    assert res1.visits_created_count == 2
    first_ids = {vc.visit_id for vc in res1.visits_created}

    # 2 回目 — 旧 2 行は soft-delete, 新 2 行が INSERT される
    res2 = await expander.expand_week(db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK)
    await db.commit()
    assert res2.visits_created_count == 2
    second_ids = {vc.visit_id for vc in res2.visits_created}

    # 新旧で ID 集合は完全に分離する (重複しない)
    assert first_ids.isdisjoint(second_ids)

    # 全行 (含 deleted) を確認: 4 行存在 (2 つは deleted, 2 つは active)
    all_rows = list(await db.scalars(select(Visit).where(Visit.patient_id == patient.id)))
    assert len(all_rows) == 4
    deleted = [v for v in all_rows if v.deleted_at is not None]
    active = [v for v in all_rows if v.deleted_at is None]
    assert len(deleted) == 2
    assert len(active) == 2

    # active な 2 行は同じ visit_group_id を共有する (新ペア)
    active_groups = {v.visit_group_id for v in active}
    assert len(active_groups) == 1
    assert next(iter(active_groups)) is not None

    # deleted 側の 2 行も同じ visit_group_id を共有する (旧ペアが両方とも soft-deleted)
    deleted_groups = {v.visit_group_id for v in deleted}
    assert len(deleted_groups) == 1
    assert next(iter(deleted_groups)) is not None
    # 新旧 group_id は別 UUID
    assert next(iter(active_groups)) != next(iter(deleted_groups))


@pytest.mark.asyncio
async def test_multi_staff_active_visits_have_auto_source(db) -> None:
    """生成された 2 visit は source=auto の標準仕様."""
    expander = Layer1Expander()
    patient = await _make_patient(db, code="W37-M08", requires_multiple_staff=True)

    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=3,  # Thu
            start_time=time(8, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=3,
            start_time=time(8, 0),
            duration_min=30,
            slot_index=1,
        )
    )
    await db.commit()

    result = await expander.expand_week(db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK)
    await db.commit()

    assert result.visits_created_count == 2
    rows = list(
        await db.scalars(
            select(Visit).where(Visit.patient_id == patient.id, Visit.deleted_at.is_(None))
        )
    )
    assert len(rows) == 2
    assert all(r.source == LAYER1_VISIT_SOURCE for r in rows)
    assert all(r.status == VISIT_STATUS_PLANNED for r in rows)
