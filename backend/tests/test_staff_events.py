"""Tests for /api/v1/staff/{id}/events (CRUD + RBAC)."""

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


async def _make_staff(db, name: str = "イベ太郎") -> Staff:
    s = Staff(name=name)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


def _payload(start="2026-05-10T09:00:00", end="2026-05-10T10:00:00") -> dict:
    return {
        "event_type": "研修",
        "starts_at": start,
        "ends_at": end,
        "title": "新人研修",
    }


@pytest.mark.asyncio
async def test_events_create_normalises_japanese_type(client, db) -> None:
    admin = await _make_user(db, "ev-admin1@example.com", "admin")
    staff = await _make_staff(db)
    res = await client.post(
        f"/api/v1/staff/{staff.id}/events",
        headers=_bearer(admin),
        json=_payload(),
    )
    assert res.status_code == 201, res.text
    assert res.json()["event_type"] == "training"


@pytest.mark.asyncio
async def test_events_create_rejects_inverted_range(client, db) -> None:
    admin = await _make_user(db, "ev-admin2@example.com", "admin")
    staff = await _make_staff(db)
    res = await client.post(
        f"/api/v1/staff/{staff.id}/events",
        headers=_bearer(admin),
        json=_payload(start="2026-05-10T10:00:00", end="2026-05-10T09:00:00"),
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_events_list_then_patch_then_delete(client, db) -> None:
    admin = await _make_user(db, "ev-admin3@example.com", "admin")
    staff = await _make_staff(db)
    c = await client.post(
        f"/api/v1/staff/{staff.id}/events",
        headers=_bearer(admin),
        json=_payload(),
    )
    assert c.status_code == 201
    eid = c.json()["id"]

    listed = await client.get(
        f"/api/v1/staff/{staff.id}/events", headers=_bearer(admin)
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    patched = await client.patch(
        f"/api/v1/staff/{staff.id}/events/{eid}",
        headers=_bearer(admin),
        json={"title": "改名済み"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["title"] == "改名済み"

    deleted = await client.delete(
        f"/api/v1/staff/{staff.id}/events/{eid}", headers=_bearer(admin)
    )
    assert deleted.status_code == 204


@pytest.mark.asyncio
async def test_events_post_staff_role_returns_403(client, db) -> None:
    staff = await _make_staff(db)
    sr_user = await _make_user(
        db, "ev-staff@example.com", "staff", staff_id=staff.id
    )
    res = await client.post(
        f"/api/v1/staff/{staff.id}/events",
        headers=_bearer(sr_user),
        json=_payload(),
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_events_get_other_staff_role_returns_404(client, db) -> None:
    s1 = await _make_staff(db, "本人")
    s2 = await _make_staff(db, "他人")
    sr_user = await _make_user(
        db, "ev-staff2@example.com", "staff", staff_id=s1.id
    )
    res = await client.get(
        f"/api/v1/staff/{s2.id}/events", headers=_bearer(sr_user)
    )
    assert res.status_code == 404, res.text
