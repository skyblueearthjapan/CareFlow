"""Tests for /api/v1/staff/{id}/shifts (GET / PUT)."""

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


async def _make_staff(db, name: str = "シフト太郎") -> Staff:
    s = Staff(name=name)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


def _full_payload() -> dict:
    return {
        "shifts": [
            {
                "weekday": wd,
                "is_on": wd != 6,
                "start_time": "09:00",
                "end_time": "17:00",
            }
            for wd in range(7)
        ]
    }


@pytest.mark.asyncio
async def test_shifts_get_returns_seven_rows_with_backfill(client, db) -> None:
    admin = await _make_user(db, "sh-admin1@example.com", "admin")
    staff = await _make_staff(db)
    res = await client.get(
        f"/api/v1/staff/{staff.id}/shifts", headers=_bearer(admin)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "shifts" in body
    assert len(body["shifts"]) == 7
    # weekday must be sorted 0..6
    assert [r["weekday"] for r in body["shifts"]] == [0, 1, 2, 3, 4, 5, 6]


@pytest.mark.asyncio
async def test_shifts_put_replaces_all_seven(client, db) -> None:
    admin = await _make_user(db, "sh-admin2@example.com", "admin")
    staff = await _make_staff(db)
    res = await client.put(
        f"/api/v1/staff/{staff.id}/shifts",
        headers=_bearer(admin),
        json=_full_payload(),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["shifts"]) == 7
    sun = next(r for r in body["shifts"] if r["weekday"] == 6)
    assert sun["is_on"] is False
    # GET round-trip mirrors PUT
    res2 = await client.get(
        f"/api/v1/staff/{staff.id}/shifts", headers=_bearer(admin)
    )
    assert res2.status_code == 200
    assert res2.json()["shifts"][0]["start_time"] == "09:00"


@pytest.mark.asyncio
async def test_shifts_put_rejects_less_than_seven(client, db) -> None:
    admin = await _make_user(db, "sh-admin3@example.com", "admin")
    staff = await _make_staff(db)
    payload = {"shifts": [{"weekday": 0, "is_on": True}]}
    res = await client.put(
        f"/api/v1/staff/{staff.id}/shifts",
        headers=_bearer(admin),
        json=payload,
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_shifts_put_staff_role_returns_403(client, db) -> None:
    staff = await _make_staff(db)
    sr_user = await _make_user(
        db, "sh-staff@example.com", "staff", staff_id=staff.id
    )
    res = await client.put(
        f"/api/v1/staff/{staff.id}/shifts",
        headers=_bearer(sr_user),
        json=_full_payload(),
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_shifts_get_other_staff_role_returns_404(client, db) -> None:
    s1 = await _make_staff(db, "本人")
    s2 = await _make_staff(db, "他人")
    sr_user = await _make_user(
        db, "sh-staff2@example.com", "staff", staff_id=s1.id
    )
    res = await client.get(
        f"/api/v1/staff/{s2.id}/shifts", headers=_bearer(sr_user)
    )
    assert res.status_code == 404, res.text
