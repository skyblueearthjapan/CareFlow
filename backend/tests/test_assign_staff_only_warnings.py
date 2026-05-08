"""W27 Phase A: assign-staff-only response warnings (event×visit 重複検出).

検証観点:
  1. event と既存 visit (assigned_staff) の重複 → warnings に含まれる
     (固定割当 / staff_assigned 保護で Layer 3 ハード除外を経由しないケース)
  2. event と visit の重複なし → warnings 空配列
  3. visits 0 件 → warnings 空配列
"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Course, Office, Patient, Staff, StaffShift, User, Visit
from app.models.course import COURSE_STATUS_PROPOSED, COURSE_STATUS_STAFF_ASSIGNED
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.staff import StaffEvent
from app.models.visit import VISIT_STATUS_PLANNED

W27_ISO_YEAR = 2026
W27_ISO_WEEK = 27
W27_WEEK_MONDAY = date(2026, 6, 29)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db, email: str, role: str = "admin") -> User:
    user = User(email=email, password_hash=hash_password("does-not-matter"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _seed_basic(db, *, with_event: bool, event_overlaps: bool):
    """1 拠点 + 1 staff + 1 コース (staff_assigned で固定済) + 1 visit を seed.

    既に staff_assigned 状態のコースは Layer 3 で保護されるため、
    assigned_staff が event と重なる場合は Layer 3 ハード除外を経由せず
    warnings 経路で検出される.
    """
    office = Office(name="W27 拠点 warn", lat=35.65, lng=140.0)
    db.add(office)
    await db.flush()

    staff = Staff(
        code="W27W-S1",
        name="W27W スタッフ",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    db.add(staff)
    await db.flush()
    db.add(StaffShift(staff_id=staff.id, weekday=0, is_on=True))
    await db.flush()

    # 月曜の patient + fixed_visit (Layer 1 用) — 今回は visits を直接 seed
    patient = Patient(
        code="W27W-P1", name="W27W 患者", status="active", primary_office_id=office.id
    )
    db.add(patient)
    await db.flush()

    # patient_fixed_visit を入れて Layer 1 が冪等に再展開できる土台を作る
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=0,
            start_time=time(9, 0),
            duration_min=60,
        )
    )
    await db.flush()

    # 既に staff_assigned 状態のコース (= Layer 3 保護対象 = warnings 経路で検出されるケース)
    course = Course(
        iso_year=W27_ISO_YEAR,
        iso_week=W27_ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=staff.id,
        office_id=office.id,
    )
    db.add(course)
    await db.flush()

    # 既存 visit (assigned 済) を直接 seed
    visit = Visit(
        patient_id=patient.id,
        visit_date=W27_WEEK_MONDAY,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=1,
        course_id=course.id,
        primary_staff_id=staff.id,
    )
    db.add(visit)

    if with_event:
        if event_overlaps:
            ev_start = datetime.combine(W27_WEEK_MONDAY, time(9, 0))
            ev_end = datetime.combine(W27_WEEK_MONDAY, time(10, 0))
        else:
            # 重ならない時間帯 (午後)
            ev_start = datetime.combine(W27_WEEK_MONDAY, time(14, 0))
            ev_end = datetime.combine(W27_WEEK_MONDAY, time(15, 0))
        db.add(
            StaffEvent(
                staff_id=staff.id,
                event_type="研修",
                starts_at=ev_start,
                ends_at=ev_end,
            )
        )
    await db.commit()
    return {"office": office, "staff": staff, "patient": patient, "course": course, "visit": visit}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_warnings_returned_when_event_overlaps_visit(client, db) -> None:
    """staff_assigned 保護コースで event と visit が重なる → warnings 1 件.

    Layer 3 のハード除外は course_fixed コースに対してしか効かないので、
    既に staff_assigned (admin 手動) のコースに対する重複は Layer 3 を
    通過し warnings 経路で報告される.
    """
    admin = await _make_user(db, "w27-warn-1@example.com")
    seeds = await _seed_basic(db, with_event=True, event_overlaps=True)

    res = await client.post(
        "/api/v1/schedule/assign-staff-only",
        headers=_bearer(admin),
        json={"iso_year": W27_ISO_YEAR, "iso_week": W27_ISO_WEEK},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "warnings" in body, "response に warnings フィールドが無い"
    warnings = body["warnings"]
    assert isinstance(warnings, list)
    assert len(warnings) == 1, f"重複 1 件想定だが {len(warnings)} 件: {warnings}"
    w = warnings[0]
    assert w["staff_id"] == str(seeds["staff"].id)
    assert w["staff_name"] == "W27W スタッフ"
    assert w["event_type"] == "研修"
    assert w["visit_id"] == str(seeds["visit"].id)
    assert w["visit_start_time"] == "09:00:00"
    assert "重複" in w["message"]


@pytest.mark.asyncio
async def test_warnings_empty_when_event_does_not_overlap(client, db) -> None:
    """event は存在するが visit と時間帯が重ならない → warnings 空."""
    admin = await _make_user(db, "w27-warn-2@example.com")
    await _seed_basic(db, with_event=True, event_overlaps=False)

    res = await client.post(
        "/api/v1/schedule/assign-staff-only",
        headers=_bearer(admin),
        json={"iso_year": W27_ISO_YEAR, "iso_week": W27_ISO_WEEK},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body.get("warnings") == [], (
        f"重複無しなのに warnings に値が入っている: {body['warnings']}"
    )


@pytest.mark.asyncio
async def test_warnings_empty_when_no_events(client, db) -> None:
    """events 0 件 → warnings 空 (regression)."""
    admin = await _make_user(db, "w27-warn-3@example.com")
    await _seed_basic(db, with_event=False, event_overlaps=False)

    res = await client.post(
        "/api/v1/schedule/assign-staff-only",
        headers=_bearer(admin),
        json={"iso_year": W27_ISO_YEAR, "iso_week": W27_ISO_WEEK},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body.get("warnings") == []


@pytest.mark.asyncio
async def test_warnings_field_present_when_no_visits(client, db) -> None:
    """visits 0 件のケースでも warnings は空配列で返る (= レスポンス schema 安定)."""
    admin = await _make_user(db, "w27-warn-4@example.com")
    # office と staff だけ seed (visits は無し)
    office = Office(name="W27 拠点 empty", lat=35.65, lng=140.0)
    db.add(office)
    await db.flush()

    staff = Staff(
        code="W27W-NV",
        name="visits無しスタッフ",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    db.add(staff)
    await db.flush()
    db.add(StaffShift(staff_id=staff.id, weekday=0, is_on=True))
    # proposed コースのみ (visits なし)
    db.add(
        Course(
            iso_year=W27_ISO_YEAR,
            iso_week=W27_ISO_WEEK,
            weekday=0,
            code="A",
            course_status=COURSE_STATUS_PROPOSED,
            office_id=office.id,
        )
    )
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/assign-staff-only",
        headers=_bearer(admin),
        json={"iso_year": W27_ISO_YEAR, "iso_week": W27_ISO_WEEK},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "warnings" in body
    assert body["warnings"] == []
