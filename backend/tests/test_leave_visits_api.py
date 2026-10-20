"""休みのレビュー①: 訪問の API が「明示の担当なし」をコース担当名に読み替えない.

正典: docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md §3-2。
"""

from __future__ import annotations

from datetime import time
from uuid import uuid4

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Course, Office, Patient, Staff, User, Visit
from app.services.leave_calendar import iso_key
from app.services.staff_leave import today_jst


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _seed(db):
    office = Office(name="明示拠点")
    lead = Staff(name="コース 担当", status="active")
    reader = Staff(name="読む 人", status="active")
    patient = Patient(
        code=f"EX-{uuid4().hex[:6]}",
        name="明示患者",
        status="active",
        special_week_active=[],
        qr_token=f"tok-{uuid4().hex[:8]}",
    )
    db.add_all([office, lead, reader, patient])
    await db.flush()
    day = today_jst()
    y, w, wd = iso_key(day)
    course = Course(
        iso_year=y,
        iso_week=w,
        weekday=wd,
        code="A",
        office_id=office.id,
        course_status="staff_assigned",
        assigned_staff_id=lead.id,
    )
    db.add(course)
    await db.flush()
    plain = Visit(
        patient_id=patient.id,
        course_id=course.id,
        visit_date=day,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status="planned",
        source="auto",
    )
    explicit = Visit(
        patient_id=patient.id,
        course_id=course.id,
        visit_date=day,
        start_time=time(14, 0),
        end_time=time(15, 0),
        type="regular",
        status="planned",
        source="auto",
        manual_staff_override=True,
    )
    db.add_all([plain, explicit])
    admin = User(
        email=f"ex-{uuid4().hex[:6]}@example.com", password_hash=hash_password("pw"), role="admin"
    )
    staff_user = User(
        email=f"exs-{uuid4().hex[:6]}@example.com",
        password_hash=hash_password("pw"),
        role="staff",
        staff_id=reader.id,
    )
    db.add_all([admin, staff_user])
    await db.commit()
    return admin, staff_user, patient.qr_token, day, plain.id, explicit.id


@pytest.mark.asyncio
async def test_list_visits_exposes_manual_and_hides_course_name(client, db) -> None:
    admin, _su, _tok, day, plain_id, explicit_id = await _seed(db)
    res = await client.get(
        "/api/v1/visits",
        headers=_bearer(admin),
        params={"week_start": day.isoformat(), "week_end": day.isoformat()},
    )
    assert res.status_code == 200, res.text
    by_id = {v["id"]: v for v in res.json()}
    assert by_id[str(explicit_id)]["manual_staff_override"] is True
    assert by_id[str(explicit_id)]["staff_name"] is None
    assert by_id[str(plain_id)]["manual_staff_override"] is False
    assert by_id[str(plain_id)]["staff_name"] == "コース 担当"


@pytest.mark.asyncio
async def test_qr_candidates_hide_course_name_for_explicit_unassigned(client, db) -> None:
    _admin, staff_user, token, _day, plain_id, explicit_id = await _seed(db)
    res = await client.get(f"/api/v1/visits/resolve-qr/{token}", headers=_bearer(staff_user))
    assert res.status_code == 200, res.text
    names = {c["visit_id"]: c["planned_staff_name"] for c in res.json()["candidates"]}
    assert names[str(plain_id)] == "コース 担当"
    assert names[str(explicit_id)] is None
