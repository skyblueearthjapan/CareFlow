"""Health endpoint smoke tests."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_healthz_returns_200(client) -> None:
    res = await client.get("/api/v1/healthz")
    assert res.status_code == 200
    body = res.json()
    assert body == {"status": "ok"}


@pytest.mark.asyncio
async def test_readyz_returns_200_when_db_reachable(client) -> None:
    res = await client.get("/api/v1/readyz")
    assert res.status_code == 200
    assert res.json()["status"] == "ready"
