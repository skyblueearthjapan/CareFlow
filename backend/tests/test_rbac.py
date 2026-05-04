"""RBAC matrix for /admin/users — verifies require_role() boundary."""

from __future__ import annotations

import pytest

from app.core.security import create_access_token, hash_password
from app.models import User


async def _make_user(db, email: str, role: str) -> User:
    user = User(
        email=email,
        password_hash=hash_password("does-not-matter-for-rbac-tests"),
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_admin_users_admin_returns_200(client, db) -> None:
    admin = await _make_user(db, "rbac-admin@example.com", "admin")
    res = await client.get("/api/v1/admin/users", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert res.json() == []


@pytest.mark.asyncio
async def test_admin_users_manager_returns_403(client, db) -> None:
    manager = await _make_user(db, "rbac-manager@example.com", "manager")
    res = await client.get("/api/v1/admin/users", headers=_bearer(manager))
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_admin_users_staff_returns_403(client, db) -> None:
    staff = await _make_user(db, "rbac-staff@example.com", "staff")
    res = await client.get("/api/v1/admin/users", headers=_bearer(staff))
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_admin_users_anonymous_returns_401(client) -> None:
    res = await client.get("/api/v1/admin/users")
    assert res.status_code == 401, res.text
