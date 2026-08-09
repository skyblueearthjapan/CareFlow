"""Geocoding relay endpoints — CareFlow Wave 4-C.

Two routes:

- ``POST /api/v1/geocode`` (admin/manager/staff)
    Resolves an address through ``GeocodingCache`` first, then calls
    Google Maps if MISS or ``force_refresh=true``. Cache row is upserted
    on every successful upstream call. Quota exhaustion surfaces as 503
    with a ``Retry-After`` header so the frontend can back off.

- ``GET /api/v1/geocoding/cache`` (admin/manager)
    Read-only listing of stored hits. The same shape was first exposed
    via ``/api/v1/integrations/geocoding/cache`` (admin-only); this
    namespace adds a manager-accessible mirror for the new dedicated
    Geocoding admin screen.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.deps import DbDep, require_role
from app.models.geocoding_cache import GeocodingCache
from app.models.user import User
from app.schemas._pagination import Paginated
from app.schemas.geocoding import (
    GeocodeRequest,
    GeocodeResponse,
    GeocodingCacheRead,
)
from app.services.geocoding.client import (
    GeocodingQuotaExceeded,
    GeocodingServiceError,
    geocode_address,
)
from app.services.geocoding.hash import address_hash, normalize_address

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/geocode",
    response_model=GeocodeResponse,
    summary="Resolve an address to lat/lng (cache-aware)",
    tags=["geocoding"],
)
async def geocode(
    payload: GeocodeRequest,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin", "staff"))],
) -> GeocodeResponse:
    """Cache-first geocode lookup.

    Flow:
      1. Normalize + sha256 the address.
      2. SELECT GeocodingCache by hash. On HIT (and not ``force_refresh``)
         return immediately with ``cached=True``.
      3. Call Google Maps. On success, upsert the cache row and return
         with ``cached=False``.
    """
    normalized = normalize_address(payload.address)
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="address must contain non-whitespace characters",
        )

    addr_hash = address_hash(payload.address)

    cached_row = await db.scalar(
        select(GeocodingCache).where(GeocodingCache.address_hash == addr_hash)
    )

    if cached_row is not None and not payload.force_refresh:
        return GeocodeResponse(
            lat=float(cached_row.lat),
            lng=float(cached_row.lng),
            formatted_address=cached_row.address,
            place_id=None,
            cached=True,
            address_hash=addr_hash,
        )

    # MISS or forced refresh — call upstream.
    try:
        result = await geocode_address(normalized)
    except GeocodingQuotaExceeded as exc:
        # Tell the client to wait one minute before retrying. We don't have
        # a precise reset-time from Google, so use a conservative constant.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
            headers={"Retry-After": "60"},
        ) from exc
    except GeocodingServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Geocoding service error: {exc}",
        ) from exc

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No geocoding result for the given address",
        )

    # Upsert: update existing row in place when force_refresh hit, else insert.
    now = datetime.now(UTC)
    if cached_row is not None:
        cached_row.lat = result.lat
        cached_row.lng = result.lng
        cached_row.address = result.formatted_address or normalized
        cached_row.provider = "google"
        cached_row.looked_up_at = now
    else:
        cached_row = GeocodingCache(
            address_hash=addr_hash,
            address=result.formatted_address or normalized,
            lat=result.lat,
            lng=result.lng,
            provider="google",
            looked_up_at=now,
        )
        db.add(cached_row)

    try:
        await db.commit()
    except IntegrityError:
        # Another concurrent request inserted the same hash — re-fetch and
        # use that row instead of bubbling a 500 to the user.
        await db.rollback()
        existing = await db.scalar(
            select(GeocodingCache).where(GeocodingCache.address_hash == addr_hash)
        )
        if existing is None:
            raise HTTPException(  # noqa: B904
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to persist geocoding result",
            )
        cached_row = existing

    return GeocodeResponse(
        lat=float(cached_row.lat),
        lng=float(cached_row.lng),
        formatted_address=cached_row.address,
        place_id=result.place_id or None,
        cached=False,
        address_hash=addr_hash,
    )


@router.get(
    "/geocoding/cache",
    response_model=Paginated[GeocodingCacheRead],
    summary="List geocoding cache entries (admin/manager)",
    tags=["geocoding"],
)
async def list_geocoding_cache(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    q: Annotated[str | None, Query(description="Substring filter on the cached address")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Paginated[GeocodingCacheRead]:
    base = select(GeocodingCache)
    count_stmt = select(func.count()).select_from(GeocodingCache)
    if q:
        base = base.where(GeocodingCache.address.ilike(f"%{q}%"))
        count_stmt = count_stmt.where(GeocodingCache.address.ilike(f"%{q}%"))

    stmt = base.order_by(GeocodingCache.looked_up_at.desc()).limit(limit).offset(offset)
    rows = (await db.scalars(stmt)).all()
    total = (await db.scalar(count_stmt)) or 0
    return Paginated[GeocodingCacheRead](
        items=[GeocodingCacheRead.model_validate(r, from_attributes=True) for r in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )
