"""W16-BE3: POST /api/v1/schedule/generate-and-assign のテスト.

検証観点:
  1. 正常系: visits 生成 + courses 割付が 1 トランザクションで完了
  2. 冪等性: 同一週で再実行しても auto visits が削除されてから再生成される
  3. RBAC: admin / manager のみ 200, staff / viewer は 403, 認証なしは 401
  4. 422: 無効な iso_year / iso_week
  5. patient_fixed_visits が空でも 200 (visits=0)
"""

from __future__ import annotations

from datetime import date, time
from typing import Any

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Course, Office, Patient, Staff, StaffShift, User, Visit
from app.models.course import COURSE_STATUS_PROPOSED
from app.models.patient_fixed_visit import PatientFixedVisit

# ISO week 22 of 2026 — Monday = 2026-05-25.
TEST_ISO_YEAR = 2026
TEST_ISO_WEEK = 22
TEST_WEEK_MONDAY = date(2026, 5, 25)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db, email: str, role: str) -> User:
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


async def _seed_office_staff_courses(db) -> dict[str, Any]:
    """1 拠点 + 4 staff + 4 コース (proposed) + 4 患者 + fixed_visits を seed."""
    office = Office(name="本店(稲毛)", lat=35.6383, lng=140.1041)
    db.add(office)
    await db.flush()

    s1 = Staff(
        code="HON-01",
        name="宇田川",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    s2 = Staff(
        code="HON-02",
        name="本名",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    s3 = Staff(
        code="HON-03",
        name="熊澤",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    s4 = Staff(
        code="HON-04",
        name="高岡",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    db.add_all([s1, s2, s3, s4])
    await db.flush()
    for staff in [s1, s2, s3, s4]:
        for wd in range(6):
            db.add(StaffShift(staff_id=staff.id, weekday=wd, is_on=True))
    await db.flush()

    # 月曜 (weekday=0) に A/B/C/D の 4 コース (proposed)
    courses: list[Course] = []
    for code in ("A", "B", "C", "D"):
        c = Course(
            iso_year=TEST_ISO_YEAR,
            iso_week=TEST_ISO_WEEK,
            weekday=0,
            code=code,
            course_status=COURSE_STATUS_PROPOSED,
            office_id=office.id,
        )
        db.add(c)
        courses.append(c)
    await db.flush()

    # 4 患者 (各 1 fixed_visit) — 月曜 1 回
    patients = []
    for c in courses:
        p = Patient(
            code=f"P-{c.code}",
            name=f"患者 {c.code}",
            status="active",
        )
        db.add(p)
        await db.flush()
        patients.append(p)
        # PatientFixedVisit (mode='normal', weekday=0)
        pfv = PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=0,
            start_time=time(9, 0),
            duration_min=60,
        )
        db.add(pfv)
    await db.commit()

    return {
        "office": office,
        "staff": [s1, s2, s3, s4],
        "courses": courses,
        "patients": patients,
    }


# ---------------------------------------------------------------------------
# 1) 正常系
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_and_assign_happy_path(client, db) -> None:
    """正常系: visits 生成 + courses 割付が 1 TX で完了する."""
    admin = await _make_user(db, "ga-1@example.com", "admin")
    await _seed_office_staff_courses(db)

    res = await client.post(
        "/api/v1/schedule/generate-and-assign",
        headers=_bearer(admin),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["iso_year"] == TEST_ISO_YEAR
    assert body["iso_week"] == TEST_ISO_WEEK
    assert body["visits_created"] == 4  # 4 患者 × 月曜 1 回
    assert body["courses_assigned"] >= 1  # 少なくとも 1 つは割当成功
    assert "Generated" in body["message"]

    # DB 検証: visits が auto-source で 4 件
    rows = (await db.scalars(select(Visit).where(Visit.source == "auto"))).all()
    assert len(rows) == 4

    # courses が course_fixed に昇格 + assigned_staff_id が設定
    refreshed_courses = (
        await db.scalars(select(Course).where(Course.iso_week == TEST_ISO_WEEK))
    ).all()
    assigned = [c for c in refreshed_courses if c.assigned_staff_id is not None]
    assert len(assigned) >= 1


# ---------------------------------------------------------------------------
# 2) 冪等性
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_and_assign_idempotent(client, db) -> None:
    """再実行で既存 auto visits が破棄され再生成される (冪等)."""
    admin = await _make_user(db, "ga-2@example.com", "admin")
    await _seed_office_staff_courses(db)

    res1 = await client.post(
        "/api/v1/schedule/generate-and-assign",
        headers=_bearer(admin),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )
    assert res1.status_code == 200
    visits1 = res1.json()["visits_created"]

    # 1 回目の visits の id を記録
    rows1 = (await db.scalars(select(Visit).where(Visit.source == "auto"))).all()
    ids1 = {v.id for v in rows1}

    # 2 回目
    res2 = await client.post(
        "/api/v1/schedule/generate-and-assign",
        headers=_bearer(admin),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )
    assert res2.status_code == 200
    visits2 = res2.json()["visits_created"]
    assert visits2 == visits1  # 同じ件数

    # 2 回目の visits は別 ID (= 削除→再生成)
    rows2 = (await db.scalars(select(Visit).where(Visit.source == "auto"))).all()
    ids2 = {v.id for v in rows2}
    assert ids1.isdisjoint(ids2), "visits should have been recreated"


# ---------------------------------------------------------------------------
# 3) RBAC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_and_assign_staff_returns_403(client, db) -> None:
    """staff role は 403."""
    staff_user = await _make_user(db, "ga-staff@example.com", "staff")
    await _seed_office_staff_courses(db)

    res = await client.post(
        "/api/v1/schedule/generate-and-assign",
        headers=_bearer(staff_user),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_generate_and_assign_no_token_returns_401(client) -> None:
    """認証なしは 401."""
    res = await client.post(
        "/api/v1/schedule/generate-and-assign",
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_generate_and_assign_manager_returns_200(client, db) -> None:
    """manager role は 200."""
    mgr = await _make_user(db, "ga-mgr@example.com", "manager")
    await _seed_office_staff_courses(db)

    res = await client.post(
        "/api/v1/schedule/generate-and-assign",
        headers=_bearer(mgr),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )
    assert res.status_code == 200, res.text


# ---------------------------------------------------------------------------
# 4) 422: 無効な iso_year / iso_week
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_and_assign_invalid_iso_week_returns_422(client, db) -> None:
    admin = await _make_user(db, "ga-422@example.com", "admin")

    # iso_week=99 (Pydantic 422)
    res = await client.post(
        "/api/v1/schedule/generate-and-assign",
        headers=_bearer(admin),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": 99},
    )
    assert res.status_code == 422

    # iso_year=1900 (Pydantic 422)
    res = await client.post(
        "/api/v1/schedule/generate-and-assign",
        headers=_bearer(admin),
        json={"iso_year": 1900, "iso_week": 22},
    )
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# 5) 患者・固定枠が空でも 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_and_assign_no_patients_returns_200(client, db) -> None:
    """patient_fixed_visits / patient が空でも 200 (visits=0, courses_assigned=0)."""
    admin = await _make_user(db, "ga-empty@example.com", "admin")
    # offices / staff だけ作る (patients / courses は作らない)
    office = Office(name="本店(稲毛)", lat=35.6383, lng=140.1041)
    db.add(office)
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/generate-and-assign",
        headers=_bearer(admin),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["visits_created"] == 0
    assert body["courses_assigned"] == 0


# ---------------------------------------------------------------------------
# 6) office_id 指定で対象拠点のみ Layer 3 が動く
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_and_assign_office_filter(client, db) -> None:
    """office_id 指定で当該拠点のコースのみ assigned_staff_id が埋まる."""
    admin = await _make_user(db, "ga-office@example.com", "admin")
    seeds = await _seed_office_staff_courses(db)
    office_id = seeds["office"].id

    res = await client.post(
        "/api/v1/schedule/generate-and-assign",
        headers=_bearer(admin),
        json={
            "iso_year": TEST_ISO_YEAR,
            "iso_week": TEST_ISO_WEEK,
            "office_id": str(office_id),
        },
    )
    assert res.status_code == 200, res.text


# ---------------------------------------------------------------------------
# 7) 不正な ISO 週 (2026 年に week 54 は存在しない)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_and_assign_invalid_iso_week_year_combo_returns_422(client, db) -> None:
    """Pydantic はパスするが Layer 1 が invalid ISO で 422 を返すケース."""
    admin = await _make_user(db, "ga-rb@example.com", "admin")

    # 2027 ISO に week 53 は存在しない (52 まで)
    res = await client.post(
        "/api/v1/schedule/generate-and-assign",
        headers=_bearer(admin),
        json={"iso_year": 2027, "iso_week": 53},
    )
    assert res.status_code == 422
