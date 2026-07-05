"""Tests for SecurityHeadersMiddleware (Wave 4-G)."""

from __future__ import annotations

import pytest

_EXPECTED_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
}


@pytest.mark.asyncio
async def test_security_headers_present_on_health_response(client) -> None:
    res = await client.get("/api/v1/healthz")
    # Status itself is exercised by test_health.py; we only care about headers.
    for name, value in _EXPECTED_HEADERS.items():
        assert res.headers.get(name) == value, f"missing/wrong {name}"


@pytest.mark.asyncio
async def test_hsts_header_set(client) -> None:
    res = await client.get("/api/v1/healthz")
    hsts = res.headers.get("strict-transport-security", "")
    assert "max-age=31536000" in hsts
    assert "includeSubDomains" in hsts


@pytest.mark.asyncio
async def test_content_security_policy_locks_origins(client) -> None:
    res = await client.get("/api/v1/healthz")
    csp = res.headers.get("content-security-policy", "")
    # Sanity-check that the production origins we depend on are listed.
    assert "default-src 'self'" in csp
    assert "https://maps.googleapis.com" in csp
    # And that we have NOT accidentally widened script-src to wildcard.
    assert "script-src 'self' 'unsafe-inline'" in csp


@pytest.mark.asyncio
async def test_security_headers_applied_to_404(client) -> None:
    """404 responses must still carry the hardening headers."""
    res = await client.get("/api/v1/this-route-does-not-exist")
    assert res.status_code == 404
    assert res.headers.get("x-content-type-options") == "nosniff"
    assert res.headers.get("x-frame-options") == "DENY"
