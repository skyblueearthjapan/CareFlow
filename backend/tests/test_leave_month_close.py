"""休みの段 #13: 月の確定から送信と勤務表の予定版・「実績を記録する」.

正典: docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md §2-4・§7-2・§7-4-2・§7-4-4。
Google ドライブ (``create_spreadsheet``) と送信の背景の処理は差し替える。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import User
from app.models.kaipoke_job import KaipokeJob
from app.models.leave import KinmuSheet, LeaveCompareRun, LeaveMonth
from app.models.staff import Staff, StaffEvent, StaffShift, StaffShiftConfirmation
from app.services import leave_compare as lc
from app.services.google_drive import GoogleDriveReconnectRequiredError
from app.services.kaipoke import leave_outbound as lo
from app.services.kaipoke import sources
from app.services.kinmu_sheet import publish
from app.services.leave_calendar import iso_key
from app.services.op_log_service import set_staff_override

MONTH = date(2030, 11, 1)
WED = date(2030, 11, 6)
URL = "/api/v1/leave/month/2030-11"


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _admin(db) -> User:
    u = User(
        email=f"mc-{uuid4().hex[:8]}@example.com", password_hash=hash_password("pw"), role="admin"
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _staff(db, name: str = "送信　花子", internal: str | None = "4321") -> Staff:
    s = Staff(name=name, role="staff")
    db.add(s)
    await db.flush()
    for wd in range(5):
        db.add(
            StaffShift(
                staff_id=s.id, weekday=wd, is_on=True, start_time=time(9), end_time=time(17, 30)
            )
        )
    if internal is not None:
        db.add(
            StaffEvent(
                staff_id=s.id,
                event_type="event",
                starts_at=datetime(2030, 10, 1, 9, 0),
                ends_at=datetime(2030, 10, 1, 9, 15),
                title="朝会",
                source="kaipoke",
                external_id=f"999:{internal}:2030-10-01",
            )
        )
    await db.commit()
    return s


async def _leave(db, staff: Staff, d: date, t: str = "off") -> None:
    y, w, wd = iso_key(d)
    await set_staff_override(
        db, staff_id=staff.id, iso_year=y, iso_week=w, weekday=wd, override_type=t
    )
    await db.commit()


@pytest.fixture
def drive(monkeypatch):
    """create_spreadsheet の差し替え (呼ばれた題名を残す)."""
    calls: list[str] = []

    async def _create(db, title, folder_id, sheets):  # noqa: ANN001
        calls.append(title)
        return (
            f"file-{len(calls)}",
            f"https://docs.google.com/spreadsheets/d/file-{len(calls)}/edit",
        )

    monkeypatch.setattr(publish, "create_spreadsheet", _create)
    return calls


@pytest.fixture
def sent_jobs(monkeypatch):
    """背景の送信を走らせず、渡された job_id を残す."""
    calls: list[UUID] = []

    async def _run(*, job_id, client):  # noqa: ANN001
        calls.append(job_id)

    monkeypatch.setattr(lo, "run_leave_outbound_job", _run)
    return calls


# ---------------------------------------------------------------------------
# 確定 → 送信のジョブ・予定版
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_without_compare_and_drive_keeps_confirmation(client, db, sent_jobs) -> None:
    admin = await _admin(db)
    s = await _staff(db)
    await _leave(db, s, WED)
    res = await client.post(f"{URL}/confirm", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["confirmed_staff_count"] == 1
    assert body["outbound"] == {
        "started": False,
        "job_id": None,
        "count": 0,
        "reason": lo.MSG_NOT_COMPARED,
    }
    sheet = body["kinmu_sheet"]
    assert sheet["created"] is False and sheet["kind"] == "plan" and sheet["reason"]
    assert sheet["sheet"]["url"] is None and sheet["sheet"]["version"] == 1
    assert sent_jobs == []
    assert len((await db.scalars(select(StaffShiftConfirmation))).all()) == 1
    row = await db.scalar(select(KinmuSheet))
    assert row.plan_snapshot is not None and str(s.id) in str(row.plan_snapshot)


@pytest.mark.asyncio
async def test_confirm_starts_outbound_and_plan_sheet(client, db, sent_jobs, drive) -> None:
    admin = await _admin(db)
    h = _bearer(admin)
    s = await _staff(db)
    await _leave(db, s, WED)
    # 休みを入れると月の行ができる (last_leave_changed_at)。その月を照合済みにする。
    lm = await db.get(LeaveMonth, MONTH)
    lm.last_compared_at = datetime.now(UTC) + timedelta(hours=1)
    await db.commit()
    res = await client.put("/api/v1/leave/months/2030-11", headers=h, json={"required_hours": 168})
    assert res.status_code == 200
    db.expire_all()

    res = await client.post(f"{URL}/confirm", headers=h)
    assert res.status_code == 200, res.text
    body = res.json()
    out = body["outbound"]
    assert out["started"] is True and out["count"] == 1 and out["reason"] is None
    assert sent_jobs == [UUID(out["job_id"])]
    job = await db.get(KaipokeJob, UUID(out["job_id"]))
    assert job.params["op"] == sources.OP_LEAVE_OUTBOUND
    sheet = body["kinmu_sheet"]
    assert sheet["created"] is True and sheet["sheet"]["url"].startswith("https://docs.google")
    assert drive == ["勤務形態一覧表_2030年11月_予定"]

    # 再確定: 送信のジョブがまだ動いている → 409 でも確定は成功・予定版は第 2 版
    res = await client.post(f"{URL}/confirm", headers=h)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["outbound"]["started"] is False
    assert "動いています" in body["outbound"]["reason"]
    assert body["kinmu_sheet"]["sheet"]["version"] == 2
    assert drive[-1] == "勤務形態一覧表_2030年11月_予定_第2版"
    assert len((await db.scalars(select(KinmuSheet))).all()) == 2
    assert len(sent_jobs) == 1


@pytest.mark.asyncio
async def test_confirm_drive_needs_reconnect(client, db, monkeypatch, sent_jobs) -> None:
    admin = await _admin(db)
    await _staff(db)
    db.add(LeaveMonth(month=MONTH, required_hours=168))
    await db.commit()

    async def _reconnect(*_a, **_k):  # noqa: ANN002, ANN003
        raise GoogleDriveReconnectRequiredError()

    monkeypatch.setattr(publish, "create_spreadsheet", _reconnect)
    res = await client.post(f"{URL}/confirm", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    sheet = res.json()["kinmu_sheet"]
    assert sheet["created"] is False
    assert sheet["reason"] == "Google ドライブにもう一度接続してください"
    assert len((await db.scalars(select(StaffShiftConfirmation))).all()) == 1


# ---------------------------------------------------------------------------
# 「実績を記録する」の条件 (6 つ)
# ---------------------------------------------------------------------------

STARTED = datetime(2030, 12, 1, 10, 0, tzinfo=UTC)
FRESH = (STARTED + timedelta(minutes=5)).isoformat()


def _run(**kw) -> LeaveCompareRun:
    base = {
        "month": MONTH,
        "diffs": [],
        "diff_count": 0,
        "planned_send_count": 0,
        "unconfirmed_days": 0,
        "fetched_at": {"tasks": {"2030-10-28": FRESH, "2030-11-04": FRESH}, "actual_csv": FRESH},
    }
    base.update(kw)
    return LeaveCompareRun(**base)


def _month(**kw) -> LeaveMonth:
    base = {
        "month": MONTH,
        "last_compared_at": STARTED + timedelta(minutes=10),
        "last_leave_changed_at": STARTED,
    }
    base.update(kw)
    return LeaveMonth(**base)


def _codes(**kw) -> list[str]:
    leave_month = kw.pop("leave_month", _month())
    run = kw.pop("run", _run())
    started = kw.pop("started", STARTED)
    return [
        b.code
        for b in lc.record_actuals_blockers(
            leave_month=leave_month, run=run, job_started_at=started
        )
    ]


def test_record_actuals_conditions() -> None:
    assert _codes() == []
    # 1. 照合がない
    assert _codes(leave_month=None) == [lc.RECORD_NOT_COMPARED]
    assert _codes(run=None) == [lc.RECORD_NOT_COMPARED]
    # 2. まだ合わせていない差分 (合わせた差分は数えない)
    assert _codes(run=_run(diffs=[{"kind": "x"}, {"kind": "y", "resolved": {"by": "a"}}])) == [
        lc.RECORD_DIFFS_REMAINING
    ]
    assert _codes(run=_run(diffs=[{"kind": "y", "resolved": {"by": "a"}}])) == []
    # 3. 未確認の日
    assert _codes(run=_run(unconfirmed_days=2)) == [lc.RECORD_UNCONFIRMED_DAYS]
    # 4. 送る予定
    assert _codes(run=_run(planned_send_count=1)) == [lc.RECORD_PLANNED_SENDS]
    # 5. 比べた後に休みを変えた
    assert _codes(leave_month=_month(last_leave_changed_at=STARTED + timedelta(hours=1))) == [
        lc.RECORD_CHANGED_SINCE_COMPARE
    ]
    # 6. 材料が古い (ジョブの開始より前・欠け・ジョブが無い)
    old = (STARTED - timedelta(minutes=1)).isoformat()
    assert _codes(run=_run(fetched_at={"tasks": {"2030-10-28": old}, "actual_csv": FRESH})) == [
        lc.RECORD_STALE_MATERIAL
    ]
    assert _codes(run=_run(fetched_at={"tasks": {"2030-10-28": FRESH}, "actual_csv": None})) == [
        lc.RECORD_STALE_MATERIAL
    ]
    assert _codes(started=None) == [lc.RECORD_STALE_MATERIAL]
    # 欠けているものは全部返す
    assert set(_codes(run=_run(unconfirmed_days=1, planned_send_count=1))) == {
        lc.RECORD_UNCONFIRMED_DAYS,
        lc.RECORD_PLANNED_SENDS,
    }


async def _compared_month(db, *, unconfirmed: int = 0) -> None:
    job = KaipokeJob(
        job_type="sync",
        status="completed",
        week_start=date(2030, 10, 28),
        params={"op": sources.OP_LEAVE_COMPARE, "month": "2030-11"},
        started_at=STARTED,
        completed_at=STARTED + timedelta(minutes=8),
        result_summary={},
    )
    db.add(job)
    await db.flush()
    run = _run(kaipoke_job_id=job.id, unconfirmed_days=unconfirmed)
    db.add(run)
    await db.flush()
    db.add(
        LeaveMonth(
            month=MONTH,
            required_hours=168,
            last_compared_at=STARTED + timedelta(minutes=10),
            last_compare_run_id=run.id,
        )
    )
    await db.commit()


@pytest.mark.asyncio
async def test_record_actuals_409_with_reasons(client, db) -> None:
    admin = await _admin(db)
    res = await client.post(f"{URL}/record-actuals", headers=_bearer(admin))
    assert res.status_code == 409
    assert [b["code"] for b in res.json()["detail"]["blockers"]] == ["not_compared"]

    await _compared_month(db, unconfirmed=3)
    res = await client.post(f"{URL}/record-actuals", headers=_bearer(admin))
    assert res.status_code == 409
    assert [b["code"] for b in res.json()["detail"]["blockers"]] == ["unconfirmed_days"]
    # 月の表も同じ判定を出す
    view = (await client.get(URL, headers=_bearer(admin))).json()
    assert view["record_actuals"]["ready"] is False
    assert [b["code"] for b in view["record_actuals"]["blockers"]] == ["unconfirmed_days"]
    assert await db.scalar(select(LeaveMonth.actual_recorded_at)) is None


@pytest.mark.asyncio
async def test_record_actuals_creates_final_sheet(client, db, drive) -> None:
    admin = await _admin(db)
    await _staff(db)
    await _compared_month(db)
    view = (await client.get(URL, headers=_bearer(admin))).json()
    assert view["record_actuals"] == {"ready": True, "blockers": [], "actual_recorded_at": None}
    res = await client.post(f"{URL}/record-actuals", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["kinmu_sheet"]["created"] is True and body["kinmu_sheet"]["kind"] == "final"
    assert drive == ["勤務形態一覧表_2030年11月_確定"]
    lm = await db.scalar(select(LeaveMonth).execution_options(populate_existing=True))
    assert lm.actual_recorded_at is not None and lm.actual_recorded_by == admin.id


@pytest.mark.asyncio
async def test_record_actuals_without_drive_still_records(client, db) -> None:
    admin = await _admin(db)
    await _staff(db)
    await _compared_month(db)
    res = await client.post(f"{URL}/record-actuals", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    sheet = res.json()["kinmu_sheet"]
    assert sheet["created"] is False and sheet["reason"]
    assert sheet["sheet"]["kind"] == "final" and sheet["sheet"]["url"] is None
    assert await db.scalar(select(LeaveMonth.actual_recorded_at)) is not None
