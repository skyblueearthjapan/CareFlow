"""Authentication endpoint tests: login success, lockout, /me with JWT."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_login_success_returns_tokens(client, test_user) -> None:
    res = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "secret-pass-01"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["user"]["email"] == "admin@example.com"
    assert body["user"]["role"] == "admin"
    assert body["tokens"]["access_token"]
    assert body["tokens"]["refresh_token"]
    assert body["tokens"]["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password_returns_401(client, test_user) -> None:
    res = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "WRONG"},
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_login_locks_after_5_failed_attempts(client, test_user) -> None:
    # 5 wrong-password attempts → account is locked on the 6th.
    for _ in range(5):
        res = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.com", "password": "WRONG"},
        )
        assert res.status_code == 401

    # The next attempt — even with the *correct* password — must hit the lock.
    res = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "secret-pass-01"},
    )
    assert res.status_code == 423, res.text


@pytest.mark.asyncio
async def test_me_returns_200_with_jwt(client, test_user) -> None:
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "secret-pass-01"},
    )
    assert login.status_code == 200
    access = login.json()["tokens"]["access_token"]

    res = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {access}"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["email"] == "admin@example.com"


@pytest.mark.asyncio
async def test_me_without_token_returns_401(client) -> None:
    res = await client.get("/api/v1/auth/me")
    assert res.status_code == 401
