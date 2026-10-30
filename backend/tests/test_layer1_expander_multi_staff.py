"""W37 Phase 2-B: Layer 1 expander multi-staff (slot 0/1) のテスト.

検証観点:
  1. requires_multiple_staff=True + slot 0/1 両方あり
     → 1 visit 生成 (2026-10-30 本番の形): required_staff_count=2・visit_group_id なし・
       course_id は slot 0 のコース. slot 1 のコースも確保する.
  2. requires_multiple_staff=True + slot 1 欠け
     → 1 visit のみ生成 + 警告ログ.
  3. requires_multiple_staff=False
     → 既存挙動: slot 0 のみ 1 visit (slot 1 行があっても無視).
  4. manual visit が同 (patient, date, time) に存在
     → スキップ.
  5. 冪等再実行時に旧 visit が soft-delete され、新しい 1 行ができる.

本番と同じ partial UNIQUE (uq_visits_pds_group_active) を張って流す (2 行作ると落ちる).
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

pytestmark = pytest.mark.usefixtures("visits_pds_unique_index")


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
# 1) requires_multiple_staff=True + slot 0/1 両方あり → 1 visit (2 名で 1 行)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_staff_both_slots_generates_one_pair_visit(db) -> None:
    """slot 0/1 両方ある複数スタッフ患者 → 1 visit (required_staff_count=2・組 id なし)."""
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

    assert result.visits_created_count == 1
    rows = list(
        await db.scalars(
            select(Visit).where(Visit.patient_id == patient.id, Visit.deleted_at.is_(None))
        )
    )
    assert len(rows) == 1
    v = rows[0]
    assert (v.visit_date, v.start_time, v.end_time) == (TEST_WEEK_MONDAY, time(9, 0), time(10, 0))
    assert v.visit_group_id is None
    assert v.required_staff_count == 2
    # 担当は Layer 3 が付ける (ここでは付けない)
    assert v.primary_staff_id is None
    assert v.secondary_staff_id is None


@pytest.mark.asyncio
async def test_multi_staff_both_slots_visit_in_slot0_course_and_slot1_course_exists(db) -> None:
    """slot 0/1 で異なる course_template_id を指定すると、訪問は slot 0 のコースに入り、
    slot 1 のコース (2 人目のコース) もその週に確保される."""
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

    assert result.visits_created_count == 1
    rows = list(
        await db.scalars(
            select(Visit).where(Visit.patient_id == patient.id, Visit.deleted_at.is_(None))
        )
    )
    assert len(rows) == 1
    course = await db.scalar(select(Course).where(Course.id == rows[0].course_id))
    assert course is not None and course.template_id == tpl_a.id

    # 2 人目のコース (tpl_b の同じ週・曜日) も確保されている
    week_courses = list(
        await db.scalars(
            select(Course).where(
                Course.iso_year == TEST_ISO_YEAR,
                Course.iso_week == TEST_ISO_WEEK,
                Course.weekday == 2,
            )
        )
    )
    assert {c.template_id for c in week_courses} >= {tpl_a.id, tpl_b.id}

    # 2 人目のコースは pair_visit で引ける (Layer 3 が secondary の担当を引く道)
    from app.services.scheduling.pair_visit import load_pair_partner_courses

    partners = await load_pair_partner_courses(db, rows)
    assert partners[rows[0].id].template_id == tpl_b.id


# ---------------------------------------------------------------------------
# 2) requires_multiple_staff=True + slot 1 欠け → 1 visit + 警告
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_staff_only_slot0_skips_visit_and_records_unplaced(db, caplog) -> None:
    """W37 hotfix C-2: slot 0 のみあり slot 1 欠け → visit 生成スキップ +
    unplaced_multi_staff_patients に記録 + 警告ログ.

    旧挙動: 1 visit を required_staff_count=2 で生成 (silent 1-staff 化).
    新挙動: visit 生成せず保留扱い (Layer 3 で誤って 1 名運用化されないため).
    """
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

    # visit は 0 件 (= 保留扱い)
    assert result.visits_created_count == 0
    rows = list(
        await db.scalars(
            select(Visit).where(Visit.patient_id == patient.id, Visit.deleted_at.is_(None))
        )
    )
    assert len(rows) == 0

    # unplaced_multi_staff_patients に該当行が含まれる
    assert len(result.unplaced_multi_staff_patients) == 1
    entry = result.unplaced_multi_staff_patients[0]
    assert entry.patient_id == patient.id
    assert entry.weekday == 0
    assert entry.reason == "missing_slot_1"

    # 警告ログが出る
    warning_logs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("slot 1 missing" in m for m in warning_logs), (
        f"slot 1 missing 警告が出るはず, got {warning_logs}"
    )


@pytest.mark.asyncio
async def test_multi_staff_only_slot1_skips_visit_and_records_unplaced(db, caplog) -> None:
    """W37 hotfix C-2: slot 0 が無く slot 1 のみ → visit 生成スキップ +
    unplaced_multi_staff_patients に記録 + 警告ログ."""
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

    # visit は 0 件
    assert result.visits_created_count == 0
    rows = list(
        await db.scalars(
            select(Visit).where(Visit.patient_id == patient.id, Visit.deleted_at.is_(None))
        )
    )
    assert len(rows) == 0

    # unplaced 記録
    assert len(result.unplaced_multi_staff_patients) == 1
    entry = result.unplaced_multi_staff_patients[0]
    assert entry.patient_id == patient.id
    assert entry.weekday == 4
    assert entry.reason == "missing_slot_0"

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
# 4) manual visit 衝突 → スキップ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_staff_manual_conflict_skips_both_visits(db) -> None:
    """manual visit が同 (patient, date, time) に存在する場合、2 名の訪問を作らない."""
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

    # auto はスキップ → visits_created は 0
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
# 5) 冪等性: 旧 visit が soft-delete されてから新しい 1 行が INSERT される
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_staff_idempotent_re_run_soft_deletes_pair(db) -> None:
    """冪等再実行時に旧 visit が soft-delete され, 新しい 1 行が INSERT される."""
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
    assert res1.visits_created_count == 1
    first_ids = {vc.visit_id for vc in res1.visits_created}

    # 2 回目 — 旧 1 行は soft-delete, 新 1 行が INSERT される (UNIQUE に当たらない)
    res2 = await expander.expand_week(db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK)
    await db.commit()
    assert res2.visits_created_count == 1
    second_ids = {vc.visit_id for vc in res2.visits_created}

    # 新旧で ID 集合は完全に分離する (重複しない)
    assert first_ids.isdisjoint(second_ids)

    all_rows = list(await db.scalars(select(Visit).where(Visit.patient_id == patient.id)))
    assert len(all_rows) == 2
    deleted = [v for v in all_rows if v.deleted_at is not None]
    active = [v for v in all_rows if v.deleted_at is None]
    assert len(deleted) == 1
    assert len(active) == 1
    assert active[0].required_staff_count == 2
    assert active[0].visit_group_id is None


@pytest.mark.asyncio
async def test_multi_staff_active_visits_have_auto_source(db) -> None:
    """生成された 2 名の訪問 (1 行) は source=auto の標準仕様."""
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

    assert result.visits_created_count == 1
    rows = list(
        await db.scalars(
            select(Visit).where(Visit.patient_id == patient.id, Visit.deleted_at.is_(None))
        )
    )
    assert len(rows) == 1
    assert all(r.source == LAYER1_VISIT_SOURCE for r in rows)
    assert all(r.status == VISIT_STATUS_PLANNED for r in rows)


# ---------------------------------------------------------------------------
# 6) 試験の DB にも本番の partial UNIQUE が効いている (2 行の組を作ると落ちる)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pds_unique_index_rejects_two_rows_sharing_group_id(db) -> None:
    """本番 (0027) と同じ索引: 同じ患者・日・時刻・組 id の 2 行は IntegrityError."""
    import uuid

    from sqlalchemy.exc import IntegrityError

    patient = await _make_patient(db, code="W37-M09", requires_multiple_staff=True)
    gid = uuid.uuid4()
    for _ in range(2):
        db.add(
            Visit(
                patient_id=patient.id,
                visit_date=TEST_WEEK_MONDAY,
                start_time=time(9, 0),
                end_time=time(10, 0),
                type="regular",
                status=VISIT_STATUS_PLANNED,
                source="auto",
                required_staff_count=2,
                visit_group_id=gid,
            )
        )
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()
