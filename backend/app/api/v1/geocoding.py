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
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select

from app.core.deps import DbDep, require_role
from app.models.geocoding_cache import GeocodingCache
from app.models.user import User
from app.schemas._pagination import Paginated
from app.schemas.geocoding import (
    GeocodeRequest,
    GeocodeResponse,
    GeocodingCacheRead,
)
from app.services.geocoding.cached import geocode_with_cache
from app.services.geocoding.client import (
    GeocodingQuotaExceeded,
    GeocodingServiceError,
)

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

    Flow (``app.services.geocoding.cached.geocode_with_cache`` と共通):
      1. Normalize + sha256 the address.
      2. SELECT GeocodingCache by hash. On HIT (and not ``force_refresh``)
         return immediately with ``cached=True``.
      3. Call Google Maps. On success, upsert the cache row and return
         with ``cached=False``.
    """
    try:
        hit = await geocode_with_cache(db, payload.address, force_refresh=payload.force_refresh)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="address must contain non-whitespace characters",
        ) from exc
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

    if hit is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No geocoding result for the given address",
        )
    if not hit.cached:
        await db.commit()

    return GeocodeResponse(
        lat=hit.lat,
        lng=hit.lng,
        formatted_address=hit.formatted_address,
        place_id=hit.place_id,
        cached=hit.cached,
        address_hash=hit.address_hash,
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
