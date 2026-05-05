"""W2-BE4: Course CRUD + UNIQUE 制約 + RBAC tests.

設計仕様書 v0.9 §4.5 / API 契約 §4 / 実装手順書 v0.2 §3 W2-BE4 に対応。
本チケットは CRUD のみ (generate / fix / assign-staff は Wave 4)。
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Course, User

# ---------------------------------------------------------------------------
# helpers
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


def _course_payload(**overrides) -> dict:
    """Minimal valid CourseV2Create payload."""
    base = {
        "iso_year": 2026,
        "iso_week": 20,
        "weekday": 0,  # Monday
        "code": "A",
        "course_status": "proposed",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. Happy path: CRUD ハッピーパス
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_courses_create_returns_201(client, db) -> None:
    admin = await _make_user(db, "c-create-admin@example.com", "admin")
    res = await client.post(
        "/api/v1/courses",
        headers=_bearer(admin),
        json=_course_payload(),
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["iso_year"] == 2026
    assert body["iso_week"] == 20
    assert body["weekday"] == 0
    assert body["code"] == "A"
    assert body["course_status"] == "proposed"
    assert body["course_fixed_at"] is None
    assert body["staff_assigned_at"] is None
    assert body["assigned_staff_id"] is None
    assert "id" in body


@pytest.mark.asyncio
async def test_courses_get_detail_returns_200(client, db) -> None:
    admin = await _make_user(db, "c-get-admin@example.com", "admin")
    create = await client.post(
        "/api/v1/courses",
        headers=_bearer(admin),
        json=_course_payload(weekday=2, code="B"),
    )
    assert create.status_code == 201, create.text
    cid = create.json()["id"]

    res = await client.get(f"/api/v1/courses/{cid}", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["id"] == cid
    assert body["code"] == "B"
    assert body["weekday"] == 2


@pytest.mark.asyncio
async def test_courses_get_unknown_returns_404(client, db) -> None:
    admin = await _make_user(db, "c-get-404-admin@example.com", "admin")
    res = await client.get(f"/api/v1/courses/{uuid4()}", headers=_bearer(admin))
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_courses_list_returns_array(client, db) -> None:
    admin = await _make_user(db, "c-list-admin@example.com", "admin")
    # 2 件作成
    for code in ("A", "B"):
        res = await client.post(
            "/api/v1/courses",
            headers=_bearer(admin),
            json=_course_payload(weekday=3, code=code),
        )
        assert res.status_code == 201, res.text

    res = await client.get("/api/v1/courses", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert isinstance(body, list)
    codes = {item["code"] for item in body if item["weekday"] == 3}
    assert {"A", "B"} <= codes


@pytest.mark.asyncio
async def test_courses_list_filters_by_year_week_weekday(client, db) -> None:
    admin = await _make_user(db, "c-list-f-admin@example.com", "admin")
    # 2 件: 別週
    await client.post(
        "/api/v1/courses",
        headers=_bearer(admin),
        json=_course_payload(iso_week=21, weekday=1, code="A"),
    )
    await client.post(
        "/api/v1/courses",
        headers=_bearer(admin),
        json=_course_payload(iso_week=22, weekday=1, code="A"),
    )

    res = await client.get(
        "/api/v1/courses?iso_year=2026&iso_week=22",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert all(item["iso_week"] == 22 for item in body)


@pytest.mark.asyncio
async def test_courses_patch_updates_status(client, db) -> None:
    admin = await _make_user(db, "c-patch-admin@example.com", "admin")
    create = await client.post(
        "/api/v1/courses",
        headers=_bearer(admin),
        json=_course_payload(weekday=4, code="C"),
    )
    assert create.status_code == 201, create.text
    cid = create.json()["id"]

    res = await client.patch(
        f"/api/v1/courses/{cid}",
        headers=_bearer(admin),
        json={"course_status": "course_fixed", "note": "確定"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["course_status"] == "course_fixed"
    assert body["note"] == "確定"


@pytest.mark.asyncio
async def test_courses_delete_admin_returns_204(client, db) -> None:
    admin = await _make_user(db, "c-del-admin@example.com", "admin")
    create = await client.post(
        "/api/v1/courses",
        headers=_bearer(admin),
        json=_course_payload(weekday=5, code="D"),
    )
    assert create.status_code == 201, create.text
    cid = create.json()["id"]

    res = await client.delete(f"/api/v1/courses/{cid}", headers=_bearer(admin))
    assert res.status_code == 204, res.text

    follow = await client.get(f"/api/v1/courses/{cid}", headers=_bearer(admin))
    assert follow.status_code == 404, follow.text


# ---------------------------------------------------------------------------
# 2. UNIQUE 制約 (iso_year, iso_week, weekday, code)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_courses_create_duplicate_returns_409(client, db) -> None:
    admin = await _make_user(db, "c-uniq-admin@example.com", "admin")
    payload = _course_payload(iso_year=2026, iso_week=30, weekday=0, code="A")
    first = await client.post("/api/v1/courses", headers=_bearer(admin), json=payload)
    assert first.status_code == 201, first.text

    duplicate = await client.post("/api/v1/courses", headers=_bearer(admin), json=payload)
    assert duplicate.status_code == 409, duplicate.text


@pytest.mark.asyncio
async def test_courses_create_different_weekday_same_code_ok(client, db) -> None:
    """同じ (year, week, code) でも weekday が違えば許可される."""
    admin = await _make_user(db, "c-uniq2-admin@example.com", "admin")
    a = await client.post(
        "/api/v1/courses",
        headers=_bearer(admin),
        json=_course_payload(iso_week=31, weekday=0, code="A"),
    )
    assert a.status_code == 201, a.text
    b = await client.post(
        "/api/v1/courses",
        headers=_bearer(admin),
        json=_course_payload(iso_week=31, weekday=1, code="A"),
    )
    assert b.status_code == 201, b.text


# ---------------------------------------------------------------------------
# 3. CHECK 制約 / バリデーション
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_courses_create_invalid_code_returns_422(client, db) -> None:
    """A/B/C/D/M 以外の code は 422 (Pydantic 側で弾かれる)."""
    admin = await _make_user(db, "c-bad-admin@example.com", "admin")
    res = await client.post(
        "/api/v1/courses",
        headers=_bearer(admin),
        json=_course_payload(code="X"),
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_courses_create_invalid_status_returns_422(client, db) -> None:
    admin = await _make_user(db, "c-bad2-admin@example.com", "admin")
    res = await client.post(
        "/api/v1/courses",
        headers=_bearer(admin),
        json=_course_payload(course_status="invalid_status"),
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_courses_create_weekday_out_of_range_returns_422(client, db) -> None:
    admin = await _make_user(db, "c-bad3-admin@example.com", "admin")
    res = await client.post(
        "/api/v1/courses",
        headers=_bearer(admin),
        json=_course_payload(weekday=7),
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 4. RBAC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_courses_list_no_token_returns_401(client) -> None:
    res = await client.get("/api/v1/courses")
    assert res.status_code == 401, res.text


@pytest.mark.asyncio
async def test_courses_create_staff_returns_403(client, db) -> None:
    staff = await _make_user(db, "c-staff@example.com", "staff")
    res = await client.post(
        "/api/v1/courses",
        headers=_bearer(staff),
        json=_course_payload(),
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_courses_list_staff_returns_403(client, db) -> None:
    staff = await _make_user(db, "c-list-staff@example.com", "staff")
    res = await client.get("/api/v1/courses", headers=_bearer(staff))
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_courses_delete_manager_returns_403(client, db) -> None:
    """DELETE は admin のみ. manager は 403."""
    admin = await _make_user(db, "c-d-admin@example.com", "admin")
    manager = await _make_user(db, "c-d-mgr@example.com", "manager")
    create = await client.post(
        "/api/v1/courses",
        headers=_bearer(admin),
        json=_course_payload(weekday=6, code="M"),
    )
    assert create.status_code == 201, create.text
    cid = create.json()["id"]

    res = await client.delete(f"/api/v1/courses/{cid}", headers=_bearer(manager))
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# 5. ORM round-trip (model 直接アクセス)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_course_orm_round_trip(db) -> None:
    """ORM 経由で Course を作成・取得できる (timestamps が埋まる)."""
    course = Course(iso_year=2026, iso_week=15, weekday=2, code="C")
    db.add(course)
    await db.commit()
    await db.refresh(course)

    assert course.id is not None
    assert course.course_status == "proposed"
    assert course.created_at is not None
    assert course.updated_at is not None
    assert course.deleted_at is None
