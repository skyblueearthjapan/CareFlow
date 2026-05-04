"""CRUD + RBAC tests for /api/v1/visits."""

from __future__ import annotations

from datetime import date, time
from uuid import uuid4

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Patient, User, Visit


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
async def test_visits_list_admin_returns_200(client, db) -> None:
    admin = await _make_user(db, "v-admin@example.com", "admin")
    res = await client.get("/api/v1/visits", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert isinstance(res.json(), list)


@pytest.mark.asyncio
async def test_visits_get_unknown_returns_404(client, db) -> None:
    admin = await _make_user(db, "v-admin2@example.com", "admin")
    res = await client.get(
        f"/api/v1/visits/{uuid4()}", headers=_bearer(admin)
    )
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_visits_delete_staff_returns_403(client, db) -> None:
    p = Patient(code="V001", name="患者")
    db.add(p)
    await db.commit()
    await db.refresh(p)

    visit = Visit(
        patient_id=p.id,
        visit_date=date(2026, 5, 1),
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="visit",
    )
    db.add(visit)
    await db.commit()
    await db.refresh(visit)

    staff_user = await _make_user(db, "v-staff@example.com", "staff")
    res = await client.delete(
        f"/api/v1/visits/{visit.id}", headers=_bearer(staff_user)
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_visits_list_no_token_returns_401(client) -> None:
    res = await client.get("/api/v1/visits")
    assert res.status_code == 401, res.text
