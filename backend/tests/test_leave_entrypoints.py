"""休みの段 #5: 入口をすべて共通の処理へ・申請の作成の検証・締め切り・変更のお願い.

正典: docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md §2-3・§3-1・付録 A-1・§11-1「申請」。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import (
    Notification,
    Patient,
    PendingRequest,
    Staff,
    StaffWeeklyOverride,
    User,
    Visit,
)
from app.models.staff import StaffShiftConfirmation
from app.services.leave_calendar import iso_key
from app.services.op_log_service import set_staff_override
from app.services.staff_leave import is_past_leave_deadline, leave_request_deadline, today_jst

JST = ZoneInfo("Asia/Tokyo")
FAR = date(2030, 5, 13)  # 締め切り (2030-04-20) はまだ先


async def _user(db, role: str, staff_id=None) -> User:
    from uuid import uuid4

    u = User(
        email=f"ep-{uuid4().hex[:8]}@example.com",
        password_hash=hash_password("pw"),
        role=role,
        staff_id=staff_id,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _staff(db, name="申請 花子") -> Staff:
    s = Staff(name=name)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


def _req(staff_id, d: date, override_type="off", **extra) -> dict:
    payload = {"staff_id": str(staff_id), "date": d.isoformat(), "override_type": override_type}
    payload.update(extra)
    return {
        "request_type": "staff_off",
        "payload": payload,
        "target_staff_id": str(staff_id),
        "target_date": d.isoformat(),
    }


async def _set(db, staff_id, d: date, t: str, **kw) -> None:
    y, w, wd = iso_key(d)
    await set_staff_override(
        db, staff_id=staff_id, iso_year=y, iso_week=w, weekday=wd, override_type=t, **kw
    )
    await db.commit()


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
# 締め切り (JST 前の月の 20 日 23:59)
# ---------------------------------------------------------------------------


def test_deadline_boundaries_jst() -> None:
    target = date(2026, 12, 10)
    assert leave_request_deadline(target) == datetime(2026, 11, 21, tzinfo=JST)
    assert not is_past_leave_deadline(target, datetime(2026, 11, 20, 23, 59, tzinfo=JST))
    assert is_past_leave_deadline(target, datetime(2026, 11, 21, 0, 0, tzinfo=JST))
    # UTC で 11/20 15:00 = JST 11/21 0:00 → 締め切り後
    assert is_past_leave_deadline(target, datetime(2026, 11, 20, 15, 0, tzinfo=UTC))
    assert not is_past_leave_deadline(target, datetime(2026, 11, 20, 14, 59, tzinfo=UTC))


def test_deadline_year_end() -> None:
    # 1 月の休みの締め切りは前の年の 12/20 23:59
    assert leave_request_deadline(date(2027, 1, 5)) == datetime(2026, 12, 21, tzinfo=JST)
    # 月末の日でも同じ月の締め切り
    assert leave_request_deadline(date(2026, 3, 31)) == datetime(2026, 2, 21, tzinfo=JST)


# ---------------------------------------------------------------------------
# 申請の作成
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_type_is_422_and_cancel_needs_change(client, db) -> None:
    staff = await _staff(db)
    user = await _user(db, "staff", staff.id)
    res = await client.post(
        "/api/v1/pending-requests", headers=_bearer(user), json=_req(staff.id, FAR, "custom_time")
    )
    assert res.status_code == 422, res.text
    res = await client.post(
        "/api/v1/pending-requests", headers=_bearer(user), json=_req(staff.id, FAR, "cancel")
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
@pytest.mark.parametrize("leave_type", ["off", "am_off", "pm_off", "paid_off"])
async def test_each_type_can_be_requested(client, db, leave_type) -> None:
    staff = await _staff(db)
    user = await _user(db, "staff", staff.id)
    res = await client.post(
        "/api/v1/pending-requests", headers=_bearer(user), json=_req(staff.id, FAR, leave_type)
    )
    assert res.status_code == 201, res.text
    assert res.json()["payload"]["override_type"] == leave_type
    assert "kind" not in res.json()["payload"]


@pytest.mark.asyncio
async def test_after_deadline_without_kind_is_422_and_change_needs_note(client, db) -> None:
    staff = await _staff(db)
    user = await _user(db, "staff", staff.id)
    day = today_jst() + timedelta(days=1)  # 締め切り (前の月の 20 日) は過ぎている
    # kind なし → 422 (理由があっても)
    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(user),
        json=_req(staff.id, day, note="通院のため"),
    )
    assert res.status_code == 422, res.text
    assert "締め切りを過ぎたため「変更をお願いする」から送ってください" in res.json()["detail"]
    # 変更のお願いで note が空 → 422
    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(user),
        json=_req(staff.id, day, kind="change", note="  "),
    )
    assert res.status_code == 422, res.text
    # 変更のお願い＋理由 → 受け付ける (理由は payload.note のまま)
    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(user),
        json=_req(staff.id, day, kind="change", note="通院のため"),
    )
    assert res.status_code == 201, res.text
    body = res.json()["payload"]
    assert body["kind"] == "change"
    assert body["note"] == "通院のため"
    assert "reason" not in body


@pytest.mark.asyncio
async def test_confirmed_month_without_kind_is_422(client, db) -> None:
    staff = await _staff(db)
    user = await _user(db, "staff", staff.id)
    db.add(
        StaffShiftConfirmation(
            staff_id=staff.id, month=FAR.replace(day=1), confirmed_at=datetime.now(UTC)
        )
    )
    await db.commit()
    res = await client.post(
        "/api/v1/pending-requests", headers=_bearer(user), json=_req(staff.id, FAR, note="家の用事")
    )
    assert res.status_code == 422, res.text
    assert "変更をお願いする" in res.json()["detail"]
    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(user),
        json=_req(staff.id, FAR, kind="change", note="家の用事"),
    )
    assert res.status_code == 201, res.text


@pytest.mark.asyncio
async def test_conflict_rules(client, db) -> None:
    staff = await _staff(db)
    user = await _user(db, "staff", staff.id)
    d_custom = FAR
    d_leave = FAR + timedelta(days=1)
    await _set(db, staff.id, d_custom, "custom_time", start_time="09:00", end_time="15:00")
    await _set(db, staff.id, d_leave, "am_off")

    # 時間変更の日には普通の申請も作れる
    res = await client.post(
        "/api/v1/pending-requests", headers=_bearer(user), json=_req(staff.id, d_custom)
    )
    assert res.status_code == 201, res.text
    # 同じ日の申請中は 409
    res = await client.post(
        "/api/v1/pending-requests", headers=_bearer(user), json=_req(staff.id, d_custom, "pm_off")
    )
    assert res.status_code == 409, res.text
    # 休みの日の普通の申請は 409
    res = await client.post(
        "/api/v1/pending-requests", headers=_bearer(user), json=_req(staff.id, d_leave, "off")
    )
    assert res.status_code == 409, res.text
    # 今と同じ種類への変更のお願いは 422
    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(user),
        json=_req(staff.id, d_leave, "am_off", kind="change", note="同じ"),
    )
    assert res.status_code == 422, res.text
    # 変更のお願い (種類の変更) は休みの日でも作れる
    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(user),
        json=_req(staff.id, d_leave, "pm_off", kind="change", note="午後に変えたい"),
    )
    assert res.status_code == 201, res.text


@pytest.mark.asyncio
async def test_cancel_change_requires_existing_leave_and_approval_removes(client, db) -> None:
    staff = await _staff(db)
    user = await _user(db, "staff", staff.id)
    admin = await _user(db, "admin")
    no_leave = FAR + timedelta(days=2)
    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(user),
        json=_req(staff.id, no_leave, "cancel", kind="change", note="出勤したい"),
    )
    assert res.status_code == 422, res.text

    d = FAR + timedelta(days=3)
    await _set(db, staff.id, d, "off")
    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(user),
        json=_req(staff.id, d, "cancel", kind="change", note="出勤したい"),
    )
    assert res.status_code == 201, res.text
    rid = res.json()["id"]
    res = await client.patch(
        f"/api/v1/pending-requests/{rid}/approve", headers=_bearer(admin), json={}
    )
    assert res.status_code == 200, res.text
    assert await _override(db, staff.id, d) is None


# ---------------------------------------------------------------------------
# 承認 → 共通の処理
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_half_day_moves_visits_and_notifies(client, db) -> None:
    staff = await _staff(db)
    user = await _user(db, "staff", staff.id)
    admin = await _user(db, "admin")
    day = today_jst() + timedelta(days=3)
    patient = Patient(code="EP-1", name="患者", status="active", special_week_active=[])
    db.add(patient)
    await db.flush()
    morning = Visit(
        patient_id=patient.id,
        primary_staff_id=staff.id,
        visit_date=day,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status="planned",
        source="auto",
    )
    afternoon = Visit(
        patient_id=patient.id,
        primary_staff_id=staff.id,
        visit_date=day,
        start_time=time(14, 0),
        end_time=time(15, 0),
        type="regular",
        status="planned",
        source="auto",
    )
    db.add_all([morning, afternoon])
    await db.flush()
    morning_id, afternoon_id = morning.id, afternoon.id
    await db.commit()
    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(user),
        json=_req(staff.id, day, "am_off", kind="change", note="通院"),
    )
    assert res.status_code == 201, res.text
    rid = res.json()["id"]
    res = await client.patch(
        f"/api/v1/pending-requests/{rid}/approve", headers=_bearer(admin), json={}
    )
    assert res.status_code == 200, res.text
    row = await _override(db, staff.id, day)
    assert row is not None and row.override_type == "am_off" and row.source == "request"
    assert str(row.pending_request_id) == rid
    m = await db.get(Visit, morning_id, populate_existing=True)
    a = await db.get(Visit, afternoon_id, populate_existing=True)
    assert m is not None and m.primary_staff_id is None and m.manual_staff_override is True
    assert a is not None and a.primary_staff_id == staff.id
    approved = (
        await db.scalars(
            select(Notification)
            .where(Notification.type == "leave_approved")
            .execution_options(populate_existing=True)
        )
    ).all()
    assert len(approved) == 1 and approved[0].user_id == user.id
    unassigned = (
        await db.scalars(select(Notification).where(Notification.type == "leave_unassigned"))
    ).all()
    assert len(unassigned) == 1 and unassigned[0].user_id == admin.id


@pytest.mark.asyncio
async def test_approve_change_upserts_type(client, db) -> None:
    staff = await _staff(db)
    user = await _user(db, "staff", staff.id)
    admin = await _user(db, "admin")
    d = FAR + timedelta(days=4)
    await _set(db, staff.id, d, "off")
    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(user),
        json=_req(staff.id, d, "pm_off", kind="change", note="午後だけに"),
    )
    rid = res.json()["id"]
    res = await client.patch(
        f"/api/v1/pending-requests/{rid}/approve", headers=_bearer(admin), json={}
    )
    assert res.status_code == 200, res.text
    row = await _override(db, staff.id, d)
    assert row is not None and row.override_type == "pm_off"
    rows = (
        await db.scalars(
            select(StaffWeeklyOverride).where(StaffWeeklyOverride.staff_id == staff.id)
        )
    ).all()
    assert len(rows) == 1
    req = await db.get(PendingRequest, UUID(rid), populate_existing=True)
    assert req is not None and req.status == "approved"


# ---------------------------------------------------------------------------
# スタッフ詳細の API・運転席
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_staff_detail_post_leave_moves_visits_custom_time_does_not(client, db) -> None:
    staff = await _staff(db)
    admin = await _user(db, "admin")
    day = today_jst() + timedelta(days=2)
    patient = Patient(code="EP-2", name="患者2", status="active", special_week_active=[])
    db.add(patient)
    await db.flush()
    v = Visit(
        patient_id=patient.id,
        primary_staff_id=staff.id,
        visit_date=day,
        start_time=time(15, 0),
        end_time=time(16, 0),
        type="regular",
        status="planned",
        source="auto",
    )
    db.add(v)
    await db.flush()
    vid = v.id
    await db.commit()
    res = await client.post(
        f"/api/v1/staff/{staff.id}/overrides",
        headers=_bearer(admin),
        json={"date": day.isoformat(), "type": "午後休"},
    )
    assert res.status_code == 201, res.text
    assert res.json()["type"] == "午後休"
    got = await db.get(Visit, vid, populate_existing=True)
    assert got is not None and got.primary_staff_id is None and got.manual_staff_override is True

    # 時間変更へ変えると行だけ (訪問はもう担当なしのまま)・source は admin
    oid = res.json()["id"]
    res = await client.patch(
        f"/api/v1/staff/{staff.id}/overrides/{oid}",
        headers=_bearer(admin),
        json={"type": "時間変更", "start_time": "09:00", "end_time": "12:00"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["type"] == "時間変更"


@pytest.mark.asyncio
async def test_staff_off_week_accepts_half_day(client, db) -> None:
    staff = await _staff(db)
    admin = await _user(db, "admin")
    day = today_jst() + timedelta(days=2)
    patient = Patient(code="EP-3", name="患者3", status="active", special_week_active=[])
    db.add(patient)
    await db.flush()
    v_am = Visit(
        patient_id=patient.id,
        primary_staff_id=staff.id,
        visit_date=day,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status="planned",
        source="auto",
    )
    v_pm = Visit(
        patient_id=patient.id,
        primary_staff_id=staff.id,
        visit_date=day,
        start_time=time(14, 0),
        end_time=time(15, 0),
        type="regular",
        status="planned",
        source="auto",
    )
    db.add_all([v_am, v_pm])
    await db.flush()
    pm_id = v_pm.id
    await db.commit()
    res = await client.post(
        "/api/v1/schedule/v2/staff-off-week",
        headers=_bearer(admin),
        json={"staff_id": str(staff.id), "date": day.isoformat(), "leave_type": "pm_off"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["moved_visit_ids"] == [str(pm_id)]
    row = await _override(db, staff.id, day)
    assert row is not None and row.override_type == "pm_off"
