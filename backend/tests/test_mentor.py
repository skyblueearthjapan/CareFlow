"""Tests for /api/v1/staff/{id}/mentor (GET / PUT)."""

from __future__ import annotations

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


async def _make_staff(db, name: str) -> Staff:
    s = Staff(name=name)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_mentor_get_returns_null_when_unset(client, db) -> None:
    admin = await _make_user(db, "mt-admin1@example.com", "admin")
    s = await _make_staff(db, "メンティー")
    res = await client.get(
        f"/api/v1/staff/{s.id}/mentor", headers=_bearer(admin)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["staff_id"] == str(s.id)
    assert body["mentor_staff_id"] is None
    assert body["mentor_name"] is None


@pytest.mark.asyncio
async def test_mentor_put_sets_then_clears(client, db) -> None:
    admin = await _make_user(db, "mt-admin2@example.com", "admin")
    mentee = await _make_staff(db, "メンティー")
    mentor = await _make_staff(db, "メンター先生")

    set_res = await client.put(
        f"/api/v1/staff/{mentee.id}/mentor",
        headers=_bearer(admin),
        json={"mentor_staff_id": str(mentor.id)},
    )
    assert set_res.status_code == 200, set_res.text
    assert set_res.json()["mentor_staff_id"] == str(mentor.id)
    assert set_res.json()["mentor_name"] == "メンター先生"

    clear_res = await client.put(
        f"/api/v1/staff/{mentee.id}/mentor",
        headers=_bearer(admin),
        json={"mentor_staff_id": None},
    )
    assert clear_res.status_code == 200, clear_res.text
    assert clear_res.json()["mentor_staff_id"] is None


@pytest.mark.asyncio
async def test_mentor_put_self_mentor_returns_422(client, db) -> None:
    admin = await _make_user(db, "mt-admin3@example.com", "admin")
    s = await _make_staff(db, "自分")
    res = await client.put(
        f"/api/v1/staff/{s.id}/mentor",
        headers=_bearer(admin),
        json={"mentor_staff_id": str(s.id)},
    )
    assert res.status_code == 422, res.text
