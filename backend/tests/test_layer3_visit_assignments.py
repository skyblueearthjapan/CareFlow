"""W7-BE4: Layer 3 が visit_staff_assignments に書き込むことを検証.

Codex Must-fix #7 に対応する追加テスト。

前提:
    Layer 3 は ``courses.assigned_staff_id`` の更新だけでなく、v2 の
    正規表現である ``visit_staff_assignments`` テーブルに
    1 visit あたり 1 行 (通常) または 2 行 (2 名体制) を INSERT する。
    Staff の visit 可視性は本テーブル経由で評価されるため、本テーブルへの
    書込が無いと割当後の訪問が Staff から見えなくなる。

検証観点:
    1. 1 名体制 (required_staff_count=1): 1 visit → 1 visit_staff_assignment 行
    2. 2 名体制 (required_staff_count=2 + visit_group_id 共有): 同一 group 内
       2 visits それぞれに primary + secondary の 2 行 (計 4 行)
    3. 既存 assignments の冪等再書込み (DELETE → INSERT)
    4. course の visits 0 件で no-op (= 例外を出さない、行も増えない)
    5. レガシー互換: visits.primary_staff_id / secondary_staff_id 同期更新
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
from app.models.course import (
    COURSE_STATUS_COURSE_FIXED,
    COURSE_STATUS_STAFF_ASSIGNED,
)
from app.models.visit import VISIT_STATUS_PLANNED
from app.services.scheduling.layer3_assignment import (
    Layer3Assigner,
    StaffAssignment,
)

# 月曜 = 2026-05-25 (week 22)
TEST_ISO_YEAR = 2026
TEST_ISO_WEEK = 22
TEST_WEEK_MONDAY = date(2026, 5, 25)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_office_and_staff(
    db,
    *,
    n_staff: int = 2,
    office_name: str = "稲毛事業所",
) -> tuple[Office, list[Staff]]:
    """1 office + N staff を作成し、月曜稼働で StaffShift を入れる."""
    office = Office(name=office_name, lat=35.6383, lng=140.1041)
    db.add(office)
    await db.flush()

    staffs: list[Staff] = []
    for i in range(n_staff):
        s = Staff(
            code=f"S{i + 1}",
            name=f"S{i + 1} 山田",
            sex="female",
            role="staff",
            status="active",
            primary_office_id=office.id,
        )
        db.add(s)
        await db.flush()
        # 月曜稼働
        db.add(StaffShift(staff_id=s.id, weekday=0, is_on=True))
        staffs.append(s)
    await db.flush()
    return office, staffs


async def _make_patient(db, *, code: str) -> Patient:
    p = Patient(
        code=code,
        name=f"L3 {code}",
        status="active",
        lat=35.6383,
        lng=140.1041,
    )
    db.add(p)
    await db.flush()
    return p


async def _make_course(db, *, weekday: int, code: str, office_id: UUID) -> Course:
    """W15-BE-FIXPATTERN: courses.office_id NOT NULL のため office_id 必須."""
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
    visit_date: date = TEST_WEEK_MONDAY,
    required_staff_count: int = 1,
    visit_group_id: UUID | None = None,
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
# 1) 1 名体制: 1 visit → 1 visit_staff_assignment 行
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_single_staff_inserts_one_assignment_row(db) -> None:
    """1 名体制 (required_staff_count=1) のとき 1 visit に対して 1 行."""
    office, staffs = await _make_office_and_staff(db, n_staff=1)
    s1 = staffs[0]

    course = await _make_course(db, weekday=0, code="A", office_id=office.id)
    patient = await _make_patient(db, code="P1")
    visit = await _make_visit(
        db,
        patient_id=patient.id,
        course_id=course.id,
        required_staff_count=1,
    )
    await db.commit()

    assigner = Layer3Assigner()
    await assigner._persist(
        db,
        [
            StaffAssignment(
                weekday=0,
                course_code="A",
                course_id=course.id,
                staff_id=s1.id,
            )
        ],
    )
    await db.commit()

    rows = (
        await db.scalars(
            select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == visit.id)
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].staff_id == s1.id

    # course も staff_assigned に遷移
    refreshed_course = await db.scalar(select(Course).where(Course.id == course.id))
    assert refreshed_course.course_status == COURSE_STATUS_STAFF_ASSIGNED
    assert refreshed_course.assigned_staff_id == s1.id

    # レガシー互換: visit.primary_staff_id 同期
    refreshed_visit = await db.scalar(select(Visit).where(Visit.id == visit.id))
    assert refreshed_visit.primary_staff_id == s1.id
    assert refreshed_visit.secondary_staff_id is None


# ---------------------------------------------------------------------------
# 2) 2 名体制: 同一 visit_group_id を共有する 2 visits
#    → 各 visit に primary + secondary の 2 行 (計 4 行)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_two_person_inserts_primary_and_secondary(db) -> None:
    """2 名体制では同一 group 内の各 visit に primary + secondary の 2 行."""
    office, staffs = await _make_office_and_staff(db, n_staff=2)
    s1, s2 = staffs[0], staffs[1]

    # 2 名体制: 別々の course (A, B) に 1 visit ずつ + 同じ visit_group_id
    course_a = await _make_course(db, weekday=0, code="A", office_id=office.id)
    course_b = await _make_course(db, weekday=0, code="B", office_id=office.id)
    patient = await _make_patient(db, code="P2")

    group_id = uuid.uuid4()
    visit_a = await _make_visit(
        db,
        patient_id=patient.id,
        course_id=course_a.id,
        required_staff_count=2,
        visit_group_id=group_id,
    )
    visit_b = await _make_visit(
        db,
        patient_id=patient.id,
        course_id=course_b.id,
        required_staff_count=2,
        visit_group_id=group_id,
    )
    await db.commit()

    assigner = Layer3Assigner()
    await assigner._persist(
        db,
        [
            StaffAssignment(
                weekday=0,
                course_code="A",
                course_id=course_a.id,
                staff_id=s1.id,
            ),
            StaffAssignment(
                weekday=0,
                course_code="B",
                course_id=course_b.id,
                staff_id=s2.id,
            ),
        ],
    )
    await db.commit()

    # visit_a は staff = {s1 (primary, 自コース), s2 (secondary, partner)}
    rows_a = (
        await db.scalars(
            select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == visit_a.id)
        )
    ).all()
    assert len(rows_a) == 2
    assert {r.staff_id for r in rows_a} == {s1.id, s2.id}

    # visit_b は staff = {s2 (primary, 自コース), s1 (secondary, partner)}
    rows_b = (
        await db.scalars(
            select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == visit_b.id)
        )
    ).all()
    assert len(rows_b) == 2
    assert {r.staff_id for r in rows_b} == {s1.id, s2.id}

    # レガシー互換: 各 visit の primary/secondary
    refreshed_a = await db.scalar(select(Visit).where(Visit.id == visit_a.id))
    refreshed_b = await db.scalar(select(Visit).where(Visit.id == visit_b.id))
    assert refreshed_a.primary_staff_id == s1.id
    assert refreshed_a.secondary_staff_id == s2.id
    assert refreshed_b.primary_staff_id == s2.id
    assert refreshed_b.secondary_staff_id == s1.id


# ---------------------------------------------------------------------------
# 3) 冪等性: 既存 assignments があっても再書込みで重複しない
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_is_idempotent_overwrites_existing_rows(db) -> None:
    """同じ Layer 3 を 2 回流しても visit_staff_assignments は 1 行のまま.

    1 回目: staff_id=s1
    2 回目: staff_id=s2 (= 担当者変更)
    → 行数 1, staff_id=s2 (上書きされている)
    """
    office, staffs = await _make_office_and_staff(db, n_staff=2)
    s1, s2 = staffs[0], staffs[1]

    course = await _make_course(db, weekday=0, code="A", office_id=office.id)
    patient = await _make_patient(db, code="P3")
    visit = await _make_visit(
        db,
        patient_id=patient.id,
        course_id=course.id,
        required_staff_count=1,
    )
    await db.commit()

    assigner = Layer3Assigner()

    # 1 回目: s1 に割当
    await assigner._persist(
        db,
        [
            StaffAssignment(
                weekday=0,
                course_code="A",
                course_id=course.id,
                staff_id=s1.id,
            )
        ],
    )
    await db.commit()

    rows = (
        await db.scalars(
            select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == visit.id)
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].staff_id == s1.id

    # 2 回目: s2 に変更
    await assigner._persist(
        db,
        [
            StaffAssignment(
                weekday=0,
                course_code="A",
                course_id=course.id,
                staff_id=s2.id,
            )
        ],
    )
    await db.commit()

    rows = (
        await db.scalars(
            select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == visit.id)
        )
    ).all()
    # 重複せず、最新の s2 のみ
    assert len(rows) == 1
    assert rows[0].staff_id == s2.id

    refreshed = await db.scalar(select(Visit).where(Visit.id == visit.id))
    assert refreshed.primary_staff_id == s2.id


# ---------------------------------------------------------------------------
# 4) course の visits 0 件 → no-op (course の status は更新されるが行は INSERT
#    されない / 例外も出ない)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_no_visits_is_noop_for_assignments_table(db) -> None:
    """course 配下の planned visits が 0 件のとき visit_staff_assignments
    に行が増えない (course の course_status は更新される)."""
    office, staffs = await _make_office_and_staff(db, n_staff=1)
    s1 = staffs[0]

    course = await _make_course(db, weekday=0, code="A", office_id=office.id)
    # visits は作らない
    await db.commit()

    assigner = Layer3Assigner()
    await assigner._persist(
        db,
        [
            StaffAssignment(
                weekday=0,
                course_code="A",
                course_id=course.id,
                staff_id=s1.id,
            )
        ],
    )
    await db.commit()

    rows = (await db.scalars(select(VisitStaffAssignment))).all()
    assert rows == []

    # course の遷移は実施されている (assigned_staff_id / status / 時刻)
    refreshed = await db.scalar(select(Course).where(Course.id == course.id))
    assert refreshed.course_status == COURSE_STATUS_STAFF_ASSIGNED
    assert refreshed.assigned_staff_id == s1.id
    assert refreshed.staff_assigned_at is not None


# ---------------------------------------------------------------------------
# 5) 空 assignments → no-op (DB へのアクセスもしない)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_empty_assignments_is_noop(db) -> None:
    """assignments=[] のとき早期 return で no-op."""
    assigner = Layer3Assigner()
    await assigner._persist(db, [])
    # 例外が出なければ OK
    rows = (await db.scalars(select(VisitStaffAssignment))).all()
    assert rows == []


# ---------------------------------------------------------------------------
# 6) cancelled / 削除済み visits は対象外
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_skips_non_planned_visits(db) -> None:
    """status != 'planned' の visit には visit_staff_assignment を作らない."""
    office, staffs = await _make_office_and_staff(db, n_staff=1)
    s1 = staffs[0]

    course = await _make_course(db, weekday=0, code="A", office_id=office.id)
    patient = await _make_patient(db, code="P4")

    # planned な visit
    planned = await _make_visit(
        db, patient_id=patient.id, course_id=course.id, required_staff_count=1
    )
    # cancelled な visit
    cancelled = Visit(
        patient_id=patient.id,
        visit_date=TEST_WEEK_MONDAY,
        start_time=time(11, 0),
        end_time=time(12, 0),
        type="regular",
        status="cancelled",
        source="auto",
        required_staff_count=1,
        course_id=course.id,
    )
    db.add(cancelled)
    await db.flush()
    await db.commit()

    assigner = Layer3Assigner()
    await assigner._persist(
        db,
        [
            StaffAssignment(
                weekday=0,
                course_code="A",
                course_id=course.id,
                staff_id=s1.id,
            )
        ],
    )
    await db.commit()

    rows = (await db.scalars(select(VisitStaffAssignment))).all()
    visit_ids = {r.visit_id for r in rows}
    assert planned.id in visit_ids
    assert cancelled.id not in visit_ids
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# 7) P3-① 保護: manual_staff_override=True の visit は再割当で上書きされない
#    (docs/plans/p3-1-staff-substitute-design.md §4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_skips_manual_staff_override_visit(db) -> None:
    """override=True の visit は VSA / primary_staff_id が差替え結果のまま保持される.

    当日欠勤の代替スタッフ提案で s_sub に差し替えた visit を、後続の
    assign-staff-only (course を s1 に割当) が上書きしないことを検証する。
    """
    office, staffs = await _make_office_and_staff(db, n_staff=2)
    s1, s_sub = staffs[0], staffs[1]

    course = await _make_course(db, weekday=0, code="A", office_id=office.id)
    patient = await _make_patient(db, code="P5")
    visit = await _make_visit(
        db,
        patient_id=patient.id,
        course_id=course.id,
        required_staff_count=1,
    )
    # 代替スタッフ提案 apply 済みの状態を再現: override フラグ + 差替え結果
    visit.manual_staff_override = True
    visit.primary_staff_id = s_sub.id
    db.add(VisitStaffAssignment(visit_id=visit.id, staff_id=s_sub.id))
    await db.commit()

    assigner = Layer3Assigner()
    await assigner._persist(
        db,
        [
            StaffAssignment(
                weekday=0,
                course_code="A",
                course_id=course.id,
                staff_id=s1.id,
            )
        ],
    )
    await db.commit()

    # VSA は差替え結果 (s_sub) のまま、s1 で上書きされていない
    rows = (
        await db.scalars(
            select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == visit.id)
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].staff_id == s_sub.id

    # primary_staff_id も保護される
    refreshed_visit = await db.scalar(select(Visit).where(Visit.id == visit.id))
    assert refreshed_visit.primary_staff_id == s_sub.id

    # course の遷移自体は行われる (course は override 対象外)
    refreshed_course = await db.scalar(select(Course).where(Course.id == course.id))
    assert refreshed_course.course_status == COURSE_STATUS_STAFF_ASSIGNED
    assert refreshed_course.assigned_staff_id == s1.id


@pytest.mark.asyncio
async def test_persist_processes_non_override_visit_alongside_override(db) -> None:
    """override=False の visit は従来通り処理される (回帰) — override と混在しても独立."""
    office, staffs = await _make_office_and_staff(db, n_staff=2)
    s1, s_sub = staffs[0], staffs[1]

    course_a = await _make_course(db, weekday=0, code="A", office_id=office.id)
    course_b = await _make_course(db, weekday=0, code="B", office_id=office.id)
    patient = await _make_patient(db, code="P6")

    # visit_a: override 済み (s_sub に差替え・保護対象)
    visit_a = await _make_visit(
        db, patient_id=patient.id, course_id=course_a.id, required_staff_count=1
    )
    visit_a.manual_staff_override = True
    visit_a.primary_staff_id = s_sub.id
    db.add(VisitStaffAssignment(visit_id=visit_a.id, staff_id=s_sub.id))

    # visit_b: 通常 (override なし・処理対象)
    visit_b = await _make_visit(
        db, patient_id=patient.id, course_id=course_b.id, required_staff_count=1
    )
    await db.commit()

    assigner = Layer3Assigner()
    await assigner._persist(
        db,
        [
            StaffAssignment(
                weekday=0, course_code="A", course_id=course_a.id, staff_id=s1.id
            ),
            StaffAssignment(
                weekday=0, course_code="B", course_id=course_b.id, staff_id=s1.id
            ),
        ],
    )
    await db.commit()

    # visit_a は保護 (s_sub のまま)
    rows_a = (
        await db.scalars(
            select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == visit_a.id)
        )
    ).all()
    assert len(rows_a) == 1
    assert rows_a[0].staff_id == s_sub.id

    # visit_b は通常処理 (s1 が INSERT)
    rows_b = (
        await db.scalars(
            select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == visit_b.id)
        )
    ).all()
    assert len(rows_b) == 1
    assert rows_b[0].staff_id == s1.id
    refreshed_b = await db.scalar(select(Visit).where(Visit.id == visit_b.id))
    assert refreshed_b.primary_staff_id == s1.id


@pytest.mark.asyncio
async def test_persist_protects_two_person_override_pair(db) -> None:
    """2 名体制で override のペア (visit_group 全行 override) は両行とも保護される."""
    office, staffs = await _make_office_and_staff(db, n_staff=4)
    s1, s2, sub1, sub2 = staffs[0], staffs[1], staffs[2], staffs[3]

    course_a = await _make_course(db, weekday=0, code="A", office_id=office.id)
    course_b = await _make_course(db, weekday=0, code="B", office_id=office.id)
    patient = await _make_patient(db, code="P7")

    group_id = uuid.uuid4()
    visit_a = await _make_visit(
        db,
        patient_id=patient.id,
        course_id=course_a.id,
        required_staff_count=2,
        visit_group_id=group_id,
    )
    visit_b = await _make_visit(
        db,
        patient_id=patient.id,
        course_id=course_b.id,
        required_staff_count=2,
        visit_group_id=group_id,
    )
    # Commit 1 は visit_group 全行に override を立てる。差替え結果を両行に設定。
    for v, sub in ((visit_a, sub1), (visit_b, sub2)):
        v.manual_staff_override = True
        v.primary_staff_id = sub.id
        db.add(VisitStaffAssignment(visit_id=v.id, staff_id=sub.id))
    await db.commit()

    assigner = Layer3Assigner()
    await assigner._persist(
        db,
        [
            StaffAssignment(
                weekday=0, course_code="A", course_id=course_a.id, staff_id=s1.id
            ),
            StaffAssignment(
                weekday=0, course_code="B", course_id=course_b.id, staff_id=s2.id
            ),
        ],
    )
    await db.commit()

    # 両 visit とも差替え結果 (sub1 / sub2) のまま・s1/s2 で上書きされない
    rows_a = (
        await db.scalars(
            select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == visit_a.id)
        )
    ).all()
    rows_b = (
        await db.scalars(
            select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == visit_b.id)
        )
    ).all()
    assert {r.staff_id for r in rows_a} == {sub1.id}
    assert {r.staff_id for r in rows_b} == {sub2.id}

    refreshed_a = await db.scalar(select(Visit).where(Visit.id == visit_a.id))
    refreshed_b = await db.scalar(select(Visit).where(Visit.id == visit_b.id))
    assert refreshed_a.primary_staff_id == sub1.id
    assert refreshed_b.primary_staff_id == sub2.id
