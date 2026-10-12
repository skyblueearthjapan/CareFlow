"""AI のつなぎ先（ChatGPT＝Codex・Claude）の管理: ログインの様子・画面からのサインイン/サインアウト。

箱の runner（agent/deploy/runner/agent_runner.py）に unix ソケットで 1 回ずつ頼む（op ごとに 1 本の接続）。
  {"op":"auth_status","provider"}  → {"ev":"auth_status","provider","state"}
  {"op":"login_start","provider"}  → {"ev":"login",...}（同時に 1 つだけ・15 分で打ち切り）
  {"op":"login_poll"} / {"op":"login_cancel"} / {"op":"login_input","data"} → {"ev":"login",...}
  {"op":"logout","provider"}       → {"ev":"logout","provider","ok","state"}
runner の返事は箱の中から来る＝信用しない入力。決まった語・決まった形の URL / コードだけを通し、ほかは捨てる。
CLI の文・トークン・メールはここまで来ない（runner が返さない）し、画面にも返さない。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Awaitable, Callable

from relay.approvals import Refused

PROVIDERS = ("codex", "claude")
AUTH_STATES = ("ok", "missing", "api_key", "unknown")
LOGIN_STATES = ("idle", "starting", "waiting", "verifying", "done", "failed", "timeout", "cancelled")
LOGIN_ERRORS = ("invalid_code", "no_url")
# runner と同じ物差し（画面に出してよい URL とコードの形）
URL_RE = {
    "codex": re.compile(r"^https://auth\.openai\.com/[A-Za-z0-9/_\-.?=&%]{1,200}$"),
    "claude": re.compile(r"^https://(?:claude\.com|claude\.ai|platform\.claude\.com)/[A-Za-z0-9/_\-.?=&%:+~]{1,2000}$"),
}
DEVICE_CODE_RE = re.compile(r"^[A-Z0-9]{4,6}-[A-Z0-9]{4,6}$")
PASTE_CODE_RE = re.compile(r"^[A-Za-z0-9_\-#.~]{8,1024}$")

NO_RUNNER_JA = "この環境では、画面からのサインインはできません"
UNREACHABLE_JA = "らく助の作業の係につながりません。少し時間をおいてください"
BUSY_JA = "いま別のサインインの途中です。終わるか「やめる」を押してから、もう一度どうぞ"

Connect = Callable[[str], Awaitable[tuple[Any, Any]]]


def _clean_login(m: dict) -> dict:
    """runner の login の返事から、画面に出してよいものだけを残す。"""
    prov = m.get("provider") if m.get("provider") in PROVIDERS else None
    state = m.get("state") if m.get("state") in LOGIN_STATES else "failed"
    url = m.get("url")
    url = url if prov and isinstance(url, str) and URL_RE[prov].match(url) else None
    code = m.get("code")
    code = code if prov == "codex" and isinstance(code, str) and DEVICE_CODE_RE.match(code) else None
    left = m.get("expires_in")
    left = min(900, max(0, left)) if isinstance(left, int) and not isinstance(left, bool) else 0
    err = m.get("error") if m.get("error") in LOGIN_ERRORS else None
    return {"provider": prov, "state": state, "url": url, "code": code,
            "needs_input": bool(m.get("needs_input")) and prov == "claude" and state == "waiting",
            "expires_in": left, "error": err}


class EngineAdmin:
    STATUS_TTL = 20.0

    def __init__(self, socket_path: str, connect: Connect | None = None, timeout: float = 45.0,
                 clock: Callable[[], float] = time.monotonic):
        self.socket_path = socket_path
        self._connect = connect or (lambda p: asyncio.open_unix_connection(p, limit=1 << 16))
        self.timeout = timeout
        self.clock = clock
        self._status: dict[str, tuple[float, str]] = {}
        #: いまのサインインを始めた人（監査用）と、「終わった」を 1 回だけ記録するための印
        self.started_by: str | None = None
        self.reported: tuple | None = None

    @property
    def available(self) -> bool:
        return bool(self.socket_path)

    async def _ask(self, obj: dict) -> dict:
        if not self.socket_path:
            raise Refused(NO_RUNNER_JA, 503)
        try:
            r, w = await self._connect(self.socket_path)
        except OSError:
            raise Refused(UNREACHABLE_JA, 503) from None
        try:
            w.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
            await w.drain()
            raw = await asyncio.wait_for(r.readline(), self.timeout)
        except (asyncio.TimeoutError, ConnectionError, ValueError):
            raise Refused(UNREACHABLE_JA, 504) from None
        finally:
            try:
                w.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            msg = json.loads(raw.decode("utf-8", "replace")) if raw else {}
        except ValueError:
            msg = {}
        if not isinstance(msg, dict):
            msg = {}
        if msg.get("ev") == "error":
            why = str(msg.get("message") or "")
            if why == "login busy":
                raise Refused(BUSY_JA, 409)
            if why == "not waiting for a code":
                raise Refused("いまはコードを受け付けていません。もう一度サインインからどうぞ", 409)
            raise Refused("うまくいきませんでした。少し時間をおいて、もう一度どうぞ", 502)
        return msg

    # ---------- ログインの様子 ----------
    async def status(self, provider: str, fresh: bool = False) -> str:
        now = self.clock()
        hit = self._status.get(provider)
        if hit and not fresh and now - hit[0] < self.STATUS_TTL:
            return hit[1]
        if not self.socket_path:
            return "unknown"
        try:
            m = await self._ask({"op": "auth_status", "provider": provider})
        except Refused:
            return "unknown"
        state = m.get("state") if m.get("ev") == "auth_status" and m.get("state") in AUTH_STATES else "unknown"
        self._status[provider] = (now, state)
        return state

    def forget(self, provider: str | None = None) -> None:
        if provider is None:
            self._status.clear()
        else:
            self._status.pop(provider, None)

    # ---------- サインイン ----------
    async def login_start(self, provider: str) -> dict:
        if provider not in PROVIDERS:
            raise Refused("その AI はありません", 400)
        m = await self._ask({"op": "login_start", "provider": provider})
        self.forget(provider)
        return _clean_login(m) if m.get("ev") == "login" else _clean_login({"state": "failed"})

    async def login_poll(self) -> dict:
        if not self.socket_path:
            return {**_clean_login({}), "state": "idle"}
        m = await self._ask({"op": "login_poll"})
        out = _clean_login(m) if m.get("ev") == "login" else _clean_login({"state": "failed"})
        if out["state"] in ("done", "failed", "timeout", "cancelled") and out["provider"]:
            self.forget(out["provider"])
        return out

    async def login_input(self, code: str) -> dict:
        code = (code or "").strip()
        if not PASTE_CODE_RE.match(code):
            raise Refused("コードの形が違うようです。ブラウザーに出たコードを、そのまま貼り付けてください", 400)
        m = await self._ask({"op": "login_input", "data": code})
        return _clean_login(m) if m.get("ev") == "login" else _clean_login({"state": "failed"})

    async def login_cancel(self) -> dict:
        m = await self._ask({"op": "login_cancel"})
        return _clean_login(m) if m.get("ev") == "login" and m.get("state") != "idle" else {
            **_clean_login({}), "state": "idle"}

    async def logout(self, provider: str) -> dict:
        if provider not in PROVIDERS:
            raise Refused("その AI はありません", 400)
        m = await self._ask({"op": "logout", "provider": provider})
        self.forget(provider)
        state = m.get("state") if m.get("ev") == "logout" and m.get("state") in AUTH_STATES else "unknown"
        if state != "unknown":
            self._status[provider] = (self.clock(), state)
        return {"provider": provider, "ok": bool(m.get("ok")), "state": state}
