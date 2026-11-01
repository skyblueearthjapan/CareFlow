"""受け入れ枠「月で見る」— 数え方 (純粋な関数)・材料の読み込み・API・A4.

試算: docs/reports/agent/2026-11-受け入れ枠の月の埋まり.html。ローカル SQLite だけ。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Office, User
from app.models.patient import Patient
from app.models.staff import Staff, StaffShift, StaffWeeklyOverride
from app.models.visit import Visit
from app.services.scheduling import acceptance_month_data as month_data
from app.services.scheduling.acceptance_month import (
    CROWDED_UTILIZATION,
    MonthStaffDay,
    MonthVisit,
    add_trend,
    day_slots,
    month_rules,
    summarize_month,
)
from app.services.scheduling.acceptance_month_print import render_month_html, util_band
from app.services.scheduling.acceptance_strict import StaffDay, StrictRules
from app.services.scheduling.config import DEFAULT_SCHEDULING_CONFIG

RULES = StrictRules.from_config(DEFAULT_SCHEDULING_CONFIG)
NO_LUNCH = month_rules(RULES, lunch=False)
TODAY = date(2026, 10, 20)
# 2026 年 11 月の月曜は 2・9・16・23・30 の 5 日。11/3 (火) は祝日。
NOV_MONDAYS = [date(2026, 11, d) for d in (2, 9, 16, 23, 30)]


def hm(text: str) -> int:
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def _sd(day: date, *, sid: str = "s1", visits=(), n: int | None = None, **kw) -> StaffDay:
    vs = [(hm(a), hm(b)) for a, b in visits]
    return StaffDay(
        staff_id=sid,
        day=day,
        shift=(hm("09:30"), hm("18:00")),
        n=len(vs) if n is None else n,
        visits=vs,
        **kw,
    )


def _msd(day: date, *, sid: str = "s1", visits=(), fixed=(), n: int | None = None, **kw):
    sd = _sd(day, sid=sid, visits=visits, n=n, **kw)
    primary = [MonthVisit(hm(a), hm(b), time_fixed=i in fixed) for i, (a, b) in enumerate(visits)]
    return MonthStaffDay(sd=sd, primary=primary)


# --- 数え方 (純粋な関数) ------------------------------------------------------


def test_day_slots_free_work_and_busy_by_30_minutes() -> None:
    ds = day_slots(_msd(NOV_MONDAYS[0], visits=[("10:00", "11:00")]), NO_LUNCH, use_off=True)
    # 9:30 開始は 10:05 まで → 前の間 (9:40〜) に重なる。11:20 からなら入れる (11:00 のマス)。
    assert hm("09:30") not in ds.free
    assert hm("10:00") not in ds.free and hm("10:30") not in ds.free
    assert hm("11:00") in ds.free and hm("17:00") in ds.free
    # 17:30 開始は 18:05 に終わる → 勤務の窓を出るので入れない。
    assert hm("17:30") not in ds.free
    assert ds.work[hm("10:00")] == 30 and ds.busy[hm("10:00")] == 30
    assert ds.busy.get(hm("11:00"), 0) == 0


def test_lunch_switch_changes_free_slots() -> None:
    # 11:30〜13:30 を訪問で埋める → 昼 60 分が取れず、考えるとその日は入れない。
    msd = _msd(NOV_MONDAYS[0], visits=[("11:20", "12:20"), ("12:40", "13:40")])
    assert day_slots(msd, RULES, use_off=True).free == set()
    assert hm("15:00") in day_slots(msd, NO_LUNCH, use_off=True).free


def test_off_day_and_cap() -> None:
    off = day_slots(_msd(NOV_MONDAYS[0], rk_type="off"), RULES, use_off=True)
    assert off.off and off.work == {}
    ignore = day_slots(_msd(NOV_MONDAYS[0], rk_type="off"), RULES, use_off=False)
    assert not ignore.off and ignore.work[hm("09:30")] == 30
    full = day_slots(_msd(NOV_MONDAYS[0], visits=[("09:30", "10:00")], n=7), RULES, use_off=True)
    assert full.full and full.free == set() and full.work


def test_month_marks_use_the_weeks_of_the_month() -> None:
    days = []
    for i, d in enumerate(NOV_MONDAYS):
        # 9:30 のマスは毎週空き、15:00 のマスは 1 週目だけ空き (2 週目以降は 14:30〜16:00 が訪問)。
        visits = [("16:50", "17:50")] if i == 0 else [("14:30", "16:00")]
        days.append(_msd(d, visits=visits))
    out = summarize_month(NOV_MONDAYS, NOV_MONDAYS, days, {"s1"}, [0], NO_LUNCH, use_off=True)
    nine = out["cells"]["0-09:30"]
    assert (nine["mark"], nine["ok"], nine["counted"], nine["total"]) == ("○", 5, 5, 5)
    three = out["cells"]["0-15:00"]
    assert (three["mark"], three["ok"]) == ("×", 1)
    # 5 週の月は 4 週以上で △ (17:00 のマスは 1 週目だけ塞がっている)。
    five = out["cells"]["0-17:00"]
    assert (five["mark"], five["ok"], five["per_week"]) == ("△", 4, [0, 1, 1, 1, 1])
    # 数える日が月の半分に満たないと「－」。
    short = summarize_month(NOV_MONDAYS, NOV_MONDAYS[:2], days, {"s1"}, [0], NO_LUNCH, use_off=True)
    assert short["cells"]["0-09:30"]["mark"] == "－"


def test_utilization_crowded_and_detail() -> None:
    d = NOV_MONDAYS[0]
    a = _msd(
        d,
        sid="a",
        visits=[("10:00", "10:30"), ("10:40", "11:10"), ("13:30", "14:00")]
        + [("15:00", "15:10")] * 3,
        fixed=(1,),
    )
    b = _msd(d, sid="b", visits=[])
    out = summarize_month([d], [d], [a, b], {"a", "b"}, [0], RULES, use_off=True)
    ten = out["cells"]["0-10:00"]
    # 10:00 のマス: a が 30 分訪問・b は 0 分 → 30 / 60。
    assert ten["utilization"] == pytest.approx(0.5)
    assert ten["crowded"] is (0.5 >= CROWDED_UTILIZATION)
    half = out["cells"]["0-10:30"]
    # 10:30〜11:00 は a が 10:30 で終わり 10:40 から → 20 分 / 60。
    assert half["utilization"] == pytest.approx(20 / 60)
    assert half["starts"] == 1 and half["pairs"] == 1
    assert half["tight_ratio"] == 1.0  # 10:30 → 10:40 は 10 分 (< 20 分)
    assert half["fixed_ratio"] == 1.0
    # a は 6 件 → 勤務しているマスに「6 件以上の職員」1 人。
    assert ten["busy_staff_avg"] == 1.0
    # 選ばれていない職員は数えない。
    only_a = summarize_month([d], [d], [a, b], {"a"}, [0], RULES, use_off=True)
    assert only_a["cells"]["0-10:00"]["utilization"] == 1.0
    assert only_a["cells"]["0-10:00"]["crowded"] is True
    assert only_a["counts"]["crowded"] >= 1
    assert out["weekday_summary"][0]["visits_per_day"] == 6.0
    assert out["peaks"]["utilization"][0]["slot"] in {"10:00", "13:30", "15:00"}


def test_trend_marks_up_and_down() -> None:
    cur = {
        "cells": {
            "0-10:00": {"utilization": 0.8},
            "0-11:00": {"utilization": 0.2},
            "0-12:00": {"utilization": 0.5},
            "0-13:00": {"utilization": None},
        }
    }
    prev = {
        "cells": {
            "0-10:00": {"utilization": 0.6},
            "0-11:00": {"utilization": 0.4},
            "0-12:00": {"utilization": 0.45},
            "0-13:00": {"utilization": 0.5},
        }
    }
    add_trend(cur, prev)
    assert (
        cur["cells"]["0-10:00"]["trend"] == "▲" and cur["cells"]["0-10:00"]["trend_points"] == 20.0
    )
    assert cur["cells"]["0-11:00"]["trend"] == "▼"
    assert cur["cells"]["0-12:00"]["trend"] is None
    assert cur["cells"]["0-13:00"]["trend_points"] is None


def test_util_band() -> None:
    assert util_band(None, 0.7) == "none"
    assert util_band(0.0, 0.7) == "u0"
    assert util_band(0.75, 0.7) == "u4"
    assert util_band(0.55, 0.7) == "u3"


# --- 材料の読み込み -------------------------------------------------------------


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


async def _staff(db, office: Office, name: str, *, role: str = "staff") -> Staff:
    s = Staff(name=name, role=role, is_trainee=False, primary_office_id=office.id)
    db.add(s)
    await db.flush()
    for wd in range(7):
        db.add(
            StaffShift(
                staff_id=s.id,
                weekday=wd,
                is_on=wd < 6,
                start_time=time(9, 30),
                end_time=time(18, 0),
            )
        )
    await db.flush()
    return s


async def _patient(db, office: Office, code: str = "P1", pattern=None) -> Patient:
    p = Patient(
        code=code,
        name=f"利用者{code}",
        status="active",
        primary_office_id=office.id,
        weekly_pattern=pattern,
    )
    db.add(p)
    await db.flush()
    return p


def _visit(patient: Patient, staff: Staff | None, d: date, start: str, end: str, **kw) -> Visit:
    return Visit(
        patient_id=patient.id,
        visit_date=d,
        start_time=time.fromisoformat(start),
        end_time=time.fromisoformat(end),
        type="regular",
        status=kw.pop("status", "planned"),
        source="auto",
        required_staff_count=1,
        primary_staff_id=staff.id if staff else None,
        **kw,
    )


@pytest.fixture
def _one_visit_opens_day(monkeypatch) -> None:
    monkeypatch.setattr(month_data, "OPEN_DAY_MIN_VISITS", 1)


@pytest.mark.asyncio
@pytest.mark.usefixtures("_one_visit_opens_day")
async def test_month_view_reads_visits_and_skips_cancelled(db) -> None:
    office = await _office(db)
    a = await _staff(db, office, "職員A")
    m = await _staff(db, office, "管理者M", role="manager")
    fixed = {"entries": [{"weekday": 1, "time_type": "固定", "preferred_start": "10:00"}]}
    p = await _patient(db, office, pattern=fixed)
    tue = date(2026, 11, 3)  # 祝日でも数える
    db.add_all(
        [
            _visit(p, a, tue, "10:00", "10:30"),
            _visit(p, a, tue, "10:30", "11:00", status="cancelled"),
            _visit(p, a, tue, "13:00", "13:30", deleted_at=datetime(2026, 11, 1)),
            _visit(p, m, tue, "15:00", "15:30"),
        ]
    )
    await db.commit()
    view = await month_data.build_month_view(db, date(2026, 11, 1), office.id, today=TODAY)
    assert view["month"] == "2026-11" and view["prev_month"] == "2026-10"
    assert view["source"] == "plan"
    assert view["selected_staff_ids"] == [str(a.id)]  # 既定は職員だけ
    assert {"date": "2026-11-03", "open": True} in view["days"]
    assert {"date": "2026-11-04", "open": False} in view["days"]  # 訪問の無い日は数えない
    ten = view["cells"]["1-10:00"]
    assert ten["utilization"] == 1.0 and ten["starts"] == 1 and ten["fixed_ratio"] == 1.0
    assert view["cells"]["1-10:30"]["utilization"] == 0.0  # 取消は数えない
    assert view["cells"]["1-13:00"]["utilization"] == 0.0  # 削除も数えない
    assert view["cells"]["1-15:00"]["utilization"] == 0.0  # マネージャーは選ばれていない
    assert view["cells"]["1-10:00"]["crowded"] is True
    assert view["cells"]["1-10:00"]["trend"] is None  # 前の月は訪問が無い

    both = await month_data.build_month_view(
        db, date(2026, 11, 1), office.id, staff_ids=[a.id, m.id], today=TODAY
    )
    assert both["cells"]["1-15:00"]["utilization"] == 0.5
    no_lunch = await month_data.build_month_view(
        db, date(2026, 11, 1), office.id, lunch=False, today=TODAY
    )
    assert no_lunch["lunch"] is False and no_lunch["rules"]["lunch_considered"] is False


@pytest.mark.asyncio
@pytest.mark.usefixtures("_one_visit_opens_day")
async def test_month_view_leave_mode_and_trend(db) -> None:
    office = await _office(db)
    a = await _staff(db, office, "職員A")
    p = await _patient(db, office)
    wed = date(2026, 11, 11)
    db.add(_visit(p, a, wed, "09:30", "10:00"))
    db.add(_visit(p, None, date(2026, 11, 18), "09:30", "10:00"))  # 担当なしでも日を開く
    db.add(_visit(p, None, date(2026, 10, 14), "09:30", "10:00"))
    db.add(
        StaffWeeklyOverride(
            staff_id=a.id, iso_year=2026, iso_week=46, weekday=2, override_type="off"
        )
    )
    await db.commit()
    real = await month_data.build_month_view(db, date(2026, 11, 1), office.id, today=TODAY)
    # 11/11 は休み → 勤務できた分に入らない。11/18 の 9:30 は空いている。
    assert real["cells"]["2-09:30"]["utilization"] == 0.0
    assert real["cells"]["2-09:30"]["per_week"] == [0, 1]
    ignore = await month_data.build_month_view(
        db, date(2026, 11, 1), office.id, mode="ignore", today=TODAY
    )
    assert ignore["cells"]["2-09:30"]["utilization"] == 0.5
    # 前の月 (10/14) は 0% → 今月 50% で ▲。
    assert ignore["cells"]["2-09:30"]["trend"] == "▲"
    assert ignore["cells"]["2-09:30"]["prev_utilization"] == 0.0


# --- API ----------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.usefixtures("_one_visit_opens_day")
async def test_month_api_roles_html_and_errors(client, db) -> None:
    admin, staff_user = await _user(db, "admin"), await _user(db, "staff")
    office = await _office(db)
    a = await _staff(db, office, "職員A")
    p = await _patient(db, office)
    db.add(_visit(p, a, date(2026, 11, 2), "10:00", "10:30"))
    await db.commit()
    url = f"/api/v1/acceptance-matrix/month?month=2026-11&office_id={office.id}"
    res = await client.get(url, headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert "html" not in body
    assert body["slots"][0] == "09:30" and body["slots"][-1] == "17:30"
    assert body["rules"]["crowded_utilization"] == CROWDED_UTILIZATION
    res = await client.get(f"{url}&include_html=true&lunch=false", headers=_bearer(staff_user))
    assert res.status_code == 200
    page = res.json()["html"]
    assert "A4 横" in page and "2026 年 11 月" in page and "昼休みは考えない" in page
    empty = await client.get(f"{url}&staff=", headers=_bearer(admin))
    assert empty.json()["selected_staff_ids"] == []
    picked = await client.get(f"{url}&staff={a.id}", headers=_bearer(admin))
    assert picked.json()["selected_staff_ids"] == [str(a.id)]
    assert (await client.get(url)).status_code == 401
    bad = await client.get(url.replace("2026-11", "2026-13"), headers=_bearer(admin))
    assert bad.status_code == 422
    bad_staff = await client.get(f"{url}&staff=xyz", headers=_bearer(admin))
    assert bad_staff.status_code == 422
    gone = url.replace(str(office.id), str(uuid.uuid4()))
    assert (await client.get(gone, headers=_bearer(admin))).status_code == 404


def test_print_escapes_names() -> None:
    d = NOV_MONDAYS[0]
    out = summarize_month([d], [d], [_msd(d)], {"s1"}, [0], RULES, use_off=True)
    add_trend(out, {"cells": {}})
    view = {
        "month": "2026-11",
        "office_id": None,
        "offices": [],
        "source": "record",
        "mode": "real",
        "lunch": True,
        "weekdays": [0],
        "slots": [f"{h // 60:02d}:{h % 60:02d}" for h in range(570, 1080, 30)],
        "rules": {
            **RULES.as_dict(),
            "crowded_utilization": 0.7,
            "trend_points": 10,
            "open_day_min_visits": 5,
        },
        "staff": [{"id": "s1", "name": "<b>職員</b>"}],
        "selected_staff_ids": ["s1"],
        "prev_utilization": None,
        **out,
    }
    page = render_month_html(view)
    assert "&lt;b&gt;職員&lt;/b&gt;" in page and "<b>職員</b>" not in page
    assert "全て" in page and "@page{size:A4 landscape" in page
