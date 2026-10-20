"""休みの照合と送信の API (/api/v1/leave/compare/*・/api/v1/leave/outbound/*).

権限 (管理者のみ)・登録の順 (latest・preview が {job_id} より先)・409・ジョブの状態・
合わせる・送信の開始。背景の処理 (RPA) はモックに差し替える。
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import User
from app.models.kaipoke_job import KaipokeJob
from app.models.leave import LeaveCompareRun, LeaveMonth, StaffLeaveKaipoke
from app.models.staff import Staff, StaffEvent, StaffWeeklyOverride
from app.services import leave_compare as lc
from app.services.kaipoke import sources
from app.services.leave_calendar import iso_key

MONTH = "2026-11"
WED = date(2026, 11, 4)


async def _user(db, role: str) -> User:
    user = User(
        email=f"{role}-{uuid.uuid4().hex[:6]}@example.com",
        password_hash=hash_password("x"),
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


ENDPOINTS = [
    ("post", "/api/v1/leave/compare/start", {"month": MONTH}),
    ("get", f"/api/v1/leave/compare/latest?month={MONTH}", None),
    ("get", f"/api/v1/leave/compare/{uuid.uuid4()}", None),
    (
        "post",
        "/api/v1/leave/compare/apply",
        {
            "items": [
                {"staff_id": str(uuid.uuid4()), "date": "2026-11-04", "action": "cancel_rakusuke"}
            ]
        },
    ),
    ("post", "/api/v1/leave/outbound/start", {"month": MONTH}),
    ("get", f"/api/v1/leave/outbound/preview?month={MONTH}", None),
    ("get", f"/api/v1/leave/outbound/{uuid.uuid4()}", None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "url", "body"), ENDPOINTS)
async def test_admin_only(client, db, method, url, body) -> None:
    staff_user = await _user(db, "staff")
    kwargs = {"json": body} if body is not None else {}
    res = await getattr(client, method)(url, headers=_bearer(staff_user), **kwargs)
    assert res.status_code == 403, res.text
    res = await getattr(client, method)(url, **kwargs)
    assert res.status_code == 401, res.text


@pytest.mark.asyncio
async def test_latest_and_preview_are_registered_before_job_id(client, db) -> None:
    admin = await _user(db, "admin")
    res = await client.get(f"/api/v1/leave/compare/latest?month={MONTH}", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert res.json()["run"] is None and res.json()["job"] is None
    res = await client.get(f"/api/v1/leave/outbound/preview?month={MONTH}", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert res.json()["blocked_reason"] == "まだカイポケと比べていないので、休みは送りません"
    res = await client.get("/api/v1/leave/compare/latest?month=2026-13", headers=_bearer(admin))
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_compare_start_runs_in_background_and_409(client, db, monkeypatch) -> None:
    admin = await _user(db, "admin")
    calls: list[uuid.UUID] = []

    async def fake_run(*, job_id, plan, client):
        calls.append(job_id)

    monkeypatch.setattr(lc, "run_leave_compare_job", fake_run)
    res = await client.post(
        "/api/v1/leave/compare/start", json={"month": MONTH}, headers=_bearer(admin)
    )
    assert res.status_code == 202, res.text
    job_id = res.json()["job_id"]
    assert calls == [uuid.UUID(job_id)]
    assert res.json()["total"] == len(lc.compare_source_plan(lc.month_first_day(MONTH)))

    # 動いている間は 409。
    res = await client.post(
        "/api/v1/leave/compare/start", json={"month": MONTH}, headers=_bearer(admin)
    )
    assert res.status_code == 409

    view = (await client.get(f"/api/v1/leave/compare/{job_id}", headers=_bearer(admin))).json()
    assert view["state"] == "fetching" and view["job"]["status"] == "running"
    latest = (
        await client.get(f"/api/v1/leave/compare/latest?month={MONTH}", headers=_bearer(admin))
    ).json()
    assert latest["job"]["id"] == job_id

    # 取り直しが終わった・差分を作成中 → 作れなかった → できた。
    await sources.finish_source_job(db, uuid.UUID(job_id), [])
    view = (await client.get(f"/api/v1/leave/compare/{job_id}", headers=_bearer(admin))).json()
    assert view["state"] == "comparing"

    job = await db.get(KaipokeJob, uuid.UUID(job_id), populate_existing=True)
    assert job is not None
    job.result_summary = {**(job.result_summary or {}), "compare_error": "こわれた"}
    await db.commit()
    view = (await client.get(f"/api/v1/leave/compare/{job_id}", headers=_bearer(admin))).json()
    assert (view["state"], view["compare_error"]) == ("compare_failed", "こわれた")

    run = await lc.compare_month(db, uuid.UUID(job_id))
    assert run is not None
    view = (await client.get(f"/api/v1/leave/compare/{job_id}", headers=_bearer(admin))).json()
    assert view["state"] == "done" and view["run"]["id"] == str(run.id)
    latest = (
        await client.get(f"/api/v1/leave/compare/latest?month={MONTH}", headers=_bearer(admin))
    ).json()
    assert latest["run"]["id"] == str(run.id) and latest["last_compared_at"] is not None

    # 別の op のジョブ id は 404。
    res = await client.get(f"/api/v1/leave/outbound/{job_id}", headers=_bearer(admin))
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_apply_via_api(client, db) -> None:
    admin = await _user(db, "admin")
    staff = Staff(name="合わせ　花子", role="staff")
    db.add(staff)
    await db.flush()
    y, w, wd = iso_key(WED)
    db.add_all(
        [
            StaffWeeklyOverride(
                staff_id=staff.id, iso_year=y, iso_week=w, weekday=wd, override_type="off"
            ),
            StaffLeaveKaipoke(staff_id=staff.id, leave_date=WED, baseline_pending=True),
        ]
    )
    await db.commit()
    diff = lc.LeaveDiff(
        kind=lc.DIFF_RAKUSUKE_ONLY_BASELINE,
        message="らく助だけの休み",
        case=6,
        staff_id=staff.id,
        day=WED,
        rakusuke="off",
        baseline_pending=True,
        action=lc.ACTION_CANCEL_RAKUSUKE,
    )
    run = LeaveCompareRun(month=date(2026, 11, 1), diffs=[diff.as_dict()], diff_count=1)
    db.add(run)
    await db.flush()
    db.add(
        LeaveMonth(
            month=date(2026, 11, 1), last_compared_at=datetime.now(UTC), last_compare_run_id=run.id
        )
    )
    await db.commit()

    body = {
        "items": [{"staff_id": str(staff.id), "date": WED.isoformat(), "action": "cancel_rakusuke"}]
    }
    res = await client.post("/api/v1/leave/compare/apply", json=body, headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert res.json()["recompare_needed"] is True
    assert res.json()["applied"][0]["rakusuke_after"] is None
    assert (await db.scalars(select(StaffLeaveKaipoke))).all() == []

    res = await client.post("/api/v1/leave/compare/apply", json=body, headers=_bearer(admin))
    assert res.status_code == 409
    bad = {"items": [{**body["items"][0], "action": "refetch_week"}]}
    res = await client.post("/api/v1/leave/compare/apply", json=bad, headers=_bearer(admin))
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_outbound_start_and_status(client, db, monkeypatch) -> None:
    from app.services.kaipoke import leave_outbound

    admin = await _user(db, "admin")
    res = await client.post(
        "/api/v1/leave/outbound/start", json={"month": MONTH}, headers=_bearer(admin)
    )
    assert res.status_code == 422
    assert res.json()["detail"] == leave_outbound.MSG_NOT_COMPARED

    staff = Staff(name="送信　花子", role="staff")
    db.add(staff)
    await db.flush()
    db.add(
        StaffEvent(
            staff_id=staff.id,
            event_type="event",
            starts_at=datetime(2026, 10, 1, 9),
            ends_at=datetime(2026, 10, 1, 9, 15),
            title="朝会",
            source="kaipoke",
            external_id="1:777:2026-10-01",
        )
    )
    await db.commit()
    y, w, wd = iso_key(WED)
    db.add_all(
        [
            StaffWeeklyOverride(
                staff_id=staff.id, iso_year=y, iso_week=w, weekday=wd, override_type="am_off"
            ),
            LeaveMonth(month=date(2026, 11, 1), last_compared_at=datetime.now(UTC)),
        ]
    )
    await db.commit()

    preview = (
        await client.get(f"/api/v1/leave/outbound/preview?month={MONTH}", headers=_bearer(admin))
    ).json()
    assert preview["sendable_count"] == 1, preview
    [item] = preview["items"]
    assert (item["title"], item["start"], item["end"]) == ("AM休み", "09:30", "13:00")

    started: list[uuid.UUID] = []

    async def fake_run(*, job_id, client):
        started.append(job_id)

    monkeypatch.setattr(leave_outbound, "run_leave_outbound_job", fake_run)
    res = await client.post(
        "/api/v1/leave/outbound/start", json={"month": MONTH}, headers=_bearer(admin)
    )
    assert res.status_code == 202, res.text
    job_id = res.json()["job_id"]
    assert started == [uuid.UUID(job_id)]
    assert (res.json()["count"], res.json()["total"]) == (1, 1)

    res = await client.post(
        "/api/v1/leave/outbound/start", json={"month": MONTH}, headers=_bearer(admin)
    )
    assert res.status_code == 409

    view = (await client.get(f"/api/v1/leave/outbound/{job_id}", headers=_bearer(admin))).json()
    assert view["job"]["status"] == "running" and view["job"]["op"] == "leave-outbound"
    assert view["count"] == 1 and view["items"] == []
    res = await client.get(f"/api/v1/leave/compare/{job_id}", headers=_bearer(admin))
    assert res.status_code == 404
    # 送信が動いている間は照合も 409。
    res = await client.post(
        "/api/v1/leave/compare/start", json={"month": MONTH}, headers=_bearer(admin)
    )
    assert res.status_code == 409
