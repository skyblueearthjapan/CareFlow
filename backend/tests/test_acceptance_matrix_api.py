"""Tests for GET /api/v1/acceptance-matrix (受け入れ枠マトリックス, P1).

拠点 × 曜日 × 時間帯の ○△× 自動算出 + 常設手動上書き (effective) を検証する
read-only endpoint。ローカル SQLite のみ (本番 DB 禁止)。
"""

from __future__ import annotations

from datetime import date, time

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Office, Patient, User
from app.models.acceptance_calendar import AcceptanceCalendar
from app.models.city import City
from app.models.course import COURSE_STATUS_PROPOSED, COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.office import OfficeCity
from app.models.staff import Staff
from app.models.visit import VISIT_STATUS_PLANNED, Visit

ISO_YEAR = 2026
ISO_WEEK = 20
WEEK_MONDAY = date.fromisocalendar(ISO_YEAR, ISO_WEEK, 1)  # Mon


async def _make_user(db, *, email: str, role: str) -> User:
    user = User(email=email, password_hash=hash_password("does-not-matter"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _seed_office_staff(db, *, name="稲毛拠点", code="INAGE") -> tuple[Office, Staff]:
    office = Office(name=name, code=code)
    db.add(office)
    await db.flush()
    staff = Staff(name="担当看護師", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(staff)
    await db.flush()
    return office, staff


async def _seed_patient(db, *, office, code, lat=35.6, lng=140.1) -> Patient:
    p = Patient(
        code=code,
        name=f"P-{code}",
        status="active",
        lat=lat,
        lng=lng,
        primary_office_id=office.id,
    )
    db.add(p)
    await db.flush()
    return p


async def _seed_course(
    db, *, office, staff, weekday=0, code="A", status=COURSE_STATUS_STAFF_ASSIGNED
) -> Course:
    course = Course(
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=weekday,
        code=code,
        course_status=status,
        assigned_staff_id=staff.id if staff is not None else None,
        office_id=office.id,
    )
    db.add(course)
    await db.flush()
    return course


async def _seed_visit(db, *, patient, course, start: time, end: time) -> Visit:
    visit = Visit(
        patient_id=patient.id,
        visit_date=WEEK_MONDAY,
        start_time=start,
        end_time=end,
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=1,
        course_id=course.id,
        primary_staff_id=course.assigned_staff_id,
    )
    db.add(visit)
    await db.flush()
    return visit


def _url(**params) -> str:
    base = f"/api/v1/acceptance-matrix?iso_year={ISO_YEAR}&iso_week={ISO_WEEK}"
    for k, v in params.items():
        base += f"&{k}={v}"
    return base


def _monday(office_block: dict) -> dict:
    return next(d for d in office_block["days"] if d["weekday"] == 0)


def _cell(day: dict, hhmm: str) -> dict:
    return next(c for c in day["cells"] if c["time_slot"].startswith(hhmm))


@pytest.mark.asyncio
async def test_matrix_basic_structure(client, db) -> None:
    admin = await _make_user(db, email="am-admin1@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    course = await _seed_course(db, office=office, staff=staff)
    p = await _seed_patient(db, office=office, code="EX1")
    await _seed_visit(db, patient=p, course=course, start=time(10, 0), end=time(10, 30))
    await db.commit()

    res = await client.get(_url(office_id=office.id), headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["week_generated_any"] is True
    assert len(body["slots"]) == 8  # 10:00..17:00
    assert len(body["offices"]) == 1
    off = body["offices"][0]
    assert off["week_generated"] is True
    assert off["operating_weekdays"] == [0, 1, 2, 3, 4, 5]
    assert len(off["days"]) == 7
    mon = _monday(off)
    assert len(mon["cells"]) == 8
    # 11:00 は完全空き → ○、12:00 は昼休み → ×.
    assert _cell(mon, "11:00")["auto_status"] == "available"
    assert _cell(mon, "12:00")["auto_status"] == "unavailable"
    # source は auto.
    assert _cell(mon, "11:00")["source"] == "auto"


@pytest.mark.asyncio
async def test_effective_manual_override(client, db) -> None:
    admin = await _make_user(db, email="am-admin2@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    course = await _seed_course(db, office=office, staff=staff)
    p = await _seed_patient(db, office=office, code="EX1")
    await _seed_visit(db, patient=p, course=course, start=time(10, 0), end=time(10, 30))
    # 常設手動: Mon 11:00 を × に上書き.
    db.add(
        AcceptanceCalendar(
            office_id=office.id, weekday=0, time_slot=time(11, 0), status="unavailable"
        )
    )
    await db.commit()

    res = await client.get(_url(office_id=office.id), headers=_bearer(admin))
    body = res.json()
    cell = _cell(_monday(body["offices"][0]), "11:00")
    assert cell["auto_status"] == "available"
    assert cell["manual_status"] == "unavailable"
    assert cell["effective_status"] == "unavailable"
    assert cell["source"] == "manual_standing"


@pytest.mark.asyncio
async def test_closed_day_all_unavailable(client, db) -> None:
    admin = await _make_user(db, email="am-admin3@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    await _seed_course(db, office=office, staff=staff)
    await db.commit()

    res = await client.get(_url(office_id=office.id), headers=_bearer(admin))
    off = res.json()["offices"][0]
    sun = next(d for d in off["days"] if d["weekday"] == 6)  # 日曜は operating 外
    assert sun["office_closed"] is True
    assert all(c["auto_status"] == "unavailable" for c in sun["cells"])
    assert sun["cells"][0]["metrics"]["reasons"] == ["定休日"]


@pytest.mark.asyncio
async def test_week_not_generated(client, db) -> None:
    admin = await _make_user(db, email="am-admin4@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    # proposed のみ (確定コースなし) → 週未生成扱い.
    await _seed_course(db, office=office, staff=staff, status=COURSE_STATUS_PROPOSED)
    await db.commit()

    res = await client.get(_url(office_id=office.id), headers=_bearer(admin))
    body = res.json()
    assert body["week_generated_any"] is False
    off = body["offices"][0]
    assert off["week_generated"] is False
    mon = _monday(off)
    assert _cell(mon, "11:00")["auto_status"] == "unavailable"
    assert _cell(mon, "11:00")["metrics"]["reasons"] == ["週未生成"]


@pytest.mark.asyncio
async def test_city_names_included(client, db) -> None:
    admin = await _make_user(db, email="am-admin5@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    city = City(prefecture="千葉県", name="稲毛区")
    db.add(city)
    await db.flush()
    db.add(OfficeCity(office_id=office.id, city_id=city.id))
    await _seed_course(db, office=office, staff=staff)
    await db.commit()

    res = await client.get(_url(office_id=office.id), headers=_bearer(admin))
    off = res.json()["offices"][0]
    assert "稲毛区" in off["city_names"]


@pytest.mark.asyncio
async def test_all_offices_when_office_id_omitted(client, db) -> None:
    admin = await _make_user(db, email="am-admin6@example.com", role="admin")
    # 稲毛: 確定コース + visit あり / 都賀: 確定コースなし (週未生成).
    inage, inage_staff = await _seed_office_staff(db, name="稲毛拠点", code="INAGE")
    tsuga, _tsuga_staff = await _seed_office_staff(db, name="都賀拠点", code="TSUGA")
    course = await _seed_course(db, office=inage, staff=inage_staff)
    p = await _seed_patient(db, office=inage, code="EX1")
    await _seed_visit(db, patient=p, course=course, start=time(10, 0), end=time(10, 30))
    await db.commit()

    res = await client.get(_url(), headers=_bearer(admin))  # office_id 未指定
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["week_generated_any"] is True
    by_name = {o["office_name"]: o for o in body["offices"]}
    assert {"稲毛拠点", "都賀拠点"} <= set(by_name)
    # 拠点ごとに独立判定: 稲毛は生成済み (11:00 ○), 都賀は週未生成 (×).
    assert by_name["稲毛拠点"]["week_generated"] is True
    assert _cell(_monday(by_name["稲毛拠点"]), "11:00")["auto_status"] == "available"
    assert by_name["都賀拠点"]["week_generated"] is False
    assert _cell(_monday(by_name["都賀拠点"]), "11:00")["auto_status"] == "unavailable"


@pytest.mark.asyncio
async def test_requires_auth(client, db) -> None:
    office, _staff = await _seed_office_staff(db)
    await db.commit()
    res = await client.get(_url(office_id=office.id))
    assert res.status_code == 401
