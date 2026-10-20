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


# ---------------------------------------------------------------------------
# ① MEDIUM-3: leave_months の upsert・UNIQUE の衝突は 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_touch_leave_month_upserts_when_row_exists_outside_session(db) -> None:
    import sqlalchemy as sa

    from app.models import LeaveMonth

    staff = Staff(name="月 行あり")
    db.add(staff)
    await db.flush()
    day = today_jst() + timedelta(days=40)
    month = day.replace(day=1)
    # 別の処理が先に作った行 (このセッションには読み込まれていない)
    await db.execute(sa.insert(LeaveMonth).values(month=month, required_hours=160))
    y, w, wd = iso_key(day)
    await set_staff_override(
        db, staff_id=staff.id, iso_year=y, iso_week=w, weekday=wd, override_type="off"
    )
    row = await db.get(LeaveMonth, month, populate_existing=True)
    assert row is not None
    assert row.required_hours == 160
    assert row.last_leave_changed_at is not None


@pytest.mark.asyncio
async def test_unique_collision_on_override_write_is_409(client, db, monkeypatch) -> None:
    from sqlalchemy.exc import IntegrityError

    import app.api.v1.staff_overrides as mod

    async def boom(*_a, **_k):
        raise IntegrityError("INSERT", {}, Exception("UNIQUE constraint failed"))

    monkeypatch.setattr(mod, "set_staff_override", boom)
    admin = await _admin(db)
    staff = Staff(name="同時 書き")
    db.add(staff)
    await db.commit()
    res = await client.post(
        f"/api/v1/staff/{staff.id}/overrides",
        headers=_bearer(admin),
        json={
            "date": (today_jst() + timedelta(days=3)).isoformat(),
            "type": "時間変更",
            "start_time": "09:00",
            "end_time": "12:00",
        },
    )
    assert res.status_code == 409, res.text
    assert "同じ日の休みが同時に入れられました" in res.json()["detail"]


# ---------------------------------------------------------------------------
# ② MEDIUM-1・HIGH-2: PATCH /courses と「戻る」・今週だけ動かす
# ---------------------------------------------------------------------------

_UNDO_URL = "/api/v1/schedule/v2/op-log/undo"


async def _course_setup(db, *, leave_type: str | None):
    from app.models import Course, CourseTemplate, Office

    office = Office(name=f"コース拠点{uuid4().hex[:4]}")
    a = Staff(name="元の 担当", status="active")
    b = Staff(name="新しい 担当", status="active")
    patient = Patient(
        code=f"CP-{uuid4().hex[:6]}", name="コース患者", status="active", special_week_active=[]
    )
    db.add_all([office, a, b, patient])
    await db.flush()
    tpl = CourseTemplate(office_id=office.id, label="A")
    db.add(tpl)
    await db.flush()
    day = today_jst() + timedelta(days=6)
    y, w, wd = iso_key(day)
    course = Course(
        iso_year=y,
        iso_week=w,
        weekday=wd,
        code="A",
        office_id=office.id,
        template_id=tpl.id,
        course_status="staff_assigned",
        assigned_staff_id=a.id,
    )
    db.add(course)
    await db.flush()
    visits = {}
    for h in (9, 14):
        v = Visit(
            patient_id=patient.id,
            primary_staff_id=a.id,
            course_id=course.id,
            visit_date=day,
            start_time=time(h, 0),
            end_time=time(h + 1, 0),
            type="regular",
            status="planned",
            source="auto",
        )
        db.add(v)
        visits[h] = v
    if leave_type is not None:
        await set_staff_override(
            db, staff_id=b.id, iso_year=y, iso_week=w, weekday=wd, override_type=leave_type
        )
    await db.commit()
    return {
        "office": office,
        "a": a.id,
        "b": b.id,
        "b_name": b.name,
        "course": course.id,
        "patient": patient.id,
        "day": day,
        "visits": {h: v.id for h, v in visits.items()},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("leave_type", ["off", "paid_off"])
async def test_patch_course_to_full_day_leave_staff_is_422(client, db, leave_type) -> None:
    admin = await _admin(db)
    s = await _course_setup(db, leave_type=leave_type)
    res = await client.patch(
        f"/api/v1/courses/{s['course']}",
        headers=_bearer(admin),
        json={"assigned_staff_id": str(s["b"])},
    )
    assert res.status_code == 422, res.text
    assert f"{s['b_name']}さんはこの日休みです" in res.text


@pytest.mark.asyncio
async def test_patch_course_half_leave_settles_and_undo_restores(client, db) -> None:
    admin = await _admin(db)
    s = await _course_setup(db, leave_type="pm_off")
    res = await client.patch(
        f"/api/v1/courses/{s['course']}",
        headers=_bearer(admin),
        json={"assigned_staff_id": str(s["b"])},
    )
    assert res.status_code == 200, res.text
    am = await db.get(Visit, s["visits"][9], populate_existing=True)
    pm = await db.get(Visit, s["visits"][14], populate_existing=True)
    assert am is not None and am.primary_staff_id == s["b"]
    assert pm is not None and pm.primary_staff_id is None and pm.manual_staff_override is True

    y, w, _ = iso_key(s["day"])
    undo = await client.post(_UNDO_URL, headers=_bearer(admin), json={"iso_year": y, "iso_week": w})
    assert undo.status_code == 200, undo.text
    from app.models import Course

    am = await db.get(Visit, s["visits"][9], populate_existing=True)
    pm = await db.get(Visit, s["visits"][14], populate_existing=True)
    course = await db.get(Course, s["course"], populate_existing=True)
    assert course is not None and course.assigned_staff_id == s["a"]
    assert am is not None and am.primary_staff_id == s["a"]
    assert pm is not None and pm.primary_staff_id == s["a"]
    assert pm.manual_staff_override is False


@pytest.mark.asyncio
async def test_move_week_only_keeps_explicit_unassigned_and_undo(client, db) -> None:
    from app.models import Course, CourseTemplate

    admin = await _admin(db)
    s = await _course_setup(db, leave_type=None)
    # 14:00 の訪問は明示の担当なし (休みの時間帯などで外した)
    pm = await db.get(Visit, s["visits"][14])
    assert pm is not None
    pm.primary_staff_id = None
    pm.manual_staff_override = True
    # 移動先のコース (別テンプレート・担当あり)
    tpl_b = CourseTemplate(office_id=s["office"].id, label="B")
    db.add(tpl_b)
    await db.flush()
    y, w, wd = iso_key(s["day"])
    db.add(
        Course(
            iso_year=y,
            iso_week=w,
            weekday=wd,
            code="B",
            office_id=s["office"].id,
            template_id=tpl_b.id,
            course_status="staff_assigned",
            assigned_staff_id=s["b"],
        )
    )
    tpl_b_id = tpl_b.id
    await db.commit()
    res = await client.post(
        "/api/v1/schedule/v2/visit-move-week-only",
        headers=_bearer(admin),
        json={
            "iso_year": y,
            "iso_week": w,
            "patient_id": str(s["patient"]),
            "old_weekday": wd,
            "old_start_time": "14:00",
            "new_weekday": wd,
            "new_start_time": "15:00",
            "new_course_template_id": str(tpl_b_id),
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["visits_moved"] == 1
    moved = await db.get(Visit, s["visits"][14], populate_existing=True)
    assert moved is not None
    assert moved.start_time == time(15, 0)
    assert moved.primary_staff_id is None
    assert moved.manual_staff_override is True

    undo = await client.post(_UNDO_URL, headers=_bearer(admin), json={"iso_year": y, "iso_week": w})
    assert undo.status_code == 200, undo.text
    back = await db.get(Visit, s["visits"][14], populate_existing=True)
    assert back is not None
    assert back.start_time == time(14, 0)
    assert back.primary_staff_id is None
    assert back.manual_staff_override is True
