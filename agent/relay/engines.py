"""AI のつなぎ先（ChatGPT＝Codex・Claude）の管理: ログインの様子・画面からのサインイン/サインアウト。

人ごとの席（PO 2026-10-13: ChatGPT Business/Team は 1 席 1 人）: ログイン情報は人ごとに分ける。
中継は user id から不透明な席の鍵（seat_key・32 桁の 16 進）を作り、runner へは鍵だけを渡す（パスは渡さない）。
箱の runner（agent/deploy/runner/agent_runner.py）に unix ソケットで 1 回ずつ頼む（op ごとに 1 本の接続）。
  {"op":"auth_status","provider","user_key"}  → {"ev":"auth_status","provider","state"}
  {"op":"login_start","provider","user_key"}  → {"ev":"login",...}（箱で同時に 1 つだけ・15 分で打ち切り）
  {"op":"login_poll","user_key"} / {"op":"login_cancel","user_key"} / {"op":"login_input","data","user_key"}
                                              → {"ev":"login",...}（他人のサインインは idle に見える）
  {"op":"logout","provider","user_key"}       → {"ev":"logout","provider","ok","state"}
  {"op":"adopt_legacy","user_key"}            → {"ev":"adopted","moved":[codex|claude…]}（前の共有のログインを席へ）
runner の返事は箱の中から来る＝信用しない入力。決まった語・決まった形の URL / コードだけを通し、ほかは捨てる。
CLI の文・トークン・メールはここまで来ない（runner が返さない）し、画面にも返さない。
"""

from __future__ import annotations

import asyncio
import hashlib
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
BUSY_JA = "いま別のサインインの途中です（ほかの方の場合もあります）。少し待つか「やめる」を押してから、もう一度どうぞ"

Connect = Callable[[str], Awaitable[tuple[Any, Any]]]


def seat_key(user_id: str) -> str:
    """らく助の user id → 箱の中の席の名前（不透明な 32 桁の 16 進）。同じ人はいつも同じ席。
    箱の中のフォルダー名に user id そのものを出さないための写像（秘密ではない）。"""
    return hashlib.sha256(("rakusuke-agent-seat\n" + str(user_id)).encode("utf-8")).hexdigest()[:32]


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
        #: (席の鍵, provider) → (時刻, 状態)
        self._status: dict[tuple[str, str], tuple[float, str]] = {}

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

    # ---------- ログインの様子（その人の席） ----------
    async def status(self, provider: str, key: str, fresh: bool = False) -> str:
        now = self.clock()
        hit = self._status.get((key, provider))
        if hit and not fresh and now - hit[0] < self.STATUS_TTL:
            return hit[1]
        if not self.socket_path:
            return "unknown"
        try:
            m = await self._ask({"op": "auth_status", "provider": provider, "user_key": key})
        except Refused:
            return "unknown"
        state = m.get("state") if m.get("ev") == "auth_status" and m.get("state") in AUTH_STATES else "unknown"
        self._status[(key, provider)] = (now, state)
        return state

    def forget(self, key: str | None = None, provider: str | None = None) -> None:
        for k in list(self._status):
            if (key is None or k[0] == key) and (provider is None or k[1] == provider):
                self._status.pop(k, None)

    # ---------- サインイン（その人の席に） ----------
    async def login_start(self, provider: str, key: str) -> dict:
        if provider not in PROVIDERS:
            raise Refused("その AI はありません", 400)
        m = await self._ask({"op": "login_start", "provider": provider, "user_key": key})
        self.forget(key, provider)
        return _clean_login(m) if m.get("ev") == "login" else _clean_login({"state": "failed"})

    async def login_poll(self, key: str) -> dict:
        if not self.socket_path:
            return {**_clean_login({}), "state": "idle"}
        m = await self._ask({"op": "login_poll", "user_key": key})
        if m.get("ev") == "login" and m.get("state") == "idle":
            return {**_clean_login({}), "state": "idle"}
        out = _clean_login(m) if m.get("ev") == "login" else _clean_login({"state": "failed"})
        if out["state"] in ("done", "failed", "timeout", "cancelled") and out["provider"]:
            self.forget(key, out["provider"])
        return out

    async def login_input(self, code: str, key: str) -> dict:
        code = (code or "").strip()
        if not PASTE_CODE_RE.match(code):
            raise Refused("コードの形が違うようです。ブラウザーに出たコードを、そのまま貼り付けてください", 400)
        m = await self._ask({"op": "login_input", "data": code, "user_key": key})
        return _clean_login(m) if m.get("ev") == "login" else _clean_login({"state": "failed"})

    async def login_cancel(self, key: str) -> dict:
        m = await self._ask({"op": "login_cancel", "user_key": key})
        return _clean_login(m) if m.get("ev") == "login" and m.get("state") != "idle" else {
            **_clean_login({}), "state": "idle"}

    async def logout(self, provider: str, key: str) -> dict:
        if provider not in PROVIDERS:
            raise Refused("その AI はありません", 400)
        m = await self._ask({"op": "logout", "provider": provider, "user_key": key})
        self.forget(key, provider)
        state = m.get("state") if m.get("ev") == "logout" and m.get("state") in AUTH_STATES else "unknown"
        if state != "unknown":
            self._status[(key, provider)] = (self.clock(), state)
        return {"provider": provider, "ok": bool(m.get("ok")), "state": state}

    async def adopt_legacy(self, key: str) -> list[str]:
        """前の共有のログイン（箱の /home/agent/.codex・.claude）を、その人の席へ移すよう頼む（席が空のときだけ）。"""
        m = await self._ask({"op": "adopt_legacy", "user_key": key})
        moved = m.get("moved") if m.get("ev") == "adopted" and isinstance(m.get("moved"), list) else []
        out = [p for p in moved if p in PROVIDERS]
        if out:
            self.forget(key)
        return out
