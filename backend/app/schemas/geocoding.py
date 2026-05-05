"""Schemas for the /api/v1/geocode endpoint — CareFlow Wave 4-C.

Mirrors the request/response contract documented in
``docs/plans/D4-integrations-plan.md`` Phase F. The cache-listing schema
(`GeocodingCacheRead`) is re-exported from `app.schemas.integrations`
where it has lived since Wave 2-B; we keep a thin alias here so callers
that import from ``app.schemas.geocoding`` get a single namespace.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# Re-export for callers that prefer the namespaced import.
from app.schemas.integrations import GeocodingCacheRead  # noqa: F401


class GeocodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: str = Field(
        min_length=1,
        max_length=500,
        description="Free-form address string to resolve.",
    )
    force_refresh: bool = Field(
        default=False,
        description=(
            "When true, ignore the GeocodingCache hit and re-query Google "
            "Maps. The fresh result overwrites the cached row in place."
        ),
    )


class GeocodeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lat: float
    lng: float
    formatted_address: str | None = None
    place_id: str | None = None
    cached: bool = Field(
        description="True when served from GeocodingCache (no upstream call)."
    )
    address_hash: str = Field(description="sha256 hex digest of normalized address.")


__all__ = [
    "GeocodeRequest",
    "GeocodeResponse",
    "GeocodingCacheRead",
]
