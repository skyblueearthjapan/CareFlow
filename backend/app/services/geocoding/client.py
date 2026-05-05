"""Google Maps Geocoding REST client — CareFlow Wave 4-C.

Thin async wrapper around the public Geocoding endpoint
(`maps.googleapis.com/maps/api/geocode/json`). Designed to be called from
the `/api/v1/geocode` relay; cache reads/writes happen at the API layer so
this module stays stateless and easy to mock in tests.

Failure model:
  - HTTP 5xx / network error          → ``GeocodingServiceError`` (502 upstream)
  - Google `OVER_QUERY_LIMIT`         → ``GeocodingQuotaExceeded`` (503 upstream)
  - Google `REQUEST_DENIED` / config  → ``GeocodingServiceError``
  - `ZERO_RESULTS`                    → returns ``None`` (caller decides)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Final, Protocol

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

GOOGLE_GEOCODE_URL: Final[str] = (
    "https://maps.googleapis.com/maps/api/geocode/json"
)
DEFAULT_TIMEOUT_SECONDS: Final[float] = 8.0


class GeocodingServiceError(RuntimeError):
    """Upstream Geocoding call failed in a non-retryable way."""


class GeocodingQuotaExceeded(GeocodingServiceError):
    """Google reported OVER_QUERY_LIMIT — caller should respond 503."""


@dataclass(frozen=True)
class GeocodeResult:
    lat: float
    lng: float
    formatted_address: str
    place_id: str
    status: str


class _SupportsGet(Protocol):
    async def get(  # pragma: no cover - structural protocol
        self, url: str, *, params: dict[str, Any], timeout: float
    ) -> httpx.Response: ...


async def geocode_address(
    address: str,
    *,
    api_key: str | None = None,
    language: str = "ja",
    region: str = "jp",
    client: _SupportsGet | None = None,
) -> GeocodeResult | None:
    """Resolve *address* to lat/lng via Google Maps Geocoding API.

    Args:
        address:    Free-form address string (already normalized by caller if
                    desired). Empty string raises ``ValueError``.
        api_key:    Override; defaults to ``settings.google_maps_api_key``.
        language:   `language` parameter passed through (default ``ja``).
        region:     `region` ccTLD bias (default ``jp``).
        client:     Inject an ``httpx.AsyncClient``-like object for tests.

    Returns:
        ``GeocodeResult`` on success, ``None`` when Google returns
        ``ZERO_RESULTS`` (no match for the input address).

    Raises:
        ValueError: empty address.
        GeocodingServiceError: API key missing, network error, non-2xx
            response, or terminal Google status (REQUEST_DENIED etc).
        GeocodingQuotaExceeded: Google ``OVER_QUERY_LIMIT``.
    """
    if not address or not address.strip():
        raise ValueError("address must be a non-empty string")

    key = api_key if api_key is not None else get_settings().google_maps_api_key
    if not key:
        raise GeocodingServiceError(
            "GOOGLE_MAPS_API_KEY is not configured on the server"
        )

    params: dict[str, Any] = {
        "address": address,
        "key": key,
        "language": language,
        "region": region,
    }

    try:
        if client is not None:
            response = await client.get(
                GOOGLE_GEOCODE_URL,
                params=params,
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        else:
            async with httpx.AsyncClient() as ac:
                response = await ac.get(
                    GOOGLE_GEOCODE_URL,
                    params=params,
                    timeout=DEFAULT_TIMEOUT_SECONDS,
                )
    except httpx.HTTPError as exc:
        logger.warning("geocoding network error: %s", exc)
        raise GeocodingServiceError(f"network error: {exc}") from exc

    if response.status_code >= 500:
        raise GeocodingServiceError(
            f"upstream HTTP {response.status_code} from Google Geocoding"
        )
    if response.status_code >= 400:
        # 400/403 typically mean key/billing problem — surface as 502 so the
        # operator notices in the audit trail.
        raise GeocodingServiceError(
            f"upstream HTTP {response.status_code} from Google Geocoding"
        )

    payload: dict[str, Any] = response.json()
    status = str(payload.get("status", "UNKNOWN"))

    if status == "OK":
        results = payload.get("results") or []
        if not results:
            return None
        first = results[0]
        loc = (first.get("geometry") or {}).get("location") or {}
        try:
            lat = float(loc["lat"])
            lng = float(loc["lng"])
        except (KeyError, TypeError, ValueError) as exc:
            raise GeocodingServiceError(
                "Google response missing geometry.location"
            ) from exc
        return GeocodeResult(
            lat=lat,
            lng=lng,
            formatted_address=str(first.get("formatted_address", "")),
            place_id=str(first.get("place_id", "")),
            status=status,
        )

    if status == "ZERO_RESULTS":
        return None

    if status == "OVER_QUERY_LIMIT":
        raise GeocodingQuotaExceeded(
            "Google Geocoding quota exceeded (OVER_QUERY_LIMIT)"
        )

    # REQUEST_DENIED, INVALID_REQUEST, UNKNOWN_ERROR, etc.
    error_message = str(payload.get("error_message") or "")
    raise GeocodingServiceError(
        f"Google Geocoding returned status={status}"
        + (f": {error_message}" if error_message else "")
    )
