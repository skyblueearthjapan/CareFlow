"""CRUD + RBAC tests for /api/v1/staff."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Staff, User


async def _make_user(db, email: str, role: str, staff_id=None) -> User:
    user = User(
        email=email,
        password_hash=hash_password("does-not-matter"),
        role=role,
        staff_id=staff_id,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_staff_list_admin_returns_200(client, db) -> None:
    admin = await _make_user(db, "s-admin@example.com", "admin")
    res = await client.get("/api/v1/staff", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert isinstance(res.json(), list)


@pytest.mark.asyncio
async def test_staff_get_unknown_returns_404(client, db) -> None:
    admin = await _make_user(db, "s-admin2@example.com", "admin")
    res = await client.get(f"/api/v1/staff/{uuid4()}", headers=_bearer(admin))
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_staff_delete_staff_role_returns_403(client, db) -> None:
    s = Staff(name="花子")
    db.add(s)
    await db.commit()
    await db.refresh(s)

    staff_user = await _make_user(db, "s-staff@example.com", "staff", staff_id=s.id)
    res = await client.delete(f"/api/v1/staff/{s.id}", headers=_bearer(staff_user))
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_staff_list_no_token_returns_401(client) -> None:
    res = await client.get("/api/v1/staff")
    assert res.status_code == 401, res.text


# ---------------------------------------------------------------------------
# スタッフコード自動採番 (services/staff_code.py・2026-08-20)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_staff_create_auto_assigns_code_when_blank(client, db) -> None:
    """コード空欄 (null) で登録すると S001 形式で自動採番される。"""
    admin = await _make_user(db, "s-auto1@example.com", "admin")
    res = await client.post(
        "/api/v1/staff",
        headers=_bearer(admin),
        json={"name": "採番 一号"},
    )
    assert res.status_code == 201, res.text
    assert res.json()["code"] == "S001"

    # 2人目は連番
    res = await client.post(
        "/api/v1/staff",
        headers=_bearer(admin),
        json={"name": "採番 二号", "code": ""},
    )
    assert res.status_code == 201, res.text
    assert res.json()["code"] == "S002"


@pytest.mark.asyncio
async def test_staff_create_respects_manual_code(client, db) -> None:
    """手入力コードはそのまま尊重される (自動採番は空欄のときだけ)。"""
    admin = await _make_user(db, "s-auto2@example.com", "admin")
    res = await client.post(
        "/api/v1/staff",
        headers=_bearer(admin),
        json={"name": "手入力 花子", "code": "S-TMP-1"},
    )
    assert res.status_code == 201, res.text
    assert res.json()["code"] == "S-TMP-1"


@pytest.mark.asyncio
async def test_staff_code_autonumber_skips_deleted_and_gaps(client, db) -> None:
    """採番は soft-delete 済みの最大番号も跨いで進む (退職者コードの再利用事故を防ぐ)。"""
    from datetime import UTC, datetime

    db.add(Staff(name="退職 済子", code="S010", deleted_at=datetime.now(UTC)))
    db.add(Staff(name="在籍 太郎", code="S003"))
    await db.commit()

    admin = await _make_user(db, "s-auto3@example.com", "admin")
    res = await client.post(
        "/api/v1/staff",
        headers=_bearer(admin),
        json={"name": "採番 十一号"},
    )
    assert res.status_code == 201, res.text
    assert res.json()["code"] == "S011"
