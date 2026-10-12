"""中継のサーバーどうしの口（backend だけが話す・:8092・docs/plans/karte-via-seat-2026-10-13.md）。

| 道 | 中身 |
|---|---|
| GET  /svc/healthz | {"ok": true}（合言葉は要る） |
| POST /svc/karte/draft | relay.karte.KarteDrafter.draft（入力の形が違えば 400） |

守り:
- ホストへは出さない（compose の ports に書かない）。backend の網（deployment_default）から
  ``http://carelink-agent:8092`` で届く。Cloudflare の道（/agent-relay/* → 公開口 :8090）とは別の口。
- 箱の網（AGENT_SANDBOX_SUBNET）からの接続は 404。
- 合言葉 ``X-Rakusuke-Service-Token``（AGENT_RELAY_SERVICE_TOKEN）。設定が無ければ全部 404（使わない）。
"""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import Body, FastAPI, Request
from fastapi.responses import JSONResponse

from relay.karte import KarteDrafter
from relay.service import RelayService

SERVICE_TOKEN_HEADER = "X-Rakusuke-Service-Token"


def create_service_app(svc: RelayService, drafter: KarteDrafter) -> FastAPI:
    cfg = svc.cfg
    app = FastAPI(title="らく助 中継（サーバーどうし）", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def _guard(request: Request, call_next):
        if not cfg.service_token or cfg.in_sandbox(request.client.host if request.client else None):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        got = request.headers.get(SERVICE_TOKEN_HEADER) or ""
        if not hmac.compare_digest(got.encode("utf-8"), cfg.service_token.encode("utf-8")):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/svc/healthz")
    async def health() -> dict:
        return {"ok": True}

    @app.post("/svc/karte/draft")
    async def karte_draft(payload: Any = Body(None)) -> Any:
        try:
            return await drafter.draft(payload)
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=400)

    return app
