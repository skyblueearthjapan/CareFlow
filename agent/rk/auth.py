"""ログインとトークン（~/.rakusuke/token.json）。

順番: 期限内の access → refresh（30 日）→ それも無ければ 1 回だけログイン。
ログインは 15 分 5 回の制限があるので、失敗しても繰り返さない。さらに 15 分に 3 回までに自分で抑える。
トークン・パスワードは画面に出さない。
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any, Callable

from rk import RkError
from rk.config import Config, load_credentials, write_private_json
from rk.opsbase import ApiError

ACCESS_FALLBACK_SEC = 3600
REFRESH_FALLBACK_SEC = 30 * 24 * 3600
MARGIN_SEC = 60
LOGIN_WINDOW_SEC = 15 * 60
LOGIN_MAX_IN_WINDOW = 3


def jwt_exp(token: str) -> float | None:
    """JWT の exp（検証はしない・期限の目安だけ）。"""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except Exception:
        return None


class TokenManager:
    def __init__(self, cfg: Config, http: Any, now: Callable[[], float] = time.time):
        self.cfg = cfg
        self.http = http  # request(method, path, body=None, params=None, token=None)
        self.now = now

    # --- キャッシュ ---
    def load(self) -> dict | None:
        p = self.cfg.token_path
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def save(self, tokens: dict) -> dict:
        now = self.now()
        access, refresh = tokens["access_token"], tokens.get("refresh_token")
        old = self.load() or {}
        rec = {
            "access_token": access,
            "access_expires_at": jwt_exp(access) or now + ACCESS_FALLBACK_SEC,
            "refresh_token": refresh or old.get("refresh_token"),
            "refresh_expires_at": (jwt_exp(refresh) or now + REFRESH_FALLBACK_SEC) if refresh
            else old.get("refresh_expires_at"),
            "saved_at": now,
        }
        write_private_json(self.cfg.token_path, rec)
        return rec

    def invalidate_access(self) -> None:
        rec = self.load()
        if rec:
            rec["access_expires_at"] = 0
            write_private_json(self.cfg.token_path, rec)

    # --- 取得 ---
    def access_token(self) -> str:
        now = self.now()
        rec = self.load()
        if rec and rec.get("access_token") and rec.get("access_expires_at", 0) - MARGIN_SEC > now:
            return rec["access_token"]
        if rec and rec.get("refresh_token") and (rec.get("refresh_expires_at") or 0) - MARGIN_SEC > now:
            try:
                res = self.http.request("POST", "/auth/refresh", {"refresh_token": rec["refresh_token"]})
                return self.save(_tokens_of(res))["access_token"]
            except ApiError as e:
                if e.status not in (400, 401, 403, 422):
                    raise RkError(f"トークンの更新に失敗しました（HTTP {e.status}）") from None
                # refresh が無効 → 下で 1 回だけログイン
        return self.login()["access_token"]

    def login(self) -> dict:
        creds = load_credentials(self.cfg)
        if not creds:
            raise RkError(
                "ログインの資格情報がありません。環境変数 RK_IDENTIFIER / RK_PASSWORD か "
                f"{self.cfg.credentials_path}（{{\"identifier\": ..., \"password\": ...}}）を用意してください"
            )
        self._guard_login_rate()
        ident, pw = creds
        try:
            res = self.http.request("POST", "/auth/login", {"identifier": ident, "password": pw})
        except ApiError as e:
            hint = {
                401: "識別子かパスワードが違います",
                423: "アカウントが 15 分ロックされています（待ってから 1 回だけ）",
                429: "ログインの回数制限です（15 分待つ）",
            }.get(e.status, "ログインに失敗しました")
            raise RkError(f"{hint}（HTTP {e.status}）。自動では繰り返しません") from None
        return self.save(_tokens_of(res))

    def _guard_login_rate(self) -> None:
        now = self.now()
        p = self.cfg.login_log_path
        try:
            hist = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        except (OSError, ValueError):
            hist = []
        hist = [t for t in hist if isinstance(t, (int, float)) and now - t < LOGIN_WINDOW_SEC]
        if len(hist) >= LOGIN_MAX_IN_WINDOW:
            raise RkError(
                f"15 分のあいだに {len(hist)} 回ログインしました。らく助の制限（5 回/15 分）に掛からないよう止めます。"
                "資格情報を確かめ、15 分おいてから 1 回だけ試してください"
            )
        hist.append(now)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(hist), encoding="utf-8")


def _tokens_of(res: Any) -> dict:
    if isinstance(res, dict):
        t = res.get("tokens") if isinstance(res.get("tokens"), dict) else res
        if t.get("access_token"):
            return t
    raise RkError("ログイン/更新の応答にトークンがありません")
