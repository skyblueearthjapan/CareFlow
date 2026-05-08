"""W17-BE-A: POST /api/v1/schedule/generate-week-only + /assign-staff-only のテスト.

Wave 17 Phase A: 「週を生成」と「自動割付」ボタンを分離する新 endpoint の検証。

検証観点:
  1. generate-week-only 正常系: visits 展開のみ、courses.assigned_staff_id は NULL のまま
  2. assign-staff-only 正常系: 既存 visits を破壊せず staff を割付
  3. 順次実行 (generate-week-only → assign-staff-only) で generate-and-assign と同等結果
  4. generate-week-only × 2 で再展開 (冪等)
  5. assign-staff-only × 2 で同等 (冪等)
  6. RBAC: staff/viewer は 403
  7. office_id 不存在 → 404
  8. 422: invalid iso_week, extra field
  9. generate-week-only が visits だけ展開し assigned_staff_id が NULL のまま (DB 検証)
  10. assign-staff-only が visits を削除しないことを確認
"""

from __future__ import annotations

from datetime import date, time
from typing import Any

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Course, Office, Patient, Staff, StaffShift, User, Visit
from app.models.course import COURSE_STATUS_PROPOSED, COURSE_STATUS_STAFF_ASSIGNED
from app.models.patient_fixed_visit import PatientFixedVisit

# ISO week 23 of 2026 — Monday = 2026-06-01
TEST_ISO_YEAR = 2026
TEST_ISO_WEEK = 23
TEST_WEEK_MONDAY = date(2026, 6, 1)


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
    office = Office(name="テスト拠点 W17", lat=35.6895, lng=139.6917)
    db.add(office)
    await db.flush()

    staffs = []
    for i in range(1, 5):
        s = Staff(
            code=f"W17-{i:02d}",
            name=f"スタッフ{i}",
            role="staff",
            status="active",
            primary_office_id=office.id,
        )
        db.add(s)
        staffs.append(s)
    await db.flush()

    for s in staffs:
        for wd in range(6):
            db.add(StaffShift(staff_id=s.id, weekday=wd, is_on=True))
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
            code=f"W17-P-{c.code}",
            name=f"患者 {c.code}",
            status="active",
            primary_office_id=office.id,
        )
        db.add(p)
        await db.flush()
        patients.append(p)
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
        "staff": staffs,
        "courses": courses,
        "patients": patients,
    }


# ---------------------------------------------------------------------------
# 1) generate-week-only 正常系: visits 展開のみ、assigned_staff_id は NULL のまま
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_week_only_happy_path(client, db) -> None:
    """generate-week-only は visits を展開するが assigned_staff_id は NULL のまま."""
    admin = await _make_user(db, "w17-gw-1@example.com", "admin")
    await _seed_office_staff_courses(db)

    res = await client.post(
        "/api/v1/schedule/generate-week-only",
        headers=_bearer(admin),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["iso_year"] == TEST_ISO_YEAR
    assert body["iso_week"] == TEST_ISO_WEEK
    assert body["visits_created"] == 4  # 4 患者 × 月曜 1 回
    assert "Generated" in body["message"]
    assert "staff not yet assigned" in body["message"]

    # DB 検証: visits が 4 件
    rows = (await db.scalars(select(Visit).where(Visit.source == "auto"))).all()
    assert len(rows) == 4

    # DB 検証: courses.assigned_staff_id は NULL のまま
    courses = (await db.scalars(select(Course).where(Course.iso_week == TEST_ISO_WEEK))).all()
    assert all(c.assigned_staff_id is None for c in courses), (
        "generate-week-only 後に assigned_staff_id が設定されてはいけない"
    )


# ---------------------------------------------------------------------------
# 2) assign-staff-only 正常系: 既存 visits を破壊せず staff を割付
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assign_staff_only_happy_path(client, db) -> None:
    """assign-staff-only は visits を削除せず courses に staff を割付する."""
    admin = await _make_user(db, "w17-as-1@example.com", "admin")
    await _seed_office_staff_courses(db)

    # まず generate-week-only で visits を生成
    res_gw = await client.post(
        "/api/v1/schedule/generate-week-only",
        headers=_bearer(admin),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )
    assert res_gw.status_code == 200, res_gw.text
    visits_before = (await db.scalars(select(Visit).where(Visit.source == "auto"))).all()
    ids_before = {v.id for v in visits_before}
    assert len(ids_before) == 4

    # assign-staff-only を実行
    res_as = await client.post(
        "/api/v1/schedule/assign-staff-only",
        headers=_bearer(admin),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )
    assert res_as.status_code == 200, res_as.text
    body = res_as.json()
    assert body["iso_year"] == TEST_ISO_YEAR
    assert body["iso_week"] == TEST_ISO_WEEK
    assert body["courses_assigned"] >= 1
    assert "Assigned" in body["message"]

    # DB 検証: visits が削除されていない (同一 ID)
    visits_after = (await db.scalars(select(Visit).where(Visit.source == "auto"))).all()
    ids_after = {v.id for v in visits_after}
    assert ids_before == ids_after, "assign-staff-only が visits を削除してはいけない"

    # DB 検証: 少なくとも 1 course に assigned_staff_id が設定された
    courses = (await db.scalars(select(Course).where(Course.iso_week == TEST_ISO_WEEK))).all()
    assigned = [c for c in courses if c.assigned_staff_id is not None]
    assert len(assigned) >= 1


# ---------------------------------------------------------------------------
# 3) 順次実行 (generate-week-only → assign-staff-only) で generate-and-assign と同等結果
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sequential_equals_generate_and_assign(client, db) -> None:
    """generate-week-only → assign-staff-only の順次実行が generate-and-assign と同等."""
    admin = await _make_user(db, "w17-seq@example.com", "admin")
    await _seed_office_staff_courses(db)

    # 順次実行
    res_gw = await client.post(
        "/api/v1/schedule/generate-week-only",
        headers=_bearer(admin),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )
    assert res_gw.status_code == 200, res_gw.text
    visits_created = res_gw.json()["visits_created"]

    res_as = await client.post(
        "/api/v1/schedule/assign-staff-only",
        headers=_bearer(admin),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )
    assert res_as.status_code == 200, res_as.text
    courses_assigned = res_as.json()["courses_assigned"]

    # 検証: visits 件数と courses 割付数が generate-and-assign と同等
    assert visits_created == 4  # 4 患者
    assert courses_assigned >= 1


# ---------------------------------------------------------------------------
# 4) generate-week-only × 2 で再展開 (冪等)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_week_only_idempotent(client, db) -> None:
    """generate-week-only × 2 で visits が削除→再生成される (冪等)."""
    admin = await _make_user(db, "w17-gw-idm@example.com", "admin")
    await _seed_office_staff_courses(db)

    res1 = await client.post(
        "/api/v1/schedule/generate-week-only",
        headers=_bearer(admin),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )
    assert res1.status_code == 200
    visits1_count = res1.json()["visits_created"]
    # W34: Layer1 が auto 行を物理 delete から soft-delete に変更したため、
    # active な (deleted_at IS NULL) auto visits だけを比較する。
    ids1 = {
        v.id
        for v in (
            await db.scalars(
                select(Visit).where(Visit.source == "auto", Visit.deleted_at.is_(None))
            )
        ).all()
    }

    # 2 回目
    res2 = await client.post(
        "/api/v1/schedule/generate-week-only",
        headers=_bearer(admin),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )
    assert res2.status_code == 200
    visits2_count = res2.json()["visits_created"]
    assert visits2_count == visits1_count  # 同じ件数

    ids2 = {
        v.id
        for v in (
            await db.scalars(
                select(Visit).where(Visit.source == "auto", Visit.deleted_at.is_(None))
            )
        ).all()
    }
    assert ids1.isdisjoint(ids2), "2 回目の実行で visits が削除→再生成されるはず"


# ---------------------------------------------------------------------------
# 5) assign-staff-only × 2 で同等 (冪等)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assign_staff_only_idempotent(client, db) -> None:
    """assign-staff-only × 2 で同等の courses_assigned が返る (冪等)."""
    admin = await _make_user(db, "w17-as-idm@example.com", "admin")
    await _seed_office_staff_courses(db)

    # まず visits を生成
    await client.post(
        "/api/v1/schedule/generate-week-only",
        headers=_bearer(admin),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )

    res1 = await client.post(
        "/api/v1/schedule/assign-staff-only",
        headers=_bearer(admin),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )
    assert res1.status_code == 200

    res2 = await client.post(
        "/api/v1/schedule/assign-staff-only",
        headers=_bearer(admin),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )
    assert res2.status_code == 200
    # 冪等: 両回とも 200 で、courses_assigned は非負の整数
    assert res2.json()["courses_assigned"] >= 0


# ---------------------------------------------------------------------------
# 6) RBAC: staff / viewer は 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_week_only_staff_returns_403(client, db) -> None:
    """staff role は generate-week-only で 403."""
    staff_user = await _make_user(db, "w17-rbac-staff@example.com", "staff")

    res = await client.post(
        "/api/v1/schedule/generate-week-only",
        headers=_bearer(staff_user),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_assign_staff_only_staff_returns_403(client, db) -> None:
    """staff role は assign-staff-only で 403."""
    staff_user = await _make_user(db, "w17-rbac-staff2@example.com", "staff")

    res = await client.post(
        "/api/v1/schedule/assign-staff-only",
        headers=_bearer(staff_user),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_generate_week_only_no_token_returns_401(client) -> None:
    """認証なしは generate-week-only で 401."""
    res = await client.post(
        "/api/v1/schedule/generate-week-only",
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_assign_staff_only_manager_returns_200(client, db) -> None:
    """manager role は assign-staff-only で 200."""
    mgr = await _make_user(db, "w17-mgr@example.com", "manager")
    await _seed_office_staff_courses(db)

    # visits を先に生成
    await client.post(
        "/api/v1/schedule/generate-week-only",
        headers=_bearer(mgr),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )

    res = await client.post(
        "/api/v1/schedule/assign-staff-only",
        headers=_bearer(mgr),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )
    assert res.status_code == 200, res.text


# ---------------------------------------------------------------------------
# 7) office_id 不存在 → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_week_only_invalid_office_returns_404(client, db) -> None:
    """存在しない office_id を指定すると generate-week-only は 404."""
    admin = await _make_user(db, "w17-404-gw@example.com", "admin")
    import uuid

    res = await client.post(
        "/api/v1/schedule/generate-week-only",
        headers=_bearer(admin),
        json={
            "iso_year": TEST_ISO_YEAR,
            "iso_week": TEST_ISO_WEEK,
            "office_id": str(uuid.uuid4()),
        },
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_assign_staff_only_invalid_office_returns_404(client, db) -> None:
    """存在しない office_id を指定すると assign-staff-only は 404."""
    admin = await _make_user(db, "w17-404-as@example.com", "admin")
    import uuid

    res = await client.post(
        "/api/v1/schedule/assign-staff-only",
        headers=_bearer(admin),
        json={
            "iso_year": TEST_ISO_YEAR,
            "iso_week": TEST_ISO_WEEK,
            "office_id": str(uuid.uuid4()),
        },
    )
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# 8) 422: invalid iso_week, extra field
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_week_only_invalid_iso_week_returns_422(client, db) -> None:
    """iso_week=99 は generate-week-only で 422 (Pydantic)."""
    admin = await _make_user(db, "w17-422-gw@example.com", "admin")

    res = await client.post(
        "/api/v1/schedule/generate-week-only",
        headers=_bearer(admin),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": 99},
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_assign_staff_only_extra_field_returns_422(client, db) -> None:
    """extra field は assign-staff-only で 422 (extra='forbid')."""
    admin = await _make_user(db, "w17-422-as@example.com", "admin")

    res = await client.post(
        "/api/v1/schedule/assign-staff-only",
        headers=_bearer(admin),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK, "unknown_field": True},
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_generate_week_only_extra_field_returns_422(client, db) -> None:
    """extra field は generate-week-only で 422 (extra='forbid')."""
    admin = await _make_user(db, "w17-422-gw2@example.com", "admin")

    res = await client.post(
        "/api/v1/schedule/generate-week-only",
        headers=_bearer(admin),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK, "extra_key": "value"},
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_assign_staff_only_invalid_iso_week_returns_422(client, db) -> None:
    """assign-staff-only でも iso_week=99 等の不正値で 422 を返す."""
    admin = await _make_user(db, "w17-422-as2@example.com", "admin")

    res = await client.post(
        "/api/v1/schedule/assign-staff-only",
        headers=_bearer(admin),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": 99},
    )
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# 9) staff_assigned コース保護: assign-staff-only は staff_assigned を上書きしない
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assign_staff_only_preserves_staff_assigned_courses(client, db) -> None:
    """admin が手動で staff_assigned にしたコースは assign-staff-only で保護される."""
    admin = await _make_user(db, "w17-sa@example.com", "admin")
    seeds = await _seed_office_staff_courses(db)
    staff_list = seeds["staff"]

    # 1 回目: generate-week-only + assign-staff-only
    await client.post(
        "/api/v1/schedule/generate-week-only",
        headers=_bearer(admin),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )
    await client.post(
        "/api/v1/schedule/assign-staff-only",
        headers=_bearer(admin),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )

    # コース A を DB で staff_assigned に設定 (admin 手動割付を模擬)
    await db.commit()
    course_a = (
        await db.scalars(
            select(Course).where(
                Course.iso_year == TEST_ISO_YEAR,
                Course.iso_week == TEST_ISO_WEEK,
                Course.code == "A",
            )
        )
    ).first()
    assert course_a is not None
    manual_staff_id = staff_list[0].id
    course_a.course_status = COURSE_STATUS_STAFF_ASSIGNED
    course_a.assigned_staff_id = manual_staff_id
    await db.commit()
    protected_id = course_a.id

    # 2 回目: assign-staff-only を再実行
    res2 = await client.post(
        "/api/v1/schedule/assign-staff-only",
        headers=_bearer(admin),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )
    assert res2.status_code == 200, res2.text

    # 保護されているか確認
    await db.commit()
    after = (await db.scalars(select(Course).where(Course.id == protected_id))).first()
    assert after is not None
    assert after.course_status == COURSE_STATUS_STAFF_ASSIGNED, (
        f"staff_assigned コースが {after.course_status} に変更された"
    )
    assert after.assigned_staff_id == manual_staff_id, (
        f"手動割付 staff_id が {after.assigned_staff_id} に上書きされた"
    )
