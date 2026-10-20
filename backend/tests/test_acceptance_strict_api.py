"""受け入れ枠 (数週続けて空いているか) — 材料の読み込み・API・取り直し (設計書 §7-2・§7-3).

ローカル SQLite だけ。RPA は使わない (取り直しの本体は段階 0 の ``run_source_job`` をモック)。
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select

from app.core.security import create_access_token, hash_password
from app.models import Office, User
from app.models.acceptance_calendar import AcceptanceCalendar
from app.models.kaipoke_csv_snapshot import KaipokeCsvSnapshot
from app.models.kaipoke_job import KaipokeJob
from app.models.kaipoke_source_snapshot import KaipokeSourceSnapshot
from app.models.staff import (
    Staff,
    StaffEvent,
    StaffSecondaryOffice,
    StaffShift,
    StaffWeeklyOverride,
)
from app.services.kaipoke import sources
from app.services.scheduling import acceptance_strict_data as data
from app.services.scheduling.acceptance_strict_refresh import (
    RefreshTarget,
    plan_targets,
    source_plan,
)

TODAY = date(2026, 10, 20)  # 火曜・週 43
MONDAY = date(2026, 10, 19)
NOW = datetime(2026, 10, 20, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    data.clear_cache()


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


async def _office(db, code: str = "INAGE") -> Office:
    office = Office(name=f"拠点{code}", code=code)
    db.add(office)
    await db.flush()
    return office


async def _staff(
    db,
    office: Office | None,
    name: str,
    *,
    role: str = "staff",
    trainee: bool = False,
    weekdays=range(6),
    start: time | None = time(9, 30),
    end: time | None = time(18, 0),
) -> Staff:
    s = Staff(
        name=name, role=role, is_trainee=trainee, primary_office_id=office.id if office else None
    )
    db.add(s)
    await db.flush()
    for wd in range(7):
        db.add(
            StaffShift(
                staff_id=s.id, weekday=wd, is_on=wd in weekdays, start_time=start, end_time=end
            )
        )
    await db.flush()
    return s


def _row(
    day: int,
    start: str,
    end: str,
    n1: str,
    n2: str = "",
    n3: str = "",
    patient: str = "利用者001",
    office: str = "稲毛",
) -> dict:
    return {
        "職員名１": n1,
        "職員名２": n2,
        "職員名３": n3,
        "事業所名": office,
        "日付": str(day),
        "利用者": patient,
        "業務種別": "医療保険",
        "開始時間": start,
        "終了時間": end,
    }


async def _snap(
    db, kind: str, period: date, rows: list, *, status: str = "ok", covered=None, fetched_at=NOW
):
    db.add(
        KaipokeSourceSnapshot(
            kind=kind,
            period_start=period,
            rows=rows,
            row_count=len(rows),
            status=status,
            covered_dates=covered,
            fetched_at=fetched_at,
        )
    )
    await db.flush()


async def _core(db, office: Office, **kw) -> dict:
    await db.commit()
    return await data.build_strict_core(db, office.id, today=kw.pop("today", TODAY), **kw)


def _cell(core: dict, staff: Staff, d: date) -> dict:
    return core["cells"][str(staff.id)][d.isoformat()]


def _day(core: dict, d: date) -> dict:
    return next(x for x in core["days"] if x["date"] == d.isoformat())


# --- 材料の読み込み -------------------------------------------------------------


@pytest.mark.asyncio
async def test_other_office_rows_and_companions_block_time(db) -> None:
    office = await _office(db)
    a = await _staff(db, office, "職員A")
    rows = [
        _row(21, "10:00", "10:50", "職員A", office="都賀"),  # 他の拠点の行も塞ぐ
        _row(21, "14:00", "14:50", "他人", n2="職員A"),  # 同行は件数に入らない
    ]
    await _snap(db, "plan_csv", date(2026, 10, 1), rows)
    core = await _core(db, office)
    cell = _cell(core, a, date(2026, 10, 21))
    assert cell["n"] == 1
    assert 10 not in cell["real"]["hours"] and 14 not in cell["real"]["hours"]
    assert core["sources"]["unmatched_staff_names"] == {"他人": 1}


@pytest.mark.asyncio
async def test_manual_event_titled_leave_only_blocks(db) -> None:
    office = await _office(db)
    a = await _staff(db, office, "職員A")
    await _snap(db, "plan_csv", date(2026, 10, 1), [_row(21, "17:00", "17:35", "職員A")])
    db.add(
        StaffEvent(
            staff_id=a.id,
            event_type="event",
            source="manual",
            title="休み",
            starts_at=datetime(2026, 10, 21, 9, 30),
            ends_at=datetime(2026, 10, 21, 11, 0),
        )
    )
    core = await _core(db, office)
    real = _cell(core, a, date(2026, 10, 21))["real"]
    assert real["state"] == "free"
    assert real["why"] == ""
    assert 9 not in real["hours"] and 10 not in real["hours"]


@pytest.mark.asyncio
async def test_tasks_snapshot_replaces_imported_events_and_uncovered_day_falls_back(db) -> None:
    office = await _office(db)
    a = await _staff(db, office, "職員A")
    await _snap(db, "plan_csv", date(2026, 10, 1), [_row(1, "09:00", "09:35", "職員A")])
    # 週 43 の個別業務は段階 0 の表から (10/19 は表示されなかった日)。
    await _snap(
        db,
        "tasks",
        MONDAY,
        [
            {
                "date": "2026-10-21",
                "start": "09:00",
                "end": "18:00",
                "title": "休み",
                "staff_name": "職員 A",
                "external_key": "1:1:2026-10-21",
            },
            {
                "date": "2026-10-22",
                "start": "10:00",
                "end": "10:00",
                "title": "メモ",
                "staff_name": "職員A",
                "external_key": "2:1:2026-10-22",
            },
        ],
        covered=[(MONDAY + timedelta(days=i)).isoformat() for i in range(1, 6)],
    )
    # 取り込み済みの kaipoke 行: 10/21 は表にあるので読まない、10/19 は代わりに読む。
    for d in (date(2026, 10, 19), date(2026, 10, 21)):
        db.add(
            StaffEvent(
                staff_id=a.id,
                event_type="event",
                source="kaipoke",
                title="会議",
                starts_at=datetime.combine(d, time(10, 0)),
                ends_at=datetime.combine(d, time(11, 0)),
            )
        )
    # 日をまたぐ予定は 24:00 まで、終わり ≤ 始まりは捨てる。
    db.add(
        StaffEvent(
            staff_id=a.id,
            event_type="event",
            source="manual",
            title="夜勤",
            starts_at=datetime(2026, 10, 23, 16, 0),
            ends_at=datetime(2026, 10, 24, 1, 0),
        )
    )
    core = await _core(db, office)
    assert _day(core, MONDAY)["tasks_from"] == "staff_events"
    assert _day(core, date(2026, 10, 21))["tasks_from"] == "snapshot"
    assert _day(core, date(2026, 10, 12))["tasks_from"] == "staff_events"
    assert _cell(core, a, date(2026, 10, 21))["real"]["state"] == "off"
    assert _cell(core, a, date(2026, 10, 21))["real"]["why"] == "休み（カイポケ）"
    assert 10 not in _cell(core, a, MONDAY)["real"]["hours"]
    assert max(_cell(core, a, date(2026, 10, 23))["real"]["hours"]) == 15
    assert core["sources"]["dropped_events"] == 1


@pytest.mark.asyncio
async def test_actual_vs_plan_by_day_and_duplicates(db) -> None:
    office = await _office(db)
    a = await _staff(db, office, "職員A")
    plan = [
        _row(d, f"{9 + k}:00", f"{9 + k}:35", "職員A", patient=f"P{k}")
        for d in (13, 14, 15)
        for k in range(4)
    ]
    plan += [_row(16, "09:00", "09:35", "職員A")]
    actual = (
        [_row(13, "09:00", "09:35", "職員A", patient="P0")]  # 4 対 1 → 予定で見る
        + [
            _row(14, "09:00", "09:35", "職員A", patient="P0"),
            _row(14, "15:00", "15:35", "職員A", patient="P9"),
        ]  # 4 対 2 → 実績
        + [_row(15, "09:00", "09:35", "職員A", patient="P0")] * 3  # 重複は 1 件 → 予定で見る
    )
    await _snap(db, "plan_csv", date(2026, 10, 1), plan)
    await _snap(db, "actual_csv", date(2026, 10, 1), actual)
    core = await _core(db, office)
    assert _day(core, date(2026, 10, 13))["src"] == "plan_fallback"
    assert _day(core, date(2026, 10, 14))["src"] == "actual"
    assert _cell(core, a, date(2026, 10, 14))["n"] == 2
    assert _day(core, date(2026, 10, 15))["src"] == "plan_fallback"
    assert _day(core, date(2026, 10, 16))["src"] == "plan_fallback"  # 予定 1・実績 0
    assert _day(core, TODAY)["src"] == "plan"  # 今日は予定
    # 9 月は材料が無い → データなし。
    assert _day(core, date(2026, 9, 1))["state"] == "no_data"


@pytest.mark.asyncio
async def test_empty_month_is_data_and_failed_only_is_no_data(db) -> None:
    office = await _office(db)
    await _staff(db, office, "職員A")
    await _snap(db, "plan_csv", date(2026, 10, 1), [_row(19, "09:00", "09:35", "職員A")])
    await _snap(db, "actual_csv", date(2026, 10, 1), [], status="empty")
    await _snap(db, "plan_csv", date(2026, 9, 1), [], status="failed")
    core = await _core(db, office)
    assert _day(core, date(2026, 10, 1))["state"] == "ok"
    assert _day(core, date(2026, 10, 1))["src"] == "actual"  # 予定 0・実績 0 (empty)
    assert _day(core, date(2026, 9, 30))["state"] == "no_data"


@pytest.mark.asyncio
async def test_holiday_off_shift_and_not_employed(db) -> None:
    office = await _office(db)
    a = await _staff(db, office, "職員A", weekdays=range(5))  # 土曜は勤務でない
    b = await _staff(db, office, "職員B")
    await _snap(
        db,
        "plan_csv",
        date(2026, 10, 1),
        [_row(15, "09:00", "09:35", "職員B"), _row(1, "09:00", "09:35", "職員A")],
    )
    await _snap(
        db,
        "actual_csv",
        date(2026, 10, 1),
        [_row(15, "09:00", "09:35", "職員B"), _row(1, "09:00", "09:35", "職員A")],
    )
    core = await _core(db, office)
    assert _day(core, date(2026, 10, 12))["state"] == "holiday"  # スポーツの日
    assert _day(core, date(2026, 10, 12))["holiday_name"]
    assert date(2026, 10, 17).isoformat() not in core["cells"][str(a.id)]
    assert _cell(core, b, date(2026, 10, 14))["state"] == "not_employed"
    assert _cell(core, b, date(2026, 10, 15))["state"] == "ok"
    assert _cell(core, a, date(2026, 10, 1))["state"] == "ok"


@pytest.mark.asyncio
async def test_leave_overrides_and_widest_kaipoke_leave(db) -> None:
    office = await _office(db)
    a = await _staff(db, office, "職員A")
    await _snap(db, "plan_csv", date(2026, 10, 1), [_row(19, "09:00", "09:35", "職員A")])
    db.add(
        StaffWeeklyOverride(
            staff_id=a.id, iso_year=2026, iso_week=43, weekday=2, override_type="paid_off"
        )
    )
    db.add(
        StaffWeeklyOverride(
            staff_id=a.id, iso_year=2026, iso_week=43, weekday=3, override_type="am_off"
        )
    )
    for title, s, e in (("PM休み", 12, 18), ("休み", 9, 18)):
        db.add(
            StaffEvent(
                staff_id=a.id,
                event_type="event",
                source="kaipoke",
                title=title,
                starts_at=datetime(2026, 10, 23, s, 0),
                ends_at=datetime(2026, 10, 23, e, 0),
            )
        )
    db.add(
        StaffEvent(
            staff_id=a.id,
            event_type="event",
            source="kaipoke",
            title="休み",
            starts_at=datetime(2026, 10, 24, 10, 0),
            ends_at=datetime(2026, 10, 24, 11, 0),
        )
    )
    core = await _core(db, office)
    assert _cell(core, a, date(2026, 10, 21))["real"]["why"] == "有給（らく助）"
    assert min(_cell(core, a, date(2026, 10, 22))["real"]["hours"]) == 13
    assert _cell(core, a, date(2026, 10, 23))["real"]["state"] == "off"  # 終日のほう
    sat = _cell(core, a, date(2026, 10, 24))
    assert sat["real"]["unknown_leave"] == "休み"
    assert 10 not in sat["real"]["hours"] and 10 in sat["ignore"]["hours"]


@pytest.mark.asyncio
async def test_candidates_by_office_and_kind(db) -> None:
    office = await _office(db)
    other = await _office(db, "TSUGA")
    await _staff(db, office, "職員A")
    await _staff(db, office, "マネA", role="manager")
    await _staff(db, office, "新人A", trainee=True)
    sec = await _staff(db, other, "兼務A")
    db.add(StaffSecondaryOffice(staff_id=sec.id, office_id=office.id))
    await _staff(db, other, "他拠点A")
    core = await _core(db, office)
    kinds = {s["name"]: (s["kind"], s["default_selected"]) for s in core["staff"]}
    assert kinds == {
        "職員A": ("staff", True),
        "マネA": ("manager", False),
        "新人A": ("trainee", False),
        "兼務A": ("staff", True),
    }


def test_today_switches_at_jst_midnight() -> None:
    assert data.jst_today(datetime(2026, 10, 19, 14, 59, tzinfo=UTC)) == date(2026, 10, 19)
    assert data.jst_today(datetime(2026, 10, 19, 15, 0, tzinfo=UTC)) == date(2026, 10, 20)


@pytest.mark.asyncio
async def test_iso_week_53_and_month_crossing(db) -> None:
    mondays = data.view_mondays(date(2027, 1, 5))
    assert date(2026, 12, 28) in mondays  # 2026 年 第 53 週
    assert len(set(mondays)) == 8
    office = await _office(db)
    a = await _staff(db, office, "職員A")
    await _snap(db, "plan_csv", date(2026, 12, 1), [_row(29, "09:00", "09:35", "職員A")])
    await _snap(db, "plan_csv", date(2027, 1, 1), [_row(2, "10:00", "10:35", "職員A")])
    core = await _core(db, office, today=date(2027, 1, 5), plan_only=True)
    weeks = {(w["iso_year"], w["iso_week"]) for w in core["weeks"]}
    assert (2026, 53) in weeks and (2027, 1) in weeks
    assert _cell(core, a, date(2026, 12, 29))["n"] == 1
    assert _cell(core, a, date(2027, 1, 2))["n"] == 1


# --- 取り直しの範囲 -------------------------------------------------------------


def test_refresh_targets_recent_by_days() -> None:
    today = date(2026, 10, 3)  # 土曜
    mondays = data.view_mondays(today)
    fresh = datetime(2026, 9, 28, 0, 0, tzinfo=UTC)
    fetched = {m: fresh for m in mondays if m < date(2026, 9, 21)}  # 週の終わりの後に取った週
    fetched[date(2026, 9, 14)] = datetime(2026, 9, 18, 0, 0, tzinfo=UTC)  # その週の日曜より前
    targets = plan_targets(today, "recent", fetched)
    kinds = [(t.kind, t.period_start) for t in targets]
    assert ("actual_csv", date(2026, 9, 1)) in kinds and ("actual_csv", date(2026, 10, 1)) in kinds
    assert [p for k, p in kinds if k == "plan_csv"] == [date(2026, 9, 1), date(2026, 10, 1)]
    weeks = [p for k, p in kinds if k == "tasks"]
    assert weeks == [date(2026, 9, 14), date(2026, 9, 21), date(2026, 9, 28)]


def test_refresh_targets_all_and_plan_shape() -> None:
    targets = plan_targets(TODAY, "all", {})
    assert [t.period_start for t in targets if t.kind == "tasks"] == sorted(
        data.view_mondays(TODAY)
    )
    assert [t.period_start for t in targets if t.kind == "actual_csv"] == [
        date(2026, 8, 1),
        date(2026, 9, 1),
        date(2026, 10, 1),
    ]
    assert source_plan([RefreshTarget("tasks", MONDAY)]) == [sources.SourceSpec("tasks", MONDAY)]


# --- API ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_strict_api_admin_and_staff_rounding(client, db) -> None:
    admin, staff_user = await _user(db, "admin"), await _user(db, "staff")
    office = await _office(db)
    a = await _staff(db, office, "職員A")
    await _snap(db, "plan_csv", date(2026, 10, 1), [_row(1, "09:00", "09:35", "職員A")])
    db.add(
        StaffWeeklyOverride(
            staff_id=a.id, iso_year=2026, iso_week=43, weekday=2, override_type="paid_off"
        )
    )
    db.add(
        StaffWeeklyOverride(
            staff_id=a.id, iso_year=2026, iso_week=43, weekday=3, override_type="pm_off"
        )
    )
    db.add(
        AcceptanceCalendar(
            office_id=office.id, weekday=0, time_slot=time(10, 0), status="available", notes="手で"
        )
    )
    await db.commit()
    url = f"/api/v1/acceptance-matrix/strict?office_id={office.id}"
    res = await client.get(url, headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["rules"]["visit_min"] == 35 and body["rules"]["cap"] == 7
    assert body["overrides"] == [
        {"weekday": 0, "hour": 10, "time_slot": "10:00:00", "status": "available", "note": "手で"}
    ]
    wed = body["cells"][str(a.id)]["2026-10-21"]["real"]
    assert wed["why"] == "有給（らく助）"
    res = await client.get(url, headers=_bearer(staff_user))
    cells = res.json()["cells"][str(a.id)]
    assert cells["2026-10-21"]["real"]["why"] == "休み"
    assert cells["2026-10-22"]["real"]["why"] == "半休"
    assert (
        await client.get(url.replace(str(office.id), str(uuid.uuid4())), headers=_bearer(admin))
    ).status_code == 404


@pytest.mark.asyncio
async def test_refresh_api_runs_stage0_job_and_does_not_touch_csv_snapshots(
    client, db, monkeypatch
) -> None:
    admin, staff_user = await _user(db, "admin"), await _user(db, "staff")
    calls: list[tuple[uuid.UUID, list[sources.SourceSpec]]] = []

    async def fake_run_source_job(session, _client, job_id, plan) -> None:
        calls.append((job_id, plan))
        failed = sources.SourceFetchResult(
            kind="tasks", period_start=MONDAY, status="failed", error="取れませんでした"
        )
        await sources.finish_source_job(session, job_id, [failed])

    monkeypatch.setattr(sources, "run_source_job", fake_run_source_job)
    from app.api.v1 import acceptance_matrix as api

    monkeypatch.setattr(api, "jst_today", lambda now=None: TODAY)
    csv_before = await db.scalar(select(func.count()).select_from(KaipokeCsvSnapshot))

    assert (
        await client.post(
            "/api/v1/acceptance-matrix/strict/refresh",
            json={"scope": "recent"},
            headers=_bearer(staff_user),
        )
    ).status_code == 403
    res = await client.post(
        "/api/v1/acceptance-matrix/strict/refresh", json={"scope": "all"}, headers=_bearer(admin)
    )
    assert res.status_code == 202, res.text
    body = res.json()
    job_id = uuid.UUID(body["job_id"])
    assert body["total"] == len(calls[0][1])
    assert calls[0][0] == job_id
    assert {p.kind for p in calls[0][1]} == {"plan_csv", "actual_csv", "tasks"}
    assert all(isinstance(p, sources.SourceSpec) for p in calls[0][1])

    job = await db.get(KaipokeJob, job_id, populate_existing=True)
    assert job.params["op"] == "acceptance-refresh"
    assert job.job_type == "fetch"
    assert job.week_start == date(2026, 8, 31)  # 範囲 (8 週) の最初の月曜

    view = (
        await client.get(
            f"/api/v1/acceptance-matrix/strict/refresh/{job_id}", headers=_bearer(staff_user)
        )
    ).json()
    assert view["status"] == "completed"
    assert view["failed"] == [
        {"kind": "tasks", "period_start": "2026-10-19", "reason": "取れませんでした"}
    ]
    # 登録の順: latest が {job_id} に取られない。
    latest = await client.get(
        "/api/v1/acceptance-matrix/strict/refresh/latest", headers=_bearer(admin)
    )
    assert latest.status_code == 200, latest.text
    assert latest.json()["job"]["id"] == str(job_id)
    assert (
        await client.get(
            f"/api/v1/acceptance-matrix/strict/refresh/{uuid.uuid4()}", headers=_bearer(admin)
        )
    ).status_code == 404
    assert await db.scalar(select(func.count()).select_from(KaipokeCsvSnapshot)) == csv_before


@pytest.mark.asyncio
async def test_refresh_api_conflict_when_rpa_job_running(client, db) -> None:
    admin = await _user(db, "admin")
    db.add(
        KaipokeJob(
            job_type="push", status="running", week_start=MONDAY, params={"op": "events-outbound"}
        )
    )
    await db.commit()
    res = await client.post(
        "/api/v1/acceptance-matrix/strict/refresh", json={"scope": "recent"}, headers=_bearer(admin)
    )
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_plan_only_rejected_in_production(client, db, monkeypatch) -> None:
    admin = await _user(db, "admin")
    office = await _office(db)
    await db.commit()
    from app.api.v1 import acceptance_matrix as api

    class _Prod:
        is_production = True

    monkeypatch.setattr(api, "get_settings", lambda: _Prod())
    res = await client.get(
        f"/api/v1/acceptance-matrix/strict?office_id={office.id}&plan_only=true",
        headers=_bearer(admin),
    )
    assert res.status_code == 403


class _StubRpa:
    """RPA のクライアントだけのモック (段階 0 の取り直しは本物を通す)。"""

    def __init__(self) -> None:
        self.exports: list[dict[str, Any]] = []
        self.tasks: list[dict[str, Any]] = []

    async def status(self) -> dict[str, Any]:
        return {"current_task": {"running": False}}

    async def export(self, payload: dict[str, Any], *, timeout: float | None = None) -> dict:
        self.exports.append(dict(payload))
        header = "職員名１,職員名２,職員名３,日付,利用者,業務種別,開始時間,終了時間"
        body = "職員A,,,21,利用者001,医療保険,10:00,10:50" if payload["division"] == "plan" else ""
        return {
            "result": {
                "success": True,
                "csv_content": f"{header}\n{body}\n",
                "division": payload["division"],
            }
        }

    async def individual_tasks(
        self, payload: dict[str, Any], *, timeout: float | None = None
    ) -> dict:
        self.tasks.append(dict(payload))
        if payload["date"] == "2026-10-12":
            return {"success": False, "error": "取れませんでした", "result": {}}
        tasks = (
            [
                {
                    "date": "2026-10-22",
                    "start": "09:00",
                    "end": "18:00",
                    "title": "休み",
                    "staff_name": "職員A",
                    "external_key": "1:1:2026-10-22",
                }
            ]
            if payload["date"] == "2026-10-19"
            else []
        )
        return {"success": True, "result": {"success": True, "tasks": tasks}}


@pytest.mark.asyncio
async def test_refresh_end_to_end_with_stage0(client, db, monkeypatch) -> None:
    """API → start_source_job → 背景で段階 0 の run_source_job (本物) → 表に反映。"""
    from app.api.v1 import acceptance_matrix as api
    from app.services import kaipoke_client

    monkeypatch.setattr(api, "jst_today", lambda now=None: TODAY)
    monkeypatch.setattr(sources, "RETRY_INTERVAL_SECONDS", 0.0)
    rpa = _StubRpa()
    kaipoke_client.set_test_client(rpa)  # type: ignore[arg-type]
    try:
        admin = await _user(db, "admin")
        office = await _office(db)
        a = await _staff(db, office, "職員A")
        await _snap(db, "plan_csv", date(2026, 9, 1), [_row(1, "09:00", "09:35", "職員A")])
        await db.commit()
        csv_before = await db.scalar(select(func.count()).select_from(KaipokeCsvSnapshot))

        res = await client.post(
            "/api/v1/acceptance-matrix/strict/refresh",
            json={"scope": "recent"},
            headers=_bearer(admin),
        )
        assert res.status_code == 202, res.text
        job_id = res.json()["job_id"]
        # 実績 10 月・予定 10 月・個別業務 8 週 = 10 本。
        assert res.json()["total"] == 10
        assert [p["division"] for p in rpa.exports] == ["actual", "plan"]
        assert len(rpa.tasks) == 8

        view = (
            await client.get(
                f"/api/v1/acceptance-matrix/strict/refresh/{job_id}", headers=_bearer(admin)
            )
        ).json()
        assert view["status"] == "completed"
        assert view["done"] == 10
        assert view["failed"] == [
            {"kind": "tasks", "period_start": "2026-10-12", "reason": "取れませんでした"}
        ]

        body = (
            await client.get(
                f"/api/v1/acceptance-matrix/strict?office_id={office.id}", headers=_bearer(admin)
            )
        ).json()
        cells = body["cells"][str(a.id)]
        assert 10 not in cells["2026-10-21"]["real"]["hours"]  # 予定 CSV の訪問
        assert cells["2026-10-22"]["real"]["why"] == "休み（カイポケ）"  # 個別業務
        days = {d["date"]: d for d in body["days"]}
        assert days["2026-10-13"]["tasks_from"] == "staff_events"  # 取れなかった週は代わり
        assert days["2026-10-20"]["tasks_from"] == "snapshot"
        assert body["sources"]["last_job"]["failed"] == view["failed"]
        assert await db.scalar(select(func.count()).select_from(KaipokeCsvSnapshot)) == csv_before
    finally:
        kaipoke_client.set_test_client(None)
