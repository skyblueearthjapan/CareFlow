"""Tests for /api/v1/course-templates (W15-BE1).

検証観点:
  1. GET (admin) → 200 + 一覧 (label 昇順)
  2. GET (staff)  → 200 (read 可)
  3. POST (admin) → 201 + body
  4. POST 重複 (office_id, label) → 409
  5. POST office_id 不正 → 422
  6. POST staff → 403 (mutation 禁止)
  7. PATCH 部分更新 → 200
  8. PATCH staff → 403
  9. PATCH 存在しない id → 404
 10. DELETE (admin) → 204 + 一覧から消える (論理削除)
 11. DELETE (manager) → 403 (admin only)
 12. DELETE 存在しない id → 404
 13. capacity 範囲外 (>50) → 422
 14. label 空文字 → 422
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Office, User

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


async def _make_office(db, name: str = "事業所A") -> Office:
    office = Office(name=name)
    db.add(office)
    await db.commit()
    await db.refresh(office)
    return office


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


def _basic_payload(office_id, label: str = "A", **overrides) -> dict:
    base = {
        "office_id": str(office_id),
        "label": label,
        "capacity_mon": 6,
        "capacity_tue": 6,
        "capacity_wed": 0,
        "capacity_thu": 6,
        "capacity_fri": 6,
        "capacity_sat": 0,
        "capacity_sun": 0,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. GET (admin) → 200 + 一覧 (label 昇順)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_course_templates_admin_sorted(client, db) -> None:
    admin = await _make_user(db, "ct-list1@example.com", "admin")
    office = await _make_office(db, "稲毛")

    # B → A の順で作成して、ソートされて返ることを確認
    r1 = await client.post(
        "/api/v1/course-templates",
        headers=_bearer(admin),
        json=_basic_payload(office.id, label="B"),
    )
    assert r1.status_code == 201, r1.text
    r2 = await client.post(
        "/api/v1/course-templates",
        headers=_bearer(admin),
        json=_basic_payload(office.id, label="A"),
    )
    assert r2.status_code == 201, r2.text

    res = await client.get(
        f"/api/v1/course-templates?office_id={office.id}",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body) == 2
    assert [r["label"] for r in body] == ["A", "B"]


# ---------------------------------------------------------------------------
# 2. GET (staff) → 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_course_templates_staff_can_read(client, db) -> None:
    staff = await _make_user(db, "ct-list-staff@example.com", "staff")
    office = await _make_office(db)
    res = await client.get(
        f"/api/v1/course-templates?office_id={office.id}",
        headers=_bearer(staff),
    )
    assert res.status_code == 200, res.text
    assert res.json() == []


# ---------------------------------------------------------------------------
# 3. POST (admin) → 201
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_course_template_admin(client, db) -> None:
    admin = await _make_user(db, "ct-c1@example.com", "admin")
    office = await _make_office(db)

    res = await client.post(
        "/api/v1/course-templates",
        headers=_bearer(admin),
        json=_basic_payload(office.id, label="A", notes="メモ"),
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["label"] == "A"
    assert body["capacity_mon"] == 6
    assert body["notes"] == "メモ"
    assert body["office_id"] == str(office.id)
    assert body["deleted_at"] is None


# ---------------------------------------------------------------------------
# 4. POST 重複 → 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_course_template_duplicate_returns_409(client, db) -> None:
    admin = await _make_user(db, "ct-dup@example.com", "admin")
    office = await _make_office(db)

    r1 = await client.post(
        "/api/v1/course-templates",
        headers=_bearer(admin),
        json=_basic_payload(office.id, label="A"),
    )
    assert r1.status_code == 201, r1.text

    r2 = await client.post(
        "/api/v1/course-templates",
        headers=_bearer(admin),
        json=_basic_payload(office.id, label="A"),
    )
    assert r2.status_code == 409, r2.text


# ---------------------------------------------------------------------------
# 4b. W15-codex-fix (3): 論理削除後の同一 label 再作成が許可される
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recreate_course_template_after_soft_delete(client, db) -> None:
    """論理削除済みの (office_id, label) と同じラベルで再作成できる
    (partial UNIQUE INDEX が deleted_at IS NULL のみを対象にする)."""
    admin = await _make_user(db, "ct-recreate@example.com", "admin")
    office = await _make_office(db, "事業所recreate")

    # 1. 作成
    r1 = await client.post(
        "/api/v1/course-templates",
        headers=_bearer(admin),
        json=_basic_payload(office.id, label="A"),
    )
    assert r1.status_code == 201, r1.text
    tpl_id = r1.json()["id"]

    # 2. 論理削除
    r_del = await client.delete(
        f"/api/v1/course-templates/{tpl_id}",
        headers=_bearer(admin),
    )
    assert r_del.status_code == 204, r_del.text

    # 3. 同じ (office_id, label='A') で再作成 → 201 (partial UNIQUE 効果)
    r2 = await client.post(
        "/api/v1/course-templates",
        headers=_bearer(admin),
        json=_basic_payload(office.id, label="A"),
    )
    assert r2.status_code == 201, r2.text
    new_id = r2.json()["id"]
    assert new_id != tpl_id, "削除済みとは別 ID で新規作成される"


# ---------------------------------------------------------------------------
# 5. POST office_id 不正 → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_course_template_invalid_office_returns_422(client, db) -> None:
    admin = await _make_user(db, "ct-bad-office@example.com", "admin")
    res = await client.post(
        "/api/v1/course-templates",
        headers=_bearer(admin),
        json=_basic_payload(uuid4(), label="A"),
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 6. POST staff → 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_course_template_staff_forbidden(client, db) -> None:
    staff = await _make_user(db, "ct-staff@example.com", "staff")
    office = await _make_office(db)
    res = await client.post(
        "/api/v1/course-templates",
        headers=_bearer(staff),
        json=_basic_payload(office.id, label="A"),
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# 7. PATCH 部分更新 → 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_course_template_partial(client, db) -> None:
    admin = await _make_user(db, "ct-patch@example.com", "admin")
    office = await _make_office(db)
    create = await client.post(
        "/api/v1/course-templates",
        headers=_bearer(admin),
        json=_basic_payload(office.id, label="A", capacity_mon=4),
    )
    assert create.status_code == 201
    tpl_id = create.json()["id"]

    res = await client.patch(
        f"/api/v1/course-templates/{tpl_id}",
        headers=_bearer(admin),
        json={"capacity_mon": 8, "notes": "更新後"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["capacity_mon"] == 8
    assert body["notes"] == "更新後"
    # 他のフィールドは保持
    assert body["capacity_tue"] == 6


# ---------------------------------------------------------------------------
# 8. PATCH staff → 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_course_template_staff_forbidden(client, db) -> None:
    admin = await _make_user(db, "ct-patch-admin@example.com", "admin")
    staff = await _make_user(db, "ct-patch-staff@example.com", "staff")
    office = await _make_office(db)
    create = await client.post(
        "/api/v1/course-templates",
        headers=_bearer(admin),
        json=_basic_payload(office.id, label="A"),
    )
    tpl_id = create.json()["id"]

    res = await client.patch(
        f"/api/v1/course-templates/{tpl_id}",
        headers=_bearer(staff),
        json={"capacity_mon": 1},
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# 9. PATCH 存在しない id → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_course_template_not_found(client, db) -> None:
    admin = await _make_user(db, "ct-patch-nf@example.com", "admin")
    res = await client.patch(
        f"/api/v1/course-templates/{uuid4()}",
        headers=_bearer(admin),
        json={"capacity_mon": 1},
    )
    assert res.status_code == 404, res.text


# ---------------------------------------------------------------------------
# 10. DELETE (admin) → 204 + 一覧から消える
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_course_template_admin(client, db) -> None:
    admin = await _make_user(db, "ct-del@example.com", "admin")
    office = await _make_office(db)
    create = await client.post(
        "/api/v1/course-templates",
        headers=_bearer(admin),
        json=_basic_payload(office.id, label="A"),
    )
    tpl_id = create.json()["id"]

    res = await client.delete(
        f"/api/v1/course-templates/{tpl_id}",
        headers=_bearer(admin),
    )
    assert res.status_code == 204, res.text

    # 一覧に出てこない
    list_res = await client.get(
        f"/api/v1/course-templates?office_id={office.id}",
        headers=_bearer(admin),
    )
    assert list_res.status_code == 200
    assert list_res.json() == []


# ---------------------------------------------------------------------------
# 11. DELETE (manager) → 403 (admin only)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_course_template_manager_forbidden(client, db) -> None:
    admin = await _make_user(db, "ct-del-admin@example.com", "admin")
    manager = await _make_user(db, "ct-del-mgr@example.com", "manager")
    office = await _make_office(db)
    create = await client.post(
        "/api/v1/course-templates",
        headers=_bearer(admin),
        json=_basic_payload(office.id, label="A"),
    )
    tpl_id = create.json()["id"]

    res = await client.delete(
        f"/api/v1/course-templates/{tpl_id}",
        headers=_bearer(manager),
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# 12. DELETE 存在しない id → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_course_template_not_found(client, db) -> None:
    admin = await _make_user(db, "ct-del-nf@example.com", "admin")
    res = await client.delete(
        f"/api/v1/course-templates/{uuid4()}",
        headers=_bearer(admin),
    )
    assert res.status_code == 404, res.text


# ---------------------------------------------------------------------------
# 13. capacity 範囲外 (>50) → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_course_template_capacity_out_of_range_422(client, db) -> None:
    admin = await _make_user(db, "ct-range@example.com", "admin")
    office = await _make_office(db)
    res = await client.post(
        "/api/v1/course-templates",
        headers=_bearer(admin),
        json=_basic_payload(office.id, label="A", capacity_mon=51),
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 14. label 空文字 → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_course_template_empty_label_422(client, db) -> None:
    admin = await _make_user(db, "ct-empty-label@example.com", "admin")
    office = await _make_office(db)
    res = await client.post(
        "/api/v1/course-templates",
        headers=_bearer(admin),
        json=_basic_payload(office.id, label=""),
    )
    assert res.status_code == 422, res.text
