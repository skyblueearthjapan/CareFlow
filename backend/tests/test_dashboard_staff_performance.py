"""GET /api/v1/dashboard/staff-performance — スタッフ別の実績.

設計: docs/plans/dashboard-staff-performance-design-2026-09-30.md §3 / §5。

* 訪問の持ち主 = 主担当。空ならコース担当 (手動差し替えは除く)。
* 取消・削除は数えない。予定外の訪問は数える。
* 出勤日数 = 訪問のあった日数・1 日あたり = 件数 ÷ 出勤日数。
* 実績 = actuals.py (調整後)。到着・退出が揃った訪問が 5 件以上のときだけ平均を出す。
* 距離 = 拠点 → 訪問 → … → 拠点 の直線。座標の無い区間は飛ばして数を返す。
* 会議・研修など = 休み以外のイベントで、最初の訪問開始〜最後の訪問終了に重なる分。
* チーム平均 = 訪問のあった人の平均。
* staff ロールは 403。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Patient, Staff, User, Visit
from app.models.course import Course
from app.models.office import Office
from app.models.staff import StaffEvent
from app.models.visit_checkin import VisitCheckin
from app.models.visit_time_adjustment import VisitTimeAdjustment
from app.services.dashboard_staff_performance import is_leave_event, week_ranges
from app.utils.geo import haversine_km

MON = date(2026, 9, 7)  # 月曜
URL = "/api/v1/dashboard/staff-performance"

OFFICE_LL = (35.60, 140.10)
P1_LL = (35.62, 140.10)
P2_LL = (35.62, 140.13)


# ---------- helpers --------------------------------------------------------


async def _admin(db, email: str = "perf-admin@example.com") -> dict[str, str]:
    user = User(email=email, password_hash=hash_password("x"), role="admin")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _office(
    db, name: str = "テスト拠点", *, short: str | None = None, ll=OFFICE_LL
) -> Office:
    o = Office(
        name=name,
        short_label=short,
        lat=ll[0] if ll else None,
        lng=ll[1] if ll else None,
    )
    db.add(o)
    await db.commit()
    await db.refresh(o)
    return o


async def _staff(db, name: str, office: Office | None = None, **kw) -> Staff:
    s = Staff(name=name, primary_office_id=office.id if office else None, **kw)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _patient(db, code: str, ll=None) -> Patient:
    p = Patient(code=code, name=code, lat=ll[0] if ll else None, lng=ll[1] if ll else None)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _visit(
    db,
    patient: Patient,
    day: date,
    start: str,
    end: str,
    *,
    staff: Staff | None = None,
    status: str = "planned",
    **kw,
) -> Visit:
    sh, sm = map(int, start.split(":"))
    eh, em = map(int, end.split(":"))
    v = Visit(
        patient_id=patient.id,
        primary_staff_id=staff.id if staff else None,
        visit_date=day,
        start_time=time(sh, sm),
        end_time=time(eh, em),
        type="visit",
        status=status,
        **kw,
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


def _jst(day: date, hh: int, mm: int) -> datetime:
    """JST の壁時計 → UTC aware。"""
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=UTC) - timedelta(hours=9)


async def _checkin(db, visit: Visit, staff: Staff, kind: str, at: datetime) -> VisitCheckin:
    row = VisitCheckin(
        visit_id=visit.id,
        patient_id=visit.patient_id,
        staff_id=staff.id,
        kind=kind,
        scanned_at=at,
        match_status="match",
        checkin_source="qr",
        threshold_snapshot={"v": 1},
        created_at=at,
    )
    db.add(row)
    await db.commit()
    return row


async def _event(db, staff: Staff, day: date, start: str, end: str, title: str, **kw) -> None:
    sh, sm = map(int, start.split(":"))
    eh, em = map(int, end.split(":"))
    db.add(
        StaffEvent(
            staff_id=staff.id,
            event_type="event",
            starts_at=datetime.combine(day, time(sh, sm)),
            ends_at=datetime.combine(day, time(eh, em)),
            title=title,
            **kw,
        )
    )
    await db.commit()


async def _get(client, headers, date_from: date, date_to: date, **params):
    q = {"from": date_from.isoformat(), "to": date_to.isoformat(), **params}
    res = await client.get(URL, headers=headers, params=q)
    assert res.status_code == 200, res.text
    return res.json()


def _row(body, staff: Staff) -> dict:
    return next(r for r in body["staff"] if r["staff_id"] == str(staff.id))


# ---------- 持ち主・取消 ---------------------------------------------------


@pytest.mark.asyncio
async def test_owner_falls_back_to_course_staff_and_excludes_cancelled(client, db) -> None:
    office = await _office(db)
    a = await _staff(db, "Aさん", office)
    p = await _patient(db, "PF-1", P1_LL)
    course = Course(
        iso_year=2026, iso_week=37, weekday=0, code="A", office_id=office.id, assigned_staff_id=a.id
    )
    db.add(course)
    await db.commit()

    await _visit(db, p, MON, "09:00", "09:30", staff=a)  # 主担当
    await _visit(db, p, MON, "10:00", "10:30", course_id=course.id)  # 主担当なし → コース担当
    await _visit(db, p, MON, "11:00", "11:30", staff=a, is_unplanned=True)  # 予定外も数える
    await _visit(db, p, MON, "12:00", "12:30", staff=a, status="cancelled")  # 取消
    await _visit(db, p, MON, "13:00", "13:30", staff=a, deleted_at=datetime.now(UTC))  # 削除
    # 手動で担当を外した訪問はコース担当に戻さない。
    await _visit(db, p, MON, "14:00", "14:30", course_id=course.id, manual_staff_override=True)

    body = await _get(client, await _admin(db), MON, MON + timedelta(days=6))
    row = _row(body, a)
    assert row["period"]["visits"] == 3
    assert row["period"]["days"] == 1
    assert len(body["staff"]) == 1


# ---------- 出勤日数・1 日あたり ------------------------------------------


@pytest.mark.asyncio
async def test_attendance_days_and_per_day(client, db) -> None:
    office = await _office(db)
    a = await _staff(db, "Aさん", office)
    p1 = await _patient(db, "PD-1", P1_LL)
    p2 = await _patient(db, "PD-2", P2_LL)
    await _visit(db, p1, MON, "09:00", "09:30", staff=a)
    await _visit(db, p2, MON, "10:00", "11:00", staff=a)
    await _visit(db, p1, MON, "13:00", "13:30", staff=a)
    await _visit(db, p1, MON + timedelta(days=2), "09:00", "09:30", staff=a)

    body = await _get(client, await _admin(db), MON, MON + timedelta(days=6))
    m = _row(body, a)["period"]
    assert m["days"] == 2
    assert m["visits"] == 4
    assert m["patients"] == 2
    assert m["per_day"] == 2.0
    assert m["plan_min"] == pytest.approx((30 + 60 + 30 + 30) / 4, abs=0.05)


# ---------- 実績 (5 件以上) ----------------------------------------------


@pytest.mark.asyncio
async def test_actual_time_needs_five_complete_visits(client, db) -> None:
    office = await _office(db)
    a = await _staff(db, "Aさん", office)
    p = await _patient(db, "PA-1", P1_LL)
    visits = []
    for i in range(5):
        v = await _visit(db, p, MON, f"{9 + i:02d}:00", f"{9 + i:02d}:30", staff=a)
        visits.append(v)
    # 4 件は到着・退出が揃う (各 40 分)、1 件は到着だけ。
    for i, v in enumerate(visits[:4]):
        await _checkin(db, v, a, "arrival", _jst(MON, 9 + i, 0))
        await _checkin(db, v, a, "departure", _jst(MON, 9 + i, 40))
    await _checkin(db, visits[4], a, "arrival", _jst(MON, 13, 0))

    headers = await _admin(db)
    body = await _get(client, headers, MON, MON)
    m = _row(body, a)["period"]
    assert m["actual_samples"] == 4
    assert m["actual_min"] is None  # 4 件では出さない
    assert m["arrival_only"] == 1
    assert body["min_actual_samples"] == 5

    # 5 件目の退出を「合わせた」時刻で入れる (実績は調整後の時刻 = 13:20 → 20 分)。
    base = await _checkin(db, visits[4], a, "departure", _jst(MON, 13, 50))
    db.add(
        VisitTimeAdjustment(
            visit_id=visits[4].id,
            kind="departure",
            adjusted_at=_jst(MON, 13, 20),
            base_checkin_id=base.id,
            source="admin",
            created_at=_jst(MON, 14, 0),
        )
    )
    await db.commit()

    body = await _get(client, headers, MON, MON)
    m = _row(body, a)["period"]
    assert m["actual_samples"] == 5
    assert m["arrival_only"] == 0
    assert m["actual_min"] == pytest.approx((40 * 4 + 20) / 5, abs=0.05)
    assert m["qr_ratio"] == 1.0


# ---------- 距離 (座標の無い区間) ----------------------------------------


@pytest.mark.asyncio
async def test_distance_skips_legs_without_coordinates(client, db) -> None:
    office = await _office(db)
    a = await _staff(db, "Aさん", office)
    p1 = await _patient(db, "PK-1", P1_LL)
    p_none = await _patient(db, "PK-2", None)
    p2 = await _patient(db, "PK-3", P2_LL)
    await _visit(db, p1, MON, "09:00", "09:30", staff=a)
    await _visit(db, p2, MON, "10:00", "10:30", staff=a)
    await _visit(db, p_none, MON, "11:00", "11:30", staff=a)

    # 拠点の座標が無い人: 行き帰りの 2 区間を飛ばし、訪問間だけ数える。
    no_ll_office = await _office(db, "座標なし拠点", ll=None)
    b = await _staff(db, "Bさん", no_ll_office)
    await _visit(db, p1, MON, "09:00", "09:30", staff=b)
    await _visit(db, p2, MON, "10:00", "10:30", staff=b)

    body = await _get(client, await _admin(db), MON, MON)
    ma = _row(body, a)["period"]
    # 拠点→P1→P2 は入り、P2→(座標なし)→拠点 の 2 区間は飛ばす。
    expected = haversine_km(*OFFICE_LL, *P1_LL) + haversine_km(*P1_LL, *P2_LL)
    assert ma["km_total"] == pytest.approx(expected, abs=0.01)
    assert ma["skipped_legs"] == 2
    assert ma["km_per_visit"] == pytest.approx(expected / 3, abs=0.01)

    mb = _row(body, b)["period"]
    assert mb["km_total"] == pytest.approx(haversine_km(*P1_LL, *P2_LL), abs=0.01)
    assert mb["skipped_legs"] == 2


# ---------- 1 日の内訳 (会議・研修は合間から分ける) ------------------------


@pytest.mark.asyncio
async def test_meetings_are_separated_from_idle_time(client, db) -> None:
    office = await _office(db)
    a = await _staff(db, "Aさん", office)
    p = await _patient(db, "PM-1", P1_LL)
    # 同じ場所で 9:00-10:00 と 12:00-13:00 → 訪問の間 120 分・訪問間の移動 0。
    await _visit(db, p, MON, "09:00", "10:00", staff=a)
    await _visit(db, p, MON, "12:00", "13:00", staff=a)
    await _event(db, a, MON, "10:30", "11:30", "カンファ")  # 数える (60 分)
    await _event(db, a, MON, "11:00", "11:15", "打合せ")  # 重なりは 1 回だけ
    await _event(db, a, MON, "10:00", "12:00", "午前休")  # 休みは数えない
    await _event(db, a, MON, "08:00", "09:10", "朝会")  # 訪問開始より前の分は数えない (10 分)
    await _event(db, a, MON, "14:00", "15:00", "研修")  # 最後の訪問の後は数えない
    await _event(db, a, MON, "11:30", "11:50", "取消した会議", cancelled_at=datetime.now(UTC))

    body = await _get(client, await _admin(db), MON, MON)
    m = _row(body, a)["period"]
    assert m["meeting_min_total"] == 70
    assert m["meeting_min_per_day"] == 70.0
    assert m["idle_min_per_day"] == 50.0  # 120 − 0 − 70
    assert m["visit_min_per_day"] == 120.0
    speed = body["travel_speed_kmh"]
    assert speed == 20.0
    km = haversine_km(*OFFICE_LL, *P1_LL) * 2
    assert m["travel_min_per_day"] == pytest.approx(km / speed * 60, abs=0.1)


def test_leave_event_titles() -> None:
    assert is_leave_event("休み")
    assert is_leave_event("有休")
    assert is_leave_event("午後休")
    assert not is_leave_event("休憩")
    assert not is_leave_event("朝会")
    assert not is_leave_event(None)


# ---------- チーム平均・拠点・週 ------------------------------------------


@pytest.mark.asyncio
async def test_team_average_office_filter_and_weeks(client, db) -> None:
    o1 = await _office(db, "第一拠点", short="一")
    o2 = await _office(db, "第二拠点")
    a = await _staff(db, "Aさん", o1, role="manager", qualification="准看護師")
    b = await _staff(db, "Bさん", o2, is_trainee=True)
    await _staff(db, "訪問なし", o1)  # 訪問の無い人は平均に入れない
    p = await _patient(db, "PT-1", P1_LL)
    for h in (9, 10):
        await _visit(db, p, MON, f"{h:02d}:00", f"{h:02d}:30", staff=a)
    for h in (9, 10, 11, 13):
        await _visit(db, p, MON, f"{h:02d}:00", f"{h:02d}:30", staff=b)
    # 翌週 (期間の 2 週目) に A だけ 1 件。
    await _visit(db, p, MON + timedelta(days=7), "09:00", "09:30", staff=a)

    headers = await _admin(db)
    body = await _get(client, headers, MON + timedelta(days=2), MON + timedelta(days=8))
    # 期間の週 (月〜日) は両端を期間に合わせて切る。9/7 の週の月・火は期間外。
    assert body["weeks"] == [
        {"start": "2026-09-09", "end": "2026-09-13"},
        {"start": "2026-09-14", "end": "2026-09-15"},
    ]
    # 9/7 (月) の訪問は期間外。A の 9/14 の 1 件だけが入る。
    assert [(r["name"], r["period"]["visits"]) for r in body["staff"]] == [("Aさん", 1)]

    body = await _get(client, headers, MON, MON + timedelta(days=13))
    ra, rb = _row(body, a), _row(body, b)
    assert ra["office_short"] == "一"
    assert rb["office_short"] == "第"  # short_label が無ければ拠点名の 1 文字目
    assert ra["is_manager"] is True and ra["qualification"] == "准看護師"
    assert rb["is_trainee"] is True and rb["is_manager"] is False
    assert ra["period"]["per_day"] == 1.5  # 3 件 / 2 日
    assert rb["period"]["per_day"] == 4.0
    team = body["team"]["period"]
    assert team["staff_count"] == 2
    assert team["visits"] == 7
    assert team["per_day"] == pytest.approx((1.5 + 4.0) / 2, abs=0.01)
    assert team["days"] == 3
    # 週ごと: 1 週目は A 2 件・B 4 件、2 週目は A 1 件だけ。
    assert [w["visits"] for w in ra["weeks"]] == [2, 1]
    assert [w["visits"] for w in rb["weeks"]] == [4, 0]
    assert rb["weeks"][1]["per_day"] is None
    assert body["team"]["weeks"][0]["per_day"] == 3.0
    assert body["team"]["weeks"][1]["staff_count"] == 1
    assert {o["short_label"] for o in body["offices"]} == {"一", "第"}

    # 拠点で絞ると、その拠点の人だけ・平均もその人だけ。
    body = await _get(client, headers, MON, MON + timedelta(days=13), office_id=str(o1.id))
    assert [r["name"] for r in body["staff"]] == ["Aさん"]
    assert body["team"]["period"]["per_day"] == 1.5
    assert body["team"]["period"]["staff_count"] == 1


def test_week_ranges_are_monday_based() -> None:
    assert week_ranges(date(2026, 9, 1), date(2026, 9, 30))[0] == (
        date(2026, 9, 1),
        date(2026, 9, 6),
    )
    assert len(week_ranges(date(2026, 9, 1), date(2026, 9, 30))) == 5


# ---------- 権限・入力 ------------------------------------------------------


@pytest.mark.asyncio
async def test_staff_role_is_forbidden(client, db) -> None:
    me = await _staff(db, "本人")
    user = User(email="perf-staff@example.com", password_hash=hash_password("x"), role="staff")
    user.staff_id = me.id
    db.add(user)
    await db.commit()
    await db.refresh(user)
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    res = await client.get(
        URL,
        headers={"Authorization": f"Bearer {token}"},
        params={"from": MON.isoformat(), "to": MON.isoformat()},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_period_validation(client, db) -> None:
    headers = await _admin(db)
    res = await client.get(URL, headers=headers, params={"from": "2026-09-10", "to": "2026-09-01"})
    assert res.status_code == 422
    res = await client.get(URL, headers=headers, params={"from": "2026-01-01", "to": "2026-12-31"})
    assert res.status_code == 422
    # 期間を省くと今週。
    res = await client.get(URL, headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert date.fromisoformat(body["date_from"]).weekday() == 0
    assert body["team"]["period"]["visits"] == 0
    assert body["team"]["period"]["per_day"] is None
