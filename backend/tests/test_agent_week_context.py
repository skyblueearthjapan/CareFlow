"""GET /api/v1/agent/week-context (エージェント用・read-only).

docs/tools/week-leveling/extract.py の _week_sql / _history_sql と同じ意味かを確かめる:
  - 訪問: 週内・deleted_at なし・取消を除く (取消は cancelled_visits へ)
  - 患者: 週の訪問 (取消を含む) ∪ 週のマーク の患者 (+ 保留プールの患者)
  - 職員: active かつ論理削除なし (休職は staff_inactive)
  - 予定: 取消を除き、壁時計 (UTC として保存) のまま日付・時刻を出す
  - 未配置の○: pool か「置いた訪問が消えた」placed。長さは固定枠→希望→30
  - 担当歴: 直前 28 日・主担当あり・取消/削除を除く
  - 権限: staff → 403
"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Staff, User
from app.models.accompaniment import Accompaniment
from app.models.course import Course
from app.models.office import Office
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.patient_ng_staff import PatientNgStaff
from app.models.patient_same_address_link import PatientSameAddressLink
from app.models.special_visit import SpecialVisitMark, SpecialVisitPeriod
from app.models.staff import StaffEvent, StaffShift, StaffWeeklyOverride
from app.models.visit import Visit

ISO_YEAR, ISO_WEEK = 2026, 42
MON = date(2026, 10, 12)
URL = f"/api/v1/agent/week-context?iso_year={ISO_YEAR}&iso_week={ISO_WEEK}"


async def _user(db, role: str, staff_id=None) -> User:
    u = User(
        email=f"{role}-{datetime.now().timestamp()}@ex.com",
        password_hash=hash_password("x"),
        role=role,
        staff_id=staff_id,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _bearer(u: User) -> dict[str, str]:
    return {
        "Authorization": "Bearer "
        + create_access_token(subject=u.id, role=u.role, staff_id=u.staff_id)
    }


def _visit(patient, d: date, h: int, **kw) -> Visit:
    return Visit(
        patient_id=patient.id,
        visit_date=d,
        start_time=time(h, 0),
        end_time=time(h, 30),
        type="regular",
        status=kw.pop("status", "planned"),
        source=kw.pop("source", "auto"),
        **kw,
    )


async def _seed(db) -> dict:
    office = Office(name="稲毛", code="INAGE", short_label="稲")
    db.add(office)
    await db.flush()
    s1 = Staff(name="青木", code="S001", role="staff", sex="female", primary_office_id=office.id)
    s2 = Staff(name="井上", code="S002", role="manager", is_trainee=False)
    s_leave = Staff(name="休職中", code="S090", status="on_leave")
    s_del = Staff(name="退職済", code="S099", deleted_at=datetime(2026, 1, 1))
    db.add_all([s1, s2, s_leave, s_del])
    await db.flush()

    p1 = Patient(
        code="P1",
        name="田中",
        status="active",
        sex_restriction="female",
        lat=35.6,
        lng=140.1,
        weekly_pattern={"frequency_per_week": 1, "service_minutes": 45},
    )
    p2 = Patient(code="P2", name="鈴木", status="active")  # 取消の訪問だけ
    p3 = Patient(  # 週に訪問なし・希望 2 回 → プール
        code="P3", name="佐藤", status="active", weekly_pattern={"frequency_per_week": 2}
    )
    p4 = Patient(  # 特別訪問週間の○ (固定枠なし → service_minutes)
        code="P4", name="高橋", status="active", weekly_pattern={"service_minutes": 40}
    )
    p5 = Patient(code="P5", name="伊藤", status="active")  # 希望未設定 → プール外
    db.add_all([p1, p2, p3, p4, p5])
    await db.flush()

    course = Course(
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=0,
        code="M",
        course_status="staff_assigned",
        assigned_staff_id=s1.id,
        office_id=office.id,
    )
    db.add(course)
    await db.flush()

    v_live = _visit(p1, MON, 9, course_id=course.id, week_pinned=True)  # 主担当なし→コース担当
    v_cancel = _visit(p2, MON + date.resolution, 10, status="cancelled")
    v_deleted = _visit(p1, MON, 11, deleted_at=datetime(2026, 10, 1))
    v_other_week = _visit(p1, date(2026, 10, 19), 9)
    # 担当歴 (直前 28 日)
    h1 = _visit(p1, date(2026, 10, 5), 9, primary_staff_id=s1.id)
    h2 = _visit(p1, date(2026, 9, 28), 9, primary_staff_id=s1.id)
    h3 = _visit(p1, date(2026, 9, 21), 9, primary_staff_id=s2.id)
    h_old = _visit(p1, date(2026, 9, 13), 9, primary_staff_id=s2.id)  # 29 日前 → 外
    h_cancel = _visit(p1, date(2026, 10, 6), 9, primary_staff_id=s2.id, status="cancelled")
    db.add_all([v_live, v_cancel, v_deleted, v_other_week, h1, h2, h3, h_old, h_cancel])
    await db.flush()

    db.add_all(
        [
            PatientFixedVisit(
                patient_id=p1.id,
                mode="normal",
                weekday=0,
                start_time=time(9, 0),
                duration_min=30,
                is_pinned=True,
                movability="locked",
            ),
            PatientNgStaff(patient_id=p1.id, staff_id=s2.id),
            PatientSameAddressLink(  # CHECK a < b
                patient_a_id=min(p1.id, p2.id),
                patient_b_id=max(p1.id, p2.id),
                pair_mode="required",
            ),
            StaffShift(
                staff_id=s1.id, weekday=0, is_on=True, start_time=time(9), end_time=time(17)
            ),
            StaffWeeklyOverride(
                staff_id=s2.id,
                iso_year=ISO_YEAR,
                iso_week=ISO_WEEK,
                weekday=2,
                override_type="off",
            ),
            StaffWeeklyOverride(  # 別の週 → 出ない
                staff_id=s2.id,
                iso_year=ISO_YEAR,
                iso_week=ISO_WEEK + 1,
                weekday=2,
                override_type="off",
            ),
            StaffEvent(
                staff_id=s1.id,
                event_type="event",
                title="朝会",
                starts_at=datetime(2026, 10, 13, 9, 0),
                ends_at=datetime(2026, 10, 13, 9, 15),
            ),
            StaffEvent(  # 取消 → 出ない
                staff_id=s1.id,
                event_type="event",
                title="取消",
                starts_at=datetime(2026, 10, 13, 13, 0),
                ends_at=datetime(2026, 10, 13, 14, 0),
                cancelled_at=datetime(2026, 10, 10),
            ),
            StaffEvent(  # 週の外 → 出ない
                staff_id=s1.id,
                event_type="event",
                title="翌週",
                starts_at=datetime(2026, 10, 19, 9, 0),
                ends_at=datetime(2026, 10, 19, 10, 0),
            ),
            Accompaniment(
                accompanying_staff_id=s2.id,
                target_type="course",
                course_id=course.id,
                kind="support",
            ),
        ]
    )
    period = SpecialVisitPeriod(
        patient_id=p4.id,
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 31),
        weekly_target=3,
    )
    db.add(period)
    await db.flush()
    gone = _visit(p4, MON, 15, deleted_at=datetime(2026, 10, 11))
    alive = _visit(p4, MON + 2 * date.resolution, 15)
    db.add_all([gone, alive])
    await db.flush()
    db.add_all(
        [
            SpecialVisitMark(
                period_id=period.id,
                patient_id=p4.id,
                iso_year=ISO_YEAR,
                iso_week=ISO_WEEK,
                weekday=1,
                kind="extra",
                status="pool",
            ),
            SpecialVisitMark(
                period_id=period.id,
                patient_id=p4.id,
                iso_year=ISO_YEAR,
                iso_week=ISO_WEEK,
                weekday=0,
                kind="extra",
                status="placed",
                placed_visit_id=gone.id,
            ),  # 訪問が消えた → 未配置扱い
            SpecialVisitMark(
                period_id=period.id,
                patient_id=p4.id,
                iso_year=ISO_YEAR,
                iso_week=ISO_WEEK,
                weekday=2,
                kind="extra",
                status="placed",
                placed_visit_id=alive.id,
            ),  # 置けている → 出ない
        ]
    )
    await db.commit()
    return {
        "s1": s1,
        "s2": s2,
        "p1": p1,
        "p2": p2,
        "p3": p3,
        "p4": p4,
        "p5": p5,
        "v_live": v_live,
        "v_cancel": v_cancel,
        "alive": alive,
        "course": course,
    }


@pytest.mark.asyncio
async def test_staff_role_forbidden(client, db) -> None:
    u = await _user(db, "staff")
    res = await client.get(URL, headers=_bearer(u))
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated(client) -> None:
    res = await client.get(URL)
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_invalid_week(client, db) -> None:
    u = await _user(db, "admin")
    res = await client.get(
        "/api/v1/agent/week-context?iso_year=2025&iso_week=53", headers=_bearer(u)
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_week_context_mirrors_extract(client, db) -> None:
    seed = await _seed(db)
    u = await _user(db, "admin")
    res = await client.get(URL, headers=_bearer(u))
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["week_start"] == "2026-10-12"
    assert body["week_end"] == "2026-10-18"

    # 訪問: 取消・削除・週外を除く (alive は p4 の特別訪問)
    ids = {v["id"] for v in body["visits"]}
    assert ids == {str(seed["v_live"].id), str(seed["alive"].id)}
    v = next(v for v in body["visits"] if v["id"] == str(seed["v_live"].id))
    assert v["start_time"] == "09:00:00"
    assert v["course_code"] == "M"
    assert v["course_office"] == "稲"
    assert v["week_pinned"] is True
    assert v["primary_staff_id"] is None
    assert v["course_staff_id"] == str(seed["s1"].id)
    assert v["effective_staff_id"] == str(seed["s1"].id)
    assert v["patient_name"] == "田中"
    assert [c["id"] for c in body["cancelled_visits"]] == [str(seed["v_cancel"].id)]

    # 患者: 訪問 (取消含む) ∪ マーク ∪ プール。p5 (希望未設定・訪問なし) は出ない
    pmap = {p["id"]: p for p in body["patients"]}
    assert str(seed["p2"].id) in pmap  # 取消の訪問だけでも出る (extract と同じ)
    assert str(seed["p4"].id) in pmap
    assert str(seed["p3"].id) in pmap and pmap[str(seed["p3"].id)]["in_pool"] is True
    assert pmap[str(seed["p3"].id)]["in_week"] is False
    assert str(seed["p5"].id) not in pmap
    assert pmap[str(seed["p1"].id)]["sex_restriction"] == "female"
    assert pmap[str(seed["p1"].id)]["lat"] == pytest.approx(35.6)

    # プール: p3 は希望 2・実 0。p1 は希望 1・実 1 で出ない
    pool = {p["patient_id"]: p for p in body["pool"]}
    assert pool[str(seed["p3"].id)]["shortage"] == 2
    assert str(seed["p1"].id) not in pool

    # 固定枠 (赤ピン・可動域)・同住所・NG
    fv = body["fixed_visits"]
    assert len(fv) == 1 and fv[0]["movability"] == "locked" and fv[0]["is_pinned"] is True
    assert body["same_address_links"][0]["pair_mode"] == "required"
    assert body["ng"] == [{"patient_id": str(seed["p1"].id), "staff_id": str(seed["s2"].id)}]

    # 職員: active のみ。休職は staff_inactive、論理削除は出ない
    assert [s["code"] for s in body["staff"]] == ["S001", "S002"]
    assert [s["code"] for s in body["staff_inactive"]] == ["S090"]
    assert body["shifts"][0]["start_time"] == "09:00:00"
    assert [(o["weekday"], o["override_type"]) for o in body["weekly_overrides"]] == [(2, "off")]
    assert body["weekly_overrides"][0]["date"] == "2026-10-14"

    # 予定: 取消・週外を除き、壁時計のまま
    assert len(body["events"]) == 1
    ev = body["events"][0]
    assert (ev["date"], ev["start"], ev["end"], ev["end_date"]) == (
        "2026-10-13",
        "09:00",
        "09:15",
        "2026-10-13",
    )

    # 未配置の○: pool (火) と 消えた placed (月)。長さ = service_minutes 40
    marks = sorted(body["special_marks"], key=lambda m: m["weekday"])
    assert [(m["weekday"], m["duration_min"]) for m in marks] == [(0, 40), (1, 40)]
    assert len(body["special_marks_all"]) == 3
    assert len(body["special_periods"]) == 1

    # コース・同行
    assert body["courses"][0]["date"] == "2026-10-12"
    assert body["accompaniments"][0]["course_id"] == str(seed["course"].id)

    # 担当歴: 直前 28 日 (9/14〜10/11)・取消除く → s1×2, s2×1
    assert body["history_from"] == "2026-09-14" and body["history_to"] == "2026-10-11"
    assert len(body["history"]) == 3
    counts = {(h["staff_id"], h["count"]) for h in body["history_counts"]}
    assert counts == {(str(seed["s1"].id), 2), (str(seed["s2"].id), 1)}
    top = body["history_counts"][0]
    assert top["staff_id"] == str(seed["s1"].id) and top["last_date"] == "2026-10-05"


@pytest.mark.asyncio
async def test_week_context_empty_week(client, db) -> None:
    u = await _user(db, "admin")
    res = await client.get(URL, headers=_bearer(u))
    assert res.status_code == 200
    body = res.json()
    assert body["visits"] == [] and body["patients"] == [] and body["history"] == []
    assert body["scheduling_settings"] is None
