"""Refresh-token endpoint coverage (happy + tampering + wrong-type + expiry)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from jose import jwt

from app.core.config import get_settings


@pytest.mark.asyncio
async def test_refresh_returns_new_token_pair(client, test_user) -> None:
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "secret-pass-01"},
    )
    assert login.status_code == 200
    refresh_token = login.json()["tokens"]["refresh_token"]

    res = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_refresh_rejects_access_token(client, test_user) -> None:
    # Passing an *access* token in place of a refresh token must be rejected.
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "secret-pass-01"},
    )
    access_token = login.json()["tokens"]["access_token"]

    res = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": access_token},
    )
    assert res.status_code == 401, res.text


@pytest.mark.asyncio
async def test_refresh_rejects_expired_token(client, test_user) -> None:
    settings = get_settings()
    expired_at = datetime.now(tz=UTC) - timedelta(seconds=10)
    claims = {
        "sub": str(test_user.id),
        "role": test_user.role,
        "staff_id": None,
        "type": "refresh",
        "iat": int(expired_at.timestamp()) - 60,
        "exp": int(expired_at.timestamp()),
    }
    expired = jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)

    res = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": expired},
    )
    assert res.status_code == 401, res.text


@pytest.mark.asyncio
async def test_refresh_rejects_tampered_signature(client, test_user) -> None:
    # Sign with a different key → signature verification must fail.
    settings = get_settings()
    now = datetime.now(tz=UTC)
    claims = {
        "sub": str(test_user.id),
        "role": test_user.role,
        "staff_id": None,
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=30)).timestamp()),
    }
    tampered = jwt.encode(
        claims, "an-entirely-different-key-32-chars!!", algorithm=settings.jwt_algorithm
    )

    res = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tampered},
    )
    assert res.status_code == 401, res.text
