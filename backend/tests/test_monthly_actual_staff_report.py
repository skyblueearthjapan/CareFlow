"""月の実績の看護師別 (GET /monthly-actual/{month}/staff-summary・/report).

* 週の平均患者数 = 月の中の週 (月曜はじまり・その月に入る日だけ) の患者数を、訪問のあった
  週で平均する。月をまたぐ週の前の月の日は数えない。
* 2 名訪問は持ち主の件数と、2 人目の「2 名訪問・同行」にそれぞれ数える (ダッシュボードと同じ)。
* 提供時間 = らく助の実績の滞在。30 分未満・退出なしは到着＋35 分。到着なしは入れない。
* 移動距離はダッシュボードのスタッフ別の実績と同じ値になる。
* 管理者だけ。A4 の見出し。
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Patient, Staff, User, Visit
from app.models.office import Office
from app.models.visit_checkin import VisitCheckin
from app.services.monthly_actual.staff_report import (
    RULE_NO_DEPARTURE,
    MonthStaffReport,
    StaffMonth,
    VisitLine,
    build_month_staff_report,
)
from app.services.monthly_actual.staff_report_html import render_month_staff_report_html

MONTH = "2026-10"
URL = f"/api/v1/monthly-actual/{MONTH}"


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


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


def _jst(day: date, hm: str) -> datetime:
    h, m = (int(x) for x in hm.split(":"))
    return datetime(day.year, day.month, day.day, h, m, tzinfo=UTC) - timedelta(hours=9)


async def _visit(
    db,
    staff: Staff,
    patient: Patient,
    day: date,
    *,
    arr: str | None = None,
    dep: str | None = None,
    start: str = "09:00",
    **kw,
) -> Visit:
    h, m = (int(x) for x in start.split(":"))
    visit = Visit(
        patient_id=patient.id,
        primary_staff_id=staff.id,
        visit_date=day,
        start_time=time(h, m),
        end_time=time(h + 1, m),
        type="regular",
        status=kw.pop("status", "planned"),
        **kw,
    )
    db.add(visit)
    await db.flush()
    for kind, value in (("arrival", arr), ("departure", dep)):
        if value is None:
            continue
        at = _jst(day, value)
        db.add(
            VisitCheckin(
                visit_id=visit.id,
                patient_id=patient.id,
                staff_id=staff.id,
                kind=kind,
                scanned_at=at,
                created_at=at,
                match_status="match",
                checkin_source="qr",
                threshold_snapshot={"v": 1},
            )
        )
    await db.commit()
    return visit


async def _seed(db) -> dict[str, Staff]:
    office = Office(name="本所", short_label="本", lat=35.60, lng=140.10)
    db.add(office)
    await db.commit()
    a = Staff(name="佐藤 花子", primary_office_id=office.id)
    b = Staff(name="鈴木 一郎", primary_office_id=office.id)
    db.add_all([a, b])
    p1 = Patient(code="P1", name="青木 さくら", lat=35.62, lng=140.10)
    p2 = Patient(code="P2", name="石田 健", lat=35.62, lng=140.13)
    p3 = Patient(code="P3", name="木下 誠", lat=None, lng=None)  # 座標なし
    db.add_all([p1, p2, p3])
    await db.commit()
    # 前の月 (月をまたぐ週の 9/30 水) は数えない。
    await _visit(db, a, p1, date(2026, 9, 30), arr="09:00", dep="09:50")
    # 週 1 (10/1 木〜10/4 日): 患者 2 名。
    await _visit(db, a, p1, date(2026, 10, 1), arr="09:00", dep="09:45")  # 45 分
    await _visit(db, a, p2, date(2026, 10, 2), arr="10:00", dep="10:20")  # 20 分 → 35
    # 週 2 (10/5〜10/11): 患者 1 名。2 名訪問 (2 人目 = 鈴木) を含む。
    await _visit(db, a, p1, date(2026, 10, 5), arr="09:00")  # 退出なし → 35
    await _visit(db, a, p1, date(2026, 10, 6), secondary_staff_id=b.id)  # 到着なし
    await _visit(db, a, p2, date(2026, 10, 7), status="cancelled", arr="09:00", dep="10:00")
    # 週 5 (10/26〜10/31・11/1 は入れない): 患者 1 名。
    await _visit(db, a, p3, date(2026, 10, 27), arr="09:00", dep="10:00")  # 60 分
    return {"a": a, "b": b}


def _row(body: dict, staff: Staff) -> dict:
    return next(r for r in body["staff"] if r["staff_id"] == str(staff.id))


@pytest.mark.asyncio
async def test_staff_summary_items(client, db) -> None:
    admin = await _user(db, "admin")
    staff = await _seed(db)
    res = await client.get(f"{URL}/staff-summary", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["date_from"] == "2026-10-01" and body["date_to"] == "2026-10-31"
    assert body["weeks"][0] == {"start": "2026-10-01", "end": "2026-10-04"}
    assert body["weeks"][-1] == {"start": "2026-10-26", "end": "2026-10-31"}

    a = _row(body, staff["a"])
    assert a["visits"] == 5  # 前の月・取消は除く
    assert a["days"] == 5
    assert a["per_day"] == 1.0
    assert a["patients"] == 3
    # 訪問のあった週 = 週 1 (2 名)・週 2 (1 名)・週 5 (1 名) → 4 ÷ 3。
    assert [w["patients"] for w in a["weeks"]] == [2, 1, 0, 0, 1]
    assert a["weekly_patients_avg"] == 1.3
    assert a["accompany_visits"] == 0
    # 提供時間: 45 + 35 (20 分) + 35 (退出なし) + 60。到着なしは入れない。
    assert a["provided_min_total"] == 175
    assert a["provided_samples"] == 4
    assert a["avg_stay_min"] == 43.8
    assert a["short_stay_count"] == 1
    assert a["no_departure_count"] == 1
    assert a["no_arrival_count"] == 1
    assert sum(w["provided_min"] for w in a["weeks"]) == 175

    # 2 名訪問の 2 人目: 件数には入れず、2 名訪問・同行に数える。
    b = _row(body, staff["b"])
    assert b["visits"] == 0 and b["accompany_visits"] == 1
    assert b["weekly_patients_avg"] is None

    assert body["totals"]["visits"] == 5
    assert body["totals"]["accompany_visits"] == 1


@pytest.mark.asyncio
async def test_distance_matches_the_dashboard(client, db) -> None:
    admin = await _user(db, "admin")
    staff = await _seed(db)
    summary = (await client.get(f"{URL}/staff-summary", headers=_bearer(admin))).json()
    dash = (
        await client.get(
            "/api/v1/dashboard/staff-performance",
            headers=_bearer(admin),
            params={"from": "2026-10-01", "to": "2026-10-31"},
        )
    ).json()
    a = _row(summary, staff["a"])
    d = next(r for r in dash["staff"] if r["staff_id"] == str(staff["a"].id))["period"]
    assert a["km_total"] > 0
    assert a["km_total"] == d["km_total"]
    assert a["km_per_day"] == d["km_per_day"]
    assert a["km_per_visit"] == d["km_per_visit"]
    assert a["skipped_legs"] == d["skipped_legs"] == 2  # 座標の無い木下様の行き帰り
    assert a["visits"] == d["visits"] and a["patients"] == d["patients"]
    db_b = next(r for r in dash["staff"] if r["staff_id"] == str(staff["b"].id))["period"]
    assert _row(summary, staff["b"])["accompany_visits"] == db_b["accompany_visits"]


@pytest.mark.asyncio
async def test_report_html_headings(client, db) -> None:
    admin = await _user(db, "admin")
    await _seed(db)
    res = await client.get(f"{URL}/report", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/html")
    assert res.headers["cache-control"] == "no-store"
    html = res.text
    for heading in (
        "看護師別の月の実績　2026 年 10 月",
        "看護師別（訪問）",
        "看護師別（時間と移動）",
        "この表の読み方",
        "全件の一覧",
        "要確認",
        "印刷 / PDF に保存",
        "佐藤 花子",
        "月の合計",
        "到着の記録がありません",
    ):
        assert heading in html, heading


@pytest.mark.asyncio
async def test_empty_month(client, db) -> None:
    admin = await _user(db, "admin")
    res = await client.get(f"{URL}/staff-summary", headers=_bearer(admin))
    assert res.status_code == 200
    assert res.json()["staff"] == []
    res = await client.get(f"{URL}/report", headers=_bearer(admin))
    assert res.status_code == 200 and "この月の訪問はありません" in res.text


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["staff-summary", "report"])
async def test_admin_only(client, db, suffix) -> None:
    staff_user = await _user(db, "staff")
    res = await client.get(f"{URL}/{suffix}", headers=_bearer(staff_user))
    assert res.status_code == 403, res.text
    res = await client.get(f"{URL}/{suffix}")
    assert res.status_code == 401, res.text
    res = await client.get(f"/api/v1/monthly-actual/2026-13/{suffix}", headers=_bearer(staff_user))
    assert res.status_code in (403, 422)


@pytest.mark.asyncio
async def test_future_days_are_not_counted_as_no_arrival(db) -> None:
    """月の途中: 今日 (JST) より後の日の到着なしは数えず、要確認にも出さない。"""
    staff = await _seed(db)
    report = await build_month_staff_report(db, MONTH, today=date(2026, 10, 5))
    assert report.counted_until == date(2026, 10, 5)
    a = next(s for s in report.staff if s.staff_id == staff["a"].id)
    assert a.no_arrival_count == 0  # 10/6 の到着なしは今日より後
    assert a.visits == 5  # 件数は変えない
    html = render_month_staff_report_html(
        report, station_name=None, generated_at=datetime(2026, 10, 5, tzinfo=UTC)
    )
    assert "今日（10/5）まで" in html
    assert "到着の記録がありません" not in html
    assert "到着の記録なし（" not in html

    done = await build_month_staff_report(db, MONTH, today=date(2026, 11, 1))
    assert done.counted_until is None
    assert next(s for s in done.staff if s.staff_id == staff["a"].id).no_arrival_count == 1


def _report_of(name: str, lines: list[VisitLine]) -> MonthStaffReport:
    sid = uuid.uuid4()
    month = StaffMonth(
        staff_id=sid,
        name=name,
        office_short=None,
        visits=len(lines),
        days=1,
        per_day=float(len(lines)),
        patients=1,
        weekly_patients_avg=1.0,
        accompany_visits=0,
        provided_min_total=35,
        provided_samples=1,
        avg_stay_min=35.0,
        short_stay_count=0,
        no_departure_count=1,
        no_arrival_count=0,
        km_total=0.0,
        km_per_day=None,
        km_per_visit=None,
        skipped_legs=0,
        weeks=[],
    )
    return MonthStaffReport(
        month=MONTH,
        date_from=date(2026, 10, 1),
        date_to=date(2026, 10, 31),
        weeks=[],
        staff=[month],
        visits=lines,
    )


def _vline(name: str, **kw) -> VisitLine:
    base = {
        "visit_id": uuid.uuid4(),
        "day": date(2026, 10, 2),
        "staff_id": uuid.uuid4(),
        "staff_name": name,
        "patient": "青木 さくら",
        "plan": "09:00–10:00",
        "arrival": "09:00",
        "departure": None,
        "stay_min": None,
        "provided_min": 35,
        "rule": RULE_NO_DEPARTURE,
        "no_show": False,
    }
    base.update(kw)
    return VisitLine(**base)


def test_ask_is_one_row_per_visit() -> None:
    """要確認は 1 訪問 1 行。理由は「／」でつなぐ。"""
    html = render_month_staff_report_html(
        _report_of("佐藤 花子", [_vline("佐藤 花子", no_show=True)]),
        station_name=None,
        generated_at=datetime(2026, 11, 1, tzinfo=UTC),
    )
    assert "退出の記録がありません。到着＋35 分にしました。／未訪問の記録があります。" in html
    assert html.count("未訪問の記録があります。件数には数えています。") == 1
    assert "拠点なし" not in html


def test_staff_name_is_escaped() -> None:
    name = "<script>alert(1)</script> & 太郎"
    html = render_month_staff_report_html(
        _report_of(name, [_vline(name)]),
        station_name="A & B",
        generated_at=datetime(2026, 11, 1, tzinfo=UTC),
    )
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; 太郎" in html
    assert "A &amp; B" in html
