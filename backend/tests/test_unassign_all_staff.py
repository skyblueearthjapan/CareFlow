"""Phase G-17: /api/v1/schedule/v2/unassign-all-staff のテスト.

仕様:
  - 表示週の courses.assigned_staff_id を NULL に
  - 同週の visit_staff_assignments を物理 delete
  - course / visit 自体は残す
  - admin / manager のみ (staff は 403)
  - 別週は影響なし
  - 既に未割当 / 0 件でも 200 成功
"""

from __future__ import annotations

import uuid
from datetime import date, time

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Office, Patient, User
from app.models.audit_log import AuditLog
from app.models.course import Course
from app.models.staff import Staff
from app.models.visit import VISIT_STATUS_PLANNED, Visit
from app.models.visit_staff_assignment import VisitStaffAssignment

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db, *, email: str, role: str) -> User:
    user = User(
        email=email,
        password_hash=hash_password("does-not-matter"),
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _seed_office_with_staff(db, *, office_name: str) -> tuple[Office, Staff]:
    office = Office(name=office_name)
    db.add(office)
    await db.flush()
    s = Staff(
        name=f"{office_name}-staff",
        role="staff",
        is_trainee=False,
        primary_office_id=office.id,
    )
    db.add(s)
    await db.flush()
    await db.commit()
    return office, s


async def _seed_patient(db, *, office: Office, code: str) -> Patient:
    p = Patient(
        code=code,
        name=f"P-{code}",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _seed_course(
    db,
    *,
    office: Office,
    staff: Staff | None,
    iso_year: int,
    iso_week: int,
    weekday: int,
    code: str = "A",
) -> Course:
    c = Course(
        iso_year=iso_year,
        iso_week=iso_week,
        weekday=weekday,
        code=code,
        course_status="staff_assigned" if staff is not None else "proposed",
        assigned_staff_id=staff.id if staff is not None else None,
        office_id=office.id,
    )
    db.add(c)
    await db.flush()
    await db.commit()
    await db.refresh(c)
    return c


async def _seed_visit_with_assignment(
    db,
    *,
    patient: Patient,
    staff: Staff,
    visit_date: date,
    start: time = time(10, 0),
    end: time = time(10, 30),
) -> Visit:
    v = Visit(
        patient_id=patient.id,
        visit_date=visit_date,
        start_time=start,
        end_time=end,
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto_alloc",
        required_staff_count=1,
    )
    db.add(v)
    await db.flush()
    db.add(VisitStaffAssignment(visit_id=v.id, staff_id=staff.id))
    await db.commit()
    await db.refresh(v)
    return v


# ---------------------------------------------------------------------------
# ケース 1: 通常実行 (admin token + 2 Course + 5 visit_staff_assignments → 全件解除)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unassign_all_staff_clears_courses_and_visit_assignments(client, db) -> None:
    """admin + 2 Course (担当あり) + 5 visit_staff_assignments → 全件解除されて 200 返却."""
    admin = await _make_user(db, email="g17-admin@example.com", role="admin")
    office, staff = await _seed_office_with_staff(db, office_name="g17-office-1")
    patient = await _seed_patient(db, office=office, code="G17-P1")

    # W21 = 2026-05-18 (Mon) - 2026-05-24 (Sun) (現在の今日 2026-05-22 を含む週).
    iso_year, iso_week = 2026, 21
    c1 = await _seed_course(
        db, office=office, staff=staff, iso_year=iso_year, iso_week=iso_week, weekday=0, code="A"
    )
    c2 = await _seed_course(
        db, office=office, staff=staff, iso_year=iso_year, iso_week=iso_week, weekday=1, code="B"
    )

    # 5 件の visit_staff_assignments を Mon-Fri に 1 件ずつ.
    week_monday = date.fromisocalendar(iso_year, iso_week, 1)
    visit_ids: list[uuid.UUID] = []
    for offset in range(5):
        v = await _seed_visit_with_assignment(
            db,
            patient=patient,
            staff=staff,
            visit_date=date.fromordinal(week_monday.toordinal() + offset),
        )
        visit_ids.append(v.id)

    # endpoint が別 session で commit する前に、後で参照する ID を生で保持しておく.
    c1_id = c1.id
    c2_id = c2.id
    admin_id = admin.id

    res = await client.post(
        "/api/v1/schedule/v2/unassign-all-staff",
        headers=_bearer(admin),
        json={
            "iso_year": iso_year,
            "iso_week": iso_week,
            "office_ids": [str(office.id)],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["courses_unassigned"] == 2, f"expected 2 courses unassigned, got {body}"
    assert body["visit_assignments_removed"] == 5, (
        f"expected 5 visit_staff_assignments removed, got {body}"
    )

    # endpoint が別 session で commit 済みなので test 側 session の identity map を expire.
    db.expire_all()

    # course 自体は残っている + assigned_staff_id が NULL に.
    refreshed_c1 = await db.scalar(select(Course).where(Course.id == c1_id))
    refreshed_c2 = await db.scalar(select(Course).where(Course.id == c2_id))
    assert refreshed_c1 is not None and refreshed_c1.assigned_staff_id is None
    assert refreshed_c2 is not None and refreshed_c2.assigned_staff_id is None

    # visit 自体は残っている.
    remaining_visits = (await db.scalars(select(Visit).where(Visit.id.in_(visit_ids)))).all()
    assert len(remaining_visits) == 5

    # visit_staff_assignments は全削除.
    remaining_assignments = (
        await db.scalars(
            select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id.in_(visit_ids))
        )
    ).all()
    assert len(remaining_assignments) == 0

    # audit_logs に記録されている (action=schedule_unassign_all_staff の手書き 1 件).
    # 他 audit_logs middleware が同 request の HTTP audit row も書くため、target_id で絞る.
    audits = (
        await db.scalars(
            select(AuditLog).where(
                AuditLog.action == "schedule_unassign_all_staff",
                AuditLog.target_table == "courses",
            )
        )
    ).all()
    assert len(audits) == 1
    a = audits[0]
    assert a.actor_user_id == admin_id
    assert a.target_id == f"{iso_year}-W{iso_week}"
    assert a.after == {"courses_unassigned": 2, "visit_assignments_removed": 5}


# ---------------------------------------------------------------------------
# ケース 2: RBAC (staff role で 403)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unassign_all_staff_rejects_staff_role(client, db) -> None:
    """staff role は 403 で拒否される."""
    staff_user = await _make_user(db, email="g17-staff@example.com", role="staff")
    res = await client.post(
        "/api/v1/schedule/v2/unassign-all-staff",
        headers=_bearer(staff_user),
        json={"iso_year": 2026, "iso_week": 21, "office_ids": []},
    )
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# ケース 3: 別週は影響なし
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unassign_all_staff_does_not_affect_other_weeks(client, db) -> None:
    """W21 を unassign しても W22 の Course / VisitStaffAssignment はそのまま."""
    admin = await _make_user(db, email="g17-other-week-admin@example.com", role="admin")
    office, staff = await _seed_office_with_staff(db, office_name="g17-office-other")
    patient = await _seed_patient(db, office=office, code="G17-P2")

    # W21 (対象週)
    c_w21 = await _seed_course(
        db, office=office, staff=staff, iso_year=2026, iso_week=21, weekday=0, code="A"
    )
    w21_monday = date.fromisocalendar(2026, 21, 1)
    v_w21 = await _seed_visit_with_assignment(
        db, patient=patient, staff=staff, visit_date=w21_monday
    )

    # W22 (別週, 影響なし期待)
    c_w22 = await _seed_course(
        db, office=office, staff=staff, iso_year=2026, iso_week=22, weekday=0, code="A"
    )
    w22_monday = date.fromisocalendar(2026, 22, 1)
    v_w22 = await _seed_visit_with_assignment(
        db, patient=patient, staff=staff, visit_date=w22_monday
    )

    c_w21_id = c_w21.id
    c_w22_id = c_w22.id
    v_w21_id = v_w21.id
    v_w22_id = v_w22.id
    staff_id = staff.id
    office_id = office.id

    res = await client.post(
        "/api/v1/schedule/v2/unassign-all-staff",
        headers=_bearer(admin),
        json={"iso_year": 2026, "iso_week": 21, "office_ids": [str(office_id)]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["courses_unassigned"] == 1
    assert body["visit_assignments_removed"] == 1

    # endpoint が別 session で commit 済みなので test 側 session の identity map を expire.
    db.expire_all()

    # W21: 解除済み.
    refreshed_c_w21 = await db.scalar(select(Course).where(Course.id == c_w21_id))
    assert refreshed_c_w21 is not None and refreshed_c_w21.assigned_staff_id is None
    w21_assignments = (
        await db.scalars(
            select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == v_w21_id)
        )
    ).all()
    assert len(w21_assignments) == 0

    # W22: 影響なし (担当 + 割当そのまま).
    refreshed_c_w22 = await db.scalar(select(Course).where(Course.id == c_w22_id))
    assert refreshed_c_w22 is not None and refreshed_c_w22.assigned_staff_id == staff_id
    w22_assignments = (
        await db.scalars(
            select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == v_w22_id)
        )
    ).all()
    assert len(w22_assignments) == 1


# ---------------------------------------------------------------------------
# ケース 4: 既に未割当の Course / 0 件 visit assignments → 0 件返却で成功
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unassign_all_staff_returns_zero_when_nothing_to_unassign(client, db) -> None:
    """既に未割当 (assigned_staff_id NULL) の course / visit_staff_assignment 0 件でも 200 成功."""
    admin = await _make_user(db, email="g17-zero-admin@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db, office_name="g17-office-zero")
    # patient は seed するが visit + assignment は作らない.
    await _seed_patient(db, office=office, code="G17-P3")
    # course を 1 件だけ作るが assigned_staff_id は最初から NULL.
    c = await _seed_course(
        db, office=office, staff=None, iso_year=2026, iso_week=21, weekday=0, code="A"
    )
    assert c.assigned_staff_id is None

    res = await client.post(
        "/api/v1/schedule/v2/unassign-all-staff",
        headers=_bearer(admin),
        json={"iso_year": 2026, "iso_week": 21, "office_ids": [str(office.id)]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["courses_unassigned"] == 0
    assert body["visit_assignments_removed"] == 0
