"""休みの段 #8: 「休み・勤務表」の月の API (app/api/v1/leave.py).

正典: docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md §2-2・§5・§11-1「数え方」。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select

from app.core.security import create_access_token, hash_password
from app.models import (
    Course,
    Notification,
    Office,
    Patient,
    PendingRequest,
    Staff,
    StaffWeeklyOverride,
    User,
    Visit,
)
from app.models.leave import OfficeClosedDay, StaffLeaveKaipoke
from app.models.staff import StaffShift, StaffShiftConfirmation
from app.services.leave_calendar import iso_key, month_days, required_holidays
from app.services.op_log_service import set_staff_override
from app.services.staff_leave import count_visits_on_leave

MONTH = date(2030, 11, 1)
TUE = date(2030, 11, 12)  # 火曜
WED = date(2030, 11, 13)
THU = date(2030, 11, 14)
SAT = date(2030, 11, 16)
PAID = date(2030, 11, 20)
CLOSED = date(2030, 11, 26)  # 火曜 (祝日でない)
WEEKDAYS = (0, 1, 2, 3, 4)


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _user(db, role: str, staff_id: UUID | None = None) -> User:
    u = User(
        email=f"lm-{uuid4().hex[:8]}@example.com",
        password_hash=hash_password("pw"),
        role=role,
        staff_id=staff_id,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _staff(
    db,
    name: str,
    *,
    code: str,
    weekdays: tuple[int, ...] = WEEKDAYS,
    employment: str = "full_time",
    trainee: bool = False,
    status: str = "active",
) -> Staff:
    s = Staff(name=name, code=code, employment_type=employment, is_trainee=trainee, status=status)
    db.add(s)
    await db.flush()
    for wd in range(7):
        db.add(
            StaffShift(
                staff_id=s.id,
                weekday=wd,
                is_on=wd in weekdays,
                start_time=time(9, 0),
                end_time=time(18, 0),
            )
        )
    await db.commit()
    return s


async def _leave(db, staff: Staff, d: date, t: str, **kw) -> None:
    y, w, wd = iso_key(d)
    await set_staff_override(
        db, staff_id=staff.id, iso_year=y, iso_week=w, weekday=wd, override_type=t, **kw
    )
    await db.commit()


async def _visit(db, staff: Staff | None, d: date, start: str, end: str, course=None) -> Visit:
    p = Patient(code=f"P-{uuid4().hex[:8]}", name="患者", status="active", special_week_active=[])
    db.add(p)
    await db.flush()
    sh, sm = (int(x) for x in start.split(":"))
    eh, em = (int(x) for x in end.split(":"))
    v = Visit(
        patient_id=p.id,
        primary_staff_id=staff.id if staff else None,
        course_id=course.id if course else None,
        visit_date=d,
        start_time=time(sh, sm),
        end_time=time(eh, em),
        type="regular",
        status="planned",
        source="auto",
    )
    db.add(v)
    await db.commit()
    return v


async def _course(db, d: date, staff: Staff | None) -> Course:
    o = Office(name=f"拠点{uuid4().hex[:4]}")
    db.add(o)
    await db.flush()
    y, w, wd = iso_key(d)
    c = Course(
        iso_year=y,
        iso_week=w,
        weekday=wd,
        code="A",
        office_id=o.id,
        course_status="staff_assigned",
        assigned_staff_id=staff.id if staff else None,
    )
    db.add(c)
    await db.commit()
    return c


async def _request(
    db, requester: User, staff: Staff, d: date, t: str, *, kind: str | None = None, note=None
) -> PendingRequest:
    payload = {"staff_id": str(staff.id), "date": d.isoformat(), "override_type": t}
    if kind:
        payload["kind"] = kind
    if note:
        payload["note"] = note
    r = PendingRequest(
        requester_user_id=requester.id,
        request_type="staff_off",
        payload=payload,
        target_staff_id=staff.id,
        target_date=d,
        status="pending",
    )
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return r


async def _override(db, staff: Staff, d: date) -> StaffWeeklyOverride | None:
    y, w, wd = iso_key(d)
    return await db.scalar(
        select(StaffWeeklyOverride)
        .where(
            StaffWeeklyOverride.staff_id == staff.id,
            StaffWeeklyOverride.iso_year == y,
            StaffWeeklyOverride.iso_week == w,
            StaffWeeklyOverride.weekday == wd,
        )
        .execution_options(populate_existing=True)
    )


def _cell(body: dict, staff: Staff, d: date) -> dict | None:
    return next(
        (c for c in body["cells"] if c["staff_id"] == str(staff.id) and c["date"] == d.isoformat()),
        None,
    )


def _row(body: dict, staff: Staff) -> dict:
    return next(s for s in body["staff"] if s["id"] == str(staff.id))


def _day(body: dict, d: date) -> dict:
    return next(x for x in body["days"] if x["date"] == d.isoformat())


def _weekend_days(month: date) -> int:
    return sum(1 for d in month_days(month) if d.weekday() >= 5)


# ---------------------------------------------------------------------------
# 月の表の中身
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_month_table_contents(client, db) -> None:
    admin = await _user(db, "admin")
    a = await _staff(db, "職員A", code="001")
    b = await _staff(db, "職員B", code="002")
    c = await _staff(db, "新人", code="003", trainee=True)
    d = await _staff(db, "非常勤", code="004", weekdays=(0, 2, 4), employment="part_time")
    await _staff(db, "退職なし", code="005", status="retired")
    f = await _staff(db, "退職あり", code="006", status="retired")
    d_user = await _user(db, "staff", d.id)

    await _leave(db, a, TUE, "off", source="admin", reason="私用")
    await _leave(db, b, TUE, "am_off", source="request")
    await _leave(db, c, TUE, "off")  # 新人は数えない
    await _leave(db, a, SAT, "off")  # 型で勤務しない曜日は数えない
    await _leave(db, a, PAID, "paid_off")
    await _leave(db, a, THU, "custom_time", start_time="09:00", end_time="15:00")
    await _leave(db, f, TUE, "off")
    db.add(OfficeClosedDay(date=CLOSED, label="創立記念日"))
    db.add(StaffLeaveKaipoke(staff_id=a.id, leave_date=TUE, sent_type=None, baseline_pending=True))
    db.add(StaffLeaveKaipoke(staff_id=b.id, leave_date=TUE, sent_type="am_off"))
    await db.commit()
    course = await _course(db, TUE, None)
    await _visit(db, a, TUE, "09:00", "10:00", course)  # 休みの時間帯に担当 = 漏れ 1 件
    await _visit(db, d, WED, "10:00", "11:00")  # 申請が通れば移る
    pend = await _request(db, d_user, d, WED, "paid_off", note="法事")
    # 承認後に取り消し (申請は承認済み・休みは無い)
    done = await _request(db, d_user, d, date(2030, 11, 18), "off")
    done.status = "approved"
    await db.commit()

    res = await client.get("/api/v1/leave/month/2030-11", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()

    required = required_holidays(MONTH, [CLOSED])
    assert body["required_holidays"] == required
    assert body["month"] == "2030-11-01"
    assert [s["name"] for s in body["staff"]] == ["職員A", "職員B", "新人", "非常勤", "退職あり"]

    # 見出し: 祝日の名前・事業所の休み・作り済みの週・その日休む人
    holidays = {h["date"]: h["name"] for h in body["holidays"]}
    assert holidays  # 11 月は文化の日・勤労感謝の日
    for h_date, name in holidays.items():
        assert _day(body, date.fromisoformat(h_date))["holiday_name"] == name
    assert _day(body, CLOSED)["closed_label"] == "創立記念日"
    tue = _day(body, TUE)
    assert tue["week_generated"] is True
    assert _day(body, date(2030, 11, 4))["week_generated"] is False
    # 職員A (公休)・職員B (午前休 = 1 人) → 2 人。新人と退職者 (在籍でない) は数えない。
    assert tue["off_count"] == 2 and tue["off_warn"] is True
    assert _day(body, SAT)["off_count"] == 0
    assert _day(body, PAID)["off_count"] == 1 and _day(body, PAID)["off_warn"] is False
    assert _day(body, WED)["pending_count"] == 1

    # マス
    ca = _cell(body, a, TUE)
    assert ca["leave_type"] == "off" and ca["source"] == "admin" and ca["note"] == "私用"
    assert ca["visit_count"] == 1 and ca["send_status"] == "baseline"
    assert _cell(body, b, TUE)["send_status"] == "sent"
    assert _cell(body, b, TUE)["leave_type"] == "am_off"
    tc = _cell(body, a, THU)
    assert tc["leave_type"] is None and tc["time_change_start"] == "09:00:00"
    assert _cell(body, d, WED)["pending"]["override_type"] == "paid_off"
    assert _cell(body, d, date(2030, 11, 18))["cancelled_after_approval"] is True
    assert _cell(body, a, date(2030, 11, 5)) is None  # 何もないマスは返さない

    # 職員の集計 (休日数・あと N 日・有給・非常勤は対象外)
    weekend = _weekend_days(MONTH)
    ra = _row(body, a)
    assert ra["off_weekdays"] == [5, 6]
    assert ra["holidays"] == weekend + 1  # 土曜の公休は型の休みと同じ 1 日
    assert ra["paid_days"] == 1
    assert ra["shortfall"] == max(0, required - (weekend + 1))
    assert _row(body, b)["holidays"] == weekend + 0.5
    rd = _row(body, d)
    assert rd["is_full_time"] is False and rd["required_holidays"] is None
    assert rd["shortfall"] is None and rd["pending_count"] == 1
    assert rd["pending_gain"] == 0  # 有給は休日数に数えない

    # 申請中の一覧
    [item] = body["pending"]
    assert item["id"] == str(pend.id) and item["note"] == "法事"
    assert item["move_visit_count"] == 1
    assert item["off_count_now"] == 0 and item["off_count_if_approved"] == 1
    assert item["staff_order"] == 3

    # 照合前・検知・確定
    assert body["compare"]["baseline_pending_count"] == 1
    assert body["compare"]["last_compared_at"] is None
    assert body["visits_on_leave_count"] == 1
    assert body["visits_on_leave_count"] == await count_visits_on_leave(
        db, MONTH, date(2030, 11, 30)
    )
    assert body["confirmation"]["confirmed"] is False
    assert body["confirmation"]["staff_count"] == 5


@pytest.mark.asyncio
async def test_month_must_be_first_day(client, db) -> None:
    admin = await _user(db, "admin")
    res = await client.get("/api/v1/leave/month/2030-11-15", headers=_bearer(admin))
    assert res.status_code == 422
    res = await client.get("/api/v1/leave/month/2030-11-01", headers=_bearer(admin))
    assert res.status_code == 200
    res = await client.put(
        "/api/v1/leave/months/2030-11-02", headers=_bearer(admin), json={"required_hours": 160}
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_year_end_iso_week_53(client, db) -> None:
    """2027-01-01 は ISO 2026 年の第 53 週。1 月の表に載り、12 月の表には載らない."""
    admin = await _user(db, "admin")
    a = await _staff(db, "職員A", code="001")
    await _leave(db, a, date(2027, 1, 1), "off")
    await _leave(db, a, date(2026, 12, 31), "pm_off")
    jan = (await client.get("/api/v1/leave/month/2027-01", headers=_bearer(admin))).json()
    assert _cell(jan, a, date(2027, 1, 1))["leave_type"] == "off"
    assert _cell(jan, a, date(2026, 12, 31)) is None
    dec = (await client.get("/api/v1/leave/month/2026-12", headers=_bearer(admin))).json()
    assert _cell(dec, a, date(2026, 12, 31))["leave_type"] == "pm_off"
    assert _cell(dec, a, date(2027, 1, 1)) is None


@pytest.mark.asyncio
async def test_month_query_count_does_not_grow(client, db) -> None:
    """職員・休み・訪問が増えてもクエリの回数は同じ (N+1 にしない)."""
    from app.db import session as db_session

    admin = await _user(db, "admin")
    engine = db_session.get_engine().sync_engine

    async def _count() -> int:
        captured: list[str] = []

        def _listener(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
            captured.append(statement)

        event.listen(engine, "before_cursor_execute", _listener)
        try:
            res = await client.get("/api/v1/leave/month/2030-11", headers=_bearer(admin))
        finally:
            event.remove(engine, "before_cursor_execute", _listener)
        assert res.status_code == 200, res.text
        return len(captured)

    async def _add(n: int, offset: int) -> None:
        for i in range(n):
            s = await _staff(db, f"職員{offset + i}", code=f"{offset + i:03d}")
            u = await _user(db, "staff", s.id)
            for day in (TUE, WED, THU, PAID):
                await _leave(db, s, day, "am_off")
                await _visit(db, s, day, "09:00", "10:00")
            await _request(db, u, s, date(2030, 11, 21), "off")

    await _add(2, 0)
    small = await _count()
    await _add(13, 100)
    large = await _count()
    assert large == small
    assert large <= 20


# ---------------------------------------------------------------------------
# 入れる・取り消す・種類を変える
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_change_and_cancel_day(client, db) -> None:
    admin = await _user(db, "admin")
    a = await _staff(db, "職員A", code="001")
    await _user(db, "staff", a.id)
    v_am = await _visit(db, a, TUE, "09:00", "10:00")
    v_pm = await _visit(db, a, TUE, "14:00", "15:00")

    res = await client.post(
        "/api/v1/leave/day",
        headers=_bearer(admin),
        json={"staff_id": str(a.id), "date": TUE.isoformat(), "leave_type": "am_off"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["moved_visit_count"] == 1 and body["past_day"] is False
    row = await _override(db, a, TUE)
    assert row.override_type == "am_off" and row.source == "admin"
    await db.refresh(v_am)
    await db.refresh(v_pm)
    assert v_am.primary_staff_id is None and v_am.manual_staff_override is True
    assert v_pm.primary_staff_id == a.id

    # 種類を変える (upsert)
    res = await client.post(
        "/api/v1/leave/day",
        headers=_bearer(admin),
        json={"staff_id": str(a.id), "date": TUE.isoformat(), "leave_type": "off"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["moved_visit_count"] == 1  # 午後の訪問も移る
    assert (await _override(db, a, TUE)).override_type == "off"

    # 取り消す (訪問は戻さない・本人へ通知)
    res = await client.delete(
        "/api/v1/leave/day",
        headers=_bearer(admin),
        params={"staff_id": str(a.id), "date": TUE.isoformat()},
    )
    assert res.status_code == 200, res.text
    assert res.json()["cancelled_type"] == "off"
    # 午前休で 1 件・公休に変えて 1 件 = 担当なしへ移した 2 件 (戻らない)
    assert res.json()["moved_visit_count"] == 2
    assert await _override(db, a, TUE) is None
    await db.refresh(v_pm)
    assert v_pm.primary_staff_id is None
    notes = (await db.scalars(select(Notification.type))).all()
    assert "leave_cancelled" in notes

    res = await client.delete(
        "/api/v1/leave/day",
        headers=_bearer(admin),
        params={"staff_id": str(a.id), "date": TUE.isoformat()},
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_put_day_with_pending_request_is_409(client, db) -> None:
    admin = await _user(db, "admin")
    a = await _staff(db, "職員A", code="001")
    u = await _user(db, "staff", a.id)
    await _request(db, u, a, TUE, "off")
    res = await client.post(
        "/api/v1/leave/day",
        headers=_bearer(admin),
        json={"staff_id": str(a.id), "date": TUE.isoformat(), "leave_type": "pm_off"},
    )
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_delete_day_with_pending_request_is_409(client, db) -> None:
    """休みの日に「取り消してほしい」の申請中 → 表から取り消すのも 409 (申請を先に決める)."""
    admin = await _user(db, "admin")
    a = await _staff(db, "職員A", code="001")
    u = await _user(db, "staff", a.id)
    await _leave(db, a, TUE, "off")
    await _request(db, u, a, TUE, "cancel", kind="change", note="出勤できます")
    res = await client.delete(
        "/api/v1/leave/day",
        headers=_bearer(admin),
        params={"staff_id": str(a.id), "date": TUE.isoformat()},
    )
    assert res.status_code == 409
    assert "申請中" in res.json()["detail"]
    assert (await _override(db, a, TUE)).override_type == "off"


@pytest.mark.asyncio
async def test_second_pending_on_one_cell_is_refused(client, db) -> None:
    """同じ職員・同じ日の申請中は 1 件だけ (0099 の部分 UNIQUE・作成時の 409)."""
    admin = await _user(db, "admin")
    a = await _staff(db, "職員A", code="001")
    u = await _user(db, "staff", a.id)
    body = {
        "request_type": "staff_off",
        "payload": {"staff_id": str(a.id), "date": TUE.isoformat(), "override_type": "am_off"},
        "target_staff_id": str(a.id),
        "target_date": TUE.isoformat(),
    }
    res = await client.post("/api/v1/pending-requests", headers=_bearer(u), json=body)
    assert res.status_code == 201, res.text
    body["payload"]["override_type"] = "off"
    res = await client.post("/api/v1/pending-requests", headers=_bearer(u), json=body)
    assert res.status_code in (409, 422), res.text
    month = (await client.get("/api/v1/leave/month/2030-11", headers=_bearer(admin))).json()
    assert _cell(month, a, TUE)["pending"]["override_type"] == "am_off"
    row = _row(month, a)
    assert row["pending_count"] == 1 and row["pending_gain"] == 0.5
    assert _day(month, TUE)["pending_count"] == 1
    assert len(month["pending"]) == 1


@pytest.mark.asyncio
async def test_bulk_day_default_targets_and_one_notice(client, db) -> None:
    admin = await _user(db, "admin")
    a = await _staff(db, "職員A", code="001")
    b = await _staff(db, "職員B", code="002")
    t = await _staff(db, "新人", code="003", trainee=True)
    await _staff(db, "非常勤", code="004", employment="part_time")
    await _staff(db, "火曜休み", code="005", weekdays=(0, 2, 3, 4))
    already = await _staff(db, "休み済み", code="006")
    asking = await _staff(db, "申請中", code="007")
    await _staff(db, "休職", code="008", status="on_leave")
    ua = await _user(db, "staff", asking.id)
    await _leave(db, already, TUE, "pm_off")
    await _request(db, ua, asking, TUE, "off")
    await _visit(db, a, TUE, "09:00", "10:00")
    await _visit(db, b, TUE, "14:00", "15:00")

    res = await client.post(
        "/api/v1/leave/day/bulk",
        headers=_bearer(admin),
        json={"date": TUE.isoformat(), "override_type": "off", "note": "創立記念日"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert sorted(x["staff_id"] for x in body["applied"]) == sorted(str(s.id) for s in (a, b, t))
    assert body["moved_visit_count"] == 2
    assert (await _override(db, already, TUE)).override_type == "pm_off"
    assert await _override(db, asking, TUE) is None
    row = await _override(db, a, TUE)
    assert row.override_type == "off" and row.source == "admin" and row.reason == "創立記念日"
    notes = (
        await db.scalars(
            select(Notification).where(
                Notification.user_id == admin.id, Notification.type == "leave_unassigned"
            )
        )
    ).all()
    assert len(notes) == 1

    # staff_ids を渡して申請中の人が入っていれば 409 (何も入らない)
    other = date(2030, 11, 19)
    await _request(db, ua, asking, other, "off")
    res = await client.post(
        "/api/v1/leave/day/bulk",
        headers=_bearer(admin),
        json={"date": other.isoformat(), "staff_ids": [str(a.id), str(asking.id)]},
    )
    assert res.status_code == 409
    assert await _override(db, a, other) is None


# ---------------------------------------------------------------------------
# 承認・まとめて承認・却下
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_with_changed_type(client, db) -> None:
    admin = await _user(db, "admin")
    a = await _staff(db, "職員A", code="001")
    u = await _user(db, "staff", a.id)
    req = await _request(db, u, a, TUE, "pm_off", note="通院")
    await _visit(db, a, TUE, "09:00", "10:00")

    res = await client.post(
        f"/api/v1/leave/requests/{req.id}/approve",
        headers=_bearer(admin),
        json={"override_type": "off"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["override_type"] == "off"
    assert body["result"]["moved_visit_count"] == 1
    row = await _override(db, a, TUE)
    assert row.override_type == "off" and row.source == "request"
    assert row.pending_request_id == req.id and row.reason == "通院"
    await db.refresh(req)
    assert req.status == "approved" and req.applied_at is not None
    assert req.edited_payload["override_type"] == "off"
    types = (await db.scalars(select(Notification.type).where(Notification.user_id == u.id))).all()
    assert types == ["leave_approved"]
    assert (
        await db.scalar(
            select(Notification.id).where(
                Notification.user_id == admin.id, Notification.type == "leave_unassigned"
            )
        )
    ) is not None

    # 二重の承認は 409
    res = await client.post(
        f"/api/v1/leave/requests/{req.id}/approve", headers=_bearer(admin), json={}
    )
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_approve_cancel_request_removes_leave(client, db) -> None:
    admin = await _user(db, "admin")
    a = await _staff(db, "職員A", code="001")
    u = await _user(db, "staff", a.id)
    await _leave(db, a, TUE, "off")
    req = await _request(db, u, a, TUE, "cancel", kind="change", note="出勤できます")
    res = await client.post(
        f"/api/v1/leave/requests/{req.id}/approve",
        headers=_bearer(admin),
        json={"override_type": "pm_off"},
    )
    assert res.status_code == 422
    res = await client.post(
        f"/api/v1/leave/requests/{req.id}/approve", headers=_bearer(admin), json={}
    )
    assert res.status_code == 200, res.text
    assert res.json()["override_type"] == "cancel" and res.json()["result"] is None
    assert await _override(db, a, TUE) is None


@pytest.mark.asyncio
async def test_approve_batch_one_transaction_one_admin_notice(client, db) -> None:
    admin = await _user(db, "admin")
    a = await _staff(db, "職員A", code="001")
    b = await _staff(db, "職員B", code="002")
    ua = await _user(db, "staff", a.id)
    ub = await _user(db, "staff", b.id)
    ra = await _request(db, ua, a, TUE, "off")
    rb = await _request(db, ub, b, WED, "am_off")
    await _visit(db, a, TUE, "09:00", "10:00")
    await _visit(db, b, WED, "09:00", "10:00")

    res = await client.post(
        "/api/v1/leave/requests/approve-batch",
        headers=_bearer(admin),
        json={"request_ids": [str(ra.id), str(rb.id)]},
    )
    assert res.status_code == 200, res.text
    assert res.json()["moved_visit_count"] == 2
    admin_notes = (
        await db.scalars(
            select(Notification).where(
                Notification.user_id == admin.id, Notification.type == "leave_unassigned"
            )
        )
    ).all()
    assert len(admin_notes) == 1 and "2件" in admin_notes[0].title
    approved = (
        await db.scalars(select(Notification.user_id).where(Notification.type == "leave_approved"))
    ).all()
    assert sorted(map(str, approved)) == sorted([str(ua.id), str(ub.id)])


@pytest.mark.asyncio
async def test_approve_batch_rolls_back_all_on_failure(client, db) -> None:
    admin = await _user(db, "admin")
    a = await _staff(db, "職員A", code="001")
    b = await _staff(db, "職員B", code="002")
    ua = await _user(db, "staff", a.id)
    ub = await _user(db, "staff", b.id)
    ra = await _request(db, ua, a, TUE, "off")
    rb = await _request(db, ub, b, WED, "off")
    rb.status = "rejected"
    await db.commit()
    res = await client.post(
        "/api/v1/leave/requests/approve-batch",
        headers=_bearer(admin),
        json={"request_ids": [str(ra.id), str(rb.id)]},
    )
    assert res.status_code == 409
    assert res.json()["detail"]["request_id"] == str(rb.id)
    await db.refresh(ra)
    assert ra.status == "pending"
    assert await _override(db, a, TUE) is None


@pytest.mark.asyncio
async def test_reject_requires_reason(client, db) -> None:
    admin = await _user(db, "admin")
    a = await _staff(db, "職員A", code="001")
    u = await _user(db, "staff", a.id)
    req = await _request(db, u, a, TUE, "off")
    res = await client.post(
        f"/api/v1/leave/requests/{req.id}/reject", headers=_bearer(admin), json={"reason": ""}
    )
    assert res.status_code == 422
    res = await client.post(
        f"/api/v1/leave/requests/{req.id}/reject", headers=_bearer(admin), json={"reason": "  "}
    )
    assert res.status_code == 422
    res = await client.post(
        f"/api/v1/leave/requests/{req.id}/reject",
        headers=_bearer(admin),
        json={"reason": "人手が足りません"},
    )
    assert res.status_code == 200, res.text
    await db.refresh(req)
    assert req.status == "rejected" and req.rejection_reason == "人手が足りません"
    assert (
        await db.scalar(select(Notification.type).where(Notification.user_id == u.id))
    ) == "leave_rejected"
    assert await _override(db, a, TUE) is None


# ---------------------------------------------------------------------------
# 事業所の休み・勤務すべき時間数・確定
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_closed_days_and_required_hours(client, db) -> None:
    admin = await _user(db, "admin")
    h = _bearer(admin)
    base = (await client.get("/api/v1/leave/month/2030-11", headers=h)).json()
    res = await client.put(
        "/api/v1/leave/closed-days", headers=h, json={"date": "2030-11-26", "label": "研修"}
    )
    assert res.status_code == 200, res.text
    res = await client.put(
        "/api/v1/leave/closed-days", headers=h, json={"date": "2030-11-26", "label": "創立記念日"}
    )
    assert res.json()["label"] == "創立記念日"
    listed = (await client.get("/api/v1/leave/closed-days?month=2030-11", headers=h)).json()
    assert [x["label"] for x in listed] == ["創立記念日"]
    after = (await client.get("/api/v1/leave/month/2030-11", headers=h)).json()
    assert after["required_holidays"] == base["required_holidays"] + 1
    assert (
        await client.delete("/api/v1/leave/closed-days/2030-11-26", headers=h)
    ).status_code == 204
    assert (
        await client.delete("/api/v1/leave/closed-days/2030-11-26", headers=h)
    ).status_code == 404

    res = await client.put("/api/v1/leave/months/2030-10", headers=h, json={"required_hours": 168})
    assert res.status_code == 200 and res.json()["required_hours"] == 168
    res = await client.put("/api/v1/leave/months/2030-11", headers=h, json={"required_hours": 160})
    assert res.status_code == 200
    body = (await client.get("/api/v1/leave/month/2030-11", headers=h)).json()
    assert body["required_hours"] == 160 and body["previous_required_hours"] == 168


@pytest.mark.asyncio
async def test_confirm_records_and_notifies(client, db) -> None:
    admin = await _user(db, "admin")
    a = await _staff(db, "職員A", code="001")
    b = await _staff(db, "職員B", code="002")  # アカウントなし = 通知なし
    ua = await _user(db, "staff", a.id)
    await _request(db, ua, a, TUE, "off")
    for d in month_days(MONTH):
        if d.weekday() < 5 and d.day <= 12:
            await _leave(db, b, d, "off")

    res = await client.post("/api/v1/leave/month/2030-11/confirm", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["confirmed_staff_count"] == 2 and body["notified_count"] == 1
    assert body["pending_remaining"] == 1
    assert [s["name"] for s in body["short_staff"]] == ["職員A"]
    rows = (await db.scalars(select(StaffShiftConfirmation))).all()
    assert {r.staff_id for r in rows} == {a.id, b.id}
    assert all(r.month == MONTH for r in rows)
    assert (
        await db.scalar(select(Notification.type).where(Notification.user_id == ua.id))
    ) == "shift_confirmed"

    month = (await client.get("/api/v1/leave/month/2030-11", headers=_bearer(admin))).json()
    assert month["confirmation"]["confirmed"] is True
    assert month["confirmation"]["changed_since_confirm"] is False

    # 再確定 = 行は増えない
    res = await client.post("/api/v1/leave/month/2030-11/confirm", headers=_bearer(admin))
    assert res.status_code == 200
    assert len((await db.scalars(select(StaffShiftConfirmation))).all()) == 2


# ---------------------------------------------------------------------------
# 権限・summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_staff_is_forbidden_except_summary(client, db) -> None:
    a = await _staff(db, "職員A", code="001")
    b = await _staff(db, "職員B", code="002")
    ua = await _user(db, "staff", a.id)
    await _leave(db, b, TUE, "off", reason="家族の通院")
    h = _bearer(ua)
    assert (await client.get("/api/v1/leave/month/2030-11", headers=h)).status_code == 403
    for method, url, kw in (
        (
            "post",
            "/api/v1/leave/day",
            {"json": {"staff_id": str(a.id), "date": "2030-11-12", "leave_type": "off"}},
        ),
        ("delete", "/api/v1/leave/day", {"params": {"staff_id": str(b.id), "date": "2030-11-12"}}),
        ("post", "/api/v1/leave/requests/approve-batch", {"json": {"request_ids": [str(uuid4())]}}),
        ("post", "/api/v1/leave/month/2030-11/confirm", {}),
        ("put", "/api/v1/leave/closed-days", {"json": {"date": "2030-11-26", "label": "x"}}),
        ("put", "/api/v1/leave/months/2030-11", {"json": {"required_hours": 1}}),
        ("get", "/api/v1/leave/closed-days", {"params": {"month": "2030-11"}}),
    ):
        res = await getattr(client, method)(url, headers=h, **kw)
        assert res.status_code == 403, (method, url, res.text)

    db.add(StaffShiftConfirmation(staff_id=a.id, month=MONTH, confirmed_at=datetime.now(UTC)))
    await db.commit()
    res = await client.get("/api/v1/leave/month/2030-11/summary", headers=h)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["confirmed"] is True
    assert body["holidays"]
    assert body["staff_count"] is None and body["pending_count"] is None
    assert "家族の通院" not in res.text
    assert body["request_deadline"].startswith("2030-10-2")

    admin = await _user(db, "admin")
    res = await client.get("/api/v1/leave/month/2030-11/summary", headers=_bearer(admin))
    body = res.json()
    assert body["confirmed"] is False
    assert body["confirmed_staff_count"] == 1 and body["staff_count"] == 2
    assert "家族の通院" not in res.text


# ---------------------------------------------------------------------------
# レビュー対応・画面からの足りないもの
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_with_changed_type_keeps_edited_payload(client, db) -> None:
    admin = await _user(db, "admin")
    a = await _staff(db, "職員A", code="001")
    u = await _user(db, "staff", a.id)
    req = await _request(db, u, a, TUE, "pm_off", note="元のメモ")
    req.edited_payload = {**req.payload, "note": "管理者が直したメモ"}
    await db.commit()
    res = await client.post(
        f"/api/v1/leave/requests/{req.id}/approve",
        headers=_bearer(admin),
        json={"override_type": "am_off"},
    )
    assert res.status_code == 200, res.text
    await db.refresh(req)
    assert req.edited_payload["note"] == "管理者が直したメモ"
    assert req.edited_payload["override_type"] == "am_off"
    assert (await _override(db, a, TUE)).reason == "管理者が直したメモ"


@pytest.mark.asyncio
async def test_confirm_only_given_staff(client, db) -> None:
    admin = await _user(db, "admin")
    a = await _staff(db, "職員A", code="001")
    b = await _staff(db, "職員B", code="002")
    ub = await _user(db, "staff", b.id)
    await _request(db, ub, b, TUE, "off")
    res = await client.post(
        "/api/v1/leave/month/2030-11/confirm",
        headers=_bearer(admin),
        json={"staff_ids": [str(a.id)]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["confirmed_staff_count"] == 1 and body["pending_remaining"] == 0
    assert [s["staff_id"] for s in body["short_staff"]] == [str(a.id)]
    rows = (await db.scalars(select(StaffShiftConfirmation.staff_id))).all()
    assert rows == [a.id]
    res = await client.post(
        "/api/v1/leave/month/2030-11/confirm",
        headers=_bearer(admin),
        json={"staff_ids": [str(uuid4())]},
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_summary_admin_counts(client, db) -> None:
    admin = await _user(db, "admin")
    a = await _staff(db, "職員A", code="001")
    b = await _staff(db, "職員B", code="002")
    p = await _staff(db, "非常勤", code="003", employment="part_time")
    ub = await _user(db, "staff", b.id)
    await _request(db, ub, b, TUE, "off")
    await _leave(db, a, TUE, "off")
    # 送ったあとにらく助で取り消した (#4)・種類を変えた (#14) = カイポケで要対応 2 件
    db.add(StaffLeaveKaipoke(staff_id=b.id, leave_date=WED, sent_type="off"))
    db.add(StaffLeaveKaipoke(staff_id=a.id, leave_date=TUE, sent_type="am_off"))
    db.add(StaffLeaveKaipoke(staff_id=p.id, leave_date=THU, sent_type=None, baseline_pending=True))
    # 表に載らない人 (消した職員) の記録は数えない
    gone = await _staff(db, "消した人", code="009")
    gone.deleted_at = datetime.now(UTC)
    db.add(StaffLeaveKaipoke(staff_id=gone.id, leave_date=WED, sent_type="off"))
    await db.commit()
    res = await client.get("/api/v1/leave/month/2030-11/summary", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["pending_count"] == 1
    assert body["kaipoke_action_count"] == 2
    # 必要休日数 = 土日 + 平日の祝日 1 日 (11/4 振替休日)。職員Aは火曜の公休で足り、職員Bは足りない。
    assert required_holidays(MONTH) == _weekend_days(MONTH) + 1
    assert body["short_staff_count"] == 1
    res = await client.get("/api/v1/leave/month/2030-11/summary", headers=_bearer(ub))
    body = res.json()
    assert body["pending_count"] is None
    assert body["short_staff_count"] is None and body["kaipoke_action_count"] is None


@pytest.mark.asyncio
async def test_kinmu_sheets_list(client, db) -> None:
    from app.models.leave import KinmuSheet

    admin = await _user(db, "admin")
    db.add_all(
        [
            KinmuSheet(month=MONTH, kind="plan", version=1, url="https://example.com/p1"),
            KinmuSheet(month=MONTH, kind="plan", version=2, url="https://example.com/p2"),
            KinmuSheet(month=MONTH, kind="final", version=1, reason=None),
            KinmuSheet(month=date(2030, 10, 1), kind="plan", version=1),
        ]
    )
    await db.commit()
    res = await client.get("/api/v1/leave/month/2030-11/kinmu-sheets", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert [(x["kind"], x["version"]) for x in res.json()] == [
        ("plan", 2),
        ("plan", 1),
        ("final", 1),
    ]
    assert res.json()[0]["url"] == "https://example.com/p2"
    staff = await _staff(db, "職員A", code="001")
    u = await _user(db, "staff", staff.id)
    res = await client.get("/api/v1/leave/month/2030-11/kinmu-sheets", headers=_bearer(u))
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_cancel_counts_only_moves_after_last_cancel(client, db) -> None:
    """公休で移す → 取り消す → 担当を戻す → 午前休 → 取り消す: 最後は 0 件."""
    admin = await _user(db, "admin")
    h = _bearer(admin)
    a = await _staff(db, "職員A", code="001")
    v = await _visit(db, a, TUE, "14:00", "15:00")
    params = {"staff_id": str(a.id), "date": TUE.isoformat()}

    res = await client.post("/api/v1/leave/day", headers=h, json={**params, "leave_type": "off"})
    assert res.json()["moved_visit_count"] == 1
    res = await client.delete("/api/v1/leave/day", headers=h, params=params)
    assert res.json()["moved_visit_count"] == 1
    # 職員スケジュールで担当を戻した
    await db.refresh(v)
    v.primary_staff_id = a.id
    v.manual_staff_override = False
    await db.commit()
    res = await client.post("/api/v1/leave/day", headers=h, json={**params, "leave_type": "am_off"})
    assert res.json()["moved_visit_count"] == 0  # 14:00 は午前休の時間帯の外
    res = await client.delete("/api/v1/leave/day", headers=h, params=params)
    assert res.status_code == 200
    assert res.json()["moved_visit_count"] == 0


@pytest.mark.asyncio
async def test_bulk_with_staff_ids_skips_existing_leave(client, db) -> None:
    admin = await _user(db, "admin")
    h = _bearer(admin)
    a = await _staff(db, "職員A", code="001")
    b = await _staff(db, "職員B", code="002")
    await _leave(db, b, TUE, "pm_off")
    res = await client.post(
        "/api/v1/leave/day/bulk",
        headers=h,
        json={"date": TUE.isoformat(), "staff_ids": [str(a.id), str(b.id)]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert [x["staff_id"] for x in body["applied"]] == [str(a.id)]
    assert body["skipped"] == [
        {"staff_id": str(b.id), "reason": "already_on_leave", "leave_type": "pm_off"}
    ]
    assert (await _override(db, b, TUE)).override_type == "pm_off"

    res = await client.post(
        "/api/v1/leave/day/bulk",
        headers=h,
        json={"date": WED.isoformat(), "staff_ids": [str(a.id), str(uuid4())]},
    )
    assert res.status_code == 422
    assert await _override(db, a, WED) is None
