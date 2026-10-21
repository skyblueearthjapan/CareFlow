"""休みの段の持ち越し: 新しい申請の通知・通知の項目・本人の訪問の判定・却下の理由・redo 枝・
月をまたいだ申請中の件数・照合の compare_error・通知の文言.

正典: docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md §3-3・§5-2・§7-4-3。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select

from app.core.security import create_access_token, hash_password
from app.models import Notification, Patient, PendingRequest, Staff, User, Visit
from app.models.kaipoke_job import KaipokeJob
from app.models.schedule_op_log import ScheduleOpLog
from app.models.staff import StaffShift
from app.services import leave_compare
from app.services.kaipoke import sources
from app.services.leave_calendar import iso_key
from app.services.leave_notify import notify_shift_confirmed
from app.services.op_log_service import set_staff_override
from app.services.staff_leave import (
    find_visits_on_leave,
    load_owned_visits_between,
    visit_owner_ids,
    write_override_day,
)

TUE = date(2030, 11, 12)
DEC = date(2030, 12, 3)


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _user(db, role: str, staff_id: UUID | None = None) -> User:
    u = User(
        email=f"fu-{uuid4().hex[:8]}@example.com",
        password_hash=hash_password("pw"),
        role=role,
        staff_id=staff_id,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _staff(db, name: str = "職員A") -> Staff:
    s = Staff(name=name)
    db.add(s)
    await db.flush()
    for wd in range(5):
        db.add(StaffShift(staff_id=s.id, weekday=wd, is_on=True))
    await db.commit()
    return s


async def _visit(db, staff: Staff | None, d: date, start: str, end: str, **kw) -> Visit:
    p = Patient(code=f"P-{uuid4().hex[:8]}", name="患者", status="active", special_week_active=[])
    db.add(p)
    await db.flush()
    sh, sm = (int(x) for x in start.split(":"))
    eh, em = (int(x) for x in end.split(":"))
    v = Visit(
        patient_id=p.id,
        primary_staff_id=staff.id if staff else None,
        visit_date=d,
        start_time=time(sh, sm),
        end_time=time(eh, em),
        type="regular",
        status="planned",
        source="auto",
        **kw,
    )
    db.add(v)
    await db.commit()
    return v


def _off_request(staff: Staff, d: date, t: str = "pm_off", **extra) -> dict:
    payload = {"staff_id": str(staff.id), "date": d.isoformat(), "override_type": t, **extra}
    return {
        "request_type": "staff_off",
        "payload": payload,
        "target_staff_id": str(staff.id),
        "target_date": d.isoformat(),
    }


# ---------------------------------------------------------------------------
# 1. 新しい申請を管理者へ知らせる・通知の項目
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_request_notifies_admins_with_reference_and_data(client, db) -> None:
    admin = await _user(db, "admin")
    other_admin = await _user(db, "admin")
    s = await _staff(db, "職員X")
    u = await _user(db, "staff", s.id)
    res = await client.post(
        "/api/v1/pending-requests", headers=_bearer(u), json=_off_request(s, TUE, note="学校")
    )
    assert res.status_code == 201, res.text
    req_id = res.json()["id"]
    rows = (
        await db.scalars(select(Notification).where(Notification.type == "leave_requested"))
    ).all()
    assert {r.user_id for r in rows} == {admin.id, other_admin.id}
    n = rows[0]
    assert n.reference_type == "leave_request" and str(n.reference_id) == req_id
    assert n.data == {
        "staff_id": str(s.id),
        "date": TUE.isoformat(),
        "override_type": "pm_off",
        "kind": "normal",
    }
    assert "職員Xさん" in n.title and "11/12" in n.title and "午後休" in n.title
    # 職員本人には届かない
    assert (await db.scalar(select(Notification.id).where(Notification.user_id == u.id))) is None

    # 一覧の API でも項目が読める
    listed = (await client.get("/api/v1/notifications", headers=_bearer(admin))).json()
    item = next(i for i in listed["items"] if i["type"] == "leave_requested")
    assert item["reference_type"] == "leave_request" and item["reference_id"] == req_id
    assert item["data"]["staff_id"] == str(s.id) and item["data"]["date"] == TUE.isoformat()


@pytest.mark.asyncio
async def test_unassigned_notice_has_staff_and_date(client, db) -> None:
    admin = await _user(db, "admin")
    s = await _staff(db)
    await _visit(db, s, TUE, "09:00", "10:00")
    res = await client.post(
        "/api/v1/leave/day",
        headers=_bearer(admin),
        json={"staff_id": str(s.id), "date": TUE.isoformat(), "leave_type": "off"},
    )
    assert res.status_code == 200, res.text
    n = await db.scalar(select(Notification).where(Notification.type == "leave_unassigned"))
    assert n.data["staff_id"] == str(s.id) and n.data["date"] == TUE.isoformat()
    assert n.data["items"] == [
        {"staff_id": str(s.id), "staff_name": "職員A", "date": TUE.isoformat(), "visit_count": 1}
    ]


@pytest.mark.asyncio
async def test_shift_confirmed_text_says_leave(db) -> None:
    s = await _staff(db)
    u = await _user(db, "staff", s.id)
    await notify_shift_confirmed(db, staff_id=s.id, month=date(2030, 11, 1))
    await db.commit()
    n = await db.scalar(select(Notification).where(Notification.user_id == u.id))
    assert n.title == "2030年11月の休みが確定しました"
    assert "出勤カレンダー" not in (n.title + (n.body or ""))
    assert n.data == {"staff_id": str(s.id), "month": "2030-11-01"}


# ---------------------------------------------------------------------------
# 2. 持ち越し
# ---------------------------------------------------------------------------


def test_visit_owner_ids_rules() -> None:
    a, b, c, d = uuid4(), uuid4(), uuid4(), uuid4()
    # primary が正
    assert visit_owner_ids(
        primary_staff_id=a, manual_staff_override=False, course_staff_id=b, secondary_staff_id=None
    ) == {a}
    # primary が空・明示の担当なしでない → コース担当
    assert visit_owner_ids(
        primary_staff_id=None,
        manual_staff_override=False,
        course_staff_id=b,
        secondary_staff_id=None,
    ) == {b}
    # 明示の担当なし → コース担当に読み替えない
    assert (
        visit_owner_ids(
            primary_staff_id=None,
            manual_staff_override=True,
            course_staff_id=b,
            secondary_staff_id=None,
        )
        == set()
    )
    # 2 名体制の 2 人目 (VSA・古い行の secondary)
    assert visit_owner_ids(
        primary_staff_id=a,
        manual_staff_override=False,
        course_staff_id=None,
        secondary_staff_id=c,
        vsa_staff_ids=[d],
    ) == {a, c, d}


@pytest.mark.asyncio
async def test_month_table_counts_legacy_secondary(client, db) -> None:
    admin = await _user(db, "admin")
    a = await _staff(db, "職員A")
    b = await _staff(db, "職員B")
    await _visit(db, a, TUE, "09:00", "10:00", secondary_staff_id=b.id)
    owned = await load_owned_visits_between(db, TUE, TUE)
    assert [v.on_primary for v in owned[(a.id, TUE)]] == [True]
    assert [v.on_primary for v in owned[(b.id, TUE)]] == [False]
    body = (await client.get("/api/v1/leave/month/2030-11", headers=_bearer(admin))).json()
    cell = next(c for c in body["cells"] if c["staff_id"] == str(b.id))
    assert cell["visit_count"] == 1


@pytest.mark.asyncio
async def test_find_visits_on_leave_reads_once(db) -> None:
    """休みのある日がいくつあっても、訪問はまとめて 1 回 (+ VSA 1 回) で読む."""
    from app.db import session as db_session

    s = await _staff(db)
    days = [date(2030, 11, d) for d in (4, 5, 6, 7, 8, 11, 12, 13)]
    for d in days:
        y, w, wd = iso_key(d)
        await set_staff_override(
            db, staff_id=s.id, iso_year=y, iso_week=w, weekday=wd, override_type="off"
        )
        await _visit(db, s, d, "09:00", "10:00")
    await db.commit()
    captured: list[str] = []

    def _listener(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        captured.append(statement)

    engine = db_session.get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", _listener)
    try:
        hits = await find_visits_on_leave(db, days[0], days[-1])
    finally:
        event.remove(engine, "before_cursor_execute", _listener)
    assert len(hits) == len(days)
    visit_reads = [q for q in captured if "FROM visits" in q]
    assert len(visit_reads) <= 2, visit_reads


@pytest.mark.asyncio
async def test_patch_reject_strips_reason(client, db) -> None:
    admin = await _user(db, "admin")
    s = await _staff(db)
    u = await _user(db, "staff", s.id)
    res = await client.post(
        "/api/v1/pending-requests", headers=_bearer(u), json=_off_request(s, TUE)
    )
    req_id = res.json()["id"]
    res = await client.patch(
        f"/api/v1/pending-requests/{req_id}/reject",
        headers=_bearer(admin),
        json={"rejection_reason": "   "},
    )
    assert res.status_code == 422
    res = await client.patch(
        f"/api/v1/pending-requests/{req_id}/reject",
        headers=_bearer(admin),
        json={"rejection_reason": "  人手が足りません  "},
    )
    assert res.status_code == 200, res.text
    assert res.json()["rejection_reason"] == "人手が足りません"


@pytest.mark.asyncio
async def test_write_override_day_time_change_clears_redo(db) -> None:
    admin = await _user(db, "admin")
    s = await _staff(db)
    y, w, _wd = iso_key(TUE)
    db.add(
        ScheduleOpLog(
            user_id=admin.id,
            iso_year=y,
            iso_week=w,
            op_group_id=uuid4(),
            op_kind="set_course_staff",
            label="やり直せる操作",
            forward_payload={},
            inverse_payload={},
            undone=True,
        )
    )
    await db.commit()
    await write_override_day(
        db,
        staff_id=s.id,
        day=TUE,
        override_type="custom_time",
        start_time=time(9, 0),
        end_time=time(15, 0),
        reason=None,
        actor=admin,
        origin="excel",
    )
    await db.commit()
    undone = (await db.scalars(select(ScheduleOpLog).where(ScheduleOpLog.undone.is_(True)))).all()
    assert undone == []


@pytest.mark.asyncio
async def test_summary_pending_total_spans_months(client, db) -> None:
    admin = await _user(db, "admin")
    s = await _staff(db)
    u = await _user(db, "staff", s.id)
    for d in (TUE, DEC):
        res = await client.post(
            "/api/v1/pending-requests", headers=_bearer(u), json=_off_request(s, d)
        )
        assert res.status_code == 201, res.text
    body = (await client.get("/api/v1/leave/month/2030-11/summary", headers=_bearer(admin))).json()
    assert body["pending_count"] == 1 and body["pending_total"] == 2
    mine = (await client.get("/api/v1/leave/month/2030-11/summary", headers=_bearer(u))).json()
    assert mine["pending_total"] is None


async def _completed_compare_job(db) -> KaipokeJob:
    job = KaipokeJob(
        job_type="sync",
        status="completed",
        week_start=date(2030, 11, 4),
        params={"op": sources.OP_LEAVE_COMPARE, "month": "2030-11"},
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        result_summary={},
    )
    db.add(job)
    await db.commit()
    return job


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["inside", "outside"])
async def test_compare_failure_always_records_error(db, monkeypatch, where) -> None:
    job = await _completed_compare_job(db)

    async def _run_source_job(session, client, job_id, plan):  # noqa: ANN001
        return await session.get(KaipokeJob, job_id)

    async def _boom(*_a, **_k):  # noqa: ANN002, ANN003
        raise RuntimeError("壊れた材料")

    monkeypatch.setattr(sources, "run_source_job", _run_source_job)
    if where == "inside":
        monkeypatch.setattr(leave_compare, "build_month_diffs", _boom)
        assert (
            await leave_compare.run_leave_compare_job(job_id=job.id, plan=[], client=None) is None
        )
    else:
        monkeypatch.setattr(leave_compare, "compare_month", _boom)
        with pytest.raises(RuntimeError):
            await leave_compare.run_leave_compare_job(job_id=job.id, plan=[], client=None)
    fresh = await db.get(KaipokeJob, job.id, populate_existing=True)
    # 画面に出す文は日本語の案内だけ (例外の型の名前と中身はログへ・2026-10-21)
    assert fresh.result_summary["compare_error"] == leave_compare.COMPARE_ERROR_MESSAGE
    assert "RuntimeError" not in fresh.result_summary["compare_error"]
    assert "壊れた材料" not in fresh.result_summary["compare_error"]


@pytest.mark.asyncio
async def test_compare_without_result_gets_error(db, monkeypatch) -> None:
    job = await _completed_compare_job(db)
    job.started_at = None  # 差分を作れない (compare_month が None を返す)
    await db.commit()

    async def _run_source_job(session, client, job_id, plan):  # noqa: ANN001
        return await session.get(KaipokeJob, job_id)

    monkeypatch.setattr(sources, "run_source_job", _run_source_job)
    assert await leave_compare.run_leave_compare_job(job_id=job.id, plan=[], client=None) is None
    fresh = await db.get(KaipokeJob, job.id, populate_existing=True)
    assert fresh.result_summary["compare_error"]


@pytest.mark.asyncio
async def test_second_request_same_day_is_409(client, db) -> None:
    s = await _staff(db)
    u = await _user(db, "staff", s.id)
    res = await client.post(
        "/api/v1/pending-requests", headers=_bearer(u), json=_off_request(s, TUE)
    )
    assert res.status_code == 201
    res = await client.post(
        "/api/v1/pending-requests", headers=_bearer(u), json=_off_request(s, TUE, "off")
    )
    assert res.status_code == 409
    # 1 件目の通知だけ (2 件目は書かれない)
    assert len((await db.scalars(select(PendingRequest))).all()) == 1


@pytest.mark.asyncio
async def test_admin_own_request_does_not_notify_self(client, db) -> None:
    me = await _user(db, "admin")
    other = await _user(db, "admin")
    s = await _staff(db, "職員Y")
    res = await client.post(
        "/api/v1/pending-requests", headers=_bearer(me), json=_off_request(s, TUE)
    )
    assert res.status_code == 201, res.text
    rows = (
        await db.scalars(select(Notification).where(Notification.type == "leave_requested"))
    ).all()
    assert [r.user_id for r in rows] == [other.id]
