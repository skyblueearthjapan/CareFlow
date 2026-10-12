"""GET /api/v1/agent/staff-week (エージェント用・職員ごとの 1 週・read-only)."""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Staff, User
from app.models.accompaniment import Accompaniment
from app.models.course import Course
from app.models.office import Office
from app.models.patient import Patient
from app.models.staff import StaffEvent, StaffShift, StaffWeeklyOverride
from app.models.visit import Visit
from app.models.visit_staff_assignment import VisitStaffAssignment

ISO_YEAR, ISO_WEEK = 2026, 42
MON = date(2026, 10, 12)
URL = f"/api/v1/agent/staff-week?iso_year={ISO_YEAR}&iso_week={ISO_WEEK}"


async def _user(db, role: str) -> User:
    u = User(email=f"{role}@ex.com", password_hash=hash_password("x"), role=role)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _bearer(u: User) -> dict[str, str]:
    return {
        "Authorization": "Bearer "
        + create_access_token(subject=u.id, role=u.role, staff_id=u.staff_id)
    }


def _visit(patient, d: date, h: int, m: int = 30, **kw) -> Visit:
    return Visit(
        patient_id=patient.id,
        visit_date=d,
        start_time=time(h, 0),
        end_time=time(h, m),
        type="regular",
        status=kw.pop("status", "planned"),
        source="auto",
        **kw,
    )


@pytest.mark.asyncio
async def test_staff_role_forbidden(client, db) -> None:
    u = await _user(db, "staff")
    res = await client.get(URL, headers=_bearer(u))
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_staff_week(client, db) -> None:
    office = Office(name="稲毛", code="INAGE")
    db.add(office)
    await db.flush()
    a = Staff(name="青木", code="S001")
    b = Staff(name="井上", code="S002")
    leave = Staff(name="休職", code="S090", status="on_leave")
    db.add_all([a, b, leave])
    await db.flush()
    p = Patient(code="P1", name="田中", status="active")
    db.add(p)
    await db.flush()
    course = Course(
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=0,
        code="M",
        course_status="staff_assigned",
        assigned_staff_id=a.id,
        office_id=office.id,
    )
    db.add(course)
    await db.flush()
    v1 = _visit(p, MON, 9, course_id=course.id)  # 主担当なし → コースの担当 a
    v2 = _visit(p, MON, 13, 45, primary_staff_id=a.id, secondary_staff_id=b.id)
    v_cancel = _visit(p, MON, 15, primary_staff_id=a.id, status="cancelled")
    v_del = _visit(p, MON, 16, primary_staff_id=a.id, deleted_at=datetime(2026, 10, 1))
    db.add_all([v1, v2, v_cancel, v_del])
    await db.flush()
    db.add_all(
        [
            VisitStaffAssignment(visit_id=v2.id, staff_id=a.id),
            VisitStaffAssignment(visit_id=v2.id, staff_id=b.id),
            StaffShift(staff_id=a.id, weekday=0, is_on=True, start_time=time(9), end_time=time(17)),
            StaffShift(staff_id=a.id, weekday=1, is_on=True, start_time=time(9), end_time=time(17)),
            StaffShift(staff_id=a.id, weekday=2, is_on=True, start_time=time(9), end_time=time(17)),
            StaffShift(staff_id=b.id, weekday=1, is_on=False),
            StaffWeeklyOverride(
                staff_id=a.id, iso_year=ISO_YEAR, iso_week=ISO_WEEK, weekday=1, override_type="off"
            ),
            StaffWeeklyOverride(
                staff_id=a.id,
                iso_year=ISO_YEAR,
                iso_week=ISO_WEEK,
                weekday=2,
                override_type="custom_time",
                start_time=time(10),
                end_time=time(15),
            ),
            StaffWeeklyOverride(
                staff_id=b.id,
                iso_year=ISO_YEAR,
                iso_week=ISO_WEEK,
                weekday=1,
                override_type="custom_time",
                start_time=time(13),
                end_time=time(16),
            ),
            StaffEvent(
                staff_id=a.id,
                event_type="event",
                title="朝会",
                starts_at=datetime(2026, 10, 12, 9, 0),
                ends_at=datetime(2026, 10, 12, 9, 15),
            ),
            StaffEvent(
                staff_id=a.id,
                event_type="training",
                title="研修 2 日",
                starts_at=datetime(2026, 10, 15, 9, 0),
                ends_at=datetime(2026, 10, 16, 17, 0),
            ),
            Accompaniment(
                accompanying_staff_id=b.id,
                target_type="course",
                course_id=course.id,
                kind="trainee",
            ),
        ]
    )
    await db.commit()

    u = await _user(db, "admin")
    res = await client.get(URL, headers=_bearer(u))
    assert res.status_code == 200, res.text
    body = res.json()
    assert [s["code"] for s in body["staff"]] == ["S001", "S002"]  # 休職は既定で出ない
    sa = body["staff"][0]
    sb = body["staff"][1]
    assert len(sa["days"]) == 7

    mon = sa["days"][0]
    assert mon["date"] == "2026-10-12"
    assert mon["is_on"] is True and mon["start_time"] == "09:00:00"
    assert mon["visits_count"] == 2  # v1 (コース担当) + v2。取消・削除は数えない
    assert mon["visit_minutes"] == 30 + 45
    assert mon["first_visit_start"] == "09:00:00" and mon["last_visit_end"] == "13:45:00"
    assert [e["title"] for e in mon["events"]] == ["朝会"]
    assert sa["week_visits_count"] == 2

    tue = sa["days"][1]
    assert tue["is_on"] is False and tue["override"]["override_type"] == "off"
    wed = sa["days"][2]
    assert (wed["is_on"], wed["start_time"], wed["end_time"]) == (True, "10:00:00", "15:00:00")
    # 日をまたぐ予定は重なる日すべてに載る
    assert [e["title"] for e in sa["days"][3]["events"]] == ["研修 2 日"]
    assert [e["title"] for e in sa["days"][4]["events"]] == ["研修 2 日"]
    assert sa["days"][5]["events"] == []

    # b: 2 人目 1 件・同行 1 件・時間変更で火曜は出勤
    assert sb["days"][0]["visits_count"] == 0
    assert sb["days"][0]["second_staff_count"] == 1
    assert sb["days"][0]["accompaniment_count"] == 1
    assert sb["days"][1]["is_on"] is True and sb["days"][1]["start_time"] == "13:00:00"
    assert sb["days"][3]["shift"] is None and sb["days"][3]["is_on"] is False

    res2 = await client.get(URL + "&include_inactive=true", headers=_bearer(u))
    assert [s["code"] for s in res2.json()["staff"]] == ["S001", "S002", "S090"]
