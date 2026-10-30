"""W37 Phase 2-B: Layer 3 が 2 visit (visit_group_id 共有) ペアに
**異なる staff** を割り当てることを検証.

Phase 2-B では Layer 1 が requires_multiple_staff=True 患者に対して
slot 0/1 を 2 visit (visit_group_id 共有, 異なる course_id) に展開する.
Layer 3 は曜日内ハンガリアンで「1 staff = 1 course / day」を保証するため,
同 weekday の course A と course B には異なる staff が割り当たる. その結果,
visit_group_id が共通の 2 visit にも自動的に異なる staff が紐付く.

このテストは Layer 1 → Layer 3 の連結ではなく, Layer 3 単体 (=visits を
事前に作っておいて _persist を叩く) で「partner と primary が同 staff になる
バグが無い」ことを直接検証する.
"""

from __future__ import annotations

import uuid
from datetime import date, time
from uuid import UUID

import pytest
from sqlalchemy import select

from app.models import (
    Course,
    Office,
    Patient,
    Staff,
    StaffShift,
    Visit,
    VisitStaffAssignment,
)
from app.models.course import COURSE_STATUS_COURSE_FIXED
from app.models.visit import VISIT_STATUS_PLANNED
from app.services.scheduling.layer3_assignment import (
    Layer3Assigner,
    StaffAssignment,
)

TEST_ISO_YEAR = 2026
TEST_ISO_WEEK = 19
TEST_WEEK_MONDAY = date(2026, 5, 4)


async def _make_office_and_staff(db, *, n_staff: int = 2) -> tuple[Office, list[Staff]]:
    office = Office(name="W37-L3-事業所", lat=35.65, lng=140.10)
    db.add(office)
    await db.flush()
    staffs: list[Staff] = []
    for i in range(n_staff):
        s = Staff(
            code=f"W37S{i + 1}",
            name=f"S{i + 1} スタッフ",
            sex="female",
            role="staff",
            status="active",
            primary_office_id=office.id,
        )
        db.add(s)
        await db.flush()
        db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True))
        staffs.append(s)
    await db.flush()
    return office, staffs


async def _make_patient(db, *, code: str, requires_multiple: bool = True) -> Patient:
    p = Patient(
        code=code,
        name=f"L3-W37 {code}",
        status="active",
        lat=35.65,
        lng=140.10,
        requires_multiple_staff=requires_multiple,
    )
    db.add(p)
    await db.flush()
    return p


async def _make_course(db, *, weekday: int, code: str, office_id: UUID) -> Course:
    c = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=weekday,
        code=code,
        course_status=COURSE_STATUS_COURSE_FIXED,
        office_id=office_id,
    )
    db.add(c)
    await db.flush()
    return c


async def _make_visit(
    db,
    *,
    patient_id: UUID,
    course_id: UUID,
    visit_group_id: UUID | None,
    visit_date: date = TEST_WEEK_MONDAY,
    required_staff_count: int = 2,
) -> Visit:
    v = Visit(
        patient_id=patient_id,
        visit_date=visit_date,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=required_staff_count,
        visit_group_id=visit_group_id,
        course_id=course_id,
    )
    db.add(v)
    await db.flush()
    return v


# ---------------------------------------------------------------------------
# 1) 2 visit が visit_group_id 共有 → 異なる staff 割当 (同一 staff バグなし)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_two_grouped_visits_get_distinct_staff(db) -> None:
    """visit_group_id 共有の 2 visit (A, B course) → 各 visit の staff 集合は
    {primary_A, primary_B} = 2 名 (同一 staff にならない)."""
    office, staffs = await _make_office_and_staff(db, n_staff=2)
    s1, s2 = staffs[0], staffs[1]

    course_a = await _make_course(db, weekday=0, code="A", office_id=office.id)
    course_b = await _make_course(db, weekday=0, code="B", office_id=office.id)
    patient = await _make_patient(db, code="W37-L3-01", requires_multiple=True)

    group_id = uuid.uuid4()
    visit_a = await _make_visit(
        db,
        patient_id=patient.id,
        course_id=course_a.id,
        visit_group_id=group_id,
    )
    visit_b = await _make_visit(
        db,
        patient_id=patient.id,
        course_id=course_b.id,
        visit_group_id=group_id,
    )
    await db.commit()

    # Layer 3 は曜日内 Hungarian で 1 staff/day を強制するので、course A と B に
    # 異なる staff が割り当たる (= s1 と s2). このテストでは _persist を直叩き.
    assigner = Layer3Assigner()
    await assigner._persist(
        db,
        [
            StaffAssignment(weekday=0, course_code="A", course_id=course_a.id, staff_id=s1.id),
            StaffAssignment(weekday=0, course_code="B", course_id=course_b.id, staff_id=s2.id),
        ],
    )
    await db.commit()

    rows_a = list(
        await db.scalars(
            select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == visit_a.id)
        )
    )
    rows_b = list(
        await db.scalars(
            select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == visit_b.id)
        )
    )

    # それぞれ 2 行 (primary + secondary)
    assert len(rows_a) == 2, f"visit_a の visit_staff_assignments は 2 行のはず, got {len(rows_a)}"
    assert len(rows_b) == 2, f"visit_b の visit_staff_assignments は 2 行のはず, got {len(rows_b)}"

    staff_set_a = {r.staff_id for r in rows_a}
    staff_set_b = {r.staff_id for r in rows_b}

    # 同 staff バグ防止: visit_a / visit_b の staff 集合は s1, s2 の 2 名で構成され、
    # 同一 staff の重複は無い.
    assert staff_set_a == {s1.id, s2.id}
    assert staff_set_b == {s1.id, s2.id}
    # visit ごとに primary は別人
    refreshed_a = await db.scalar(select(Visit).where(Visit.id == visit_a.id))
    refreshed_b = await db.scalar(select(Visit).where(Visit.id == visit_b.id))
    assert refreshed_a.primary_staff_id != refreshed_b.primary_staff_id


# ---------------------------------------------------------------------------
# 2) 単一 visit (group なし) は通常の 1 staff 割当 (regression)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_single_visit_without_group_id_one_staff(db) -> None:
    """visit_group_id=None の場合は 1 staff 割当 (W37 の前後で挙動変わらない)."""
    office, staffs = await _make_office_and_staff(db, n_staff=1)
    s1 = staffs[0]

    course = await _make_course(db, weekday=0, code="A", office_id=office.id)
    patient = await _make_patient(db, code="W37-L3-02", requires_multiple=False)
    visit = await _make_visit(
        db,
        patient_id=patient.id,
        course_id=course.id,
        visit_group_id=None,
        required_staff_count=1,
    )
    await db.commit()

    assigner = Layer3Assigner()
    await assigner._persist(
        db,
        [StaffAssignment(weekday=0, course_code="A", course_id=course.id, staff_id=s1.id)],
    )
    await db.commit()

    rows = list(
        await db.scalars(
            select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == visit.id)
        )
    )
    assert len(rows) == 1
    assert rows[0].staff_id == s1.id


# ---------------------------------------------------------------------------
# 3) partner visit が deleted (片方欠け状態) → primary のみ割当 (フォールバック)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_with_deleted_partner_falls_back_to_primary_only(db) -> None:
    """visit_group_id 共有のはずの partner visit が soft-delete 済みの場合、
    Layer 3 は primary のみ 1 staff 割当して継続する (= 片方欠けても落ちない)."""
    from datetime import datetime as dt

    office, staffs = await _make_office_and_staff(db, n_staff=2)
    s1, s2 = staffs[0], staffs[1]

    course_a = await _make_course(db, weekday=0, code="A", office_id=office.id)
    course_b = await _make_course(db, weekday=0, code="B", office_id=office.id)
    patient = await _make_patient(db, code="W37-L3-03", requires_multiple=True)

    group_id = uuid.uuid4()
    visit_a = await _make_visit(
        db,
        patient_id=patient.id,
        course_id=course_a.id,
        visit_group_id=group_id,
    )
    visit_b = await _make_visit(
        db,
        patient_id=patient.id,
        course_id=course_b.id,
        visit_group_id=group_id,
    )
    # partner を soft-delete
    visit_b.deleted_at = dt.now()
    await db.commit()

    assigner = Layer3Assigner()
    await assigner._persist(
        db,
        [
            StaffAssignment(weekday=0, course_code="A", course_id=course_a.id, staff_id=s1.id),
            StaffAssignment(weekday=0, course_code="B", course_id=course_b.id, staff_id=s2.id),
        ],
    )
    await db.commit()

    rows_a = list(
        await db.scalars(
            select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == visit_a.id)
        )
    )
    # partner が deleted → secondary 解決できず primary のみ 1 行
    assert len(rows_a) == 1
    assert rows_a[0].staff_id == s1.id

    # visit_b は deleted なので assignments 行は無い (DELETE 対象にも含まれない;
    # _persist は status=planned かつ deleted_at IS NULL のみを対象とする)
    rows_b = list(
        await db.scalars(
            select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == visit_b.id)
        )
    )
    assert rows_b == []


# ---------------------------------------------------------------------------
# 4) 2 名で訪問の 1 行 (本番の形・visit_group_id なし): 2 人目 = slot 1 のコースの担当
# ---------------------------------------------------------------------------


async def _single_row_pair_setup(db, code: str):
    """A/B コース (ひな形つき)・固定訪問 slot 0=A / slot 1=B・A に 2 名の訪問 1 行."""
    from app.models.course_template import CourseTemplate
    from app.models.patient_fixed_visit import PatientFixedVisit

    office, staffs = await _make_office_and_staff(db, n_staff=2)
    courses: list[Course] = []
    for label in ("A", "B"):
        tpl = CourseTemplate(office_id=office.id, label=label)
        db.add(tpl)
        await db.flush()
        c = await _make_course(db, weekday=0, code=label, office_id=office.id)
        c.template_id = tpl.id
        courses.append(c)
    patient = await _make_patient(db, code=code)
    for slot, c in enumerate(courses):
        db.add(
            PatientFixedVisit(
                patient_id=patient.id,
                mode="normal",
                weekday=0,
                start_time=time(9, 0),
                duration_min=60,
                slot_index=slot,
                course_template_id=c.template_id,
            )
        )
    visit = await _make_visit(
        db, patient_id=patient.id, course_id=courses[0].id, visit_group_id=None
    )
    await db.commit()
    return staffs, courses, visit


async def _vsa_staff(db, visit_id: UUID) -> set[UUID]:
    return set(
        (
            await db.scalars(
                select(VisitStaffAssignment.staff_id).where(
                    VisitStaffAssignment.visit_id == visit_id
                )
            )
        ).all()
    )


@pytest.mark.asyncio
async def test_persist_single_row_pair_secondary_from_slot1_course(db) -> None:
    """1 行の 2 名で訪問: primary = A の担当・secondary = B (slot 1) の担当・VSA も 2 人."""
    (s1, s2), (ca, cb), visit = await _single_row_pair_setup(db, "W37-L3-R1")

    await Layer3Assigner()._persist(
        db,
        [
            StaffAssignment(weekday=0, course_code="A", course_id=ca.id, staff_id=s1.id),
            StaffAssignment(weekday=0, course_code="B", course_id=cb.id, staff_id=s2.id),
        ],
    )
    await db.commit()
    await db.refresh(visit)

    assert visit.primary_staff_id == s1.id
    assert visit.secondary_staff_id == s2.id
    assert await _vsa_staff(db, visit.id) == {s1.id, s2.id}


@pytest.mark.asyncio
async def test_persist_partner_course_only_updates_secondary(db) -> None:
    """2 人目のコース (B) だけ担当が決まった → 訪問の 2 人目と VSA だけそろう (1 人目は不変)."""
    (s1, s2), (ca, cb), visit = await _single_row_pair_setup(db, "W37-L3-R2")
    visit.primary_staff_id = s1.id
    db.add(VisitStaffAssignment(visit_id=visit.id, staff_id=s1.id))
    await db.commit()

    await Layer3Assigner()._persist(
        db, [StaffAssignment(weekday=0, course_code="B", course_id=cb.id, staff_id=s2.id)]
    )
    await db.commit()
    await db.refresh(visit)

    assert visit.primary_staff_id == s1.id
    assert visit.secondary_staff_id == s2.id
    assert await _vsa_staff(db, visit.id) == {s1.id, s2.id}


@pytest.mark.asyncio
async def test_persist_single_row_pair_same_staff_leaves_secondary_empty(db) -> None:
    """2 人目のコースの担当が 1 人目と同じ人 → 2 人目は空 (足りない扱い)."""
    (s1, _s2), (ca, cb), visit = await _single_row_pair_setup(db, "W37-L3-R3")

    await Layer3Assigner()._persist(
        db,
        [
            StaffAssignment(weekday=0, course_code="A", course_id=ca.id, staff_id=s1.id),
            StaffAssignment(weekday=0, course_code="B", course_id=cb.id, staff_id=s1.id),
        ],
    )
    await db.commit()
    await db.refresh(visit)

    assert visit.primary_staff_id == s1.id
    assert visit.secondary_staff_id is None
    assert await _vsa_staff(db, visit.id) == {s1.id}


@pytest.mark.asyncio
async def test_persist_single_row_pair_without_slot1_keeps_secondary(db) -> None:
    """固定訪問に slot 1 が無い 1 行 (カイポケ取込など) は今の 2 人目を残す."""
    office, (s1, s2) = await _make_office_and_staff(db, n_staff=2)
    ca = await _make_course(db, weekday=0, code="A", office_id=office.id)
    patient = await _make_patient(db, code="W37-L3-R4")
    visit = await _make_visit(db, patient_id=patient.id, course_id=ca.id, visit_group_id=None)
    visit.secondary_staff_id = s2.id
    await db.commit()

    await Layer3Assigner()._persist(
        db, [StaffAssignment(weekday=0, course_code="A", course_id=ca.id, staff_id=s1.id)]
    )
    await db.commit()
    await db.refresh(visit)

    assert visit.primary_staff_id == s1.id
    assert visit.secondary_staff_id == s2.id
    assert await _vsa_staff(db, visit.id) == {s1.id, s2.id}


@pytest.mark.asyncio
async def test_course_targets_count_single_row_pair_in_partner_course(db) -> None:
    """担当決め (Layer 3) の対象: 2 人目のコース B にも 1 行の 2 名で訪問を数える.

    B に自分の訪問が無くても空コースとして落とさない (2 人目の担当が決まらなくなる).
    """
    _staffs, (ca, cb), visit = await _single_row_pair_setup(db, "W37-L3-R5")

    targets = await Layer3Assigner()._load_course_targets(
        db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK
    )
    by_id = {t.course_id: t for t in targets}
    assert ca.id in by_id and cb.id in by_id
    assert by_id[cb.id].patient_ids == [visit.patient_id]
    assert len(by_id[cb.id].visits) == 1
