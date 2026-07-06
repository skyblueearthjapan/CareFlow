"""Async HTTP client wrapper for the existing kaipoke-api (Flask + Playwright).

Phase 5-1 Wave 4-A — wraps `https://kaipoke-api.net/api/*` so the FastAPI
relay layer can call expand/export/diff/apply/status/stop without leaking
Bearer tokens or raw httpx calls into the route handlers.

Behaviour:
  * Bearer token (env `KAIPOKE_API_TOKEN`) auto-attached.
  * 30 second timeout, single retry on 5xx.
  * 409 Conflict (kaipoke "busy") raised as `KaipokeBusyError`.
  * Other non-2xx raised as `KaipokeApiError(status, body)`.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from typing import Any

import httpx

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_BASE_URL = "https://kaipoke-api.net"


class KaipokeApiError(RuntimeError):
    """Raised when kaipoke-api returns a non-2xx HTTP status (other than 409)."""

    def __init__(self, status_code: int, body: Any, *, message: str | None = None) -> None:
        super().__init__(message or f"kaipoke-api error {status_code}")
        self.status_code = status_code
        self.body = body


class KaipokeBusyError(KaipokeApiError):
    """Raised on HTTP 409 — kaipoke worker already running another job."""

    def __init__(self, body: Any) -> None:
        super().__init__(409, body, message="kaipoke-api is busy (409)")


class KaipokeClient:
    """Thin async wrapper around the kaipoke-api endpoints we relay."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = (base_url or os.getenv("KAIPOKE_API_BASE_URL") or DEFAULT_BASE_URL).rstrip(
            "/"
        )
        self._token = token if token is not None else os.getenv("KAIPOKE_API_TOKEN", "")
        self._timeout = timeout
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> KaipokeClient:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.aclose()

    # --- Public surface ---------------------------------------------------

    async def status(self) -> dict[str, Any]:
        return await self._request("GET", "/api/status")

    async def expand(
        self, payload: Mapping[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        # /api/expand は同期で 15-20 分ブロックする。呼び出し側は短い timeout を
        # 渡し、Timeout(504) を「起動した」とみなす (kaipoke 側は走り続ける)。
        return await self._request("POST", "/api/expand", json=payload, timeout=timeout)

    async def export(
        self, payload: Mapping[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        # 同期 export (async:false) は ~50s ブロックするため、呼び出し側で
        # timeout を延長できるようにする (既定 30s では足りない)。
        return await self._request("POST", "/api/export", json=payload, timeout=timeout)

    async def diff(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/api/diff", json=payload)

    async def login_test(
        self, payload: Mapping[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        """接続テスト (C-1): payload の認証情報で実ログインを1回試す (同期 ~60s)。"""
        return await self._request("POST", "/api/login-test", json=payload, timeout=timeout)

    async def apply(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/api/apply", json=payload)

    async def export_result(self) -> dict[str, Any]:
        """Poll the (single-slot) export result.

        Returns {status: running|completed|error|no_result, result?, error?}.
        """
        return await self._request("GET", "/api/export/result")

    async def apply_result(self) -> dict[str, Any]:
        """Poll the (single-slot) apply result incl. live progress.

        Returns {status: running|completed|error|no_result, progress?, result?}.
        """
        return await self._request("GET", "/api/apply/result")

    async def logs(self, tail: int = 200) -> dict[str, Any]:
        """Tail the kaipoke ring-buffer log (`{ok, lines[], total}`)."""
        return await self._request("GET", f"/api/kaipoke/logs?tail={int(tail)}")

    async def stop(self) -> dict[str, Any]:
        """Request a graceful emergency stop.

        kaipoke-api is single-slot: `/api/stop` takes no job id and halts the
        one running Playwright task after its current record completes.
        """
        return await self._request("POST", "/api/stop")

    # --- Internals --------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        retry: bool = True,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        kwargs: dict[str, Any] = {"json": json, "headers": self._headers()}
        if timeout is not None:
            kwargs["timeout"] = timeout
        try:
            resp = await self._client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            raise KaipokeApiError(504, {"error": "timeout"}, message=str(exc)) from exc
        except httpx.RequestError as exc:
            raise KaipokeApiError(502, {"error": "network"}, message=str(exc)) from exc

        if resp.status_code >= 500 and retry:
            # Single retry with brief backoff on 5xx.
            await asyncio.sleep(0.25)
            return await self._request(method, path, json=json, retry=False, timeout=timeout)

        body: Any
        try:
            body = resp.json() if resp.content else {}
        except ValueError:
            body = {"raw": resp.text}

        if resp.status_code == 409:
            raise KaipokeBusyError(body)
        if resp.status_code >= 400:
            raise KaipokeApiError(resp.status_code, body)
        return body if isinstance(body, dict) else {"data": body}


# --- Module-level factory & override hook --------------------------------

_OVERRIDE: KaipokeClient | None = None


def set_test_client(client: KaipokeClient | None) -> None:
    """Test seam: inject a stub client without monkeypatching the constructor."""
    global _OVERRIDE
    _OVERRIDE = client


def get_kaipoke_client() -> KaipokeClient:
    """FastAPI dependency: per-request client (or a test override)."""
    if _OVERRIDE is not None:
        return _OVERRIDE
    return KaipokeClient()
