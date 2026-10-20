"""休みの段 #4: 共通の処理 apply_staff_leave / cancel_staff_leave と後始末・検知.

正典: docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md §3・§4-2・§11-1「共通の処理」。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.core.security import hash_password
from app.models import (
    Course,
    LeaveMonth,
    Notification,
    Office,
    Patient,
    PendingRequest,
    Staff,
    StaffWeeklyOverride,
    User,
    Visit,
)
from app.models.accompaniment import Accompaniment
from app.models.staff import StaffEvent
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.services.leave_calendar import iso_key
from app.services.op_log_service import execute_redo, execute_undo, set_staff_override
from app.services.staff_leave import (
    StaffLeaveError,
    apply_staff_leave,
    cancel_staff_leave,
    count_visits_on_leave,
    find_visits_on_leave,
    settle_leave_unassign,
    today_jst,
)


def _future(days: int = 7) -> date:
    return today_jst() + timedelta(days=days)


class Seed:
    def __init__(self, db) -> None:
        self.db = db

    async def admin(self, email: str | None = None) -> User:
        u = User(
            email=email or f"adm-{uuid4().hex[:8]}@example.com",
            password_hash=hash_password("pw"),
            role="admin",
        )
        self.db.add(u)
        await self.db.flush()
        return u

    async def staff(self, name: str, *, with_user: bool = False) -> Staff:
        s = Staff(name=name)
        self.db.add(s)
        await self.db.flush()
        if with_user:
            self.db.add(
                User(
                    email=f"st-{uuid4().hex[:8]}@example.com",
                    password_hash=hash_password("pw"),
                    role="staff",
                    staff_id=s.id,
                )
            )
            await self.db.flush()
        return s

    async def office(self) -> Office:
        o = Office(name=f"拠点{uuid4().hex[:4]}")
        self.db.add(o)
        await self.db.flush()
        return o

    async def patient(self, name: str = "患者") -> Patient:
        p = Patient(code=f"P-{uuid4().hex[:8]}", name=name, status="active", special_week_active=[])
        self.db.add(p)
        await self.db.flush()
        return p

    async def course(self, office: Office, day: date, staff: Staff | None, code="A") -> Course:
        y, w, wd = iso_key(day)
        c = Course(
            iso_year=y,
            iso_week=w,
            weekday=wd,
            code=code,
            office_id=office.id,
            course_status="staff_assigned",
            assigned_staff_id=staff.id if staff else None,
        )
        self.db.add(c)
        await self.db.flush()
        return c

    async def visit(
        self,
        day: date,
        start: str,
        end: str,
        *,
        staff: Staff | None,
        course: Course | None = None,
        status: str = "planned",
        members: list[Staff] | None = None,
        patient: Patient | None = None,
    ) -> Visit:
        p = patient or await self.patient()
        sh, sm = (int(x) for x in start.split(":"))
        eh, em = (int(x) for x in end.split(":"))
        v = Visit(
            patient_id=p.id,
            primary_staff_id=staff.id if staff else None,
            course_id=course.id if course else None,
            visit_date=day,
            start_time=time(sh, sm),
            end_time=time(eh, em),
            type="regular",
            status=status,
            source="auto",
        )
        self.db.add(v)
        await self.db.flush()
        for m in members if members is not None else ([staff] if staff else []):
            self.db.add(VisitStaffAssignment(visit_id=v.id, staff_id=m.id))
        await self.db.flush()
        return v


async def _override(db, staff_id: UUID, day: date) -> StaffWeeklyOverride | None:
    y, w, wd = iso_key(day)
    return await db.scalar(
        select(StaffWeeklyOverride).where(
            StaffWeeklyOverride.staff_id == staff_id,
            StaffWeeklyOverride.iso_year == y,
            StaffWeeklyOverride.iso_week == w,
            StaffWeeklyOverride.weekday == wd,
        )
    )


async def _vsa(db, visit_id) -> set:
    return set(
        (
            await db.scalars(
                select(VisitStaffAssignment.staff_id).where(
                    VisitStaffAssignment.visit_id == visit_id
                )
            )
        ).all()
    )


async def _notifications(db, type_: str) -> list[Notification]:
    return list((await db.scalars(select(Notification).where(Notification.type == type_))).all())


# ---------------------------------------------------------------------------
# 種類ごとの担当なしの範囲 (境目ちょうど)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_am_off_unassigns_only_visits_starting_before_13(db) -> None:
    s = Seed(db)
    actor = await s.admin()
    nurse = await s.staff("午前 休子")
    office = await s.office()
    day = _future()
    course = await s.course(office, day, nurse)
    v_1259 = await s.visit(day, "12:59", "13:30", staff=nurse, course=course)
    v_1300 = await s.visit(day, "13:00", "14:00", staff=nurse, course=course)
    v_0900 = await s.visit(day, "09:00", "10:00", staff=None, course=course)  # コース担当で本人

    res = await apply_staff_leave(
        db,
        staff_id=nurse.id,
        day=day,
        leave_type="am_off",
        source="admin",
        note=None,
        actor=actor,
        op_group_id=None,
        origin="leave_page",
    )
    assert set(res.moved_visit_ids) == {v_1259.id, v_0900.id}
    assert res.moved_course_ids == []  # 半休はコースを動かさない
    for v in (v_1259, v_0900):
        await db.refresh(v)
        assert v.primary_staff_id is None
        assert v.manual_staff_override is True
    await db.refresh(v_1300)
    assert v_1300.primary_staff_id == nurse.id
    await db.refresh(course)
    assert course.assigned_staff_id == nurse.id
    assert await _vsa(db, v_1259.id) == set()


@pytest.mark.asyncio
async def test_pm_off_boundary_end_1230_kept_1231_moved(db) -> None:
    s = Seed(db)
    nurse = await s.staff("午後 休子")
    day = _future()
    keep = await s.visit(day, "11:30", "12:30", staff=nurse)
    move = await s.visit(day, "11:31", "12:31", staff=nurse)
    res = await apply_staff_leave(
        db,
        staff_id=nurse.id,
        day=day,
        leave_type="pm_off",
        source="admin",
        note=None,
        actor=None,
        op_group_id=None,
        origin="leave_page",
    )
    assert res.moved_visit_ids == [move.id]
    await db.refresh(keep)
    assert keep.primary_staff_id == nurse.id


@pytest.mark.asyncio
@pytest.mark.parametrize("leave_type", ["off", "paid_off"])
async def test_full_day_clears_course_and_unassigns_all(db, leave_type) -> None:
    s = Seed(db)
    actor = await s.admin()
    nurse = await s.staff("終日 休子")
    office = await s.office()
    day = _future()
    course = await s.course(office, day, nurse)
    v1 = await s.visit(day, "09:00", "10:00", staff=nurse, course=course)
    v2 = await s.visit(day, "16:00", "17:00", staff=None, course=course)
    res = await apply_staff_leave(
        db,
        staff_id=nurse.id,
        day=day,
        leave_type=leave_type,
        source="admin",
        note="私用",
        actor=actor,
        op_group_id=None,
        origin="leave_page",
    )
    assert set(res.moved_visit_ids) == {v1.id, v2.id}
    assert res.moved_course_ids == [course.id]
    await db.refresh(course)
    assert course.assigned_staff_id is None
    for v in (v1, v2):
        await db.refresh(v)
        assert v.primary_staff_id is None
        assert v.manual_staff_override is False  # コースごと担当なし
    row = await _override(db, nurse.id, day)
    assert row is not None and row.override_type == leave_type and row.reason == "私用"


@pytest.mark.asyncio
async def test_checked_in_visits_are_not_moved(db) -> None:
    s = Seed(db)
    nurse = await s.staff("打刻 済子")
    day = today_jst()  # 当日 (今日は対象)
    done = await s.visit(day, "09:00", "10:00", staff=nurse, status="completed")
    running = await s.visit(day, "10:00", "11:00", staff=nurse, status="in_progress")
    planned = await s.visit(day, "11:00", "12:00", staff=nurse)
    res = await apply_staff_leave(
        db,
        staff_id=nurse.id,
        day=day,
        leave_type="off",
        source="admin",
        note=None,
        actor=None,
        op_group_id=None,
        origin="cockpit",
    )
    assert res.moved_visit_ids == [planned.id]
    assert res.skipped_visit_ids == [done.id, running.id]
    for v in (done, running):
        await db.refresh(v)
        assert v.primary_staff_id == nurse.id


@pytest.mark.asyncio
async def test_second_staff_only_own_slot_removed_and_accompaniment_counted(db) -> None:
    s = Seed(db)
    lead = await s.staff("主 担当")
    nurse = await s.staff("二人目 休子")
    trainee_lead = await s.staff("先輩")
    day = _future()
    pair = await s.visit(day, "09:00", "10:00", staff=lead, members=[lead, nurse])
    accompanied = await s.visit(day, "10:30", "11:30", staff=trainee_lead)
    db.add(
        Accompaniment(accompanying_staff_id=nurse.id, target_type="visit", visit_id=accompanied.id)
    )
    await db.flush()
    res = await apply_staff_leave(
        db,
        staff_id=nurse.id,
        day=day,
        leave_type="am_off",
        source="admin",
        note=None,
        actor=None,
        op_group_id=None,
        origin="leave_page",
    )
    assert res.moved_visit_ids == [pair.id]
    await db.refresh(pair)
    assert pair.primary_staff_id == lead.id  # 相方は残る
    assert await _vsa(db, pair.id) == {lead.id}
    assert res.accompaniment_count == 1
    assert await db.scalar(select(func.count()).select_from(Accompaniment)) == 1


@pytest.mark.asyncio
async def test_half_day_fixed_events_cancelled_by_time(db) -> None:
    s = Seed(db)
    nurse = await s.staff("会議 休子")
    day = _future()
    morning = StaffEvent(
        staff_id=nurse.id,
        title="朝会",
        event_type="event",
        starts_at=datetime.combine(day, time(9, 0)),
        ends_at=datetime.combine(day, time(9, 30)),
        source="fixed",
    )
    afternoon = StaffEvent(
        staff_id=nurse.id,
        title="カンファ",
        event_type="event",
        starts_at=datetime.combine(day, time(15, 0)),
        ends_at=datetime.combine(day, time(16, 0)),
        source="fixed",
    )
    db.add_all([morning, afternoon])
    await db.flush()
    res = await apply_staff_leave(
        db,
        staff_id=nurse.id,
        day=day,
        leave_type="am_off",
        source="admin",
        note=None,
        actor=None,
        op_group_id=None,
        origin="leave_page",
    )
    assert res.cancelled_event_ids == [morning.id]


# ---------------------------------------------------------------------------
# 過去日・upsert・通知
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_past_day_records_override_only(db) -> None:
    s = Seed(db)
    await s.admin()
    nurse = await s.staff("過去 休子", with_user=True)
    office = await s.office()
    day = today_jst() - timedelta(days=3)
    course = await s.course(office, day, nurse)
    v = await s.visit(day, "09:00", "10:00", staff=nurse, course=course)
    requester = await s.admin()
    req = PendingRequest(
        requester_user_id=requester.id,
        request_type="staff_off",
        payload={"override_type": "off"},
        target_staff_id=nurse.id,
        target_date=day,
        status="pending",
    )
    db.add(req)
    await db.flush()
    res = await apply_staff_leave(
        db,
        staff_id=nurse.id,
        day=day,
        leave_type="off",
        source="request",
        note=None,
        actor=None,
        op_group_id=None,
        origin="request_list",
        request=req,
    )
    assert res.past_day is True
    assert res.moved_visit_ids == []
    await db.refresh(v)
    await db.refresh(course)
    assert v.primary_staff_id == nurse.id
    assert course.assigned_staff_id == nurse.id
    row = await _override(db, nurse.id, day)
    assert row is not None and row.source == "request" and row.pending_request_id == req.id
    assert len(await _notifications(db, "leave_approved")) == 1
    assert await _notifications(db, "leave_unassigned") == []


@pytest.mark.asyncio
async def test_upsert_replaces_type_and_keeps_one_row(db) -> None:
    s = Seed(db)
    nurse = await s.staff("上書き 休子")
    day = _future()
    y, w, wd = iso_key(day)
    await set_staff_override(
        db,
        staff_id=nurse.id,
        iso_year=y,
        iso_week=w,
        weekday=wd,
        override_type="custom_time",
        start_time="09:00",
        end_time="15:00",
    )
    for t in ("am_off", "paid_off"):
        await apply_staff_leave(
            db,
            staff_id=nurse.id,
            day=day,
            leave_type=t,
            source="admin",
            note=None,
            actor=None,
            op_group_id=None,
            origin="leave_page",
        )
    rows = (
        await db.scalars(
            select(StaffWeeklyOverride).where(StaffWeeklyOverride.staff_id == nurse.id)
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].override_type == "paid_off"
    assert rows[0].start_time is None


@pytest.mark.asyncio
async def test_notifications_admins_and_requester(db) -> None:
    s = Seed(db)
    a1 = await s.admin()
    await s.admin()
    nurse = await s.staff("通知 休子", with_user=True)
    day = _future()
    p1 = await s.patient("患者1")
    p2 = await s.patient("患者2")
    await s.visit(day, "09:00", "10:00", staff=nurse, patient=p1)
    await s.visit(day, "10:30", "11:00", staff=nurse, patient=p1)
    await s.visit(day, "11:00", "12:00", staff=nurse, patient=p2)
    req = PendingRequest(
        requester_user_id=a1.id,
        request_type="staff_off",
        payload={"override_type": "off"},
        target_staff_id=nurse.id,
        target_date=day,
        status="pending",
    )
    db.add(req)
    await db.flush()
    await apply_staff_leave(
        db,
        staff_id=nurse.id,
        day=day,
        leave_type="off",
        source="request",
        note=None,
        actor=a1,
        op_group_id=None,
        origin="request_list",
        request=req,
    )
    admins = await _notifications(db, "leave_unassigned")
    assert len(admins) == 2  # 管理者 2 人へ 1 通ずつ
    assert "2名の訪問を予定していましたが、未割り当てとしました" in (admins[0].body or "")
    assert "カイポケへは送られません" in (admins[0].body or "")
    approved = await _notifications(db, "leave_approved")
    assert len(approved) == 1
    assert "公休が承認されました" in approved[0].title


@pytest.mark.asyncio
async def test_notify_false_sends_nothing(db) -> None:
    s = Seed(db)
    await s.admin()
    nurse = await s.staff("黙って 休子")
    day = _future()
    await s.visit(day, "09:00", "10:00", staff=nurse)
    await apply_staff_leave(
        db,
        staff_id=nurse.id,
        day=day,
        leave_type="off",
        source="admin",
        note=None,
        actor=None,
        op_group_id=None,
        origin="excel",
        notify=False,
    )
    assert await _notifications(db, "leave_unassigned") == []


# ---------------------------------------------------------------------------
# 引き受け先
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_to_staff_on_half_leave_in_window_is_422(db) -> None:
    s = Seed(db)
    nurse = await s.staff("休む人")
    helper = await s.staff("午後休の人")
    day = _future()
    await s.visit(day, "14:00", "15:00", staff=nurse)
    y, w, wd = iso_key(day)
    await set_staff_override(
        db, staff_id=helper.id, iso_year=y, iso_week=w, weekday=wd, override_type="pm_off"
    )
    with pytest.raises(StaffLeaveError) as err:
        await apply_staff_leave(
            db,
            staff_id=nurse.id,
            day=day,
            leave_type="off",
            source="admin",
            note=None,
            actor=None,
            op_group_id=None,
            origin="cockpit",
            to_staff_id=helper.id,
        )
    assert err.value.status_code == 422


@pytest.mark.asyncio
async def test_to_staff_takes_over_with_manual(db) -> None:
    s = Seed(db)
    nurse = await s.staff("休む人2")
    helper = await s.staff("引き受け")
    office = await s.office()
    day = _future()
    course = await s.course(office, day, nurse)
    v = await s.visit(day, "09:00", "10:00", staff=nurse, course=course)
    await apply_staff_leave(
        db,
        staff_id=nurse.id,
        day=day,
        leave_type="off",
        source="admin",
        note=None,
        actor=None,
        op_group_id=None,
        origin="cockpit",
        to_staff_id=helper.id,
    )
    await db.refresh(v)
    await db.refresh(course)
    assert v.primary_staff_id == helper.id and v.manual_staff_override is True
    assert course.assigned_staff_id == helper.id


# ---------------------------------------------------------------------------
# 取り消し・戻る・last_leave_changed_at
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_keeps_moved_visits_and_notifies(db) -> None:
    s = Seed(db)
    nurse = await s.staff("取消 休子", with_user=True)
    day = _future()
    v = await s.visit(day, "09:00", "10:00", staff=nurse)
    await apply_staff_leave(
        db,
        staff_id=nurse.id,
        day=day,
        leave_type="pm_off",
        source="admin",
        note=None,
        actor=None,
        op_group_id=None,
        origin="leave_page",
    )
    v2 = await s.visit(day, "14:00", "15:00", staff=None)
    v2.manual_staff_override = True
    await db.flush()
    got = await cancel_staff_leave(
        db, staff_id=nurse.id, day=day, actor=None, op_group_id=None, origin="leave_page"
    )
    assert got == "pm_off"
    assert await _override(db, nurse.id, day) is None
    await db.refresh(v2)
    assert v2.primary_staff_id is None  # 戻さない
    await db.refresh(v)
    assert v.primary_staff_id == nurse.id
    assert len(await _notifications(db, "leave_cancelled")) == 1
    with pytest.raises(StaffLeaveError):
        await cancel_staff_leave(
            db, staff_id=nurse.id, day=day, actor=None, op_group_id=None, origin="leave_page"
        )


@pytest.mark.asyncio
async def test_undo_restores_everything_and_source(db) -> None:
    s = Seed(db)
    actor = await s.admin()
    nurse = await s.staff("戻る 休子")
    office = await s.office()
    day = _future()
    course = await s.course(office, day, nurse)
    v = await s.visit(day, "09:00", "10:00", staff=nurse, course=course)
    y, w, wd = iso_key(day)
    # 先に申請由来の午前休
    req = PendingRequest(
        requester_user_id=actor.id,
        request_type="staff_off",
        payload={"override_type": "am_off"},
        target_staff_id=nurse.id,
        target_date=day,
        status="approved",
    )
    db.add(req)
    await db.flush()
    await set_staff_override(
        db,
        staff_id=nurse.id,
        iso_year=y,
        iso_week=w,
        weekday=wd,
        override_type="am_off",
        source="request",
        pending_request_id=req.id,
    )
    await db.commit()
    month = day.replace(day=1)
    lm = await db.get(LeaveMonth, month)
    before = lm.last_leave_changed_at
    assert before is not None

    await apply_staff_leave(
        db,
        staff_id=nurse.id,
        day=day,
        leave_type="off",
        source="admin",
        note=None,
        actor=actor,
        op_group_id=None,
        origin="cockpit",
    )
    await db.commit()
    await execute_undo(db, user_id=actor.id, iso_year=y, iso_week=w)
    await db.commit()

    row = await _override(db, nurse.id, day)
    assert row is not None
    assert row.override_type == "am_off"
    assert row.source == "request"
    assert row.pending_request_id == req.id
    await db.refresh(v)
    await db.refresh(course)
    assert v.primary_staff_id == nurse.id
    assert course.assigned_staff_id == nurse.id
    await db.refresh(lm)
    assert lm.last_leave_changed_at is not None
    assert lm.last_leave_changed_at.replace(tzinfo=UTC) >= before.replace(tzinfo=UTC)
    # やり直しで終日休みに戻る
    await execute_redo(db, user_id=actor.id, iso_year=y, iso_week=w)
    await db.commit()
    row = await _override(db, nurse.id, day)
    assert row is not None and row.override_type == "off" and row.source == "admin"


@pytest.mark.asyncio
async def test_last_leave_changed_not_touched_by_custom_time_only(db) -> None:
    s = Seed(db)
    nurse = await s.staff("時間 変子")
    day = _future()
    y, w, wd = iso_key(day)
    await set_staff_override(
        db,
        staff_id=nurse.id,
        iso_year=y,
        iso_week=w,
        weekday=wd,
        override_type="custom_time",
        start_time="09:00",
        end_time="15:00",
    )
    assert await db.get(LeaveMonth, day.replace(day=1)) is None
    await set_staff_override(
        db, staff_id=nurse.id, iso_year=y, iso_week=w, weekday=wd, override_type="pm_off"
    )
    assert (await db.get(LeaveMonth, day.replace(day=1))).last_leave_changed_at is not None


# ---------------------------------------------------------------------------
# 後始末と検知
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settle_unassigns_future_only_and_detection(db) -> None:
    s = Seed(db)
    await s.admin()
    nurse = await s.staff("後始末 休子")
    future = _future()
    past = today_jst() - timedelta(days=2)
    for d in (future, past):
        y, w, wd = iso_key(d)
        await set_staff_override(
            db, staff_id=nurse.id, iso_year=y, iso_week=w, weekday=wd, override_type="am_off"
        )
    v_future = await s.visit(future, "09:00", "10:00", staff=nurse)
    v_past = await s.visit(past, "09:00", "10:00", staff=nurse)
    v_after = await s.visit(future, "13:00", "14:00", staff=nurse)

    found = await find_visits_on_leave(db, past, future)
    assert [f.visit_id for f in found] == [v_future.id]  # 過去日は検知しない
    assert await count_visits_on_leave(db, past, future) == 1

    n = await settle_leave_unassign(db, dates=[future, past])
    assert n == 1
    await db.refresh(v_future)
    await db.refresh(v_past)
    await db.refresh(v_after)
    assert v_future.primary_staff_id is None and v_future.manual_staff_override is True
    assert v_past.primary_staff_id == nurse.id  # 過去の日は触らない
    assert v_after.primary_staff_id == nurse.id
    assert len(await _notifications(db, "leave_unassigned")) == 1
    # 2 回目は何もしない (冪等)
    assert await settle_leave_unassign(db, dates=[future]) == 0
    assert await count_visits_on_leave(db, past, future) == 0


@pytest.mark.asyncio
async def test_settle_full_day_by_iso_week(db) -> None:
    s = Seed(db)
    nurse = await s.staff("週 休子")
    office = await s.office()
    day = _future()
    course = await s.course(office, day, nurse)
    v = await s.visit(day, "15:00", "16:00", staff=None, course=course)
    y, w, wd = iso_key(day)
    await set_staff_override(
        db, staff_id=nurse.id, iso_year=y, iso_week=w, weekday=wd, override_type="paid_off"
    )
    n = await settle_leave_unassign(db, iso_week=(y, w), notify=False)
    assert n == 1
    await db.refresh(course)
    await db.refresh(v)
    assert course.assigned_staff_id is None
    assert v.primary_staff_id is None and v.manual_staff_override is False
