"""W27 Phase A: Layer 3 で StaffEvent 時間帯と重なる staff をハード除外する.

検証観点:
  1. event があり時間帯重複 → Layer 3 ハンガリアンで当該 staff×course を除外し
     他 staff に割付される
  2. event 時間帯と visit 時間帯が重ならない → 通常通り割付 (event 影響無し)
  3. events 空 → 従来挙動を維持 (regression)
  4. 純粋関数 solve(events_by_staff=...) で hard-exclusion が効く
"""

from __future__ import annotations

from datetime import date, datetime, time
from uuid import uuid4

import pytest

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

W27_ISO_YEAR = 2026
W27_ISO_WEEK = 27
W27_WEEK_MONDAY = date(2026, 6, 29)  # 2026-W27 月曜


# ---------------------------------------------------------------------------
# 純粋関数版テスト (solve API で events_by_staff を直接渡す)
# ---------------------------------------------------------------------------


def _staff(name: str, work_days: frozenset[int] | None = None) -> StaffInfo:
    return StaffInfo(
        staff_id=uuid4(),
        name=name,
        sex=None,
        role="staff",
        primary_office_lat=None,
        primary_office_lng=None,
        work_days=work_days if work_days is not None else frozenset(range(6)),
    )


def _course(code: str, weekday: int, start: time, end: time) -> CourseAssignmentTarget:
    return CourseAssignmentTarget(
        course_id=uuid4(),
        weekday=weekday,
        course_code=code,
        centroid_lat=None,
        centroid_lng=None,
        gender_restrictions=frozenset(),
        visits=[VisitTimeSlot(start_time=start, end_time=end)],
    )


def _event(
    staff_id, starts_at: datetime, ends_at: datetime, event_type: str = "研修"
) -> StaffEvent:
    return StaffEvent(
        id=uuid4(),
        staff_id=staff_id,
        event_type=event_type,
        starts_at=starts_at,
        ends_at=ends_at,
    )


def test_solve_event_overlap_excludes_staff() -> None:
    """event 時間帯が visit 時間帯と重なる staff は当該 course から除外される.

    シナリオ: コース A (月 09:00-10:00) + staff S1/S2.
    S1 は月 09:00-10:00 に研修予定 → S1 を除外し S2 が割付される.
    """
    s1 = _staff("S1")
    s2 = _staff("S2")
    course_a = _course("A", weekday=0, start=time(9, 0), end=time(10, 0))

    events_by_staff = {
        s1.staff_id: [
            _event(
                s1.staff_id,
                datetime.combine(W27_WEEK_MONDAY, time(9, 0)),
                datetime.combine(W27_WEEK_MONDAY, time(10, 0)),
            )
        ]
    }

    assigner = Layer3Assigner()
    result = assigner.solve(
        [course_a],
        [s1, s2],
        events_by_staff=events_by_staff,
        week_monday=W27_WEEK_MONDAY,
    )
    assert len(result.assignments) == 1
    assert result.assignments[0].staff_id == s2.staff_id, (
        f"event 時間帯と重なる S1 が除外されず S1 が割付された: {result.assignments}"
    )


def test_solve_event_outside_visit_window_does_not_exclude() -> None:
    """event 時間帯が visit 時間帯と重ならなければハード除外しない (regression).

    シナリオ: コース A (月 09:00-10:00). S1 は月 14:00-15:00 に研修.
    時間帯が完全に分離しているため S1 は依然として候補に残る.
    距離なし・履歴なしなので S1 が選ばれる可能性は十分にある (S2 と同コスト).
    """
    s1 = _staff("S1")
    course_a = _course("A", weekday=0, start=time(9, 0), end=time(10, 0))

    events_by_staff = {
        s1.staff_id: [
            _event(
                s1.staff_id,
                datetime.combine(W27_WEEK_MONDAY, time(14, 0)),
                datetime.combine(W27_WEEK_MONDAY, time(15, 0)),
            )
        ]
    }

    assigner = Layer3Assigner()
    result = assigner.solve(
        [course_a],
        [s1],
        events_by_staff=events_by_staff,
        week_monday=W27_WEEK_MONDAY,
    )
    assert len(result.assignments) == 1
    assert result.assignments[0].staff_id == s1.staff_id, (
        "event が visit 時間帯と重ならないのに staff が除外された"
    )


def test_solve_no_events_dict_keeps_legacy_behavior() -> None:
    """events_by_staff=None (未指定) は従来挙動 (regression).

    スタッフ 1 名 + コース 1 件で必ず割付されることを確認.
    """
    s1 = _staff("S1")
    course_a = _course("A", weekday=0, start=time(9, 0), end=time(10, 0))

    assigner = Layer3Assigner()
    result = assigner.solve([course_a], [s1])
    assert len(result.assignments) == 1
    assert result.assignments[0].staff_id == s1.staff_id


def test_solve_event_adjacent_no_overlap() -> None:
    """event が visit 開始より BUFFER_MINUTES(15 分) 超前に終わる場合は除外しない.

    W33 バッファ制約: event 終了 → visit 開始 が 15 分超の場合は除外対象外。
    visit: 09:00-10:00. event: 07:00-08:30 (= visit 開始 30 分前に終了).
    バッファ 15 分を加味しても 08:30+15min=08:45 < 09:00 なので除外されない.
    """
    s1 = _staff("S1")
    course_a = _course("A", weekday=0, start=time(9, 0), end=time(10, 0))

    events_by_staff = {
        s1.staff_id: [
            _event(
                s1.staff_id,
                datetime.combine(W27_WEEK_MONDAY, time(7, 0)),
                datetime.combine(W27_WEEK_MONDAY, time(8, 30)),
            )
        ]
    }

    assigner = Layer3Assigner()
    result = assigner.solve(
        [course_a],
        [s1],
        events_by_staff=events_by_staff,
        week_monday=W27_WEEK_MONDAY,
    )
    assert len(result.assignments) == 1
    assert result.assignments[0].staff_id == s1.staff_id, (
        "バッファ超過 (30 分余裕) の event で誤って除外された"
    )


def test_solve_event_partial_overlap_excludes() -> None:
    """visit (09:00-10:00) と event (09:30-10:30) の partial overlap でも除外."""
    s1 = _staff("S1")
    s2 = _staff("S2")
    course_a = _course("A", weekday=0, start=time(9, 0), end=time(10, 0))

    events_by_staff = {
        s1.staff_id: [
            _event(
                s1.staff_id,
                datetime.combine(W27_WEEK_MONDAY, time(9, 30)),
                datetime.combine(W27_WEEK_MONDAY, time(10, 30)),
            )
        ]
    }

    assigner = Layer3Assigner()
    result = assigner.solve(
        [course_a],
        [s1, s2],
        events_by_staff=events_by_staff,
        week_monday=W27_WEEK_MONDAY,
    )
    assert len(result.assignments) == 1
    assert result.assignments[0].staff_id == s2.staff_id


# ---------------------------------------------------------------------------
# DB 経由テスト (assigner.assign 経由で全体フロー検証)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assigner_assign_event_overlap_excludes_via_db(db) -> None:
    """assigner.assign(db, ...) 経由で event 重複 staff が除外される.

    DB に StaffEvent を 1 件入れ、当該 staff が visit 時間帯と重なるとき
    Layer 3 ハンガリアンで除外され他 staff に割付されることを検証する.
    """
    office = Office(name="W27 拠点 ev1", lat=35.65, lng=140.0)
    db.add(office)
    await db.flush()

    s1 = Staff(
        code="W27-S1",
        name="W27 スタッフ 1",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    s2 = Staff(
        code="W27-S2",
        name="W27 スタッフ 2",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    db.add_all([s1, s2])
    await db.flush()

    for staff in [s1, s2]:
        db.add(StaffShift(staff_id=staff.id, weekday=0, is_on=True))
    await db.flush()

    # 月曜 A コース (course_fixed)
    course_a = Course(
        iso_year=W27_ISO_YEAR,
        iso_week=W27_ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_COURSE_FIXED,
        office_id=office.id,
    )
    db.add(course_a)
    await db.flush()

    p = Patient(code="W27-P1", name="W27 患者 1", status="active")
    db.add(p)
    await db.flush()
    db.add(
        Visit(
            patient_id=p.id,
            visit_date=W27_WEEK_MONDAY,
            start_time=time(9, 0),
            end_time=time(10, 0),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto",
            required_staff_count=1,
            course_id=course_a.id,
        )
    )

    # S1 に研修 09:00-10:00 を仕込む
    db.add(
        StaffEvent(
            staff_id=s1.id,
            event_type="研修",
            starts_at=datetime.combine(W27_WEEK_MONDAY, time(9, 0)),
            ends_at=datetime.combine(W27_WEEK_MONDAY, time(10, 0)),
        )
    )
    await db.commit()

    assigner = Layer3Assigner()
    result = await assigner.assign(db, iso_year=W27_ISO_YEAR, iso_week=W27_ISO_WEEK)
    await db.commit()

    # S1 は除外され S2 が割付される
    assert len(result.assignments) == 1
    assert result.assignments[0].course_code == "A"
    assert result.assignments[0].staff_id == s2.id, (
        f"S1 が研修中なのに割付された: {result.assignments[0].staff_id}"
    )


@pytest.mark.asyncio
async def test_assigner_assign_no_events_unchanged(db) -> None:
    """events 0 件 → 既存 Layer 3 挙動と同じ (regression)."""
    office = Office(name="W27 拠点 ev2", lat=35.65, lng=140.0)
    db.add(office)
    await db.flush()

    s1 = Staff(
        code="W27R-S1",
        name="W27R スタッフ 1",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    db.add(s1)
    await db.flush()
    db.add(StaffShift(staff_id=s1.id, weekday=0, is_on=True))
    await db.flush()

    course_a = Course(
        iso_year=W27_ISO_YEAR,
        iso_week=W27_ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_COURSE_FIXED,
        office_id=office.id,
    )
    db.add(course_a)
    await db.flush()

    p = Patient(code="W27R-P1", name="W27R 患者", status="active")
    db.add(p)
    await db.flush()
    db.add(
        Visit(
            patient_id=p.id,
            visit_date=W27_WEEK_MONDAY,
            start_time=time(9, 0),
            end_time=time(10, 0),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto",
            required_staff_count=1,
            course_id=course_a.id,
        )
    )
    await db.commit()

    assigner = Layer3Assigner()
    result = await assigner.assign(db, iso_year=W27_ISO_YEAR, iso_week=W27_ISO_WEEK)
    await db.commit()

    assert len(result.assignments) == 1
    assert result.assignments[0].staff_id == s1.id
