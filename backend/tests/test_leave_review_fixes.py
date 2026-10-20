"""休みの段のレビュー①②の直し (入口・後始末・取込の件数など).

正典: docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md §2-1・§3・§4-2。
"""

from __future__ import annotations

from datetime import date, time, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import (
    Patient,
    PendingRequest,
    ScheduleOpLog,
    Staff,
    StaffWeeklyOverride,
    User,
    Visit,
)
from app.services.leave_calendar import iso_key
from app.services.op_log_service import set_staff_override
from app.services.staff_leave import today_jst


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _admin(db) -> User:
    u = User(
        email=f"rf-{uuid4().hex[:8]}@example.com", password_hash=hash_password("pw"), role="admin"
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _override(db, staff_id, d: date) -> StaffWeeklyOverride | None:
    y, w, wd = iso_key(d)
    return await db.scalar(
        select(StaffWeeklyOverride)
        .where(
            StaffWeeklyOverride.staff_id == staff_id,
            StaffWeeklyOverride.iso_year == y,
            StaffWeeklyOverride.iso_week == w,
            StaffWeeklyOverride.weekday == wd,
        )
        .execution_options(populate_existing=True)
    )


# ---------------------------------------------------------------------------
# ① MEDIUM-1・2: スタッフ詳細の PATCH
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_note_only_keeps_source_and_request(client, db) -> None:
    admin = await _admin(db)
    staff = Staff(name="メモ 花子")
    db.add(staff)
    await db.flush()
    req = PendingRequest(
        requester_user_id=admin.id,
        request_type="staff_off",
        payload={"override_type": "am_off"},
        target_staff_id=staff.id,
        status="approved",
    )
    db.add(req)
    await db.flush()
    day = today_jst() + timedelta(days=9)
    y, w, wd = iso_key(day)
    oid = await set_staff_override(
        db,
        staff_id=staff.id,
        iso_year=y,
        iso_week=w,
        weekday=wd,
        override_type="am_off",
        source="request",
        pending_request_id=req.id,
    )
    staff_id, req_id = staff.id, req.id
    await db.commit()
    res = await client.patch(
        f"/api/v1/staff/{staff_id}/overrides/{oid}",
        headers=_bearer(admin),
        json={"note": "通院（時刻の連絡あり）"},
    )
    assert res.status_code == 200, res.text
    row = await _override(db, staff_id, day)
    assert row is not None
    assert row.reason == "通院（時刻の連絡あり）"
    assert row.source == "request"
    assert row.pending_request_id == req_id


@pytest.mark.asyncio
async def test_patch_leave_to_custom_time_cancels_first_in_same_group(client, db) -> None:
    admin = await _admin(db)
    staff = Staff(name="切替 次郎")
    db.add(staff)
    await db.flush()
    day = today_jst() + timedelta(days=9)
    y, w, wd = iso_key(day)
    oid = await set_staff_override(
        db, staff_id=staff.id, iso_year=y, iso_week=w, weekday=wd, override_type="off"
    )
    staff_id, admin_id = staff.id, admin.id
    await db.commit()
    res = await client.patch(
        f"/api/v1/staff/{staff_id}/overrides/{oid}",
        headers=_bearer(admin),
        json={"type": "時間変更", "start_time": "09:00", "end_time": "12:00"},
    )
    assert res.status_code == 200, res.text
    row = await _override(db, staff_id, day)
    assert row is not None and row.override_type == "custom_time"
    logs = (
        await db.scalars(
            select(ScheduleOpLog)
            .where(ScheduleOpLog.user_id == admin_id, ScheduleOpLog.op_kind == "set_staff_off")
            .execution_options(populate_existing=True)
        )
    ).all()
    # 取り消し (休み→無し) が op_log に残る
    assert any(log.forward_payload.get("type") is None for log in logs)

    # 休みの日付を移すときも、元の日の休みは取り消しを通る
    res2 = await client.post(
        f"/api/v1/staff/{staff_id}/overrides",
        headers=_bearer(admin),
        json={"date": (day + timedelta(days=1)).isoformat(), "type": "休み"},
    )
    assert res2.status_code == 201, res2.text
    res3 = await client.patch(
        f"/api/v1/staff/{staff_id}/overrides/{res2.json()['id']}",
        headers=_bearer(admin),
        json={"date": (day + timedelta(days=2)).isoformat()},
    )
    assert res3.status_code == 200, res3.text
    assert await _override(db, staff_id, day + timedelta(days=1)) is None
    moved = await _override(db, staff_id, day + timedelta(days=2))
    assert moved is not None and moved.override_type == "off"


# ---------------------------------------------------------------------------
# ① MEDIUM-4: 終日の休みで、本人が primary を持つ他人のコースの訪問
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_day_leave_on_other_course_visit_is_explicit_unassigned(db) -> None:
    from app.models import Course, Office
    from app.services.staff_leave import apply_staff_leave

    office = Office(name="他人コース")
    owner = Staff(name="コース 持ち主")
    nurse = Staff(name="主担当 休子")
    patient = Patient(code="OC-1", name="他人コース患者", status="active", special_week_active=[])
    db.add_all([office, owner, nurse, patient])
    await db.flush()
    day = today_jst() + timedelta(days=5)
    y, w, wd = iso_key(day)
    course = Course(
        iso_year=y,
        iso_week=w,
        weekday=wd,
        code="B",
        office_id=office.id,
        course_status="staff_assigned",
        assigned_staff_id=owner.id,
    )
    db.add(course)
    await db.flush()
    v = Visit(
        patient_id=patient.id,
        primary_staff_id=nurse.id,
        course_id=course.id,
        visit_date=day,
        start_time=time(10, 0),
        end_time=time(11, 0),
        type="regular",
        status="planned",
        source="auto",
        manual_staff_override=True,
    )
    db.add(v)
    await db.flush()
    await apply_staff_leave(
        db,
        staff_id=nurse.id,
        day=day,
        leave_type="paid_off",
        source="admin",
        note=None,
        actor=None,
        op_group_id=None,
        origin="leave_page",
    )
    await db.refresh(v)
    await db.refresh(course)
    assert course.assigned_staff_id == owner.id  # 他人のコースは動かさない
    assert v.primary_staff_id is None
    assert v.manual_staff_override is True  # コース担当 (休んでいない人) に読み替えない


@pytest.mark.asyncio
async def test_legacy_secondary_column_is_released_on_leave(db) -> None:
    from app.services.staff_leave import apply_staff_leave, count_visits_on_leave

    lead = Staff(name="主 担当2")
    nurse = Staff(name="二人目 列だけ")
    patient = Patient(code="SC-1", name="二人目患者", status="active", special_week_active=[])
    db.add_all([lead, nurse, patient])
    await db.flush()
    day = today_jst() + timedelta(days=5)
    v = Visit(
        patient_id=patient.id,
        primary_staff_id=lead.id,
        secondary_staff_id=nurse.id,
        visit_date=day,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status="planned",
        source="auto",
    )
    db.add(v)
    y, w, wd = iso_key(day)
    await set_staff_override(
        db, staff_id=nurse.id, iso_year=y, iso_week=w, weekday=wd, override_type="am_off"
    )
    await db.flush()
    assert await count_visits_on_leave(db, day, day) == 1
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
    assert res.moved_visit_ids == [v.id]
    await db.refresh(v)
    assert v.primary_staff_id == lead.id
    assert v.secondary_staff_id is None
