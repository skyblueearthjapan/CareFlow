"""CRUD + RBAC tests for /api/v1/patients."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Patient, User


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


@pytest.mark.asyncio
async def test_patients_list_admin_returns_200(client, db) -> None:
    admin = await _make_user(db, "p-admin@example.com", "admin")
    res = await client.get("/api/v1/patients", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert isinstance(res.json(), list)


@pytest.mark.asyncio
async def test_patients_get_unknown_returns_404(client, db) -> None:
    admin = await _make_user(db, "p-admin2@example.com", "admin")
    res = await client.get(f"/api/v1/patients/{uuid4()}", headers=_bearer(admin))
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_patients_delete_staff_returns_403(client, db) -> None:
    # Create a real patient via admin path so the row exists.
    admin = await _make_user(db, "p-admin3@example.com", "admin")  # noqa: F841
    p = Patient(code="P001", name="テスト")
    db.add(p)
    await db.commit()
    await db.refresh(p)

    staff_user = await _make_user(db, "p-staff@example.com", "staff")
    res = await client.delete(f"/api/v1/patients/{p.id}", headers=_bearer(staff_user))
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_patients_list_no_token_returns_401(client) -> None:
    res = await client.get("/api/v1/patients")
    assert res.status_code == 401, res.text
