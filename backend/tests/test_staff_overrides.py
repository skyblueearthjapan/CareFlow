"""Tests for /api/v1/staff/{id}/overrides (CRUD + RBAC + date-range)."""

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


async def _make_staff(db, name: str = "休み太郎") -> Staff:
    s = Staff(name=name)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_overrides_create_normalises_japanese(client, db) -> None:
    admin = await _make_user(db, "ov-admin1@example.com", "admin")
    staff = await _make_staff(db)
    res = await client.post(
        f"/api/v1/staff/{staff.id}/overrides",
        headers=_bearer(admin),
        json={
            "date": "2026-05-06",
            "override_type": "休み",
            "reason": "私用",
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["override_type"] == "off"
    assert body["weekday"] == 2  # 2026-05-06 is a Wednesday
    assert body["reason"] == "私用"


@pytest.mark.asyncio
async def test_overrides_create_custom_time_requires_times(client, db) -> None:
    admin = await _make_user(db, "ov-admin2@example.com", "admin")
    staff = await _make_staff(db)
    res = await client.post(
        f"/api/v1/staff/{staff.id}/overrides",
        headers=_bearer(admin),
        json={
            "date": "2026-05-07",
            "override_type": "時間変更",
            # missing start_time/end_time
        },
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_overrides_list_filters_by_date_range(client, db) -> None:
    admin = await _make_user(db, "ov-admin3@example.com", "admin")
    staff = await _make_staff(db)
    # Insert one in May, one in June.
    for d in ("2026-05-06", "2026-06-10"):
        r = await client.post(
            f"/api/v1/staff/{staff.id}/overrides",
            headers=_bearer(admin),
            json={"date": d, "override_type": "休み"},
        )
        assert r.status_code == 201, r.text

    res = await client.get(
        f"/api/v1/staff/{staff.id}/overrides",
        headers=_bearer(admin),
        params={"from": "2026-06-01", "to": "2026-06-30"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body) == 1
    assert body[0]["iso_year"] == 2026


@pytest.mark.asyncio
async def test_overrides_patch_changes_type(client, db) -> None:
    admin = await _make_user(db, "ov-admin4@example.com", "admin")
    staff = await _make_staff(db)
    create = await client.post(
        f"/api/v1/staff/{staff.id}/overrides",
        headers=_bearer(admin),
        json={"date": "2026-05-08", "override_type": "休み"},
    )
    assert create.status_code == 201
    oid = create.json()["id"]

    patch = await client.patch(
        f"/api/v1/staff/{staff.id}/overrides/{oid}",
        headers=_bearer(admin),
        json={
            "override_type": "時間変更",
            "start_time": "10:00",
            "end_time": "16:00",
        },
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["override_type"] == "custom_time"
    assert patch.json()["start_time"] == "10:00"


@pytest.mark.asyncio
async def test_overrides_delete_returns_204(client, db) -> None:
    admin = await _make_user(db, "ov-admin5@example.com", "admin")
    staff = await _make_staff(db)
    create = await client.post(
        f"/api/v1/staff/{staff.id}/overrides",
        headers=_bearer(admin),
        json={"date": "2026-05-09", "override_type": "休み"},
    )
    assert create.status_code == 201
    oid = create.json()["id"]

    res = await client.delete(
        f"/api/v1/staff/{staff.id}/overrides/{oid}",
        headers=_bearer(admin),
    )
    assert res.status_code == 204, res.text


@pytest.mark.asyncio
async def test_overrides_post_staff_role_returns_403(client, db) -> None:
    staff = await _make_staff(db)
    sr_user = await _make_user(
        db, "ov-staff@example.com", "staff", staff_id=staff.id
    )
    res = await client.post(
        f"/api/v1/staff/{staff.id}/overrides",
        headers=_bearer(sr_user),
        json={"date": "2026-05-09", "override_type": "休み"},
    )
    assert res.status_code == 403, res.text
