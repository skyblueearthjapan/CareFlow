"""休みの段 #6: エンジン・候補・固定イベント・後始末の呼び出し・検知・取込の件数.

正典: docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md §4-1・§4-2・§11-1「エンジン」
「後始末と検知」。
"""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest
from sqlalchemy import select

from app.models import (
    Course,
    CourseTemplate,
    Office,
    Patient,
    PatientFixedVisit,
    Staff,
    StaffShift,
    Visit,
)
from app.models.course import COURSE_STATUS_COURSE_FIXED, COURSE_STATUS_PROPOSED
from app.models.staff import StaffEvent, StaffEventDefault
from app.services.leave_calendar import iso_key
from app.services.op_log_service import set_staff_override
from app.services.scheduling.auto_allocator_v2 import unassign_all_staff_for_week
from app.services.scheduling.feasibility_check import KIND_ON_LEAVE, build_feasibility_report
from app.services.scheduling.layer1_expander import Layer1Expander
from app.services.scheduling.layer3_assignment import (
    COST_HALF_DAY_LEAVE_VISIT,
    CourseAssignmentTarget,
    Layer3Assigner,
    StaffInfo,
    VisitTimeSlot,
)
from app.services.scheduling.substitute_candidates import build_candidates_for_visits
from app.services.scheduling.week_clear import apply_clear, plan_clear
from app.services.staff_event_defaults import expand_staff_event_defaults
from app.services.staff_leave import collect_leave_conflicts, today_jst


def _future_monday() -> date:
    """来週以降の月曜 (週のどの日も今日以降)."""
    t = today_jst()
    return t + timedelta(days=7 - t.weekday() + 7)


async def _leave(db, staff_id, d: date, t: str) -> None:
    y, w, wd = iso_key(d)
    await set_staff_override(
        db, staff_id=staff_id, iso_year=y, iso_week=w, weekday=wd, override_type=t
    )


async def _office_staff(db, *, name="稲毛", n=1) -> tuple[Office, list[Staff]]:
    office = Office(name=f"{name}拠点", lat=35.64, lng=140.10)
    db.add(office)
    await db.flush()
    staffs = []
    for i in range(n):
        s = Staff(
            code=f"LE{i}{name}",
            name=f"エンジン{i} {name}",
            sex="female",
            role="staff",
            status="active",
            primary_office_id=office.id,
        )
        db.add(s)
        await db.flush()
        for wd in range(6):
            db.add(
                StaffShift(
                    staff_id=s.id,
                    weekday=wd,
                    is_on=True,
                    start_time=time(9, 0),
                    end_time=time(18, 0),
                )
            )
        staffs.append(s)
    await db.flush()
    return office, staffs


# ---------------------------------------------------------------------------
# load_active_staff・コスト
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_active_staff_paid_off_removes_day_half_keeps(db) -> None:
    monday = _future_monday()
    _office, (s,) = await _office_staff(db)
    await _leave(db, s.id, monday, "paid_off")
    await _leave(db, s.id, monday + timedelta(days=1), "am_off")
    y, w, _ = iso_key(monday)
    infos = await Layer3Assigner().load_active_staff(db, iso_year=y, iso_week=w, week_monday=monday)
    info = next(i for i in infos if i.staff_id == s.id)
    assert 0 not in info.work_days  # 有給 = 終日休み
    assert 1 in info.work_days  # 半休はコースを持てる (人数 1)
    assert info.half_day_off == {1: "am_off"}


def test_cost_adds_half_day_penalty_per_hit_visit() -> None:
    from uuid import uuid4

    staff = StaffInfo(
        staff_id=uuid4(),
        name="半休",
        sex="female",
        role="staff",
        primary_office_lat=None,
        primary_office_lng=None,
        work_days=frozenset({0}),
        half_day_off={0: "pm_off"},
    )
    course = CourseAssignmentTarget(
        course_id=uuid4(),
        weekday=0,
        course_code="A",
        centroid_lat=None,
        centroid_lng=None,
        gender_restrictions=frozenset(),
        visits=[
            VisitTimeSlot(start_time=time(9, 0), end_time=time(10, 0)),
            VisitTimeSlot(start_time=time(14, 0), end_time=time(15, 0)),
            VisitTimeSlot(start_time=time(16, 0), end_time=time(17, 0)),
        ],
    )
    assigner = Layer3Assigner()
    with_half = assigner._cost_single_cell(weekday=0, course=course, staff=staff, history=[])
    staff.half_day_off = {}
    without = assigner._cost_single_cell(weekday=0, course=course, staff=staff, history=[])
    assert with_half - without == pytest.approx(2 * COST_HALF_DAY_LEAVE_VISIT)


# ---------------------------------------------------------------------------
# 週を作り直しても半休の時間帯に戻らない (3 通り)
# ---------------------------------------------------------------------------


async def _seed_week(db):
    monday = _future_monday()
    office, (nurse,) = await _office_staff(db, name="作り直し")
    tpl = CourseTemplate(office_id=office.id, label="A")
    db.add(tpl)
    await db.flush()
    pats = []
    for i, (h, code) in enumerate(((9, "RB-AM"), (14, "RB-PM"))):
        p = Patient(
            code=code,
            name=f"作り直し患者{i}",
            status="active",
            special_week_active=[],
            primary_office_id=office.id,
            lat=35.64,
            lng=140.10,
        )
        db.add(p)
        await db.flush()
        db.add(
            PatientFixedVisit(
                patient_id=p.id,
                mode="normal",
                weekday=0,
                start_time=time(h, 0),
                duration_min=60,
                course_template_id=tpl.id,
            )
        )
        pats.append(p)
    await _leave(db, nurse.id, monday, "pm_off")
    await db.commit()
    return monday, office, nurse, pats


async def _monday_visits(db, monday: date) -> dict[int, Visit]:
    rows = (
        await db.scalars(
            select(Visit)
            .where(Visit.visit_date == monday, Visit.deleted_at.is_(None))
            .execution_options(populate_existing=True)
        )
    ).all()
    return {v.start_time.hour: v for v in rows}


async def _generate_and_assign(db, monday: date) -> None:
    y, w, _ = iso_key(monday)
    await Layer1Expander().expand_week(db, iso_year=y, iso_week=w)
    await db.commit()
    await _assign(db, y, w)


async def _assign(db, y: int, w: int) -> None:
    """assign-staff-only と同じ: proposed のコースを course_fixed にしてから割当."""
    for c in (
        await db.scalars(
            select(Course).where(
                Course.iso_year == y,
                Course.iso_week == w,
                Course.course_status == COURSE_STATUS_PROPOSED,
                Course.deleted_at.is_(None),
            )
        )
    ).all():
        c.course_status = COURSE_STATUS_COURSE_FIXED
    await db.flush()
    await Layer3Assigner().assign(db, iso_year=y, iso_week=w)
    await db.commit()


def _assert_pm_unassigned(visits: dict[int, Visit], nurse: Staff) -> None:
    assert visits[9].primary_staff_id == nurse.id
    assert visits[14].primary_staff_id is None
    assert visits[14].manual_staff_override is True


@pytest.mark.asyncio
async def test_rebuild_three_ways_never_returns_to_half_leave(db) -> None:
    monday, office, nurse, _pats = await _seed_week(db)
    nurse_id = nurse.id
    y, w, _ = iso_key(monday)

    # 生成 → 割当 (割当の最後の後始末で午後休の時間帯の訪問は担当なし)
    await _generate_and_assign(db, monday)
    visits = await _monday_visits(db, monday)
    course = await db.get(Course, visits[9].course_id)
    assert course is not None and course.assigned_staff_id == nurse_id  # コースは本人のまま
    _assert_pm_unassigned(visits, nurse)

    # 1) 自動スタッフ割当だけをやり直す
    await _assign(db, y, w)
    _assert_pm_unassigned(await _monday_visits(db, monday), nurse)

    # 2) 一斉未割当 → 自動スタッフ割当
    await unassign_all_staff_for_week(db, iso_year=y, iso_week=w, office_ids=[office.id])
    await db.commit()
    await _assign(db, y, w)
    _assert_pm_unassigned(await _monday_visits(db, monday), nurse)

    # 3) 週を空にして作り直す (clear → generate → assign)
    apply_clear(await plan_clear(db, monday))
    await db.commit()
    await _generate_and_assign(db, monday)
    _assert_pm_unassigned(await _monday_visits(db, monday), nurse)


# ---------------------------------------------------------------------------
# 候補・固定イベント
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_substitute_half_day_staff_is_ng_only_in_window(db) -> None:
    monday = _future_monday()
    office, (absent, half) = await _office_staff(db, name="候補", n=2)
    p = Patient(
        code="SB-1",
        name="候補患者",
        status="active",
        special_week_active=[],
        primary_office_id=office.id,
    )
    db.add(p)
    await db.flush()
    y, w, wd = iso_key(monday)
    course = Course(
        iso_year=y,
        iso_week=w,
        weekday=wd,
        code="A",
        office_id=office.id,
        course_status="staff_assigned",
        assigned_staff_id=absent.id,
    )
    db.add(course)
    await db.flush()
    pm = Visit(
        patient_id=p.id,
        primary_staff_id=absent.id,
        course_id=course.id,
        visit_date=monday,
        start_time=time(14, 0),
        end_time=time(15, 0),
        type="regular",
        status="planned",
        source="auto",
    )
    am = Visit(
        patient_id=p.id,
        primary_staff_id=absent.id,
        course_id=course.id,
        visit_date=monday,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status="planned",
        source="auto",
    )
    db.add_all([pm, am])
    await _leave(db, half.id, monday, "pm_off")
    await db.flush()

    res_pm = await build_candidates_for_visits(
        db, target_date=monday, visits=[pm], exclude_staff_id=absent.id
    )
    cand = next(c for g in res_pm.groups for c in g.candidates if c.staff_id == half.id)
    assert cand.status == "ng"
    assert any(r.code == "off" for r in cand.reasons)

    res_am = await build_candidates_for_visits(
        db, target_date=monday, visits=[am], exclude_staff_id=absent.id
    )
    cand_am = next(c for g in res_am.groups for c in g.candidates if c.staff_id == half.id)
    assert all(r.code != "off" for r in cand_am.reasons)


@pytest.mark.asyncio
async def test_fixed_events_skip_paid_off_and_half_day_window(db) -> None:
    monday = _future_monday()
    _office, (s,) = await _office_staff(db, name="会議")
    db.add_all(
        [
            StaffEventDefault(
                staff_id=s.id,
                weekday=0,
                start_time=time(9, 0),
                end_time=time(9, 30),
                title="朝会",
            ),
            StaffEventDefault(
                staff_id=s.id,
                weekday=1,
                start_time=time(9, 0),
                end_time=time(9, 30),
                title="朝会",
            ),
            StaffEventDefault(
                staff_id=s.id,
                weekday=1,
                start_time=time(15, 0),
                end_time=time(16, 0),
                title="カンファ",
            ),
        ]
    )
    await _leave(db, s.id, monday, "paid_off")
    await _leave(db, s.id, monday + timedelta(days=1), "am_off")
    await db.flush()
    y, w, _ = iso_key(monday)
    created = await expand_staff_event_defaults(db, y, w)
    assert created == 1
    ev = (await db.scalars(select(StaffEvent).where(StaffEvent.staff_id == s.id))).all()
    assert [e.title for e in ev] == ["カンファ"]


# ---------------------------------------------------------------------------
# 検知 (実現性チェック)・取込のプレビュー
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feasibility_reports_visits_on_leave(db) -> None:
    monday = _future_monday()
    office, (s,) = await _office_staff(db, name="検知")
    p = Patient(
        code="FE-1",
        name="検知患者",
        status="active",
        special_week_active=[],
        primary_office_id=office.id,
    )
    db.add(p)
    await db.flush()
    db.add(
        Visit(
            patient_id=p.id,
            primary_staff_id=s.id,
            visit_date=monday,
            start_time=time(10, 0),
            end_time=time(11, 0),
            type="regular",
            status="planned",
            source="kaipoke",
        )
    )
    await _leave(db, s.id, monday, "off")
    await db.flush()
    y, w, _ = iso_key(monday)
    report = await build_feasibility_report(db, iso_year=y, iso_week=w)
    hits = [f for f in report.findings if f.kind == KIND_ON_LEAVE]
    assert len(hits) == 1
    assert hits[0].to == "検知患者"
    assert hits[0].severity == "soft"


@pytest.mark.asyncio
async def test_collect_leave_conflicts_for_inbound_preview(db) -> None:
    monday = _future_monday()
    _office, (s,) = await _office_staff(db, name="取込")
    await _leave(db, s.id, monday, "am_off")
    past = today_jst() - timedelta(days=1)
    await _leave(db, s.id, past, "off")
    await db.flush()
    got = await collect_leave_conflicts(
        db,
        [
            (s.id, monday, time(9, 0), time(10, 0)),  # 午前休の時間帯 → 出る
            (s.id, monday, time(9, 0), time(10, 0)),  # 同じ枠は 1 件
            (s.id, monday, time(13, 0), time(14, 0)),  # 13:00 から勤務 → 出ない
            (s.id, past, time(9, 0), time(10, 0)),  # 過去の日 → 出ない
        ],
    )
    assert [(c.visit_date, c.start_time, c.leave_type) for c in got] == [
        (monday, time(9, 0), "am_off")
    ]
    assert got[0].staff_name == s.name
