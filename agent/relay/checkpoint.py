"""中継の内側口（箱の中の rk だけが話す・設計 §2-2）。公開しない（agent-internal 網か unix ソケット）。

| 道 | 許すもの |
|---|---|
| GET /rk/api/v1/<path> | すべての GET（カイポケのロボットを動かす読み取りは止める）。tool_calls に記録 |
| POST /rk/api/v1/<path> | rk/readonly_posts.py の許可リストだけ（週を空にする/作るは本文 dry_run:true のときだけ）。ほかの POST/PUT/PATCH/DELETE は 403 |
| POST /rk/sql | 1 文の SELECT（rk.sqlrun の検査）を agent_ro で |
| POST /rk/plans | 案の提出（rk plan submit） |
| POST /rk/apply・/rk/undo | 有効な承認番号があるときだけ（relay.approvals） |
| POST /rk/progress・/rk/backup・GET /rk/runs/<id>/plan | 途中経過・バックアップ・照合用の案 |
| POST /rk/journal/<write・list・search・show・retire> | 相談の引き継ぎ帳（事業所ごと・DB の agent.journal_entries） |
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import Body, FastAPI, Request
from fastapi.responses import JSONResponse

from rk import RkError
from rk.consult_journal import build_entry, office_key, rank
from rk.opsbase import ApiError
from rk.readonly_posts import is_dry_run_post, is_readonly_post
from rk.transport import RELAY_TOKEN_HEADER, UPSTREAM_HEADER, normalize_path

from relay.approvals import Refused
from relay.service import RelayService

#: カイポケにログインして画面を操作する読み取り（AGENTS.md §4-2 の最下行）。段 3 では関所で止める。
ROBOT_PATH_RE = re.compile(r"(^|/)(diff-local|diff|export|smart-inbound-preview|smart-inbound-start|smart-inbound|"
                           r"plan-actual-compare|expand)(/|$|\?)")
WRITE_REFUSAL = "反映は画面の承認を通してください（rk plan submit → 画面で承認 → rk apply --approved ap_…）"


def _refuse(message: str, status: int = 403) -> JSONResponse:
    return JSONResponse({"rk_error": message}, status_code=status)


def create_checkpoint_app(svc: RelayService) -> FastAPI:
    app = FastAPI(title="らく助 中継（内側口）", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def _only_sandbox(request: Request, call_next):
        # AGENT_SANDBOX_SUBNET があれば、箱の網からの接続だけを受ける（backend の網からは使わせない）
        cfg = svc.cfg
        if cfg.sandbox_subnet and not cfg.in_sandbox(request.client.host if request.client else None):
            return _refuse("ここは箱の rk だけが使えます", 403)
        return await call_next(request)

    @app.exception_handler(Refused)
    async def _refused(_req: Request, exc: Refused) -> JSONResponse:
        return _refuse(exc.message, exc.status)

    @app.exception_handler(RkError)
    async def _rkerr(_req: Request, exc: RkError) -> JSONResponse:
        return _refuse(str(exc), 400)

    def session_of(request: Request) -> dict:
        return svc.session_for_token(request.headers.get(RELAY_TOKEN_HEADER))

    def _proxy(session: dict, method: str, path: str, params: dict | None, body: Any) -> JSONResponse:
        t0 = time.monotonic()
        api = svc.api_factory(None, None)
        kind = "get" if method == "GET" else "post-read"
        try:
            data = api.get(path, params or None) if method == "GET" else api.call("POST", path, body)
        except ApiError as e:
            svc.record_tool_call(session, kind, path, e.status, int((time.monotonic() - t0) * 1000))
            return JSONResponse(e.body, status_code=e.status, headers={UPSTREAM_HEADER: "1"})
        svc.record_tool_call(session, kind, path, 200, int((time.monotonic() - t0) * 1000))
        return JSONResponse(data)

    @app.api_route("/rk/api/v1/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def api_gate(path: str, request: Request, body: Any = Body(None)) -> JSONResponse:
        session = session_of(request)
        try:
            p = normalize_path("/" + path)
        except RkError as e:
            return _refuse(str(e), 400)
        method = request.method.upper()
        if ROBOT_PATH_RE.search(p):
            svc.record_tool_call(session, "refused", f"{method} {p}", 403, 0)
            return _refuse("カイポケのロボットを動かす読み取りは、この画面からはできません（了承が要ります）")
        if method == "GET":
            return _proxy(session, "GET", p, dict(request.query_params), None)
        if method == "POST" and (is_readonly_post(p) or is_dry_run_post(p, body)):
            return _proxy(session, "POST", p, None, body if body is not None else {})
        svc.record_tool_call(session, "refused", f"{method} {p}", 403, 0)
        return _refuse(WRITE_REFUSAL)

    @app.post("/rk/sql")
    def sql(request: Request, payload: dict = Body(...)) -> Any:
        session = session_of(request)
        if svc.sql is None:
            return _refuse("読み取りの係が用意されていません", 503)
        text = str(payload.get("sql") or "")
        params = {str(k): str(v) for k, v in (payload.get("params") or {}).items()}
        name = payload.get("name")
        t0 = time.monotonic()
        try:
            rows = svc.sql.run(text, params)
        except RkError as e:
            svc.record_tool_call(session, "sql", str(name or "(その場の SQL)"), 400,
                                 int((time.monotonic() - t0) * 1000), sql_head=text)
            return _refuse(str(e), 400)
        svc.record_tool_call(session, "sql", str(name or "(その場の SQL)"), 200, int((time.monotonic() - t0) * 1000),
                             sql_head=text, rows=len(rows) if isinstance(rows, list) else None)
        return JSONResponse(rows)

    @app.post("/rk/plans")
    def submit(request: Request, payload: dict = Body(...)) -> Any:
        return svc.submit_plan(session_of(request), payload.get("plan"))

    @app.post("/rk/apply")
    def apply(request: Request, payload: dict = Body(...)) -> Any:
        return svc.apply(session_of(request), payload.get("plan"), str(payload.get("approval") or ""))

    @app.post("/rk/undo")
    def undo(request: Request, payload: dict = Body(...)) -> Any:
        return svc.undo(session_of(request), str(payload.get("run_id") or ""), str(payload.get("approval") or ""),
                        bool(payload.get("partial")))

    @app.post("/rk/progress")
    def progress(request: Request, payload: dict = Body(...)) -> Any:
        svc.progress(session_of(request), str(payload.get("text") or ""))
        return {"ok": True}

    @app.post("/rk/backup")
    def backup(request: Request, payload: dict = Body(...)) -> Any:
        return svc.run_backup(session_of(request), str(payload.get("label") or ""))

    def _jdict(e: dict) -> dict:
        return {k: (v.strftime("%Y-%m-%dT%H:%M:%SZ") if hasattr(v, "strftime") else v) for k, v in e.items()}

    def _utc(text: str | None) -> datetime | None:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if text else None

    @app.post("/rk/journal/{op}")
    def journal(op: str, request: Request, payload: dict = Body(...)) -> Any:
        """相談の引き継ぎ帳。事業所ごと。検査（長さ・核の約束を変える書き方）は rk と同じ build_entry。"""
        session = session_of(request)
        office = office_key(payload.get("office"))
        st = svc.store
        if op == "write":
            e = build_entry(payload.get("entry") or {}, office, session["id"])
            e["ts"] = _utc(e["ts"])
            return _jdict(st.add_journal(**e))
        if op == "list":
            lim = max(1, min(int(payload.get("limit") or 10), 1000))
            return [_jdict(e) for e in st.list_journal(office, _utc(payload.get("since")),
                                                       bool(payload.get("include_retired")), lim)]
        if op == "search":
            words = [str(w) for w in payload.get("words") or [] if str(w).strip()]
            return rank([_jdict(e) for e in st.list_journal(office, limit=1000)], words,
                        max(1, min(int(payload.get("limit") or 10), 100)))
        if op in ("show", "retire"):
            eid = str(payload.get("id") or "")
            if op == "retire" and not st.retire_journal(office, eid, str(payload.get("reason") or "")):
                return _refuse(f"{eid} という記録はありません", 404)
            e = st.get_journal(office, eid)
            return _jdict(e) if e else _refuse(f"{eid} という記録はありません", 404)
        return _refuse("知らない操作です", 404)

    @app.get("/rk/runs/{run_id}/plan")
    def run_plan(run_id: str, request: Request) -> Any:
        return svc.run_plan(session_of(request), run_id)

    return app
