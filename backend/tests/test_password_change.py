"""Wave 40 — `POST /api/v1/auth/change-password` (self-service password change)."""

from __future__ import annotations

import pytest

from app.core.security import hash_password, verify_password
from app.models import User

_ENDPOINT = "/api/v1/auth/change-password"
_OLD = "secret-pass-01"  # matches conftest.test_user fixture
_NEW = "newpass99"  # 9 chars, letters+digits


async def _login(client) -> str:
    """Helper: log in as the conftest test_user and return the access token."""
    res = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": _OLD},
    )
    assert res.status_code == 200, res.text
    return res.json()["tokens"]["access_token"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_change_password_success_returns_204(client, db, test_user) -> None:
    token = await _login(client)
    res = await client.post(
        _ENDPOINT,
        headers=_bearer(token),
        json={"current_password": _OLD, "new_password": _NEW},
    )
    assert res.status_code == 204, res.text

    await db.refresh(test_user)
    # Hash rotated and verifies against the new plaintext.
    assert verify_password(_NEW, test_user.password_hash)
    assert not verify_password(_OLD, test_user.password_hash)


@pytest.mark.asyncio
async def test_change_password_clears_must_change_password_flag(client, db, test_user) -> None:
    # Simulate an admin reset by flipping the flag on directly.
    test_user.must_change_password = True
    await db.commit()

    token = await _login(client)
    res = await client.post(
        _ENDPOINT,
        headers=_bearer(token),
        json={"current_password": _OLD, "new_password": _NEW},
    )
    assert res.status_code == 204, res.text

    await db.refresh(test_user)
    assert test_user.must_change_password is False


@pytest.mark.asyncio
async def test_change_password_then_login_with_new_password_succeeds(client, test_user) -> None:
    token = await _login(client)
    res = await client.post(
        _ENDPOINT,
        headers=_bearer(token),
        json={"current_password": _OLD, "new_password": _NEW},
    )
    assert res.status_code == 204

    # Reset the IP-based limiter so the follow-up /login isn't rate-limited
    # (5/15min applies independently to /login and /change-password).
    from app.core.rate_limit import limiter

    limiter.reset()

    relog = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": _NEW},
    )
    assert relog.status_code == 200, relog.text
    # Old password no longer accepted.
    bad = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": _OLD},
    )
    assert bad.status_code == 401


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_change_password_wrong_current_returns_401(client, test_user) -> None:
    token = await _login(client)
    res = await client.post(
        _ENDPOINT,
        headers=_bearer(token),
        json={"current_password": "WRONG", "new_password": _NEW},
    )
    assert res.status_code == 401, res.text


@pytest.mark.asyncio
async def test_change_password_too_short_returns_422(client, test_user) -> None:
    token = await _login(client)
    res = await client.post(
        _ENDPOINT,
        headers=_bearer(token),
        json={"current_password": _OLD, "new_password": "ab1"},
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_change_password_no_digit_returns_422(client, test_user) -> None:
    token = await _login(client)
    res = await client.post(
        _ENDPOINT,
        headers=_bearer(token),
        json={"current_password": _OLD, "new_password": "abcdefgh"},
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_change_password_no_letter_returns_422(client, test_user) -> None:
    token = await _login(client)
    res = await client.post(
        _ENDPOINT,
        headers=_bearer(token),
        json={"current_password": _OLD, "new_password": "12345678"},
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_change_password_same_as_current_returns_422(client, test_user) -> None:
    token = await _login(client)
    res = await client.post(
        _ENDPOINT,
        headers=_bearer(token),
        json={"current_password": _OLD, "new_password": _OLD},
    )
    # Same value passes the regex but is rejected by the route guard.
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_change_password_unauthenticated_returns_401(client) -> None:
    res = await client.post(
        _ENDPOINT,
        json={"current_password": _OLD, "new_password": _NEW},
    )
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# LoginResponse exposes must_change_password (Wave 40 NextAuth contract)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_response_includes_must_change_password_false(client, test_user) -> None:
    res = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": _OLD},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["user"]["must_change_password"] is False


@pytest.mark.asyncio
async def test_login_response_includes_must_change_password_true(client, db, test_user) -> None:
    test_user.must_change_password = True
    await db.commit()

    res = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": _OLD},
    )
    assert res.status_code == 200, res.text
    assert res.json()["user"]["must_change_password"] is True


# ---------------------------------------------------------------------------
# Wave 40 α regression — admin endpoints set must_change_password=True
# (admin.py already does this; we pin it here so the contract stays.)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_create_user_defaults_must_change_password_true(client, db) -> None:
    from app.core.security import create_access_token

    admin = User(
        email="w40-create-admin@example.com",
        password_hash=hash_password("does-not-matter"),
        role="admin",
    )
    db.add(admin)
    await db.commit()
    await db.refresh(admin)

    token = create_access_token(subject=admin.id, role=admin.role, staff_id=admin.staff_id)
    res = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(token),
        json={"email": "fresh@example.com", "role": "staff"},
    )
    assert res.status_code == 201, res.text
    assert res.json()["user"]["must_change_password"] is True


@pytest.mark.asyncio
async def test_admin_reset_password_sets_must_change_password_true(client, db) -> None:
    from app.core.security import create_access_token

    admin = User(
        email="w40-reset-admin@example.com",
        password_hash=hash_password("does-not-matter"),
        role="admin",
    )
    target = User(
        email="w40-reset-target@example.com",
        password_hash=hash_password("does-not-matter"),
        role="staff",
        must_change_password=False,
    )
    db.add_all([admin, target])
    await db.commit()
    await db.refresh(admin)
    await db.refresh(target)

    token = create_access_token(subject=admin.id, role=admin.role, staff_id=admin.staff_id)
    res = await client.post(
        f"/api/v1/admin/users/{target.id}/reset-password",
        headers=_bearer(token),
    )
    assert res.status_code == 200, res.text

    await db.refresh(target)
    assert target.must_change_password is True
