"""Smoke tests for /api/v1/allocate/run (Phase W1-D continuation)."""

from __future__ import annotations

import pytest

from app.core.security import create_access_token, hash_password
from app.models import User


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
async def test_allocate_run_admin_returns_200(client, db) -> None:
    admin = await _make_user(db, "alloc-admin@example.com", "admin")
    res = await client.post(
        "/api/v1/allocate/run",
        headers=_bearer(admin),
        json={"week_start": "2026-05-04"},  # Monday
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "summary" in body and "assignments" in body
    assert body["week_start"] == "2026-05-04"
    assert body["summary"]["total"] == len(body["assignments"])


@pytest.mark.asyncio
async def test_allocate_run_staff_returns_403(client, db) -> None:
    staff_user = await _make_user(db, "alloc-staff@example.com", "staff")
    res = await client.post(
        "/api/v1/allocate/run",
        headers=_bearer(staff_user),
        json={"week_start": "2026-05-04"},
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_allocate_run_no_token_returns_401(client) -> None:
    res = await client.post(
        "/api/v1/allocate/run",
        json={"week_start": "2026-05-04"},
    )
    assert res.status_code == 401, res.text


@pytest.mark.asyncio
async def test_allocate_run_non_monday_returns_422(client, db) -> None:
    admin = await _make_user(db, "alloc-admin2@example.com", "admin")
    res = await client.post(
        "/api/v1/allocate/run",
        headers=_bearer(admin),
        json={"week_start": "2026-05-05"},  # Tuesday
    )
    assert res.status_code == 422, res.text
