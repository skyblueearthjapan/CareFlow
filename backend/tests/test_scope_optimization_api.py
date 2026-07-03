"""Tests for POST /api/v1/schedule/v2/scope-optimization/simulate (範囲最適化 W1 BE-2).

検証内容:
    - 改善余地のあるコースで 200 + steps (suggestion 契約 = improvement と同一形).
    - 0 手でも 200 + excluded_summary + before=after.
    - office 不在 404 / RBAC (staff 403) / scope の曜日範囲外 422.
    - read-only: simulate が PFV を変更しない.

ローカル SQLite のみ (本番 DB 禁止).
"""

from __future__ import annotations

import uuid
from datetime import date, time

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models.course import COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.office import Office
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.staff import Staff, StaffShift
from app.models.user import User
from app.models.visit import VISIT_STATUS_PLANNED, Visit

ISO_YEAR = 2026
ISO_WEEK = 20
WEEK_MONDAY = date.fromisocalendar(ISO_YEAR, ISO_WEEK, 1)

BASE = (35.6000, 140.1000)
FAR = (35.6300, 140.1400)

_URL = "/api/v1/schedule/v2/scope-optimization/simulate"


async def _make_user(db, *, email: str, role: str) -> User:
    user = User(email=email, password_hash=hash_password("does-not-matter"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _seed_sandwich_office(db) -> tuple[Office, Patient]:
    """FAR — P(BASE, time_flexible) — FAR の course A (Mon) を持つ拠点を作る."""
    office = Office(name="稲", code="INAGE")
    db.add(office)
    await db.flush()
    staff = Staff(name="S1", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(staff)
    await db.flush()
    db.add(StaffShift(staff_id=staff.id, weekday=0, is_on=True))

    course = Course(
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=staff.id,
        office_id=office.id,
    )
    db.add(course)
    await db.flush()

    def _patient(code: str, lat: float, lng: float) -> Patient:
        return Patient(
            code=code,
            name=f"P-{code}",
            status="active",
            lat=lat,
            lng=lng,
            primary_office_id=office.id,
        )

    p = _patient("TGT", *BASE)
    fa1 = _patient("FA1", *FAR)
    fa2 = _patient("FA2", *FAR)
    db.add_all([p, fa1, fa2])
    await db.flush()

    def _visit(patient: Patient, start: time, end: time) -> Visit:
        return Visit(
            patient_id=patient.id,
            visit_date=WEEK_MONDAY,
            start_time=start,
            end_time=end,
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto",
            required_staff_count=1,
            course_id=course.id,
            primary_staff_id=staff.id,
        )

    db.add_all(
        [
            _visit(fa1, time(9, 30), time(10, 0)),
            _visit(p, time(10, 30), time(11, 0)),
            _visit(fa2, time(11, 15), time(11, 45)),
        ]
    )
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=0,
            slot_index=0,
            start_time=time(10, 30),
            duration_min=30,
            movability="time_flexible",
        )
    )
    await db.commit()
    return office, p


def _body(office: Office, **scope_overrides) -> dict:
    scope = {"office_id": str(office.id), "weekdays": [0], "course_codes": ["A"]}
    scope.update(scope_overrides)
    return {"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "scope": scope}


@pytest.mark.asyncio
async def test_simulate_returns_steps_with_improvement_contract(client, db) -> None:
    admin = await _make_user(db, email="so-admin1@example.com", role="admin")
    office, p = await _seed_sandwich_office(db)

    res = await client.post(_URL, headers=_bearer(admin), json=_body(office))
    assert res.status_code == 200, res.text
    data = res.json()

    assert data["iso_year"] == ISO_YEAR
    assert data["office_id"] == str(office.id)
    assert data["state_token"]
    assert data["steps"], data["excluded_summary"]

    step = data["steps"][0]
    assert step["seq"] == 1
    assert step["patient_id"] == str(p.id)
    assert step["patient_name"] == p.name
    # suggestion は improvement-suggestions と同一契約.
    sug = step["suggestion"]
    assert sug["kind"] == "time_change"
    assert sug["delta"]["travel_minutes_saved"] >= 10
    assert sug["current"]["start_time"] == "10:30"
    assert sug["requires_patient_confirmation"] is False
    assert step["cumulative_delta_minutes"] >= sug["delta"]["travel_minutes_saved"]
    # 前後メトリクス: 改善方向.
    assert data["after"]["travel_minutes"] < data["before"]["travel_minutes"]


@pytest.mark.asyncio
async def test_simulate_is_read_only(client, db) -> None:
    admin = await _make_user(db, email="so-admin2@example.com", role="admin")
    office, p = await _seed_sandwich_office(db)

    res = await client.post(_URL, headers=_bearer(admin), json=_body(office))
    assert res.status_code == 200, res.text

    pfv = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == p.id))
    ).one()
    assert pfv.weekday == 0
    assert pfv.start_time == time(10, 30)


@pytest.mark.asyncio
async def test_simulate_zero_steps_returns_200_with_summary(client, db) -> None:
    admin = await _make_user(db, email="so-admin3@example.com", role="admin")
    office = Office(name="稲", code="INAGE")
    db.add(office)
    await db.commit()

    res = await client.post(_URL, headers=_bearer(admin), json=_body(office))
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["steps"] == []
    assert data["before"] == data["after"]
    assert data["excluded_summary"]["truncated"] is False


@pytest.mark.asyncio
async def test_simulate_unknown_office_404(client, db) -> None:
    admin = await _make_user(db, email="so-admin4@example.com", role="admin")
    body = {
        "iso_year": ISO_YEAR,
        "iso_week": ISO_WEEK,
        "scope": {"office_id": str(uuid.uuid4())},
    }
    res = await client.post(_URL, headers=_bearer(admin), json=body)
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_simulate_staff_forbidden(client, db) -> None:
    staff_user = await _make_user(db, email="so-staff1@example.com", role="staff")
    office, _p = await _seed_sandwich_office(db)
    res = await client.post(_URL, headers=_bearer(staff_user), json=_body(office))
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_simulate_invalid_weekday_422(client, db) -> None:
    admin = await _make_user(db, email="so-admin5@example.com", role="admin")
    office, _p = await _seed_sandwich_office(db)
    res = await client.post(_URL, headers=_bearer(admin), json=_body(office, weekdays=[7]))
    assert res.status_code == 422
