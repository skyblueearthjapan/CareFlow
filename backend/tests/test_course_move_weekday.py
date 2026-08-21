"""週空間 A2後段 — POST /api/v1/schedule/v2/course-move-weekday-week-only

正典: docs/plans/weekly-space-design.md §4-2 (コース丸ごと曜日スライド・今週のみ)。

検証観点:
  1. 移動: course.weekday + 配下 planned visits の visit_date / source='manual_week'
  2. planned 以外は動かない・PFV 不変
  3. 同 code コースが移動先に既存 → 422 / 青ピン配下 → 422 / 同曜日 no-op
  4. undo: op_log の move_course_weekday payload で元へ戻る
  5. RBAC: staff 403
"""

from __future__ import annotations

from datetime import date, time

import pytest
from sqlalchemy import func, select

from app.core.security import create_access_token, hash_password
from app.models import Patient, User, Visit
from app.models.course import Course
from app.models.office import Office
from app.models.patient_fixed_visit import PatientFixedVisit
from app.services.op_log_service import _execute_payload

_URL = "/api/v1/schedule/v2/course-move-weekday-week-only"

# 2026-09-04 = 金曜 = ISO 2026-W36 weekday4
_ISO_YEAR, _ISO_WEEK = 2026, 36


async def _make_user(db, *, email: str, role: str, staff_id=None) -> User:
    user = User(email=email, password_hash=hash_password("pw"), role=role, staff_id=staff_id)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_office(db, *, name: str) -> Office:
    o = Office(name=name)
    db.add(o)
    await db.commit()
    await db.refresh(o)
    return o


async def _make_patient(db, *, code: str) -> Patient:
    p = Patient(code=code, name=f"患者{code}", status="active", special_week_active=[])
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_course(db, *, office: Office, code: str = "A", weekday: int = 4) -> Course:
    c = Course(
        iso_year=_ISO_YEAR,
        iso_week=_ISO_WEEK,
        weekday=weekday,
        code=code,
        course_status="course_fixed",
        office_id=office.id,
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


async def _make_visit(
    db,
    *,
    patient: Patient,
    course: Course,
    status_value: str = "planned",
    start: time = time(10, 0),
    week_pinned: bool = False,
) -> Visit:
    v = Visit(
        patient_id=patient.id,
        course_id=course.id,
        visit_date=date.fromisocalendar(_ISO_YEAR, _ISO_WEEK, course.weekday + 1),
        start_time=start,
        end_time=time(start.hour + 1, start.minute),
        type="regular",
        status=status_value,
        source="auto",
        week_pinned=week_pinned,
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


@pytest.mark.asyncio
async def test_move_shifts_course_and_planned_visits(client, db) -> None:
    admin = await _make_user(db, email="cmw-1@example.com", role="admin")
    office = await _make_office(db, name="稲毛CMW1")
    course = await _make_course(db, office=office, weekday=4)
    p1 = await _make_patient(db, code="CMW-1a")
    p2 = await _make_patient(db, code="CMW-1b")
    v1 = await _make_visit(db, patient=p1, course=course)
    v_done = await _make_visit(db, patient=p2, course=course, status_value="completed")
    course_id, v1_id, v_done_id = course.id, v1.id, v_done.id
    # PFV は一切触れないことの検証用ベースライン
    pfv_before = await db.scalar(select(func.count()).select_from(PatientFixedVisit))

    res = await client.post(
        _URL,
        headers=_bearer(admin),
        json={"course_id": str(course_id), "to_weekday": 1},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["from_weekday"] == 4
    assert body["to_weekday"] == 1
    assert body["visits_moved"] == 1  # planned のみ

    db.expire_all()
    moved = await db.scalar(select(Visit).where(Visit.id == v1_id))
    assert moved.visit_date == date.fromisocalendar(_ISO_YEAR, _ISO_WEEK, 2)  # 火曜
    assert moved.source == "manual_week"
    untouched = await db.scalar(select(Visit).where(Visit.id == v_done_id))
    assert untouched.visit_date == date.fromisocalendar(_ISO_YEAR, _ISO_WEEK, 5)  # 金曜のまま
    c_after = await db.scalar(select(Course).where(Course.id == course_id))
    assert c_after.weekday == 1
    pfv_after = await db.scalar(select(func.count()).select_from(PatientFixedVisit))
    assert pfv_after == pfv_before


@pytest.mark.asyncio
async def test_move_same_weekday_is_noop(client, db) -> None:
    admin = await _make_user(db, email="cmw-2@example.com", role="admin")
    office = await _make_office(db, name="稲毛CMW2")
    course = await _make_course(db, office=office, weekday=2)

    res = await client.post(
        _URL,
        headers=_bearer(admin),
        json={"course_id": str(course.id), "to_weekday": 2},
    )
    assert res.status_code == 200, res.text
    assert res.json()["visits_moved"] == 0


@pytest.mark.asyncio
async def test_move_duplicate_code_at_target_returns_422(client, db) -> None:
    admin = await _make_user(db, email="cmw-3@example.com", role="admin")
    office = await _make_office(db, name="稲毛CMW3")
    course = await _make_course(db, office=office, code="A", weekday=4)
    await _make_course(db, office=office, code="A", weekday=1)  # 移動先に同code既存

    res = await client.post(
        _URL,
        headers=_bearer(admin),
        json={"course_id": str(course.id), "to_weekday": 1},
    )
    assert res.status_code == 422, res.text
    assert "同じコース" in res.text


@pytest.mark.asyncio
async def test_move_with_week_pinned_visit_returns_422(client, db) -> None:
    admin = await _make_user(db, email="cmw-4@example.com", role="admin")
    office = await _make_office(db, name="稲毛CMW4")
    course = await _make_course(db, office=office, weekday=4)
    p1 = await _make_patient(db, code="CMW-4a")
    await _make_visit(db, patient=p1, course=course, week_pinned=True)

    res = await client.post(
        _URL,
        headers=_bearer(admin),
        json={"course_id": str(course.id), "to_weekday": 0},
    )
    assert res.status_code == 422, res.text
    assert "青ピン" in res.text


@pytest.mark.asyncio
async def test_move_warns_on_patient_double_booking(client, db) -> None:
    admin = await _make_user(db, email="cmw-5@example.com", role="admin")
    office = await _make_office(db, name="稲毛CMW5")
    course = await _make_course(db, office=office, code="A", weekday=4)
    other = await _make_course(db, office=office, code="B", weekday=1)
    p1 = await _make_patient(db, code="CMW-5a")
    await _make_visit(db, patient=p1, course=course)
    await _make_visit(db, patient=p1, course=other, start=time(14, 0))  # 火曜に既存訪問

    res = await client.post(
        _URL,
        headers=_bearer(admin),
        json={"course_id": str(course.id), "to_weekday": 1},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["visits_moved"] == 1
    assert any("別の訪問" in w for w in body["warnings"])


@pytest.mark.asyncio
async def test_staff_role_returns_403(client, db) -> None:
    office = await _make_office(db, name="稲毛CMW6")
    course = await _make_course(db, office=office, weekday=4)
    sr = await _make_user(db, email="cmw-6@example.com", role="staff")

    res = await client.post(
        _URL,
        headers=_bearer(sr),
        json={"course_id": str(course.id), "to_weekday": 1},
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_oplog_move_course_weekday_restores(client, db) -> None:
    """undo 実体: move_course_weekday の inverse payload 実行で元の曜日へ戻る."""
    admin = await _make_user(db, email="cmw-7@example.com", role="admin")
    office = await _make_office(db, name="稲毛CMW7")
    course = await _make_course(db, office=office, weekday=4)
    p1 = await _make_patient(db, code="CMW-7a")
    visit = await _make_visit(db, patient=p1, course=course)
    course_id, visit_id = course.id, visit.id

    res = await client.post(
        _URL,
        headers=_bearer(admin),
        json={"course_id": str(course_id), "to_weekday": 0},
    )
    assert res.status_code == 200, res.text
    db.expire_all()

    await _execute_payload(
        db,
        {
            "op": "move_course_weekday",
            "course_id": str(course_id),
            "iso_year": _ISO_YEAR,
            "iso_week": _ISO_WEEK,
            "to_weekday": 4,
        },
    )
    await db.commit()
    restored_course = await db.scalar(select(Course).where(Course.id == course_id))
    assert restored_course.weekday == 4
    restored_visit = await db.scalar(select(Visit).where(Visit.id == visit_id))
    assert restored_visit.visit_date == date.fromisocalendar(_ISO_YEAR, _ISO_WEEK, 5)
