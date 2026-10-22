"""住所 → 座標 (控え ``geocoding_cache`` 優先 → 無ければ Google → 控えに保存).

``POST /api/v1/geocode`` と、患者の訪問先の位置をサーバーで求める経路
(``app.services.patient_address.geocode_visit_address``) が共有する。commit はしない
(呼び出し側の transaction に乗る)。控えの INSERT は savepoint で包み、同じ住所を
並行して保存したときは先に入った行を使う。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.geocoding_cache import GeocodingCache
from app.services.geocoding.client import geocode_address
from app.services.geocoding.hash import address_hash, normalize_address


@dataclass(frozen=True)
class CachedGeocode:
    lat: float
    lng: float
    formatted_address: str
    place_id: str | None
    cached: bool
    address_hash: str


async def geocode_with_cache(
    db: AsyncSession, address: str, *, force_refresh: bool = False
) -> CachedGeocode | None:
    """控え優先で住所の位置を求める.

    Returns:
        ``CachedGeocode``。Google が該当なし (ZERO_RESULTS) なら None。

    Raises:
        ValueError: 空白だけの住所。
        GeocodingServiceError / GeocodingQuotaExceeded: キー未設定・通信・上限
            (``app.services.geocoding.client.geocode_address`` と同じ)。
    """
    normalized = normalize_address(address)
    if not normalized:
        raise ValueError("address must contain non-whitespace characters")
    addr_hash = address_hash(address)

    cached_row = await db.scalar(
        select(GeocodingCache).where(GeocodingCache.address_hash == addr_hash)
    )
    if cached_row is not None and not force_refresh:
        return CachedGeocode(
            lat=float(cached_row.lat),
            lng=float(cached_row.lng),
            formatted_address=cached_row.address,
            place_id=None,
            cached=True,
            address_hash=addr_hash,
        )

    result = await geocode_address(normalized)
    if result is None:
        return None

    now = datetime.now(UTC)
    if cached_row is not None:
        cached_row.lat = result.lat
        cached_row.lng = result.lng
        cached_row.address = result.formatted_address or normalized
        cached_row.provider = "google"
        cached_row.looked_up_at = now
    else:
        try:
            async with db.begin_nested():
                cached_row = GeocodingCache(
                    address_hash=addr_hash,
                    address=result.formatted_address or normalized,
                    lat=result.lat,
                    lng=result.lng,
                    provider="google",
                    looked_up_at=now,
                )
                db.add(cached_row)
        except IntegrityError:
            # 同じ住所を並行して保存した → 先に入った行を使う。
            existing = await db.scalar(
                select(GeocodingCache).where(GeocodingCache.address_hash == addr_hash)
            )
            if existing is None:
                raise
            cached_row = existing

    return CachedGeocode(
        lat=float(cached_row.lat),
        lng=float(cached_row.lng),
        formatted_address=cached_row.address,
        place_id=result.place_id or None,
        cached=False,
        address_hash=addr_hash,
    )
