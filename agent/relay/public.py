"""中継の公開口（ブラウザー向け・Cloudflare Tunnel の /agent-relay/* → :8090・設計 §1-7・§1-8）。

鍵 = らく助のログインの access JWT（Authorization: Bearer）。backend の /auth/me で確かめ、管理者だけ。
加えて: 全体の止め（AGENT_RELAY_ENABLED）・試験中の許可（AGENT_ALLOWED_USER_IDS）・機能の旗（rakusuke_agent）。
相談は作った本人だけが見られる（D8）。流れは SSE（fetch で読む・15 秒ごとに ping）。

画面（frontend/components/agent/relay.ts）との約束 — 道・中身はここが正:
  GET    /agent-relay/healthz                                  → {"ok": true}（鍵なし・compose の healthcheck）
  GET    /sessions                                             → [Session]（Session = 下の _public_session）
  POST   /sessions {title?, provider?: codex|claude}           → Session（provider を省くと既定＝codex）
  GET    /engine                                               → {default_provider, providers, login{codex|claude:
                                                                  {state: ok|missing|not_subscription, plan, at}}}
  PATCH  /sessions/{id} {title?, pinned?}                      → Session
  GET    /sessions/{id}                                        → Session ＋ plans・live・busy
  GET    /sessions/{id}/events?after=<seq>                     → SSE（id: seq / data: 共通イベント）
  POST   /sessions/{id}/messages {text, device, plan_ref?}     → {ok, seq}（plan_ref = 案の版の番号）
  POST   /sessions/{id}/answers {question_id, answers{質問: 答え}, device} → {ok}
  POST   /sessions/{id}/stop                                   → {ok}
  GET    /sessions/{id}/plans/{version}/html?size=a4|a3        → 案の HTML（CSP sandbox）
  POST   /sessions/{id}/plans/{version}/print-url {size}       → {url}（5 分・その人・その版だけの署名つき）
  GET    /agent-relay/print/{id}/{version}?size&u&exp&sig      → 案の HTML（鍵なし・署名で確かめる）
  POST   /sessions/{id}/plans/{version}/approve {device}       → {ok, version, expires_at}
  POST   /sessions/{id}/plans/{version}/reject {device}        → {ok}
  POST   /sessions/{id}/runs/{run_id}/undo/approve {device}    → {ok, expires_at}
承認番号（ap_…）は画面に返さない。中継が CLI へ直接渡す（モデルは画面を通して番号を受け取らない・設計 §3-3）。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from relay.approvals import Refused
from relay.auth import AuthChecker, FeatureGate, User, always_on
from relay.service import RelayService

PREFIX = "/agent-relay"
#: 案の HTML（srcdoc でも新しいタブでも）: スクリプト不可・不透明オリジン（設計 §1-9）。
PLAN_CSP = ("sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; "
            "form-action 'none'; base-uri 'none'; frame-ancestors 'self'")


#: 印刷用の署名つき URL の寿命（秒・設計 §1-7）。
PRINT_URL_TTL = 300
#: 画面に出さない会話の項目（CLI の会話 ID は中継の中だけで使う）。
_HIDDEN_SESSION_FIELDS = {"cli_session_id"}


def _iso_session(s: dict) -> dict:
    out = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in s.items()
           if k not in _HIDDEN_SESSION_FIELDS}
    out["needs_you"] = s.get("status") in ("waiting_answer", "waiting_approval")
    return out


def _device(payload: dict) -> str:
    return "mobile" if str(payload.get("device") or "pc") == "mobile" else "pc"


def print_signature(key: str, session_id: str, version: int, size: str, user_id: str, exp: int) -> str:
    msg = f"{session_id}\n{version}\n{size}\n{user_id}\n{exp}".encode("utf-8")
    return hmac.new(key.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def _plan_html_response(html: str) -> HTMLResponse:
    return HTMLResponse(html, headers={"Content-Security-Policy": PLAN_CSP, "Cache-Control": "no-store",
                                       "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})


def create_public_app(svc: RelayService, auth: AuthChecker, feature_gate: FeatureGate = always_on,
                      sweep_seconds: float = 30.0) -> FastAPI:
    cfg = svc.cfg

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async def sweeper():
            while True:
                await asyncio.sleep(sweep_seconds)
                try:
                    await svc.sweep()
                except Exception:
                    pass
        task = asyncio.create_task(sweeper())
        try:
            yield
        finally:
            task.cancel()
            await svc.shutdown()

    app = FastAPI(title="らく助 中継", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    # 本番は同じオリジン（Cloudflare の道）なので CORS は使わない。staging（手元の Next.js :3000 → SSH 転送の中継）
    # だけ、許した Origin（AGENT_PUBLIC_ORIGIN）からの呼び出しを通す。Cookie は使わない（Bearer）。
    app.add_middleware(CORSMiddleware, allow_origins=list(cfg.allowed_origins), allow_credentials=False,
                       allow_methods=["GET", "POST", "PATCH"], allow_headers=["Authorization", "Content-Type"],
                       max_age=600)

    @app.middleware("http")
    async def _not_from_sandbox(request: Request, call_next):
        # 箱の網からは公開口を使わせない（箱は公開口へ届かない網の作りだが二重に・設計 §3-3）
        if cfg.in_sandbox(request.client.host if request.client else None):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return await call_next(request)

    @app.exception_handler(Refused)
    async def _refused(_req: Request, exc: Refused) -> JSONResponse:
        return JSONResponse({"detail": exc.message}, status_code=exc.status)

    async def current_user(request: Request) -> User:
        if not cfg.enabled:
            raise HTTPException(404, "Not Found")
        if request.method != "GET":
            origin = request.headers.get("origin")
            if origin and origin not in cfg.allowed_origins:
                raise HTTPException(403, "この画面からは使えません")
        h = request.headers.get("authorization") or ""
        if not h.lower().startswith("bearer ") or not h[7:].strip():
            raise HTTPException(401, "ログインしてください")
        user = await auth(h[7:].strip())
        if user is None:
            raise HTTPException(401, "ログインが切れました。もう一度ログインしてください")
        if not user.is_admin:
            raise HTTPException(403, "管理者だけが使えます")
        if cfg.allowed_user_ids and user.id not in cfg.allowed_user_ids:
            raise HTTPException(403, "いまは試験中のため、決められた方だけが使えます")
        if not await feature_gate(user):
            raise HTTPException(404, "Not Found")
        return user

    def session_of(session_id: str, user: User) -> dict:
        return svc.owned_session(session_id, user)

    @app.get(PREFIX + "/health")
    @app.get(PREFIX + "/healthz")
    async def health() -> dict:
        return {"ok": True}

    @app.post(PREFIX + "/sessions")
    async def create(payload: dict = Body(default={}), user: User = Depends(current_user)) -> Any:
        prov = payload.get("provider")
        return _iso_session(svc.create_session(user, str(payload.get("title") or ""),
                                               str(prov) if prov in ("codex", "claude") else None))

    @app.get(PREFIX + "/engine")
    async def engine(user: User = Depends(current_user)) -> Any:
        return svc.engine_info()

    @app.get(PREFIX + "/sessions")
    async def list_sessions(user: User = Depends(current_user)) -> Any:
        return [_iso_session(s) for s in svc.store.list_sessions(user.id)]

    @app.patch(PREFIX + "/sessions/{session_id}")
    async def update(session_id: str, payload: dict = Body(...), user: User = Depends(current_user)) -> Any:
        session_of(session_id, user)
        fields: dict[str, Any] = {}
        if "title" in payload:
            title = " ".join(str(payload.get("title") or "").split())[:80]
            if not title:
                raise HTTPException(400, "名前が空です")
            fields["title"] = title
        if "pinned" in payload:
            fields["pinned"] = bool(payload.get("pinned"))
        if fields:
            svc.store.update_session(session_id, **fields)
        return _iso_session(session_of(session_id, user))

    @app.get(PREFIX + "/sessions/{session_id}")
    async def detail(session_id: str, user: User = Depends(current_user)) -> Any:
        s = session_of(session_id, user)
        plans = [{"version": p["version"], "title": p["title"], "status": p["status"],
                  "ready": p["check_result"].get("ready", False)} for p in svc.store.list_plans(session_id)]
        live = svc.live.get(session_id)
        return {**_iso_session(s), "plans": plans, "live": live is not None,
                "busy": bool(live and live.runner.busy)}

    @app.get(PREFIX + "/sessions/{session_id}/events")
    async def stream(session_id: str, request: Request, after: int = 0, follow: bool = True,
                     user: User = Depends(current_user)) -> StreamingResponse:
        session_of(session_id, user)
        until = None
        if user.token_exp:
            until = asyncio.get_running_loop().time() + max(0.0, user.token_exp - time.time())

        async def gen():
            async for e in svc.bus.subscribe(session_id, after=after, follow=follow, until=until):
                if await request.is_disconnected():
                    return
                if e is None:
                    yield ": ping\n\n"
                else:
                    yield f"id: {e['seq']}\ndata: {json.dumps(e, ensure_ascii=False, default=str)}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"})

    @app.post(PREFIX + "/sessions/{session_id}/messages")
    async def message(session_id: str, payload: dict = Body(...), user: User = Depends(current_user)) -> Any:
        s = session_of(session_id, user)
        ref = payload.get("plan_ref")
        if ref is not None and (isinstance(ref, bool) or not isinstance(ref, int)):
            raise HTTPException(400, "plan_ref は案の版の番号です")
        e = await svc.send_message(s, user, str(payload.get("text") or ""), _device(payload), plan_ref=ref)
        return {"ok": True, "seq": e.get("seq")}

    @app.post(PREFIX + "/sessions/{session_id}/answers")
    async def answer(session_id: str, payload: dict = Body(...), user: User = Depends(current_user)) -> Any:
        s = session_of(session_id, user)
        answers = payload.get("answers") if payload.get("answers") is not None else payload.get("choice")
        if not isinstance(answers, (dict, str)):
            raise HTTPException(400, "answers（{質問: 答え}）か choice（答え）が要ります")
        await svc.answer(s, user, str(payload.get("question_id") or ""), answers, _device(payload))
        return {"ok": True}

    @app.post(PREFIX + "/sessions/{session_id}/stop")
    async def stop(session_id: str, user: User = Depends(current_user)) -> Any:
        await svc.stop(session_of(session_id, user))
        return {"ok": True}

    @app.get(PREFIX + "/sessions/{session_id}/plans/{version}/html")
    async def plan_html(session_id: str, version: int, size: str = "a4",
                        user: User = Depends(current_user)) -> HTMLResponse:
        session_of(session_id, user)
        p = svc.store.get_plan(session_id, version)
        if p is None:
            raise HTTPException(404, "案が見つかりません")
        html = p["html_a3"] if size == "a3" else p["html_a4"]
        if not html:
            raise HTTPException(404, "この案には A3 の時間割がありません")
        return _plan_html_response(html)

    @app.post(PREFIX + "/sessions/{session_id}/plans/{version}/print-url")
    async def print_url(session_id: str, version: int, payload: dict = Body(default={}),
                        user: User = Depends(current_user)) -> Any:
        session_of(session_id, user)
        if svc.store.get_plan(session_id, version) is None:
            raise HTTPException(404, "案が見つかりません")
        size = "a3" if payload.get("size") == "a3" else "a4"
        exp = int(time.time()) + PRINT_URL_TTL
        sig = print_signature(cfg.url_signing_key, session_id, version, size, user.id, exp)
        q = urlencode({"size": size, "u": user.id, "exp": exp, "sig": sig})
        return {"url": f"{PREFIX}/print/{session_id}/{version}?{q}", "expires_in": PRINT_URL_TTL}

    @app.get(PREFIX + "/print/{session_id}/{version}")
    async def print_page(session_id: str, version: int, size: str = "a4", u: str = "", exp: int = 0,
                         sig: str = "") -> HTMLResponse:
        # 新しいタブは Bearer を付けられないので、5 分・その人・その版だけの署名で確かめる（設計 §1-7）
        if not cfg.enabled:
            raise HTTPException(404, "Not Found")
        size = "a3" if size == "a3" else "a4"
        good = print_signature(cfg.url_signing_key, session_id, version, size, u, exp)
        if not sig or not hmac.compare_digest(good, sig) or exp < int(time.time()):
            raise HTTPException(403, "この印刷用のリンクは使えません（期限切れ）。画面からもう一度開いてください")
        s = svc.store.get_session(session_id)
        if s is None or str(s["user_id"]) != u or s.get("archived_at"):
            raise HTTPException(404, "案が見つかりません")
        p = svc.store.get_plan(session_id, version)
        html = (p["html_a3"] if size == "a3" else p["html_a4"]) if p else None
        if not html:
            raise HTTPException(404, "案が見つかりません")
        return _plan_html_response(html)

    @app.post(PREFIX + "/sessions/{session_id}/plans/{version}/approve")
    async def approve(session_id: str, version: int, payload: dict = Body(default={}),
                      user: User = Depends(current_user)) -> Any:
        s = session_of(session_id, user)
        r = await svc.approve_plan(s, user, version, _device(payload))
        # 承認番号は画面に返さない（中継が CLI に直接渡す）
        return {"ok": True, "version": r["version"], "expires_at": r["expires_at"]}

    @app.post(PREFIX + "/sessions/{session_id}/plans/{version}/reject")
    async def reject(session_id: str, version: int, payload: dict = Body(default={}),
                     user: User = Depends(current_user)) -> Any:
        s = session_of(session_id, user)
        await svc.reject_plan(s, user, version, _device(payload))
        return {"ok": True}

    @app.post(PREFIX + "/sessions/{session_id}/runs/{run_id}/undo/approve")
    async def approve_undo(session_id: str, run_id: str, payload: dict = Body(default={}),
                           user: User = Depends(current_user)) -> Any:
        s = session_of(session_id, user)
        r = await svc.approve_undo(s, user, run_id, _device(payload))
        return {"ok": True, "expires_at": r["expires_at"]}

    return app
