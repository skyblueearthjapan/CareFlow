"""Tests for /api/v1/geocode + /api/v1/geocoding/cache (Wave 4-C)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import User
from app.models.geocoding_cache import GeocodingCache
from app.services.geocoding.client import (
    GeocodeResult,
    GeocodingQuotaExceeded,
    GeocodingServiceError,
)
from app.services.geocoding.hash import address_hash, normalize_address


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


GEOCODE_PATH = "app.api.v1.geocoding.geocode_address"


# --- /geocode -------------------------------------------------------------


@pytest.mark.asyncio
async def test_geocode_cache_miss_calls_upstream_and_persists(client, db) -> None:
    user = await _make_user(db, "geo-staff@example.com", "staff")
    fake = AsyncMock(
        return_value=GeocodeResult(
            lat=35.6895,
            lng=139.6917,
            formatted_address="日本、〒163-8001 東京都新宿区西新宿2-8-1",
            place_id="ChIJ_dummy",
            status="OK",
        )
    )
    with patch(GEOCODE_PATH, fake):
        res = await client.post(
            "/api/v1/geocode",
            json={"address": "東京都新宿区西新宿2-8-1"},
            headers=_bearer(user),
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["cached"] is False
    assert body["lat"] == pytest.approx(35.6895)
    assert body["lng"] == pytest.approx(139.6917)
    assert body["address_hash"] == address_hash("東京都新宿区西新宿2-8-1")
    fake.assert_awaited_once()

    # Row was upserted.
    row = await db.scalar(
        select(GeocodingCache).where(GeocodingCache.address_hash == body["address_hash"])
    )
    assert row is not None


@pytest.mark.asyncio
async def test_geocode_cache_hit_skips_upstream(client, db) -> None:
    user = await _make_user(db, "geo-mgr@example.com", "manager")
    raw = "東京都千代田区"
    db.add(
        GeocodingCache(
            address_hash=address_hash(raw),
            address=normalize_address(raw),
            lat=35.69,
            lng=139.75,
            provider="google",
        )
    )
    await db.commit()

    fake = AsyncMock(side_effect=AssertionError("should not be called on HIT"))
    with patch(GEOCODE_PATH, fake):
        res = await client.post(
            "/api/v1/geocode",
            json={"address": raw},
            headers=_bearer(user),
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["cached"] is True
    assert body["lat"] == pytest.approx(35.69)
    fake.assert_not_called()


@pytest.mark.asyncio
async def test_geocode_force_refresh_overwrites_cache(client, db) -> None:
    user = await _make_user(db, "geo-admin@example.com", "admin")
    raw = "東京都港区"
    db.add(
        GeocodingCache(
            address_hash=address_hash(raw),
            address=normalize_address(raw),
            lat=10.0,
            lng=20.0,
            provider="google",
        )
    )
    await db.commit()

    fake = AsyncMock(
        return_value=GeocodeResult(
            lat=35.658,
            lng=139.751,
            formatted_address="東京都港区 (refreshed)",
            place_id="pid",
            status="OK",
        )
    )
    with patch(GEOCODE_PATH, fake):
        res = await client.post(
            "/api/v1/geocode",
            json={"address": raw, "force_refresh": True},
            headers=_bearer(user),
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["cached"] is False
    assert body["lat"] == pytest.approx(35.658)
    fake.assert_awaited_once()

    refreshed = await db.scalar(
        select(GeocodingCache).where(GeocodingCache.address_hash == address_hash(raw))
    )
    assert refreshed is not None
    assert float(refreshed.lat) == pytest.approx(35.658)
    assert "refreshed" in refreshed.address


@pytest.mark.asyncio
async def test_geocode_zero_results_returns_404(client, db) -> None:
    user = await _make_user(db, "geo-staff2@example.com", "staff")
    with patch(GEOCODE_PATH, AsyncMock(return_value=None)):
        res = await client.post(
            "/api/v1/geocode",
            json={"address": "存在しない住所XYZ"},
            headers=_bearer(user),
        )
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_geocode_quota_exceeded_returns_503_with_retry_after(client, db) -> None:
    user = await _make_user(db, "geo-q@example.com", "staff")
    fake = AsyncMock(side_effect=GeocodingQuotaExceeded("OVER_QUERY_LIMIT"))
    with patch(GEOCODE_PATH, fake):
        res = await client.post(
            "/api/v1/geocode",
            json={"address": "東京都"},
            headers=_bearer(user),
        )
    assert res.status_code == 503, res.text
    assert res.headers.get("retry-after") == "60"


@pytest.mark.asyncio
async def test_geocode_upstream_error_returns_502(client, db) -> None:
    user = await _make_user(db, "geo-e@example.com", "staff")
    fake = AsyncMock(side_effect=GeocodingServiceError("network error: boom"))
    with patch(GEOCODE_PATH, fake):
        res = await client.post(
            "/api/v1/geocode",
            json={"address": "東京都"},
            headers=_bearer(user),
        )
    assert res.status_code == 502, res.text


@pytest.mark.asyncio
async def test_geocode_no_token_returns_401(client) -> None:
    res = await client.post("/api/v1/geocode", json={"address": "東京都"})
    assert res.status_code == 401, res.text


@pytest.mark.asyncio
async def test_geocode_address_hash_stable_for_equivalent_inputs(client, db) -> None:
    """Whitespace/full-width variants should hit the SAME cache row."""
    user = await _make_user(db, "geo-h@example.com", "staff")
    fake = AsyncMock(
        return_value=GeocodeResult(
            lat=1.0,
            lng=2.0,
            formatted_address="東京都 千代田区",
            place_id="x",
            status="OK",
        )
    )

    with patch(GEOCODE_PATH, fake):
        # First call populates cache.
        r1 = await client.post(
            "/api/v1/geocode",
            json={"address": "  東京都   千代田区 "},
            headers=_bearer(user),
        )
        assert r1.status_code == 200, r1.text
        assert r1.json()["cached"] is False

        # Second call with NFKC-equivalent input must hit the cache.
        r2 = await client.post(
            "/api/v1/geocode",
            json={"address": "東京都 千代田区"},
            headers=_bearer(user),
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["cached"] is True

    # Upstream called exactly once.
    assert fake.await_count == 1


# --- /geocoding/cache -----------------------------------------------------


@pytest.mark.asyncio
async def test_cache_listing_requires_admin_or_manager(client, db) -> None:
    staff_user = await _make_user(db, "geo-cs@example.com", "staff")
    res = await client.get("/api/v1/geocoding/cache", headers=_bearer(staff_user))
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_cache_listing_admin_returns_paginated_rows(client, db) -> None:
    admin = await _make_user(db, "geo-ca@example.com", "admin")
    db.add(
        GeocodingCache(
            address_hash=address_hash("住所A"),
            address="住所A",
            lat=1.0,
            lng=2.0,
            provider="google",
        )
    )
    await db.commit()
    res = await client.get("/api/v1/geocoding/cache", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] >= 1
    assert any(item["address"] == "住所A" for item in body["items"])
